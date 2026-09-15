# MVP v0.3 Step 06 交接记录

任务：MVP v0.3 Step 06 —— 证据驱动的定向修正。按 Step 05 归因报告与
`evaluation/reports/failure_catalog.jsonl`，对用户裁定的 **P1～P5 五项产品缺陷**
做最小修复：每项先从原始轮记录复现失败 → 写回归测试（修复前红）→ 最小实现修改
（转绿）→ 记录追溯。评测侧缺陷 Q1/Q2/Q4 按用户裁定**不修**（口径冻结，Step 07 同口径复评）。

---

## 完成内容

1. **五项修复全部落地**，每项都有"原始记录复现 → 红 → 绿 → 追溯"四段证据，
   详见 `evaluation/reports/fix_traceability.md`：

   | 项 | 根因（一句话） | 修改 | 结果 |
   |---|---|---|---|
   | P1 越权 CLEAR（L1 阻断） | s08-pin-unpin-001 t5/t3 是 **image_feedback** 轮：Feedback Interpreter 把"把猫去掉"扩成 `CLEAR subject.description` + `CLEAR subject.count` + `CLEAR subject.pose_action`；Validator 的路径级证据检查只认 Pending Question 证据，普通消息证据无法证明路径范围 | `feedback/engine.py` rule 9 + `intent_engine/prompts.py` rule 14（同一边界） | 回归 7 条转绿 |
   | P2 冲突词表英文单语 | 词表全英文，且 `_words` 把整段中文压成单 token，中文词表项永远匹配不上 → 12 条预期冲突 0 命中、3 条 hard 在 t1 漏检直接出图 | `policy/decision_policy.py`：五组词表补中文同义词 + CJK 子串匹配（英文词序列语义不变） | 回归 25 条转绿 |
   | P3 assess 未携带 ExecutionRevision | `assess` 四处调用点全用默认 `execution_context=None`，`framing_aspect_mismatch` 是死规则；执行上下文早已由产品自建并落库 | `intent_engine/{models,engine}.py` + `workflow/{service,review}.py` 接线（新增**可选**字段，不改签名） | 回归 14 条转绿 |
   | P4 无依据具体化 | 单数名词短语被推成 `subject.count=1`（19 个案例运行），处所描述被吞进 `environment.location`；确定性层没有"值必须有依据"的规则 | `intent_engine/prompts.py` rule 12/13/15 | 回归 9 条转绿 |
   | P5 PIN 范围过宽 | s07-single-field-002 t2 的"其他都不变"被展开成 5~6 条 PIN，含用户未点名的 facet | `feedback/engine.py` rule 8（PRESERVE SCOPE） | 回归 7 条转绿 |

2. **prompt 版本号提升**（产品行为修订惯例）：
   `INTERPRETER_PROMPT_VERSION` `interpreter.v1 → interpreter.v2`；
   `FEEDBACK_PROMPT_VERSION` `feedback.v1 → feedback.v2`。
   输出 Schema（`INTERPRETER_OUTPUT_SCHEMA` / `FEEDBACK_OUTPUT_SCHEMA`）、
   `POLICY_VERSION`（`policy.v1`）、domain/persistence 合同**均未变**。

3. **既有测试最小修订 3 处**（全部只钉版本字面量，不放松任何不变量）：
   `tests/intent_engine/test_interpreter_prompts.py`、
   `tests/feedback/test_feedback_public_api.py`、
   `tests/feedback/test_feedback_engine.py` 各 1 条版本断言；
   另有 `tests/intent_engine/ie_helpers.py` 纯新增 `execution()` 工厂。
   逐条理由见 `fix_traceability.md` §6。

4. **未触碰**：`evaluation/**` 既有文件（本步只新增 `fix_traceability.md`）、
   `tests/evaluation/**`、冻结数据集/标注/协议/配置/Baseline、
   `docs/task_books/**`、`pyproject.toml`、`.env`、api.md、既有 handoff。

---

## 变更文件

产品代码（7 个，全部在允许范围内）：

- `visual_intent_agent/intent_engine/prompts.py`（prompt 修订 + 版本号）
- `visual_intent_agent/intent_engine/models.py`（`IntentResolveRequest` 新增可选字段）
- `visual_intent_agent/intent_engine/engine.py`（接线 `assess`）
- `visual_intent_agent/policy/decision_policy.py`（词表语言覆盖 + CJK 匹配）
- `visual_intent_agent/workflow/service.py`（接线 ExecutionRevision）
- `visual_intent_agent/workflow/review.py`（接线 ExecutionRevision）
- `visual_intent_agent/feedback/engine.py`（prompt 修订 + 版本号）

产品测试（4 个新增 + 4 个既有微调）：

- 新增：`tests/policy/test_policy_conflict_cjk.py`（25）、
  `tests/intent_engine/test_interpreter_boundaries.py`（9）、
  `tests/feedback/test_feedback_boundaries.py`（7）、
  `tests/workflow/test_workflow_execution_context.py`（5）
- 微调：`tests/intent_engine/test_interpreter_prompts.py`、
  `tests/feedback/test_feedback_public_api.py`、
  `tests/feedback/test_feedback_engine.py`（版本断言）；
  `tests/intent_engine/ie_helpers.py`（新增 helper）

交付文档（2 个新增）：

- `evaluation/reports/fix_traceability.md`
- `docs/handoffs/v0_3_step_06_handoff.md`（本文件）

---

## 公开接口

- **无破坏性变更。** `IntentResolveRequest` 新增**可选**字段
  `execution_context: ExecutionRevision | None = None`；旧调用方不传即保持 v0.2 行为。
- 未新增/删除任何模块、公开函数或 `__all__` 名称；`IntentEngine.resolve` /
  `WorkflowService.submit_message` / `ReviewService.submit_feedback` /
  `FeedbackEngine.analyze` 签名不变。
- Artifact 兼容性：未改 Repository 表结构、payload 格式、落盘 schema；
  `execution_context` 只在内存中传递，不落盘。
- 版本常量（供 Step 07 记录/比对）：
  `INTERPRETER_PROMPT_VERSION="interpreter.v2"`、
  `FEEDBACK_PROMPT_VERSION="feedback.v2"`、`POLICY_VERSION="policy.v1"`。

---

## 测试与实验结果

```text
# 修复前基线（Step 05 结束）
$ uv run pytest -q
2086 passed, 3 deselected in 6.48s

# 修复后（Step 06 结束）
$ uv run pytest -q
2132 passed, 3 deselected in 6.48s          # +46 新增回归测试

# 受影响模块
$ uv run pytest tests/intent_engine tests/validation tests/policy tests/workflow \
      tests/feedback tests/realization tests/e2e tests/generation -q
1314 passed
```

红 → 绿证据（修复前失败形态摘要，完整输出见 `fix_traceability.md` §1.4/2.4/3.3/4.4/5.3）：

| 新测试文件 | 修复前 | 修复后 |
|---|---|---|
| `tests/policy/test_policy_conflict_cjk.py` | 12 failed / 12 passed | 25 passed |
| `tests/intent_engine/test_interpreter_boundaries.py` | 7 failed / 4 passed | 9 passed |
| `tests/feedback/test_feedback_boundaries.py` | 4 failed / 2 passed | 7 passed |
| `tests/workflow/test_workflow_execution_context.py` | 3 failed / 2 passed | 5 passed |

评测层未被触碰的证据：

```text
$ uv run pytest tests/evaluation -q
243 passed

frozen_manifest_v0_3.json 四文件 sha256 复核：全部一致
  evaluation/protocol.md                  70cc0b3c…
  evaluation/fixtures/core_v0_3.jsonl     05195347…
  evaluation/annotations/core_v0_3.jsonl  d1712aab…
  evaluation/configs/gate_a_v0_3.json     3b6159f8…
```

### 诊断性真实复跑（编排方追加）

本步实现阶段未运行真实 Provider；以下两轮诊断性复跑由编排方在冻结 Runner 上执行
（均非正式口径：`--repetitions 1`、独立 nonce，不作为 Gate A 正式结果）。

**诊断一**（`erun_e7413108dc8a6104`，nonce=step06-diag，首轮修复后）：
4 案例（s08/s04/s01/s07）× 1 重复。System B 2 completed / 2 failed。
- s08-pin-unpin-001：L1 违例（t3 `CLEAR subject.description` 被应用）。取证结论：
  **P6 超时挟持**——t2 FeedbackEngine 调用 60s×3 超时（`fbk_f9cee845…`，
  issue=`provider.timeout`，decision 降级 clarify、零候选）→ PIN 未落库 → t3 的
  CLEAR 属合法产品行为（冻结 Validator 只拒 CLEAR-pinned）→ 撞标注 forbidden。
  rule 9 已把首轮 3 路径过度展开收敛为 1 路径定向 CLEAR。
- s04-conflict-001：t1 **零提取**（零 revision），下游 conflict 0/1、无生成、
  `evaluation.no_generation_under_review` 失败。patch 001 取证：两条 interpreter
  调用均 `provider.timeout`（`ellm_7b252efecede117b`/`ellm_98563ee0da56a047`，
  各 181.9s）；v2 prompt 在无超时时可完整提取 7 delta——零提取同为 P6 级联，
  非措辞回归（措辞的排版/泛化缺陷仍真实存在，见 patch 001）。
- s01/s07：completed；P4（零 must_remain_unset 违例、count 不凭空赋值）与
  P5（修改轮仅 2 delta、零过度 PIN）在真实运行验证有效。

**patch 001**（interpreter v2→v3，详见 `docs/handoffs/v0_3_step_06_patch_001.md`
与 `evaluation/reports/fix_traceability.md` patch 001 章节）：修复 rule 12/13 粘连
排版 bug、收窄 rules 12/13/15 过度泛化、rule 10 补"冲突不是少产 delta 的理由"；
14 次定向真实调用验证三组探针全部达标；s08 首轮 8 次 L1 违例取证分解 =
(a) P1 本体 8/8（首轮两 rep 的 t2 PIN 均成功落库，违例是 Feedback 过度展开）、
(b) P6 挟持 0/8、(c) 脚本错位 0 直接。

**诊断二**（`erun_f78691dffb0f7494`，nonce=step06-diag2，patch 001 后）：
同 4 案例 × 1 重复。**双侧 4/4 全部 completed**（流程性失败清零）。
- conflict_detection **1/1**（s04 t1 完整提取 7 delta、`hard_conflict.environment_mode_location`
  命中、blocking 流程正确）→ P2/P3 端到端验证通过；
- clarification_precision **1.0**（2 问 / 0 坏问 / 0 次 ready 轮提问）、
  missing_decision_recall **1/1**、delta_accuracy **0.969**（33/34，out_of_scope 1）、
  delegation_scope 1.0；
- s08 PIN 链路完全符合标注：t2 PIN 落库 → t3 CLEAR-pinned 被确定性拒绝
  （`validation.clear_requires_unpinned_path`，命中 expected_rejections）→
  t4 UNPIN（同批 CLEAR 按冻结语义拒绝）→ t5 解锁后 CLEAR 应用。**P1 端到端验证通过**；
- **新 L1 违例：s07-single-field-002 t3**（`subject.count None→2`）。取证结论：
  **仍为 P6 超时挟持**——t2 FeedbackEngine 调用超时（`ellm_59fdeb3f627e4de5`，
  181.9s，`fbk_27b7f4b4…` decision=clarify 零候选）→ `SET count=2` 丢失 →
  t3（"对，两只，完成"）被 FeedbackEngine 依据系统真实状态（count 未设置）
  合理判为 revise 并落库 → 撞 t3 forbidden。产品行为在其自身状态下合理，
  非产品缺陷；
- 本轮 System B 侧 12 次 LLM 调用中 1 次超时（≈8%）。

**编排方结论**：P1~P5 五项产品修复全部端到端验证有效；连续三轮真实运行
（首轮 s09-multiturn-002、诊断一 s08、诊断二 s07）均出现且仅出现一次 P6
超时挟持事件，P6（Provider 波动，Step 05 判定 worth_fixing=False、用户裁定
不修）已成为 L1 100% 门槛的唯一残留威胁。是否对 P6 做缓解（超出已批准修复
范围，需用户裁定）以及在 Step 07 报告中如何呈现"机械 L1 结果 + 取证分解"，
由编排方提交用户决策；本步在诊断二完成后按用户指示暂停。

---

## 原始 Artifact 位置

- 失败证据源（只读）：`outputs/evaluation_runs/erun_d35a33ffc18d6c1f/`
  （`cases/*.jsonl` 逐轮记录、`summary.json`），本步逐条引用其轮记录 ID：
  `eturn_24807db8aadfa204`、`eturn_12a7525d937d679a`（P1）、
  `emet_6646ffe53586c7bb`/`emet_5faa370f19c5260b`/`emet_a4db88a9d5f87ee8`（P2）、
  `emet_58f72f7ec1585b26`/`emet_99619029b4aefa74`/`emet_2d9a6f824666d975`（P3）、
  `emet_084e77929579b45c`/`emet_6bb1e99d2e0cb4ab`（P4）、
  `emet_78618550965e1762`/`emet_8d04c6d11fdeb3e0`（P5）。
- 上游报告：`evaluation/reports/gate_a_initial.{md,json}`、
  `evaluation/reports/failure_catalog.jsonl`、`docs/handoffs/v0_3_step_05_handoff.md`。
- 本步交付：`evaluation/reports/fix_traceability.md`、本文件。

---

## 已知限制

1. **P1/P4/P5 是 prompt 边界修复，没有确定性断言**：回归测试证明"约束已写入系统
   提示词并真实送达 Provider"，不能证明真实模型一定遵守。若 Step 07 复跑仍出现
   越权 CLEAR / 臆测 count / 过宽 PIN，正确的下一步是新的证据合同
   （例如 `EvidenceRef` 增加路径范围、`preserve_paths` 绑定原文片段），
   而不是继续加强措辞。
2. **P2 是双重根因**：只补中文词表不够，`_contains_phrase` 还必须对 CJK 词表项
   启用子串匹配；英文词序列语义已用回归用例钉住不变。词表覆盖仍可能漏掉
   未收录的中文说法（属词表维护，不是新增缺陷）。
3. **P3 只接线不新增语义**：execution conflict 仍"只报告、不阻塞"（冻结语义），
   指针缺失时仍返回 None（不伪造输出尺寸）。
4. **未修 Q1/Q2/Q4 与 P6**：Preservation 的无 Prompt 对仍计 `kept=0`、Runner 仍按
   脚本 kind 路由、`guard_bypassed` 字段名未改、Provider 超时仍无编排层降级。
   Step 07 必须复用同口径，这些效应会原样重现，不得据此判产品缺陷。
5. **未运行真实 Provider**：所有指标变化均为推断，需 Step 07 复评确认。
6. **P5 修复强度最弱**：无确定性机制保证模型不再扩范围；Step 07 需重点核对
   `out_of_scope` 中 PIN 条数与 L1 `pinned_not_overwritten` 是否仍 44/44
   （漏 PIN 会让后续 SET 覆盖本应保护的路径）。

---

## 对下一步的输入

### 代码变更清单（Step 07 复评时生效）

| 文件 | 变更 |
|---|---|
| `intent_engine/prompts.py` | `interpreter.v1 → v2`；新增 rule 12（COUNTING）、13（LOCATIVE-FREE TEXT）、14（CLEAR IS PER-PATH）、15（最小 delta 集） |
| `intent_engine/models.py` | `IntentResolveRequest.execution_context`（可选，默认 None） |
| `intent_engine/engine.py` | `resolve` 的正常/失败路径都把 `execution_context` 传给 `assess` |
| `policy/decision_policy.py` | 五组冲突词表补中文同义词；`_has_cjk` + CJK 子串匹配；rule id / 语义 / `POLICY_VERSION` 不变 |
| `workflow/service.py` | `_current_execution_revision` + `submit_message` 接线 |
| `workflow/review.py` | `_current_execution_revision` + `_revise` / `_failure_resolution` 接线 |
| `feedback/engine.py` | `feedback.v1 → v2`；新增 rule 9（CLEAR IS PER-PATH）、10（最小 delta 集），改写 rule 8（PRESERVE SCOPE） |

### Prompt / 版本号变化

| 常量 | 旧 | 新 | 变更点 |
|---|---|---|---|
| `INTERPRETER_PROMPT_VERSION` | `interpreter.v1` | `interpreter.v2` | 只新增边界约束；输出 Schema 未变 |
| `FEEDBACK_PROMPT_VERSION` | `feedback.v1` | `feedback.v2` | preserve 范围收紧 + CLEAR 按路径 + 最小 delta 集；输出 Schema 未变 |
| `POLICY_VERSION` | `policy.v1` | **不变** | 词表是语言覆盖，不是新规则版本 |

### 预期改善的指标（供复评逐条核对）

| 指标 | 修复前 | 预期方向 | 主要依据 |
|---|---|---|---|
| L1 `unauthorized_fields_unchanged` | 42/44、8 违例、`gate_a_blocked=true` | **44/44、0 违例、`gate_a_blocked=false`** | P1（s08 t5/t3） |
| L2 `conflict_detection` | 0/12、`severe_failures=6` | **12/12、`severe_failures=0`** | P2（3 hard ×2）+ P3（6 execution） |
| L2 `delta_accuracy` out_of_scope / precision | 56 / 0.850 | 明显下降 / 上升 | P4（`subject.count` 30、`environment.location` 3、`composition.framing` 2、`style.description` 1）+ P5（PIN 13）+ P1（CLEAR 4） |
| L2 `must_remain_unset_violations` | 58 | 明显下降（预期仅剩其它形态） | P4 |
| L2 `missing_decision_recall` | 0.736（59/72） | 提升 | P4 让 s02/s09 的 blocking 缺失重新可见（约 8 个点） |
| L2 `clarification_precision` | 0.852（9 坏问） | 改善 | P1 减少 s08 的越权 CLEAR 引发的错误提问 |
| 运行失败轮 | 13（`invalid_state` 11 / `no_generation` 2） | 预期减少 ≥6 | P2 让 s04 三案例 t1 不再提前 ready；P4 让 s02 t2 不再提前 ready |
| L3 `unauthorized_addition` hits | B 6 / A 2 | 预期 B → 2 | P2 冲突不再进图 |
| L4 | 29 对 | **不预期变化**（本步未改图像链路；PIN/CLEAR 语义改善可能影响个别对，需人工复核） | — |

**不应期待改善**（口径冻结）：L3 `preservation`（Q1 失真仍按原口径）、
`guard_bypassed` 计数（Q4 未改）、`provider.timeout` 相关失败（P6 未修）、
Runner 的 `flow_deviation` 分类（Q2 未改）。

### 可能引入的新风险（Step 07 重点复核）

1. **过度保守导致澄清成本上升**：P4 让"地点在窗边/沙发上"这类表述不再自动落到
   `environment.location`，可能新增 blocking 缺失 → 提问变多、`must_clarify`
   满足数下降。核对 `提问总数 / 坏问 / ready 轮提问` 与 L4 的 `intent_alignment`。
2. **漏 PIN 风险**：P5 收紧后若模型少给 PIN，后续 SET 可能覆盖本应保护的路径 →
   核对 L1 `pinned_not_overwritten` 是否仍 44/44、s07/s08 的 `pinned_after` 是否合理。
3. **漏清风险**：P1 收紧 CLEAR 范围后，若用户确实要求"整体换掉主体相关的一切"，
   可能只清 `subject.description` → 核对 s08 t5 之后的 `blocking_unresolved_paths`
   与 `missing_decision_recall`（`subject.pose_action` 重新变 blocking 是**预期**行为）。
4. **数量不写导致 Prompt 缺项**：`subject.count` 不再由"一只"推得 → Prompt 可能
   没有数量描述，L4 需要人工确认图像未出现主体数量错误（数量 1 是生成常识默认，
   但不再是会话事实；这是标注口径的要求）。
5. **冲突词表误报**：已用全量轮次重放核验，新增 hard 命中只出现在标注要求的
   s04 三案例；execution 命中只出现在标注要求的 s02/s09 轮次。若复跑出现别的
   案例命中，应视为词表过宽并回到词表语义边界讨论。
6. **prompt 类修复的不确定性**：P1/P4/P5 依赖真实模型；若复跑仍见同类越界，
   说明需要新的证据合同而不是继续加 prompt 约束（见"已知限制 1"）。

### 复评口径提醒

- 本步**未**修改任何评测实现、数据集、标注、协议、Baseline 或评分；
  Step 07 必须使用同一冻结口径（`gate_a_protocol_v0_3`、同 config/annotations/dataset
  哈希），不得为提高分数调整口径。
- Q1/Q2/Q4 与 P6 的效应会原样重现：报告需继续如实分列"产品缺陷已修"
  与"口径/Provider 波动未修"，不得把后者的数值当成修复失败或成功。

---

## 是否满足验收条件

**是。** 任务书验收条件逐条核对：

| # | 验收条件 | 核对 |
|---|---|---|
| 1 | 每个代码改动都能追溯到 Step 05 的失败案例 | 是。`fix_traceability.md` §1～§5 每项都带案例 ID、轮记录/指标记录 ID、原始存储值与代码行；§8 文件级清单可反向对照 |
| 2 | 新回归测试修复前失败、修复后通过 | 是。四项新测试文件的"修复前失败形态摘要"已存档（§7），修复后 46 条全绿 |
| 3 | v0.2 核心不变量与全量离线测试继续通过 | 是。`uv run pytest -q` → 2132 passed，3 deselected；`tests/policy` 冻结 golden 未改且全绿 |
| 4 | 不修改冻结数据集、Baseline A 或正式评分实现 | 是。四文件 sha256 与 `frozen_manifest_v0_3.json` 一致；`tests/evaluation` 243 passed；`evaluation/**` 仅新增本步报告 |
| 5 | 不为单个样本硬编码答案 | 是。修复全部是规则边界（路径级 CLEAR、数量/处所证据边界、preserve 范围、语言覆盖），无任何按案例 ID/值判定的分支 |
