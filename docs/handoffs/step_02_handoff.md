# Step 02 交接记录：Validator 与 Reducer

任务：Step 02 — Validator 与 Reducer（Candidate Delta 确定性验证、不可变 Reducer、ChangeSummary、状态不变量测试）

## 完成内容

### 1. `validation/models.py` — 五个公开结果模型（frozen + extra="forbid"）

| 模型 | 字段 |
|---|---|
| `EvidenceContext` | `available_message_ids: frozenset[str]`；`pending_question_id: str\|None`；`pending_question_path: str\|None` |
| `RejectedDelta` | `delta: IntentDelta`；`issues: list[Issue]` |
| `ValidationResult` | `accepted: list[IntentDelta]`；`rejected: list[RejectedDelta]`；`issues: list[Issue]`（全部 issue 的扁平汇总） |
| `ChangeSummary` | `changed_paths`；`pinned_paths`；`unpinned_paths`；`cleared_paths`；`confirmation_invalidated` |
| `ReduceResult` | `intent: VisualIntent`；`change_summary: ChangeSummary` |

默认空列表一律 `Field(default_factory=list)`，避免实例间共享可变对象（`frozen=True` 不阻止 `list` 内容原地修改）。

### 2. `validation/validator.py` — 7 项检查，全部确定性

`validate(candidate_deltas, evidence_context, current_intent) -> ValidationResult`

按任务书原文顺序执行，且**对每个 Delta 运行全部适用检查**（命中第一条不停止），因此一个 Delta 可携带多条 issue；不变量是"被拒 Delta 必有 ≥1 条 `severity=error`、code ∈ `validation.*` 的 issue"。

1. **operation 合法**：`operation ∈ {SET, CLEAR, PIN, UNPIN}`；
2. **path ∈ Schema v1 白名单**：直接 `from visual_intent_agent.domain import INTENT_PATHS`，本步不维护第二份清单；
3. **value 类型兼容**：`subject.count` → 严格 `int` 且 `> 0`（`bool`/`float`/`"3"` 都拒绝）；其余 11 条 → 严格 `str`；
4. **证据存在性**：非空 `evidence_refs`；每条 `message_id ∈ available_message_ids`；证据带 `pending_question_id` 时必须等于当前 `pending_question_id`（否则 `unknown`/`mismatch`），且当 `pending_question_path` 非空时 `delta.path` 必须等于它（回答问题的授权不扩大）；**不**限制"当前有 pending question 时其他路径不能被普通消息证据修改"——待答问题只约束回答它的证据；
5. **授权范围**：resolution 自报的授权强度必须被证据支持（矩阵见下）；
6. **系统字段保护**：`path` 命中 `SYSTEM_FIELD_NAMES`（含以其为根的路径，如 `workflow_state.*`）一律拒绝；
7. **PIN / UNPIN（+ CLEAR）目标合法性**：PIN 要求该路径当前有值；UNPIN 要求当前确实 pinned；CLEAR 要求当前未 pinned。

额外检查（同样显式拒绝、不静默）：**无效果 Delta** —— 纯 resolution 的 SET 且 Resolution 未变；CLEAR 一个既无值也无 ResolutionRecord 的路径。

**检查 5 的授权矩阵（Step 02 细化，只收紧不放宽）**

| Delta 自报 | 必须满足 | 违反时 code |
|---|---|---|
| `user_specified` | 必须携带具体 `value` | `validation.unauthorized_resolution_claim` |
| `not_applicable` | 必须携带具体 `value`（"不适用"必须有可检查理由，不能掩盖遗漏） | 同上 |
| `user_confirmed_proposal` | 证据必须绑定当前待答问题（确认的是已展示的提案） | 同上 |
| `user_delegated` | 证据必须绑定当前待答问题（"明确授权"必须落在问题范围内；`EvidenceRef` 没有路径字段，裸消息无法证明范围） | 同上 |
| `resolution` 为 None | 值型 SET 即用户指定；不做额外声明 | — |

非法 Delta **整个**进 `rejected`，`delta` 原样保存，绝不改写路径、绝不静默忽略、绝不隐式填充。

### 3. `validation/reducer.py` — 确定性不可变 Reducer

`reduce(current_intent, validated_deltas) -> ReduceResult`（纯函数）

- **SET**：只写指定路径。带 `resolution` → 写 `resolutions[path]`（带该 Delta 的 evidence_refs）；带 value 不带 resolution → 写值，且**仅当该路径当前没有 ResolutionRecord 时**新建 `user_specified`，已有记录原样保留（Reducer 不隐式改写授权语义）；纯 resolution → 只写 Resolution，不动值。
- **CLEAR**：清该路径的值 + 移除 `resolutions[path]`（**不**写 `user_delegated`；清除后该路径回到 missing 语义）。
- **PIN**：只把路径加入 `pinned_paths`，不改值、不动 resolutions。
- **UNPIN**：只从 `pinned_paths` 移除，不改值、不写 Resolution、不授权重新设计。
- 未出现在 Delta 中的字段逐值保持；输入对象（Intent / deltas / resolutions / pinned_paths）完全不被原地修改（新 Facet、新 dict、新 frozenset、新 Intent）。
- **顺序语义**：按列表顺序依次应用，后面的 Delta 覆盖前面同路径的效果（last-write-wins），并有测试固定 `SET→SET`、`SET→CLEAR`、`CLEAR→SET`、`PIN→UNPIN`、`UNPIN→PIN`。
- **不创建 IntentRevision**（revision 组装与 `parent_revision_id` 属 Step 06）。
- 防御纵深：收到绕过 Validator 的 Delta（伪造类型 / 未知字段路径 / 未知 operation）抛 `ReducerError`（带 `.code`），绝不静默写入坏状态。

### 4. ChangeSummary 规则

- `changed_paths`：被接受 `SET` 的路径（含纯 resolution SET），按应用顺序去重；`cleared_paths`：被接受 `CLEAR` 的路径；`pinned_paths` / `unpinned_paths`：真正改变保持状态的路径。同一路径可同时出现在 `changed_paths` 与 `cleared_paths`（如 `SET→CLEAR`）。
- `confirmation_invalidated`：**保守规则**——只要 `changed_paths`/`cleared_paths`/`pinned_paths`/`unpinned_paths` 任一非空即为 `True`（重要视觉字段 SET/CLEAR 必须失效；PIN/UNPIN 按架构冻结也失效）。空 Delta 列表 → 全空 + `False`。
- 被拒 Delta 不会以任何形式进入 summary。

### 5. 测试（`tests/validation/`，684 个用例）

`tests/validation/conftest.py` 提供确定性工厂（`full_intent`、`intent_with`、`with_resolution`、`with_pinned`、`set_delta`/`clear_delta`/`pin_delta`/`unpin_delta`、`context`、`path_snapshot`/`non_target_snapshot`），全部使用字面量 ID（不调用 `new_id()`），保证可复现。

| 文件 | 用例数 | 内容 |
|---|---|---|
| `test_validation_validator.py` | 349 | 7 项检查 + 无效果检查 + 输出契约（表驱动，12 条路径 × 4 种 operation 全覆盖） |
| `test_validation_reducer.py` | 113 | 四种 operation 的语义 + 12 条路径逐条属性断言 + 前置条件失败 `ReducerError` |
| `test_validation_scenarios.py` | 123 | 任务书"必测场景"逐条映射 + 全部 5 条状态不变量 |
| `test_validation_change_summary.py` | 60 | 五字段精确行为 + 保守失效规则（12 条路径 × SET/CLEAR/PIN/UNPIN） |
| `test_validation_models.py` | 23 | 模型形状、frozen/extra、默认列表不共享 |
| `test_validation_public_api.py` | 16 | 再导出、无 LLM/DB/网络依赖（干净子进程验证）、不预实现 Step 03+ |

任务书「必测场景」→ 测试映射：

- SET：`test_set_composition_framing_keeps_every_other_field`（改 `composition.framing` 后 11 条非目标路径逐值不变）；`test_illegal_path_rejects_the_whole_delta`（非法路径整条拒绝、原样返回、状态零变化）；`test_llm_candidate_without_evidence_is_rejected`。
- CLEAR：`test_clear_only_clears_the_target_field_and_its_resolution`（12 参数）；`test_clear_is_not_interpreted_as_delegated`；`test_clearing_a_pinned_path_is_rejected_with_an_issue`（12 参数）。
- PIN/UNPIN：`test_pin_keeps_values_unchanged`、`test_unpin_keeps_values_unchanged`、`test_pin_of_a_path_without_a_value_is_rejected_with_an_issue`、`test_unpin_of_an_unpinned_path_produces_no_hidden_modification`（各 12 参数）。
- 不变量：`test_original_object_is_completely_unchanged_after_reduce`；`test_delta_order_is_observable_and_last_write_wins`（+ SET/CLEAR、PIN/UNPIN 顺序）；`test_non_target_paths_are_deep_equal_for_a_single_set`（12 参数，覆盖全部七类 Facet）+ `test_non_target_facets_are_deep_equal`；`test_important_change_invalidates_the_old_confirmation` / `test_clear_invalidates...` / `test_pin_and_unpin_invalidate...`；`test_replaying_the_same_revision_is_reproducible`。

## 变更文件

新增（**未修改任何只读或既有文件**）：

- `visual_intent_agent/validation/__init__.py`（原为空文件，现再导出公开面）
- `visual_intent_agent/validation/models.py`
- `visual_intent_agent/validation/validator.py`
- `visual_intent_agent/validation/reducer.py`
- `tests/validation/conftest.py`
- `tests/validation/test_validation_models.py`
- `tests/validation/test_validation_validator.py`
- `tests/validation/test_validation_reducer.py`
- `tests/validation/test_validation_change_summary.py`
- `tests/validation/test_validation_scenarios.py`
- `tests/validation/test_validation_public_api.py`
- `docs/handoffs/step_02_handoff.md`（本文件）

未触碰：MVP v0.2 任务书、任务索引与 Agent 派发提示词、`api.md`、`docs/ARCHITECTURE.md`、`visual_intent_agent/domain/**`、`visual_intent_agent/config.py`、`tests/conftest.py`、`tests/test_sanity.py`、`tests/domain/**`、`tests/fixtures/**`、`pyproject.toml`、`uv.lock`、`.env`。**无新增依赖**（仍只有 pydantic / httpx / pytest；validation 只 import pydantic 与 domain）。

## 公开接口

后续步骤统一从此处导入（也可按模块完整路径导入）：

```python
from visual_intent_agent.validation import (
    validate, reduce,
    ValidationResult, ChangeSummary, ReduceResult,
    EvidenceContext, RejectedDelta,
    ReducerError,   # 程序级失败（非业务 issue），带 .code
)
```

| 公开名 | 冻结签名 / 形状 |
|---|---|
| `validate` | `validate(candidate_deltas: list[IntentDelta], evidence_context: EvidenceContext, current_intent: VisualIntent) -> ValidationResult` |
| `reduce` | `reduce(current_intent: VisualIntent, validated_deltas: list[IntentDelta]) -> ReduceResult` |
| `ValidationResult` | `accepted: list[IntentDelta]`；`rejected: list[RejectedDelta]`；`issues: list[Issue]` |
| `RejectedDelta` | `delta: IntentDelta`；`issues: list[Issue]` |
| `EvidenceContext` | `available_message_ids: frozenset[str]`；`pending_question_id: str\|None=None`；`pending_question_path: str\|None=None` |
| `ChangeSummary` | `changed_paths`；`pinned_paths`；`unpinned_paths`；`cleared_paths`；`confirmation_invalidated` |
| `ReduceResult` | `intent: VisualIntent`；`change_summary: ChangeSummary` |
| `ReducerError` | `Exception` 子类，构造 `ReducerError(code, message)`，`.code` ∈ `validation.*`；仅用于"绕过 Validator 的调用错误" |

`validation/__init__.py::__all__` 恰好为上述 8 个名字。`validator.py` 另外公开（模块级常量，便于测试与调用方比对）：`DELTA_OPERATIONS` 与全部 issue code 常量。

### 本步新增 Issue code（命名空间 `validation.*`，全部 `severity=error`）

| code | 触发条件 |
|---|---|
| `validation.operation_invalid` | operation 不属于 SET/CLEAR/PIN/UNPIN（模型层已拦，防御纵深） |
| `validation.path_not_whitelisted` | path ∉ `INTENT_PATHS` |
| `validation.value_type_mismatch` | value 与路径类型不兼容（`subject.count` 非正整数 int；其余非 str） |
| `validation.evidence_missing` | `evidence_refs` 为空 |
| `validation.evidence_message_not_available` | `message_id` ∉ `available_message_ids` |
| `validation.evidence_pending_question_unknown` | 证据声明回答 pending question，但当前没有待答问题 |
| `validation.evidence_pending_question_mismatch` | 证据的 `pending_question_id` ≠ 当前待答问题 |
| `validation.evidence_pending_question_path_mismatch` | 回答待答问题的 Delta 指向了别的路径 |
| `validation.unauthorized_resolution_claim` | resolution 自报的授权强度缺少证据支持（见授权矩阵） |
| `validation.pin_requires_existing_value` | PIN 的路径当前没有值 |
| `validation.unpin_requires_pinned_path` | UNPIN 的路径当前未被 pinned |
| `validation.clear_requires_unpinned_path` | CLEAR 的路径当前被 pinned（需先 UNPIN） |
| `validation.no_state_effect` | Delta 不产生任何可观察效果（重复的纯 resolution SET；清空无值无记录路径） |

`ReducerError.code` 复用同一命名空间：`validation.invalid_delta_path` / `validation.value_type_mismatch` / `validation.operation_invalid`。

## 测试命令与结果

```text
$ cd /home/change/projects/image_system
$ uv run pytest -q
........................................................................ [ 51%]
........................................................................ [ 60%]
........................................................................ [ 69%]
........................................................................ [ 77%]
........................................................................ [ 86%]
........................................................................ [ 95%]
........................................                                 [100%]
832 passed in 0.96s

# 既有基线未回归（Step 01 + 骨架）
$ uv run pytest tests/domain tests/test_sanity.py -q
148 passed in 0.21s
```

新增 684 个用例（832 − 148），分布见上表。

```text
# 跨进程确定性：换 PYTHONHASHSEED 全绿
$ PYTHONHASHSEED=0      uv run pytest -q   → 832 passed
$ PYTHONHASHSEED=7      uv run pytest -q   → 832 passed
$ PYTHONHASHSEED=424242 uv run pytest -q   → 832 passed

# 跨进程 JSON 稳定性（Step 06 的 summary hash 依赖逐字节稳定）：
# reduce 输出（intent + change_summary）的 sha256 在三个 hash seed 下完全一致
intent         : ae17f77c0c6db390cab6e31473de81756efe14df7dff693b1ca1986cc0cdd672
change_summary : 2d396cf0682b492cbb23c855673aad944afb0ddd97d2508d6292759dfed8548d
```

零网络访问、零外部依赖（`test_validation_public_api.py` 用干净子进程断言 `visual_intent_agent.validation` 只加载 `domain` 与自身三个模块，不加载 `config`/`providers`/`httpx`/`sqlite3` 等）。源码/测试/handoff 中无明文 key（已按 `.env` 中 key 的前 8 字符全仓 grep，无命中）。

## 已知限制

1. **批次内不做中间状态模拟（最重要的顺序语义）**：3/4/7 项检查（尤其"PIN 需要有值""UNPIN 需要已 pinned""CLEAR 需要未 pinned"）一律对**本批次开始前的** `current_intent` 判定。因此同批次 `UNPIN → CLEAR` 里的 CLEAR 仍会因初始状态 pinned 被拒；跨请求分两步即可。Reducer 本身仍是严格的顺序应用（last-write-wins）。若 Step 03/05/06 需要同批次顺序状态推进，应作为一次显式的最小修订提出，本步不自行扩大语义。
2. **Reducer 不隐式升级已有授权语义**：值型 SET 未携带 `resolution` 时，只有"该路径当前没有 ResolutionRecord"才新建 `user_specified`；已有 `user_delegated` 记录原样保留。若产品要求"用户给出具体值即升级为 user_specified"，由 `Interpreter`/`IntentEngine` 在 Delta 中显式携带 `resolution=USER_SPECIFIED`（Validator 检查 5 会核验证据）。
3. **`changed_paths` 含纯 resolution 的 SET**：该字段定义为"被接受 SET 的路径"（含仅改变授权范围的 SET），不区分"值变了"与"授权变了"；`confirmation_invalidated` 对二者都为真。Step 09 `evaluate_carry` 若需区分，可自行比对前后 Intent 或提出最小修订。
4. **`EvidenceRef` 没有路径字段**：`user_delegated` 的范围只能靠 Pending Question 证据（`pending_question_id` + `pending_question_path`）证明；裸消息证据不足以证明"委派范围"。Step 05 构造 `EvidenceContext` 时必须带上 `pending_question_id/path`（架构已冻结 IntentEngine 的构造方式）。
5. **`_INT_VALUE_PATHS` 是一份"路径 → 类型"的第二处事实**（validation 内 validator 与 reducer 各有一份，内容相同）：`subject.count` 是 int、其余 11 条是 str。`domain` 层没有冻结"路径 → 类型"的公开映射，Step 03 若需要同一事实请从 Facet 模型推导或提出最小修订（建议上游在 `domain.paths` 增加该映射）。
6. **`frozen=True` 只阻止属性赋值**：`IntentDelta.evidence_refs` / `ValidationResult.accepted` 等 `list` 字段内容仍可被原地修改；Step 02 自身不原地改任何输入，也未引入深冻结类型（会改变 Step 01 冻结的 `list[...]` 字段类型）。
7. **`ReducerError` 是 Step 02 自定的程序级异常**，架构冻结表未列出；它不用于业务失败（业务失败一律是 `Issue`）。Step 05 若要捕获，可从 `visual_intent_agent.validation.reducer` 或包根导入。

## 对下一步的输入

**Step 03（DecisionPolicy）可直接使用：**

- `change_summary.confirmation_invalidated` 判断"本轮是否发生重要修改"；
- `ChangeSummary` 五字段用于确认页 diff-first 展示；
- 被拒 Delta 的 `Issue`（全部 `validation.*`、`severity=error`）可与 `policy.*` issue 合并进 `IntentResolution.issues`；
- `reduce(...).intent` 即"归并后的 Draft"，`assess` 的输入。

**Step 04（Repository 与状态机）：** 本步无持久化职责；`ChangeSummary.confirmation_invalidated` 是"旧确认失效"判定的唯一来源，Repository 只需按 `intent_revision_id` 判有效。

**Step 05（Interpreter 与 IntentEngine）编排方式（架构已冻结，本步可满足）：**

```python
context = EvidenceContext(
    available_message_ids=request.available_message_ids,
    pending_question_id=request.pending_question.question_id if request.pending_question else None,
    pending_question_path=request.pending_question.target_path if request.pending_question else None,
)
result = validate(interpreter_result.candidate_deltas, context, request.current_intent)
# 被拒 Delta 的 issues 保留（result.issues / result.rejected）
reduced = reduce(request.current_intent, result.accepted)
resolution = assess(reduced.intent).model_copy(update={"applied_deltas": result.accepted})
```

- 被拒时**不要**重试或"修复"路径；把 issue 交给 Workflow 决定澄清或报错；
- LLM 解析失败不产生 Delta，本步不参与；
- 调用 `validate` 前必须保证每条 Delta 带证据（含证据的 `message_id` 真的在该轮 `available_message_ids` 中）。

## 验收条件逐条核对

| # | 任务书验收条件 | 核对结果 |
|---|---|---|
| 1 | State Correctness 相关测试 100% 通过 | 是：`uv run pytest -q` → 832 passed（新增 684 + 既有 148），零网络访问 |
| 2 | Validator 和 Reducer 均无 LLM、数据库或网络依赖 | 是：两个模块只 import `pydantic` 与 `visual_intent_agent.domain`；`test_validation_public_api.py` 在干净子进程断言无 `httpx`/`requests`/`sqlite3`/`openai`/`PIL`/`numpy`/`config`/`providers`/`policy`；另有源码级禁用名检查 |
| 3 | 非目标字段保持测试覆盖七类 Facet | 是：`test_non_target_paths_are_deep_equal_for_a_single_set`（12 条路径参数化，逐值比较）+ `test_non_target_facets_are_deep_equal`（七类 Facet 逐个 `==`）+ reducer/clear 场景中的 12 参数化非目标快照断言 |
| 4 | 所有拒绝均有可观察 issue | 是：`test_every_rejected_delta_carries_at_least_one_error_issue` 断言每个 `RejectedDelta.issues` 非空、全为 `severity=error`、code ∈ `validation.*`；`rejected`/`issues` 由 `ValidationResult` 显式区分 |
| 5 | 无静默修复或隐式字段填充 | 是：被拒 Delta 原样返回（`test_rejected_path_is_never_rewritten_by_the_validator`）；未指定字段逐值保持；PIN 不创建值（无值即拒绝）；CLEAR 不写 delegated；reducer 对伪造 Delta 抛 `ReducerError` 而非猜测修复；no-op Delta 显式拒绝 |

## 是否满足验收条件

是。5 条验收条件逐条通过；7 项检查与 8 项 Reducer 保证全部落地并有测试；未实现 DecisionPolicy / LLM / 持久化 / 澄清问题 / Prompt 编译；未修改任何只读或既有文件；未新增依赖；源码、测试、fixture 与本文档中无明文 key。
