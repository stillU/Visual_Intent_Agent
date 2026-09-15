# MVP v0.3 Step 01 交接记录：评测协议与数据集冻结

任务：MVP v0.3 Step 01 —— 冻结 Gate A 唯一评测口径：评测协议、Core Dataset（22 案例）、
人工标注答案、实验配置与数据集校验测试。不实现 baseline、runner、指标计算或任何产品功能。

- 依据：`docs/task_books/mvp_v0.3/README.md`、`docs/task_books/mvp_v0.3/01_evaluation_protocol_dataset.md`、
  `docs/task_books/mvp_v0.2/10_gate_a_evaluation.md`（Go 条件原文）、`docs/ARCHITECTURE.md`（Rev.3）、
  `docs/handoffs/step_09_handoff.md`、`tests/fixtures/multiturn/`（多轮案例参考）。
- 范围纪律：**未修改** `visual_intent_agent/` 下任何文件、v0.2 任务书与既有 handoff、
  `tests/` 下既有测试、`pyproject.toml`、`uv.lock`、`.env`；未运行 System B / Baseline 对答案；
  标注全部由任务书与冻结合同静态推导（并用只读脚本对 Validator/Reducer/Policy 做了静态一致性复核，
  不涉及任何 LLM 或系统实际输出）。

---

## 完成内容

### 1. `evaluation/protocol.md` — Gate A 评测协议（冻结）

- Baseline A / System B 精确链路与入口映射（`user_message`/`clarification_answer` →
  `WorkflowService.submit_message`；`image_feedback`/`accept` → `ReviewService.submit_feedback`）；
  Baseline 复用同一 Provider/模型/超时/重试，不提问、不维护状态；`accept` 轮不在 Baseline 执行。
- 四层指标口径：L1 四条不变量（未授权不变/旧确认不复用/pinned 不被覆盖/历史不改）逐轮自动检查；
  L2 五指标（Delta Accuracy、Missing Decision Recall、Clarification Precision、Conflict Detection、
  Delegation Scope Accuracy）仅 System B；L3 四指标（Coverage/Unauthorized Addition/Preservation/
  Model Compatibility）双系统同口径；L4 四维度人工盲评（去标签、随机顺序、原始评分落盘）。
- Go 条件 5 条按 v0.2 Step 10 原文冻结并给出操作化判定，不放松、不预设百分比门槛。
- 自动 vs 人工分工、缺失数据三态（`not_applicable` / `missing_data` / `failed`，不剔分母）、
  随机性记录字段、重复次数（L1~L3×2，L4×1，禁止单图重抽）、失败重试口径（仅 adapter 内冻结策略；
  解析失败属被测行为不外部重试；基础设施崩溃整案重跑 1 次）。
- `evaluation/` 目录组织约定（第 8 节）：本步为纯数据/文档目录、无 `__init__.py`，
  后续步骤模块与运行产物的归属约定。

### 2. `evaluation/fixtures/core_v0_3.jsonl` — Core Dataset（22 案例，冻结）

每案例：稳定 `case_id`（`sXX-<slug>-<nnn>`）、`scenario`（10 类封闭枚举）、`turn_type`、
`tags`、`turns`（`user_message` / `clarification_answer` / `image_feedback` / `accept`，
`clarification_answer` 可带按待答问题目标路径选择的 `answer_variants`）。用户文本模拟真实中文用户。

场景覆盖（22 例）：完整明确单轮 ×2、缺失 Core ×2、缺失 Perceptual ×2、冲突 ×3、
明确委托 ×2、模糊委托 ×2、单字段修改 ×2、PIN/UNPIN ×2、连续 3~5 轮修改 ×3、
对图片的模糊反馈 ×2。单轮 2 例；多轮 20 例，轮次全部 ∈ [3,5]。

### 3. `evaluation/annotations/core_v0_3.jsonl` — 人工标注（与 fixture 同 ID 同序，冻结）

逐轮：`expected_deltas`（value_match 四模式：`exact_token`/`contains_any`/`int_equals`/
`any_non_empty`）、`acceptable_extra_deltas`、`expected_rejections`（条件性，
`expected_issue_codes` 命中任一即符合）、`forbidden_change_paths`、`paths_must_remain_unset`、
`expected_resolution_records`、`blocking_missing_paths_after_turn`（与 `assess` 的
`unresolved_decisions` 同口径：每 Decision 报第一个未解决成员）、`must_clarify` /
`must_not_clarify_paths` / `acceptable_clarify_paths`、`expected_conflicts`（冻结 rule_id +
blocking 标志）、`expected_feedback_decision`、`expected_carry`、`expected_outcome`（四值枚举）。
案例级：`image_evaluation_dimensions`（含逐维度评审焦点与 `applies_to_turns`）、
`prompt_expectations`（L3 关键词真值）。

全部路径/操作/Resolution 使用系统冻结词汇（`INTENT_PATHS`、SET/CLEAR/PIN/UNPIN、
Resolution 四值），由合同测试强制。

### 4. `evaluation/configs/gate_a_v0_3.json` — 冻结实验配置

环境变量名与 ARCHITECTURE.md 第 3 节命名表逐字一致（`VIA_PROVIDER_BASE_URL` /
`VIA_PROVIDER_API_KEY` / `VIA_LLM_MODEL` / `VIA_IMAGE_MODEL` / 两个超时 / 重试），
默认值 `qwen3.8-max` / `qwen-image-3.0` / 60s / 120s / 2；LLM 不显式设置
temperature/max_tokens（与 System B 冻结调用行为一致）；图像 `1024x1024`、每次 1 张、
seed 只记录不伪造；QuestionBuilder 用确定性模板；重复次数与失败处理口径；无任何明文凭据。

### 5. `evaluation/frozen_manifest_v0_3.json` + 哈希固化

四个冻结文件的 sha256 同时写入清单 sidecar 与 `tests/evaluation/test_dataset_contract.py`
内嵌常量；任何内容变化被默认测试捕获。修订必须新版本、不得覆盖。

冻结哈希（sha256）：

| 文件 | 哈希 |
|---|---|
| `evaluation/protocol.md` | `70cc0b3c4bde970a91172191b398cf3869df539fa0971ae85cfd849c52c26d93` |
| `evaluation/fixtures/core_v0_3.jsonl` | `0519534702225949bede2782516cd1543c936d2fd298834ad36dd253fc63a64b` |
| `evaluation/annotations/core_v0_3.jsonl` | `d1712aab45c42251d920d22f6a5a6a1fe5bef964f577e2b45aead851dc6a6b57` |
| `evaluation/configs/gate_a_v0_3.json` | `3b6159f8bc620f6c31fc85fb40a937a7ced1a71296a8c6ab9d0514c0f329905b` |

### 6. `tests/evaluation/test_dataset_contract.py` — 42 条离线合同测试

ID 唯一与格式、字段完整、fixture/annotation 同 ID 同序、逐轮对齐、路径全部 ∈
`INTENT_PATHS`（只 import 不复制清单）、操作/Resolution 冻结枚举、SET 形状镜像
domain 不变量、value_match 模式完整性、issue code 命名空间合法、冲突 rule_id 冻结、
多轮引用闭合（回答跟随提问、反馈/接受需有在先图片、accept 终局）、outcome 一致性、
场景全覆盖（10 类、每类 ≥2、总数 ∈ [20,30]、多轮 3~5 轮）、L4 维度枚举与引用闭合、
无图案例维度为空、配置与命名表/默认值一致、凭据卫生扫描（sk-/Bearer 模式）、
冻结哈希双处一致。无 `conftest.py`、不跨目录 import、全离线。

---

## 变更文件

新增（6 个文件，无修改既有文件）：

- `evaluation/protocol.md`
- `evaluation/fixtures/core_v0_3.jsonl`
- `evaluation/annotations/core_v0_3.jsonl`
- `evaluation/configs/gate_a_v0_3.json`
- `evaluation/frozen_manifest_v0_3.json`
- `tests/evaluation/test_dataset_contract.py`
- `docs/handoffs/v0_3_step_01_handoff.md`（本文件）

## 公开接口

本步不新增产品代码与 Issue code。供后续步骤消费的冻结物：

- 协议版本 `gate_a_protocol_v0_3`；数据集版本 `core_v0_3`；配置版本 `gate_a_v0_3`；
  清单版本 `frozen_manifest_v0_3`。
- 数据集/标注/config 的字段语义以 `evaluation/protocol.md` 第 2、8 节为准；
  `tests/evaluation/test_dataset_contract.py` 是结构口径的可执行定义。

## 测试与实验结果

```text
$ uv run pytest tests/evaluation -q
42 passed in 0.07s

$ uv run pytest -q
1885 passed, 3 deselected in 4.14s   # 基线 1843 + 本步 42；既有测试零回归
```

另用一次性只读脚本（不进仓库）把 22 案例的金标准 Delta 逐轮代入冻结的
Validator/Reducer/Policy（含 1024x1024 执行上下文）复核：全部标注的
`expected_outcome` / `must_clarify` / `blocking_missing_paths_after_turn` /
`expected_conflicts` / `expected_rejections` / `expected_resolution_records` /
`paths_must_remain_unset` 与冻结合同静态推导一致（复核不涉及任何 LLM 与系统实际输出）。

## 原始 Artifact 位置

- 冻结物：`evaluation/`（上表 5 文件，哈希见第 5 节）。
- 本步不产生运行记录（无 A/B 执行）。

## 已知限制

1. **冲突检测依赖值词表**：三条 hard conflict 谓词按冻结英文词表匹配值文本；Interpreter
   若以中文存值可能不命中。标注中的 `expected_conflicts` 是真值，系统未检出按
   Conflict Detection 漏报记录——这是被测行为，不是标注缺陷。
2. **委托只能经待答问题授权**（Validator 检查 5）：首轮裸委托标记必然被拒，
   “明确委托”场景因此均为多轮（表达意愿 → 系统提问 → 回答中委托）。这为
   Clarification Cost 提供了天然测量点。
3. **`s06-delegate-vague-002` 以持续澄清结束、System B 无图片**：冻结单问题策略下
   批量模糊委托无法一次授权多路径。L4 对该案例按 `missing_data` 报告；该案例同时是
   缺失数据报告规则的实证样本。
4. **Reducer 保留既有 ResolutionRecord**：用户以具体值接管 delegated 路径后，其记录
   仍为 `user_delegated`（冻结语义，路径已解决、不影响 ready）。Step 03 实现
   Delegation Scope / 状态比对时需按此口径，不得把它判为异常。
5. **对 pinned 路径的 SET 为冻结允许行为**（Validator 只拒 CLEAR-pinned）：
   L1 的“pinned 不被覆盖”不变量按值不被 SET/CLEAR 改变测量，对 pinned 的 SET 逐例
   记录供归因（数据集以 CLEAR-pinned 拒绝覆盖该不变量，见 s08-pin-unpin-001）。
6. **关键词真值的语言局限**：`prompt_expectations` / `value_match` 关键词为中英并列，
   只保证“命中任一即符合”的宽松下界；L3 指标是文本层代理，不得代替 L4。
7. 22 案例规模以稳定复现问题为准（任务书口径），不构成统计功效证明；扩展必须新版本。

## 对下一步的输入

- **Step 02（Baseline A）**：`evaluation/configs/gate_a_v0_3.json` 的
  `baseline_a_driver` 与凭据/模型/重复/失败口径；`accept` 轮不执行；其余轮次用户文本逐字；
  禁止 import `domain`/`validation`/`policy`/`workflow`/`prompt_engine`/`realization`。
- **Step 03（Runner 与 L1~L3）**：协议第 2~5 节是指标与缺失数据的唯一口径；
  多轮驱动规则（自动确认、answer_variants、flow_deviation）见协议第 3 节与配置
  `system_b_driver`；`blocking_missing_paths_after_turn` 与 `assess` 的
  `unresolved_decisions` 同口径；注意已知限制 4/5 的比对口径。
- **Step 04（图片 A/B 盲评）**：L4 维度与逐案例评审焦点在标注
  `image_evaluation_dimensions`；无图案例报 `missing_data`；`s08-pin-unpin-001` 的
  评审须知（PIN 保护下“不删猫”是期望行为）已写进该案例标注 `notes`。

## 是否满足验收条件

**是。** 任务书验收条件逐条核对：

| # | 条件 | 核对 |
|---|---|---|
| 1 | 覆盖全部规定场景，单轮多轮均有 | 10 类场景 22 案例（每类 ≥2），单轮 2 例、多轮 20 例（3~5 轮），合同测试强制 |
| 2 | 标注不依赖待评系统实际输出 | 标注由任务书与冻结合同静态推导；未运行 System B/LLM；独立性与三条决定标注形态的冻结行为已写入协议第 2.4 节 |
| 3 | Provider 条件和评分口径可被后续 Agent 直接读取 | `configs/gate_a_v0_3.json`（环境变量名/默认值/重复/失败）+ `protocol.md`（四层指标、Go 条件、驱动规则） |
| 4 | 数据校验测试离线通过 | `uv run pytest tests/evaluation -q` → 42 passed（全离线）；全量 1885 passed 零回归 |
| 5 | 数据集以内容哈希冻结，修改必须新版本 | 4 个冻结文件 sha256 双处固化（清单 + 测试内嵌常量），默认测试捕获任何变化；修订规则写入协议第 9 节 |
