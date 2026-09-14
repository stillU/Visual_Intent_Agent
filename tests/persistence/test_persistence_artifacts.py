"""Artifact 信封测试：refs 外键完整性、payload 原样存取、最小可扩展边界。"""

from __future__ import annotations

import pytest
from persistence_helpers import (
    make_intent_revision,
    seed_context,
    seed_prompt_and_generation,
    seed_session,
)

from visual_intent_agent.persistence import RepositoryError


def test_prompt_artifact_envelope_round_trip(repo) -> None:
    intent_revision, _, confirmation = seed_context(repo)
    assert confirmation is not None
    payload = '{"prompt": "a lone astronaut, cinematic", "parameters": {"size": "1024x1024"}}'
    repo.append_prompt_artifact(
        "pra_0001",
        "ses_0001",
        {
            "intent_revision_id": intent_revision.intent_revision_id,
            "confirmation_id": confirmation.confirmation_id,
        },
        payload,
    )

    artifact = repo.get_prompt_artifact("pra_0001")
    assert artifact.artifact_id == "pra_0001"
    assert artifact.session_id == "ses_0001"
    assert artifact.payload == payload
    assert artifact.refs == {
        "intent_revision_id": "irev_0001",
        "confirmation_id": "cnf_0001",
    }
    assert artifact.created_at.tzinfo is not None


def test_generation_artifact_requires_an_existing_prompt_artifact(repo) -> None:
    seed_prompt_and_generation(repo)
    artifact = repo.get_generation_artifact("gen_0001")
    assert artifact.refs == {"prompt_artifact_id": "pra_0001"}

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_generation_artifact(
            "gen_missing_ref", "ses_0001", {"prompt_artifact_id": "pra_missing"}, "{}"
        )
    assert excinfo.value.code == "persistence.foreign_key_violation"
    with pytest.raises(RepositoryError):
        repo.get_generation_artifact("gen_missing_ref")


def test_feedback_result_requires_an_existing_generation(repo) -> None:
    seed_prompt_and_generation(repo)
    repo.append_feedback_result(
        "fbk_0001", "ses_0001", {"generation_id": "gen_0001"}, '{"decision": "accept"}'
    )

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_feedback_result(
            "fbk_missing_ref", "ses_0001", {"generation_id": "gen_missing"}, "{}"
        )
    assert excinfo.value.code == "persistence.foreign_key_violation"


def test_realization_state_requires_an_existing_intent_revision(repo) -> None:
    seed_context(repo)
    repo.append_realization_state(
        "rlz_0001", "ses_0001", {"based_on_intent_revision_id": "irev_0001"}, '{"values": []}'
    )
    assert repo.get_current_realization_state("ses_0001").artifact_id == "rlz_0001"

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_realization_state(
            "rlz_missing_ref", "ses_0001", {"based_on_intent_revision_id": "irev_missing"}, "{}"
        )
    assert excinfo.value.code == "persistence.foreign_key_violation"


def test_missing_required_ref_key_is_rejected(repo) -> None:
    intent_revision, _, _ = seed_context(repo)
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_prompt_artifact(
            "pra_0001",
            "ses_0001",
            {"intent_revision_id": intent_revision.intent_revision_id},
            "{}",
        )
    assert excinfo.value.code == "persistence.invalid_refs"


def test_unknown_ref_key_is_rejected(repo) -> None:
    intent_revision, _, confirmation = seed_context(repo)
    assert confirmation is not None
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_prompt_artifact(
            "pra_0001",
            "ses_0001",
            {
                "intent_revision_id": intent_revision.intent_revision_id,
                "confirmation_id": confirmation.confirmation_id,
                "realization_id": "rlz_0001",
            },
            "{}",
        )
    assert excinfo.value.code == "persistence.invalid_refs"


def test_non_string_or_empty_ref_value_is_rejected(repo) -> None:
    intent_revision, _, confirmation = seed_context(repo)
    assert confirmation is not None
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_prompt_artifact(
            "pra_0001",
            "ses_0001",
            {
                "intent_revision_id": intent_revision.intent_revision_id,
                "confirmation_id": "",
            },
            "{}",
        )
    assert excinfo.value.code == "persistence.invalid_field"


def test_cross_session_ref_is_rejected(repo) -> None:
    seed_context(repo, "ses_0001")
    seed_context(repo, "ses_0002", suffix="_b")
    repo.append_prompt_artifact(
        "pra_0001",
        "ses_0001",
        {"intent_revision_id": "irev_0001", "confirmation_id": "cnf_0001"},
        "{}",
    )
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_generation_artifact(
            "gen_cross", "ses_0002", {"prompt_artifact_id": "pra_0001"}, "{}"
        )
    assert excinfo.value.code == "persistence.ref_session_mismatch"


def test_artifact_for_missing_session_is_rejected(repo) -> None:
    seed_context(repo, "ses_0001")
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_prompt_artifact(
            "pra_0001",
            "ses_missing",
            {"intent_revision_id": "irev_0001", "confirmation_id": "cnf_0001"},
            "{}",
        )
    assert excinfo.value.code == "persistence.session_not_found"


def test_duplicate_artifact_ids_are_rejected(repo) -> None:
    seed_prompt_and_generation(repo)
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_prompt_artifact(
            "pra_0001",
            "ses_0001",
            {"intent_revision_id": "irev_0001", "confirmation_id": "cnf_0001"},
            "{}",
        )
    assert excinfo.value.code == "persistence.duplicate_id"


def test_get_missing_artifact_raises_typed_error(repo) -> None:
    seed_context(repo)
    with pytest.raises(RepositoryError) as excinfo:
        repo.get_prompt_artifact("pra_missing")
    assert excinfo.value.code == "persistence.artifact_not_found"
    with pytest.raises(RepositoryError) as excinfo:
        repo.get_generation_artifact("gen_missing")
    assert excinfo.value.code == "persistence.artifact_not_found"


def test_list_generation_artifacts_is_ordered_and_empty_for_new_session(repo) -> None:
    seed_prompt_and_generation(repo)
    assert repo.list_generation_artifacts("ses_0001") == [repo.get_generation_artifact("gen_0001")]

    second_prompt = "pra_0002"
    repo.append_prompt_artifact(
        second_prompt,
        "ses_0001",
        {"intent_revision_id": "irev_0001", "confirmation_id": "cnf_0001"},
        "{}",
    )
    repo.append_generation_artifact("gen_0002", "ses_0001", {"prompt_artifact_id": second_prompt}, "{}")

    ids = [artifact.artifact_id for artifact in repo.list_generation_artifacts("ses_0001")]
    assert ids == ["gen_0001", "gen_0002"]

    repo.create_session("ses_other")
    assert repo.list_generation_artifacts("ses_other") == []


def test_current_realization_state_is_none_until_the_first_state(repo) -> None:
    seed_context(repo)
    assert repo.get_current_realization_state("ses_0001") is None

    repo.append_realization_state(
        "rlz_0001", "ses_0001", {"based_on_intent_revision_id": "irev_0001"}, '{"values": []}'
    )
    repo.append_intent_revision(make_intent_revision("ses_0001", "irev_0002", "irev_0001"))
    repo.append_realization_state(
        "rlz_0002", "ses_0001", {"based_on_intent_revision_id": "irev_0002"}, '{"values": [1]}'
    )

    current = repo.get_current_realization_state("ses_0001")
    assert current.artifact_id == "rlz_0002"
    assert current.payload == '{"values": [1]}'
    # 历史 realization 不覆盖：两张行都在
    assert repo.get_current_realization_state("ses_0001").refs == {
        "based_on_intent_revision_id": "irev_0002"
    }


def test_pending_question_pointer_round_trip(repo) -> None:
    session_id = seed_session(repo)
    repo.save_pending_question(session_id, "qst_0001", '{"target_path": "style.primary"}')
    snapshot = repo.get_current_session_snapshot(session_id)
    assert snapshot.pending_question_id == "qst_0001"
    assert snapshot.pending_question_payload == '{"target_path": "style.primary"}'

    repo.clear_pending_question(session_id)
    cleared = repo.get_current_session_snapshot(session_id)
    assert cleared.pending_question_id is None
    assert cleared.pending_question_payload is None


def test_pending_question_for_missing_session_is_rejected(repo) -> None:
    with pytest.raises(RepositoryError) as excinfo:
        repo.save_pending_question("ses_missing", "qst_0001", "{}")
    assert excinfo.value.code == "persistence.session_not_found"
    with pytest.raises(RepositoryError) as excinfo:
        repo.clear_pending_question("ses_missing")
    assert excinfo.value.code == "persistence.session_not_found"
