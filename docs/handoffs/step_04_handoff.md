# Step 04 交接记录：Repository 与状态机

任务：Step 04 — Repository 与状态机（SQLite schema/迁移、revision 快照、Confirmation 严格绑定、确定性状态机、临时库集成测试）

## 完成内容

### 1. `visual_intent_agent/persistence/schema.sql` — 9 张表 + 外键（`PRAGMA user_version` 迁移）

| # | 表 | 职责 | 写路径 |
|---|---|---|---|
| 1 | `sessions` | 会话 + 当前指针（workflow_state / current intent+execution revision / latest confirmation / pending question） | INSERT + 事务内 UPDATE 指针 |
| 2 | `messages` | 会话消息 | 只 INSERT |
| 3 | `intent_revisions` | 每次 Intent 修改的完整快照（`revision_json`）+ parent 链 | 只 INSERT |
| 4 | `execution_revisions` | ExecutionContext 快照（`revision_json`）+ parent 链 | 只 INSERT |
| 5 | `confirmations` | Confirmation 的 intent_revision + execution_revision + summary_hash 绑定 | 只 INSERT |
| 6 | `prompt_artifacts` | 信封：必填 refs `intent_revision_id`,`confirmation_id` | 只 INSERT |
| 7 | `generation_artifacts` | 信封：必填 refs `prompt_artifact_id` | 只 INSERT |
| 8 | `feedback_results` | 信封：必填 refs `generation_id` | 只 INSERT |
| 9 | `realization_states` | 信封：必填 refs `based_on_intent_revision_id` | 只 INSERT |

- 未建 `knowledge_bundles`（Step 11 才可能新增）。
- 全部 `CREATE TABLE/INDEX IF NOT EXISTS`，中途失败可安全重跑。
- 所有 refs 必填键都是**真实外键列**（`REFERENCES ...`），FK 不存在的写入由 SQLite 拒绝；
  `PRAGMA foreign_keys=ON` 由 repository 在每次连接时显式打开并在事务外验证为 1。
- `payload` / `revision_json` 存各 Step 模型的完整 JSON，Step 04 不预知 07/08/09 的业务字段。

### 2. `visual_intent_agent/persistence/state_machine.py` — 冻结状态机

- `WorkflowState`（str 枚举）恰好 7 个持久状态；`RETRIEVING` / `REFINING` 不存在，构造即 `ValueError`。
- `ALLOWED_TRANSITIONS: frozenset[tuple[WorkflowState, WorkflowState]]` 恰好任务书 10 条：

  ```text
  UNDERSTANDING          -> WAITING_CLARIFICATION
  UNDERSTANDING          -> WAITING_CONFIRMATION
  WAITING_CLARIFICATION  -> UNDERSTANDING
  WAITING_CONFIRMATION   -> GENERATING
  GENERATING             -> WAITING_REVIEW
  GENERATING             -> FAILED
  FAILED                 -> GENERATING
  FAILED                 -> WAITING_CONFIRMATION
  WAITING_REVIEW         -> UNDERSTANDING
  WAITING_REVIEW         -> COMPLETED
  ```

  "new session → UNDERSTANDING" 是 `create_session` 直接写入的**初始状态**，不是状态间迁移，
  故不在表内（表共 10 条）。
- `InvalidStateTransitionError(Exception)`：`.code = "persistence.invalid_state_transition"`，
  另带 `.session_id` / `.from_state` / `.to_state`，消息含两端口语化状态名。
- 非法**状态改变**（32 条未冻结组合、`COMPLETED` 之后的任何迁移、未知状态字符串）**显式拒绝且不落库**。
- `to_state == 当前状态` 不是迁移、而是**幂等重复请求**：`transition_state` 返回成功且零写入。
  这是必需的：Step 06 冻结的用户消息流程（06 任务书「用户消息」第 2 步 / ARCHITECTURE.md
  Step 06 `submit_message` 注释）每轮都调用"状态进入 `UNDERSTANDING`"，而新会话本就在
  `UNDERSTANDING`；若同状态报错，P1 第一条消息即失败。同状态不改变任何持久数据，因此不违反
  "非法迁移无法落库"。

### 3. `visual_intent_agent/persistence/records.py` — 三个存储记录（frozen + extra=forbid）

| 模型 | 字段（与 ARCHITECTURE.md 4 逐字一致） |
|---|---|
| `ConfirmationRecord` | `schema_version`、`confirmation_id`、`session_id`、`intent_revision_id`、`execution_revision_id`、`summary_hash`、`confirmed_at` |
| `SessionSnapshot` | `session_id`、`workflow_state`、`current_intent_revision_id`、`current_execution_revision_id`、`latest_confirmation_id`、`pending_question_id`、`pending_question_payload`、`message_ids: tuple[str, ...]` |
| `StoredArtifact` | `artifact_id`、`session_id`、`refs: dict[str, str]`、`payload: str`、`created_at` |

`confirmed_at` / `created_at` 与 domain 同约定：naive 输入拒绝、非 UTC 归一到 UTC、JSON 固定 `+00:00`。

### 4. `visual_intent_agent/persistence/repository.py` — Repository 协议 + SQLite 实现

**迁移**：`apply_migrations(conn)` 读 `PRAGMA user_version`：0 → 执行 v1 DDL 并置 1；
1 → 幂等无操作；>1 → `RepositoryError("persistence.schema_too_new")` 拒绝打开新库。
`LATEST_SCHEMA_USER_VERSION = 1`。

**事务**：连接 `isolation_level=None`，写操作显式 `BEGIN IMMEDIATE` + 成功 `COMMIT` / 失败 `ROLLBACK`；
读快照用 `BEGIN`（deferred）保证一致视图；已在事务内则加入外层事务。
`sqlite3.IntegrityError` 被翻译为带 `persistence.*` code 的 `RepositoryError`。

**revision（只增不改）**：`append_intent_revision` / `append_execution_revision` 先做重复 ID 显式检查
（`duplicate_id`），再校验 `parent_revision_id` **必须等于当前 head**（乐观并发，违者 `stale_revision`），
INSERT 整份 `model_dump_json()`，最后在同一事务内推进 `sessions` 指针。读回用 `model_validate_json`，
逐值（含 datetime tz、applied_deltas、resolutions、pinned_paths）一致。

**Confirmation**：
- `save_confirmation` 要求绑定**当前** intent + execution revision（否则 `stale_revision`），
  revision 必须存在且属于同一会话，`summary_hash` 非空；
- 行内落 `binding_hash = sha256(intent_revision_id \x1f execution_revision_id \x1f summary_hash)`；
- `is_confirmation_valid(id)` = 记录存在 **且** 绑定当前两类 revision **且** summary_hash 非空
  **且** `binding_hash` 重算一致；
- 旧 confirmation 永远保留（`get_confirmation` 可读），只是不再有效。

**状态迁移**：`transition_state` 在写事务内"读当前状态 → 查表 → UPDATE"；
目标状态与当前状态相同时返回成功且零写入（幂等重复请求）；其余非法即抛
`InvalidStateTransitionError`（回滚，不留任何写入）。

**Artifact 信封**：`append_prompt_artifact` / `append_generation_artifact` / `append_feedback_result` /
`append_realization_state` 校验 refs 必填键齐全、无未知键、值非空，并额外校验被引用行属于**同一会话**
（`ref_session_mismatch`）；`get_*` / `list_generation_artifacts` / `get_current_realization_state`
返回 `StoredArtifact`（refs 由 `sort_keys` JSON 稳定往返）。

**pending question**：`save_pending_question` 幂等更新当前指针，`clear_pending_question` 置空。

### 5. `visual_intent_agent/persistence/__init__.py`

再导出 9 个公开名（见「公开接口」）；不导出内部工具（`apply_migrations` 等从
`visual_intent_agent.persistence.repository` 导入，供迁移测试/诊断）。

### 6. 测试 `tests/persistence/`（174 个用例，全部 `tmp_path` 临时 SQLite）

| 文件 | 用例 | 内容 |
|---|---|---|
| `test_persistence_state_machine.py` | 63 | 7 状态、10 条迁移逐条可落库、全部 32 条非法组合被拒且零写入、同状态幂等无操作、`COMPLETED` 终态、缺会话 |
| `test_persistence_records.py` | 10 | 三模型字段面精确、frozen/extra、naive 时间戳拒绝、UTC 归一、JSON `+00:00` |
| `test_persistence_schema.py` | 10 | 恰好 9 表、无 `knowledge_bundles`、`user_version=1`、FK 开启、迁移幂等、拒绝未来版本、append-only 源码级断言 |
| `test_persistence_revisions.py` | 15 | 新 revision 不覆盖旧、父链、重复/断链/跨会话拒绝、deltas 往返、双会话隔离、消息顺序 |
| `test_persistence_confirmation.py` | 14 | 绑定有效、intent/execution 变更后失效、旧记录可审计、非当前 revision 保存被拒、篡改 hash 失效 |
| `test_persistence_transactions.py` | 7 | 触发器故障注入下 INSERT+指针 UPDATE 整体回滚、连接可复用、重开库恢复、多语句原子性 |
| `test_persistence_artifacts.py` | 15 | 四类信封往返、FK 缺失拒绝、refs 缺键/未知键/跨会话拒绝、重复 ID、列表与"当前"语义、pending question |
| `test_persistence_idempotency.py` | 9 | 重复请求显式拒绝；同状态迁移幂等无操作；4 写并发同名消息 / 并发迁移 / 并发子 revision 各恰一胜者 |
| `test_persistence_public_api.py` | 22 | 再导出、`isinstance(repo, Repository)`、协议/实现参数名一致、干净子进程依赖边界、code 命名空间、无凭据 |
| `test_persistence_scenarios.py` | 9 | 任务书「必测场景」9 条各一个显式端到端测试 |

`tests/persistence/persistence_helpers.py` 放确定性工厂与场景装配；`tests/persistence/conftest.py`
只放 `db_path` / `repo` fixture（原因见「已知限制 1」）。

## 变更文件

新增（**未修改任何只读或既有文件**）：

- `visual_intent_agent/persistence/state_machine.py`
- `visual_intent_agent/persistence/records.py`
- `visual_intent_agent/persistence/repository.py`
- `visual_intent_agent/persistence/schema.sql`
- `visual_intent_agent/persistence/__init__.py`（原为空文件，现再导出公开面）
- `tests/persistence/conftest.py`、`persistence_helpers.py`
- `tests/persistence/test_persistence_{state_machine,records,schema,revisions,confirmation,transactions,artifacts,idempotency,public_api,scenarios}.py`
- `docs/handoffs/step_04_handoff.md`（本文件）

未触碰：MVP v0.2 任务书、任务索引与 Agent 派发提示词、`api.md`、`docs/ARCHITECTURE.md`、
`visual_intent_agent/domain/**`、`validation/**`、`policy/**`、`providers/**`、`intent_engine/**`、
`config.py`、`tests/conftest.py`、`tests/test_sanity.py`、`tests/domain/**`、`tests/validation/**`、
`tests/policy/**`、`tests/providers/**`、`tests/intent_engine/**`、`tests/fixtures/**`、
`pyproject.toml`、`uv.lock`、`.env`。**无新增依赖**（只用 stdlib `sqlite3` + pydantic）。

## 公开接口

```python
from visual_intent_agent.persistence import (
    Repository, SQLiteRepository, RepositoryError,
    WorkflowState, ALLOWED_TRANSITIONS, InvalidStateTransitionError,
    ConfirmationRecord, SessionSnapshot, StoredArtifact,
)
```

### Repository 方法（业务层唯一依赖面）

```python
class Repository(Protocol):
    # 会话与消息
    def create_session(self, session_id: str) -> None: ...                       # 初始状态 UNDERSTANDING
    def append_message(self, session_id: str, message_id: str, role: str, content: str, created_at: datetime) -> None: ...
    # revision：只增不改（无 UPDATE）
    def append_intent_revision(self, revision: IntentRevision) -> None: ...
    def append_execution_revision(self, revision: ExecutionRevision) -> None: ...
    # 确认
    def save_confirmation(self, record: ConfirmationRecord) -> None: ...
    def is_confirmation_valid(self, confirmation_id: str) -> bool: ...
    # 读取
    def get_current_session_snapshot(self, session_id: str) -> SessionSnapshot: ...
    def get_intent_revision(self, intent_revision_id: str) -> IntentRevision: ...
    def get_execution_revision(self, execution_revision_id: str) -> ExecutionRevision: ...   # ← 最小修订新增（见下）
    def get_confirmation(self, confirmation_id: str) -> ConfirmationRecord: ...              # ← 最小修订新增（见下）
    # 状态机（非法迁移抛 InvalidStateTransitionError，不落库）
    def transition_state(self, session_id: str, to_state: WorkflowState) -> None: ...
    # pending question（payload 序列化/反序列化归 Step 06）
    def save_pending_question(self, session_id: str, question_id: str, payload: str) -> None: ...
    def clear_pending_question(self, session_id: str) -> None: ...
    # Artifact 信封（refs 必填键冻结；payload 归各 Step 模型）
    def append_prompt_artifact(self, prompt_artifact_id: str, session_id: str, refs: dict[str, str], payload: str) -> None: ...
    def get_prompt_artifact(self, prompt_artifact_id: str) -> StoredArtifact: ...
    def append_generation_artifact(self, generation_id: str, session_id: str, refs: dict[str, str], payload: str) -> None: ...
    def get_generation_artifact(self, generation_id: str) -> StoredArtifact: ...
    def list_generation_artifacts(self, session_id: str) -> list[StoredArtifact]: ...
    def append_feedback_result(self, feedback_id: str, session_id: str, refs: dict[str, str], payload: str) -> None: ...
    def append_realization_state(self, realization_id: str, session_id: str, refs: dict[str, str], payload: str) -> None: ...
    def get_current_realization_state(self, session_id: str) -> StoredArtifact | None: ...
```

`SQLiteRepository` 另有资源方法 `close()` 与只读属性 `connection`（诊断/测试用 `PRAGMA`）。
`Repository` 是 `@runtime_checkable` Protocol，`isinstance(SQLiteRepository(path), Repository) is True`。

### 最小修订提案（additive，已实现，等架构方追认）

架构冻结表缺少两个只读 getter，而任务书自身与下游步骤都需要：

1. `get_execution_revision(execution_revision_id) -> ExecutionRevision`
   —— Step 07 `PromptEngine.compile` 冻结流程要"读当前 Intent/Execution revision"（target_model /
   output_size），冻结表只有 intent 侧 getter；没有它 Step 07 只能绕过协议。
2. `get_confirmation(confirmation_id) -> ConfirmationRecord`
   —— 任务书「必测场景：旧确认记录仍可审计」需要读回历史确认；`is_confirmation_valid` 只返回 bool。

两者均为**新增只读方法**，不修改任何既有签名、不改变任何写入语义；如架构方否决，删除后
冻结表内的其余 21 个方法不受影响（Step 06 的确认流程不依赖它们）。

3. **（状态表缺口，未自行扩充）** Step 06 冻结流程要求"用户消息 → 状态进入 `UNDERSTANDING`"，
   但冻结的 10 条迁移**不含 `WAITING_CONFIRMATION → UNDERSTANDING`**。因此在确认页收到一条
   修改意见时，Step 06 会撞上非法迁移。建议架构方二选一：
   (a) 将 `(WAITING_CONFIRMATION, UNDERSTANDING)` 加入 `ALLOWED_TRANSITIONS`（变为 11 条，
   同时更新 ARCHITECTURE.md 4 的"恰好 10 条"表述）；或
   (b) 明确 Step 06 在 `WAITING_CONFIRMATION` 时拒绝新消息（必须先把当前确认确认掉或另开会话）。
   本步严格遵守冻结表（保持 10 条），只把"与当前状态相同"的调用解释为幂等重复请求（见上文），
   因为该调用在冻结流程中是每轮无条件的。

### 本步新增 Issue/Error code（命名空间 `persistence.*`）

| code | 承载 | 触发条件 |
|---|---|---|
| `persistence.session_not_found` | `RepositoryError` | 会话不存在（读/写/迁移） |
| `persistence.session_exists` | `RepositoryError` | `create_session` 重复 |
| `persistence.duplicate_id` | `RepositoryError` | message/revision/confirmation/artifact 主键重复 |
| `persistence.stale_revision` | `RepositoryError` | revision 的 parent ≠ 当前 head；确认未绑定当前 revision |
| `persistence.foreign_key_violation` | `RepositoryError` | refs / 外键指向不存在的记录（含显式预检） |
| `persistence.ref_session_mismatch` | `RepositoryError` | 被引用记录属于别的会话 |
| `persistence.invalid_refs` | `RepositoryError` | refs 缺必填键 / 含未知键 |
| `persistence.invalid_field` | `RepositoryError` | 必填字符串为空 / payload 非 str / naive 时间戳 |
| `persistence.invalid_summary_hash` | `RepositoryError` | `summary_hash` 为空白 |
| `persistence.integrity_error` | `RepositoryError` | 其他 SQLite 约束失败（翻译） |
| `persistence.database_error` | `RepositoryError` | SQLite 低层错误 / FK 未能开启 |
| `persistence.schema_too_new` | `RepositoryError` | 库 `user_version` 高于本代码支持版本 |
| `persistence.artifact_not_found` | `RepositoryError` | `get_prompt_artifact` / `get_generation_artifact` 缺失 |
| `persistence.intent_revision_not_found` | `RepositoryError` | `get_intent_revision` 缺失 |
| `persistence.execution_revision_not_found` | `RepositoryError` | `get_execution_revision` 缺失 |
| `persistence.confirmation_not_found` | `RepositoryError` | `get_confirmation` 缺失 |
| `persistence.invalid_state_transition` | `InvalidStateTransitionError.code` | 非法状态迁移（不落库） |

## 测试命令与结果

```text
$ cd /home/change/projects/image_system

# Step 04 范围
$ uv run pytest tests/persistence -q
174 passed in 0.62s

# 默认全量（含既有 domain/validation/policy/sanity 与并行 Step 05 的 providers/intent_engine）
$ uv run pytest -q
1384 passed, 2 deselected in 3.08s        # 2 deselected = @pytest.mark.smoke（默认离线）

# 重复运行稳定 + 三个 PYTHONHASHSEED 全绿
$ for i in 1..5: uv run pytest tests/persistence -q   -> 174 passed（每次）
$ PYTHONHASHSEED=0/7/424242 uv run pytest tests/persistence -q -> 174 passed（各）

# 任务书要求的范围验证（见「已知限制 1」：字面多目录参数有上游 conftest 冲突，按目录等价执行）
$ uv run pytest tests/persistence tests/domain tests/test_sanity.py -q   -> 322 passed
$ uv run pytest tests/validation -q                                      -> 684 passed
$ uv run pytest tests/policy -q                                          -> 240 passed
$ uv run pytest tests/persistence tests/policy -q                        -> 414 passed
```

零网络访问；`test_persistence_public_api.py` 在干净子进程断言
`visual_intent_agent.persistence` 只加载自身 + `visual_intent_agent.domain`（不加载
`config`/`providers`/`intent_engine`/`validation`/`policy`/`httpx`/`openai` 等），
并断言源码无 `httpx`/`openai`/`random`/`uuid4`/`sk-`/`api_key`。

### 任务书「必测场景」→ 测试映射（9/9，均在 `test_persistence_scenarios.py` 有显式用例）

1. 新 revision 不覆盖旧 revision → `test_scenario_1_...`（并见 `test_persistence_revisions.py::test_new_revision_does_not_overwrite_the_old_one`）；
2. 父 revision 链正确 → `test_scenario_2_...`（`[None, irev_0001, irev_0002]` + 断链拒绝）；
3. 旧确认在 Intent 修改后无效 → `test_scenario_3_...`；
4. 旧确认记录仍可审计 → `test_scenario_4_...`（`get_confirmation` 逐字段读回旧记录）；
5. execution revision 改变后也要求重新确认 → `test_scenario_5_...`；
6. 非法状态迁移被拒绝 → `test_scenario_6_...`（并在 state_machine 测试中对 32 条非法组合参数化）；
7. 写入中途失败时事务回滚 → `test_scenario_7_...`（SQLite 触发器注入 UPDATE 失败，断言指针与行均回滚）；
8. 重开数据库后 session snapshot 可恢复 → `test_scenario_8_...`；
9. Feedback / Generation 的外键不能指向不存在的记录 → `test_scenario_9_...`（`foreign_key_violation`）。

## 已知限制

1. **字面多目录参数下 `uv run pytest tests/persistence tests/domain tests/validation tests/policy tests/test_sanity.py -q`
   会在收集阶段报 5 个 `tests/validation/*` 的 ImportError —— 这是上游既有测试基础设施问题，与本步无关**：
   `tests/validation` 与 `tests/policy` 的 conftest.py 都使用 `from conftest import ...`，pytest 9.1.1
   在**多个目录参数**下把最后一个目录的 conftest 缓存在 `sys.modules["conftest"]`，于是 validation 的测试
   模块从 `tests/policy/conftest.py` 导入而失败（证据：`ImportError: cannot import name 'TYPICAL_VALUES'
   from 'conftest' (/…/tests/policy/conftest.py)`）。该命令在 Step 04 之前就已失败
   （`uv run pytest tests/validation tests/policy -q` 同样失败），修复需要改 `tests/validation/**` 或
   `pyproject.toml`，均在本步**禁止触碰**范围。Step 04 已把自己的 helper 移出 `conftest` 命名
   （`persistence_helpers.py`），因此 persistence 目录在任意参数组合下都能独立收集；
   等价的范围验证见上方命令（322 + 684 + 240，全绿），默认全量 `uv run pytest -q` 亦全绿。
2. **`is_confirmation_valid` 中的"hash 匹配"语义**：Step 04 无法重算 Step 06 的 summary hash
   （`compute_summary_hash` 需要 `ConfirmationSummary`，属 Step 06）。因此 Step 04 的 hash 校验落为
   "记录存在 + 绑定当前两类 revision + `summary_hash` 非空 + 行内 `binding_hash` 与
   `(intent_revision_id, execution_revision_id, summary_hash)` 重算一致（防篡改/串接）"。
   真实 summary 的重算与比对由 Step 06 `confirm_current_intent` 在 save 前完成（架构冻结）。
3. **`save_confirmation` 只接受绑定当前 revision 的确认**：绑定旧 revision 的写入被
   `persistence.stale_revision` 拒绝（防止"出生即无效"的确认落库）。历史确认保留由"先确认、后修改
   revision"保证。
4. **revision 必须接在当前 head 之后**：`parent_revision_id` 恒等于 `sessions` 的当前指针
   （genesis 用 `None`）。因此不支持从历史节点分叉的 DAG 历史；这也是最小并发保护（两个并发子 revision
   只有一个能赢，另一个 `stale_revision`）。
5. **refs 采用冻结键的严格模式**：缺键或出现未冻结键都报 `persistence.invalid_refs`。每个 ref 键都有
   对应的真实外键列，因此新增 ref 键 = 一次显式 schema 迁移（`LATEST_SCHEMA_USER_VERSION` +1），
   这是为了不让"无外键约束的自由 ref"静默存在。
6. **跨会话 ref 被拒绝**（`persistence.ref_session_mismatch`）：外键只保证行存在，Step 04 额外保证
   被引用的 revision/confirmation/prompt/generation 属于同一 `session_id`。
7. **重复请求不做幂等 no-op 写，只有同状态迁移例外**：重放同一 `create_session` / `append_message` /
   revision / confirmation / artifact 一律显式报错（`session_exists` / `duplicate_id`）；同状态
   `transition_state` 返回成功且零写入（理由见「完成内容 2」与「最小修订提案 3」）。这是"最小幂等保护"：
   绝不产生第二行、绝不静默吞掉调用错误；调用方（Step 06）应把前者当作可观察失败处理。
   并发保护靠 `BEGIN IMMEDIATE` + 主键 + head 校验。
8. **`payload` 只做类型检查（str），不校验 JSON 合法性**：07/08/09 的模型序列化归各步；
   唯一例外是 `summary_hash` 非空校验（见限制 2）。
9. **`sessions.latest_confirmation_id` 始终指向最近一次确认（即使它已失效）**："有效"永远通过
   `is_confirmation_valid` 派生，而不是靠指针；这样旧确认不会被覆盖删除。
10. **`messages` 无编辑/删除路径**，读取顺序 = rowid（插入顺序）；`message_ids` 是 `tuple`，
    以匹配 `SessionSnapshot` 冻结字段类型。
11. **`get_current_realization_state` / `list_generation_artifacts` 对未知会话抛
    `session_not_found`**（而非返回 `None`/`[]`）：`None` 只表示"该会话还没有 realization"。
12. **SQLite 写锁超时固定 10s**（`_BUSY_TIMEOUT_SECONDS`）：不新增配置项（架构未冻结该项）；
    超时会被翻译为 `persistence.database_error`。
13. **未实现（属后续步骤）**：Workflow/用例编排、PendingQuestion 的 payload 模型与序列化、
    PromptEngine、生成、Feedback、Realization 业务语义、Web API、Event Sourcing、异步队列。

## 对下一步的输入

### Step 06（澄清、确认与 Workflow）

```python
from visual_intent_agent.persistence import SQLiteRepository, ConfirmationRecord, WorkflowState
repo = SQLiteRepository(settings.db_path)     # 测试用 tmp_path
```

- 创建会话：`repo.create_session(new_id("ses"))` → 初始状态 `UNDERSTANDING`；随后
  `repo.append_execution_revision(ExecutionRevision(execution_revision_id=new_id("erev"),
  session_id=..., parent_revision_id=None, target_model=settings.image_model))`。
- `submit_message`：`repo.append_message(sid, new_id("msg"), "user", text, utc_now())`
  → `repo.transition_state(sid, WorkflowState.UNDERSTANDING)`。**可以无条件调用**：会话已是
  `UNDERSTANDING` 时是幂等无操作，从 `WAITING_CLARIFICATION` / `WAITING_REVIEW` 回来是合法迁移。
  但注意 `WAITING_CONFIRMATION → UNDERSTANDING` **不在**冻结迁移表内（见「最小修订提案 3」）：
  在确认页收到消息时当前只能拒绝或先确认。
  → 有新 delta 时 `append_intent_revision(IntentRevision(..., parent_revision_id=快照.current_intent_revision_id))`
  → 有问题：`save_pending_question(sid, qst_id, payload_str)` + `transition_state(sid, "WAITING_CLARIFICATION")`；
  ready：`transition_state(sid, "WAITING_CONFIRMATION")`。
- `get_session`：`repo.get_current_session_snapshot(sid)`；`message_ids` 是 `tuple`。
- `confirm_current_intent`：重算 `compute_summary_hash`，构造 `ConfirmationRecord`
  （`summary_hash` 必非空、必须绑定当前两类 revision），`repo.save_confirmation(record)`；
  之后用 `repo.is_confirmation_valid(record.confirmation_id)` 复核。
- pending question 的 payload 是 `str`：序列化/反序列化（`PendingQuestion` ↔ JSON）归 Step 06。
- 临时 SQLite fixture：`tests/persistence/conftest.py` 的 `db_path`/`repo` 模式可直接复制
  （`SQLiteRepository(tmp_path / "x.db")`，用完 `close()`）。

### Step 07（PromptEngine）

- 复核确认：`repo.is_confirmation_valid(request.confirmation_id)`（False → `prompt.no_valid_confirmation`）。
- 读当前上下文：`snap = repo.get_current_session_snapshot(sid)` → `snap.current_intent_revision_id` /
  `snap.current_execution_revision_id`；`repo.get_intent_revision(...)`、
  **`repo.get_execution_revision(...)`**（本步新增的只读方法，取 `target_model` / `output_size`）。
- Realization：`repo.get_current_realization_state(sid) -> StoredArtifact | None`；
  新状态 `repo.append_realization_state(new_id("rlz"), sid, {"based_on_intent_revision_id": irev_id}, payload_json)`
  （payload = `RealizationState.model_dump_json()`；历史不覆盖，失效产生新 state）。
- Prompt：`repo.append_prompt_artifact(new_id("pra"), sid, {"intent_revision_id": irev_id,
  "confirmation_id": cnf_id}, prompt_artifact.model_dump_json())`；
  读回 `repo.get_prompt_artifact(id).payload` 后 `PromptArtifact.model_validate_json(...)`。

### Step 08（图像生成闭环）

- 门禁：快照必须 `WAITING_CONFIRMATION` + `is_confirmation_valid`；`transition_state(sid, "GENERATING")`。
- 落库：`repo.append_generation_artifact(new_id("gen"), sid, {"prompt_artifact_id": pra_id},
  generation_artifact.model_dump_json())`；成功 `transition_state(sid, "WAITING_REVIEW")`；
  `ProviderError` → `transition_state(sid, "FAILED")`（不得伪造 Artifact）。
- retry：`snap.workflow_state is WorkflowState.FAILED` → `get_generation_artifact(old_id)` 取
  `refs["prompt_artifact_id"]` 复用同一 PromptArtifact（不重新编译）→
  `transition_state(sid, "GENERATING")` → 新 `generation_id` 落新行。
- 历史：`repo.list_generation_artifacts(sid)`（按 created_at, rowid 升序）。

### Step 09（Feedback / Realization / Review）

- `repo.append_feedback_result(new_id("fbk"), sid, {"generation_id": gen_id},
  feedback_result.model_dump_json())`（外键必须指向存在的 generation，否则 `foreign_key_violation`）。
- 反馈后的 revise 路径：`append_intent_revision`（parent = 当前 head）→ 重新确认 →
  `append_realization_state`（`based_on_intent_revision_id`）→ 再生成。
- accept：`transition_state(sid, "COMPLETED")`（仅从 `WAITING_REVIEW` 可达）；
  clarify：`WAITING_REVIEW` **没有**直达 `WAITING_CLARIFICATION` 的冻结迁移，按两步走
  `transition_state(sid, "UNDERSTANDING")` → `save_pending_question(...)` →
  `transition_state(sid, "WAITING_CLARIFICATION")`（两步都在冻结表内）。

### 给 Step 06～09 的通用提醒

- 所有写操作在事务中；失败回滚后连接仍可用，可直接重试。
- 重复 ID / 过期 parent / 非法状态 / 外键指向不存在记录都是**可观察异常**（`RepositoryError.code`,
  `InvalidStateTransitionError.code`），不会静默吞掉；需要幂等语义时由调用方处理。
- ID 前缀：`ses`/`msg`/`irev`/`erev`/`cnf`/`qst`/`pra`/`gen`/`fbk`/`rlz`（ARCHITECTURE.md 5.1）。
- 时间戳一律 `domain.identifiers.utc_now()`；写入的 naive datetime 会被 `persistence.invalid_field` 拒绝。

## 验收条件逐条核对

| # | 任务书验收条件 | 核对结果 |
|---|---|---|
| 1 | 历史数据不可覆盖 | 是：`intent_revisions` / `execution_revisions` / `confirmations` / 四张 Artifact 表在源码中**没有任何 UPDATE**（`test_revision_and_artifact_tables_have_no_update_path` 静态断言 + 运行时逐值快照断言）；只有 `sessions` 保存当前指针；parent 链可完整追溯 |
| 2 | Confirmation 有严格 revision 绑定 | 是：`ConfirmationRecord` 显式绑定 intent_revision + execution_revision + summary_hash；保存时必须是当前两类 revision，读回时 `is_confirmation_valid` 逐项校验（含 binding hash 完整性）；intent 或 execution revision 任一变化都使旧确认失效，旧记录仍可 `get_confirmation` 审计 |
| 3 | 非法状态迁移无法落库 | 是：`transition_state` 在写事务内查 `ALLOWED_TRANSITIONS`，非法（含 32 条非法组合、未知状态、`COMPLETED` 之后）抛 `InvalidStateTransitionError` 并回滚；参数化测试断言快照逐字段不变 |
| 4 | 测试使用 SQLite 并可重复运行 | 是：全部 174 个用例用 `tmp_path` 临时 SQLite（含 `:memory:` 一个），零网络、零外部服务；连续 5 次与 3 个 `PYTHONHASHSEED` 下均 174 passed |
| 5 | 无 Event Sourcing、Milvus 或 Provider 依赖 | 是：只用 stdlib `sqlite3`（快照式存储，无事件表/回放），无 Milvus/向量库，干净子进程断言不加载 `providers`/`intent_engine`/`httpx`/`openai`；无新增依赖 |

## 是否满足验收条件

是。5 条验收条件逐条通过；9 条「必测场景」全部有显式测试映射；`uv run pytest tests/persistence -q`
→ **174 passed**，默认全量 `uv run pytest -q` → **1384 passed, 2 deselected**（含并行 Step 05 的
providers/intent_engine 与既有 domain/validation/policy/sanity，无失败）；未实现任何后续步骤能力，
未修改任何只读或既有文件，未新增依赖，源码/测试/schema/handoff 中无明文 key。
唯一未能按字面执行的验证命令是多目录参数的范围命令，原因是上游 `tests/validation` 与 `tests/policy`
的 conftest basename 冲突（本步之前即存在，修复越界）——已给出等价的全绿验证命令（见「测试命令与结果」）。
