# MVP v0.3 Step 06 patch 002 交接记录

任务：首轮（P1~P5）与 patch 001（Interpreter v3 收窄）之后，连续三轮真实运行每轮都出现且仅
出现一次 **P6 Provider 超时挟持**（首轮 s09-multiturn-002、诊断一 s08-pin-unpin-001、诊断二
s07-single-field-002）。用户裁定"先做 P6 缓解补丁再进 Step 07"，本 patch 做**两项最小修订**：

- **缓解一**：`FeedbackEngine` 引擎级重试，严格对齐 Interpreter 既有
  `MAX_INTERPRETATION_ATTEMPTS=2` 先例；
- **缓解二**：Interpreter 与 FeedbackEngine 系统提示词**保语义精简** + 版本 bump。

- 上游证据：`evaluation/reports/fix_traceability.md`（首轮 + patch 001 + 本 patch 章节）、
  `docs/handoffs/v0_3_step_06_handoff.md`、`docs/handoffs/v0_3_step_06_patch_001.md`、
  诊断复跑 `erun_e7413108dc8a6104`（s08 t2）/ `erun_f78691dffb0f7494`（s07 t2）。
- 架构记录：`docs/handoffs/architecture_decision_004.md`（Rev.4）。
- 未触碰：`evaluation/**` 既有文件（仅追加 `fix_traceability.md`）、`tests/evaluation/**`、
  Step 01~05 交付物与冻结物、超时/重试/Provider **配置项**、`pyproject.toml`、`api.md`、
  `.env`、v0.2 冻结降级语义、PIN/证据/冲突规则语义。

---

## 完成内容

### A. 缓解一：FeedbackEngine 引擎级重试（对齐 Interpreter 先例）

**实现方式（最小）**：`visual_intent_agent/feedback/engine.py`

1. 新增常量 `MAX_FEEDBACK_ATTEMPTS: int = 2`——与 `workflow.service.MAX_INTERPRETATION_ATTEMPTS`
   **同值同语义**（初次 + 至多一次重试）；测试钉住两者相等。
2. 新增私有方法 `_complete_with_retries(llm_request)`：`for attempt in range(MAX_FEEDBACK_ATTEMPTS)`
   调用 `self._llm.complete`；捕获 `ProviderError` 后**仅当**可重试且尚有预算才继续，否则
   `break`；循环结束抛**最后一次** `ProviderError`。
3. 新增模块级 `_is_retryable_provider_error(exc) -> bool`：`return exc.code in RETRYABLE_CODES`
   ——与 `workflow.service._has_retryable_failure` 的 Provider 分支逐字同口径。
4. `analyze` 的 `try/except ProviderError` 结构不变，只是被 `try` 的对象换成
   `_complete_with_retries(...)`；`except` 仍走**原封不动**的 `_provider_failure_result`。

**可重试判定（只这一类）**：`providers.errors.RETRYABLE_CODES` = rate_limited / timeout /
network / server_error。`provider.auth` / `provider.invalid_request` /
`provider.unparseable_response` 立即降级、不消耗重试预算。**解析失败**
（`feedback.unparseable_output.*`）在重试包装**之外**处理，按"被测系统行为"立即降级、不重试
——这是与 Interpreter 先例唯一有意的差异（授权范围明确要求）。

**降级语义不变的证明**：

- `_provider_failure_result` / `_failure_result` 两个方法**逐字节零改动**；
- 新回归 `test_retry_exhausted_degrades_to_recoverable_clarify_without_state_change`：
  两次 timeout → `decision=CLARIFY`、`candidate_deltas==[]`、`preserve_paths==[]`、
  `compile_feedback is None`、`issues==["provider.timeout"]`（含 `retryable=True`）、
  请求携带的 Intent `model_dump_json()` 逐字节不变；
- 既有测试 `test_provider_failure_becomes_a_recoverable_issue` 继续通过（单次 timeout 语义
  在不可重试场景下不变）。

**与 adapter 层正交**：Engine 层不 sleep、不退避（与 Interpreter `_resolve_with_single_repair`
同口径）；`Settings.llm_timeout_seconds` / `Settings.http_max_retries` 与 adapter 内
`0.5s × 2^n` 退避**一律未动**。

### B. 缓解二：prompt 保语义精简 + 版本 bump

| prompt | patch 001 后 | patch 002 后 | Δ | 版本 |
|---|---|---|---|---|
| Interpreter 系统提示词 | 5 660（v3） | **4 604**（v4） | **-1 056（-18.7%）** | `interpreter.v3 → v4` |
| FeedbackEngine 系统提示词 | 4 485（v2） | **3 657**（v3） | **-828（-18.5%）** | `feedback.v2 → v3` |

- v1 基线：Interpreter 3 363、Feedback 3 512 → v4/v3 相对 v1 为 **+36.9% / +4.1%**
  （v3/v2 时为 +68.3% / +27.7%）。
- **被断言的关键句全部逐字保留**：patch 001 回归文件中的 6 个稳定 marker
  （`COUNTING`、`LOCATIVE-FREE TEXT`、`COUNT_SCOPE_MARKER`、`EXPLICIT_PLACE_MARKER`、
  `MINIMAL_MARKER`、`CONFLICT_MARKER`）与两类 feedback 边界 marker
  （`CLEAR IS PER-PATH`、`PRESERVE SCOPE`）一字未动、断言全部继续通过。**故本轮没有需要改写的
  规则关键句断言**，仅更新了 6 处版本字面量（3 个 interpreter 文件、3 个 feedback 文件）。
- 新增字符数上界守卫（只钉方向、不钉死值）：Interpreter `< 5000`、Feedback `< 4000`。

#### 语义等价对照（原句 → 精简句 → 理由；只列实质改写，全部断言句原样保留）

Interpreter：

| # | 原句（v3） | 精简句（v4） | 为何语义等价 |
|---|---|---|---|
| 1 | rule 2：`...facet objects (subject, composition, environment, style, lighting, camera, color, resolutions, pinned_paths, or any Intent envelope).` | `...facet objects.` | facet 枚举与 rule 5（只允许 ALLOWED PATHS）重复；禁止"返回完整 Intent/facet"本身未变 |
| 2 | rule 3：`Never output IDs, revisions, timestamps, session/workflow state, confidence scores, or confirmation data.` | `Never output IDs, revisions, timestamps, session/workflow state or confidence scores;` | 去掉"confirmation data"（属系统管理数据，与同句前半重复）；禁止项集合未缩小 |
| 3 | rule 6 尾：`Never touch a path the user did not mention. Untouched fields stay exactly as they are.` | `Never touch a path the user did not mention.` | 第二句是同一禁止的复述；rule 15 已覆盖"未提及路径保持缺失" |
| 4 | rule 6：每条 CLEAR/PIN/UNPIN 后各写 `must NOT carry value or resolution` | 提到 rule 6 标题一处：`(CLEAR/PIN/UNPIN must NOT carry value or resolution)` | 约束内容与作用对象完全一致，只是去重 |
| 5 | rule 7 `user_delegated`：`("你决定", "随便", "按你推荐的")` | 删除此处中文例子（rule 8 仍保留 `"你决定"`、`"就按你推荐的"`） | 委托语义（需 pending question、沉默≠委托）逐字保留；例子未从 prompt 消失 |
| 6 | rule 8：`the user cannot delegate that path.` | `delegation is impossible.` | 同一约束（`allow_delegate=false` 时不可委托） |
| 7 | rule 9：`"背景不要海边" / "not the seaside" without saying what the background should be` | `"背景不要海边" with no replacement stated` | "不猜替代值 + 写 unresolved_language"的核心语义不变，例子精简 |
| 8 | rule 10：`...A conflict is NEVER a reason to emit fewer deltas: every value the user states still gets its own delta.` | `...A conflict is NEVER a reason to emit fewer deltas.` | 冒号后是前句的展开；被断言 marker 原样保留，冲突必须保留双方 delta |
| 9 | rule 12：`...("两只" / "two", "三只", "一群")...` | `...("两只" / "two")...` | 例子减少；"显式数字才算数量、单数名词/量词/不定冠词不算、不得默认 1"全部保留（`a classifier` 明确保留） |
| 10 | rule 13：例 `"地点是客厅" / "地点是海边沙滩" / "in a park", "location: the beach"` 与 `"lying by the fireplace", "on a sofa"` | 保留 `"地点是海边沙滩" / "in a park", "location: the beach"` 与 `"趴在壁炉边" / "坐在窗边" / "趴在沙发上"` | 明示地点必须提取、自由处所短语不得吞并 `environment.location` 的核心语义与断言句全部保留，仅删多余例子 |
| 11 | rule 14：`If a request seems to require more than one clear, emit only the paths the user actually named.` | `Emit only the named paths.` | 同一"只 CLEAR 用户点名路径"约束 |
| 12 | rule 15：括号例 `(a message that states a subject, a style, an environment mode, an explicit location, a framing and a lighting value yields one delta for each of those paths)` | 删除该例 | `MINIMAL never means fewer... every path the user explicitly addresses MUST get its own delta` 与"No rule above... authorises dropping"全部保留 |

FeedbackEngine：

| # | 原句（v2） | 精简句（v3） | 为何语义等价 |
|---|---|---|---|
| 1 | 引言逐个罗列 artifact 与 `The user has just looked at a generated image...` | `You read ONE feedback message about a generated image plus read-only artifacts (...)` | 输入集合与"只裁决、不应用"边界不变 |
| 2 | rule 4：`...session/workflow state, confidence scores, or confirmation data.` | `...session/workflow state;` | 属系统管理数据；禁止输出系统字段的约束不变 |
| 3 | rule 6：五个 bullet 分别列 SET/CLEAR/PIN/UNPIN 与 `Never touch a path...` | 合并为一句：`SET writes...; CLEAR removes...; PIN preserves...; UNPIN only releases...; Never touch...` | 四个操作定义与全部限制（PIN 不写值/不改值、UNPIN 不授权重设计、CLEAR/PIN/UNPIN 不带 value/resolution）逐条保留 |
| 4 | rule 8 尾：`...and it must NOT pull in paths of a facet the user did not mention. Never add a preserve path for a facet the user did not name.` | `...and must NOT pull in an unnamed facet.` | 两句同义；`其他都不变`/`everything else` 只覆盖点名范围、不得 PIN 全部有值路径、不得 SET preserve 路径全部保留（断言 marker 原样） |
| 5 | rule 11：`...put the reason in clarify_reason. clarify_path MUST be one of the ALLOWED PATHS. If you cannot identify one path, set clarify_path to null.` | `...with the reason in clarify_reason; clarify_path MUST be in ALLOWED PATHS or null.` | 同一"不猜替代值、clarify_path 必须白名单或 null"约束 |
| 6 | rule 13：`...They are not user facts and you must not treat them as new requirements. Do not invalidate, rewrite or re-select them; deterministic carry code does that.` | `...they are not user facts, do not treat them as new requirements, and do not invalidate, rewrite or re-select them; deterministic carry code does that.` | 逐句同义 |

---

## 变更文件

| 文件 | 类型 | 变更 |
|---|---|---|
| `visual_intent_agent/feedback/engine.py` | 修改 | `MAX_FEEDBACK_ATTEMPTS=2`；`_complete_with_retries` + `_is_retryable_provider_error`；prompt v3 精简；`FEEDBACK_PROMPT_VERSION` v2→v3；docstring `[Rev.3]` |
| `visual_intent_agent/feedback/__init__.py` | 修改 | 再导出 `MAX_FEEDBACK_ATTEMPTS`（向后兼容） |
| `visual_intent_agent/feedback/models.py` | 修改 | 仅 docstring 准确性（引擎重试语义） |
| `visual_intent_agent/intent_engine/prompts.py` | 修改 | prompt v4 精简；`INTERPRETER_PROMPT_VERSION` v3→v4；docstring `[Rev.4]` |
| `tests/feedback/test_feedback_retry.py` | **新增** | 6 条引擎级重试回归（红→绿） |
| `tests/feedback/test_feedback_boundaries.py` | 微调 | 版本字面量 + 1 条精简守卫 |
| `tests/feedback/test_feedback_engine.py` | 微调 | 版本字面量 |
| `tests/feedback/test_feedback_public_api.py` | 微调 | 版本字面量 |
| `tests/intent_engine/test_interpreter_prompts.py` | 微调 | 版本字面量 |
| `tests/intent_engine/test_interpreter_boundaries.py` | 微调 | 版本字面量 |
| `tests/intent_engine/test_interpreter_v3_boundaries.py` | 微调 | 版本字面量 + 1 条精简守卫 |
| `docs/handoffs/architecture_decision_004.md` | **新增** | 架构修订记录 |
| `docs/handoffs/v0_3_step_06_patch_002.md` | **新增** | 本文件 |
| `evaluation/reports/fix_traceability.md` | **追加** | patch 002 章节（首轮与 patch 001 原文未动） |

真实调用探针脚本只在 `/tmp`（`/tmp/via_patch002/via_probe_patch002.py`、
`/tmp/via_patch002/via_s04_probe.py`），不进交付。

---

## 公开接口

- **无破坏性变更**：唯一新增公开名是常量 `MAX_FEEDBACK_ATTEMPTS`（`feedback` 包
  `__all__` 与 `feedback.engine.__all__` 各加一项，向后兼容）。
- `FeedbackEngine.analyze(self, request)` 签名不变；`FeedbackResult` 12 字段不变；
  `FEEDBACK_OUTPUT_SCHEMA` / `INTERPRETER_OUTPUT_SCHEMA` 与内部人读 Schema 逐字未变；
  无新 Issue code、无新状态迁移、无落盘 schema 变化。
- 版本常量：`INTERPRETER_PROMPT_VERSION="interpreter.v4"`；
  `FEEDBACK_PROMPT_VERSION="feedback.v3"`；`POLICY_VERSION="policy.v1"`（未变）。

---

## 真实调用记录

- **预算**：≤24 次逻辑 `provider.complete` 调用、零图片生成。
- **实际**：**23 次逻辑调用**（5+5+3+5+3+2，见下），**0 次真实图片生成**
  （feedback 探针用离线 `FakeImageProvider` 装配 P3 session 取得合法 `FeedbackRequest`）。
- 每次逻辑调用内部仍受冻结 adapter 策略影响（最多 3 次 HTTP 尝试 × 60s 读超时），
  因此单条 `provider.timeout` 的 elapsed ≈ 182s。
- 探针口径：直连真实 Provider，用真实 `Interpreter` / `IntentEngine` / `FeedbackEngine`
  跑**精简后**的 prompt，记录输入摘要 / 输出 delta / 耗时。

最终文本（Interpreter v4 4 604 字符 / Feedback v3 3 657 字符）的权威探针记录：

| # | 探针 | 输入 | 逻辑调用 | elapsed | 输出 delta / 裁决 | 判定 |
|---|---|---|---|---|---|---|
| 1 | s04-t1 | `一只黑猫坐在窗台上，写实摄影风格，摄影棚环境，地点是海边沙滩，中景，柔和光线。` | 1 | 181.9 s | `provider.timeout`（0 delta） | P6 复现 |
| 2 | s04-t1 重试 | 同上 | 1 | 113.4 s | SET `subject.description=黑猫`、`subject.pose_action=坐在窗台上`、`style.primary=写实摄影`、`environment.mode=摄影棚`、`environment.location=海边沙滩`、`composition.framing=中景`、`lighting.character=柔和光线`；`count=None`；命中 `policy.hard_conflict.environment_mode_location`；`ready=False` | ✅ |
| 3 | s01-t1 | s01-complete-001 t1 原文（橘猫蜷睡客厅） | 1 | 42.5 s | 7 路径完整（description/pose/style/mode/location/framing/lighting）；`count=None`；`ready=True` | ✅ |
| 4 | p4-sofa | `画一只猫在沙发上` | 1 | 38.2 s | SET `subject.description=猫`、SET `subject.pose_action=在沙发上`；**无 `count`、无 `location`** | ✅ |
| 5 | rule13-place | `一只黑猫坐在窗台上，地点是海边沙滩，中景。` | 1 | 22.4 s | description=黑猫、pose=坐在窗台上、**location=海边沙滩**、framing=中景；`count=None` | ✅ |
| 6 | feedback-pin-set | `这只猫的形象我很满意，锁定它别再变了，另外把光线改成戏剧性一点的。` | 1 | 47.9 s | `revise`：**`PIN subject.description`** + **`SET lighting.character=dramatic`**；`preserve_paths=["subject.description"]`；`issues=[]` | ✅ |

达标：① s04 t1 七路径完整 + 冲突命中 + `ready=False`；② s01 t1 七路径完整、`count` 不凭空
赋值、`ready=True`；③ P4 反例不产生 `count`/`location`；④ rule 13 `location` 提取；⑤
feedback PIN+SET。**全部达标**。

**P6 局限的实测证据**：s04 t1 在最终文本上首试仍 `provider.timeout`；同一探针批（4590 字符
版本）曾出现**连续三次**完整调用全超时（181.9 / 181.9 / 182.0 s），达标依赖后续重试成功。
另有探针批中 s04 t1 两次完整调用连续超时（181.9 / 182.0 s）后第 3 次成功。这直接证明
**P6 只能概率性缓解、不能消除**。

---

## 重试的 FakeLLM 回归测试（红 → 绿形态）

临时把 `MAX_FEEDBACK_ATTEMPTS` 置 1（等价 v0.2 无引擎级重试）：

```text
# 红
$ uv run pytest tests/feedback/test_feedback_retry.py -q
3 failed, 3 passed
FAILED ::test_retryable_timeout_retries_and_applies_the_second_attempt_delta
  E assert 1 == 2   # 第一次 timeout 即降级，第二次脚本从未被调用，SET count 丢失
FAILED ::test_retry_exhausted_degrades_to_recoverable_clarify_without_state_change
FAILED ::test_retry_budget_matches_the_interpreter_precedent

# 绿（恢复 MAX_FEEDBACK_ATTEMPTS = 2）
$ uv run pytest tests/feedback/test_feedback_retry.py -q
6 passed
```

覆盖用例：① `timeout → 成功`（第二次 delta 保留、同一请求被调用两次、`issues==[]`）；
② `timeout ×2`（recoverable clarify、零候选、状态逐字节不变）；③ `auth` / `invalid_request`
（不重试、只调一次、`retryable=False`）；④ 非法 JSON（不重试、只调一次）；⑤ 重试预算 ==
`MAX_INTERPRETATION_ATTEMPTS` == 2。

---

## 版本号变化

| 常量 | patch 001 后 | patch 002 后 |
|---|---|---|
| `INTERPRETER_PROMPT_VERSION` | `interpreter.v3` | **`interpreter.v4`** |
| `FEEDBACK_PROMPT_VERSION` | `feedback.v2` | **`feedback.v3`** |
| `POLICY_VERSION` | `policy.v1` | `policy.v1`（未变） |

---

## 测试与实验结果

```text
$ uv run pytest -q
2151 passed, 3 deselected in 6.40s     # 2143 基线 + 6 重试回归 + 2 prompt 精简守卫

$ uv run pytest tests/evaluation -q
243 passed

$ uv run pytest tests/feedback tests/intent_engine -q
181 passed

$ uv run ruff check visual_intent_agent/feedback visual_intent_agent/intent_engine \
      tests/feedback tests/intent_engine
All checks passed!
```

冻结物哈希复核：`evaluation/protocol.md` `70cc0b3c…`、
`evaluation/fixtures/core_v0_3.jsonl` `05195347…`、
`evaluation/annotations/core_v0_3.jsonl` `d1712aab…`、
`evaluation/configs/gate_a_v0_3.json` `3b6159f8…` —— 与
`evaluation/frozen_manifest_v0_3.json` **逐字节一致**。

---

## 给 Step 07 的更新风险清单（P6 缓解后的残留风险）

1. **P6 只能概率性缓解、不能消除（最高优先）**：引擎级重试把"单次超时挟持"降为"连续两次
   完整引擎调用都超时（每次内部最多 3 次 HTTP 尝试）才挟持"。本 patch 已实测到连续三次
   完整调用全超时。Step 07 约 130+ 次 System B LLM 调用中，任一次超时落在 PIN/accept
   敏感链路仍可能机械触发 `gate_a_blocked`，可能让 Step 07 因 Provider 波动而非产品质量
   被判 No-Go。**评审时先查 `feedback_results.issues` / `llm_calls` 是否含
   `provider.timeout`，再判产品缺陷。**
2. **重试会改变调用/延迟计数**：重试在同一 turn 产生两条 `role=feedback_engine`
   `llm_calls`；评测的调用数/延迟统计随之上升，须与 Baseline A 对照说明，不得计为产品缺陷。
3. **prompt 精简未证明因果**：v4/v3 更短，但不能声称超时率因此下降（patch 001 已记载 v1
   同样超时）。若 Step 07 仍高频超时，正确下一步在 Provider/评测口径层（超时配置、
   Provider 选择），不是继续在引擎层叠加重试次数——已证明连续两次完整调用仍可能双双超时。
4. **精简后的真实覆盖有限**：v4 仅 4 条真实探针（s04/s01/P4/rule13）、v3 仅 1 条 feedback
   探针；`must_not_clarify_paths`、多轮 PIN/UNPIN、UNPIN+CLEAR 同批次等真实行为仍需
   Step 07 正式复评（多案例多 rep）覆盖。
5. **解析失败仍不重试**：Step 07 若大量出现 `feedback.unparseable_output.*`，那是模型输出
   畸形（被测系统行为），按冻结语义立即降级；不得用重试掩盖，也不得据 P6 口径记为
   Provider 波动。
6. **越权 CLEAR 的最终防线仍是 prompt**：rule 9 未变；若 Step 07 再见越权 CLEAR，正确
   下一步是给 `EvidenceRef` 增加路径范围字段，而不是继续加 prompt 文字。
7. **探针口径非正式**：本 patch 的真实调用只是定向探针、不进入评分；所有指标结论以
   Step 07 正式复评为准。

---

## 是否满足验收条件

**是。**

| # | 验收条件 | 核对 |
|---|---|---|
| 1 | 缓解一：FeedbackEngine 引擎级重试对齐 `MAX_INTERPRETATION_ATTEMPTS=2` 先例、只重试可重试 Provider 错误、降级语义不变 | 是；`MAX_FEEDBACK_ATTEMPTS=2` + `code in RETRYABLE_CODES` + `_provider_failure_result` 零改动；6 条 FakeLLM 回归红→绿 |
| 2 | 缓解二：Interpreter + Feedback prompt 保语义精简、版本 bump、关键句断言语义不变 | 是；-18.7% / -18.5%；全部被断言关键句逐字保留、断言未删；`interpreter.v4` / `feedback.v3` |
| 3 | 真实调用 ≤24 次、零图片、覆盖 s04/s01/P4 反例/rule13（+ feedback 探针）并记录 | 是；**23 次逻辑调用**、0 图片、6 条探针记录（含 elapsed 与 delta），全部达标 |
| 4 | 架构修订记录 `architecture_decision_004.md` | 是；含模块/原行为/新行为/最小性/合同兼容性/P6 局限 |
| 5 | `fix_traceability.md` 追加 patch 002（首轮与 patch 001 不动） | 是；文件由 711 → 923 行，纯追加 |
| 6 | 全量离线测试绿、`tests/evaluation 243` 绿、冻结物 sha256 一致 | 是；`2151 passed`；`243 passed`；四文件哈希一致 |
| 7 | 未改冻结配置、v0.2 降级语义、`evaluation/**` 既有文件、`pyproject.toml`、`api.md`、`.env` | 是；仅动允许清单内文件 + 新增文档/测试 + 追加报告 |
