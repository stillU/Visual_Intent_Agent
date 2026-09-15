# 架构裁定记录 004（Rev.4）：Step 06 patch 002 —— FeedbackEngine 引擎级重试 + prompt 保语义精简

- 日期：2026-09-15
- 裁定方：MVP v0.3 Step 06 patch 002 实现 Agent（用户已明确授权本轮两项最小修订）
- 上游证据：
  - 首轮与 patch 001：`evaluation/reports/fix_traceability.md`（首轮 P1~P5 + patch 001）、
    `docs/handoffs/v0_3_step_06_handoff.md`、`docs/handoffs/v0_3_step_06_patch_001.md`；
  - P6 超时挟持三轮真实复跑各一次：首轮 `s09-multiturn-002`；诊断一
    `outputs/evaluation_runs/erun_e7413108dc8a6104/cases/s08-pin-unpin-001.jsonl`
    （`eturn_c86c180e3ebc5094`，`feedback_id=fbk_f9cee845098946359af5947813da0bba`，
    `llm_call=ellm_981ba576cfcd9065`，`provider.timeout`，`latency_ms=181 947`）；诊断二
    `outputs/evaluation_runs/erun_f78691dffb0f7494/cases/s07-single-field-002.jsonl`
    （`eturn_fbb635a1d116d9b7`，`feedback_id=fbk_27b7f4b4a6e04b4687764f0e9c0d8e60`，
    `llm_call=ellm_59fdeb3f627e4de5`，`provider.timeout`，`latency_ms=181 925`）；
  - `failure_catalog.jsonl#FC-P6-no-degradation-after-provider-timeout`（`worth_fixing=false`，
    `classification=provider_variance`）。
- 裁定结果：**采纳两项最小修订**（用户裁定"先做 P6 缓解补丁再进 Step 07"）。
  (1) `FeedbackEngine` 增加引擎级重试，严格对齐 Interpreter 既有
  `MAX_INTERPRETATION_ATTEMPTS=2` 先例；(2) Interpreter 与 FeedbackEngine 系统提示词
  **保语义精简**并 bump 版本常量。两项均**不**改 adapter 层超时/重试配置，**不**改任何
  数据合同或落盘 schema。
- 事实核验（裁定当日执行）：全量 `uv run pytest -q` → **2151 passed, 3 deselected**
  （基线 2143 + 新增 6 条 FeedbackEngine 重试回归 + 2 条 prompt 精简守卫）；
  `tests/evaluation` → **243 passed**；`frozen_manifest_v0_3.json` 四文件 sha256 逐字节一致
  （protocol `70cc0b3c…`、fixtures `05195347…`、annotations `d1712aab…`、config `3b6159f8…`）。

---

## 裁定 1（需要决定）：FeedbackEngine 引擎级重试 —— 采纳，对齐 Interpreter 先例

### 现状（v0.2 冻结行为）

`FeedbackEngine.analyze`（`visual_intent_agent/feedback/engine.py`）对 Provider 失败**只做
单次**尝试：`self._llm.complete(...)` 抛 `ProviderError` 即转
`_provider_failure_result` → `decision=clarify`、`candidate_deltas=[]`、`compile_feedback=None`、
`provider.*` error issue、状态不变。`feedback/engine.py` 模块 docstring 当时明确写
"**不重试**：引擎自身不重试；重试决策属调用方"。

对照先例：Interpreter 路径的"不合法输出最多修复一次"由 `workflow/service.py` 的
`MAX_INTERPRETATION_ATTEMPTS = 2` + `_resolve_with_single_repair` 承担（初次 + 至多一次重试），
`_has_retryable_failure` 的 Provider 分支按冻结的 `providers.errors.RETRYABLE_CODES`
（rate_limited / timeout / network / server_error）判定。**Feedback 路径此前没有对应的
引擎级重试**——诊断一 s08 t2 与诊断二 s07 t2 的超时都发生在 FeedbackEngine 调用上。

### 新行为（冻结细节）

1. **常量**：`visual_intent_agent/feedback/engine.py` 新增
   `MAX_FEEDBACK_ATTEMPTS: int = 2`，与 `MAX_INTERPRETATION_ATTEMPTS = 2` **同值同语义**
   （初次 + 至多一次重试）；有测试
   `tests/feedback/test_feedback_retry.py::test_retry_budget_matches_the_interpreter_precedent`
   同时钉住两者相等。
2. **可重试判定**：模块级 `_is_retryable_provider_error(exc) -> bool`，返回
   `exc.code in RETRYABLE_CODES`——与 `workflow.service._has_retryable_failure` 的 Provider
   分支逐字同口径（`ProviderError` 构造时强校验 `retryable == (code in RETRYABLE_CODES)`，
   两者恒等；显式用 code 判定以对齐先例）。
3. **调用点**：唯一新增私有方法 `FeedbackEngine._complete_with_retries(llm_request)`；
   `analyze` 内 `try: response = self._complete_with_retries(...) except ProviderError:`
   仍走**原封不动**的 `_provider_failure_result`。公开签名
   `analyze(self, request)` 不变（既有 `test_feedback_engine_exposes_the_frozen_analyze_signature`
   继续通过）。
4. **循环语义**：最多 `MAX_FEEDBACK_ATTEMPTS` 次；仅当 `exc` 可重试且尚有预算时继续，
   否则 `break`；重试用尽后抛**最后一次** `ProviderError`。Engine 层**不 sleep、不退避**
   （与 Interpreter 先例 `_resolve_with_single_repair` 同口径），不引入新机制/新架构层。
5. **不可重试错误**（`provider.auth` / `provider.invalid_request` /
   `provider.unparseable_response`）立即降级，**不消耗**重试预算。
6. **解析失败不重试**：`FeedbackParseError`（`feedback.unparseable_output.*`）在
   `_complete_with_retries` **之外**的既有 `except FeedbackParseError` 分支处理——解析失败
   属**被测系统行为**（模型输出畸形），与 Provider 波动不同类，按 v0.2 冻结语义立即降级。
   这是与 Interpreter 先例**唯一有意的差异**：Interpreter 的 `_has_retryable_failure` 也重试
   `interpreter.unparseable_output*`，但本 patch 的授权范围明确要求"解析失败不重试"，
   故 Feedback 侧只重试 Provider 层 retryable 错误。
7. **降级语义逐字不变**：`_provider_failure_result` 与 `_failure_result` **零改动**——
   重试用尽后仍是 recoverable clarify、`candidate_deltas=[]`、`preserve_paths=[]`、
   `compile_feedback=None`、`provider.*` issue（message 含 `retryable=...`）、状态不变、
   不伪造问题。本 patch **只改"降级之前多试一次"**。

### 为什么这是最小修订

1. **零公开面变化**：`analyze` 签名、`FeedbackResult` 12 字段、`__all__` 既有名字、
   落盘 payload、Repository 表与 refs 全部不变；唯一新增公开名是
   `MAX_FEEDBACK_ATTEMPTS`（常量，向后兼容）。
2. **零新架构层**：复用既有 `LLMProvider.complete` 与既有 `ProviderError` 分类，不新增
   Provider 包装、不新增重试框架、不修改 adapter。
3. **与 adapter 层正交**：`Settings.llm_timeout_seconds` / `Settings.http_max_retries` 与
   adapter 内 `0.5s × 2^n` 退避**一律未动**（冻结配置）。Engine 层重试是在 adapter 已用尽
   自身重试之后再多一次**完整**引擎调用，用于覆盖"adapter 三次 HTTP 尝试全超时"的窗口。
4. **可观察**：评测侧 `RecordingLLMProvider`（`role=feedback_engine`）会把每次
   `complete` 记成一条 `llm_calls`，重试因此天然可审计（`retry_count` 语义不变，因为它是
   adapter 内部计数；引擎级重试表现为**两条** feedback_engine 调用记录）。

### 对 Artifact / 合同兼容性的影响

- **无 schema 变化**：`FeedbackResult` / `FeedbackRequest` / `IntentDelta` / `EvidenceRef`
  与所有落盘 payload 形状不变；无新 Issue code；无新状态迁移。
- **无行为回归**：所有既有 Feedback 离线测试（含 `_provider_failure_result` 断言
  `issues[0].code == "provider.timeout"`、`retryable=True`）继续通过；新增 6 条重试回归
  证明"红→绿"：`MAX_FEEDBACK_ATTEMPTS = 1`（等价 v0.2 无重试）时核心用例
  `test_retryable_timeout_retries_and_applies_the_second_attempt_delta` 失败
  （`assert 1 == 2`，delta 丢失），恢复为 2 后通过。

### 为什么 P6 只能缓解、不能消除（必须如实记录）

- P6 的根因是 **Provider 侧波动**（约 8% LLM 调用超时；诊断样本 181.9s ≈ adapter 3 次
  60s 读超时），**不是产品逻辑缺陷**（`failure_catalog` 判定 `provider_variance`、
  `worth_fixing=false`）。产品代码无法让 Provider 不超时。
- 引擎级重试把"单次超时即挟持状态轨迹"降为"连续两次完整调用都超时（每次内部最多 3 次
  HTTP 尝试）才挟持"。若单次完整调用超时概率近似 `p`，则两次独立尝试都超时的概率近似
  `p²`（**前提**：两次尝试的失败相互独立；真实 Provider 可能处于持续劣化窗口，独立性
  不成立，实际下降幅度可能远小于 `p²`）。**这是概率性缓解，不是保证**。
- 本 patch 的定向真实探针即出现反例：s04-conflict-001 t1 的两次完整引擎调用**连续超时**
  （`attempt=1` / `attempt=2` 各 181.9s / 182.0s），第 3 次才成功——证明缓解无法归零。
- patch 001 精简 prompt 只是**降低单次调用延迟（从而降低超时概率）的候选因素**，
  **未证明因果**；本 patch 亦不主张因果。

## 裁定 2（追认类）：prompt 保语义精简 —— 追认为最小修订

**裁定**：Interpreter 系统提示词 v3→v4（5660→4590 字符，-18.9%）、FeedbackEngine 系统
提示词 v2→v3（4485→3657 字符，-18.5%）为**保语义精简**，不新增/删除任何规则语义。

冻结细节：

1. **被断言的关键句逐字保留**：patch 001 的
   `tests/intent_engine/test_interpreter_v3_boundaries.py` 及
   `test_interpreter_boundaries.py` / `test_feedback_boundaries.py` 中全部关键句断言
   （`COUNTING`、`LOCATIVE-FREE TEXT`、`CLEAR IS PER-PATH`、`This rule constrains
   subject.count ONLY`、`must NOT produce a subject.count delta`、`never default it to 1`、
   `you MUST emit SET environment.location`、`An explicit place marker always yields its own
   delta`、`MINIMAL never means fewer`、`a reason to emit fewer deltas`、`PRESERVE SCOPE` 等）
   **一字未改，全部继续通过**。因此无需改动任何"规则关键句"断言，只更新版本字面量。
2. **版本 bump**：prompt 文本变化 → `INTERPRETER_PROMPT_VERSION` `interpreter.v3→v4`；
   `FEEDBACK_PROMPT_VERSION` `feedback.v2→v3`。
3. **语义逐条对照**写入 `evaluation/reports/fix_traceability.md` patch 002 章节
   （原句 → 精简句 → 语义等价理由），并新增字符数上界回归守卫
   （Interpreter `< 5000`、Feedback `< 4000`，只钉方向不钉死具体值）。
4. 输出 Schema、证据合同、操作语义、decision 语义、preserve→PIN 展开实现**全部未变**。

理由：P6 缓解的概率 = 降低单次调用延迟；更短的 system prompt 是**候选**降延迟手段，
成本极低（纯文本），且可在**不触碰任何保护语义**的前提下完成。任何以"删规则/删反例"为
代价的进一步压缩**不做**——保护语义优先于字符数（任务书"不设硬指标"）。

## 对后续步骤的输入

- Step 07 正式复评仍可能因 Provider 波动触发 gate_a_blocked（见
  `docs/handoffs/v0_3_step_06_patch_002.md`「给 Step 07 的风险清单」）。若再见
  PIN/accept 敏感链路的超时挟持，应先在 `feedback_results.issues` 中确认
  `provider.timeout`，再判产品缺陷。
- 若 Provider 超时率长期高于可接受阈值，正确的下一步在 **Provider/评测口径**层（例如
  调整 `VIA_LLM_TIMEOUT_SECONDS` 或换 Provider），而不是继续在引擎层叠加重试次数——
  本轮已证明连续两次完整调用仍会双双超时。
- 本 patch 只改 `feedback/engine.py`、`feedback/__init__.py`、`feedback/models.py`（仅
  docstring）、`intent_engine/prompts.py` 与对应测试；`evaluation/**` 既有文件只追加
  `fix_traceability.md`。Step 01~05 交付物、冻结物、配置、`pyproject.toml`、`api.md`、
  `.env` 零触碰。
