# v0.6 Step 02 交接：最小可复现 B/C 执行器（离线）

- 日期：2026-09-17
- 依据：[docs/task_books/mvp_v0.6/02_paired_runner.md](../task_books/mvp_v0.6/02_paired_runner.md)、
  [Step 01 协议与样本](../task_books/mvp_v0.6/01_protocol_and_cases.md)、
  [v0.6 README](../task_books/mvp_v0.6/README.md)、
  [v0.5 发布交接](v0_5_release_handoff.md)、[v0.5 后置收尾交接](post_v0_5_fixes_001_handoff.md)。
- 范围：只新增 `evaluation/v0_6/` 的 B/C 编排、记录/预算/汇总与 `tests/evaluation/test_v0_6_*.py`；
  **未改** `visual_intent_agent/**`、领域合同、Provider、`knowledge_base/**`、v0.3/v0.5 冻结物，
  **未 commit / push / stash**，**未联网、未构造真实 Provider、未执行正式评测**。
- 授权状态：`real_run_authorized = false`；G1 未授权，本步只交付离线执行器与其离线证据。
- 基线：HEAD `b039b9b7cd49becd0a25e251b37d930fee07da88`，分支 `feature`，工作区 **dirty 且未提交**。

## 0. 结论速览

| 结论项 | 结果 |
|---|---|
| 最小 B/C 执行器（Fake、零网络） | **完成**：每 `case×repetition×arm` 独立 SQLite / 会话；B 关闭 RAG、C 加载 `v0.5-approved-1`；其余条件逐项相同 |
| 固定 revision ID（纯哈希、不搜索） | **完成**：`irev_v06_<sha256(case_id|rep<N>)[:16]>`，B/C 同值；不读取候选值、不循环挑 ID |
| 合法确认绑定 | **完成**：经 `Repository` 公开写接口落固定 revision，再走 `WorkflowService.confirm_current_intent` |
| 预算守卫（覆盖内部重试） | **完成**：请求前持久化最坏预留（`1 + http_max_retries`），timeout/中断 → `unknown`，绝不自动重发 |
| 只追加续跑 | **完成**：`run.json` 只写一次；`summary` 写 append-only 快照（`summaries/summary_NNNN.json`，首次另写 `summary.json`）；`records/budget/decisions` 追加 + `flush/fsync`；`ok` 跳过、`unknown` 不重发、重试用全新 attempt 库 |
| 进程中断（C1） | **完成**：账本有预留/结算但无 PairRecord 的**两种崩溃窗口**（reserve→settle、settle→append）都重建为可追溯记录并写人工决定，绝不重发；DB 不可读时停止整批 |
| 分母不丢失败 | **完成**：区分 `planned_comparison_pairs`（case×rep）与 `planned_arm_runs`（×2）；配对结局 `both_ok/b_failed/c_failed/both_failed/unknown/not_run` 之和恒等于比较对数；子集续跑只按选中计划汇总 |
| 成本口径 | **完成**：`known_cost_minor`（已结算已知）与 `unknown_cost_upper_bound_minor`（unknown + 未结算上界）分列 |
| 真实入口 fail-closed | **完成**：授权、formal clean 身份、冻结哈希、知识快照、LLM/图像模型身份、超时/重试、预算、四项阈值任一缺失即拒绝；**本步不构造真实 Provider**；`dry_run` 只接受显式 offline/fake/test factory |
| Step 02 定向测试 | **87 passed**（context 5 / records 12 / budget 6 / gates 30 / paired_runner 26 / frozen_curriculum 6 / cli 2） |
| 30 上下文 Fake 演练 | **通过**：比较对 60、arm 运行 120、`both_ok=60`、无 unknown、无安全违例 |
| 正式运行 / 盲评 / 质量结论 | **未执行**（无 G1 授权；本步不是质量结论） |

## 1. 交付文件

| 文件 | 作用 |
|---|---|
| `evaluation/v0_6/__init__.py` | 包入口；继承父包的 `dont_write_bytecode` 并清理自身 `__pycache__`（凭据卫生扫描按 UTF-8 读取 evaluation/ 全树） |
| `evaluation/v0_6/records.py` | 输入解析（cases/annotations）+ 输出记录合同（`bc_records_v0_6`）；`classify_pair_outcome` |
| `evaluation/v0_6/context.py` | 固定 revision seed、Intent/Execution/确认装配、知识语料与 arm 引擎 |
| `evaluation/v0_6/budget.py` | `BudgetPolicy` / 只追加 `BudgetLedger`（发出前最坏预留、按最坏上界记账） |
| `evaluation/v0_6/reporting.py` | 两种分母、配对结局、检索/采用/成本汇总；只追加落盘/读取 |
| `evaluation/v0_6/paired_runner.py` | B/C 编排、fail-closed 门禁、续跑、CLI |
| `tests/evaluation/v0_6_helpers.py` | 自包含测试工具（合成 fixture、Fake provider、固定时间源） |
| `tests/evaluation/test_v0_6_context.py` | 固定 seed 纯函数性、B/C 绑定、Intent 装配 |
| `tests/evaluation/test_v0_6_records.py` | 记录合同、配对结局分类 |
| `tests/evaluation/test_v0_6_budget.py` | 最坏预留、上限、unknown、崩溃残留重放 |
| `tests/evaluation/test_v0_6_gates.py` | 真实入口门禁逐项 fail-closed、不构造真实 Provider |
| `tests/evaluation/test_v0_6_paired_runner.py` | 端到端 Fake 配对、调用顺序、续跑/崩溃重建、子集分母、unknown、失败、重试、成本分列、约束检查 |
| `tests/evaluation/test_v0_6_frozen_curriculum.py` | 对 Step 01 冻结 30 上下文跑完整 Fake 演练（比较对 60 / arm 120）+ 词表对齐 |
| `tests/evaluation/test_v0_6_cli.py` | `--list-cases` 与 `--provider real` fail-closed（不构造真实 Provider） |

## 2. 关键实现口径

### 2.1 隔离与公平性（rev seed 控制）

- 每个 `case × repetition × arm`：独立 `SQLiteRepository`（`cases/<case>/rep<N>/<arm>/session.db`，
  续跑 attempt>1 时 `.../<arm>_r<attempt>/`）、独立会话、无 active Realization；不跨库复制
  Prompt / Bundle / 确认。
- **固定 revision seed**：`intent_revision_id = irev_v06_<sha256(case_id|rep<N>)[:16]>`，单次哈希、
  B/C 两侧同值。**不读取候选值、不循环搜索、不依赖生成结果**；实现处
  `evaluation/v0_6/context.py::revision_seed_for` 只接收 `case_id` 与 `repetition`（测试用
  `inspect.signature` 断言参数集合）。
- **合法确认绑定**：`submit_message` 用 `new_id()` 生成随机 revision，无法固定；因此评测层直接经
  `Repository.append_intent_revision` / `append_execution_revision` 写固定 ID（`parent_revision_id ==
  当前 head` 校验照常），`transition_state(WAITING_CONFIRMATION)` 后调用
  `WorkflowService.confirm_current_intent`（重算 `summary_hash` 的 Hard Confirmation Gate）。
  未绕过任何领域校验；两侧 `summary_hash` 相同（它只是 intent/execution/target_model/output_size
  的函数），确认 ID 各自独立。
- **B/C 差异控制**：B = `PromptEngine(knowledge_engine=None)`；C = 注入
  `LocalKnowledgeEngine(knowledge_base/v0.5)`。其余（Intent / Execution / 目标模型 / 尺寸 /
  Provider 构造参数）逐项相同；测试逐对比较 Prompt，断言除被授权路径外 clause 完全一致。

### 2.2 调用顺序与图片 seed

- `ImageGenerationRequest` 只有 `prompt/size/model`，`OpenAIImageProvider` 只发这三个字段：
  **当前不支持显式 seed**。因此不传 seed、不伪造 Provider 未返回的 seed（记录
  `seed_requested=None`、`seed_returned=None`），测试断言请求面结构上无 `seed` 字段。
- 每对调用顺序由固定规则决定：`sha256(case_id|rep<N>|order)` 的最低位 → `B_first` / `C_first`；
  runner **真正按该顺序执行**（不只是记录），`PairRecord.call_order` 可直接复建真实次序。

### 2.3 预算 / 重试 / unknown / 续跑

- 每次图片调用**发出前**向 `budget_ledger.jsonl` 追加 `reserve`（最坏尝试 `1 + http_max_retries`、
  最坏成本），调用后追加 `settle`。adapter 内部重试不可观察，故：
  - 成功 → 按最坏上界记账（`cost_basis=worst_case_upper_bound_unobservable_internal_retries`）；
  - 确定性拒绝（`provider.auth` / `provider.invalid_request`）→ 记 0 成本；
  - 超时 / 网络 / 5xx → `unknown`，按最坏成本计入且**不自动重发**。
- 上限四重（总额 / 请求数 / 尝试数 / 单次最坏成本）在发出前判定；耗尽 → 该 arm 记
  `skipped` + `evaluation.budget_exhausted`，其余计划 arm 记 `unattempted` 并**仍计入分母**。
- 重试：仅确定性失败且显式开启 `--retry-deterministic-failures` 时，用 `GenerationPipeline.retry`
  复用**同一** PromptArtifact（不重新编译、不重新检索）；`unknown` 永不重试。
- 续跑：`ok` 跳过；`unknown` 写 `decisions_required.jsonl` 且不重发；`failed` 默认不重试，
  开启 `--retry-failed-on-resume` 时用**全新** attempt 目录/SQLite/会话重跑（避免
  `persistence.session_exists`），旧记录保留（append-only，最新者生效）。
- **进程中断（C1）**：启动时对每个**在本计划中但无 PairRecord** 的账本预留做重建，覆盖
  两个崩溃窗口：
  - `reserve` 后、`settle` 前（结果未知）；
  - `settle` 后、`PairRecord` append 前（账本已有 ok/failed/unknown 结算但无记录；否则续跑会
    命中 `persistence.session_exists` 永久卡住）。

  重建时读取原 attempt 目录 DB 的 revision / confirmation / 已落 Prompt sha256 /
  GenerationArtifact 与检索 Bundle：`settle=ok` 且有 GenerationArtifact → 重建 `ok`；
  `settle=failed` 且 DB 可读 → 重建 `failed`（code `evaluation.failed_before_record`）；
  无法可靠重建 → 保守 `unknown` + `DecisionRequired`；DB 缺失/不可读 → 独立 interruption
  记录并**停止整批**。所有重建记录 `interrupted=True`、**绝不调用 factory**、`provider_calls=[]`，
  追加 `DecisionRequired`。测试覆盖：`KeyboardInterrupt`（reserve→settle）与 monkeypatch 精确
  模拟 settle→append 窗口，两者都断言续跑 Provider 调用数不增、不 `session_exists`、记录/决定
  与分母正确。
- **只追加落盘**：`run.json` 首次写后续跑校验身份/配置（忽略 `created_at` 与逐次调用的
  `case_ids/case_count`）后**复用**，绝不覆盖；`summary` 写
  `summaries/summary_NNNN.json`（序号递增）并首次另写 `summary.json`——**取最新 = `summaries/`
  序号最大者**，`summary.json` 是首份快照。`records.jsonl` / `budget_ledger.jsonl` /
  `decisions_required.jsonl` 只追加且每次 `flush + fsync`。
- **截断 JSONL 尾行**：`read_records` 检测到末行解析失败且文件不以换行结尾 → 抛
  `TruncatedRecordsError`（fail-closed，绝不静默跳过或猜测），CLI 返回 3 并要求先核对
  未知预留、写人工决定。
- **子集续跑**：`run(case_ids=...)` 只按选中计划汇总（分母只含选中 case），不会把 run 目录里
  其它 case 的旧记录算入，`pair_outcomes` 不会出现负数 `not_run`。
- **逐 case repetitions**：只允许与全局 `repetitions` 一致；混合计划 fail-closed。
- **稳定分区**：Run ID 内容寻址（代码身份 + 协议/配置/数据集/标注/清单哈希 + 知识指纹 +
  重复数 + **有效 Provider 配置（image/llm model、timeout、retries、size、images_per_generation）
  + factory 模式** + nonce）：配置、代码、知识或 factory 变化即新 Run，不混用旧输出；
  **不使用** v0.3 runner 的 `shutil.rmtree`。

### 2.4 汇总口径（两种分母 + 成本分列）

- `planned_comparison_pairs = case_count × repetitions`（真正的 B/C 比较对数；净胜率等配对指标分母）。
- `planned_arm_runs = case_count × repetitions × 2`（基础 arm 运行 / 图片请求数）。
- `pair_outcomes = both_ok / b_failed / c_failed / both_failed / unknown / not_run`，
  之和恒等于 `planned_comparison_pairs`；失败与 unknown 绝不剔除。
- `known_cost_minor` = 已结算且状态已知（ok / 确定性 failed）的记账额；
  `unknown_cost_upper_bound_minor` = 已结算 unknown + 未结算预留的最坏上界。二者分列，
  **绝不把 unknown 叫 known**。
- 30 上下文（Step 01 配置 2 次重复）应为：比较对 60、arm 运行 120。**不得**用 120 作为配对指标分母。

### 2.5 真实入口 fail-closed（`real_run_blockers`，纯函数）

任一缺失即列出并拒绝：`real_run_authorized` 非 true；`authorization.status` 不是恰好
`"granted"`（缺失/None 也拒绝）；无 formal `CodeIdentity` / 非 `CodeIdentity` / mode 非
formal / 工作区 dirty / commit 不可解析；冻结清单哈希未通过；知识快照版本/指纹/manifest
sha256 不符；LLM 与图像的 `model_identity_verified` 非 true；LLM 与图像的 model 标识缺失或
与 `Settings.llm_model` / `settings.image_model` 不一致；LLM/图像 `timeout_seconds`、
`max_retries` 为 null 或与 `Settings` 不一致；预算缺字段或
`unknown_cost_requests_counted_as_consumed` 非 true；阈值状态未 `confirmed` 或四项阈值
（`minimum_meaningful_gain` / `allowed_completion_rate_difference` / `max_latency_seconds` /
`max_cost`）任一为 null / 非非负数。
门禁全过后，`assert_real_run_allowed` 仍拒绝——**Step 02 不构造真实 Provider**，G1 后由 Step 03 接线。
`dry_run` 另要求 `factory_mode ∈ {fake, offline, test}`，防止任意真实 factory 绕过门禁。

> factory_mode 加固说明：`image_factory` 是**程序化依赖注入**，无法在类型层证明其一定是离线
> 实现；`factory_mode` 是调用方声明的契约标签，**仅可信依赖注入**，不是类型级安全保证。本步
> 因此不提供任何真实 factory（真实入口在门禁全过后仍主动拒绝），并在合同 docstring 中明确该
> 边界，避免把标签误当作强制隔离。正式接线由 G1 后的 Step 03 负责。

## 3. 安全 / 公平性不变量检查（每条记录都执行）

`PairedRunner._constraint_violations` 在关闭库之前检查，违例写入 Run 汇总的
`safety_violations` 并**暂停批次**（保留已有结果）：

1. 确认绑定仍有效；
2. 当前 Intent / Execution revision 未被生成改写；
3. 历史 append-only：本步骤恰 1 条 intent / execution / confirmation、0 条消息；
4. 明确值原样保留；合法 PIN（**现存值 + PIN**）值原样保留且不得被知识覆盖；
5. 非授权字段（已确认值 / 委托路径不得新增值）不变；
6. C 臂知识只允许落在三知识路径、且不得落在 PIN / 已有值路径；B 臂绝不产生 Bundle；
7. PromptArtifact（若有）必须绑定本会话当前 revision 与确认。

> PIN 口径说明：Step 01 审查后 PIN 负对照改为**有现存值**的合法流程。本实现据此：
> `build_intent` 不再把 PIN 路径自动标成 `user_delegated`；检查项断言 PIN 值保持可观察。

## 4. 离线测试与结果

执行（离线、无网络、无真实 Provider）：

```bash
uv run pytest -q tests/evaluation/test_v0_6_context.py \
  tests/evaluation/test_v0_6_records.py tests/evaluation/test_v0_6_budget.py \
  tests/evaluation/test_v0_6_gates.py tests/evaluation/test_v0_6_paired_runner.py \
  tests/evaluation/test_v0_6_frozen_curriculum.py tests/evaluation/test_v0_6_cli.py
# 87 passed（context 5 / records 12 / budget 6 / gates 30 / paired_runner 26 /
#           frozen_curriculum 6 / cli 2）
```

全量默认离线回归（`addopts = -m 'not smoke'`，无网络、无凭据）：

```bash
uv run pytest -q
# 2786 passed, 3 deselected（exit 0）
```

覆盖任务书“必需离线测试”逐条：

| 任务书要求 | 覆盖测试 |
|---|---|
| 同对控制变量/种子一致，数据库与确认各自合法，非授权字段不变 | `test_v0_6_context.py`、`test_v0_6_paired_runner.py::test_bc_prompts_differ_only_in_the_authorized_path`、约束检查用例 |
| 默认零网络、未授权/dirty/预算缺失/哈希变动拒绝真实入口 | `test_v0_6_gates.py`（逐条件参数化 + 不构造真实 Provider）、`test_v0_6_cli.py`（`--list-cases` / `--provider real`） |
| 五条规则适用与回退、负对照、既有数据库兼容 | `test_v0_6_paired_runner.py`（采用/回退/明确值/PIN/无命中）、`test_v0_6_frozen_curriculum.py`（冻结 30 上下文 + 标注词表逐项对齐）；既有库兼容由既有 `tests/persistence` 与 v0.3/v0.5 套件继续覆盖，本步不改 schema |
| 模拟重试、超时、单侧/双侧失败、预算边界、中断和续跑，不重复付费请求 | `test_v0_6_budget.py`、`test_v0_6_paired_runner.py`（unknown、失败分母、预算边界、确定性重试、续跑新库、reserve→settle 与 settle→append 两个崩溃窗口恢复、截断尾行 fail-closed、子集分母一致） |
| 身份完整、报告分母不丢失败、Prompt/Bundle/Realization 可追溯 | `test_v0_6_paired_runner.py`（prompt sha 回读、bundle/realization、两种分母、pair_outcomes 之和、known/unknown 成本分列、run_id 绑定 Provider 配置与 factory 模式） |
| 未知图片 seed 不偷偷传参；调用顺序可复建 | `test_v0_6_paired_runner.py`（请求面无 seed、`call_order` 与文件顺序一致） |

## 5. 已知限制

1. **PIN 口径已对齐**：Step 01 审查后 PIN 负对照为“现存显式值 + PIN”（`explicit_paths` 与
   `pinned_paths` 同时含主路径）。`_retrieval_trace` 按 **PIN 优先** 记录冻结词表
   `pinned_existing_value_not_queried` / `path_pinned`，并断言固定值未变、未被知识覆盖；
   synthetic helper 与定向测试同步为同一口径；`test_v0_6_frozen_curriculum.py` 逐 C 臂断言
   record 的 outcome/reason/adoption/overreach 与冻结标注完全一致。
2. **candidate-agnostic revision seed 已对齐**：Step 01 标注的 `fallback_constraint` 已统一为
   `none`，并明确允许知识采用值恰好等于确定性回退值。本实现按纯 `case_id+repetition`
   单次哈希选种；若两值相同，则如实记录 `adopted_without_prompt_delta = true`（采用但 Prompt
   无可观察差异）——这是**有效样本**，不人为规避、不得据此删样本或改变阈值。
3. **图片 seed 不可用**：见 §2.2；真实结论必须披露非像素级可复现。
4. **模型身份未核实**：`qwen-image-3.0` 与官方版本映射未核实；v0.6 不执行真实运行，v1 如需接线必须重新核实。
5. **工作区 dirty**：实现依赖未提交增量；本步不 commit/stash。
6. **未执行且不再列入 v0.6**：真实 Provider、正式批次、盲评、质量/收益结论均未产生；用户已决定全部收益、图片质量和正式验收延后至 v1。工程测试通过只表示链路可运行。
7. **factory_mode 仅是被注入契约**：程序化 `image_factory` 无法在类型层验证离线性质，
   `factory_mode` 只是调用方标签；本步用“dry_run 白名单 + 真实入口始终拒绝 + 不提供真实
   factory”降低风险，但不声称类型级隔离保证。v1 若接入真实 Provider，需要重新设计并授权真实接线。
8. **崩溃窗口恢复不重放 Provider**：settle→append 窗口按账本+DB 证据重建 `ok/failed/unknown`；
   `provider_calls` 无法从账本还原每次请求参数，故重建记录的该字段为空、成本以账本预留为准，
   并统一写 `DecisionRequired` 供人工核对。

## 6. 复现命令

```bash
# 离线校验 Step 01 冻结候选集（只读）
uv run python tools/validate_v0_6_cases.py

# Step 02 定向离线测试
uv run pytest -q tests/evaluation/test_v0_6_context.py tests/evaluation/test_v0_6_records.py \
  tests/evaluation/test_v0_6_budget.py tests/evaluation/test_v0_6_gates.py \
  tests/evaluation/test_v0_6_paired_runner.py tests/evaluation/test_v0_6_frozen_curriculum.py \
  tests/evaluation/test_v0_6_cli.py

# 只列出候选集（不需要凭据/网络）
uv run python -m evaluation.v0_6.paired_runner --list-cases
```

产物（真实/Fake 演练）默认落 `outputs/evaluation_runs_v0_6/<run_id>/`（`outputs/` 已 gitignore），
内含一次写入的 `run.json`、首份 `summary.json`、append-only 的 `summaries/summary_NNNN.json`
（**取最新 = 序号最大者**）、`records.jsonl`、`budget_ledger.jsonl`、`decisions_required.jsonl`
与逐配对 `session.db`；**绝不**写入 `evaluation/`。
