"""Revision 测试：append-only、父链、乐观并发、读回逐值一致。"""

from __future__ import annotations

import pytest
from persistence_helpers import (
    FIXED_TIME,
    make_delta,
    make_execution_revision,
    make_intent_revision,
    seed_context,
    seed_session,
)

from visual_intent_agent.persistence import RepositoryError


def test_genesis_intent_revision_advances_the_current_pointer(repo) -> None:
    session_id = seed_session(repo)
    revision = make_intent_revision(session_id, "irev_0001")
    repo.append_intent_revision(revision)

    snapshot = repo.get_current_session_snapshot(session_id)
    assert snapshot.current_intent_revision_id == "irev_0001"
    assert repo.get_intent_revision("irev_0001") == revision


def test_new_revision_does_not_overwrite_the_old_one(repo) -> None:
    intent_revision, _, _ = seed_context(repo)
    first_json = repo.get_intent_revision("irev_0001").model_dump_json()

    second = make_intent_revision(
        "ses_0001", "irev_0002", "irev_0001", description="a standing astronaut"
    )
    repo.append_intent_revision(second)

    assert repo.get_intent_revision("irev_0001").model_dump_json() == first_json
    assert repo.get_intent_revision("irev_0001") == intent_revision
    assert repo.get_intent_revision("irev_0002") == second
    assert repo.get_current_session_snapshot("ses_0001").current_intent_revision_id == "irev_0002"


def test_parent_chain_is_preserved_across_three_revisions(repo) -> None:
    seed_session(repo)
    first = make_intent_revision("ses_0001", "irev_0001")
    second = make_intent_revision("ses_0001", "irev_0002", "irev_0001")
    third = make_intent_revision("ses_0001", "irev_0003", "irev_0002")
    for revision in (first, second, third):
        repo.append_intent_revision(revision)

    assert repo.get_intent_revision("irev_0001").parent_revision_id is None
    assert repo.get_intent_revision("irev_0002").parent_revision_id == "irev_0001"
    assert repo.get_intent_revision("irev_0003").parent_revision_id == "irev_0002"


def test_applied_deltas_and_facets_round_trip(repo) -> None:
    session_id = seed_session(repo)
    delta = make_delta(path="style.primary", value="cinematic realism")
    revision = make_intent_revision(session_id, "irev_0001", deltas=[delta])
    repo.append_intent_revision(revision)

    stored = repo.get_intent_revision("irev_0001")
    assert stored == revision
    assert stored.applied_deltas == [delta]
    assert stored.intent.subject.description == "a lone astronaut"
    assert stored.applied_deltas[0].evidence_refs[0].message_id == "msg_evidence"
    assert stored.created_at == revision.created_at


def test_duplicate_intent_revision_is_rejected_and_pointer_is_unchanged(repo) -> None:
    seed_context(repo)
    duplicate = make_intent_revision("ses_0001", "irev_0001")
    before = repo.get_current_session_snapshot("ses_0001")

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_intent_revision(duplicate)

    assert excinfo.value.code == "persistence.duplicate_id"
    assert repo.get_current_session_snapshot("ses_0001") == before
    assert repo.get_intent_revision("irev_0001").intent.subject.description == "a lone astronaut"


def test_stale_parent_revision_is_rejected(repo) -> None:
    seed_context(repo)
    stale_genesis = make_intent_revision("ses_0001", "irev_0002")
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_intent_revision(stale_genesis)
    assert excinfo.value.code == "persistence.stale_revision"
    assert repo.get_current_session_snapshot("ses_0001").current_intent_revision_id == "irev_0001"
    with pytest.raises(RepositoryError):
        repo.get_intent_revision("irev_0002")


def test_parent_from_another_revision_branch_is_rejected(repo) -> None:
    seed_session(repo)
    repo.append_intent_revision(make_intent_revision("ses_0001", "irev_0001"))
    repo.append_intent_revision(make_intent_revision("ses_0001", "irev_0002", "irev_0001"))

    forked = make_intent_revision("ses_0001", "irev_0003", "irev_0001")
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_intent_revision(forked)
    assert excinfo.value.code == "persistence.stale_revision"


def test_revision_for_missing_session_is_rejected(repo) -> None:
    revision = make_intent_revision("ses_missing", "irev_0001")
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_intent_revision(revision)
    assert excinfo.value.code == "persistence.session_not_found"


def test_get_missing_revisions_raise_typed_errors(repo) -> None:
    with pytest.raises(RepositoryError) as intent_error:
        repo.get_intent_revision("irev_missing")
    assert intent_error.value.code == "persistence.intent_revision_not_found"

    with pytest.raises(RepositoryError) as execution_error:
        repo.get_execution_revision("erev_missing")
    assert execution_error.value.code == "persistence.execution_revision_not_found"


def test_execution_revision_chain_and_pointer(repo) -> None:
    seed_session(repo)
    first = make_execution_revision("ses_0001", "erev_0001")
    repo.append_execution_revision(first)
    assert repo.get_current_session_snapshot("ses_0001").current_execution_revision_id == "erev_0001"

    second = make_execution_revision("ses_0001", "erev_0002", "erev_0001", output_size="1280x720")
    repo.append_execution_revision(second)

    assert repo.get_execution_revision("erev_0001") == first
    assert repo.get_execution_revision("erev_0002") == second
    assert repo.get_execution_revision("erev_0002").output_size == "1280x720"
    assert repo.get_current_session_snapshot("ses_0001").current_execution_revision_id == "erev_0002"


def test_execution_revision_does_not_touch_the_intent_pointer(repo) -> None:
    repo.create_session("ses_0001")
    repo.append_execution_revision(make_execution_revision("ses_0001", "erev_0001"))
    snapshot = repo.get_current_session_snapshot("ses_0001")
    assert snapshot.current_intent_revision_id is None
    assert snapshot.current_execution_revision_id == "erev_0001"


def test_duplicate_and_stale_execution_revisions_are_rejected(repo) -> None:
    seed_session(repo)
    repo.append_execution_revision(make_execution_revision("ses_0001", "erev_0001"))

    with pytest.raises(RepositoryError) as duplicate:
        repo.append_execution_revision(make_execution_revision("ses_0001", "erev_0001"))
    assert duplicate.value.code == "persistence.duplicate_id"

    with pytest.raises(RepositoryError) as stale:
        repo.append_execution_revision(make_execution_revision("ses_0001", "erev_0002"))
    assert stale.value.code == "persistence.stale_revision"


def test_revisions_of_two_sessions_are_isolated(repo) -> None:
    repo.create_session("ses_a")
    repo.create_session("ses_b")
    repo.append_intent_revision(make_intent_revision("ses_a", "irev_a1"))
    repo.append_intent_revision(make_intent_revision("ses_b", "irev_b1"))

    assert repo.get_current_session_snapshot("ses_a").current_intent_revision_id == "irev_a1"
    assert repo.get_current_session_snapshot("ses_b").current_intent_revision_id == "irev_b1"

    # b 的下一条不能挂到 a 的 head 上
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_intent_revision(make_intent_revision("ses_b", "irev_b2", "irev_a1"))
    assert excinfo.value.code == "persistence.stale_revision"


def test_messages_are_append_only_and_ordered_by_insertion(repo) -> None:
    session_id = seed_session(repo)
    for index in range(3):
        repo.append_message(
            session_id, f"msg_{index}", "user", f"text {index}", FIXED_TIME
        )
    assert repo.get_current_session_snapshot(session_id).message_ids == (
        "msg_0",
        "msg_1",
        "msg_2",
    )


def test_message_for_missing_session_is_rejected(repo) -> None:
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_message("ses_missing", "msg_1", "user", "hi", FIXED_TIME)
    assert excinfo.value.code == "persistence.session_not_found"
