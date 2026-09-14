"""事务与恢复测试：中途失败回滚、连接可复用、重开数据库后状态可恢复。"""

from __future__ import annotations

import sqlite3

import pytest
from persistence_helpers import (
    FIXED_TIME,
    drop_trigger,
    install_abort_trigger,
    make_confirmation,
    make_execution_revision,
    make_intent_revision,
    seed_context,
    seed_session,
)

from visual_intent_agent.persistence import (
    InvalidStateTransitionError,
    RepositoryError,
    SQLiteRepository,
)


def test_append_intent_revision_rolls_back_when_the_pointer_update_fails(repo, db_path) -> None:
    session_id = seed_session(repo)
    install_abort_trigger(
        db_path, table="sessions", column="current_intent_revision_id", name="boom_intent"
    )

    with pytest.raises(RepositoryError):
        repo.append_intent_revision(make_intent_revision(session_id, "irev_0001"))

    # 事务回滚：revision 行没有落库，session 指针也没有前进
    with pytest.raises(RepositoryError) as excinfo:
        repo.get_intent_revision("irev_0001")
    assert excinfo.value.code == "persistence.intent_revision_not_found"
    assert repo.get_current_session_snapshot(session_id).current_intent_revision_id is None
    assert repo.connection.in_transaction is False

    # 连接仍可用：移除故障注入后同样的写入成功
    drop_trigger(db_path, "boom_intent")
    repo.append_intent_revision(make_intent_revision(session_id, "irev_0001"))
    assert repo.get_current_session_snapshot(session_id).current_intent_revision_id == "irev_0001"


def test_append_execution_revision_rolls_back_when_the_pointer_update_fails(repo, db_path) -> None:
    session_id = seed_session(repo)
    install_abort_trigger(
        db_path, table="sessions", column="current_execution_revision_id", name="boom_execution"
    )

    with pytest.raises(RepositoryError):
        repo.append_execution_revision(make_execution_revision(session_id, "erev_0001"))

    with pytest.raises(RepositoryError):
        repo.get_execution_revision("erev_0001")
    assert repo.get_current_session_snapshot(session_id).current_execution_revision_id is None
    assert repo.connection.in_transaction is False


def test_save_confirmation_rolls_back_when_the_pointer_update_fails(repo, db_path) -> None:
    intent_revision, execution_revision, _ = seed_context(repo, confirm=False)
    install_abort_trigger(
        db_path, table="sessions", column="latest_confirmation_id", name="boom_confirmation"
    )
    record = make_confirmation(
        "ses_0001", "cnf_0001", intent_revision.intent_revision_id,
        execution_revision.execution_revision_id,
    )

    with pytest.raises(RepositoryError):
        repo.save_confirmation(record)

    with pytest.raises(RepositoryError) as excinfo:
        repo.get_confirmation("cnf_0001")
    assert excinfo.value.code == "persistence.confirmation_not_found"
    assert repo.is_confirmation_valid("cnf_0001") is False
    assert repo.get_current_session_snapshot("ses_0001").latest_confirmation_id is None
    assert repo.connection.in_transaction is False
    # 第一个 revision 仍在（回滚只影响本次事务）
    assert repo.get_intent_revision("irev_0001") == intent_revision


def test_failed_artifact_write_leaves_no_row_and_no_partial_state(repo) -> None:
    intent_revision, execution_revision, _ = seed_context(repo, confirm=False)

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_prompt_artifact(
            "pra_0001",
            "ses_0001",
            {
                "intent_revision_id": intent_revision.intent_revision_id,
                "confirmation_id": "cnf_missing",
            },
            "{}",
        )
    assert excinfo.value.code == "persistence.foreign_key_violation"
    with pytest.raises(RepositoryError):
        repo.get_prompt_artifact("pra_0001")
    assert repo.connection.in_transaction is False
    assert repo.get_execution_revision(execution_revision.execution_revision_id) == execution_revision


def test_failed_transition_does_not_write(repo) -> None:
    session_id = seed_session(repo)
    before = repo.get_current_session_snapshot(session_id)
    with pytest.raises(InvalidStateTransitionError):
        repo.transition_state(session_id, "GENERATING")
    assert repo.get_current_session_snapshot(session_id) == before
    assert repo.connection.in_transaction is False


def test_snapshot_is_restored_after_reopening_the_database(db_path) -> None:
    first = SQLiteRepository(db_path)
    try:
        session_id = seed_session(first)
        intent_revision = make_intent_revision(session_id, "irev_0001")
        execution_revision = make_execution_revision(session_id, "erev_0001")
        first.append_intent_revision(intent_revision)
        first.append_execution_revision(execution_revision)
        first.append_message(session_id, "msg_0001", "user", "hello", FIXED_TIME)
        first.save_pending_question(session_id, "qst_0001", '{"target_path": "style.primary"}')
        first.transition_state(session_id, "WAITING_CLARIFICATION")
        before = first.get_current_session_snapshot(session_id)
    finally:
        first.close()

    second = SQLiteRepository(db_path)
    try:
        after = second.get_current_session_snapshot(session_id)
        assert after == before
        assert after.message_ids == ("msg_0001",)
        assert after.pending_question_id == "qst_0001"
        assert after.pending_question_payload == '{"target_path": "style.primary"}'
        assert second.get_intent_revision("irev_0001") == intent_revision
        assert second.get_execution_revision("erev_0001") == execution_revision
    finally:
        second.close()


def test_multiple_statement_write_is_atomic(repo, db_path) -> None:
    """create_session + 首个 execution revision 成功后，指针与行同时存在。"""
    repo.create_session("ses_0001")
    repo.append_execution_revision(make_execution_revision("ses_0001", "erev_0001"))
    repo.append_intent_revision(make_intent_revision("ses_0001", "irev_0001"))

    raw = sqlite3.connect(db_path)
    try:
        executions = raw.execute("SELECT COUNT(*) FROM execution_revisions").fetchone()[0]
        intents = raw.execute("SELECT COUNT(*) FROM intent_revisions").fetchone()[0]
        pointer = raw.execute(
            "SELECT current_intent_revision_id, current_execution_revision_id FROM sessions"
        ).fetchone()
    finally:
        raw.close()
    assert (executions, intents) == (1, 1)
    assert pointer == ("irev_0001", "erev_0001")
