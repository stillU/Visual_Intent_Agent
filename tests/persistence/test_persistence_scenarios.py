"""任务书「必测场景」9 条的端到端映射（每条一个显式测试）。"""

from __future__ import annotations

from persistence_helpers import (
    FIXED_TIME,
    install_abort_trigger,
    make_confirmation,
    make_execution_revision,
    make_intent_revision,
    seed_context,
    seed_prompt_and_generation,
    seed_session,
)

import pytest

from visual_intent_agent.persistence import (
    InvalidStateTransitionError,
    RepositoryError,
    SQLiteRepository,
)


def test_scenario_1_a_new_revision_never_overwrites_the_old_one(repo) -> None:
    intent_revision, _, _ = seed_context(repo)
    original_json = repo.get_intent_revision("irev_0001").model_dump_json()

    repo.append_intent_revision(
        make_intent_revision("ses_0001", "irev_0002", "irev_0001", description="changed")
    )

    assert repo.get_intent_revision("irev_0001").model_dump_json() == original_json
    assert repo.get_intent_revision("irev_0001") == intent_revision
    assert repo.get_intent_revision("irev_0002").intent.subject.description == "changed"


def test_scenario_2_the_parent_revision_chain_is_correct(repo) -> None:
    seed_session(repo)
    for revision_id, parent in (
        ("irev_0001", None),
        ("irev_0002", "irev_0001"),
        ("irev_0003", "irev_0002"),
    ):
        repo.append_intent_revision(make_intent_revision("ses_0001", revision_id, parent))

    parents = [
        repo.get_intent_revision(revision_id).parent_revision_id
        for revision_id in ("irev_0001", "irev_0002", "irev_0003")
    ]
    assert parents == [None, "irev_0001", "irev_0002"]
    # 断链/回环被拒绝
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_intent_revision(make_intent_revision("ses_0001", "irev_0004", "irev_0001"))
    assert excinfo.value.code == "persistence.stale_revision"


def test_scenario_3_old_confirmation_is_invalid_after_an_intent_change(repo) -> None:
    _, _, confirmation = seed_context(repo)
    assert confirmation is not None
    assert repo.is_confirmation_valid("cnf_0001") is True

    repo.append_intent_revision(
        make_intent_revision("ses_0001", "irev_0002", "irev_0001", description="changed")
    )

    assert repo.is_confirmation_valid("cnf_0001") is False


def test_scenario_4_old_confirmation_records_remain_auditable(repo) -> None:
    _, execution_revision, old_confirmation = seed_context(repo)
    assert old_confirmation is not None
    repo.append_intent_revision(
        make_intent_revision("ses_0001", "irev_0002", "irev_0001", description="changed")
    )
    new_confirmation = make_confirmation(
        "ses_0001", "cnf_0002", "irev_0002", execution_revision.execution_revision_id
    )
    repo.save_confirmation(new_confirmation)

    fetched = repo.get_confirmation("cnf_0001")
    assert fetched == old_confirmation
    assert fetched.summary_hash == old_confirmation.summary_hash
    assert fetched.intent_revision_id == "irev_0001"
    assert repo.is_confirmation_valid("cnf_0001") is False
    assert repo.is_confirmation_valid("cnf_0002") is True


def test_scenario_5_execution_revision_change_requires_reconfirmation(repo) -> None:
    _, _, confirmation = seed_context(repo)
    assert confirmation is not None
    assert repo.is_confirmation_valid("cnf_0001") is True

    repo.append_execution_revision(
        make_execution_revision("ses_0001", "erev_0002", "erev_0001", output_size="1280x720")
    )

    assert repo.is_confirmation_valid("cnf_0001") is False


def test_scenario_6_illegal_state_transitions_are_rejected(repo) -> None:
    session_id = seed_session(repo)

    with pytest.raises(InvalidStateTransitionError) as excinfo:
        repo.transition_state(session_id, "COMPLETED")

    assert excinfo.value.code == "persistence.invalid_state_transition"
    assert repo.get_current_session_snapshot(session_id).workflow_state.value == "UNDERSTANDING"


def test_scenario_7_a_failure_in_the_middle_of_a_write_rolls_back(repo, db_path) -> None:
    session_id = seed_session(repo)
    install_abort_trigger(
        db_path, table="sessions", column="current_intent_revision_id", name="rollback_boom"
    )

    with pytest.raises(RepositoryError):
        repo.append_intent_revision(make_intent_revision(session_id, "irev_0001"))

    with pytest.raises(RepositoryError):
        repo.get_intent_revision("irev_0001")
    assert repo.get_current_session_snapshot(session_id).current_intent_revision_id is None


def test_scenario_8_session_snapshot_survives_reopening_the_database(db_path) -> None:
    first = SQLiteRepository(db_path)
    try:
        session_id = seed_session(first)
        first.append_intent_revision(make_intent_revision(session_id, "irev_0001"))
        first.append_message(session_id, "msg_0001", "user", "hello", FIXED_TIME)
        first.transition_state(session_id, "WAITING_CONFIRMATION")
        before = first.get_current_session_snapshot(session_id)
    finally:
        first.close()

    second = SQLiteRepository(db_path)
    try:
        assert second.get_current_session_snapshot(session_id) == before
        assert second.get_intent_revision("irev_0001") == make_intent_revision(session_id, "irev_0001")
    finally:
        second.close()


def test_scenario_9_feedback_and_generation_foreign_keys_are_enforced(repo) -> None:
    seed_prompt_and_generation(repo)

    with pytest.raises(RepositoryError) as generation_error:
        repo.append_generation_artifact(
            "gen_bad", "ses_0001", {"prompt_artifact_id": "pra_missing"}, "{}"
        )
    assert generation_error.value.code == "persistence.foreign_key_violation"

    with pytest.raises(RepositoryError) as feedback_error:
        repo.append_feedback_result(
            "fbk_bad", "ses_0001", {"generation_id": "gen_missing"}, "{}"
        )
    assert feedback_error.value.code == "persistence.foreign_key_violation"

    # 指向存在记录时正常落库
    repo.append_feedback_result(
        "fbk_good", "ses_0001", {"generation_id": "gen_0001"}, '{"decision": "accept"}'
    )
    assert repo.get_generation_artifact("gen_0001") is not None
