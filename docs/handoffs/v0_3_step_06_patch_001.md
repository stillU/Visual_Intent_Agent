# MVP v0.3 Step 06 patch 001 交接记录

任务：Step 06 首轮修复（P1~P5）之后，编排方诊断性真实复跑
`outputs/evaluation_runs/erun_e7413108dc8a6104/`（4 案例 ×1 rep，非正式口径）暴露问题，
本 patch 做**第二轮最小修复 + 取证**，为 Step 07 正式复评扫清障碍。

- 上游证据：`erun_e7413108dc8a6104`（诊断）、`erun_d35a33ffc18d6c1f`（首轮）、
  `evaluation/reports/fix_traceability.md`（首轮 + 本 patch 章节）、
  `docs/handoffs/v0_3_step_06_handoff.md`。
- 本 patch 范围：**A** 收窄 Interpreter v2 的过度泛化措辞（→ `interpreter.v3`）；
  **B** s08 L1 违例只读取证（a/b/c 分解）；**C** `questions_bad` 与 P2 重放核查。
- 未触碰：`evaluation/**` 既有文件（只追加 `fix_traceability.md`）、`tests/evaluation/**`、
  Step 01~05 交付物、冻结物、`docs/task_books/**`、`pyproject.toml`、`.env`、api.md；
  **未改** P6 超时/重试策略（冻结配置）与 v0.2 冻结行为语义。

---

## 完成内容

### A. Interpreter 过度保守回归：**根因更正 + 措辞收窄**

**根因更正（最重要）**：编排方假设的"s04 t1 零 delta = v2 措辞让模型不敢提取"
被运行记录与定向探针**否证**。诊断复跑 s04 t1 的 `llm_calls` 是两条
`provider.timeout`（各 181 9xx ms，`MAX_INTERPRETATION_ATTEMPTS=2`），turn latency
363 867 ms ≈ 2×181.9 s；t2 才产生唯一 revision。v2 探针在同一消息上正常返回时
**完整提取 7 条**；首轮 v1 也正常提取。零 delta 的真实根因是 **P6 Provider 超时级联**。

**但仍然收窄措辞**（v2 确有排版与泛化缺陷），版本
`INTERPRETER_PROMPT_VERSION` `interpreter.v2 → interpreter.v3`：

| 规则 | v2 → v3 收窄点 |
|---|---|
| 编号 | 修复 rule 12/13 之间**丢失的换行**（v2 被拼成 `never default it to 1.13. LOCATIVE…`） |
| 12 COUNTING | 明确"只约束 `subject.count`，不是少提取其它路径的理由" |
| 13 LOCATIVE-FREE TEXT | 明确"仅禁止把**自由处所短语**里的地点词吞进 `environment.location`"；用户以"地点是/位于/in/location:"**明示**给出地点时 **MUST** 提取（即使同句还有处所短语/第二个环境值） |
| 15 最小 delta 集 | 明确"**最小 ≠ 少于用户明示内容**"，明示路径必须逐条产出 |
| 10 冲突 | 明确"冲突绝不是少提取的理由" |

保护意图（P4：无依据不赋值）与 rule 14（CLEAR IS PER-PATH）语义未变；输出 Schema、
证据合同未变。**未改** `feedback/engine.py`（测量 v1→v2 只增加 973 字符，
无证据表明它是超时主因）。

### B. s08 L1 违例取证（只读，不修）

首轮 8 条违例（rep1 t5、rep2 t3 各 4 条）**100% 是 (a) Feedback Interpreter 过度展开
（P1 本体）**：`CLEAR subject.count` / `CLEAR subject.pose_action` 是用户从未点名的路径；
两 rep 的 t2 PIN 都成功落库、无超时。首轮 t4/t5 的 `invalid_state` 是 (a) 引起的状态
漂移**下游**，不是独立成因。

诊断复跑 2 条违例 **100% 是 (b) Provider 超时级联（P6）**：t2 feedback 调用
`provider.timeout`（181 948 ms）→ PIN 丢失 → t3 的 **CLEAR subject.description 单条**
（rule 9 生效，且与标注 expected 完全一致）被合法应用 → 2 条违例 + `WAITING_CLARIFICATION`
→ t4/t5 `invalid_state`。

(c) 脚本流程错位：0 条直接违例。首轮 rep1 t3 的
`feedback.unparseable_output.invalid_delta`（模型给 CLEAR 附带非法字段）是另一类模型输出
畸形，被冻结的可恢复降级吸收，未改变流程走向。

### C. 顺带核查

- **3 条 `questions_bad`**（s04 t1/t2、s08 t3，target 均为 `subject.description`）全部是
  超时下游（s04 零提取、s08 PIN 丢失），**不是独立提问逻辑缺陷**；`missed_must_clarify`
  同源。提取/PIN 恢复后应自行消失。
- **P2 确定性重放**：用首轮每轮 `intent_values_after` 重建 Intent 调当前 `assess()`，
  s04-conflict-001/002/003 的 t1 全部命中标注 hard conflict，**其它 19 个案例 0 误报**；
  首轮记录 `conflict_rule_ids` 全 0 → P2 修复仍成立，诊断的 `0/1` 只是 location 从未入库的下游。

---

## 变更文件

| 文件 | 类型 | 变更 |
|---|---|---|
| `visual_intent_agent/intent_engine/prompts.py` | 修改 | `interpreter.v2 → v3`；rule 10/12/13/15 措辞收窄 + 编号换行修复；docstring `[Rev.3]` |
| `tests/intent_engine/test_interpreter_v3_boundaries.py` | **新增** | 11 条 v3 收窄回归（prompt + FakeLLM 端到端） |
| `tests/intent_engine/test_interpreter_prompts.py` | 微调 | 版本字面量 `v2 → v3`（语义不变） |
| `tests/intent_engine/test_interpreter_boundaries.py` | 微调 | 版本字面量 `v2 → v3`（语义不变） |
| `evaluation/reports/fix_traceability.md` | **追加** | patch 001 章节（A/B/C + 真实调用记录 + 风险） |
| `docs/handoffs/v0_3_step_06_patch_001.md` | **新增** | 本文件 |

真实调用探针脚本只在 `/tmp`（`/tmp/via_probe.py`、`/tmp/via_replay_raw.py`、
`/tmp/via_p2_replay.py`），不进交付。

---

## 公开接口

- **无破坏性变更**：未改任何模块/函数/`__all__`/数据合同/落盘 schema/Repository 表。
- 版本常量：`INTERPRETER_PROMPT_VERSION="interpreter.v3"`；
  `FEEDBACK_PROMPT_VERSION="feedback.v2"`（未变）；`POLICY_VERSION="policy.v1"`（未变）。
- 输出 Schema（`INTERPRETER_OUTPUT_SCHEMA`）与 `interpreter._LLMOutput` 逐字未变。

---

## 真实调用记录（A 节预算）

- 预算：**14 次 `provider.complete` 逻辑调用**（≤20）、**0 次图片生成**；
  每次超时逻辑调用内部由冻结的 adapter 重试策略产生 ≤3 次 HTTP 尝试。
- 探针脚本会为"raw + 解析"各调用一次，故每次探针 = 2 次逻辑调用；
  `v3-s04t1` 的第二次调用超时，其 raw 用 `/tmp/via_replay_raw.py` **零调用**离线重放确认。

| # | 探针 | prompt | 输入 | elapsed | 输出（离线重放确认） | 判定 |
|---|---|---|---|---|---|---|
| 1 | `v2-s04t1` | v2 | s04 t1 | 181.9 s | `provider.timeout`，无 raw | 复现 P6 |
| 2 | `v2-s01t1` | v2 | s01 t1 | — | 7 delta，`count` 未设，ready | v2 基线正常 |
| 3 | `v2-s04t1-repeat` | v2 | s04 t1 | 292.0 s | **7 delta** + 命中 hard conflict | 证明非措辞问题 |
| 4 | `v3-s04t1` | v3 | s04 t1 | 214.7 s | **raw 7 delta**（含 `location=海边沙滩`、`mode=摄影棚环境`），`count=None`，命中 `policy.hard_conflict.environment_mode_location`，`ready=False` | ✅ |
| 5 | `v3-s01t1` | v3 | s01 t1 | 58.2 s | **7 delta**，`count=None`，`ready=True` | ✅ |
| 6 | `v3-p4-sofa` | v3 | `画一只猫在沙发上` | 51.4 s | `description=一只猫`、`pose=在沙发上`；**无 `count`、无 `location`** | ✅ P4 反例 |
| 7 | `v3-locative-plus-place` | v3 | `一只黑猫坐在窗台上，地点是海边沙滩，中景。` | 47.9 s | `description=黑猫`、`pose=坐在窗台上`、**`location=海边沙滩`**、`framing=中景`；`count=None` | ✅ rule 13 |

达标判定：s04 t1（探针 4）7 路径完整且冲突命中；s01 t1（探针 5）仍 7 路径完整、
`count` 不被凭空赋值；P4 反例（探针 6）不产生 `count`/`location`。全部达标。
（s04 t1 三次首次调用中 1 次超时、2 次成功，超时波动无法在预算内消除。）

---

## 测试与实验结果

```text
$ uv run pytest -q
2143 passed, 3 deselected in 6.52s          # 首轮 2132 + 11（新增 v3 回归）

$ uv run pytest tests/policy/test_policy_conflict_cjk.py \
      tests/intent_engine/test_interpreter_boundaries.py \
      tests/feedback/test_feedback_boundaries.py \
      tests/workflow/test_workflow_execution_context.py -q
46 passed                                    # 首轮 Step 06 的 46 条回归全部保持绿

$ uv run pytest tests/evaluation -q
243 passed

frozen_manifest_v0_3.json 四文件 sha256：逐字节一致（protocol 70cc0b3c…、
fixtures 05195347…、annotations d1712aab…、config 3b6159f8…）。
```

说明：本 patch 是**措辞修订**，"修复前红/后绿"不适用；改为
(a) 新测试对 v3 prompt 断言 + FakeLLM 端到端确定性断言，
(b) 复跑首轮 46 条回归证明未破坏。

---

## 给 Step 07 的风险清单

1. **P6 超时级联挟持 s08 L1（最高优先）**：s08 t2 的 feedback 超时 → PIN 丢失 →
   t3 合法 CLEAR 变 L1 违例 + `invalid_state`。Step 07 必须先查该 rep
   `feedback_results.issues` 是否含 `provider.timeout`，再判是否 P1 残留；
   建议 L1 结果按"PIN 是否落库"分两支呈现。
2. **s04 冲突检出依赖 t1 不超时**：提取已被证明完整，但 t1 若再超时，
   `conflict_detection` 会再次 0/1 —— 记 P6，不记 P2。
3. **prompt 变长**：Interpreter 系统提示词 v1→v3 由 3 363 → 5 660 字符（+68%）。
   无法证明它推高延迟（v1 的 s09-multiturn-002 同样超时），但应列为超时候选因素；
   若 Step 07 仍高频超时，可另立"精简 prompt + 保持边界"的版本（不改冻结配置）。
4. **v3 只经 5 条真实样本验证**：明示 7 路径、显式地点、单数 count、处所短语各通过；
   多轮 PIN/UNPIN、`must_not_clarify_paths` 的真实行为未用 v3 复跑，
   需 Step 07 正式复评（多案例多 rep）覆盖。
5. **诊断复跑口径非正式**（4 案例 ×1 rep）：本 patch 的真实调用只是定向探针，
   不进入评分；所有指标结论仍以待 Step 07 正式复评为准。
6. **越权 CLEAR 的最终防线仍是 prompt**：rule 9 在诊断中收敛为 1 条，但一旦
   模型再扩路径，冻结 Validator 仍会放行（Step 05 已确认的语义缺口）。
   若 Step 07 再见越权 CLEAR，正确下一步是给 `EvidenceRef` 增加路径范围字段，
   而不是继续加 prompt 文字。

---

## 是否满足验收条件

**是。**

| # | 验收条件 | 核对 |
|---|---|---|
| 1 | A：定位并收窄 v2 过度泛化措辞、版本升级、追溯记录 | 是；rule 10/12/13/15 逐条给出原文→修订，`interpreter.v3`，`fix_traceability.md` patch 001 |
| 2 | A：真实调用 ≤20 次、零图片，覆盖 s04/s01/P4 反例并记录 | 是；14 次逻辑调用、0 图片、7 条探针记录（含 elapsed 与 delta） |
| 3 | A：离线回归钉住版本与关键句，首轮 46 条回归保持绿 | 是；新增 11 条，`2143 passed`，46 passed |
| 4 | B：s08 L1 只读取证，a/b/c 分解与证据 | 是；首轮 8 违例 a=100%，诊断 2 违例 b=100%，c=0 直接 |
| 5 | C：`questions_bad` 比对 + P2 重放、无误报 | 是；3 条全为超时下游；P2 重放 3 案例命中、其它 19 案例 0 误报 |
| 6 | 冻结物未被改动、全量离线测试绿 | 是；四文件 sha256 一致；`2143 passed`；`tests/evaluation 243 passed` |
| 7 | 未改超时/重试/Provider 配置、v0.2 冻结行为语义、`evaluation/**` 既有文件 | 是；仅动 prompts.py + 2 处版本字面量 + 新增测试 + 追加报告 |
