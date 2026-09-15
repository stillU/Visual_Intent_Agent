# Gate A 评测协议（MVP v0.3 · Step 01 冻结）

- 协议版本：`gate_a_protocol_v0_3`（本文件纳入内容哈希冻结清单，见第 12 节）
- 依据：`docs/task_books/mvp_v0.2/10_gate_a_evaluation.md`（四层评测与 5 条 Go 条件的原始定义）、
  `docs/task_books/mvp_v0.3/01_evaluation_protocol_dataset.md`、`docs/ARCHITECTURE.md`（Rev.3）、
  `docs/handoffs/step_09_handoff.md`
- 地位：Gate A 评测的**唯一口径**。评测开始后（Step 03 首次真实运行起）不得为提高分数修改本协议、
  数据集、标注或配置；任何修订必须生成新版本文件，旧版本只读保留。

---

## 1. 对比系统

### Baseline A（Direct LLM）

```text
User → 同一 LLM → Prompt → 同一 Image Model → Image
多轮：历史对话 + 前轮 Prompt 摘要 → LLM → 新 Prompt → Image
```

- 复用 `visual_intent_agent/providers/{llm,image}.py` 的同一 Provider、模型、超时与重试配置；
  不得选用更弱或更强的模型，不得为基线单独调参。
- 不提问、不维护字段状态、不输出 IntentDelta、不使用 System B 的确认/Realization 信息。
- 多轮输入格式由 Step 02 冻结并在 Artifact 中记录版本；协议只要求：用户文本逐字来自冻结数据集，
  历史顺序与轮次边界与 System B 一致。
- `accept` 轮次**不在 Baseline A 上执行**（用户满意即停止，不再产生 LLM 调用与图片）；
  其余全部轮次的用户文本逐字执行。

### System B（Visual Intent Agent，v0.2 冻结实现）

```text
User → Intent → Clarification → Confirmation → Prompt → 同一 Image Model → Image
多轮：IntentDelta → Realization Carry → New Prompt → Image
```

- 代码零修改参评；评测代码不得改动任何产品规则。
- 入口映射：`user_message` / `clarification_answer` → `WorkflowService.submit_message`；
  `image_feedback` / `accept` → `ReviewService.submit_feedback`。
- `QuestionBuilder` 一律以**确定性模板**构造（不注入 LLM 改写措辞），消除措辞随机性。

### 共同条件（冻结，见 `evaluation/configs/gate_a_v0_3.json`）

同一 base_url/key（只引用环境变量名）、同一 LLM 模型（默认 `qwen3.8-max`）、
同一图像模型（默认 `qwen-image-3.0`）、同一输出尺寸 `1024x1024`、每次生成 1 张、
同一超时与重试策略。LLM 调用不显式设置 `temperature` / `max_tokens`
（与 System B 三处调用点的冻结行为一致，使用 Provider 默认）；System B 的
Interpreter / FeedbackEngine 使用其冻结的 `response_format={"type": "json_object"}`，
Baseline A 的直接 Prompt 生成不使用 `response_format`（自由文本）。该差异是系统结构本身，
不构成评分偏差；记录时照实登记（第 9 节）。

---

## 2. 数据集

- 文件：`evaluation/fixtures/core_v0_3.jsonl`（输入，22 案例）+
  `evaluation/annotations/core_v0_3.jsonl`（同 ID 同序的人工标注答案）。
- 版本：`dataset_version = "core_v0_3"`；冻结以内容哈希标识（第 12 节）。
- 场景覆盖（每类场景 ≥2 例）：完整明确单轮 / 缺失 Core Decision / 缺失 Perceptual Decision /
  冲突 / 明确委托 / 模糊委托 / 单字段修改 / PIN 与 UNPIN / 连续 3~5 轮修改 / 对图片的模糊反馈。
- 轮次：单轮案例 1 轮；多轮案例 3~5 轮（`turn_type = single | multi`）。
- 用户文本模拟真实中文用户（可夹带英文术语）；标注中的路径、操作、Resolution、
  Severity 一律使用系统冻结词汇（`INTENT_PATHS` 12 条、`SET/CLEAR/PIN/UNPIN`、
  `user_specified/user_confirmed_proposal/user_delegated/not_applicable` 等），不发明新词。

### 2.1 Fixture 字段

| 字段 | 含义 |
|---|---|
| `case_id` | 稳定 ID，形如 `s04-conflict-001`（场景前缀 + 序号），冻结后不复用、不改名 |
| `scenario` | 封闭枚举（10 类，见校验测试） |
| `turns[].kind` | `user_message` / `clarification_answer` / `image_feedback` / `accept` |
| `turns[].user_text` | 逐字输入两个系统的用户文本 |
| `turns[].answer_variants` | 可选。`clarification_answer` 轮按 System B 实际待答问题的 `target_path` 选择回答文本；无匹配时用 `user_text`（即 default）。Baseline A 始终使用 `user_text` |

### 2.2 标注字段（ground truth，对待评系统保密）

逐轮：`expected_deltas`（金标准 Delta 集合）、`acceptable_extra_deltas`（不罚的可接受附加，
如 preserve 展开成的 PIN）、`expected_rejections`（条件性：若系统提出则必须被拒；
`expected_issue_codes` 为可接受 code 列表，命中任一即符合——具体 code 取决于
Interpreter 的证据绑定方式，拒绝本身才是冻结行为）、`forbidden_change_paths`
（本轮后值与 Resolution 必须逐值不变的路径；PIN 状态变化只允许出现在
expected/acceptable 中）、`paths_must_remain_unset`、`expected_resolution_records`、
`blocking_missing_paths_after_turn`（与 `assess` 的 `unresolved_decisions`
（action=block）同口径：每个 Decision 按声明顺序只报第一个未解决成员路径）、
`must_clarify` / `must_not_clarify_paths` / `acceptable_clarify_paths`、
`expected_conflicts`（含 `blocking` 标志）、`expected_feedback_decision`、
`expected_carry`（carried/invalidated）、
`expected_outcome`（`ready_and_generate` / `clarification_expected` / `accepted_completed` /
`no_state_change`）。

案例级：`image_evaluation_dimensions`（L4 维度与逐维度评审焦点）、
`prompt_expectations`（L3 关键词真值：同义关键词组 `must_mention_groups` 与
`must_not_mention`）。

### 2.3 Delta 值匹配模式（`value_match`）

| mode | 语义 |
|---|---|
| `exact_token` | 规范化（小写、`_`/`-` 视空格、压缩空白）后字符串相等 |
| `contains_any` | 值（不区分大小写）包含关键词列表中至少一个（中英文关键词并列给出） |
| `int_equals` | 整数相等（`subject.count` 唯一 int 路径） |
| `any_non_empty` | 任意非空值 |
| `null` | 该 Delta 不携带值（CLEAR/PIN/UNPIN 或纯 resolution SET） |

### 2.4 标注独立性声明

标注由 Step 01 依据任务书与 v0.2 冻结合同（`DECISION_POLICIES` 规则表、Validator 七项检查、
Reducer 语义、FeedbackEngine 处理规则）客观推导；**未运行 System B 对答案**。
其中三条冻结行为直接决定标注形态，后续步骤不得据此声称标注错误：

1. `user_delegated` / `user_confirmed_proposal` 的证据必须绑定该路径的**当前待答问题**
   （Validator 检查 5）——首轮消息中的裸委托标记必然被拒，系统正确行为是提出允许委托的问题；
2. 待答问题绑定的回答不得改变目标以外的路径（`evidence_pending_question_path_mismatch`）——
   “其他全部你决定”无法一次授权多个路径；
3. PIN/CLEAR 检查针对**批次开始前**的初始状态——同批次 UNPIN 后 CLEAR 仍被拒；
   CLEAR pinned 路径必被拒；CLEAR 同时移除值与 Resolution 记录（不写成委托）。

另注意（数据观察，非标注依据）：Reducer 对“携带 value 但不携带 resolution 的 SET”
保留该路径已有 ResolutionRecord——用户接管 delegated 路径后其记录仍为 `user_delegated`
（路径已解决，不影响 ready 判定）；冲突规则谓词按冻结英文词表匹配值文本，
Interpreter 若以中文存值可能不命中——这属于**被测量的 Conflict Detection 行为**，
标注中的 `expected_conflicts` 是真值，不因系统未检出而改变。

---

## 3. 运行驱动规则（Runner 口径）

### System B

1. 每轮按 `kind` 选择入口（第 1 节）；逐字提交 `user_text`（或 `answer_variants` 选中文本）。
2. 会话进入 `WAITING_CONFIRMATION` 时，Runner 始终代表用户**自动确认当前摘要**
   （重算 `summary_hash` 后调用 `confirm_current_intent`），随后立即
   `GenerationPipeline.generate`；确认与生成计入该轮。
3. 会话进入 `WAITING_CLARIFICATION` 时，Runner 不即兴作答：等待脚本中的下一轮
   `clarification_answer`；若下一轮不是回答或 `answer_variants` 无匹配且与默认文本不符，
   仍按脚本提交并在结果中记录 `flow_deviation = true`（不静默跳过、不替用户改写）。
4. `accept` 轮：提交 `ReviewService.submit_feedback`；期望 `decision=accept` → `COMPLETED`。
5. 任何与 `expected_outcome` 的偏离都原样记录；指标按实际发生计算，失败不从分母剔除。

### Baseline A

1. 逐字串联全部非 `accept` 轮次的用户文本；每轮产出 1 个 Prompt + 1 张图片。
2. `accept` 轮不执行（第 1 节）。
3. Baseline 无澄清/确认/状态概念：L1、L2 指标一律记 `not_applicable`（不得伪装成零分或满分）。

---

## 4. 四层指标

四层必须分开报告，禁止用任何单层指标代替最终效果。

### Layer 1 — State Correctness（自动；仅 System B；目标 100%）

逐轮、逐案例检查四条核心不变量：

1. **未授权字段不改变**：本轮被接受 Delta 之外的路径，值与 Resolution 逐值不变
   （对照 `forbidden_change_paths` 与系统实际 `applied_deltas`）。
2. **旧确认不复用**：任何 `confirmation_invalidated=True` 的变更后，未经新确认不得生成；
   `confirm_current_intent` 绑定过期 revision/hash 必须失败。
3. **pinned 内容不被覆盖**：pinned 路径的值不被 SET/CLEAR 改变
   （CLEAR 必被拒；对 pinned 路径的 SET 虽为冻结允许行为，仍逐例记录供归因）；
   PIN/UNPIN 状态变化必须来自用户显式操作。
4. **历史不被修改**：Intent/Execution revision、Prompt/Generation/Feedback/Realization
   各表 append-only——计数单调不减、既有行 payload 逐字节不变。

任一核心不变量失败 → 该案 `l1_failed = true`，摘要显式标记 **Gate A 阻断**。

### Layer 2 — Intent Understanding（自动；仅 System B）

| 指标 | 口径 |
|---|---|
| Intent Delta Accuracy | 逐轮对金标准：`expected_deltas` 按 (operation, path) + `value_match` + `resolution` 匹配得 recall；系统被接受 Delta 中落入 `expected ∪ acceptable_extra` 的比例得 precision；其余被接受 Delta 计为越界产生 |
| Missing Decision Recall | `blocking_missing_paths_after_turn` 中被系统 `unresolved_decisions`（action=block）覆盖的比例 |
| Clarification Precision | 系统实际提问的 target_path ∈ `must_clarify ∪ acceptable_clarify_paths` 且不在 `must_not_clarify_paths`；不该问而问、问错路径均计失败；`expected_outcome=ready_and_generate` 的轮次提出任何问题即失败 |
| Conflict Detection | `expected_conflicts` 中被系统 `conflicts` / `detected_conflicts`（同 rule_id）检出的比例；`blocking=true` 的 hard conflict 未检出且系统进入 ready 记为严重失败 |
| Delegation Scope Accuracy | 系统 Intent 中 `user_delegated` 记录的路径集合 == 标注期望（`expected_resolution_records` 中 delegated 项）；多标（越界）/漏标分别计数 |

### Layer 3 — Prompt Semantics（自动；System B 与 Baseline A 同口径）

基于每个 PromptArtifact（B）/ 基线 Prompt 记录（A）与标注 `prompt_expectations`：

| 指标 | 口径 |
|---|---|
| Intent Coverage | `must_mention_groups` 中至少命中一个关键词的组占比（逐案例，最终就绪态 Prompt） |
| Unauthorized Addition | `must_not_mention` 关键词命中数；B 侧另检查 `prompt.unauthorized_addition` 防护是否被触发/绕过 |
| Preservation | 多轮修改轮次：前轮 Prompt 中对应 `forbidden_change_paths` 内容的保留（关键词组在前轮命中且本轮仍命中的比例） |
| Model Compatibility | Prompt 参数面合法（size 形如 `<w>x<h>` 且为冻结值、无越权参数、目标模型名正确） |

L3 是 Prompt 文本层指标，**不得**当作图片结果评分（v0.2 Step 10 禁令）。

### Layer 4 — Image Result（人工盲评；两系统成对）

维度（每案例 `image_evaluation_dimensions` 给出评审焦点，可含 `applies_to_turns`）：

| 维度 | 评审问题 |
|---|---|
| Intent Alignment | 图片与案例最终用户意图的一致程度（1–5） |
| Attribute Preservation | 指定轮次间未修改属性是否保持（1–5；单轮案例 `not_applicable`） |
| Edit Success | 指定修改轮次的修改是否落实（1–5；以标注为准——例如 PIN 保护下“不落实删除”是期望行为） |
| User Preference | 去标签成对偏好（A 优 / B 优 / 平局） |

盲评规程：去除系统标签与文件名痕迹；A/B 呈现顺序逐案例随机化并记录；评审依据
`image_evaluation_dimensions[].focus` 逐维度打分；原始评分逐案例逐维度落盘
（评审人 ID、时间戳、分数、理由），聚合时保留分布而非只报均值。

---

## 5. 自动 vs 人工与缺失数据

- 自动计算：L1 全部、L2 全部、L3 全部；L4 不自动评分。
- 人工盲评：仅 L4 四个维度。
- 缺失数据三态，逐案例逐指标显式记录，**不得**从分母静默剔除：
  - `not_applicable`：指标对该系统/案例结构上不适用（如 L1/L2 对 Baseline A、
    Preservation 对单轮案例）；
  - `missing_data`：应产生而未产生（如案例未生成图片——`s06-delegate-vague-002` 的
    System B 侧无图，L4 对该案例两系统均报 `missing_data` 并注明原因）；
  - `failed`：执行失败（Provider 错误、超时、解析失败用尽等），携带错误 code。
- 聚合摘要必须给出各指标的样本量与三态计数；聚合结果可追溯到原始 Turn 与 Artifact。

---

## 6. Gate A Go 条件（以 v0.2 Step 10 原文为准，不得放松）

必须同时满足：

1. **L1 核心不变量 100% 通过**（四条全绿；任一失败直接 No-Go，先修复 P0~P3）。
2. 结构化系统在 **Silent Decision / Missing Decision / 多轮 Drift** 上至少一项有明确改善
   （对照 Baseline A 的同案例表现：Silent Decision 以 L3 Unauthorized Addition +
   未说明维度被静默具体化的案例数测量；Missing Decision 以 L2 Missing Decision Recall
   对照 Baseline 无此能力的结构性缺席；Drift 以 L4 Attribute Preservation +
   L3 Preservation 测量）。
3. **Edit Success 或 User Preference** 至少一项有可测量优势（L4）。
4. **Clarification Cost** 没有高到抵消上述收益（逐案例提问次数、达到就绪所需轮次、
   用户回答字数，与收益指标同表报告；不设先验数值门槛，首轮报告实际分布）。
5. 结果可在**重复实验**中复现（第 7 节重复次数下，L1 结论不变、L2/L3 关键指标方向一致）。

不预先伪造百分比门槛；首轮实验报告实际分布，后续量化阈值在 Step 05/07 另行冻结。

---

## 7. 重复、随机性与失败口径

- 重复次数（冻结于配置）：L1~L3 每案例运行 **2** 次（测量 LLM 层稳定性）；
  L4 图片每系统每案例 **1** 次成对产生；禁止对单张图片“重抽”，任何复评必须整案重跑并保留全部原始记录。
- 随机性记录（每次 LLM/图像调用）：实际发送的 model / temperature / max_tokens /
  response_format、provider_request_id、图像 seed（Provider 未返回则记 null，禁止伪造）、
  延迟、重试次数；Run 级记录：数据集与配置文件的 sha256、代码版本标识、协议版本。
- 失败重试：Provider 错误只经 adapter 内冻结策略重试（可重试类别、最多
  `VIA_HTTP_MAX_RETRIES` 次、指数退避 `0.5s × 2^n`）；用尽后记 `failed` 保留在分母。
  LLM 输出解析失败属于**被测系统行为**（System B 的可恢复 issue 路径），不外部重试；
  基础设施级崩溃（非 Provider、非被测代码）允许整案重跑 1 次，两次原始记录均保留。

---

## 8. 目录组织约定（供后续步骤遵循）

`evaluation/` 是 v0.3 评测的顶层目录，本步只交付冻结物：

```text
evaluation/
├── protocol.md                    # 本协议（冻结）
├── fixtures/core_v0_3.jsonl       # Core Dataset 输入（冻结）
├── annotations/core_v0_3.jsonl    # 人工标注答案（冻结）
├── configs/gate_a_v0_3.json       # 实验配置（冻结）
└── frozen_manifest_v0_3.json      # 内容哈希清单（sidecar）
```

- 本步不创建任何 Python 模块；`evaluation/` 当前为纯数据/文档目录，**不放**
  `__init__.py`。Step 02 起新增 `evaluation/models.py`、`direct_baseline.py`、
  `runner.py`、`metrics/` 等模块时，是否将其变为包由对应步骤任务书决定，
  但不得改动本步四个冻结数据文件与清单。
- `tests/evaluation/` 遵循 v0.2 测试惯例：无 `conftest.py`、不跨测试目录 import、
  共享工具放唯一命名 helper 模块；默认全离线。
- 运行产物（Run/Turn 记录、盲评表、图片副本）由 Step 03/04 放在
  `evaluation/runs/<run_id>/`（运行期生成，不入冻结清单，不计入默认测试）。

## 9. 冻结与修订

- 冻结物：`protocol.md`、`fixtures/core_v0_3.jsonl`、`annotations/core_v0_3.jsonl`、
  `configs/gate_a_v0_3.json`，其 sha256 固化于 `frozen_manifest_v0_3.json` 与
  `tests/evaluation/test_dataset_contract.py`；任何内容变化都会被默认测试捕获。
- 修订规则：冻结后任何修改必须生成**新版本文件**（新版本号、新清单、新哈希），
  旧版本只读保留，不得覆盖。
