"""Schema 与最小迁移测试：10 张表、user_version、外键、无 UPDATE 覆盖路径。"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from persistence_helpers import seed_context

from visual_intent_agent.persistence import RepositoryError, SQLiteRepository
from visual_intent_agent.persistence.repository import (
    LATEST_SCHEMA_USER_VERSION,
    apply_migrations,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = PROJECT_ROOT / "visual_intent_agent" / "persistence"

EXPECTED_TABLES = {
    "sessions",
    "messages",
    "intent_revisions",
    "execution_revisions",
    "confirmations",
    "realization_states",
    "prompt_artifacts",
    "generation_artifacts",
    "feedback_results",
    "knowledge_bundles",
}

#: 只有 sessions 保存"当前指针"；其余全部 append-only（无 UPDATE 路径）。
APPEND_ONLY_TABLES = EXPECTED_TABLES - {"sessions", "messages"}


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def test_schema_creates_exactly_the_ten_frozen_tables(repo) -> None:
    assert _user_tables(repo.connection) == EXPECTED_TABLES


def test_knowledge_bundles_is_created_as_an_append_only_envelope(repo) -> None:
    assert "knowledge_bundles" in _user_tables(repo.connection)
    schema = (PACKAGE_DIR / "schema.sql").read_text(encoding="utf-8")
    match = re.search(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+knowledge_bundles\s*\((.*?)\);",
        schema,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert match, "schema.sql must declare knowledge_bundles"
    body = match.group(1)
    assert re.search(r"\bbundle_id\s+TEXT\s+PRIMARY\s+KEY", body)
    assert re.search(r"\bsession_id\s+TEXT\s+NOT\s+NULL\s+REFERENCES\s+sessions\(session_id\)", body)
    assert re.search(r"\brefs_json\s+TEXT\s+NOT\s+NULL", body)
    assert re.search(r"\bpayload\s+TEXT\s+NOT\s+NULL", body)
    assert re.search(r"\bcreated_at\s+TEXT\s+NOT\s+NULL", body)


def test_user_version_is_the_frozen_schema_version(repo) -> None:
    version = repo.connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == LATEST_SCHEMA_USER_VERSION == 2


def test_foreign_keys_are_enabled_on_every_connection(repo, db_path) -> None:
    assert repo.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    other = SQLiteRepository(db_path)
    try:
        assert other.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        other.close()


def test_migration_is_idempotent_and_preserves_data(db_path) -> None:
    first = SQLiteRepository(db_path)
    try:
        seed_context(first)
    finally:
        first.close()

    second = SQLiteRepository(db_path)
    try:
        assert second.connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert _user_tables(second.connection) == EXPECTED_TABLES
        snapshot = second.get_current_session_snapshot("ses_0001")
        assert snapshot.current_intent_revision_id == "irev_0001"
        assert second.get_intent_revision("irev_0001").intent_revision_id == "irev_0001"
    finally:
        second.close()


def test_apply_migrations_on_a_brand_new_file_sets_the_latest_version(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "empty.db", isolation_level=None)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert apply_migrations(conn) == LATEST_SCHEMA_USER_VERSION
        assert _user_tables(conn) == EXPECTED_TABLES
        # 再跑一次：幂等
        assert apply_migrations(conn) == LATEST_SCHEMA_USER_VERSION
    finally:
        conn.close()


def test_newer_database_is_refused(tmp_path) -> None:
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA user_version = 99")
    finally:
        conn.close()
    with pytest.raises(RepositoryError) as excinfo:
        SQLiteRepository(path)
    assert excinfo.value.code == "persistence.schema_too_new"


def test_revision_and_artifact_tables_have_no_update_path() -> None:
    source = (PACKAGE_DIR / "repository.py").read_text(encoding="utf-8")
    updated_tables = set(re.findall(r"UPDATE\s+([a-z_]+)", source, flags=re.IGNORECASE))
    assert updated_tables == {"sessions"}, updated_tables
    for table in APPEND_ONLY_TABLES:
        assert f"UPDATE {table}" not in source
        assert f"UPDATE {table}".upper() not in source.upper()


def test_schema_sql_declares_every_frozen_ref_as_a_real_foreign_key() -> None:
    schema = (PACKAGE_DIR / "schema.sql").read_text(encoding="utf-8")
    expected_foreign_keys = (
        ("intent_revision_id", "intent_revisions", "intent_revision_id"),
        ("execution_revision_id", "execution_revisions", "execution_revision_id"),
        ("confirmation_id", "confirmations", "confirmation_id"),
        ("prompt_artifact_id", "prompt_artifacts", "prompt_artifact_id"),
        ("generation_id", "generation_artifacts", "generation_id"),
        ("based_on_intent_revision_id", "intent_revisions", "intent_revision_id"),
    )
    for column, table, referenced in expected_foreign_keys:
        pattern = (
            rf"\b{column}\s+TEXT\s+NOT\s+NULL\s+REFERENCES\s+{table}\({referenced}\)"
        )
        assert re.search(pattern, schema), f"missing FK for {column} -> {table}.{referenced}"
    # refs 外键列的三种归属各出现一次以上（prompt / generation / feedback / realization）
    for table in ("intent_revisions", "confirmations", "prompt_artifacts", "generation_artifacts"):
        assert f"REFERENCES {table}(" in schema, table

    # v2 knowledge_bundles 的精确三键 refs 各自是真外键
    bundle_match = re.search(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+knowledge_bundles\s*\((.*?)\);",
        schema,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert bundle_match
    bundle_body = bundle_match.group(1)
    for column in ("intent_revision_id", "execution_revision_id", "confirmation_id"):
        assert re.search(rf"\b{column}\s+TEXT\s+NOT\s+NULL\s+REFERENCES\s+\w+\(", bundle_body), column


def test_memory_database_is_supported() -> None:
    repo = SQLiteRepository(":memory:")
    try:
        repo.create_session("ses_memory")
        assert repo.get_current_session_snapshot("ses_memory").session_id == "ses_memory"
    finally:
        repo.close()
