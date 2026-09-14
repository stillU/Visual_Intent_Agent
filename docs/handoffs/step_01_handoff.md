# Step 01 交接记录：领域合同与数据模型

任务：Step 01 — 领域合同与数据模型（VisualIntent Schema v1、Resolution、IntentDelta、Revision、Issue、Evidence）

## 完成内容

1. 在 `visual_intent_agent/domain/` 实现全部纯数据合同（无 LLM / 数据库 / Web / 图像 Provider 依赖）：
   - `constants.py`：`SCHEMA_VERSION: Literal["v1"] = "v1"`（全系统唯一版本常量）；
   - `identifiers.py`：`Id = str`、`new_id(prefix) -> "<prefix>_<uuid4hex>"`、`utc_now() -> tz-aware UTC`；
   - `paths.py`：`INTENT_PATHS` 恰好 12 条（与七类 Facet 字段一一对应）与 `SYSTEM_FIELD_NAMES`（10 个系统字段名）；
   - `issue.py`：`Severity`（error/warning/info）、`EvidenceRef`（`message_id` 必填，`fragment`/`pending_question_id` 可选）、`Issue`（`code`/`message`/`path?`/`severity` 默认 error）；
   - `intent.py`：`Resolution` 四值枚举、七个 Facet、`ResolutionRecord`、`VisualIntent`；
   - `delta.py`：`DeltaOperation` 四值、`IntentDelta` 及 operation 形状 `model_validator`；
   - `revision.py`：`IntentRevision`、`ExecutionRevision`；
   - `__init__.py`：再导出上述全部公开名。
2. 合同约束按 `docs/ARCHITECTURE.md` 第 4 节冻结项逐条落地：
   - 所有合同模型 `ConfigDict(frozen=True, extra="forbid")`；
   - `VisualIntent.resolutions` 与 `pinned_paths` 的键必须 ⊆ `INTENT_PATHS`（`model_validator`）；
   - `SET` 必须至少携带 `value` 或 `resolution`；`CLEAR`/`PIN`/`UNPIN` 两者都不得携带；
   - `created_at` 默认 `utc_now()`；naive datetime 被拒绝；JSON 输出 ISO 8601 带 `+00:00`；
   - `schema_version` 为 `Literal["v1"]`，不支持版本反序列化直接拒绝；
   - `missing ≠ user_delegated`：缺失字段无值且不产生 `ResolutionRecord`，不被自动授权；
   - 未新增任何顶层字段（服装/镜头型号/材质/情绪等一律不存在）；核心合同无 `dict[str, Any]`。
3. 建立 Schema v1 JSON fixture 12 个：空 Intent、完整 Intent（全 12 路径）、显式委派/缺失对照 Intent、`SET`（值 + 分辨率）、`SET`（resolution-only，含 pending question 证据）、`CLEAR`、`PIN`、`UNPIN`、genesis Revision、完整 Revision（含 applied deltas 与父链）、ExecutionRevision、Issue。
4. 单测 143 个（`tests/domain/`），覆盖任务书"必测场景"全部 9 条；全量 pytest（含既有 sanity）148 passed。

## 变更文件

新增（未修改任何只读或既有文件）：

- `visual_intent_agent/domain/constants.py`、`identifiers.py`、`paths.py`、`issue.py`、`intent.py`、`delta.py`、`revision.py`、`__init__.py`（`__init__.py` 原为空文件，现改为再导出）
- `tests/domain/conftest.py`、`test_paths.py`、`test_identifiers.py`、`test_intent.py`、`test_delta.py`、`test_issue.py`、`test_revision.py`、`test_fixtures_schema_v1.py`、`test_public_api.py`
- `tests/fixtures/schema_v1/`：`empty_intent.json`、`complete_intent.json`、`partial_intent_with_delegation.json`、`delta_set.json`、`delta_set_resolution_only.json`、`delta_clear.json`、`delta_pin.json`、`delta_unpin.json`、`intent_revision_genesis.json`、`intent_revision.json`、`execution_revision.json`、`issue.json`
- `docs/handoffs/step_01_handoff.md`（本文件）

未触碰：MVP v0.2 任务书与任务索引、`api.md`、`docs/ARCHITECTURE.md`、`visual_intent_agent/config.py`、`tests/conftest.py`、`tests/test_sanity.py`、`pyproject.toml`、`uv.lock`、`.env`。**无新增依赖**（仍只有 pydantic / httpx / pytest）。

## 公开接口

后续步骤统一从 `visual_intent_agent.domain` 导入（也可按模块完整路径导入）：

| import 路径 | 公开名 | 关键点 |
|---|---|---|
| `visual_intent_agent.domain` | `SCHEMA_VERSION` | `Literal["v1"]`，值 `"v1"` |
| 同上 | `INTENT_PATHS` | `frozenset[str]`，恰好 12 条；路径白名单唯一定义 |
| 同上 | `SYSTEM_FIELD_NAMES` | `frozenset[str]`，10 个系统字段名 |
| 同上 | `Id` / `new_id` / `utc_now` | `Id = str`；`new_id(prefix) -> "<prefix>_<uuid4hex>"`；`utc_now() -> tz-aware UTC` |
| 同上 | `Severity` | `str` 枚举：`error` / `warning` / `info` |
| 同上 | `EvidenceRef` | `message_id: str`（必填）、`fragment: str\|None`、`pending_question_id: str\|None` |
| 同上 | `Issue` | `code: str`、`message: str`、`path: str\|None`、`severity: Severity = ERROR` |
| 同上 | `Resolution` | `str` 枚举：`user_specified` / `user_confirmed_proposal` / `user_delegated` / `not_applicable` |
| 同上 | `ResolutionRecord` | `resolution: Resolution`、`evidence_refs: list[EvidenceRef]`、`reason: str\|None` |
| 同上 | `VisualIntent` | `schema_version`、`intent_id: str\|None`、七个 Facet、`resolutions: dict[str, ResolutionRecord]`、`pinned_paths: frozenset[str]` |
| 同上 | `SubjectFacet` | `description`、`count: int\|None (>0)`、`pose_action` |
| 同上 | `CompositionFacet` | `framing` |
| 同上 | `EnvironmentFacet` | `mode`、`location` |
| 同上 | `StyleFacet` | `primary`、`description` |
| 同上 | `LightingFacet` | `character` |
| 同上 | `CameraFacet` | `angle`、`depth_of_field` |
| 同上 | `ColorFacet` | `palette` |
| 同上 | `DeltaOperation` | `str` 枚举：`SET` / `CLEAR` / `PIN` / `UNPIN` |
| 同上 | `IntentDelta` | `operation`、`path: str`、`value: str\|int\|None`、`resolution: Resolution\|None`、`evidence_refs: list[EvidenceRef]` |
| 同上 | `IntentRevision` | `schema_version`、`intent_revision_id`、`session_id`、`parent_revision_id: str\|None`、`intent`、`applied_deltas`、`created_at` |
| 同上 | `ExecutionRevision` | `schema_version`、`execution_revision_id`、`session_id`、`parent_revision_id`、`target_model`、`output_size="1024x1024"`、`created_at` |

本步**没有新增 Issue code**（domain 层不产生 Issue，只提供结构）。Step 02 起的 code 命名空间见 ARCHITECTURE.md 5.5。

### 实现说明（未改变任何冻结签名）

1. `VisualIntent.pinned_paths` 的 JSON 序列化按字典序输出（`field_serializer`, `when_used="json"`）：`frozenset` 迭代序随 `PYTHONHASHSEED` 变化，若不固定，Step 06 `compute_summary_hash` 的 `json.dumps(..., sort_keys=True)` 无法排序数组元素，确认 hash 会跨进程漂移。`model_dump()`（python 模式）仍返回 `frozenset`。
2. `created_at` 的 JSON 形态固定为 `...+00:00`（pydantic 默认输出 `Z`）；输入同时接受 `Z` 与 `+00:00`，round-trip 语义不变。
3. 非 UTC 的 tz-aware `created_at` 被归一到 UTC（同一时刻），naive datetime 被拒绝。
4. `IntentDelta.path` 在模型层**不**校验白名单（LLM 输出的解析目标属 Step 02 职责）；`VisualIntent.resolutions` / `pinned_paths` 才校验白名单。
5. `ExecutionRevision.output_size` 只冻结默认值与"`<w>x<h>`"语义，Step 01 不强制格式（格式校验属 Step 08 的 `ImageGenerationRequest`）。
6. 未新增 `FACET_MODELS` 之类未冻结的公开名；Facet 集合可由 `VisualIntent.model_fields` 推导。

## 测试命令与结果

```text
$ cd /home/change/projects/image_system
$ uv run pytest -q
........................................................................ [ 48%]
........................................................................ [ 97%]
....                                                                     [100%]
148 passed in 0.23s

$ uv run pytest tests/domain -q
143 passed in 0.21s

# 交叉验证：换 PYTHONHASHSEED 仍全绿（fixture / frozenset 序列化稳定）
$ PYTHONHASHSEED=31337 uv run pytest tests/domain -q
143 passed in 0.21s

$ uv run pytest tests/test_sanity.py -v
tests/test_sanity.py::test_pydantic_is_v2 PASSED
tests/test_sanity.py::test_settings_load_from_env_only PASSED
tests/test_sanity.py::test_settings_env_file_fallback PASSED
tests/test_sanity.py::test_settings_missing_credentials_raise PASSED
tests/test_sanity.py::test_settings_are_frozen PASSED
5 passed in 0.02s

# 跨进程 fixture 序列化稳定性实测：三个 hash seed 下 model_dump_json 的 sha256 完全一致
b0950fc1... （PYTHONHASHSEED=0 / 7 / 424242 相同）
```

`tests/domain/` 分布：`test_delta.py` 20、`test_fixtures_schema_v1.py` 34、`test_identifiers.py` 5、`test_intent.py` 34、`test_issue.py` 11、`test_paths.py` 5、`test_public_api.py` 19、`test_revision.py` 15 = 143。

必测场景 → 测试映射：

1. 空 Intent 可创建且缺失不被自动标记 delegated → `test_intent.py::test_empty_intent_can_be_created`、`::test_missing_is_not_auto_marked_as_user_delegated`、`test_fixtures_schema_v1.py::test_empty_intent_fixture_is_empty_and_not_delegated`；
2. 七类 Facet round-trip → `test_intent.py::test_each_facet_field_round_trips`（12 参数）、`::test_each_facet_model_round_trips`（7 参数）、`::test_all_seven_facets_round_trip_together`；
3. 未知顶层字段被拒绝 → `::test_unknown_top_level_field_is_rejected`、`::test_unknown_facet_field_is_rejected`；
4. 非法 Resolution 被拒绝 → `::test_illegal_resolution_is_rejected`；
5. 非法 Delta operation 被拒绝 → `test_delta.py::test_illegal_delta_operation_is_rejected`；
6. `SET` 缺 value 且缺 resolution 被拒绝 → `::test_set_missing_value_and_resolution_is_rejected`；
7. EvidenceRef 准确关联消息 → `test_issue.py::test_evidence_ref_associates_an_exact_message`；
8. IntentRevision round-trip 内容不变 → `test_revision.py::test_full_intent_revision_round_trip_content_unchanged`、`::test_genesis_intent_revision_round_trip_content_unchanged`；
9. Schema v1 fixture 重复加载稳定 → `test_fixtures_schema_v1.py::test_fixture_repeated_loading_is_stable`（12 参数）。

## 已知限制

1. domain 层不生成 `intent_id`（由 Step 06 首次持久化时赋值）；ARCHITECTURE.md 5.1 的前缀表未包含 intent 前缀，Step 06 若需要请自行冻结前缀或按最小修订提出。
2. `frozen=True` 只阻止属性赋值，`list`/`dict` 字段内容仍可被原地修改（pydantic 语义）；"修改 = 构造新对象"的执行纪律由 Step 02 Reducer / Step 06 Service 保证，本步不引入深冻结类型（会改变冻结签名的 `list[...]` 字段类型）。
3. `ExecutionRevision.output_size` 的 `"<w>x<h>"` 格式未在 Step 01 强制（见实现说明 5）。
4. 本步不产生 Issue code，也不提供任何 Validator/Reducer 语义。
5. 按要求未实现（属后续步骤）：Validator、Reducer、DecisionPolicy、Interpreter、PromptEngine、生成、持久化、HTTP API。

## 对下一步的输入

Step 02 可直接使用：

- 路径白名单：`from visual_intent_agent.domain import INTENT_PATHS, SYSTEM_FIELD_NAMES`（唯一定义，勿复制清单）；
- 类型：`VisualIntent`、`IntentDelta`/`DeltaOperation`、`Resolution`/`ResolutionRecord`、`EvidenceRef`/`Issue`/`Severity`、`Id`/`new_id`/`utc_now`、`SCHEMA_VERSION`；
- Validator 需自行实现（Step 01 未实现）：`path ∈ INTENT_PATHS`、证据存在性、`SET` 值类型与路径匹配（`subject.count` → int）、`SET` 目标是否命中等；`IntentDelta` 模型层只保证 operation 形状；
- Reducer 语义提示：`CLEAR` 需同时移除 `resolutions[path]`；`missing ≠ user_delegated` 是 domain 已固化语义；
- fixture：`tests/fixtures/schema_v1/*.json` 可作 Step 02/03 的输入样例；加载后必须 round-trip 稳定。

## 验收条件逐条核对

| # | 任务书验收条件 | 核对结果 |
|---|---|---|
| 1 | 所有模型测试通过 | 是：`uv run pytest -q` → 148 passed（domain 143 + sanity 5），零网络访问 |
| 2 | Schema 只包含冻结字段 | 是：`test_facet_models_accept_only_their_frozen_fields` 钉住七 Facet 与 `VisualIntent` 的精确字段集；无服装/镜头型号/材质/情绪等新增字段；`extra="forbid"` 全模型生效 |
| 3 | 无 LLM、数据库、Web API 或图像 Provider 依赖 | 是：domain 仅依赖 pydantic/标准库；`test_public_api.py::test_domain_package_has_no_provider_database_or_web_dependency` 用子进程验证导入 `visual_intent_agent.domain` 不会引入 httpx/sqlite3/fastapi/openai/PIL/numpy，也不 import `config`/`providers` |
| 4 | 后续 Agent 不需要阅读内部实现即可使用公开模型 | 是：`domain/__init__.py` 再导出全部公开名（`test_domain_package_reexports_the_frozen_public_surface`），本文件"公开接口"给出逐名表 |
| 5 | 没有以 `dict[str, Any]` 替代核心合同 | 是：domain 中不存在 `Any`/`dict[str, Any]`；唯一 dict 为 `resolutions: dict[str, ResolutionRecord]` |

## 是否满足验收条件

是。所有验收条件逐条通过；未修改任何只读文件或既有测试，未新增依赖，未实现任何后续步骤能力，源码/测试/fixture 中无明文 key。
