# Step 03 交接记录：DecisionPolicy 与 IntentResolution

任务：Step 03 — DecisionPolicy 与 IntentResolution（九项 Decision 的数据驱动规则表、确定性 evaluator、缺失/冲突/优先级/ready_for_confirmation、golden tests）

## 完成内容

### 1. `visual_intent_agent/policy/models.py` — 六个公开数据模型（frozen + extra="forbid"）

| 模型 | 字段（冻结面） |
|---|---|
| `Materiality`（str 枚举） | `core` / `perceptual` / `implementation` |
| `PolicyAction`（str 枚举） | `block` / `omit` / `runtime` |
| `DecisionPolicy` | `path`；`materiality`；`required_if: str`；`delegatable: bool`；`dependencies: list[str]`；`conflict_rules: list[str]`；`invalidates_realization: bool`（恰好七字段） |
| `UnresolvedDecision` | `path`；`materiality`；`action`；`reason` |
| `QuestionSpec` | `target_path`；`reason`；`allow_delegate`；`allow_custom=True`；`suggested_values: tuple[str,...]=()`；`question_text: str\|None=None`；`question_id: str\|None=None` |
| `IntentResolution` | `intent`；`applied_deltas: list[IntentDelta]=[]`；`issues: list[Issue]=[]`；`unresolved_decisions: list[UnresolvedDecision]=[]`；`conflicts: list[Issue]=[]`；`question: QuestionSpec\|None=None`；`ready_for_confirmation=False` |

- 默认空列表一律 `Field(default_factory=list)`（`frozen` 不阻止 list 内容原地修改，与 Step 01/02 同约定）。
- `QuestionSpec.question_text` / `question_id` 在 Step 03 产生时恒为 `None`（由 Step 06 的 QuestionBuilder 填充）。

### 2. `visual_intent_agent/policy/decision_policy.py` — 规则表 + evaluator

- `POLICY_VERSION = "policy.v1"`。
- `DECISION_POLICIES` 恰好 **9 条**，全部是代码内常量（不引入 YAML / 配置文件）。
- `REQUIRED_IF_CONDITIONS` 是**封闭枚举**；import 时校验：9 条、`path`/`dependencies` ⊆ `INTENT_PATHS`、`required_if` ∈ 枚举、`conflict_rules` 已在 `CONFLICT_RULES` 注册、成员路径不被两个 Decision 覆盖（违反即 `ValueError`，import 失败）。
- `assess(intent, execution_context: ExecutionRevision | None = None) -> IntentResolution`：纯确定性，`applied_deltas` 恒为 `[]`，绝不修改传入 Intent。
- 冲突规则表 `CONFLICT_RULES: dict[rule_id, _ConflictRule]`：rule id → 类别（hard / execution）+ 涉及路径 + 确定性谓词；`DecisionPolicy.conflict_rules` 声明哪些决策参与哪些规则，求值时按规则表顺序去重（同一条规则只报一次）。
- Issue code 命名空间 `policy.*`（见「公开接口」）。

### 3. 九项 Decision 规则表（逐值）

| # | Decision | `path` | `materiality` | `required_if` | `delegatable` | `dependencies` | `conflict_rules` | `invalidates_realization` |
|---|---|---|---|---|---|---|---|---|
| 1 | Primary Subject | `subject.description` | core | `always` | **False** | `[]` | `[]` | True |
| 2 | Style | `style.primary` | core | `always` | True | `[]` | `hard_conflict.style_medium_mismatch` | True |
| 3 | Environment | `environment.mode` | core | `always` | True | `[environment.location]` | `hard_conflict.environment_mode_location`, `hard_conflict.lighting_environment_source` | True |
| 4 | Framing | `composition.framing` | perceptual | `when_subject_specified` | True | `[]` | `execution_conflict.framing_aspect_mismatch` | True |
| 5 | Pose / Action | `subject.pose_action` | perceptual | `when_subject_specified` | True | `[]` | `[]` | True |
| 6 | Lighting | `lighting.character` | perceptual | `when_environment_specified` | True | `[]` | `hard_conflict.lighting_environment_source` | True |
| 7 | Camera Angle | `camera.angle` | perceptual | `when_camera_specified` | True | `[]` | `[]` | True |
| 8 | Color | `color.palette` | perceptual | `never` | True | `[]` | `[]` | True |
| 9 | Depth of Field | `camera.depth_of_field` | perceptual | `when_camera_specified` | True | `[]` | `[]` | True |

**Decision 成员语义**：成员路径 = `(path, *dependencies)`。一个 Decision 只要有一个成员未解决即算未解决；`UnresolvedDecision.path` 取按声明顺序**第一个**未解决的成员路径（即下一待询问目标）。Environment 由 `environment.mode` + `environment.location` 共同构成（任务书派发：Environment → mode + location）。

### 4. `required_if` 封闭枚举集（逐值语义）

| 取值 | 求值（确定性，只读 Intent） | 出现在 |
|---|---|---|
| `always` | 恒 True | `subject.description`、`style.primary`、`environment.mode` |
| `never` | 恒 False | `color.palette` |
| `when_subject_specified` | `subject.description` 有值 | `composition.framing`、`subject.pose_action` |
| `when_environment_specified` | `environment.mode` 或 `environment.location` 有值 | `lighting.character` |
| `when_camera_specified` | `camera.angle` 或 `camera.depth_of_field` 有值 | `camera.angle`、`camera.depth_of_field` |

`required_if` 不得是自由表达式：唯一合法值是上述 5 个字符串；未知值在 import 校验或求值时报 `ValueError`（程序级错误）。

**策略结果**：未解决 + 条件成立 → `block`；未解决 + 条件不成立 → `omit`；已解决 → 不进入 `unresolved_decisions`。`omit` 只表示"允许 unspecified"，不授权 PromptEngine 具体化。

### 5. 冲突规则（每条稳定 rule id）

| rule id | 类别 | 声明于 | `Issue.path` | 确定性谓词 | 词表（闭集，大小写无关、整词/短语匹配） |
|---|---|---|---|---|---|
| `hard_conflict.environment_mode_location` | Hard Conflict | `environment.mode` | `environment.mode` | 封闭模式 × 户外地点 | mode ∈ {studio, indoor, indoors, interior} 且 location 命中 {outdoor, outdoors, street, forest, beach, mountain, field, garden, desert, ocean, sea, sky, park, rooftop} |
| `hard_conflict.lighting_environment_source` | Hard Conflict | `environment.mode` + `lighting.character` | `lighting.character` | 自然光 × 封闭环境 | mode ∈ 封闭模式 且 lighting.character 命中 {natural light, natural lighting, sunlight, daylight, golden hour} |
| `hard_conflict.style_medium_mismatch` | Hard Conflict | `style.primary` | `style.primary` | 写实摄影主值 × 非摄影媒介补充说明 | style.primary 命中 {photorealistic, photorealism, photograph, photography, hyperrealistic} 且 style.description 命中 {anime, cartoon, oil painting, watercolor, watercolour, sketch, pencil drawing, 3d render, pixel art, comic} |
| `execution_conflict.framing_aspect_mismatch` | Execution Conflict | `composition.framing` | `composition.framing` | 宽幅构图 × 输出比例不足 | framing 命中 {wide, wide shot, wide angle, panorama, panoramic, landscape, establishing shot, long shot, full shot} 且 `execution_context` 非空、`output_size` 可解析为 `<w>x<h>` 且 `w/h < 1.5` |

- 冲突以 `Issue` 表达：Hard Conflict code = `policy.hard_conflict.<rule>`，Execution Conflict code = `policy.execution_conflict.<rule>`；`severity` 默认 `error`。
- 冲突**只显式输出**，绝不覆盖或抹掉任何字段来消解（冲突双方的字段值保持原样）。
- `is_hard_conflict` / `is_execution_conflict` 按 code 前缀分类，供 evaluator 与调用方使用。
- 输出顺序固定：先按 `DECISION_POLICIES` 顺序、再按该决策 `conflict_rules` 顺序，去重求值。

### 6. Resolution 合法性检查（对 `intent.resolutions` 的每一条记录）

| Resolution | 已解决的规则条件 | 违反时 issue |
|---|---|---|
| `user_delegated` | 所属 Decision 的 `delegatable=True`（由委托本身解决）；无规则条目的补充路径不算授权 | `policy.delegation_not_allowed` |
| `not_applicable` | 所属 Decision 的 `required_if` **不成立**（即该决策当前不适用）；无规则条目同理不成立 | `policy.not_applicable_not_allowed` |
| `user_specified` / `user_confirmed_proposal` | 该 path 确有具体值 | `policy.resolution_requires_value` |

- **每个** ResolutionRecord 都被验证（含 `subject.count`、`style.description` 两条补充路径），不存在被静默跳过的记录。
- 非法委托/不适用**不会**抹掉已有具体值：若该 path 已有值（且是值型 resolution），路径仍算已解决，issue 照常输出。
- `not_applicable` 即使在 Step 02 语义下携带"理由值"，只要规则条件成立就仍视为**未解决**（不允许"不适用"掩盖缺失）。

### 7. 判定优先级与 ready

- 优先级（冻结）：`policy.hard_conflict` > core missing > perceptual missing > `policy.execution_conflict`；每轮最多产生**一个** `QuestionSpec`。
- `ready_for_confirmation` = 无 `action=block` 的未解决决策 **且** 无 Hard Conflict。Execution Conflict 只报告、不阻塞。
- `QuestionSpec`：`target_path` = 冲突路径或缺失决策路径；`reason` = 对应确定性原因/冲突说明；`allow_delegate` = 该 path 所属 Decision 的 `delegatable`；`allow_custom=True`；`suggested_values` 来自决策维度的静态选项表（`subject.description` 与 `environment.location` 为开放式输入，故意不给预设值）；`question_text` / `question_id` 留空。

### 8. 测试与 fixture

- `tests/policy/` 共 **240** 个用例；`tests/fixtures/policy_cases/` 9 个 golden JSON（版本化，含完整预期 `IntentResolution`）。
- golden fixture：`empty_intent`、`subject_only_core_missing`、`delegated_delegatable_resolves`、`delegated_not_delegatable_issue`、`not_applicable_invalid_issue`、`omit_not_blocking`、`hard_conflict_over_missing`、`ready_for_confirmation`、`execution_conflict_non_blocking`。

## 变更文件

新增（**未修改任何只读或既有文件**）：

- `visual_intent_agent/policy/models.py`
- `visual_intent_agent/policy/decision_policy.py`
- `visual_intent_agent/policy/__init__.py`（原为空文件，现再导出公开面）
- `tests/policy/conftest.py`
- `tests/policy/test_policy_models.py`、`test_policy_rules.py`、`test_policy_resolution_legality.py`、`test_policy_conflicts.py`、`test_policy_question_selection.py`、`test_policy_scenarios.py`、`test_policy_determinism.py`、`test_policy_golden.py`、`test_policy_public_api.py`
- `tests/fixtures/policy_cases/`：上述 9 个 JSON
- `docs/handoffs/step_03_handoff.md`（本文件）

未触碰：根目录任务书 md、`README.md`、`AGENT_DISPATCH_PROMPTS.md`、`api.md`、`docs/ARCHITECTURE.md`、`visual_intent_agent/domain/**`、`config.py`、`validation/**`、`tests/conftest.py`、`tests/test_sanity.py`、`tests/domain/**`、`tests/validation/**`、`tests/fixtures/schema_v1/**`、`pyproject.toml`、`uv.lock`、`.env`。**无新增依赖**（policy 只 import `pydantic` 与 `visual_intent_agent.domain`）。

## 公开接口

```python
from visual_intent_agent.policy import (
    assess,                 # assess(intent, execution_context: ExecutionRevision | None = None) -> IntentResolution
    IntentResolution,
    QuestionSpec,
    DecisionPolicy,
    Materiality,
    PolicyAction,
    UnresolvedDecision,
    DECISION_POLICIES,      # tuple[DecisionPolicy, ...]，恰好 9 条
    POLICY_VERSION,         # "policy.v1"
)
```

`policy/__init__.py::__all__` 恰好为上述 9 个名字。规则表细节从模块路径访问（不扩大包根公开面）：

```python
from visual_intent_agent.policy.decision_policy import (
    REQUIRED_IF_CONDITIONS,          # frozenset[str]，5 个封闭条件名
    REQUIRED_IF_ALWAYS, REQUIRED_IF_NEVER,
    REQUIRED_IF_SUBJECT_SPECIFIED, REQUIRED_IF_ENVIRONMENT_SPECIFIED, REQUIRED_IF_CAMERA_SPECIFIED,
    CONFLICT_RULES,                  # dict[rule_id, _ConflictRule]
    DELEGATION_NOT_ALLOWED,          # "policy.delegation_not_allowed"
    NOT_APPLICABLE_NOT_ALLOWED,      # "policy.not_applicable_not_allowed"
    RESOLUTION_REQUIRES_VALUE,       # "policy.resolution_requires_value"
    HARD_CONFLICT_PREFIX,            # "policy.hard_conflict"
    EXECUTION_CONFLICT_PREFIX,       # "policy.execution_conflict"
    required_if_holds, is_hard_conflict, is_execution_conflict,
)
```

### 本步新增 Issue code（命名空间 `policy.*`）

| code | 触发条件 |
|---|---|
| `policy.delegation_not_allowed` | `user_delegated` 落在 `delegatable=False` 的路径，或没有规则条目的补充路径 |
| `policy.not_applicable_not_allowed` | `not_applicable` 落在规则条件当前成立的路径，或没有规则条目的补充路径 |
| `policy.resolution_requires_value` | `user_specified` / `user_confirmed_proposal` 所在路径没有具体值 |
| `policy.hard_conflict.environment_mode_location` | 见「冲突规则」 |
| `policy.hard_conflict.lighting_environment_source` | 同上 |
| `policy.hard_conflict.style_medium_mismatch` | 同上 |
| `policy.execution_conflict.framing_aspect_mismatch` | 同上 |

> code 形态说明：ARCHITECTURE.md 5.5 的 `<area>.<name>` 为命名约定；冲突需要**每条规则一个可区分的稳定 code**，故采用 `<area>.<class>.<rule>`（class = `hard_conflict` / `execution_conflict`，与冻结的优先级 token `policy.hard_conflict` / `policy.execution_conflict` 前缀一致）。分类一律用前缀判断，不使用精确相等。

## 测试命令与结果

```text
$ cd /home/change/projects/image_system
$ uv run pytest -q
................................................................         [100%]
1072 passed in 2.24s

# 既有基线未回归（Step 00～02）
$ uv run pytest tests/domain tests/validation tests/test_sanity.py -q
832 passed in 0.84s

$ uv run pytest tests/policy -q
240 passed in 1.43s

# 跨进程确定性：三个 PYTHONHASHSEED 下全量测试均绿
$ PYTHONHASHSEED=0      uv run pytest -q   -> 1072 passed
$ PYTHONHASHSEED=7      uv run pytest -q   -> 1072 passed
$ PYTHONHASHSEED=424242 uv run pytest -q   -> 1072 passed

# 跨进程 JSON 稳定性（assess(hard_conflict_over_missing) 的 model_dump_json sha256）
seed=0 / 7 / 424242 均为
9ce9790c54d127516ba3eec0db3e733e55f15d933638113c2ea0ec5b17e2476d
```

新增 240 个用例（1072 − 832），分布：`test_policy_resolution_legality.py` 75、`test_policy_conflicts.py` 40、`test_policy_golden.py` 38、`test_policy_rules.py` 18、`test_policy_models.py` 17、`test_policy_public_api.py` 16、`test_policy_question_selection.py` 16、`test_policy_scenarios.py` 12、`test_policy_determinism.py` 8。

零网络访问、零外部依赖：`test_policy_public_api.py` 在干净子进程断言 `visual_intent_agent.policy` 只加载 `domain` 与自身 3 个模块，不加载 `config`/`providers`/`validation`/`persistence`/`prompt_engine`/`httpx`/`sqlite3`/`openai` 等；另断言 policy 源码中无 `random`/`uuid`/`time.time`/`datetime.now`/`open(`/`api_key`/`sk-`。

### 任务书「必测场景」→ 测试映射（9/9）

1. 空 Intent 不可 Ready → `test_policy_scenarios.py::test_scenario_1_empty_intent_is_never_ready`；
2. 只有 subject 但核心必需项缺失不可 Ready → `::test_scenario_2_subject_alone_leaves_core_decisions_missing`；
3. delegated 且可委托视为已解决 → `::test_scenario_3_delegation_on_a_delegatable_path_resolves` + `test_policy_resolution_legality.py::test_user_delegated_on_a_delegatable_path_is_resolved`（9 条可委托路径参数化）；
4. 不可委托字段被 delegated 返回 issue → `::test_scenario_4_delegation_on_a_non_delegatable_path_returns_an_issue` + `::test_user_delegated_on_the_primary_subject_is_rejected`；
5. not_applicable 不满足条件返回 issue → `::test_scenario_5_not_applicable_without_a_matching_rule_condition_returns_an_issue` + `::test_not_applicable_on_a_contextual_path_flips_with_the_condition`；
6. omit 不阻塞但保持 unspecified → `::test_scenario_6_omitted_fields_do_not_block_and_stay_unspecified`；
7. Hard Conflict 优先于 Missing → `::test_scenario_7_hard_conflict_outranks_missing_decisions` + `test_policy_question_selection.py::test_hard_conflict_outranks_core_missing_for_the_question`；
8. 所有 block 项解决且无冲突时 Ready → `::test_scenario_8_ready_when_every_block_is_resolved_and_no_conflict`；
9. 结果不依赖 LLM/随机数 → `::test_scenario_9_results_do_not_depend_on_llm_or_randomness`、`::test_scenario_9_dict_insertion_order_does_not_change_the_result`、`test_policy_determinism.py`（含 3 个 hash seed 的子进程 digest 一致）。

Golden 案例驱动 `IntentResolution` **全字段**断言：`test_policy_golden.py::test_golden_case_matches_the_full_intent_resolution`（9 个案例参数化）。

## 已知限制

1. **Environment 含两条路径**：按派发映射把 `environment.mode` 与 `environment.location` 都作为 Environment 的必需成员（`dependencies`），二者都未解决时先问 mode、再问 location。这是对「Environment→mode+location」的实现选择，已在规则表写明。
2. **补充路径（`subject.count`、`style.description`）没有规则条目**：它们永不阻塞、值随 Intent 保留（满足"不单独阻塞、不新增维度"），但**不具备**委托/不适用授权——对它们写 `user_delegated` / `not_applicable` 会得到显式 issue（无 `delegatable=True` 规则）。若产品需要允许"数量/风格细节随便你定"，属于一次显式规则扩展（例如为补充路径引入可选的 `delegatable` 元数据），本步不自行扩大。**最小修订建议**：由架构方在 `domain` 或 policy 规则表中冻结"补充路径"的授权口径。
3. **`runtime` / `implementation` 未被任何条目使用**：9 个 Decision 都是视觉决策且都在 12 条 Intent 路径上；实现参数（token 顺序、sampling）不在白名单内，因此 policy v1 不产生 `runtime` 动作。枚举值保留给未来实现型决策。
4. **Execution Conflict 不阻塞、也（在 v1）不会成为问题**：`ready_for_confirmation` 只由 block 项与 Hard Conflict 决定，而 `ready=False` 必然来自 block 或 Hard Conflict（二者优先级都高于 Execution Conflict），因此 `_select_question` 中的 execution 分支逻辑上不可达；Execution Conflict 只出现在 `conflicts` 中供调用方展示。优先级槽位已按冻结顺序接好，留待未来语义变化。
5. **`when_*_specified` 只看具体值**：条件读的是 Facet 值，不读 ResolutionRecord。因此 `camera.angle` 被 `user_delegated` 不会让 `camera.depth_of_field` 变为 required（"委托"不等于"具体化"）。
6. **冲突规则是闭集词表匹配**：大小写无关、整词/短语匹配，词表外自由文本不触发冲突；不是知识库、不做语义相似度、结果确定。扩展词表 = 一次 policy 版本变更（`POLICY_VERSION`）。
7. **`assess` 不读取 `pinned_paths`**：PIN/UNPIN 的影响（确认失效/Realization 失效）由 Step 02 的 `ChangeSummary` 与 Step 09 的 `evaluate_carry` 负责。
8. **`assess` 不合并 Step 02 的 `validation.*` issues**：`IntentResolution.issues` 只含 `policy.*`；Step 05 IntentEngine 负责把被拒 Delta 的 issues 一并放进 `issues`（Step 02 handoff 已冻结编排）。
9. **`suggested_values` 是静态选项词表**（每维度 2～3 个通用 token），不是知识库；`subject.description` 与 `environment.location` 为开放式输入，返回空元组。Step 06 的 QuestionBuilder 必须原样使用（架构冻结）。
10. **`not_applicable` 的"规则条件"用 `required_if` 表达**：`DecisionPolicy` 七字段中没有独立的 `applicable_if`，故"不适用是否成立"等价于"该决策当前是否 required"。若未来需要二者分离，需要架构方扩展字段。
11. **冲突 code 为多段**（`policy.hard_conflict.<rule>`）：见「公开接口」的 code 形态说明；分类只用前缀，冻结的 `policy.hard_conflict` / `policy.execution_conflict` 仍是类级别口径。
12. **`frozen=True` 只阻止属性赋值**：`IntentResolution.conflicts` 等 list 内容仍可被原地修改；policy 自身不原地改任何对象（有测试断言输入不变）。

## 对下一步的输入

**Step 05（Interpreter 与 IntentEngine）** — 直接使用（与 Step 02 handoff 的编排一致）：

```python
from visual_intent_agent.policy import assess

resolution = assess(reduced.intent, execution_context)          # applied_deltas == []
resolution = resolution.model_copy(update={"applied_deltas": result.accepted})
# issues 合并：list(result.issues) + list(resolution.issues)
# question 非空 -> Workflow 生成 PendingQuestion(spec=resolution.question)
# ready_for_confirmation -> WAITING_CONFIRMATION
```

- `question.question_text` / `question_id` 为空；`target_path` / `allow_delegate` / `suggested_values` 必须原样传给 QuestionBuilder（`allow_delegate=False` 的路径不得展示"交给你决定"）。
- LLM 失败时不要伪造 Delta；`assess` 只对当前（未变的）Intent 判定。

**Step 04（Repository 与状态机）**：本步无持久化职责、无新依赖；`IntentResolution` 不是持久化合同。

**Step 06（澄清、确认与 Workflow）**：`QuestionSpec` 即 PendingQuestion 的构造输入；每次 Resolution 至多一个 question；`ready_for_confirmation=True` 时 `question is None`。

**Step 09（FeedbackEngine / evaluate_carry）**：`DECISION_POLICIES` 与 `invalidates_realization` 是本步交付；`dependencies` 表达同一 Decision 的成员路径（含 `environment.location`），可作为 carry 失效的判定输入。

## 验收条件逐条核对

| # | 任务书验收条件 | 核对结果 |
|---|---|---|
| 1 | 九项 Decision 均有明确规则 | 是：`DECISION_POLICIES` 恰好 9 条且逐值列出（Decision / path / materiality / required_if / delegatable / dependencies / conflict_rules / invalidates_realization）；`test_policy_rules.py` 钉住九项映射与路径集合 |
| 2 | 每个 block 都能说明 path 和原因 | 是：`UnresolvedDecision` 带 `path` + `action` + `reason`（含规则条件与具体原因）；`test_every_block_decision_explains_its_path_and_rule` |
| 3 | 每个 delegated / not_applicable 均经过规则验证 | 是：`_resolution_issues` 遍历 `intent.resolutions` 的**每一条**记录（按路径排序），含补充路径；`test_every_resolution_record_is_rule_checked`（12 路径全带 delegated）与 75 个合法性用例 |
| 4 | `ready_for_confirmation` 完全由确定性代码产生 | 是：`ready = (无 block 项) and (无 Hard Conflict)`，只在 `assess` 内计算；`test_ready_flag_is_derived_only_from_block_decisions_and_hard_conflicts` + 跨进程 hash seed digest 一致 |
| 5 | 无 LLM、数据库或 Prompt 代码依赖 | 是：policy 只 import `pydantic` 与 `domain`；干净子进程断言无 httpx/sqlite3/openai/config/providers/validation/persistence/prompt_engine；源码级禁用名检查（random/uuid/time.time/datetime.now/open(/api_key/sk-） |

## 是否满足验收条件

是。5 条验收条件逐条通过；9 条「必测场景」全部有测试映射，golden 案例驱动 `IntentResolution` 全字段断言，`uv run pytest -q` → **1072 passed**（既有 832 全数保留 + 新增 240），三个 `PYTHONHASHSEED` 下全绿且 `assess` 输出逐字节一致；未实现任何后续步骤能力（无 Interpreter / PromptEngine / Repository / Workflow / 生成 / 澄清文案），未修改任何只读或既有文件，未新增依赖，源码/测试/fixture/本文档中无明文 key。
