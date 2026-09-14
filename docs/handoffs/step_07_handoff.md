# Step 07 交接记录：PromptEngine 与 PromptArtifact

任务：Step 07 — PromptEngine 与 PromptArtifact（从**已确认** Intent、ExecutionContext 与合法
Realization 编译目标模型 Prompt，保存可完整追溯的 PromptArtifact；实现 Source Binding 与
unauthorized addition 检查、确定性 delegated 局部实现与稳定复用、唯一目标模型 Renderer；
**不调用图像 Provider、不接 RAG、不引入 Prompt AST、不实现 Step 09 的 carry/失效逻辑**）

- 依据：`07_prompt_engine.md`（任务书）、`README.md`（10 条全局不变量与统一交接格式）、
  `docs/ARCHITECTURE.md` 第 4 节「Step 07 — prompt_engine + realization/models」冻结表 /
  5.1 ID 前缀 / 5.3 schema_version / 5.5 code 命名空间 / 5.7 不可变模型 / 第 7 节测试策略（Rev.1）、
  `docs/handoffs/step_01/04/06_handoff.md`、`step_04_patch_001.md`、`architecture_decision_001.md`。
- 范围纪律：只新增 `visual_intent_agent/realization/models.py`、`visual_intent_agent/prompt_engine/**`、
  `tests/prompt_engine/**`、`tests/realization/**` 与本文件；**未修改任何上游或既有文件**
  （`domain/`、`validation/`、`policy/`、`persistence/`、`intent_engine/`、`providers/`、`workflow/`、
  `config.py`、`pyproject.toml`、`uv.lock`、`.env`、根目录任务书、`README.md`、`docs/ARCHITECTURE.md`）；
  `realization/__init__.py` 按架构冻结**保持空白**（见「已知限制 1」）；**无新增依赖**
  （只用 pydantic + stdlib 的 `hashlib` / `dataclasses` / `typing`）。

---

## 完成内容

### 1. `realization/models.py` — RealizationValue / RealizationState（实现步骤：模型与读写字段）

| 公开名 | 字段（与冻结表逐字一致） |
|---|---|
| `RealizationValue` | `path`；`value`；`source: Literal["user_delegated"]`；`first_prompt_artifact_id`；`carry_policy: Literal["preserve_until_invalidated"]="preserve_until_invalidated"`；`status: Literal["active","invalidated"]="active"`；`invalidated_reason: str\|None=None`；`invalidated_at: datetime\|None=None`（tz-aware UTC，naive 拒绝、非 UTC 归一、JSON `+00:00`） |
| `RealizationState` | `schema_version`；`realization_id`；`session_id`；`based_on_intent_revision_id`；`values: list[RealizationValue]`；`created_at` |

- 两个便捷只读方法：`RealizationState.active_values()`、`active_value_for(path)`
  （只返回 `status == "active"` 的值；同路径多个 active 时取**第一个**，确定性）。
- **本步不实现任何 carry / 失效评估**：`carry_policy` / `status` / `invalidated_*` 只是承载字段，
  由 Step 09 的 `realization/carry.py::evaluate_carry` 写入；Step 07 只读 active 值并原样复用。
- 历史不覆盖：失效产生**新** `RealizationState`（新 `rlz` id），旧 state 保留可审计；
  模型 `frozen=True`，无原地修改路径（有 append-only 持久化测试）。

### 2. `prompt_engine/models.py` — 公开合同与失败类型

| 公开名 | 形状 |
|---|---|
| `SourceBinding` | `clause_id`；`text`；`source_kind: Literal["intent","delegation","realization","runtime"]`；`intent_path: str\|None`；`rule_id: str\|None`；`realization_id: str\|None` |
| `PromptParameters` | `size: str = "1024x1024"`（**唯一**参数面，无 seed / negative_prompt / style） |
| `PromptArtifact` | `schema_version`；`prompt_artifact_id`；`session_id`；`based_on_intent_revision_id`；`based_on_confirmation_id`；`target_model`；`prompt`；`parameters`；`source_bindings`；`realization_refs`；`created_at` |
| `PromptCompileRequest` | `session_id`；`confirmation_id`（不含任何 Prompt 文本 / Intent 对象） |
| `PromptCompilationError` | `.code` 仅允许四个冻结值（构造时自检，未知 code → `ValueError`） |
| 常量 | `SOURCE_KINDS` / 四个 `SOURCE_KIND_*` / `PROMPT_ERROR_CODES` / 四个 `PROMPT_*` code |

全部模型 `frozen=True, extra="forbid"`；`created_at` 与 domain 同约定（naive 拒绝、UTC 归一、
JSON `+00:00`）。核心合同无 `dict[str, Any]`。

### 3. `prompt_engine/spec.py` — CompilationSpec（**内部**，不导出）

- `CompilationClause{clause_id, text, intent_path, source_kind, rule_id, realization_id}` 与
  `CompilationSpec{session_id, target_model, parameters, clauses}`；
- 轻量、无层级、无权重、无分支——**不是 Prompt AST**；`prompt_engine/__init__.py` 不导出其任何名字；
- `CompilationClause.source_kind` 故意为自由字符串（不是 `Literal`）：这样"人为插入/被篡改的
  clause"能被表达出来并交给 `check_source_bindings` 判为 `prompt.unauthorized_addition` /
  `prompt.missing_source_binding`（编译失败，而不是构造期 pydantic 报错）。

### 4. `prompt_engine/renderer.py` — 唯一目标模型 Renderer

| 公开名 | 内容 |
|---|---|
| `ModelRenderer(Protocol)` | `target_model` / `renderer_version` 属性 + `supports(target_model, size)` + `render(spec)`；`runtime_checkable` |
| `QwenImageRenderer` | **唯一**具体 Renderer（`QWEN_IMAGE_MODEL = "qwen-image-3.0"`） |
| `RENDERER_VERSION` | `"qwen_image.v1"`（Prompt 组织方式版本，不是模型版本） |
| `parse_output_size(size)` | 解析 `"<w>x<h>"`，非法返回 None（不抛异常） |

- `render` 只把既有 clause 的 `text` 用 `", "` 连接：**不加任何前缀/后缀/质量或风格修饰词**
  （不出现 "masterpiece / best quality / 8k" 之类"最佳实践" token），因此渲染结果可被
  `source_bindings` 逐条覆盖（来源覆盖率 100%）；
- `supports` 只接受唯一模型名 + 可解析的正尺寸；其余由引擎转为 `prompt.unsupported_requirement`；
- 模块内只有一个 `ModelRenderer` Protocol 与一个具体实现，无第二个目标模型 Renderer。

### 5. `prompt_engine/engine.py` — PromptEngine.compile（严格按冻结流程）

`PromptEngine(renderer, repo).compile(PromptCompileRequest) -> PromptArtifact`：

1. `repo.get_current_session_snapshot` → `repo.is_confirmation_valid(confirmation_id)` **复核**
   （缺失 / 已失效 / 绑定非当前 revision / 不属于该会话 / 空 id → `prompt.no_valid_confirmation`）；
2. `get_confirmation` → `get_intent_revision` / `get_execution_revision`（Step 04 的两个只读 getter）；
   `target_model` / `output_size` 取自 ExecutionRevision；`renderer.supports(...)` 不通过 →
   `prompt.unsupported_requirement`；
3. 逐 clause 生成 `SourceBinding`（`check_source_bindings`）+ **覆盖率检查** `check_clause_coverage`；
4. delegated 路径局部实现：
   - 已有 active Realization → **原样复用**（`source_kind="realization"`，不重新选择，
     不改写 `first_prompt_artifact_id`）；
   - 尚无值 → 用固定候选表 + 稳定摘要规则选择（`source_kind="delegation"`），组装进**新**
     `RealizationState`（新 `rlz` id；carry 现有 active 值，历史不覆盖）；
   - 无任何新选择时**不写**新 state（纯复用不产生冗余历史）；
5. unauthorized addition 检查：任何重要 clause 无合法 binding → `prompt.unauthorized_addition`；
   已解决的每个重要路径都必须有 clause（静默丢弃 → `prompt.missing_source_binding`）；
6. 保存前**再次**复核 Confirmation 有效性；
7. `append_realization_state(new_id("rlz"), sid, {"based_on_intent_revision_id": irev_id}, payload)`
   （仅当有新选择）→ `append_prompt_artifact(new_id("pra"), sid,
   {"intent_revision_id": irev_id, "confirmation_id": cnf_id}, payload)` → 返回。

**Source Binding 合法性规则（`bind_clause`）**：

| clause 形态 | 结果 |
|---|---|
| `source_kind="intent"` + 白名单 `intent_path` | binding(intent)，`rule_id`/`realization_id` 必须为空 |
| `source_kind="delegation"` + `intent_path` + 非空 `realization_id` | binding(delegation) |
| `source_kind="realization"` + `intent_path` + 非空 `realization_id` | binding(realization) |
| `source_kind="runtime"` + 非空 `rule_id` | binding(runtime)（本步不产生，仅保留合法类别） |
| 声明了类别但缺少该类别强制字段 / 字段非法 | `prompt.missing_source_binding` |
| `source_kind` 缺失 / 非法（含人为插入的"无来源 clause"） | `prompt.unauthorized_addition` |

**确定性 delegated 局部实现**：`DELEGATED_CANDIDATES` 是 9 个可委托成员路径的固定候选表；
`select_delegated_value(path, seed)` 取 `index = sha256(f"{path}\x1f{seed}")[:8] % len(candidates)`，
`seed = intent_revision_id`——纯确定性、无随机数、无时钟、无 LLM；候选表缺失该路径时抛
`prompt.unsupported_requirement`（绝不由模型临时编造）。clause 顺序由 `CLAUSE_ORDER` 冻结
（import 时断言其集合恰等于 `INTENT_PATHS`，不是第二份白名单）。

**unspecified + omit 不具体化**：既无值、又无 `user_delegated` 记录的路径（含 `not_applicable`
与 policy `omit`）**不产生 clause、不产生 binding、不产生 Realization**；有值路径一律按值绑定
（即使同时存在 resolution 记录），delegated 只在无值时生效。

### 6. `prompt_engine/__init__.py` — 公开面再导出

`PromptEngine`、`PromptArtifact`、`SourceBinding`、`PromptParameters`、`PromptCompileRequest`、
`PromptCompilationError`、`PROMPT_ERROR_CODES`、四个 `PROMPT_*` code、`SOURCE_KINDS`、
四个 `SOURCE_KIND_*`、`ModelRenderer`、`QwenImageRenderer`、`RENDERER_VERSION`、`QWEN_IMAGE_MODEL`、
`parse_output_size`。**不导出** `CompilationSpec` / `CompilationClause` / `spec` 模块任何名字。

---

## 变更文件

新增（**未修改任何上游或既有文件**）：

- `visual_intent_agent/realization/models.py`
- `visual_intent_agent/prompt_engine/models.py`
- `visual_intent_agent/prompt_engine/spec.py`
- `visual_intent_agent/prompt_engine/renderer.py`
- `visual_intent_agent/prompt_engine/engine.py`
- `visual_intent_agent/prompt_engine/__init__.py`（原为 0 字节空文件，现再导出公开面）
- `tests/prompt_engine/prompt_engine_helpers.py`（唯一命名 helper 模块，遵守 Rev.1；自包含、全离线）
- `tests/prompt_engine/test_prompt_models.py`
- `tests/prompt_engine/test_prompt_renderer.py`
- `tests/prompt_engine/test_prompt_engine_scenarios.py`
- `tests/prompt_engine/test_prompt_engine_compile.py`
- `tests/prompt_engine/test_prompt_engine_e2e.py`
- `tests/prompt_engine/test_prompt_engine_public_api.py`
- `tests/realization/test_realization_models.py`
- `docs/handoffs/step_07_handoff.md`（本文件）

未触碰：根目录任务书 md、`README.md`、`AGENT_DISPATCH_PROMPTS.md`、`api.md`、
`docs/ARCHITECTURE.md`、`visual_intent_agent/{domain,validation,policy,persistence,intent_engine,providers,workflow,config.py}`、
`visual_intent_agent/realization/__init__.py`（保持 0 字节）、`tests/{domain,validation,policy,persistence,
providers,intent_engine,workflow,e2e,smoke,fixtures}`、`tests/conftest.py`、`tests/test_sanity.py`、
`pyproject.toml`、`uv.lock`、`.env`。**无新增依赖。**

两个新测试目录都无 `conftest.py`、不 `from conftest import ...`、不跨目录 import
（helper 是各自目录内的唯一命名模块）。

---

## 公开接口

```python
from visual_intent_agent.prompt_engine import (
    PromptEngine, PromptArtifact, SourceBinding, PromptParameters,
    PromptCompileRequest, PromptCompilationError,
    PROMPT_ERROR_CODES, PROMPT_NO_VALID_CONFIRMATION, PROMPT_UNAUTHORIZED_ADDITION,
    PROMPT_MISSING_SOURCE_BINDING, PROMPT_UNSUPPORTED_REQUIREMENT,
    SOURCE_KINDS, SOURCE_KIND_INTENT, SOURCE_KIND_DELEGATION,
    SOURCE_KIND_REALIZATION, SOURCE_KIND_RUNTIME,
    ModelRenderer, QwenImageRenderer, RENDERER_VERSION, QWEN_IMAGE_MODEL, parse_output_size,
)

from visual_intent_agent.realization.models import RealizationState, RealizationValue
# realization/__init__.py 保持空白（多 Step 拥有），不经包再导出。

# 内部（engine.py / spec.py，仅引擎与测试直接 import）：
from visual_intent_agent.prompt_engine.engine import (
    PromptEngine, CLAUSE_ORDER, RENDER_LABELS, DELEGATED_CANDIDATES,
    PROMPT_ARTIFACT_ID_PREFIX, REALIZATION_ID_PREFIX,
    select_delegated_value, render_clause_text, read_intent_path, resolved_important_paths,
    bind_clause, check_source_bindings, check_clause_coverage,
)
from visual_intent_agent.prompt_engine.spec import CompilationClause, CompilationSpec
```

### 本步新增 Issue / Error code（命名空间 `prompt.*`，ARCHITECTURE 5.5）

| code | 承载 | 触发条件 |
|---|---|---|
| `prompt.no_valid_confirmation` | `PromptCompilationError` | Confirmation 不存在 / 已失效 / 未绑定当前两类 revision / 属于别的会话 / id 为空；保存前复核失败 |
| `prompt.unauthorized_addition` | `PromptCompilationError` | 重要 clause 没有任何合法来源（`source_kind` 缺失或非法）——编译失败，不继续生成 |
| `prompt.missing_source_binding` | `PromptCompilationError` | clause 声明了来源类别但缺少强制绑定字段 / 绑定字段非法；已解决的重要路径没有绑定 clause（静默丢弃） |
| `prompt.unsupported_requirement` | `PromptCompilationError` | 非唯一目标模型 / 无法解析的输出尺寸 / 已确认 Intent 无任何可渲染 clause / 委托路径没有确定性候选表 |

### Repository 使用（不绕过协议直读 SQLite）

`get_current_session_snapshot`、`is_confirmation_valid`、`get_confirmation`、`get_intent_revision`、
`get_execution_revision`、`get_current_realization_state`、`append_realization_state`、
`append_prompt_artifact`（refs 必填键严格照 step_04 冻结）。

### 对 ARCHITECTURE.md 的最小修订建议（未自行修改文档）

1. **`realization/__init__.py` 的再导出冲突**：派发提示词写"`realization/__init__.py` 再导出"，
   但 ARCHITECTURE 第 1 节规则与第 4 节 Step 07 冻结表都冻结该文件为**空白**（多 Step 拥有，
   Step 09 还会加 `carry.py`）。本步按架构冻结保持空白，下游用完整路径
   `visual_intent_agent.realization.models` 导入。若架构方希望改为再导出（additive），
   建议在 Rev.2 中同时解除"多 Step 拥有包 `__init__.py` 必须空白"的约束。
2. **Step 07 冻结表可补一行说明**：`compile` **不**要求会话处于 `WAITING_CONFIRMATION`
   （Step 08 的 `generate` 是 `WAITING_CONFIRMATION + 有效确认 → GENERATING → compile`，
   compile 只看 `is_confirmation_valid`）。
3. **`first_prompt_artifact_id` 的生成时机**：新 RealizationState 必须在 PromptArtifact 之前落库
   （冻结流程），因此 `pra` id 在计划阶段用 `new_id("pra")` 预生成并写入 RealizationValue；
   `realization_states` 对 `prompt_artifacts` **没有**外键（step_04 冻结 refs 只有
   `based_on_intent_revision_id`），故无 FK 顺序问题。建议在冻结表注明"id 预生成、先写
   realization 后写 prompt artifact"。

---

## 测试命令与结果

```text
$ cd /home/change/projects/image_system

# 基线（实施前）
$ uv run pytest -q
1472 passed, 2 deselected

# 全量（基线 1472 + 本步 113）
$ uv run pytest -q
1585 passed, 2 deselected in 3.93s

# 本步范围
$ uv run pytest tests/prompt_engine -q
92 passed in 0.39s
$ uv run pytest tests/realization -q
21 passed in 0.04s
$ uv run pytest tests/prompt_engine tests/realization -q
113 passed in 0.45s

# Rev.1 上游多目录验收命令（未回归）
$ uv run pytest tests/persistence tests/domain tests/validation tests/policy tests/test_sanity.py -q
1247 passed in 2.88s

# 跨进程确定性
$ PYTHONHASHSEED=0 / 7 / 424242 uv run pytest tests/prompt_engine tests/realization -q
-> 均 113 passed

# 依赖边界（干净子进程导入 visual_intent_agent.prompt_engine）
-> 只加载 visual_intent_agent.{domain,persistence,prompt_engine,realization} 与 stdlib sqlite3；
   未拉入 config / providers / intent_engine / workflow / generation / feedback / httpx。
```

全部默认测试离线：只用 `FakeLLMProvider` + `tmp_path` SQLite，零网络访问、零真实凭据
（对 `.env` 真实 key 全文 grep 本步新增文件 0 命中；无 HTTP 客户端、无图像 Provider、
无 `random`/`uuid4` 直接调用）。`test_prompt_engine_public_api.py` 逐文件静态断言
`prompt_engine/*.py` 不含 `httpx`/`requests`/`urllib`/`openai`/`socket`/`random`/`uuid4`
与任何凭据 token。

### 任务书「必测场景」→ 测试映射（8/8）

| # | 场景 | 测试 |
|---|---|---|
| 1 | 无有效确认时拒绝编译 | `test_prompt_engine_scenarios.py::test_scenario_01_compile_without_any_confirmation_is_rejected`、`::test_scenario_01_empty_confirmation_id_is_rejected`、`::test_scenario_01_confirmation_of_another_session_is_rejected`、`::test_scenario_01_invalidated_confirmation_is_rejected`、`test_prompt_engine_e2e.py::test_p1_compile_before_confirmation_is_rejected` |
| 2 | 每个重要 clause 有来源 | `test_scenario_02_every_important_clause_has_a_source_binding`、`::test_scenario_02_bindings_record_the_right_source_kind` |
| 3 | unspecified + omit 不被具体化 | `test_scenario_03_unspecified_and_omitted_fields_are_never_concretized`、`::test_scenario_03_not_applicable_is_not_a_source` |
| 4 | delegated lighting 可实现但不决定 color | `test_scenario_04_delegated_lighting_is_implemented_but_color_is_not_decided`、`::test_scenario_04_only_the_delegated_path_is_decided`、`::test_delegated_choice_is_deterministic_and_within_the_candidate_table` |
| 5 | 已有 Realization 稳定复用 | `test_scenario_05_recompiling_reuses_the_same_realization`、`::test_scenario_05_existing_active_realization_wins_over_reselection` |
| 6 | revision 变化后旧 PromptArtifact 不被覆盖 | `test_scenario_06_old_prompt_artifact_is_not_overwritten_after_revision_change`、`test_prompt_engine_e2e.py::test_p1_reconfirmation_keeps_old_artifact_and_reuses_realization` |
| 7 | Renderer 只处理一个目标模型 | `test_prompt_renderer.py`（22 例：唯一实现、版本、`supports`、无修饰词）、`test_prompt_engine_compile.py::test_compile_rejects_a_non_supported_target_model` |
| 8 | 人为插入无 source clause 时编译失败 | `test_scenario_08_a_clause_without_any_source_is_rejected`、`test_scenario_08_injected_clause_fails_compilation_before_any_write`、`test_scenario_08_malformed_binding_is_reported_as_missing_source_binding` |

另覆盖：模型字段面/frozen/时间戳（`test_prompt_models.py` 13 例）、保存前复核
（`test_compile_revalidates_the_confirmation_before_saving`）、已解决路径被丢弃
（`test_dropping_a_resolved_clause_fails_the_coverage_check`）、新 state carry + 新选择
（`test_new_realization_state_carries_active_values_and_adds_new_selections`）、
GENERATING 后编译（`test_compile_can_run_after_the_session_transitions_to_generating`）、
refs 精确（`test_artifact_is_saved_with_the_frozen_refs_and_reads_back`、
`test_realization_state_refs_use_the_frozen_required_key`）、完整可追溯
（`test_artifact_is_fully_traceable_to_intent_confirmation_and_realization`）、
Realization 模型与 append-only 历史（`tests/realization/` 21 例）、公开面与依赖边界
（`test_prompt_engine_public_api.py` 12 例）、P1 集成（`test_prompt_engine_e2e.py` 6 例）。

---

## 已知限制

1. **`realization/__init__.py` 保持空白（按 ARCHITECTURE 冻结，未按派发提示词再导出）**：
   架构第 1 节规定多 Step 拥有的包（`providers/`、`realization/`）`__init__.py` 必须空白，
   Step 07 冻结表也如此写。派发提示词中"+ `realization/__init__.py` 再导出"与该冻结冲突，
   本步选择服从架构冻结（避免 Step 09 写冲突），下游一律
   `from visual_intent_agent.realization.models import ...`；已在「公开接口」提最小修订建议。
2. **`compile` 不检查工作流状态**：只复核 `is_confirmation_valid`（绑定当前两类 revision）。
   这是**刻意**的：Step 08 冻结流程是 `WAITING_CONFIRMATION + 有效确认 → transition GENERATING
   → compile`，若 compile 要求 `WAITING_CONFIRMATION` 会打断 Step 08（有测试钉住该顺序）。
   "无确认不生成 Prompt"由 `prompt.no_valid_confirmation` 保证，不依赖状态。
3. **Step 07 不做 Realization 失效评估**：只要当前 state 中某路径的值 `status == "active"`
   且该路径当前仍为 `user_delegated`，就原样复用，**不比较** state 的
   `based_on_intent_revision_id` 是否为当前 revision。跨 revision 的失效/继承判定是
   Step 09 `evaluate_carry` 的职责（`invalidates_realization` 字段已由 Step 03 提供）；
   若 Step 09 需要在编译前先落新 state，只需保证 `get_current_realization_state` 已是最新。
4. **新 RealizationState 只 carry `active` 值**：组装新 state（因出现新的 delegated 路径）时
   保留现有 active 值（原顺序、原 `first_prompt_artifact_id`），**不**把 `invalidated` 值带进
   新快照（旧 state 仍可审计）。Step 09 若要保留失效明细，应在自己的新 state 中显式重建。
5. **`first_prompt_artifact_id` 是"首次产生该值的 PromptArtifact"**：在计划阶段用
   `new_id("pra")` 预生成并写入新 `RealizationValue`，随后才 `append_prompt_artifact`。
   `realization_states` 对该 id 无外键（step_04 冻结 refs 仅
   `based_on_intent_revision_id`），因此顺序安全；但若历史 PromptArtifact 被外部删除，
   该字段是弱引用（append-only 保证正常流程不会发生）。
6. **只支持唯一目标模型 `qwen-image-3.0`**：`Settings.image_model` 若改成别的值，
   `WorkflowService.create_session` 会把它写进 ExecutionRevision，compile 将抛
   `prompt.unsupported_requirement`。这是"只支持一个目标模型"的落地；要换模型需新增
   Renderer（属架构变更，不在本步范围）。
7. **委托选择规则与 revision 绑定**：`select_delegated_value` 的 seed 是 `intent_revision_id`，
   因此同一 revision 结果逐字相同、不同 revision 可能不同；一旦值被持久化，后续编译**只复用**
   不再调用选择函数（"重新编译不得重新选择"）。候选表刻意不做"模型最佳实践"式扩展。
8. **Confirmation 的 summary hash 不在 compile 中重算**：沿用 Step 04 冻结语义
   （`is_confirmation_valid` = 记录存在 + 绑定当前两类 revision + 行内 binding hash 完整），
   重算与比对由 Step 06 `confirm_current_intent` 在保存时完成；compile 只做门禁复核，
   `PromptArtifact.based_on_confirmation_id` 保证可追溯。
9. **Realization 与 Prompt 的落库是两次独立事务**：流程要求"保存前再次验证"，但两个
   `append_*` 之间没有跨表事务（Repository 未提供）。MVP 单进程/单写者下可接受；若未来并发
   写同一会话，需由 Repository 增加跨 artifact 事务（属 Step 04 修订）。
10. **Renderer 措辞是 MVP 最小版**：`"<label>: <value>"` 逗号连接，未做模型专属语法/权重；
    token 顺序与措辞属 implementation 自由度，但**不得**新增 clause 内容（有测试禁止
    质量修饰词）。`VisualIntent.intent_id` 仍保持 `None`（P1/P2 无消费方，未提出修订）。
11. **未实现（属后续步骤）**：图像 Provider 调用与 GenerationArtifact（08）、FeedbackEngine /
    carry / 多轮修改（09）、Web API、RAG/KnowledgeEngine（11）、Prompt AST、seed/negative_prompt
    等额外参数面。

---

## 对下一步的输入

### Step 08（图像生成闭环）：编译结果读取、目标模型参数、失败类型、Provider 最小输入

**1. 调用顺序（冻结）**：

```python
from visual_intent_agent.persistence import WorkflowState
from visual_intent_agent.prompt_engine import (
    PromptEngine, QwenImageRenderer, PromptCompileRequest,
    PromptArtifact, PromptCompilationError,
)

snapshot = repo.get_current_session_snapshot(session_id)
assert snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
confirmation_id = snapshot.latest_confirmation_id
assert repo.is_confirmation_valid(confirmation_id) is True

repo.transition_state(session_id, WorkflowState.GENERATING)      # 先迁移
engine = PromptEngine(QwenImageRenderer(), repo)                 # renderer 唯一实现
artifact = engine.compile(
    PromptCompileRequest(session_id=session_id, confirmation_id=confirmation_id)
)                                                                # 后编译；compile 不要求状态
```

- `compile` 内部已复核 Confirmation（无效 → `prompt.no_valid_confirmation`），Step 08
  **不要**自行重算 binding hash；
- `compile` **不**改变工作流状态；`GENERATING` 由 `GenerationPipeline` 负责。

**2. 已保存 PromptArtifact 的读取方式**：

```python
stored = repo.get_prompt_artifact(artifact.prompt_artifact_id)   # StoredArtifact 信封
assert stored.refs == {"intent_revision_id": ..., "confirmation_id": ...}
restored = PromptArtifact.model_validate_json(stored.payload)    # 与返回值逐字段相等
# retry 路径：从旧 GenerationArtifact 的 refs["prompt_artifact_id"] 取回同一 artifact
# （stored.refs 只有 prompt_artifact_id → get_prompt_artifact → PromptArtifact.model_validate_json），
# 不得重新 compile。
```

**3. 目标模型参数（Provider 调用的最小输入）**：

| Provider 需要 | 来源 |
|---|---|
| `prompt`（文本） | `artifact.prompt`（纯文本，**不是**生成结果） |
| `size`（`"<w>x<h>"`） | `artifact.parameters.size`（来自已确认 `ExecutionRevision.output_size`） |
| `model`（目标图像模型） | `artifact.target_model`（唯一值 `qwen-image-3.0`，即 `QwenImageRenderer().target_model`） |
| 建议记录到 GenerationArtifact | `artifact.prompt_artifact_id`、`artifact.target_model`、`artifact.parameters`、`RENDERER_VERSION`（可选，便于 Step 10 复现 Prompt 组织方式） |

`PromptParameters` **只有** `size`；Step 08 不要从 PromptArtifact 之外臆造 seed / negative_prompt。

**4. PromptEngine 的失败类型（`PromptCompilationError.code`，全部硬失败、不产生 Artifact）**：

| code | Step 08 的建议处理 |
|---|---|
| `prompt.no_valid_confirmation` | 门禁失败：不调用 Provider，不写 GenerationArtifact；返回可恢复错误/要求重新确认 |
| `prompt.unauthorized_addition` | 程序级失败：说明编译越权，应视为 bug，转 `FAILED` 并记录 |
| `prompt.missing_source_binding` | 程序级失败：已确认要求无法绑定来源（同上） |
| `prompt.unsupported_requirement` | 执行侧不支持（非唯一模型 / 非法 size / 无可渲染 clause）：转 `FAILED`，**不得**降级猜测 |

`ProviderError`（Step 05/08 共用的 `providers/errors.py`）与上述异常是两类：前者是 Provider
调用失败，Step 08 按冻结表 `transition FAILED` 且不伪造 Artifact。

**5. 边界**：Step 07 不 import `providers.image` / `httpx`；Step 08 引入图像 Provider 后，
`generation/` 依赖 `prompt_engine` 是单向的（prompt_engine 不反向依赖 generation）。

### Step 09（Feedback / Realization / Review）

- `realization/models.py` 已就绪：`RealizationState` / `RealizationValue` 八字段（含
  `carry_policy` / `status` / `invalidated_reason` / `invalidated_at`）。Step 09 在新增文件
  `realization/carry.py` 中实现 `evaluate_carry(...)`，**不改** `models.py`；
  `realization/__init__.py` 保持空白。
- 失效语义冻结（Step 03 已提供 `DecisionPolicy.invalidates_realization`）：用户 SET/CLEAR 同路径、
  依赖路径变化、PIN/UNPIN 影响 → 产生**新** `RealizationState`（旧值显式 `status="invalidated"` +
  reason + `invalidated_at`），经 `append_realization_state` 落库，历史不覆盖。
- `PromptEngine` 在 compile 时只复用 `status == "active"` 且路径仍 `user_delegated` 的值；
  因此 Step 09 只需保证"重新确认前已把正确的 active state 落库"，compile 无需改动。

### 通用

- 新测试目录（`tests/prompt_engine/`、`tests/realization/`）已建立：共享工具放唯一命名模块
  （`prompt_engine_helpers.py`），无 `from conftest import ...`，无跨目录 import。
- 新发现冻结表缺口继续走"最小修订提案 → 架构方裁定"（见本文件「公开接口」小节）。

---

## 验收条件逐条核对

| # | 任务书验收条件 | 核对结果 |
|---|---|---|
| 1 | 无 Confirmation 不生成 Prompt | **是**。compile 首步与保存前各复核一次 `is_confirmation_valid` + 绑定当前 revision + 会话归属（`prompt.no_valid_confirmation`）；缺失/空 id/跨会话/失效/保存前失效五类场景各有测试；失败时 `prompt_artifacts` 与 `realization_states` 计数均为 0（编译失败不继续）。 |
| 2 | 重要视觉 clause 的来源覆盖率为 100% | **是**。每个 clause 经 `bind_clause` 生成 binding，`source_kind=intent/delegation/realization/runtime` 分别强制 `intent_path`（白名单）/ `realization_id` / `rule_id`；`check_clause_coverage` 保证已解决的每个重要路径都有绑定 clause；Prompt 文本由 clause 文本拼装，测试断言每个 `binding.text` 均出现在 prompt 中且覆盖集合等于已解决路径集合（11/11 = 1.0）。 |
| 3 | unspecified 不被等同于 delegated | **是**。无值且无 `user_delegated` 记录的路径（含 `not_applicable` 与 policy `omit`）不产生 clause/binding/Realization；`resolved_important_paths` 只认"有值"或"显式 user_delegated"；`total realization_states == 0` 的测试证明没有把缺失当成委托。 |
| 4 | 不引入第二个图像模型 Renderer | **是**。`renderer.py` 只有一个 `ModelRenderer` Protocol + 一个 `QwenImageRenderer`（静态类名断言 + 运行期 `isinstance` + 包内 `*Renderer` 只有两个名字）；`RENDERER_VERSION="qwen_image.v1"`；非唯一模型的 ExecutionRevision → `prompt.unsupported_requirement`。 |
| 5 | PromptArtifact 可完整追溯到 Intent 和 Confirmation | **是**。`based_on_intent_revision_id` / `based_on_confirmation_id` 经 repo 读回逐字段校验；`append_prompt_artifact` 的 refs 精确为冻结必填键；每个 binding 指向真实存在且值存在的 Intent 路径或当前 RealizationState；`realization_states` refs 为 `{"based_on_intent_revision_id"}`；全量字段往返一致（`model_validate_json == artifact`）。 |

## 是否满足验收条件

**是。** 5 条验收条件逐条通过；任务书 8 条「必测场景」全部有显式测试映射；本步新增
113 个用例全绿（`tests/prompt_engine` 92 + `tests/realization` 21），上游既有测试未回归
（Rev.1 多目录 1247 passed），全量 `uv run pytest -q` → **1585 passed, 2 deselected**
（零网络、零真实凭据）；三个 `PYTHONHASHSEED` 下均 113 passed；`prompt_engine` 干净子进程导入
不拉入 `config`/`providers`/`intent_engine`/`workflow`/`generation`/`feedback`/`httpx`。
唯一与派发提示词的字面差异是 `realization/__init__.py` 保持空白（服从 ARCHITECTURE 冻结表，
已提最小修订建议并在「已知限制 1」说明）。未实现任何后续步骤能力（无图像调用、无 RAG、
无 carry/失效逻辑、无 Prompt AST），未修改任何上游或既有文件，未新增依赖，
源码/测试/本文件中无明文 API key。
