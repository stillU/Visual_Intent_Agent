"""IntentRevision / ExecutionRevision 合同测试。

覆盖任务书必测场景：IntentRevision round-trip 后内容不变；并钉住 tz-aware UTC
时间戳约定（ARCHITECTURE.md 5.2）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from visual_intent_agent.domain import (
    ExecutionRevision,
    IntentDelta,
    IntentRevision,
    VisualIntent,
)


def _intent() -> VisualIntent:
    return VisualIntent(
        subject={"description": "a red fox", "count": 1},
        style={"primary": "watercolor"},
        pinned_paths={"style.primary"},
    )


def _delta() -> IntentDelta:
    return IntentDelta(operation="SET", path="subject.description", value="a red fox")


def test_genesis_intent_revision_round_trip_content_unchanged() -> None:
    revision = IntentRevision(
        intent_revision_id="irev_1",
        session_id="ses_1",
        parent_revision_id=None,
        intent=VisualIntent(),
    )
    assert revision.applied_deltas == []
    dumped = revision.model_dump(mode="json")
    restored = IntentRevision.model_validate(dumped)
    assert restored == revision
    assert restored.model_dump(mode="json") == dumped


def test_full_intent_revision_round_trip_content_unchanged() -> None:
    revision = IntentRevision(
        intent_revision_id="irev_2",
        session_id="ses_1",
        parent_revision_id="irev_1",
        intent=_intent(),
        applied_deltas=[_delta()],
        created_at=datetime(2026, 1, 15, 8, 31, 30, tzinfo=timezone.utc),
    )
    dumped = revision.model_dump(mode="json")
    restored = IntentRevision.model_validate_json(json.dumps(dumped))
    assert restored == revision
    assert restored.model_dump(mode="json") == dumped
    assert restored.intent == revision.intent
    assert restored.intent.pinned_paths == frozenset({"style.primary"})
    assert restored.applied_deltas == revision.applied_deltas


def test_revision_fields_are_all_present() -> None:
    assert set(IntentRevision.model_fields) == {
        "schema_version",
        "intent_revision_id",
        "session_id",
        "parent_revision_id",
        "intent",
        "applied_deltas",
        "created_at",
    }
    assert set(ExecutionRevision.model_fields) == {
        "schema_version",
        "execution_revision_id",
        "session_id",
        "parent_revision_id",
        "target_model",
        "output_size",
        "created_at",
    }


def test_parent_revision_id_is_required_but_may_be_none() -> None:
    with pytest.raises(ValidationError):
        IntentRevision(intent_revision_id="irev_1", session_id="ses_1", intent=VisualIntent())
    assert (
        IntentRevision(
            intent_revision_id="irev_1",
            session_id="ses_1",
            parent_revision_id=None,
            intent=VisualIntent(),
        ).parent_revision_id
        is None
    )


def test_created_at_defaults_to_aware_utc() -> None:
    revision = IntentRevision(
        intent_revision_id="irev_1",
        session_id="ses_1",
        parent_revision_id=None,
        intent=VisualIntent(),
    )
    assert revision.created_at.tzinfo is not None
    assert revision.created_at.utcoffset() == timedelta(0)


def test_naive_created_at_is_rejected() -> None:
    with pytest.raises(ValidationError):
        IntentRevision(
            intent_revision_id="irev_1",
            session_id="ses_1",
            parent_revision_id=None,
            intent=VisualIntent(),
            created_at=datetime(2026, 1, 15, 8, 30, 0),
        )
    with pytest.raises(ValidationError):
        ExecutionRevision(
            execution_revision_id="erev_1",
            session_id="ses_1",
            parent_revision_id=None,
            target_model="qwen-image-3.0",
            created_at=datetime(2026, 1, 15, 8, 30, 0),
        )


def test_non_utc_aware_created_at_is_normalized_to_utc() -> None:
    plus_eight = timezone(timedelta(hours=8))
    revision = IntentRevision(
        intent_revision_id="irev_1",
        session_id="ses_1",
        parent_revision_id=None,
        intent=VisualIntent(),
        created_at=datetime(2026, 1, 15, 16, 30, 0, tzinfo=plus_eight),
    )
    assert revision.created_at == datetime(2026, 1, 15, 8, 30, 0, tzinfo=timezone.utc)
    assert revision.created_at.utcoffset() == timedelta(0)


def test_json_serialization_uses_iso8601_with_plus_zero_offset() -> None:
    for revision in (
        IntentRevision(
            intent_revision_id="irev_1",
            session_id="ses_1",
            parent_revision_id=None,
            intent=VisualIntent(),
            created_at=datetime(2026, 1, 15, 8, 30, 0, tzinfo=timezone.utc),
        ),
        ExecutionRevision(
            execution_revision_id="erev_1",
            session_id="ses_1",
            parent_revision_id=None,
            target_model="qwen-image-3.0",
            created_at=datetime(2026, 1, 15, 8, 30, 0, tzinfo=timezone.utc),
        ),
    ):
        payload = json.loads(revision.model_dump_json())
        assert payload["created_at"] == "2026-01-15T08:30:00+00:00"
        # python 模式仍保留 datetime 对象
        assert isinstance(revision.model_dump()["created_at"], datetime)


def test_z_suffix_input_is_accepted_and_re_emitted_as_plus_zero() -> None:
    revision = IntentRevision.model_validate(
        {
            "intent_revision_id": "irev_1",
            "session_id": "ses_1",
            "parent_revision_id": None,
            "intent": {"schema_version": "v1"},
            "created_at": "2026-01-15T08:30:00Z",
        }
    )
    assert revision.created_at == datetime(2026, 1, 15, 8, 30, 0, tzinfo=timezone.utc)
    assert json.loads(revision.model_dump_json())["created_at"] == "2026-01-15T08:30:00+00:00"


def test_execution_revision_minimal_shape_and_round_trip() -> None:
    revision = ExecutionRevision(
        execution_revision_id="erev_1",
        session_id="ses_1",
        parent_revision_id=None,
        target_model="qwen-image-3.0",
    )
    assert revision.schema_version == "v1"
    assert revision.output_size == "1024x1024"
    restored = ExecutionRevision.model_validate_json(revision.model_dump_json())
    assert restored == revision


def test_execution_revision_requires_target_model() -> None:
    with pytest.raises(ValidationError):
        ExecutionRevision(
            execution_revision_id="erev_1",
            session_id="ses_1",
            parent_revision_id=None,
        )


@pytest.mark.parametrize("model", [IntentRevision, ExecutionRevision])
def test_revision_models_reject_unknown_fields_and_unsupported_version(model: type) -> None:
    base = {
        "session_id": "ses_1",
        "parent_revision_id": None,
        "created_at": "2026-01-15T08:30:00+00:00",
    }
    if model is IntentRevision:
        base.update({"intent_revision_id": "irev_1", "intent": {"schema_version": "v1"}})
    else:
        base.update({"execution_revision_id": "erev_1", "target_model": "qwen-image-3.0"})

    with pytest.raises(ValidationError):
        model.model_validate({**base, "summary_hash": "deadbeef"})
    with pytest.raises(ValidationError):
        model.model_validate({**base, "schema_version": "v2"})


@pytest.mark.parametrize("model", [IntentRevision, ExecutionRevision])
def test_revision_models_are_frozen(model: type) -> None:
    payload = {
        "session_id": "ses_1",
        "parent_revision_id": None,
    }
    if model is IntentRevision:
        payload["intent_revision_id"] = "irev_1"
        payload["intent"] = {"schema_version": "v1"}
    else:
        payload["execution_revision_id"] = "erev_1"
        payload["target_model"] = "qwen-image-3.0"
    revision = model.model_validate(payload)
    with pytest.raises(ValidationError):
        revision.session_id = "ses_2"  # type: ignore[misc]
