"""Confirmation 测试：严格 revision 绑定、旧确认失效但可审计、hash 完整性。"""

from __future__ import annotations

import sqlite3

import pytest
from persistence_helpers import (
    make_confirmation,
    make_execution_revision,
    make_intent_revision,
    make_summary_hash,
    seed_context,
)

from visual_intent_agent.persistence import RepositoryError


def test_confirmation_binds_both_current_revisions_and_is_valid(repo) -> None:
    _, _, confirmation = seed_context(repo)
    assert confirmation is not None
    assert repo.is_confirmation_valid(confirmation.confirmation_id) is True
    assert repo.get_confirmation(confirmation.confirmation_id) == confirmation
    assert (
        repo.get_current_session_snapshot("ses_0001").latest_confirmation_id
        == confirmation.confirmation_id
    )


def test_confirmation_becomes_invalid_after_an_intent_revision_change(repo) -> None:
    _, _, confirmation = seed_context(repo)
    assert confirmation is not None

    repo.append_intent_revision(
        make_intent_revision("ses_0001", "irev_0002", "irev_0001", description="changed")
    )

    assert repo.is_confirmation_valid(confirmation.confirmation_id) is False


def test_confirmation_becomes_invalid_after_an_execution_revision_change(repo) -> None:
    _, _, confirmation = seed_context(repo)
    assert confirmation is not None

    repo.append_execution_revision(
        make_execution_revision("ses_0001", "erev_0002", "erev_0001", output_size="1280x720")
    )

    assert repo.is_confirmation_valid(confirmation.confirmation_id) is False


def test_old_confirmation_record_remains_auditable(repo) -> None:
    intent_revision, execution_revision, confirmation = seed_context(repo)
    assert confirmation is not None

    repo.append_intent_revision(
        make_intent_revision("ses_0001", "irev_0002", "irev_0001", description="changed")
    )
    new_confirmation = make_confirmation(
        "ses_0001", "cnf_0002", "irev_0002", execution_revision.execution_revision_id
    )
    repo.save_confirmation(new_confirmation)

    # 旧记录仍在，且逐字段可读；只有当前 revision 的确认有效
    stored_old = repo.get_confirmation("cnf_0001")
    assert stored_old == confirmation
    assert stored_old.intent_revision_id == intent_revision.intent_revision_id
    assert repo.get_confirmation("cnf_0002") == new_confirmation
    assert repo.is_confirmation_valid("cnf_0001") is False
    assert repo.is_confirmation_valid("cnf_0002") is True
    assert repo.get_current_session_snapshot("ses_0001").latest_confirmation_id == "cnf_0002"


def test_unknown_confirmation_is_not_valid_and_cannot_be_read(repo) -> None:
    seed_context(repo)
    assert repo.is_confirmation_valid("cnf_missing") is False
    assert repo.is_confirmation_valid("") is False
    with pytest.raises(RepositoryError) as excinfo:
        repo.get_confirmation("cnf_missing")
    assert excinfo.value.code == "persistence.confirmation_not_found"


def test_confirmation_must_bind_the_current_intent_revision(repo) -> None:
    _, execution_revision, _ = seed_context(repo)
    repo.append_intent_revision(make_intent_revision("ses_0001", "irev_0002", "irev_0001"))
    stale = make_confirmation(
        "ses_0001", "cnf_stale", "irev_0001", execution_revision.execution_revision_id
    )

    with pytest.raises(RepositoryError) as excinfo:
        repo.save_confirmation(stale)
    assert excinfo.value.code == "persistence.stale_revision"
    assert repo.is_confirmation_valid("cnf_stale") is False


def test_confirmation_must_bind_the_current_execution_revision(repo) -> None:
    intent_revision, _, _ = seed_context(repo)
    repo.append_execution_revision(
        make_execution_revision("ses_0001", "erev_0002", "erev_0001")
    )
    stale = make_confirmation(
        "ses_0001", "cnf_stale", intent_revision.intent_revision_id, "erev_0001"
    )

    with pytest.raises(RepositoryError) as excinfo:
        repo.save_confirmation(stale)
    assert excinfo.value.code == "persistence.stale_revision"


def test_confirmation_with_unknown_revision_is_rejected(repo) -> None:
    _, execution_revision, _ = seed_context(repo)
    record = make_confirmation(
        "ses_0001", "cnf_x", "irev_missing", execution_revision.execution_revision_id
    )
    with pytest.raises(RepositoryError) as excinfo:
        repo.save_confirmation(record)
    assert excinfo.value.code == "persistence.foreign_key_violation"


def test_confirmation_with_cross_session_revision_is_rejected(repo) -> None:
    _, execution_revision, _ = seed_context(repo, "ses_0001")
    seed_context(repo, "ses_0002", suffix="_b")
    cross = make_confirmation(
        "ses_0002", "cnf_cross", "irev_0001", execution_revision.execution_revision_id
    )
    with pytest.raises(RepositoryError) as excinfo:
        repo.save_confirmation(cross)
    assert excinfo.value.code == "persistence.ref_session_mismatch"


def test_empty_summary_hash_is_rejected(repo) -> None:
    intent_revision, execution_revision, _ = seed_context(repo, confirm=False)
    record = make_confirmation(
        "ses_0001",
        "cnf_blank",
        intent_revision.intent_revision_id,
        execution_revision.execution_revision_id,
        summary_hash="   ",
    )
    with pytest.raises(RepositoryError) as excinfo:
        repo.save_confirmation(record)
    assert excinfo.value.code == "persistence.invalid_summary_hash"


def test_duplicate_confirmation_id_is_rejected_and_previous_record_kept(repo) -> None:
    _, _, confirmation = seed_context(repo)
    assert confirmation is not None
    duplicate = make_confirmation(
        "ses_0001",
        "cnf_0001",
        confirmation.intent_revision_id,
        confirmation.execution_revision_id,
        summary_hash=make_summary_hash("another summary"),
    )

    with pytest.raises(RepositoryError) as excinfo:
        repo.save_confirmation(duplicate)
    assert excinfo.value.code == "persistence.duplicate_id"
    assert repo.get_confirmation("cnf_0001") == confirmation
    assert repo.is_confirmation_valid("cnf_0001") is True


def test_tampered_summary_hash_fails_the_hash_match(repo, db_path) -> None:
    _, _, confirmation = seed_context(repo)
    assert confirmation is not None
    assert repo.is_confirmation_valid("cnf_0001") is True

    raw = sqlite3.connect(db_path)
    try:
        raw.execute(
            "UPDATE confirmations SET summary_hash = ? WHERE confirmation_id = ?",
            (make_summary_hash("tampered"), "cnf_0001"),
        )
        raw.commit()
    finally:
        raw.close()

    assert repo.is_confirmation_valid("cnf_0001") is False
    # 记录仍在（append-only 只针对本模块；被外部篡改后仍可读但不再有效）
    assert repo.get_confirmation("cnf_0001").summary_hash == make_summary_hash("tampered")


def test_confirmation_of_missing_session_raises_session_not_found(repo) -> None:
    repo.create_session("ses_0001")
    repo.append_intent_revision(make_intent_revision("ses_0001", "irev_0001"))
    repo.append_execution_revision(make_execution_revision("ses_0001", "erev_0001"))
    record = make_confirmation("ses_missing", "cnf_x", "irev_0001", "erev_0001")
    with pytest.raises(RepositoryError) as excinfo:
        repo.save_confirmation(record)
    assert excinfo.value.code == "persistence.session_not_found"


def test_reconfirmation_after_reverting_to_the_same_revision_content(repo) -> None:
    intent_revision, execution_revision, confirmation = seed_context(repo, confirm=False)
    assert confirmation is None
    first = make_confirmation(
        "ses_0001", "cnf_0001", intent_revision.intent_revision_id,
        execution_revision.execution_revision_id, summary_hash=make_summary_hash("first"),
    )
    repo.save_confirmation(first)
    repo.append_intent_revision(
        make_intent_revision("ses_0001", "irev_0002", "irev_0001", description="changed")
    )
    assert repo.is_confirmation_valid("cnf_0001") is False

    second = make_confirmation(
        "ses_0001", "cnf_0002", "irev_0002", execution_revision.execution_revision_id,
        summary_hash=make_summary_hash("second"),
    )
    repo.save_confirmation(second)
    assert repo.is_confirmation_valid("cnf_0002") is True
    assert repo.get_confirmation("cnf_0001").intent_revision_id == "irev_0001"
