"""v1 → v2 迁移测试：真实旧库 fixture、旧 Prompt/Realization 可读、幂等升级。

fixture 是 **v1 DDL 的冻结快照**（`fixtures/schema_v1.sql`，取自 v1 版本
`schema.sql`），用手工构造的旧行验证：升级只新增 `knowledge_bundles`，不删表、
不改写旧 payload/refs，且可重复打开。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from persistence_helpers import FIXED_TIME, make_execution_revision, make_intent_revision

from visual_intent_agent.persistence import SQLiteRepository
from visual_intent_agent.persistence.repository import (
    LATEST_SCHEMA_USER_VERSION,
    apply_migrations,
)

FIXTURES = Path(__file__).with_name("fixtures")
V1_SCHEMA = FIXTURES / "schema_v1.sql"

V1_TABLES = {
    "sessions",
    "messages",
    "intent_revisions",
    "execution_revisions",
    "confirmations",
    "realization_states",
    "prompt_artifacts",
    "generation_artifacts",
    "feedback_results",
}
V2_TABLES = V1_TABLES | {"knowledge_bundles"}

LEGACY_SESSION = "ses_legacy"
LEGACY_INTENT = "irev_legacy"
LEGACY_EXECUTION = "erev_legacy"
LEGACY_CONFIRMATION = "cnf_legacy"
LEGACY_PROMPT = "pra_legacy"
LEGACY_REALIZATION = "rlz_legacy"

LEGACY_PROMPT_PAYLOAD = '{"prompt": "legacy prompt", "parameters": {"size": "1024x1024"}}'
LEGACY_REALIZATION_PAYLOAD = '{"values": [{"path": "lighting.character"}]}'
LEGACY_SUMMARY_HASH = hashlib.sha256(b"legacy-summary").hexdigest()
LEGACY_BINDING_HASH = hashlib.sha256(
    "\x1f".join((LEGACY_INTENT, LEGACY_EXECUTION, LEGACY_SUMMARY_HASH)).encode("utf-8")
).hexdigest()

_PROMPT_REFS = {"intent_revision_id": LEGACY_INTENT, "confirmation_id": LEGACY_CONFIRMATION}
_REALIZATION_REFS = {"based_on_intent_revision_id": LEGACY_INTENT}


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _legacy_intent_revision():
    return make_intent_revision(LEGACY_SESSION, LEGACY_INTENT)


def build_v1_database(db_path: Path) -> None:
    """用冻结的 v1 DDL 建库并写入旧 Prompt/Realization 行。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(V1_SCHEMA.read_text(encoding="utf-8"))
        assert _user_tables(conn) == V1_TABLES
        created_at = FIXED_TIME.isoformat()
        intent_revision = _legacy_intent_revision()
        execution_revision = make_execution_revision(LEGACY_SESSION, LEGACY_EXECUTION)
        conn.execute(
            "INSERT INTO sessions (session_id, workflow_state, current_intent_revision_id, "
            "current_execution_revision_id, latest_confirmation_id, created_at, updated_at) "
            "VALUES (?, 'WAITING_CONFIRMATION', ?, ?, ?, ?, ?)",
            (
                LEGACY_SESSION,
                LEGACY_INTENT,
                LEGACY_EXECUTION,
                LEGACY_CONFIRMATION,
                created_at,
                created_at,
            ),
        )
        conn.execute(
            "INSERT INTO intent_revisions (intent_revision_id, session_id, parent_revision_id, "
            "schema_version, revision_json, created_at) VALUES (?, ?, NULL, 'v1', ?, ?)",
            (LEGACY_INTENT, LEGACY_SESSION, intent_revision.model_dump_json(), created_at),
        )
        conn.execute(
            "INSERT INTO execution_revisions (execution_revision_id, session_id, parent_revision_id, "
            "schema_version, revision_json, created_at) VALUES (?, ?, NULL, 'v1', ?, ?)",
            (LEGACY_EXECUTION, LEGACY_SESSION, execution_revision.model_dump_json(), created_at),
        )
        conn.execute(
            "INSERT INTO confirmations (confirmation_id, session_id, intent_revision_id, "
            "execution_revision_id, summary_hash, binding_hash, schema_version, confirmed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'v1', ?)",
            (
                LEGACY_CONFIRMATION,
                LEGACY_SESSION,
                LEGACY_INTENT,
                LEGACY_EXECUTION,
                LEGACY_SUMMARY_HASH,
                LEGACY_BINDING_HASH,
                created_at,
            ),
        )
        conn.execute(
            "INSERT INTO prompt_artifacts (prompt_artifact_id, session_id, intent_revision_id, "
            "confirmation_id, refs_json, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                LEGACY_PROMPT,
                LEGACY_SESSION,
                LEGACY_INTENT,
                LEGACY_CONFIRMATION,
                json.dumps(_PROMPT_REFS, sort_keys=True),
                LEGACY_PROMPT_PAYLOAD,
                created_at,
            ),
        )
        conn.execute(
            "INSERT INTO realization_states (realization_id, session_id, "
            "based_on_intent_revision_id, refs_json, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                LEGACY_REALIZATION,
                LEGACY_SESSION,
                LEGACY_INTENT,
                json.dumps(_REALIZATION_REFS, sort_keys=True),
                LEGACY_REALIZATION_PAYLOAD,
                created_at,
            ),
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()


def _raw_prompt_row(db_path: Path) -> tuple[str, str]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT refs_json, payload FROM prompt_artifacts WHERE prompt_artifact_id = ?",
            (LEGACY_PROMPT,),
        ).fetchone()
    finally:
        conn.close()


def _raw_realization_row(db_path: Path) -> tuple[str, str]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT refs_json, payload FROM realization_states WHERE realization_id = ?",
            (LEGACY_REALIZATION,),
        ).fetchone()
    finally:
        conn.close()


def test_v1_fixture_starts_at_version_one_without_knowledge_bundles(tmp_path) -> None:
    db_path = tmp_path / "legacy_v1.db"
    build_v1_database(db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert _user_tables(conn) == V1_TABLES
    finally:
        conn.close()


def test_v1_database_upgrade_adds_only_knowledge_bundles_and_keeps_old_rows(tmp_path) -> None:
    db_path = tmp_path / "legacy_v1.db"
    build_v1_database(db_path)
    prompt_before = _raw_prompt_row(db_path)
    realization_before = _raw_realization_row(db_path)

    repo = SQLiteRepository(db_path)
    try:
        assert repo.connection.execute("PRAGMA user_version").fetchone()[0] == (
            LATEST_SCHEMA_USER_VERSION
        )
        assert LATEST_SCHEMA_USER_VERSION == 2
        assert _user_tables(repo.connection) == V2_TABLES

        # 旧 Prompt 信封可读：payload 与 refs 逐字不变
        prompt = repo.get_prompt_artifact(LEGACY_PROMPT)
        assert prompt.payload == LEGACY_PROMPT_PAYLOAD
        assert prompt.refs == _PROMPT_REFS
        # 旧 Realization 可用：payload 与 refs 逐字不变
        realization = repo.get_current_realization_state(LEGACY_SESSION)
        assert realization is not None
        assert realization.artifact_id == LEGACY_REALIZATION
        assert realization.payload == LEGACY_REALIZATION_PAYLOAD
        assert realization.refs == _REALIZATION_REFS
        # 旧 revision / confirmation 仍可读且确认仍有效
        assert repo.get_intent_revision(LEGACY_INTENT) == _legacy_intent_revision()
        assert repo.is_confirmation_valid(LEGACY_CONFIRMATION) is True
        # 新表存在且为空
        assert repo.list_knowledge_bundles(LEGACY_SESSION) == []
    finally:
        repo.close()

    assert _raw_prompt_row(db_path) == prompt_before
    assert _raw_realization_row(db_path) == realization_before


def test_reopening_the_upgraded_v1_database_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "legacy_v1.db"
    build_v1_database(db_path)

    first = SQLiteRepository(db_path)
    first.close()
    snapshot_after_first = _raw_prompt_row(db_path)

    second = SQLiteRepository(db_path)
    try:
        assert second.connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert _user_tables(second.connection) == V2_TABLES
        assert second.get_prompt_artifact(LEGACY_PROMPT).payload == LEGACY_PROMPT_PAYLOAD
    finally:
        second.close()
    assert _raw_prompt_row(db_path) == snapshot_after_first


def test_apply_migrations_on_a_v1_connection_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "legacy_v1.db"
    build_v1_database(db_path)

    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        assert apply_migrations(conn) == 2
        assert _user_tables(conn) == V2_TABLES
        prompt_after_first = conn.execute(
            "SELECT refs_json, payload FROM prompt_artifacts WHERE prompt_artifact_id = ?",
            (LEGACY_PROMPT,),
        ).fetchone()
        # 再跑两次：无变化
        assert apply_migrations(conn) == 2
        assert apply_migrations(conn) == 2
        assert _user_tables(conn) == V2_TABLES
        assert (
            conn.execute(
                "SELECT refs_json, payload FROM prompt_artifacts WHERE prompt_artifact_id = ?",
                (LEGACY_PROMPT,),
            ).fetchone()
            == prompt_after_first
        )
    finally:
        conn.close()