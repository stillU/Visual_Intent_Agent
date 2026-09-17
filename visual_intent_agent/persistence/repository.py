"""Step 04 Repository 实现：SQLite 持久化、revision 快照与状态迁移。

设计约束（ARCHITECTURE.md 4「Step 04」、5.1～5.7）：

- 只用标准库 `sqlite3`，**不引入 ORM / Event Sourcing / 异步队列**；
- `Repository(Protocol)` 隔离 SQLite 细节，业务层只依赖协议；
- revision / Confirmation / Artifact **只有 INSERT 路径**，没有 UPDATE：
  历史不可覆盖；
- `sessions` 保存"当前指针"（current intent/execution revision、latest confirmation、
  pending question），由本模块在**同一事务**内推进；
- 所有写操作 `BEGIN IMMEDIATE` + 失败 ROLLBACK；读快照使用 `BEGIN`（deferred）
  保证一致视图；
- 最小幂等/并发保护：主键 + 唯一约束拒绝重复写入；revision 必须接在当前 head
  之后（乐观并发）；状态迁移的"读当前状态 → 校验 → 写"整体在写事务内完成。

对象字段与 SQLite 的映射：revision 整份 `model_dump_json()` 存入 `revision_json`，
读回用 `model_validate_json`，保证逐值（含 datetime tz、deltas、pinned_paths）一致。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from visual_intent_agent.domain.identifiers import utc_now
from visual_intent_agent.domain.revision import ExecutionRevision, IntentRevision

from .records import ConfirmationRecord, SessionSnapshot, StoredArtifact
from .state_machine import ALLOWED_TRANSITIONS, InvalidStateTransitionError, WorkflowState

#: 当前 schema 的 `PRAGMA user_version`。新增迁移 = 递增并追加步骤。
#: v2 = v1 的 9 张表 + v0.4 Step 03 的 append-only `knowledge_bundles`。
LATEST_SCHEMA_USER_VERSION = 2

_SCHEMA_FILE = Path(__file__).with_name("schema.sql")

#: SQLite 写锁等待时间（秒）：并发写在此窗口内串行化，超时则报 database_error。
_BUSY_TIMEOUT_SECONDS = 10.0

#: refs 的冻结必填键（ARCHITECTURE.md 4）：每个 ref 键都对应一个真实外键列。
#: 三元组 = (ref 键/列名, 被引用表, 被引用主键列)。
_ARTIFACT_REFS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "prompt_artifacts": (
        ("intent_revision_id", "intent_revisions", "intent_revision_id"),
        ("confirmation_id", "confirmations", "confirmation_id"),
    ),
    "generation_artifacts": (("prompt_artifact_id", "prompt_artifacts", "prompt_artifact_id"),),
    "feedback_results": (("generation_id", "generation_artifacts", "generation_id"),),
    "realization_states": (
        ("based_on_intent_revision_id", "intent_revisions", "intent_revision_id"),
    ),
    "knowledge_bundles": (
        ("intent_revision_id", "intent_revisions", "intent_revision_id"),
        ("execution_revision_id", "execution_revisions", "execution_revision_id"),
        ("confirmation_id", "confirmations", "confirmation_id"),
    ),
}

#: Artifact 表 → 主键列名。
_ARTIFACT_PK: dict[str, str] = {
    "prompt_artifacts": "prompt_artifact_id",
    "generation_artifacts": "generation_id",
    "feedback_results": "feedback_id",
    "realization_states": "realization_id",
    "knowledge_bundles": "bundle_id",
}


class RepositoryError(Exception):
    """持久化层的程序级失败（带 `persistence.*` 命名空间的 `.code`）。

    业务上的"确认无效"不是异常，而是 `is_confirmation_valid(...) is False`。
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _iso(value: datetime) -> str:
    """统一 UTC ISO 8601（带 +00:00）；naive 输入视为调用方错误。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise RepositoryError("persistence.invalid_field", "timestamp must be timezone-aware UTC")
    return value.astimezone(timezone.utc).isoformat()


def _now_iso() -> str:
    return _iso(utc_now())


def _require_non_empty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RepositoryError("persistence.invalid_field", f"{field} must be a non-empty string")
    return value


def _validate_refs(table: str, refs: dict[str, str]) -> dict[str, str]:
    """校验 Artifact 信封的 refs：必填键齐全、无未知键、值非空。

    未知 ref 键被显式拒绝：每个 ref 键都必须有对应的外键列，否则完整性约束
    就会静默缺失。新增 ref 键 = 一次 schema 迁移（`LATEST_SCHEMA_USER_VERSION`）。
    """
    if not isinstance(refs, dict):
        raise RepositoryError("persistence.invalid_refs", f"{table} refs must be a dict[str, str]")
    required = tuple(ref for ref, _, _ in _ARTIFACT_REFS[table])
    missing = sorted(set(required) - set(refs))
    if missing:
        raise RepositoryError(
            "persistence.invalid_refs",
            f"{table} refs is missing required keys: {missing}; required={list(required)}",
        )
    unknown = sorted(set(refs) - set(required))
    if unknown:
        raise RepositoryError(
            "persistence.invalid_refs",
            f"{table} refs contains unsupported keys: {unknown}; "
            "adding a ref key requires an explicit schema migration",
        )
    for ref, value in refs.items():
        _require_non_empty(value, f"{table}.refs[{ref!r}]")
    return dict(refs)


def _binding_hash(intent_revision_id: str, execution_revision_id: str, summary_hash: str) -> str:
    """Confirmation 双 revision + summary hash 的完整性摘要（见 schema.sql）。"""
    parts = (intent_revision_id, execution_revision_id, summary_hash)
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _json_dump(value: object) -> str:
    """确定性 JSON（sort_keys），保证同一 refs 逐字节一致。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _require_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        raise RepositoryError("persistence.session_not_found", f"session {session_id!r} does not exist")
    return row


def _ref_session(
    conn: sqlite3.Connection,
    table: str,
    pk_column: str,
    value: str,
    *,
    ref_label: str,
) -> str:
    """读取被引用记录的 session_id；不存在直接抛外键类错误。"""
    row = conn.execute(
        f"SELECT session_id FROM {table} WHERE {pk_column} = ?", (value,)
    ).fetchone()
    if row is None:
        raise RepositoryError(
            "persistence.foreign_key_violation",
            f"{ref_label} references missing {table} row {value!r}",
        )
    return str(row["session_id"])


def _require_same_session(
    conn: sqlite3.Connection,
    table: str,
    pk_column: str,
    value: str,
    session_id: str,
    *,
    ref_label: str,
) -> None:
    owner = _ref_session(conn, table, pk_column, value, ref_label=ref_label)
    if owner != session_id:
        raise RepositoryError(
            "persistence.ref_session_mismatch",
            f"{ref_label} belongs to session {owner!r}, not {session_id!r}",
        )


def _require_absent(
    conn: sqlite3.Connection, table: str, pk_column: str, value: str, *, label: str
) -> None:
    """重复写入的显式前置检查（并发下仍有主键约束兜底）。"""
    if conn.execute(f"SELECT 1 FROM {table} WHERE {pk_column} = ?", (value,)).fetchone():
        raise RepositoryError("persistence.duplicate_id", f"{label} {value!r} already exists")


def _translate_sqlite_error(exc: sqlite3.IntegrityError) -> RepositoryError:
    text = str(exc).lower()
    if "foreign key" in text:
        return RepositoryError("persistence.foreign_key_violation", f"foreign key violation: {exc}")
    if "unique" in text or "primary key" in text:
        return RepositoryError("persistence.duplicate_id", f"duplicate id: {exc}")
    return RepositoryError("persistence.integrity_error", f"integrity error: {exc}")


# ---------------------------------------------------------------------------
# 最小迁移机制
# ---------------------------------------------------------------------------


def apply_migrations(conn: sqlite3.Connection) -> int:
    """按 `PRAGMA user_version` 应用最小迁移，返回应用后的版本。

    - `user_version == 0` → 执行当前 DDL（v2，10 张表）并置 2；
    - `user_version == 1` → 幂等补建 v2 新增的 `knowledge_bundles`（及其索引）；
      schema.sql 的其余语句全部 `IF NOT EXISTS`，在 v1 库上是 no-op，**不删表、
      不改写旧 payload/refs**；随后置 2；
    - `user_version == LATEST_SCHEMA_USER_VERSION` → 不做任何事；
    - `user_version > LATEST_SCHEMA_USER_VERSION` → 显式拒绝（新库被旧代码打开）。

    v0 与 v1 都通过重放同一份幂等 DDL 升级：这是本模块自 Step 04 起的既有约定
    （见 schema.sql 头部注释），因此新增迁移只需向 schema.sql 追加 `IF NOT EXISTS`
    对象并递增 `LATEST_SCHEMA_USER_VERSION`，不需要改写为迁移脚本。
    """
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version > LATEST_SCHEMA_USER_VERSION:
        raise RepositoryError(
            "persistence.schema_too_new",
            f"database user_version={version} is newer than supported "
            f"{LATEST_SCHEMA_USER_VERSION}; refusing to open",
        )
    if version == LATEST_SCHEMA_USER_VERSION:
        return version
    conn.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.execute(f"PRAGMA user_version = {LATEST_SCHEMA_USER_VERSION}")
    return LATEST_SCHEMA_USER_VERSION


# ---------------------------------------------------------------------------
# Repository 协议（业务层唯一依赖的公开面）
# ---------------------------------------------------------------------------


@runtime_checkable
class Repository(Protocol):
    """SQLite 细节的隔离边界（ARCHITECTURE.md 4「Repository 方法冻结」）。"""

    # 会话与消息
    def create_session(self, session_id: str) -> None: ...

    def append_message(
        self,
        session_id: str,
        message_id: str,
        role: str,
        content: str,
        created_at: datetime,
    ) -> None: ...

    # revision：只增不改（无 UPDATE）
    def append_intent_revision(self, revision: IntentRevision) -> None: ...

    def append_execution_revision(self, revision: ExecutionRevision) -> None: ...

    # 确认
    def save_confirmation(self, record: ConfirmationRecord) -> None: ...

    def is_confirmation_valid(self, confirmation_id: str) -> bool: ...

    # 读取
    def get_current_session_snapshot(self, session_id: str) -> SessionSnapshot: ...

    def get_intent_revision(self, intent_revision_id: str) -> IntentRevision: ...

    def get_execution_revision(self, execution_revision_id: str) -> ExecutionRevision: ...

    def get_confirmation(self, confirmation_id: str) -> ConfirmationRecord: ...

    # 状态机（非法迁移抛 InvalidStateTransitionError，不落库）
    def transition_state(self, session_id: str, to_state: WorkflowState) -> None: ...

    # pending question（payload 的序列化/反序列化归 Step 06）
    def save_pending_question(self, session_id: str, question_id: str, payload: str) -> None: ...

    def clear_pending_question(self, session_id: str) -> None: ...

    # Artifact 信封（refs 必填键见 _ARTIFACT_REFS；payload 归各 Step 模型）
    def append_prompt_artifact(
        self, prompt_artifact_id: str, session_id: str, refs: dict[str, str], payload: str
    ) -> None: ...

    def get_prompt_artifact(self, prompt_artifact_id: str) -> StoredArtifact: ...

    def get_latest_prompt_artifact(self, session_id: str) -> StoredArtifact | None: ...

    def append_generation_artifact(
        self, generation_id: str, session_id: str, refs: dict[str, str], payload: str
    ) -> None: ...

    def get_generation_artifact(self, generation_id: str) -> StoredArtifact: ...

    def list_generation_artifacts(self, session_id: str) -> list[StoredArtifact]: ...

    def append_feedback_result(
        self, feedback_id: str, session_id: str, refs: dict[str, str], payload: str
    ) -> None: ...

    def append_realization_state(
        self, realization_id: str, session_id: str, refs: dict[str, str], payload: str
    ) -> None: ...

    def get_current_realization_state(self, session_id: str) -> StoredArtifact | None: ...

    # 知识 Bundle 信封（v0.4 Step 03；refs 精确三键见 _ARTIFACT_REFS，append-only）
    def append_knowledge_bundle(
        self, bundle_id: str, session_id: str, refs: dict[str, str], payload: str
    ) -> None: ...

    def get_knowledge_bundle(self, bundle_id: str) -> StoredArtifact: ...

    def list_knowledge_bundles(self, session_id: str) -> list[StoredArtifact]: ...


# ---------------------------------------------------------------------------
# SQLite 实现
# ---------------------------------------------------------------------------


class SQLiteRepository:
    """`Repository` 的 stdlib-sqlite3 实现。

    构造参数只有 db_path（不接受 Settings）；测试一律传 `tmp_path`。
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path,
            timeout=_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,  # 事务由本模块显式 BEGIN/COMMIT/ROLLBACK 管理
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if int(self._conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise RepositoryError("persistence.database_error", "failed to enable SQLite foreign_keys")
        try:
            apply_migrations(self._conn)
        except RepositoryError:
            self._conn.close()
            raise

    # -- 连接与事务 ---------------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        """底层连接（仅供测试/诊断读取 `PRAGMA` 等元信息，业务代码不要使用）。"""
        return self._conn

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        conn = self._conn
        if conn.in_transaction:  # 已在事务内 → 加入外层事务，不新建边界
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield conn
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        else:
            if conn.in_transaction:
                conn.commit()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        try:
            with self._transaction(write=True) as conn:
                yield conn
        except sqlite3.IntegrityError as exc:
            raise _translate_sqlite_error(exc) from exc
        except sqlite3.Error as exc:
            raise RepositoryError("persistence.database_error", f"SQLite error: {exc}") from exc

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        try:
            with self._transaction(write=False) as conn:
                yield conn
        except sqlite3.Error as exc:
            raise RepositoryError("persistence.database_error", f"SQLite error: {exc}") from exc

    # -- 会话与消息 ---------------------------------------------------------

    def create_session(self, session_id: str) -> None:
        _require_non_empty(session_id, "session_id")
        now = _now_iso()
        with self._write() as conn:
            if conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone():
                raise RepositoryError(
                    "persistence.session_exists", f"session {session_id!r} already exists"
                )
            conn.execute(
                "INSERT INTO sessions (session_id, workflow_state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, WorkflowState.UNDERSTANDING.value, now, now),
            )

    def append_message(
        self,
        session_id: str,
        message_id: str,
        role: str,
        content: str,
        created_at: datetime,
    ) -> None:
        _require_non_empty(session_id, "session_id")
        _require_non_empty(message_id, "message_id")
        _require_non_empty(role, "role")
        if not isinstance(content, str):
            raise RepositoryError("persistence.invalid_field", "content must be a string")
        with self._write() as conn:
            _require_session(conn, session_id)
            conn.execute(
                "INSERT INTO messages (message_id, session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (message_id, session_id, role, content, _iso(created_at)),
            )

    # -- revision（只增不改） ----------------------------------------------

    def append_intent_revision(self, revision: IntentRevision) -> None:
        with self._write() as conn:
            _require_session(conn, revision.session_id)
            _require_absent(
                conn,
                "intent_revisions",
                "intent_revision_id",
                revision.intent_revision_id,
                label="intent revision",
            )
            current = self._current_pointer(conn, revision.session_id, "current_intent_revision_id")
            if revision.parent_revision_id != current:
                raise RepositoryError(
                    "persistence.stale_revision",
                    f"intent revision {revision.intent_revision_id!r} must extend the current head "
                    f"{current!r}, got parent {revision.parent_revision_id!r}",
                )
            try:
                conn.execute(
                    "INSERT INTO intent_revisions "
                    "(intent_revision_id, session_id, parent_revision_id, schema_version, "
                    " revision_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        revision.intent_revision_id,
                        revision.session_id,
                        revision.parent_revision_id,
                        revision.schema_version,
                        revision.model_dump_json(),
                        _iso(revision.created_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise _translate_sqlite_error(exc) from exc
            conn.execute(
                "UPDATE sessions SET current_intent_revision_id = ?, updated_at = ? "
                "WHERE session_id = ?",
                (revision.intent_revision_id, _now_iso(), revision.session_id),
            )

    def append_execution_revision(self, revision: ExecutionRevision) -> None:
        with self._write() as conn:
            _require_session(conn, revision.session_id)
            _require_absent(
                conn,
                "execution_revisions",
                "execution_revision_id",
                revision.execution_revision_id,
                label="execution revision",
            )
            current = self._current_pointer(
                conn, revision.session_id, "current_execution_revision_id"
            )
            if revision.parent_revision_id != current:
                raise RepositoryError(
                    "persistence.stale_revision",
                    f"execution revision {revision.execution_revision_id!r} must extend the current "
                    f"head {current!r}, got parent {revision.parent_revision_id!r}",
                )
            try:
                conn.execute(
                    "INSERT INTO execution_revisions "
                    "(execution_revision_id, session_id, parent_revision_id, schema_version, "
                    " revision_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        revision.execution_revision_id,
                        revision.session_id,
                        revision.parent_revision_id,
                        revision.schema_version,
                        revision.model_dump_json(),
                        _iso(revision.created_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise _translate_sqlite_error(exc) from exc
            conn.execute(
                "UPDATE sessions SET current_execution_revision_id = ?, updated_at = ? "
                "WHERE session_id = ?",
                (revision.execution_revision_id, _now_iso(), revision.session_id),
            )

    # -- 确认 ---------------------------------------------------------------

    def save_confirmation(self, record: ConfirmationRecord) -> None:
        _require_non_empty(record.confirmation_id, "confirmation_id")
        if not isinstance(record.summary_hash, str) or not record.summary_hash.strip():
            raise RepositoryError(
                "persistence.invalid_summary_hash",
                "summary_hash must be a non-empty string (see Step 06 compute_summary_hash)",
            )
        with self._write() as conn:
            _require_session(conn, record.session_id)
            _require_same_session(
                conn,
                "intent_revisions",
                "intent_revision_id",
                record.intent_revision_id,
                record.session_id,
                ref_label="confirmations.intent_revision_id",
            )
            _require_same_session(
                conn,
                "execution_revisions",
                "execution_revision_id",
                record.execution_revision_id,
                record.session_id,
                ref_label="confirmations.execution_revision_id",
            )
            current_intent = self._current_pointer(
                conn, record.session_id, "current_intent_revision_id"
            )
            current_execution = self._current_pointer(
                conn, record.session_id, "current_execution_revision_id"
            )
            if record.intent_revision_id != current_intent:
                raise RepositoryError(
                    "persistence.stale_revision",
                    f"confirmation must bind the current intent revision {current_intent!r}, "
                    f"got {record.intent_revision_id!r}",
                )
            if record.execution_revision_id != current_execution:
                raise RepositoryError(
                    "persistence.stale_revision",
                    f"confirmation must bind the current execution revision {current_execution!r}, "
                    f"got {record.execution_revision_id!r}",
                )
            try:
                conn.execute(
                    "INSERT INTO confirmations "
                    "(confirmation_id, session_id, intent_revision_id, execution_revision_id, "
                    " summary_hash, binding_hash, schema_version, confirmed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.confirmation_id,
                        record.session_id,
                        record.intent_revision_id,
                        record.execution_revision_id,
                        record.summary_hash,
                        _binding_hash(
                            record.intent_revision_id,
                            record.execution_revision_id,
                            record.summary_hash,
                        ),
                        record.schema_version,
                        _iso(record.confirmed_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise _translate_sqlite_error(exc) from exc
            conn.execute(
                "UPDATE sessions SET latest_confirmation_id = ?, updated_at = ? WHERE session_id = ?",
                (record.confirmation_id, _now_iso(), record.session_id),
            )

    def is_confirmation_valid(self, confirmation_id: str) -> bool:
        """有效性 = 记录存在 + 绑定当前两类 revision + summary hash 完整性匹配。"""
        if not isinstance(confirmation_id, str) or not confirmation_id:
            return False
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM confirmations WHERE confirmation_id = ?", (confirmation_id,)
            ).fetchone()
            if row is None:
                return False
            session = conn.execute(
                "SELECT current_intent_revision_id, current_execution_revision_id "
                "FROM sessions WHERE session_id = ?",
                (row["session_id"],),
            ).fetchone()
            if session is None:
                return False
            if row["intent_revision_id"] != session["current_intent_revision_id"]:
                return False
            if row["execution_revision_id"] != session["current_execution_revision_id"]:
                return False
            summary_hash = row["summary_hash"]
            if not isinstance(summary_hash, str) or not summary_hash.strip():
                return False
            expected = _binding_hash(
                row["intent_revision_id"], row["execution_revision_id"], summary_hash
            )
            return str(row["binding_hash"]) == expected

    # -- 读取 ---------------------------------------------------------------

    def get_current_session_snapshot(self, session_id: str) -> SessionSnapshot:
        _require_non_empty(session_id, "session_id")
        with self._read() as conn:
            row = _require_session(conn, session_id)
            message_ids = tuple(
                str(message_row["message_id"])
                for message_row in conn.execute(
                    "SELECT message_id FROM messages WHERE session_id = ? ORDER BY rowid",
                    (session_id,),
                )
            )
            return SessionSnapshot(
                session_id=row["session_id"],
                workflow_state=WorkflowState(row["workflow_state"]),
                current_intent_revision_id=row["current_intent_revision_id"],
                current_execution_revision_id=row["current_execution_revision_id"],
                latest_confirmation_id=row["latest_confirmation_id"],
                pending_question_id=row["pending_question_id"],
                pending_question_payload=row["pending_question_payload"],
                message_ids=message_ids,
            )

    def get_intent_revision(self, intent_revision_id: str) -> IntentRevision:
        _require_non_empty(intent_revision_id, "intent_revision_id")
        with self._read() as conn:
            row = conn.execute(
                "SELECT revision_json FROM intent_revisions WHERE intent_revision_id = ?",
                (intent_revision_id,),
            ).fetchone()
        if row is None:
            raise RepositoryError(
                "persistence.intent_revision_not_found",
                f"intent revision {intent_revision_id!r} does not exist",
            )
        return IntentRevision.model_validate_json(row["revision_json"])

    def get_execution_revision(self, execution_revision_id: str) -> ExecutionRevision:
        _require_non_empty(execution_revision_id, "execution_revision_id")
        with self._read() as conn:
            row = conn.execute(
                "SELECT revision_json FROM execution_revisions WHERE execution_revision_id = ?",
                (execution_revision_id,),
            ).fetchone()
        if row is None:
            raise RepositoryError(
                "persistence.execution_revision_not_found",
                f"execution revision {execution_revision_id!r} does not exist",
            )
        return ExecutionRevision.model_validate_json(row["revision_json"])

    def get_confirmation(self, confirmation_id: str) -> ConfirmationRecord:
        _require_non_empty(confirmation_id, "confirmation_id")
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM confirmations WHERE confirmation_id = ?", (confirmation_id,)
            ).fetchone()
        if row is None:
            raise RepositoryError(
                "persistence.confirmation_not_found",
                f"confirmation {confirmation_id!r} does not exist",
            )
        return ConfirmationRecord(
            schema_version=row["schema_version"],
            confirmation_id=row["confirmation_id"],
            session_id=row["session_id"],
            intent_revision_id=row["intent_revision_id"],
            execution_revision_id=row["execution_revision_id"],
            summary_hash=row["summary_hash"],
            confirmed_at=row["confirmed_at"],
        )

    # -- 状态机 -------------------------------------------------------------

    def transition_state(self, session_id: str, to_state: WorkflowState) -> None:
        _require_non_empty(session_id, "session_id")
        try:
            target = to_state if isinstance(to_state, WorkflowState) else WorkflowState(to_state)
        except ValueError:
            raise InvalidStateTransitionError(
                session_id, None, to_state, reason="unknown workflow state"
            ) from None
        with self._write() as conn:
            row = _require_session(conn, session_id)
            current = WorkflowState(row["workflow_state"])
            if target == current:
                # 幂等重复请求：会话已是目标状态 → 成功且零写入
                # （Step 06 冻结流程每轮用户消息都会调用 "状态进入 UNDERSTANDING"）。
                return
            if (current, target) not in ALLOWED_TRANSITIONS:
                raise InvalidStateTransitionError(
                    session_id,
                    current,
                    target,
                    reason="target state is not reachable from the current state",
                )
            conn.execute(
                "UPDATE sessions SET workflow_state = ?, updated_at = ? WHERE session_id = ?",
                (target.value, _now_iso(), session_id),
            )

    # -- pending question ---------------------------------------------------

    def save_pending_question(self, session_id: str, question_id: str, payload: str) -> None:
        _require_non_empty(session_id, "session_id")
        _require_non_empty(question_id, "question_id")
        if not isinstance(payload, str):
            raise RepositoryError("persistence.invalid_field", "pending question payload must be a string")
        with self._write() as conn:
            _require_session(conn, session_id)
            conn.execute(
                "UPDATE sessions SET pending_question_id = ?, pending_question_payload = ?, "
                "updated_at = ? WHERE session_id = ?",
                (question_id, payload, _now_iso(), session_id),
            )

    def clear_pending_question(self, session_id: str) -> None:
        _require_non_empty(session_id, "session_id")
        with self._write() as conn:
            _require_session(conn, session_id)
            conn.execute(
                "UPDATE sessions SET pending_question_id = NULL, pending_question_payload = NULL, "
                "updated_at = ? WHERE session_id = ?",
                (_now_iso(), session_id),
            )

    # -- Artifact 信封 ------------------------------------------------------

    def append_prompt_artifact(
        self, prompt_artifact_id: str, session_id: str, refs: dict[str, str], payload: str
    ) -> None:
        self._append_artifact("prompt_artifacts", prompt_artifact_id, session_id, refs, payload)

    def get_prompt_artifact(self, prompt_artifact_id: str) -> StoredArtifact:
        return self._get_artifact("prompt_artifacts", prompt_artifact_id)

    def get_latest_prompt_artifact(self, session_id: str) -> StoredArtifact | None:
        """本会话**最新一条** prompt_artifacts 信封；无记录返回 None（Rev.2 只读 getter）。

        查询形态与 `get_current_realization_state` 逐行同构：
        `ORDER BY created_at DESC, rowid DESC LIMIT 1`（同一 `created_at` 用 rowid 定序）。
        会话不存在抛 `persistence.session_not_found`（与既有 getter 一致）。
        """
        _require_non_empty(session_id, "session_id")
        with self._read() as conn:
            _require_session(conn, session_id)
            row = conn.execute(
                "SELECT prompt_artifact_id, session_id, refs_json, payload, created_at "
                "FROM prompt_artifacts WHERE session_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_artifact(row, "prompt_artifact_id")

    def append_generation_artifact(
        self, generation_id: str, session_id: str, refs: dict[str, str], payload: str
    ) -> None:
        self._append_artifact("generation_artifacts", generation_id, session_id, refs, payload)

    def get_generation_artifact(self, generation_id: str) -> StoredArtifact:
        return self._get_artifact("generation_artifacts", generation_id)

    def list_generation_artifacts(self, session_id: str) -> list[StoredArtifact]:
        _require_non_empty(session_id, "session_id")
        with self._read() as conn:
            _require_session(conn, session_id)
            rows = conn.execute(
                "SELECT generation_id, session_id, refs_json, payload, created_at "
                "FROM generation_artifacts WHERE session_id = ? ORDER BY created_at, rowid",
                (session_id,),
            ).fetchall()
            return [self._row_to_artifact(row, "generation_id") for row in rows]

    def append_feedback_result(
        self, feedback_id: str, session_id: str, refs: dict[str, str], payload: str
    ) -> None:
        self._append_artifact("feedback_results", feedback_id, session_id, refs, payload)

    def append_realization_state(
        self, realization_id: str, session_id: str, refs: dict[str, str], payload: str
    ) -> None:
        self._append_artifact("realization_states", realization_id, session_id, refs, payload)

    def get_current_realization_state(self, session_id: str) -> StoredArtifact | None:
        _require_non_empty(session_id, "session_id")
        with self._read() as conn:
            _require_session(conn, session_id)
            row = conn.execute(
                "SELECT realization_id, session_id, refs_json, payload, created_at "
                "FROM realization_states WHERE session_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_artifact(row, "realization_id")

    # -- 知识 Bundle 信封（append-only） -----------------------------------

    def append_knowledge_bundle(
        self, bundle_id: str, session_id: str, refs: dict[str, str], payload: str
    ) -> None:
        """写入一条不可变 KnowledgeBundle 快照；重复 bundle_id 显式拒绝。

        refs 必须是精确三键 `intent_revision_id` / `execution_revision_id` /
        `confirmation_id`，且三条被引用记录都必须存在并属于 `session_id`。
        """
        self._append_artifact("knowledge_bundles", bundle_id, session_id, refs, payload)

    def get_knowledge_bundle(self, bundle_id: str) -> StoredArtifact:
        return self._get_artifact("knowledge_bundles", bundle_id)

    def list_knowledge_bundles(self, session_id: str) -> list[StoredArtifact]:
        """本会话的全部 Bundle，按 `created_at, rowid` 升序（插入顺序稳定）。"""
        _require_non_empty(session_id, "session_id")
        with self._read() as conn:
            _require_session(conn, session_id)
            rows = conn.execute(
                "SELECT bundle_id, session_id, refs_json, payload, created_at "
                "FROM knowledge_bundles WHERE session_id = ? ORDER BY created_at, rowid",
                (session_id,),
            ).fetchall()
            return [self._row_to_artifact(row, "bundle_id") for row in rows]

    # -- 内部辅助 -----------------------------------------------------------

    @staticmethod
    def _current_pointer(conn: sqlite3.Connection, session_id: str, column: str) -> str | None:
        row = conn.execute(
            f"SELECT {column} FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise RepositoryError(
                "persistence.session_not_found", f"session {session_id!r} does not exist"
            )
        value = row[column]
        return None if value is None else str(value)

    def _append_artifact(
        self,
        table: str,
        artifact_id: str,
        session_id: str,
        refs: dict[str, str],
        payload: str,
    ) -> None:
        _require_non_empty(artifact_id, f"{_ARTIFACT_PK[table]}")
        _require_non_empty(session_id, "session_id")
        if not isinstance(payload, str):
            raise RepositoryError("persistence.invalid_field", f"{table}.payload must be a string")
        validated_refs = _validate_refs(table, refs)
        pk_column = _ARTIFACT_PK[table]
        ref_columns = tuple(ref for ref, _, _ in _ARTIFACT_REFS[table])
        columns = (pk_column, "session_id", *ref_columns, "refs_json", "payload", "created_at")
        placeholders = ", ".join("?" for _ in columns)
        with self._write() as conn:
            _require_session(conn, session_id)
            for ref, ref_table, ref_pk in _ARTIFACT_REFS[table]:
                _require_same_session(
                    conn,
                    ref_table,
                    ref_pk,
                    validated_refs[ref],
                    session_id,
                    ref_label=f"{table}.{ref}",
                )
            conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                (
                    artifact_id,
                    session_id,
                    *(validated_refs[ref] for ref in ref_columns),
                    _json_dump(validated_refs),
                    payload,
                    _now_iso(),
                ),
            )

    def _get_artifact(self, table: str, artifact_id: str) -> StoredArtifact:
        pk_column = _ARTIFACT_PK[table]
        _require_non_empty(artifact_id, pk_column)
        with self._read() as conn:
            row = conn.execute(
                f"SELECT {pk_column}, session_id, refs_json, payload, created_at "
                f"FROM {table} WHERE {pk_column} = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise RepositoryError(
                "persistence.artifact_not_found",
                f"{table} row {artifact_id!r} does not exist",
            )
        return self._row_to_artifact(row, pk_column)

    @staticmethod
    def _row_to_artifact(row: sqlite3.Row, pk_column: str) -> StoredArtifact:
        return StoredArtifact(
            artifact_id=row[pk_column],
            session_id=row["session_id"],
            refs=json.loads(row["refs_json"]),
            payload=row["payload"],
            created_at=row["created_at"],
        )


__all__ = ["Repository", "SQLiteRepository", "RepositoryError"]
