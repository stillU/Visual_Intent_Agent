"""存储记录模型测试：字段面冻结、frozen/extra、tz-aware UTC。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from persistence_helpers import FIXED_TIME, make_summary_hash
from pydantic import ValidationError

from visual_intent_agent.persistence import (
    ConfirmationRecord,
    SessionSnapshot,
    StoredArtifact,
    WorkflowState,
)

NAIVE = datetime(2024, 5, 1, 8, 30, 0)


def test_confirmation_record_exposes_exactly_the_frozen_fields() -> None:
    assert set(ConfirmationRecord.model_fields) == {
        "schema_version",
        "confirmation_id",
        "session_id",
        "intent_revision_id",
        "execution_revision_id",
        "summary_hash",
        "confirmed_at",
    }
    record = ConfirmationRecord(
        confirmation_id="cnf_1",
        session_id="ses_1",
        intent_revision_id="irev_1",
        execution_revision_id="erev_1",
        summary_hash=make_summary_hash(),
    )
    assert record.schema_version == "v1"
    assert record.confirmed_at.tzinfo is not None


def test_confirmation_record_is_frozen_and_rejects_extra_fields() -> None:
    record = ConfirmationRecord(
        confirmation_id="cnf_1",
        session_id="ses_1",
        intent_revision_id="irev_1",
        execution_revision_id="erev_1",
        summary_hash=make_summary_hash(),
    )
    with pytest.raises(ValidationError):
        record.summary_hash = "0" * 64  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ConfirmationRecord(
            confirmation_id="cnf_1",
            session_id="ses_1",
            intent_revision_id="irev_1",
            execution_revision_id="erev_1",
            summary_hash=make_summary_hash(),
            unexpected="x",
        )


def test_confirmation_record_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        ConfirmationRecord(
            confirmation_id="cnf_1",
            session_id="ses_1",
            intent_revision_id="irev_1",
            execution_revision_id="erev_1",
            summary_hash=make_summary_hash(),
            confirmed_at=NAIVE,
        )


def test_confirmation_record_normalises_non_utc_timestamp() -> None:
    plus_two = timezone(timedelta(hours=2))
    record = ConfirmationRecord(
        confirmation_id="cnf_1",
        session_id="ses_1",
        intent_revision_id="irev_1",
        execution_revision_id="erev_1",
        summary_hash=make_summary_hash(),
        confirmed_at=datetime(2024, 5, 1, 10, 30, tzinfo=plus_two),
    )
    assert record.confirmed_at == FIXED_TIME
    assert record.confirmed_at.utcoffset() == timedelta(0)


def test_confirmation_record_json_timestamp_ends_with_utc_offset() -> None:
    record = ConfirmationRecord(
        confirmation_id="cnf_1",
        session_id="ses_1",
        intent_revision_id="irev_1",
        execution_revision_id="erev_1",
        summary_hash=make_summary_hash(),
        confirmed_at=FIXED_TIME,
    )
    assert '"confirmed_at":"2024-05-01T08:30:00+00:00"' in record.model_dump_json()


def test_session_snapshot_exposes_exactly_the_frozen_fields() -> None:
    assert set(SessionSnapshot.model_fields) == {
        "session_id",
        "workflow_state",
        "current_intent_revision_id",
        "current_execution_revision_id",
        "latest_confirmation_id",
        "pending_question_id",
        "pending_question_payload",
        "message_ids",
    }
    snapshot = SessionSnapshot(session_id="ses_1", workflow_state=WorkflowState.UNDERSTANDING)
    assert snapshot.current_intent_revision_id is None
    assert snapshot.current_execution_revision_id is None
    assert snapshot.latest_confirmation_id is None
    assert snapshot.pending_question_id is None
    assert snapshot.pending_question_payload is None
    assert snapshot.message_ids == ()


def test_session_snapshot_coerces_state_strings_and_is_immutable() -> None:
    snapshot = SessionSnapshot(session_id="ses_1", workflow_state="WAITING_REVIEW")
    assert snapshot.workflow_state is WorkflowState.WAITING_REVIEW
    with pytest.raises(ValidationError):
        snapshot.workflow_state = WorkflowState.COMPLETED  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SessionSnapshot(
            session_id="ses_1", workflow_state=WorkflowState.UNDERSTANDING, extra=1
        )


def test_stored_artifact_exposes_exactly_the_envelope_fields() -> None:
    assert set(StoredArtifact.model_fields) == {
        "artifact_id",
        "session_id",
        "refs",
        "payload",
        "created_at",
    }
    artifact = StoredArtifact(
        artifact_id="pra_1",
        session_id="ses_1",
        refs={"intent_revision_id": "irev_1", "confirmation_id": "cnf_1"},
        payload='{"prompt": "x"}',
        created_at=FIXED_TIME,
    )
    assert artifact.refs == {"intent_revision_id": "irev_1", "confirmation_id": "cnf_1"}
    assert artifact.created_at == FIXED_TIME


def test_stored_artifact_requires_refs_and_payload() -> None:
    with pytest.raises(ValidationError):
        StoredArtifact(artifact_id="pra_1", session_id="ses_1", payload="{}")
    with pytest.raises(ValidationError):
        StoredArtifact(
            artifact_id="pra_1",
            session_id="ses_1",
            refs={"intent_revision_id": "irev_1"},
        )


def test_all_records_are_frozen_with_forbidden_extra_fields() -> None:
    for model in (ConfirmationRecord, SessionSnapshot, StoredArtifact):
        config = model.model_config
        assert config.get("frozen") is True, model.__name__
        assert config.get("extra") == "forbid", model.__name__
