# 修复追溯记录（MVP v0.3 · Step 06）

- 上游裁决：`evaluation/reports/gate_a_initial.md`（No-Go，可修复型）+
  `evaluation/reports/failure_catalog.jsonl`（11 条归因）。
- 修复范围（用户裁定）：`failure_catalog` 的 **P1～P5 全部五项产品缺陷**；
  评测侧缺陷 Q1/Q2/Q4 **不修**（口径冻结，Step 07 同口径复评）。
- 本步零触碰：`evaluation/**`（除本文件）、`tests/evaluation/**`、数据集/标注/协议/
  配置/Baseline、`docs/task_books/**`、`pyproject.toml`、`.env`、api.md。
- 冻结物哈希复核（Step 06 结束时）：`evaluation/protocol.md` `70cc0b3c…`、
  `evaluation/fixtures/core_v0_3.jsonl` `05195347…`、
  `evaluation/annotations/core_v0_3.jsonl` `d1712aab…`、
  `evaluation/configs/gate_a_v0_3.json` `3b6159f8…` —— 四文件 sha256 与
  `evaluation/frozen_manifest_v0_3.json` **逐字节一致**；`tests/evaluation` 243 passed。

修复原则（任务书「禁止范围」）：

1. 每条改动都能追到 Step 05 的失败案例与原始轮记录；
2. 修复的是**规则边界**（"无用户证据支持的赋值/越界操作必须不产生"），不为单个样本
   硬编码答案；
3. 不新增架构层、不顺带重构、不改冻结口径。

---

## 0. 追溯总表

| 项 | 缺陷 | 根因确认（一句话） | 修改模块 | 回归测试 | 修复前 |
|---|---|---|---|---|---|
| P1 | 越权 CLEAR（L1 阻断） | s08-pin-unpin-001 的 t3/t5 是 **image_feedback** 轮：Feedback Interpreter 把"把猫去掉"扩成 `CLEAR subject.description` + `CLEAR subject.count` + `CLEAR subject.pose_action`，后两条路径用户从未提及；Validator 的路径级证据检查只覆盖 Pending Question 证据（`_has_path_scoped_evidence`），普通消息证据无法证明路径范围，故被放行 | `feedback`（prompt 边界）+ `intent_engine`（同一边界） | `tests/feedback/test_feedback_boundaries.py::test_feedback_prompt_states_the_clear_is_per_path_boundary`、`::test_the_boundary_prompt_is_the_one_actually_sent_to_the_provider`、`tests/intent_engine/test_interpreter_boundaries.py::test_system_prompt_states_the_clear_is_per_path_boundary` | 红 |
| P2 | 冲突词表英文单语 | 词表全英文 + `_words` 把整段中文压成单 token，双重原因导致 12 条预期冲突 0 命中；三条 hard 规则在 t1 未检出即 ready 并出图 | `policy` | `tests/policy/test_policy_conflict_cjk.py`（25 条） | 红（12 条正向全红） |
| P3 | assess 从未携带 ExecutionRevision | `assess` 四处调用点全用默认 `execution_context=None`，`_framing_aspect_mismatch_conflict` 在 `decision_policy.py:291` 直接 `return False`（死规则）；而 1024x1024 的 ExecutionRevision 由产品自建并已落库 | `intent_engine`（models/engine）+ `workflow`（service/review） | `tests/intent_engine/test_interpreter_boundaries.py::test_engine_threads_the_execution_context_into_assess`、`tests/workflow/test_workflow_execution_context.py`（3 条） | 红 |
| P4 | 无依据具体化 | Interpreter 把单数名词短语推成 `subject.count=1`（19 个案例运行），并把处所描述（趴在壁炉边/沙发上）吞进 `environment.location`；确定性层没有"值必须有用户依据"的规则可拦（Value 与 evidence 无绑定） | `intent_engine`（prompt 边界） | `tests/intent_engine/test_interpreter_boundaries.py`（COUNTING/LOCATIVE 4 条） | 红 |
| P5 | PIN 范围过宽 | s07-single-field-002 t2 是 image_feedback 轮："其他都不变"被展开成 5~6 条 PIN（含用户未点名的 `subject.pose_action` 等），未声明的 facet 也被并入 | `feedback`（prompt 边界） | `tests/feedback/test_feedback_boundaries.py`（PRESERVE 4 条） | 红（prompt 4 条） |

版本号变化：`INTERPRETER_PROMPT_VERSION` `interpreter.v1 → interpreter.v2`；
`FEEDBACK_PROMPT_VERSION` `feedback.v1 → feedback.v2`。
`POLICY_VERSION` 保持 `policy.v1`（词表是规则表的**语言覆盖**，不是新规则版本；
若 Step 07 要把"中文命中"写成新口径，应另立版本）。

---

## 1. P1 —— 越权 CLEAR（L1 阻断项，最高优先）

### 1.1 从原始记录复现

`outputs/evaluation_runs/erun_d35a33ffc18d6c1f/cases/s08-pin-unpin-001.jsonl`：

| 轮 | rep | kind | 用户原文 | `applied_deltas` | 结果 |
|---|---|---|---|---|---|
| `eturn_24807db8aadfa204` | 1 | `image_feedback` | 现在把猫去掉。 | `CLEAR subject.description`、`CLEAR subject.count`、`CLEAR subject.pose_action` | 值/Resolution 被移除 → `forbidden_change_paths` 4 条违例 |
| `eturn_12a7525d937d679a` | 2 | `image_feedback` | 把猫从画面里去掉。 | `CLEAR subject.pose_action`、`CLEAR subject.count` | 同上（`subject.description` 的 CLEAR 被 PIN 规则拦下） |

标注（`annotations/core_v0_3.jsonl#s08-pin-unpin-001`）：t5 只允许
`CLEAR subject.description`；`forbidden_change_paths` 含 `subject.count`、
`subject.pose_action`。指标记录 `emet_8aebc69dc10e4d11`（rep1）/
`emet_8859330254893a75`（rep2）→ `unauthorized_fields_unchanged` 42/44、8 违例、
`gate_a_blocked=true`。

### 1.2 提出者与放行者（证据链）

- **提出者是 FeedbackEngine，不是 Interpreter**：两轮 `turn_kind=image_feedback`，
  经 `review.submit_feedback` → `FeedbackEngine.analyze` 产生候选 Delta。
  （Step 05 报告把归因写成 `intent_engine/prompts.py#52-59`；原始轮记录的
  `turn_kind` 证据更强，故修复同时落在 feedback 与 interpreter 两处 prompt 边界。）
- **放行者是 Validator 的冻结设计**：`validation/validator.py` 的
  `_has_path_scoped_evidence` 只认 Pending Question 绑定的证据；`EvidenceRef`
  没有路径字段，普通消息证据无法证明"这条 CLEAR 只覆盖用户点名的路径"，因此
  `CLEAR subject.count` / `CLEAR subject.pose_action` 通过全部 7 项检查。
- **不能靠 Validator 修**：要拦它必须让 Validator 解析自然语言片段或引入
  "路径 ⊆ 用户点名路径"的新证据字段，两者都超出"最小修改"，且会与冻结的
  `EvidenceRef` 形状冲突（Step 05 §2.2 明确判定 validation/policy/reducer 行为
  "冻结且正确"）。

### 1.3 修改

| 文件 | 改动 |
|---|---|
| `visual_intent_agent/feedback/engine.py` | `FEEDBACK_SYSTEM_PROMPT_V1` 新增 rule 9 `CLEAR IS PER-PATH` + rule 10（最小 delta 集）；`FEEDBACK_PROMPT_VERSION` → `feedback.v2` |
| `visual_intent_agent/intent_engine/prompts.py` | 同一边界写入 rule 14 `CLEAR IS PER-PATH`（防止同类语义从用户消息侧进入）；版本 → `interpreter.v2` |

规则文本（要点）：删除请求只 CLEAR 用户点名的路径；删主体只清
`subject.description`；**不得**因为"同一 facet"就顺带清 `subject.count` /
`subject.pose_action`。

### 1.4 红 → 绿证据

```text
# 修复前（feedback）
$ uv run pytest tests/feedback/test_feedback_boundaries.py -q
4 failed, 2 passed
FAILED ...::test_feedback_prompt_version_is_bumped_for_the_boundary_revision
FAILED ...::test_feedback_prompt_states_the_clear_is_per_path_boundary
FAILED ...::test_feedback_prompt_states_the_preserve_scope_boundary
FAILED ...::test_the_boundary_prompt_is_the_one_actually_sent_to_the_provider
  AssertionError: assert 'CLEAR IS PER-PATH' in 'You are the Feedback Interpreter ...'

# 修复前（intent_engine）
$ uv run pytest tests/intent_engine/test_interpreter_boundaries.py -q
7 failed, 4 passed     # 含 CLEAR 边界与 COUNTING/LOCATIVE 边界
  AssertionError: assert 'CLEAR IS PER-PATH' in ...
```

修复后：`tests/feedback`、`tests/intent_engine` 全绿（见 §7 全量数字）。

### 1.5 预期复评变化与残留风险

- 预期：s08-pin-unpin-001 t5/t3 的 `applied_deltas` 只含被点名路径 →
  L1 `unauthorized_fields_unchanged` 44/44、`gate_a_blocked=false`；
  L2 `out_of_scope` 至少减少 4（`CLEAR subject.count`×2、`CLEAR subject.pose_action`×2）。
- 残留风险：**约束在 prompt，不在确定性层**。若 LLM 仍产生越权 CLEAR，Validator
  依旧会接受（这是 Step 05 已确认的冻结语义缺口，本步无权重写 Validator 证据模型）。
  Step 07 复跑必须先核对 L1 是否 44/44；若仍有个别越权，需要的是"给 EvidenceRef
  增加路径范围字段"的新版本验证合同，而不是继续加 prompt 文字。
- 反向风险：过严的 prompt 可能让系统在"用户明确要求删除多个相关字段"时漏清；
  现有标注里没有这种样本，Step 07 需在复跑中人工抽查 `missing_decision_recall` 与
  t3/t4 的状态机行为是否异常。

---

## 2. P2 —— 冲突词表英文单语

### 2.1 从原始记录复现

系统实际存储值（`cases/s04-conflict-*.jsonl`）：

| 案例 | 轮 | 系统值 | 冻结词表 | 旧结果 |
|---|---|---|---|---|
| s04-conflict-001 | t1 | `environment.mode="摄影棚环境"`、`environment.location="海边沙滩"` | `_ENCLOSED_ENVIRONMENT_MODES`（studio/indoor/…）、`_OPEN_AIR_LOCATION_TERMS`（outdoor/beach/…） | 双向 0 命中 → `conflict_rule_ids=[]`、`ready=True`、出图；`emet_6646ffe53586c7bb`/`emet_59a42cd91a8a8d3e` |
| s04-conflict-002 | t1 | `environment.mode="室内环境"`、`lighting.character="自然光照明"` | `_NATURAL_LIGHT_TERMS`（natural light/…） | 0 命中 → 同上；`emet_5faa370f19c5260b`/`emet_df2a1a9aa0e08d66` |
| s04-conflict-003 | t1 | `style.primary="写实摄影"`、`style.description="画面整体要水彩画的质感"` | `_PHOTO_STYLE_TERMS` / `_NON_PHOTO_STYLE_TERMS` | 0 命中 → 同上；`emet_a4db88a9d5f87ee8`/`emet_34f2465667eae094` |
| s02-missing-core-002 t3 / s09-multiturn-001 t3/t4 | — | `composition.framing="wide_shot"` / `"全景"` | `_WIDE_FRAMING_TERMS` | 前者命中英文但 execution 规则缺上下文（P3）；后者中文 0 命中 |

### 2.2 根因是**两重**的（都必须在最小范围内修）

1. **词表只有英文** —— 补中文同义词；
2. **`_words()` 把整段中文压成单个 token** —— 中文没有词间空格，
   `_contains_phrase` 的"连续完整单词序列"永远匹配不上。所以只补词表不够，
   必须同时给 **CJK 词表项**启用子串匹配：
   `_contains_phrase` 在词表项自身含 CJK 时用 `phrase in word`，英文短语仍走
   原来的词序列比较（`photo` 不会命中 `photograph`）。这是语言覆盖修正，
   **不改变任何"哪两个值冲突"的规则语义**。

### 2.3 修改

`visual_intent_agent/policy/decision_policy.py`：

- 五组词表各补中文同义词（环境封闭/户外地点/自然光/摄影媒介/非摄影媒介/宽幅构图）；
- 新增 `_has_cjk()`；`_contains_phrase()` 对 CJK 词表项启用子串匹配，
  英文路径逐字保持原实现；
- 注释写明 `[Rev.2]` 与"只补语言覆盖、不改规则语义"。

`POLICY_VERSION` 保持不变：规则表、rule id、谓词结构、冲突判定语义均未变。

### 2.4 红 → 绿证据

```text
# 修复前
$ uv run pytest tests/policy/test_policy_conflict_cjk.py -q
12 failed, 12 passed
FAILED ...[摄影棚环境-海边沙滩]        AssertionError: assert 'policy.hard_conflict.environment_mode_location' in []
FAILED ...[室内环境-自然光照明]        AssertionError: assert 'policy.hard_conflict.lighting_environment_source' in []
FAILED ...[写实摄影-画面整体要水彩画的质感] AssertionError: assert 'policy.hard_conflict.style_medium_mismatch' in []
FAILED ...[远景构图] / ...[全景]        AssertionError: assert 'policy.execution_conflict.framing_aspect_mismatch' in []
# 负向用例与英文对照用例修复前即通过（证明只扩语言、不扩语义）
```

`tests/policy/test_policy_conflicts.py`（v0.2 冻结 golden 行为）全程未改、保持绿。

### 2.5 影响面核验（防止误报新冲突）

按修复后的谓词，对 `erun_d35a33ffc18d6c1f` 全部 System B 轮次的
`intent_values_after` 重放：新增命中的 hard conflict **只出现在**
s04-conflict-001/002/003（正是标注 `expected_conflicts` 的三案例），
无任何其它案例被新判为 hard conflict；新增 execution conflict 只出现在
s02-missing-core-002 t3 与 s09-multiturn-001 t3/t4（标注要求的位置）。

### 2.6 预期复评变化与残留风险

- 预期：Conflict Detection 由 **0/12 → 12/12**，`severe_failures` 由 6 → 0；
  s04 三案例 t1 不再 ready，t2 的脚本化 `clarification_answer` 不再被状态机拒绝
  （预期减少 6 个 `workflow.invalid_state` 失败轮）；
  6 次 `unauthorized_addition`（摄影棚/自然光/写实）应随之消失。
- 残留风险：**中英混排**（如 `写实photo摄影`）仍按词序列/or 子串的既有口径判定，
  能命中；但"中文同义词未收录"的新说法仍可能漏检——这属于词表维护，不是本轮
  新增缺陷。Step 07 复查 `conflict_detection` 的 `missed_conflicts` 明细即可。

---

## 3. P3 —— assess 从未携带 ExecutionRevision

### 3.1 从原始记录复现

- s02-missing-core-002 t3：`composition.framing="wide_shot"`，
  `conflict_rule_ids=[]`、`ready=True`（标注要求
  `execution_conflict.framing_aspect_mismatch`；`emet_58f72f7ec1585b26`）。
- s09-multiturn-001 t3/t4：`composition.framing="全景"`，同样 0 命中
  （`emet_99619029b4aefa74`/`emet_2d9a6f824666d975`）。
- 代码：`engine.py:62,100`、`review.py:322,563` 均 `assess(...)` 不传第二参；
  `_framing_aspect_mismatch_conflict` 在 `execution_context is None` 时
  `return False`。执行上下文由 `WorkflowService.create_session`
  （`service.py:150`，`DEFAULT_OUTPUT_SIZE="1024x1024"`）落库，从未被读取。

### 3.2 修改（公开合同向后兼容）

| 文件 | 改动 |
|---|---|
| `intent_engine/models.py` | `IntentResolveRequest` **新增可选字段** `execution_context: ExecutionRevision \| None = None`（新增而非改签名；不破坏既有构造与落盘 payload） |
| `intent_engine/engine.py` | `resolve` 正常路径与 `_failure_resolution` 都传 `request.execution_context` |
| `workflow/service.py` | 新增 `_current_execution_revision(repo, snapshot)`；`submit_message` 组装请求时接线 |
| `workflow/review.py` | 新增同口径 `_current_execution_revision`；`_revise` 与 `_failure_resolution` 接线 |

指针缺失时返回 `None`（不伪造输出尺寸），保持 v0.2 "无上下文即不判执行冲突"语义。

### 3.3 红 → 绿证据

```text
# 修复前
$ uv run pytest tests/workflow/test_workflow_execution_context.py -q
3 failed, 2 passed
FAILED ...[全景] / ...[wide_shot] / ...[远景构图]
  AssertionError: assert 'policy.execution_conflict.framing_aspect_mismatch' in []
$ uv run pytest tests/intent_engine/test_interpreter_boundaries.py::test_engine_threads_the_execution_context_into_assess -q
1 failed
  AssertionError: ... in []
```

### 3.4 预期复评变化与残留风险

- 预期：s02-missing-core-002 t3、s09-multiturn-001 t3/t4 命中 execution conflict
  → Conflict Detection 的 6 条 execution 分支可检出（配合 P2 的中文词表）。
- 残留风险：execution conflict 只报告、不阻塞（冻结语义），因此不会改变
  `ready_for_confirmation`；且单问题优先级下 core 缺失优先，问题目标可能仍不是
  `composition.framing`（回归测试已按冻结优先级写成"冲突可见 + 不 ready"）。
  Step 07 不应把"问题目标"当成 execution conflict 的检出证据。

---

## 4. P4 —— 无依据具体化

### 4.1 从原始记录复现

| 形态 | 案例/轮 | 系统产生的 delta | 标注要求 |
|---|---|---|---|
| 数量臆测 | s01-complete-001/002 t1、s02/s03/s04/s05/s06/s07/s08/s09/s10 共 19 个案例运行 | `SET subject.count=1, resolution=user_specified`（"一只/一位"） | `paths_must_remain_unset` 含 `subject.count` |
| 处所吞并 | s02-missing-core-001 t1（`emet_084e77929579b45c`） | `SET environment.location="壁炉边"`（原文"趴在壁炉边"） | `environment.location` 保持 unset 且 blocking |
| 处所吞并 | s09-multiturn-002 rep2 t1（`emet_6bb1e99d2e0cb4ab`） | `SET environment.location="沙发"` | 同上 |
| 其它 | s01-complete-002 rep2 t1、s03-missing-perceptual-001 rep2 t1、s09-multiturn-002 rep2 t1 | `SET composition.framing`（原文没说构图）、`SET style.description="水彩"`（原文说"水彩插画风格"） | 各自 `paths_must_remain_unset` |

影响：`out_of_scope 56`（其中 `subject.count` 30、`environment.location` 3、
`composition.framing` 2、`style.description` 1）、
`must_remain_unset_violations 58`（`subject.count` 50、`environment.location` 5、
`style.description` 3）；s02/s09 因此跳过一个 blocking 缺失而提前 ready，
导致 8 次流程错位。

### 4.2 为什么落在 prompt 而不是 Validator

- Validator 能看到的证据只有 `EvidenceContext`（`available_message_ids` +
  Pending Question 绑定）与 `IntentDelta.value`；**没有**"值必须能在用户原文里找到"
  这条冻结规则，`EvidenceRef.fragment` 也不参与校验。
- 要确定性拦截，必须新增"文本包含片段 / 值有依据"的规则或给 Validator 注入消息
  文本——两者都会改变冻结的验证合同，且需要自然语言判定（超出确定性层职责）。
- 因此按 Step 05 的归因（`suggested_fix_target=intent_engine`）落 prompt 边界；
  这也符合任务书"禁止为单个样本硬编码答案"（例如不能写"若值是 `壁炉边` 则拒绝"）。

### 4.3 修改

`visual_intent_agent/intent_engine/prompts.py`：

- rule 12 `COUNTING`：`subject.count` 只接受用户明说的数量词（两只/two/三只/一群）；
  "一只猫/一位老人/a cat"不得产生任何 count delta；未说明数量时必须留空，
  不得默认 1；
- rule 13 `LOCATIVE-FREE TEXT`：处所/方位描述属于 `subject.description` /
  `subject.pose_action`；只有用户显式给出地点（"地点是客厅"/"in a park"）才写
  `environment.location`；
- rule 15（最小 delta 集）。

### 4.4 红 → 绿证据

```text
# 修复前
$ uv run pytest tests/intent_engine/test_interpreter_boundaries.py -q
7 failed, 4 passed
FAILED ...::test_prompt_version_is_bumped_for_the_boundary_revision
FAILED ...::test_system_prompt_states_the_no_invented_count_boundary
FAILED ...::test_system_prompt_states_the_locative_boundary
FAILED ...::test_the_boundary_prompt_is_the_one_actually_sent_to_the_provider
  AssertionError: assert 'COUNTING' in 'You are the Intent Interpreter ...'
```

### 4.5 预期复评变化与残留风险

- 预期：`subject.count` 的 30 个 out_of_scope、50 个 unset 违例消失；
  `environment.location` 不再被吞并（s02-missing-core-001 与 s09-multiturn-002
  恢复 blocking 缺失 → Missing Decision Recall 提升约 8 个 covered blocking 点；
  s02 t2 不再提前 ready、t3 的 `clarification_answer` 不再被拒）；
  `composition.framing` / `style.description` 的 3 个越界消失。
  唯一允许 `subject.count` 的 s07-single-field-002 t2（"改成两只"）不受影响
  （prompt 明确保留显式数量）。
- 残留风险：
  1. 约束在 prompt；若 LLM 仍臆测，指标会如实记录（评测口径不动）。
  2. `environment.location` 的判定是语义边界，"地点在窗边/沙发上"这类介于
     处所与地点之间的说法可能被过度保守地留空 → 可能是澄清成本上升。
     Step 07 需对比 `must_clarify 满足数` 与 `提问总数`，确认不是用更多提问换分。
  3. 同理，`subject.count` 不再由"一只"推得后，某些案例的 Prompt 可能缺少数量描述；
     L4 的 `intent_alignment` 需要人工抽查（数量为 1 是图像常识默认，不是会话事实）。

---

## 5. P5 —— PIN 范围过宽

### 5.1 从原始记录复现

s07-single-field-002 t2（`emet_78618550965e1762`/`emet_8d04c6d11fdeb3e0`）：

- 用户："改成两只边牧，一黑一白，其他都不变。"（`image_feedback` 轮）
- 系统：`SET subject.count=2`、`SET subject.description=一黑一白边牧` +
  `PIN composition.framing`、`environment.location`、`environment.mode`、
  `lighting.character`、`style.primary`、`subject.pose_action`（rep1 6 条 / rep2 5 条）
- 标注 t2：`expected_deltas` 只有 `SET subject.count`，`acceptable_extra_deltas`
  只有 `SET subject.description`；**没有任何 PIN 是 expected/acceptable** →
  6/5 条 PIN 全计入 `out_of_scope`（13 个越界中的主力）。
- 同形态：s07-single-field-001 t2 把"猫和背景都别动"扩到 `lighting.character` +
  `style.primary`（用户未点名）；s09-multiturn-001 t3"狗保持不变"的 PIN 是正确的
  对照（只 subject 三路径）。

### 5.2 修改

`visual_intent_agent/feedback/engine.py` `FEEDBACK_SYSTEM_PROMPT_V1` rule 8
`PRESERVE SCOPE`：笼统的"其他都不变"只覆盖用户点名的路径/具名 facet，不得变成
"所有有值路径的 PIN"，也不得把用户没提的 facet 拉进来；
`FEEDBACK_PROMPT_VERSION` → `feedback.v2`。

确定性展开实现（`match_preserve_pattern` / `expand_preserve_paths` /
`preserve_pin_deltas`）**未改**：它一直只按 LLM 声明的模式展开，且只为有值路径发 PIN。

### 5.3 红 → 绿证据

```text
# 修复前
$ uv run pytest tests/feedback/test_feedback_boundaries.py -q
4 failed, 2 passed
FAILED ...::test_feedback_prompt_states_the_preserve_scope_boundary
  AssertionError: assert 'PRESERVE SCOPE' in 'You are the Feedback Interpreter ...'
FAILED ...::test_the_boundary_prompt_is_the_one_actually_sent_to_the_provider
# 两条按"标注 acceptable preserve 形态"设计的用例修复前即通过
#   （证明收紧只针对未点名范围，不误伤具名 facet 的合法 PIN）
```

### 5.4 预期复评变化与残留风险

- 预期：s07-single-field-002 t2 的 out_of_scope 由 6/5 降到 0~2（若只保留
  `SET subject.count` + acceptable 的 `SET subject.description`；
  `composition.framing`/`style.primary` 若仍被 PIN 也属越界，交由复评核验）；
  s07-single-field-001 t2 的 2 条越界消失；L2 `delta_accuracy.details.precision`
  从 0.850 上升。
- 残留风险：**本项最弱**——收紧完全依赖 LLM 对"哪些 facet 被点名"的判断，
  没有确定性断言可以证明模型不会再扩。若 Step 07 复跑仍见 5~6 条 PIN，
  正确的下一步是给 `preserve_paths` 增加"必须在反馈原文中有对应片段"的
  确定性校验（新的证据合同），而不是继续加强措辞。
- 反向风险：过度收紧可能漏 PIN → `pinned_not_overwritten` 是 L1 不变量，
  漏 PIN 会让后续 SET 覆盖本应保护的路径。s08 案例的 PIN 来自"锁定它别再变了"
  这类明确指令，不受"其他都不变"规则影响；Step 07 需重点核对
  `pinned_not_overwritten` 是否仍 44/44。

---

## 6. 既有测试的最小修订（逐条）

任务书允许对"恰好断言了被证据判定为缺陷的行为"的既有测试做最小修订。本步共修订
**3 处断言（2 个文件 3 个测试函数）、1 个测试 helper**，均为版本号钉住，
不放松任何不变量：

| # | 文件::测试 | 原断言 | 失败证据 | 修订后断言 | 为何不构成放松 |
|---|---|---|---|---|---|
| 1 | `tests/intent_engine/test_interpreter_prompts.py::test_prompt_version_is_recorded` | `INTERPRETER_PROMPT_VERSION == "interpreter.v1"` | P1/P4 要求修改系统提示词（`FC-P1`、`FC-P4`），模块惯例是"改 prompt 必 bump 版本" | `== "interpreter.v2"` | 只钉版本字面量；下方 `test_system_prompt_states_every_boundary_the_task_requires` 的全部边界断言未动，且新版本新增约束而非删除 |
| 2 | `tests/feedback/test_feedback_public_api.py::test_feedback_prompt_version_is_recorded` | `FEEDBACK_PROMPT_VERSION == "feedback.v1"` | P1/P5 要求修改反馈系统提示词 | `== "feedback.v2"` | 同上；`__all__` 断言未动 |
| 3 | `tests/feedback/test_feedback_engine.py::test_user_prompt_contains_the_generation_binding_and_the_feedback_text` | `FEEDBACK_PROMPT_VERSION == "feedback.v1"` | 同 2 | `== "feedback.v2"` | 该测试主体（user prompt 注入 generation/artifacts/原文）全部保留 |
| 4 | `tests/intent_engine/ie_helpers.py` | 无 `execution()` 工厂；`make_request` 不支持执行上下文 | P3 需要引擎级回归用例构造 `ExecutionRevision` | 新增 `execution(output_size="1024x1024")` 工厂；测试用 `model_copy` 填 `execution_context` | 纯新增 helper，不改任何既有 helper 行为；未见 `make_request` 签名改动 |

**未修订**：`tests/policy/**`（含 golden、`test_policy_conflicts.py` 全绿未改）、`tests/validation/**`、
`tests/workflow/**`、`tests/feedback/**` 的其它测试、`tests/e2e/**`、
`tests/evaluation/**`（243 条全绿，零改动）。`tests/fixtures/policy_cases/*.json`
golden fixture 未动。

---

## 7. 测试与结果

```text
# 修复前基线（Step 05 结束）
$ uv run pytest -q
2086 passed, 3 deselected in 6.48s

# 修复后（Step 06 结束）
$ uv run pytest -q
2132 passed, 3 deselected in 6.48s          # +46（新增回归测试）

# 受影响模块单独跑
$ uv run pytest tests/intent_engine tests/validation tests/policy tests/workflow \
      tests/feedback tests/realization tests/e2e tests/generation -q
1314 passed

# 评测层未被触碰的证据
$ uv run pytest tests/evaluation -q
243 passed

# 冻结物哈希
evaluation/protocol.md                    70cc0b3c4bde970a…  OK
evaluation/fixtures/core_v0_3.jsonl       0519534702225949…  OK
evaluation/annotations/core_v0_3.jsonl    d1712aab45c42251…  OK
evaluation/configs/gate_a_v0_3.json       3b6159f8bc620f6c…  OK
```

新增回归测试清单（46 条，全部"修复前红 → 修复后绿"）：

| 文件 | 条数 | 覆盖 |
|---|---|---|
| `tests/policy/test_policy_conflict_cjk.py` | 25 | P2：三组 hard 冲突的中文值、`wide_shot`/中文宽幅、负向语义边界、英文行为不变、CJK 子串匹配口径 |
| `tests/intent_engine/test_interpreter_boundaries.py` | 9 | P1/P4：prompt 版本、COUNTING/LOCATIVE/CLEAR 边界、约束真实进入 Provider、输出合同不变；P3：引擎把 ExecutionRevision 交给 assess（含未提供时不臆造） |
| `tests/feedback/test_feedback_boundaries.py` | 7 | P1/P5：prompt 版本、CLEAR/PRESERVE 边界、具名 facet 的合法 preserve 不误伤、未点名 facet 不外溢 |
| `tests/workflow/test_workflow_execution_context.py` | 5 | P3：真实工作流链路（create_session → submit_message → assess）可达 execution conflict、会话 ExecutionRevision 尺寸、非宽幅不误报 |

修复前失败形态摘要（红证据存档）：

```text
tests/policy/test_policy_conflict_cjk.py            12 failed, 12 passed
  assert 'policy.hard_conflict.environment_mode_location' in []
  assert 'policy.hard_conflict.lighting_environment_source' in []
  assert 'policy.hard_conflict.style_medium_mismatch' in []
  assert 'policy.execution_conflict.framing_aspect_mismatch' in []
tests/intent_engine/test_interpreter_boundaries.py   7 failed, 4 passed
  assert 'COUNTING' / 'LOCATIVE-FREE TEXT' / 'CLEAR IS PER-PATH' / 'interpreter.v2' in ...
  assert 'policy.execution_conflict.framing_aspect_mismatch' in []
tests/feedback/test_feedback_boundaries.py           4 failed, 2 passed
  assert 'CLEAR IS PER-PATH' / 'PRESERVE SCOPE' / 'feedback.v2' in ...
tests/workflow/test_workflow_execution_context.py    3 failed, 2 passed
  assert 'policy.execution_conflict.framing_aspect_mismatch' in []
```

---

## 8. 代码变更清单（文件级）

| 文件 | 类型 | 变更摘要 |
|---|---|---|
| `visual_intent_agent/intent_engine/prompts.py` | prompt 修订 | `interpreter.v1 → v2`；新增 rule 12/13/14/15（COUNTING、LOCATIVE-FREE TEXT、CLEAR IS PER-PATH、最小 delta 集）；输出 Schema 未变 |
| `visual_intent_agent/intent_engine/models.py` | 向后兼容新增字段 | `IntentResolveRequest.execution_context`（可选，默认 None） |
| `visual_intent_agent/intent_engine/engine.py` | 接线 | 正常路径与失败路径都把 `execution_context` 传给 `assess` |
| `visual_intent_agent/policy/decision_policy.py` | 语言覆盖 | 五组词表补中文同义词；`_has_cjk` + CJK 子串匹配；规则语义/rule id 未变；`POLICY_VERSION` 未变 |
| `visual_intent_agent/workflow/service.py` | 接线 | 新增 `_current_execution_revision`；`submit_message` 组装请求时携带 |
| `visual_intent_agent/workflow/review.py` | 接线 | 新增同口径 `_current_execution_revision`；`_revise` / `_failure_resolution` 携带 |
| `visual_intent_agent/feedback/engine.py` | prompt 修订 | `feedback.v1 → v2`；新增 rule 9（CLEAR IS PER-PATH）、rule 10（最小 delta 集），改写 rule 8（PRESERVE SCOPE）；输出 Schema 未变 |
| `tests/policy/test_policy_conflict_cjk.py` | 新增测试 | P2 回归 |
| `tests/intent_engine/test_interpreter_boundaries.py` | 新增测试 | P1/P3/P4 回归 |
| `tests/feedback/test_feedback_boundaries.py` | 新增测试 | P1/P5 回归 |
| `tests/workflow/test_workflow_execution_context.py` | 新增测试 | P3 回归 |
| `tests/intent_engine/ie_helpers.py` | 测试 helper | 新增 `execution()` 工厂 |
| `tests/intent_engine/test_interpreter_prompts.py`、`tests/feedback/test_feedback_public_api.py`、`tests/feedback/test_feedback_engine.py` | 既有测试最小修订 | 仅版本字面量（见 §6） |

Artifact 兼容性：未改任何落盘 schema / Repository 表 / payload 格式。
`IntentResolveRequest.execution_context` 只在内存中传递（不落盘），且为可选字段，
旧调用方不传即保持原行为。

---

## 9. 已知限制 / 未做（必须由 Step 07 复核）

1. **prompt 类修复没有确定性断言**：P1/P4/P5 的最终行为取决于真实模型输出。
   回归测试能证明"约束已写入并送达 Provider"，不能证明"模型一定遵守"。
   这是本步与 P2/P3 的本质差别，已在各项"残留风险"中逐条写明。
2. **未运行真实 Provider**（任务书硬约束）：所有结论来自离线分析 + 离线回归测试，
   预期指标变化是**基于规则与词表的确定性推断**，实际数值由编排方诊断性复跑给出。
3. **未修 Q1/Q2/Q4**（用户裁定冻结口径）：Preservation 的无 Prompt 对仍按
   `kept=0` 计、Runner 仍按脚本 kind 路由、`guard_bypassed` 字段名未改；
   Step 07 复用同一口径复评，这些口径失真会原样重现，不得据此判产品缺陷。
4. **P6（Provider 超时无降级）未修**：`failure_catalog` 标 `worth_fixing=false`，
   不属于 P1~P5 范围；Step 07 仍会出现 `provider.timeout` 造成的整案失败。
5. **P5 的修复强度最弱**：没有确定性证据能证明模型不再扩范围；若复跑仍有
   5~6 条 PIN，需要新的证据合同（`preserve_paths` 必须绑定反馈原文片段）。

---
---

# patch 001（MVP v0.3 Step 06 · 第二轮最小修复 + 取证）

- 触发：编排方在首轮修复后的**诊断性真实复跑** `outputs/evaluation_runs/erun_e7413108dc8a6104/`
  （4 案例 × 1 重复：s01-complete-001 / s04-conflict-001 / s07-single-field-002 /
  s08-pin-unpin-001；非正式口径，1 repetition）。
- 本 patch 的范围（用户裁定）：**A** 收窄 Interpreter v2 的过度泛化措辞（版本 → v3）；
  **B** 对首轮 `erun_d35a33ffc18d6c1f` 的 s08 L1 违例做只读取证与 a/b/c 分解；
  **C** 顺带核查 `questions_bad` 3 条与 P2 确定性重放。
- 本 patch **不修** P6（Provider 超时/重试为冻结配置），**不改** FeedbackEngine 语义，
  **不改** `evaluation/**` 既有文件（只追加本文件）、数据集/标注/协议/配置/Baseline。

## P-0 首轮诊断结论的更正（最重要）

编排方首轮假设"**s04-conflict-001 t1 的零 delta 是 Interpreter v2 过度保守**"。
本 patch 用运行记录 + 定向真实探针**否证**了该假设：零 delta 的真实根因是
**Provider 超时级联（P6）**，不是提取退化。

### P-0.1 诊断复跑的硬证据（`erun_e7413108dc8a6104`）

| 轮 | turn latency | LLM 调用记录（`llm_calls`） | issue | 结果 |
|---|---|---|---|---|
| s04 t1 | 363 867 ms | `ellm_7b252efecede117b` interpreter **failed / provider.timeout / 181 944 ms**；`ellm_98563ee0da56a047` interpreter **failed / provider.timeout / 181 910 ms** | `provider.timeout` | 两次 resolve（`MAX_INTERPRETATION_ATTEMPTS=2`）都超时 → 0 delta → 只有 t2 之后 1 个 revision；core `subject.description` 缺失 → 提问 |
| s08 t2 | 181 953 ms | `ellm_981ba576cfcd9065` feedback_engine **failed / provider.timeout / 181 948 ms** | `provider.timeout` | PIN 未落 → t3 的 CLEAR 失去冻结保护 → t3 的 2 条 L1 违例 |

因果链复核：诊断复跑中 s04 t1 的 `363 867 ms ≈ 2 × 181.9 s`（一次 timeout 循环 =
adapter 的 `http_max_retries=2` 三次 60 s 读超时 + 退避），与"工作流最多重试一次"
的冻结实现完全吻合。`session.db` 只有 `irev_0e7ece1f…`（t2 的 SET
`environment.mode=outdoor`），t1 无 revision、无 issue 表（intent 侧 issue 不落盘，
正是 Step 05 记录的盲区）。

### P-0.2 定向真实探针（v2 复现）

| 探针 | 输入 | 结果 |
|---|---|---|
| `v2-s04t1` | s04 t1 原文 | 两次 `provider.complete` 均 `provider.timeout`（首次耗时 181.9 s），无 raw 输出 |
| `v2-s04t1-repeat` | s04 t1 原文 | **成功**：raw 7 条 delta（`subject.description=黑猫`、`pose=坐在窗台上`、`style=写实摄影`、`mode=摄影棚`、`location=海边沙滩`、`framing=中景`、`lighting=柔和光线`），`subject.count` 未出现；离线重放命中 `policy.hard_conflict.environment_mode_location`，`ready=False` |

结论：**在 v2 下，同一消息只要 Provider 正常返回，就能完整提取 7 条明示信息**；
首轮 v1 的 s04 t1（interpreter 53.9 s、`retry_count=0`）也正常提取过。超时在 v1
（`erun_d35a33ffc18d6c1f` 的 s09-multiturn-002 rep1 t1 = 364 392 ms，两次 interpreter
调用）与 v2（s04、s08）都出现过 —— 属 Provider 波动（P6），不得归因于 prompt。

## P-1 A 节修复：Interpreter v3 收窄（规则措辞）

版本：`INTERPRETER_PROMPT_VERSION` `interpreter.v2 → interpreter.v3`
（`visual_intent_agent/intent_engine/prompts.py`，模块 docstring 新增 `[Rev.3]`）。
输出 Schema、证据合同、CLEAR/PIN 语义**未变**。

| 规则 | v2 原文关键句 | v3 修订后关键句 | 修订理由 |
|---|---|---|---|
| 编号 | `…never default it to 1.13. LOCATIVE-FREE TEXT belongs…`（rule 12 与 13 之间**换行丢失**，两条规则被拼成 `1.13.`） | 恢复为独立的 `12. COUNTING` 与 `13. LOCATIVE-FREE TEXT vs EXPLICIT PLACES` 两条（正文不再含 `1.13.`） | 编号不可读会让模型把"数量约束"误读成对整段文本的总约束；属首轮补丁的排版缺陷 |
| 12 | `COUNTING (never infer a quantity)`；结尾只说"不得默认 1" | `COUNTING (this rule constrains exactly ONE path)` + `This rule constrains subject.count ONLY. It is not a reason to skip, merge or drop the delta of any other path.` | 数量约束只允许作用于 `subject.count`，不得成为少提取其它路径的理由（保留"一只猫/一位老人不得产生 count、不得默认 1"的保护语义） |
| 13 | `Set environment.location ONLY when the user names a place explicitly`（未说"明示标记必须提取"） | `But when the user names a place explicitly ("地点是客厅"/"地点是海边沙滩"/"in a park", "location: the beach"), you MUST emit SET environment.location with that place, even when the same message also contains a locative phrase or a second environment value. An explicit place marker always yields its own delta.` | 保护意图只是"**自由处所短语**里的地点词不得被吞并"（`趴在壁炉边`/`坐在窗边`/`趴在沙发上`）；用户以"地点是/位于/in/location:"明示给出地点时**必须**提取 |
| 15 | `Emit the MINIMAL delta set: one delta per path the user actually addressed.` | 追加 `MINIMAL never means fewer than what the user stated: every path the user explicitly addresses MUST get its own delta … No rule above (counting, locative or conflict) authorises dropping an explicitly stated path.` | "最小 ≠ 少于用户明示内容"；明示的 subject/style/mode/location/framing/lighting 必须逐条产出 |
| 10 | `keep both deltas and/or report the conflict` | 追加 `A conflict is NEVER a reason to emit fewer deltas: every value the user states still gets its own delta.` | 自相矛盾的输入（摄影棚 + 海边沙滩）不得让模型"干脆不提取" |

规则 14（`CLEAR IS PER-PATH`）与 rule 1~9、11 逐字未动；rule 12/13 仍保留中文反例
（`一只猫`、`一位老人`、`壁炉边`、`坐在窗边`、`趴在沙发上`）。

**未改反馈 prompt**：测量 v1→v2 的 `FEEDBACK_SYSTEM_PROMPT_V1` 由 3 512 → 4 485 字符
（+973，约 +28%），无证据表明它是超时主因（超时在 v1 的 feedback/interpreter 调用上
同样出现），按"最小修改"原则不动 `feedback/engine.py`。

## P-2 真实调用验证（预算内，零图片）

预算：**14 次 provider.complete 逻辑调用**（≤20；每次 timeout 逻辑调用内部由冻结的
adapter 重试策略产生 ≤3 次 HTTP 尝试）；**0 次图片生成**；探针脚本只放 `/tmp`
（`/tmp/via_probe.py`、`/tmp/via_replay_raw.py`），不进交付。

| # | 探针 | prompt | 输入摘要 | elapsed | 输出 delta（离线重放确认） | 达标 |
|---|---|---|---|---|---|---|
| 1 | `v2-s04t1` | v2 | s04 t1 | 181.9 s（+第二次同况） | 超时，无输出 | 复现 P6 |
| 2 | `v2-s01t1` | v2 | s01 t1 | — | 7 delta，count 未设 | 基线 |
| 3 | `v2-s04t1-repeat` | v2 | s04 t1 | 292.0 s | 7 delta，count 未设，命中 hard conflict | 证明非措辞问题 |
| 4 | `v3-s04t1` | v3 | s04 t1 | 214.7 s | **raw 7 delta**（黑猫/坐在窗台上/写实摄影风格/摄影棚环境/**海边沙滩**/中景/柔和光线）+ LLM 自报冲突；离线重放 → `count=None`、命中 `policy.hard_conflict.environment_mode_location`、`ready=False` | ✅ |
| 5 | `v3-s01t1` | v3 | s01 t1 | 58.2 s | **7 delta**，`count=None`，`ready=True` | ✅ |
| 6 | `v3-p4-sofa` | v3 | `画一只猫在沙发上` | 51.4 s | `subject.description=一只猫`、`subject.pose_action=在沙发上`；**无 `subject.count`、无 `environment.location`** | ✅（P4 反例） |
| 7 | `v3-locative-plus-place` | v3 | `一只黑猫坐在窗台上，地点是海边沙滩，中景。` | 47.9 s | `description=黑猫`、`pose=坐在窗台上`、**`environment.location=海边沙滩`**、`framing=中景`；`count=None` | ✅（rule 13 收窄） |

说明：
- 探针 1/3 的 `provider.complete` 是"一次逻辑调用"；探针脚本原设计会为解析再调用一次
  Interpreter（探针 4 的第二次调用超时），因此探针 4 的 `raw` 来自第一次成功调用，
  其 `deltas` 为空只是第二次调用超时；`/tmp/via_replay_raw.py` 用已落盘 raw 做**零调用**
  离线重放，确认 7 条全部生效且冲突命中。
- 探针 1 与 3 合起来说明：v2（以及 v1）对 s04 t1 的输出**正确且完整**，超时是 Provider
  波动；探针 4 说明 v3 在同一消息上仍完整提取（含显式地点）并触发冲突。
- 未再做更多真实调用；s04 t1 的"是否超时"无法在预算内稳定复现（探针 1、3、4 三次
  首次调用中 1 次超时、2 次成功），这一点原样交给 Step 07 作为风险。

## P-3 离线回归

新增 `tests/intent_engine/test_interpreter_v3_boundaries.py`（11 条）：
版本号 `interpreter.v3`；`1.13.` 粘连编号消失；rule 12 的 `subject.count ONLY` 作用域；
rule 13 的"明示地点 MUST 提取 + 明示 marker"；P4 处所保护（壁炉边/沙发/`never extract
it as one`）；rule 15 的 `MINIMAL never means fewer`；rule 10 的冲突不减少 delta；
收窄点真实进入 Provider 的 system message；用户 prompt 合同未变；
FakeLLM 端到端钉住"明示 7 路径全部落库 + `subject.count` 未设 +
`hard_conflict.environment_mode_location` 命中"。

既有测试最小修订 2 处（仅版本字面量，语义不变，符合任务书允许项）：
`tests/intent_engine/test_interpreter_prompts.py::test_prompt_version_is_recorded`、
`tests/intent_engine/test_interpreter_boundaries.py::test_prompt_version_is_bumped_for_the_boundary_revision`
（`interpreter.v2 → interpreter.v3`）。

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

frozen_manifest 四文件 sha256 复核：逐字节一致
  evaluation/protocol.md                   70cc0b3c4bde970a…  OK
  evaluation/fixtures/core_v0_3.jsonl      0519534702225949…  OK
  evaluation/annotations/core_v0_3.jsonl   d1712aab45c42251…  OK
  evaluation/configs/gate_a_v0_3.json      3b6159f8bc620f6c…  OK
```

## P-B s08-pin-unpin-001 L1 违例取证（只读）

数据源：首轮 `erun_d35a33ffc18d6c1f/{rep1,rep2}` 与诊断 `erun_e7413108dc8a6104/rep1`
的 `session.db`（`intent_revisions` / `feedback_results` / `messages`）与
`cases/s08-pin-unpin-001.jsonl`。

### P-B.1 首轮 rep1（`emet_8aebc69dc10e4d11`，4 条违例，t5）

| 轮 | 时刻 | 事实 | PIN 状态 |
|---|---|---|---|
| t2 | 20:08:00 | `fbk_589f0579…` decision=revise，`PIN subject.description` + `SET lighting.character=戏剧性光线`，LLM 42.1 s `status=ok` | **PIN 成功落库**（`irev_d9cc6a49…` `pinned_paths=["subject.description"]`） |
| t3 | 20:09:34 | `fbk_d0d635a4…` decision=**clarify**，issue `feedback.unparseable_output.invalid_delta`（模型给出的 `CLEAR subject.description` 违反冻结 IntentDelta 形状）；`applied=[]`，outcome=`no_state_change`（与标注一致） | 仍 pinned |
| t4 | 20:10:24 | `fbk_cc2212cd…` `UNPIN subject.description` 被接受、同批 `CLEAR subject.description` 被 **Validator 拒绝**（`validation.clear_requires_unpinned_path`） | UNPIN 后 `pinned_paths=[]`，值未变 |
| t5 | 20:13:58 | `fbk_8c20c3d8…` **CLEAR ×3**（description/count/pose_action）全部被接受 | 无 pin → 4 条 L1 违例：`subject.count` 1→None、`subject.pose_action` '端坐在钢琴上'→None（各计 value + resolution） |

### P-B.2 首轮 rep2（`emet_8859330254893a75`，4 条违例，t3）

| 轮 | 事实 | PIN 状态 |
|---|---|---|
| t2 | `fbk_edc5dbd4…` `PIN subject.description` 成功（LLM 51.7 s `ok`） | pinned |
| t3 | `fbk_03f32973…` **CLEAR ×3**；`CLEAR subject.description` 被 PIN 拒绝，`CLEAR subject.count` / `CLEAR subject.pose_action` **被接受** → 4 条 L1 违例；随后 core `subject.description` 仍缺失 + t3 触发 `subject.pose_action` 提问 → `WAITING_CLARIFICATION` | pinned（description 未变） |
| t4/t5 | `workflow.invalid_state`（在 `WAITING_CLARIFICATION` 收到 image_feedback） | 无变化 |

### P-B.3 诊断复跑 rep1（`emet_f3efc216bb0ee4b1`，2 条违例，t3）

| 轮 | 事实 | PIN 状态 |
|---|---|---|
| t2 | `fbk_f9cee845…` decision=**clarify**，issue **`provider.timeout`**（181 948 ms，三次 60 s 读超时） | **PIN 未落**（`pinned_paths=[]`） |
| t3 | `fbk_f9e84871…` **CLEAR subject.description 单条**（rule 9 生效；与标注 expected 完全一致）被接受 → 2 条 L1 违例（description value+resolution）+ `WAITING_CLARIFICATION` | 无 pin |
| t4/t5 | `workflow.invalid_state` | — |

### P-B.4 a/b/c 分解

| 归因 | 首轮违例 | 诊断违例 | 证据 |
|---|---|---|---|
| **(a) Feedback Interpreter 过度展开（P1 本体，rule 9 修的对象）** | **8/8（100%）**：rep1 t5 的 `CLEAR subject.count`/`CLEAR subject.pose_action`；rep2 t3 同两条 | 0/2 | 首轮 feedback payload `candidate_deltas`；rep2 `validation.clear_requires_unpinned_path` 只拦住 description |
| **(b) Provider 超时级联（P6）** | 0/8（s08 rep1/rep2 的 t2 PIN 都成功，无超时） | **2/2（100%）**：t2 timeout → PIN 丢失 → t3 合法 CLEAR 变违例 | `feedback_results.issues[0].code=provider.timeout`；turn `latency_ms=181 953`；`llm_calls.ellm_981ba576… status=failed` |
| **(c) 脚本流程错位（独立成因）** | 0/8 直接；rep2 的 t4/t5 `invalid_state` 是 (a) 造成的状态漂移**下游** | 0/2 直接；t4/t5 `invalid_state` 是 (b) 造成 | turn `flow_deviation_reason`；首轮 rep1 的 t3 clarify **未**造成错位（outcome 与标注一致） |

补充：首轮 rep1 t3 的 `feedback.unparseable_output.invalid_delta`（模型给 CLEAR 附带
非法字段）是**另一类模型输出畸形**（可由 FeedbackEngine 的冻结可恢复降级吸收），
不产生 L1 违例、也未改变首轮流程走向；本 patch 不为其新增规则。

**结论（Step 07 解读口径）**：首轮 L1 的 8 条违例 100% 是 P1 过度展开，rule 9 在
诊断复跑中确实把越权 CLEAR 收敛为**恰好 1 条**（且与标注一致）；但诊断复跑的 2 条
L1 违例 100% 是 P6 超时级联 —— **s08 的 L1 结果现在被 t2 的 Provider 波动"挟持"**。
Step 07 若看到 s08 L1 违例，必须先查该 rep 的 t2 `feedback_results.issues` 是否含
`provider.timeout`：有则记为实验限制（P6），无则才是 P1 残留。

## P-C 顺带核查

### P-C.1 3 条 `questions_bad`

| 案例 | turn | reason / target | 上游成因 | 提取恢复后是否消失 |
|---|---|---|---|---|
| s04-conflict-001 | t1 | `unexpected_path` / `subject.description` | t1 零 delta（P6 超时）→ core `subject.description` 成为唯一缺失 → 提问它 | 是（t1 正常提取后 `subject.description` 有值，问题目标应为冲突路径 `environment.mode`） |
| s04-conflict-001 | t2 | `question_on_ready_turn` / `subject.description` | 同上（t2 后仍未解决） | 是 |
| s08-pin-unpin-001 | t3 | `unexpected_path` / `subject.description` | t2 PIN 因 P6 超时丢失 → t3 的 CLEAR 合法应用 → core 缺失 | 是（PIN 落库后 t3 的 CLEAR 被拒，不产生缺失） |

比对标注 `must_clarify` / `acceptable_clarify_paths`：s04 t1 的
`must_clarify=[environment.mode]`、`acceptable_clarify_paths=[]`，
s08 t3 的 `must_clarify=[]`、`must_not_clarify_paths` 含 `subject.description` ——
三条坏问全部是**超时下游**（`missed_must_clarify` 也同源：s04 t1 漏
`environment.mode`、s08 t5 从未执行），**不是独立的提问逻辑缺陷**；
一旦提取恢复（s04）或 PIN 恢复（s08），它们应自行消失。Step 07 只需确认
`questions_on_ready_turns` 归零即可。

### P-C.2 P2 确定性修复的离线重放（复用首轮记录值）

方法：读取首轮 `cases/*.jsonl` 每轮 `system_b.intent_values_after` +
`resolutions_after` 重建 `VisualIntent`，调用当前冻结的 `assess()`，与标注
`expected_conflicts`（kind=hard）比对（脚本 `/tmp/via_p2_replay.py`，只读，零 LLM）。

| 结果 | 数值 |
|---|---|
| 重放案例数（有 hard 标注） | 22 个案例全部轮次 |
| 首轮记录 `conflict_rule_ids` | 全 0（含 s04 三案例 t1） |
| 重放后命中标注要求 | s04-conflict-001/002/003 的 t1 **全部命中**（各 rep ×3 案例 = 6/6） |
| **其它案例的误报** | **0**（s01/s02/s03/s05/s06/s07/s08/s09/s10 全部 0 命中） |
| 同一三案例的 t2/t3 额外命中 12 条 | 首轮 t2 因 `workflow.invalid_state` 未改状态，重放的是 t1 的同一组值 → 属"重放坏轨迹"的伪差，不是谓词过宽 |

结论：P2 的确定性修复（词表语言覆盖 + CJK 子串匹配）仍成立，
`hard_conflict.environment_mode_location` 等三规则**一旦 location/mode 入库即可检出**，
且没有把冲突语义扩散到其它案例。这同时说明诊断复跑 s04 的
`conflict_detection 0/1` 是"t1 零 delta → location 从未入库"的下游，P2 修复被 P6 掩盖。

## P-D 给 Step 07 的风险清单（本 patch 新增/强化）

1. **P6 超时级联对 L1 的威胁（最高优先）**：s08 t2 的 feedback 调用一旦超时，
   PIN 丢失会让 t3 的**合法** CLEAR 变成 L1 违例并引发后续 `invalid_state`。
   Step 07 必须先看 `feedback_results.issues` 是否含 `provider.timeout`，
   再决定是否把该违例计为 P1 残留；建议在报告中把 s08 的 L1 结果按
   "PIN 是否落库"分成两支呈现。超时/重试是冻结配置，本 patch 未动。
2. **s04 t1 的提取不再可疑，但冲突检出依赖该轮不超时**：v3 探针证明提取完整；
   若 Step 07 复跑 s04 t1 仍超时，`conflict_detection` 会再次 0/1 —— 记 P6，不记 P2。
3. **prompt 变长**：Interpreter 系统提示词 v1→v3 由 3 363 → 5 660 字符（+68%），
   v2/v3 下 s04 t1 出现多次 60 s 读超时，而 v1 首轮该轮正常。无法证明因果
   （v1 的 s09-multiturn-002 也超时），但 Step 07 应把"prompt 体量"列为超时的候选
   因素之一，必要时另立"精简 prompt + 保持边界"的版本。
4. **v3 只有 5 条真实样本的验证**：7 条明示路径、显式地点、单数 count、处所短语
   四类边界各 1~2 例通过；`must_not_clarify_paths`、多轮 PIN/UNPIN 的真实行为未用
   v3 复跑。Step 07 正式复评（多案例多 rep）是唯一能覆盖这些的验证。
5. **诊断复跑口径非正式**（4 案例 ×1 rep），不能当作修复成功/失败的判定；
   本 patch 的所有真实调用只是**定向探针**，不进入评分。


---

# patch 002（MVP v0.3 Step 06 · 第三轮：P6 超时缓解 + prompt 保语义精简）

- 触发：首轮与 patch 001 后，编排方两轮诊断性真实复跑确认 P1~P5 端到端有效，但**连续三轮
  真实运行每轮都出现且仅出现一次 P6 超时挟持事件**（首轮 s09-multiturn-002、诊断一
  s08-pin-unpin-001、诊断二 s07-single-field-002）。用户裁定"先做 P6 缓解补丁再进 Step 07"。
- 授权范围（只此两项，均为最小修改）：**缓解一** FeedbackEngine 引擎级重试（对齐
  Interpreter `MAX_INTERPRETATION_ATTEMPTS=2` 先例）；**缓解二** Interpreter 与
  FeedbackEngine 系统提示词保语义精简 + 版本 bump。
- 本 patch 零触碰：`evaluation/**` 既有文件（仅追加本文件）、`tests/evaluation/**`、
  Step 01~05 交付物与冻结物、超时/重试/Provider **配置项**
  （`VIA_LLM_TIMEOUT_SECONDS` / `VIA_HTTP_MAX_RETRIES` 等 adapter 层配置一律未动）、
  `pyproject.toml`、`api.md`、`.env`、v0.2 冻结降级语义、PIN/证据/冲突规则语义。
- 架构记录：`docs/handoffs/architecture_decision_004.md`（Rev.4）。

---

## P6-0 失败证据（三轮真实运行的同型事件）

| 轮次 | 运行 / 案例 | turn | feedback_id | llm_call | 失败 | 下游 L1/指标 |
|---|---|---|---|---|---|---|
| 首轮 | `erun_d35a33ffc18d6c1f` / s09-multiturn-002 | — | — | — | `provider.timeout` | 消息丢失、状态轨迹偏离 |
| 诊断一 | `erun_e7413108dc8a6104` / s08-pin-unpin-001 | `eturn_c86c180e3ebc5094` | `fbk_f9cee845098946359af5947813da0bba` | `ellm_981ba576cfcd9065` | `provider.timeout`，`latency_ms=181 947`（adapter 内 2 次退避重试共 3 次 HTTP 尝试全超时） | PIN 未落库 → t3 合法 `CLEAR subject.description` 撞标注 `forbidden_change_paths`（L1 违例）+ `invalid_state` |
| 诊断二 | `erun_f78691dffb0f7494` / s07-single-field-002 | `eturn_fbb635a1d116d9b7` | `fbk_27b7f4b4a6e04b4687764f0e9c0d8e60` | `ellm_59fdeb3f627e4de5` | `provider.timeout`，`latency_ms=181 925` | t2 SET `subject.count` 丢失 → t3 被合理判为 revise 落库 → 撞 forbidden |

**取证结论（patch 001 已确立，本 patch 重申）**：这些 L1 违例**不是产品缺陷**——产品在自身
状态下行为合理；是 Provider 超时（60s 读超时 + adapter 内 3 次尝试全超时）改变了状态轨迹，
下游轮次撞上按"上游成功"假设冻结的标注。`failure_catalog.jsonl`
`FC-P6-no-degradation-after-provider-timeout` 分类为 `provider_variance`、`worth_fixing=false`。

---

## P6-1 缓解一：FeedbackEngine 引擎级重试（对齐 Interpreter 先例）

### 修改

| 文件 | 变更 |
|---|---|
| `visual_intent_agent/feedback/engine.py` | 新增 `MAX_FEEDBACK_ATTEMPTS = 2`；新增 `_complete_with_retries`（初次 + 至多一次）与 `_is_retryable_provider_error`（`code in RETRYABLE_CODES`）；`analyze` 改走重试包装，`except ProviderError` 仍走**原封不动**的 `_provider_failure_result`；`__all__` 增加常量 |
| `visual_intent_agent/feedback/__init__.py` | 再导出 `MAX_FEEDBACK_ATTEMPTS`（向后兼容） |
| `visual_intent_agent/feedback/models.py` | 仅 docstring 准确性（"引擎自身不重试解析失败；仅可重试 Provider 错误最多 2 次"） |
| `tests/feedback/test_feedback_retry.py` | **新增** 6 条回归（红→绿） |

### 关键边界（与 v0.2 冻结语义的关系）

- **只改"降级之前多试一次"**：`_provider_failure_result` / `_failure_result` 零改动，
  重试用尽后仍是 recoverable clarify、`candidate_deltas=[]`、`preserve_paths=[]`、
  `compile_feedback=None`、`provider.*` issue、状态不变、不伪造问题；
- **只重试可重试 Provider 错误**（`RETRYABLE_CODES`：rate_limited / timeout / network /
  server_error）；`provider.auth` / `provider.invalid_request` /
  `provider.unparseable_response` 立即降级；
- **解析失败不重试**（`feedback.unparseable_output.*` 属被测系统行为），与 Interpreter
  先例唯一有意的差异，理由见 `architecture_decision_004.md`；
- **与 adapter 层正交**：Engine 层不 sleep/不退避（与 Interpreter 先例同口径）；
  adapter 的 `0.5s × 2^n` 退避与 `http_max_retries` 配置未动；
- **公开面零变化**：`analyze(self, request)` 签名、`FeedbackResult` 12 字段、落盘 schema、
  状态迁移、Issue code 全部不变。

### 红 → 绿证据（FakeLLM 离线）

临时把 `MAX_FEEDBACK_ATTEMPTS` 置 1（等价 v0.2 无重试）后运行新回归：

```text
# 红（MAX_FEEDBACK_ATTEMPTS = 1，等价"无引擎级重试"）
$ uv run pytest tests/feedback/test_feedback_retry.py -q
3 failed, 3 passed
FAILED ::test_retryable_timeout_retries_and_applies_the_second_attempt_delta
  > assert len(provider.requests) == 2
  E assert 1 == 2          # 第一次 timeout 即降级 → 第二次脚本从未被调用 → delta 丢失
FAILED ::test_retry_exhausted_degrades_to_recoverable_clarify_without_state_change
FAILED ::test_retry_budget_matches_the_interpreter_precedent

# 绿（恢复 MAX_FEEDBACK_ATTEMPTS = 2）
$ uv run pytest tests/feedback/test_feedback_retry.py -q
6 passed
```

覆盖：`timeout → 成功`（delta 保留、同一请求被调两次）；`timeout ×2`（降级 + 状态逐字节
不变）；`auth` / `invalid_request`（不重试、只调一次、`retryable=False`）；非法 JSON
（不重试、只调一次）；重试预算 == `MAX_INTERPRETATION_ATTEMPTS` == 2。

### 预期指标影响

- PIN/accept 敏感链路上的"单次超时即挟持状态轨迹"降为"**连续两次完整引擎调用都超时**才
  挟持"。若单次完整调用超时概率近似 `p`，两次都超时近似 `p²`（仅在两次尝试相互独立时成立；
  Provider 持续劣化窗口下独立性不成立，实际下降更小）。
- **非归零**：P6 是 Provider 波动，产品代码无法消除；正向探针即在 s04 t1 观察到连续
  两次/三次完整调用全超时（见 P6-4）。
- 评测侧 `llm_calls` 会因重试出现**两条** `role=feedback_engine` 记录（可审计）。

---

## P6-2 缓解二：prompt 保语义精简（字符数前后对比）

| prompt | patch 001 后（v3 / v2） | patch 002 后（v4 / v3） | Δ | 相对 v1 |
|---|---|---|---|---|
| Interpreter 系统提示词 | 5 660 字符（v3） | **4 604 字符**（v4） | **-1 056（-18.7%）** | v1=3 363 → +36.9%（v3 为 +68.3%） |
| FeedbackEngine 系统提示词 | 4 485 字符（v2） | **3 657 字符**（v3） | **-828（-18.5%）** | v1=3 512 → +4.1%（基本回到 v1 量级） |

- 版本 bump：`INTERPRETER_PROMPT_VERSION` `interpreter.v3 → v4`；
  `FEEDBACK_PROMPT_VERSION` `feedback.v2 → v3`。
- **被断言的关键句逐字保留**：patch 001 的
  `tests/intent_engine/test_interpreter_v3_boundaries.py` 与
  `test_interpreter_boundaries.py` / `test_feedback_boundaries.py` 中全部关键句断言
  （`COUNTING`、`LOCATIVE-FREE TEXT`、`CLEAR IS PER-PATH`、`This rule constrains
  subject.count ONLY`、`must NOT produce a subject.count delta`、`never default it to 1`、
  `you MUST emit SET environment.location`、`An explicit place marker always yields its own
  delta`、`MINIMAL never means fewer`、`a reason to emit fewer deltas`、`PRESERVE SCOPE`、
  `其他都不变` 等）**一字未改、全部继续通过**。因此本轮**没有需要改写的规则关键句断言**，
  只更新了版本字面量（`interpreter.v3→v4` ×3 文件、`feedback.v2→v3` ×3 文件）。
- 语义等价说明（原句 → 精简句 → 理由）见 `docs/handoffs/v0_3_step_06_patch_002.md` P6-2 表。
- 新增字符数上界守卫（只钉方向、不钉死值）：Interpreter `< 5000`、Feedback `< 4000`。
- **不主张因果**：更短 prompt = 更低单次延迟是**候选**因素；patch 001 已记载 v1 同样超时，
  故本 patch 不声称"精简消除了超时"。

---

## P6-3 真实调用验证（精简后 prompt；预算 ≤24、零图片生成）

- 探针脚本 `/tmp/via_probe_patch002.py`（Interpreter 4 项 + Feedback 1 项）与
  `/tmp/via_s04_probe.py`（s04 t1 定向重试）；**均不进入交付**。
- **实际逻辑调用总数：23 次**（≤24）；**真实图片生成：0 次**（feedback 探针用离线
  `FakeImageProvider` 装配 P3 session 取得合法 `FeedbackRequest`，不触网生成图片）。
- 说明：为覆盖"连续超时"与最终文本，共执行 6 次探针批（5+5+3+5+3+2）。每次逻辑调用内部仍
  受冻结 adapter 策略影响（最多 3 次 HTTP 尝试 × 60s 读超时）。

| # | 探针 | prompt | 输入 | 逻辑调用 | elapsed | 输出 | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | s04-t1 首试 | v4 | s04-conflict-001 t1 原文 | 1 | 181.9 s | `provider.timeout` | P6 复现 |
| 2 | s04-t1 重试 | v4 | 同上 | 1 | 113.4 s | **7 delta**（含 `environment.location=海边沙滩`、`environment.mode=摄影棚`），`count=None`，命中 `policy.hard_conflict.environment_mode_location`，`ready=False` | ✅ |
| 3 | s01-t1 | v4 | s01-complete-001 t1 原文 | 1 | 42.5 s | **7 路径完整**（description/pose/style/mode/location/framing/lighting），`count=None`，`ready=True` | ✅ |
| 4 | p4-sofa | v4 | `画一只猫在沙发上` | 1 | 38.2 s | `description=猫`、`pose=在沙发上`；**无 `count`、无 `location`** | ✅ P4 反例 |
| 5 | rule13-place | v4 | `一只黑猫坐在窗台上，地点是海边沙滩，中景。` | 1 | 22.4 s | `description=黑猫`、`pose=坐在窗台上`、**`location=海边沙滩`**、`framing=中景`；`count=None` | ✅ rule 13 |
| 6 | feedback-pin-set | v3 | `这只猫的形象我很满意，锁定它别再变了，另外把光线改成戏剧性一点的。` | 1 | 47.9 s | `revise`：**`PIN subject.description`** + **`SET lighting.character=dramatic`**，`preserve_paths=["subject.description"]`，`issues=[]` | ✅ P5 对照 |

达标判定：① s04 t1（#2）7 路径完整 + 冲突命中 + `ready=False`；② s01 t1（#3）7 路径完整、
`count` 不被凭空赋值、`ready=True`；③ P4 反例（#4）不产生 `count`/`location`；
④ rule 13（#5）`location` 正确提取；⑤ feedback（#6）PIN + SET 正确。**全部达标**。

**必须记录的局限**：s04 t1 在最终文本上首试仍 `provider.timeout`（#1），且同一探针批曾出现
**连续三次**完整调用全超时（181.9 / 181.9 / 182.0 s，`/tmp/via_s04_probe.py` 首轮）；
本次达标依赖后续重试成功。这直接证明 **P6 只能概率性缓解、不能消除**。

---

## P6-4 测试与结果（本 patch 结束）

```text
$ uv run pytest -q
2151 passed, 3 deselected in 6.40s          # 2143 基线 + 6 重试回归 + 2 prompt 精简守卫

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

## P6-5 代码变更清单（文件级）

| 文件 | 类型 | 变更 |
|---|---|---|
| `visual_intent_agent/feedback/engine.py` | 修改 | 引擎级重试 + prompt v3 精简 + 版本 bump + docstring `[Rev.3]` |
| `visual_intent_agent/feedback/__init__.py` | 修改 | 再导出 `MAX_FEEDBACK_ATTEMPTS` |
| `visual_intent_agent/feedback/models.py` | 修改 | 仅 docstring 准确性（引擎重试语义） |
| `visual_intent_agent/intent_engine/prompts.py` | 修改 | prompt v4 精简 + 版本 bump + docstring `[Rev.4]` |
| `tests/feedback/test_feedback_retry.py` | **新增** | 6 条引擎级重试回归（红→绿） |
| `tests/feedback/test_feedback_boundaries.py` | 微调 | 版本字面量 + 1 条精简守卫 |
| `tests/feedback/test_feedback_engine.py` | 微调 | 版本字面量 |
| `tests/feedback/test_feedback_public_api.py` | 微调 | 版本字面量 |
| `tests/intent_engine/test_interpreter_prompts.py` | 微调 | 版本字面量 |
| `tests/intent_engine/test_interpreter_boundaries.py` | 微调 | 版本字面量 |
| `tests/intent_engine/test_interpreter_v3_boundaries.py` | 微调 | 版本字面量 + 1 条精简守卫 |
| `docs/handoffs/architecture_decision_004.md` | **新增** | 架构修订记录 |
| `docs/handoffs/v0_3_step_06_patch_002.md` | **新增** | 统一交接记录 |
| `evaluation/reports/fix_traceability.md` | **追加** | 本 patch 002 章节（首轮与 patch 001 原文未动） |

---

## P6-6 给 Step 07 的风险清单（P6 缓解后的残留风险）

1. **P6 未被消除（最高优先）**：引擎级重试只把"单次超时挟持"降为"两次完整调用都超时
   才挟持"；本 patch 探针已观察到连续 3 次完整调用全超时。Step 07 约 130+ 次 System B
   LLM 调用中若仍有一次落在 PIN/accept 敏感链路，"gate_a_blocked" 仍可能机械触发。
   评审时请先查 `feedback_results.issues` / `llm_calls` 是否含 `provider.timeout`，
   再判产品缺陷。
2. **引擎级重试会重复计费/重复调用**：重试表现为同一 turn 两条 `role=feedback_engine`
   `llm_calls`，评测侧的"调用数/延迟"统计会随之上升；口径应与 Baseline A 对照时说明，
   不得把重试当成产品逻辑缺陷。
3. **prompt 精简未证明因果**：v4/v3 更短，但不能声称"超时率因此下降"；若 Step 07 仍高频
   超时，正确下一步在 Provider/评测口径层（超时配置、Provider 选择），而不是继续叠加重试。
4. **精简后的真实覆盖有限**：v4 只经 4 条真实探针（s04/s01/P4/rule13）、v3 只经 1 条
   feedback 探针；`must_not_clarify_paths`、多轮 PIN/UNPIN、UNPIN+CLEAR 同批次等真实行为
   仍需 Step 07 正式复评覆盖。
5. **解析失败仍不重试**：若 Step 07 大量出现 `feedback.unparseable_output.*`，那是模型输出
   畸形（被测系统行为），按冻结语义立即降级；不得用重试掩盖。
6. **探针口径非正式**：本 patch 的真实调用只是定向探针、不进入评分；所有指标结论以
   Step 07 正式复评为准。
