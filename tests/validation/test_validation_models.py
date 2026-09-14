"""Step 02 结果模型测试：形状、不可变性、默认值不共享。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from visual_intent_agent.domain import (
    DeltaOperation,
    IntentDelta,
    Issue,
    Resolution,
    VisualIntent,
)
from visual_intent_agent.validation import (
    ChangeSummary,
    EvidenceContext,
    ReduceResult,
    RejectedDelta,
    ValidationResult,
)
from visual_intent_agent.validation.models import _FROZEN  # type: ignore[attr-defined]

CONTRACT_MODELS = [
    EvidenceContext,
    RejectedDelta,
    ValidationResult,
    ChangeSummary,
    ReduceResult,
]


def make_delta() -> IntentDelta:
    return IntentDelta(operation=DeltaOperation.CLEAR, path="style.primary")


def test_evidence_context_shape_and_defaults() -> None:
    context = EvidenceContext(available_message_ids=frozenset({"msg_1"}))
    assert context.available_message_ids == frozenset({"msg_1"})
    assert context.pending_question_id is None
    assert context.pending_question_path is None
    assert context.model_dump() == {
        "available_message_ids": frozenset({"msg_1"}),
        "pending_question_id": None,
        "pending_question_path": None,
    }


def test_validation_result_defaults_are_empty() -> None:
    result = ValidationResult()
    assert result.accepted == []
    assert result.rejected == []
    assert result.issues == []


def test_validation_result_distinguishes_accepted_rejected_and_issues() -> None:
    accepted = make_delta()
    rejected_delta = IntentDelta(operation=DeltaOperation.CLEAR, path="not.a.path")
    rejected = RejectedDelta(
        delta=rejected_delta,
        issues=[Issue(code="validation.path_not_whitelisted", message="nope")],
    )
    result = ValidationResult(
        accepted=[accepted], rejected=[rejected], issues=list(rejected.issues)
    )
    assert result.accepted == [accepted]
    assert result.rejected[0].delta == rejected_delta
    assert [issue.code for issue in result.issues] == ["validation.path_not_whitelisted"]


def test_change_summary_defaults_are_false_and_empty() -> None:
    summary = ChangeSummary()
    assert summary.changed_paths == []
    assert summary.pinned_paths == []
    assert summary.unpinned_paths == []
    assert summary.cleared_paths == []
    assert summary.confirmation_invalidated is False


def test_reduce_result_requires_intent_and_defaults_summary() -> None:
    result = ReduceResult(intent=VisualIntent())
    assert result.intent == VisualIntent()
    assert result.change_summary == ChangeSummary()


@pytest.mark.parametrize("model", CONTRACT_MODELS, ids=lambda m: m.__name__)
def test_models_are_frozen_and_forbid_extra_fields(model: type) -> None:
    assert model.model_config.get("frozen") is True
    assert model.model_config.get("extra") == "forbid"


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (EvidenceContext, {"available_message_ids": frozenset()}),
        (RejectedDelta, {"delta": make_delta()}),
        (ValidationResult, {}),
        (ChangeSummary, {}),
        (ReduceResult, {"intent": VisualIntent()}),
    ],
    ids=lambda value: value.__name__ if isinstance(value, type) else "",
)
def test_models_accept_only_their_frozen_fields(model: type, payload: dict) -> None:
    with pytest.raises(ValidationError) as excinfo:
        model(**payload, unexpected_field=1)
    assert excinfo.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (ValidationResult, {}),
        (ChangeSummary, {}),
        (RejectedDelta, {"delta": make_delta()}),
        (ReduceResult, {"intent": VisualIntent()}),
    ],
    ids=["ValidationResult", "ChangeSummary", "RejectedDelta", "ReduceResult"],
)
def test_default_list_fields_are_not_shared_between_instances(model: type, payload: dict) -> None:
    first = model(**payload)
    second = model(**payload)
    for field_name in ("accepted", "rejected", "issues", "changed_paths", "cleared_paths"):
        if field_name in type(first).model_fields:
            first_list = getattr(first, field_name)
            second_list = getattr(second, field_name)
            assert first_list == []
            assert first_list is not second_list


def test_frozen_models_reject_attribute_assignment() -> None:
    summary = ChangeSummary(changed_paths=["style.primary"])
    with pytest.raises(ValidationError):
        summary.confirmation_invalidated = True  # type: ignore[misc]
    result = ReduceResult(intent=VisualIntent())
    with pytest.raises(ValidationError):
        result.intent = VisualIntent()  # type: ignore[misc]


def test_frozen_config_is_shared_by_every_step_02_model() -> None:
    assert _FROZEN["frozen"] is True
    assert _FROZEN["extra"] == "forbid"


def test_change_summary_round_trips_through_json() -> None:
    summary = ChangeSummary(
        changed_paths=["style.primary"],
        pinned_paths=["color.palette"],
        unpinned_paths=["camera.angle"],
        cleared_paths=["environment.location"],
        confirmation_invalidated=True,
    )
    assert ChangeSummary.model_validate_json(summary.model_dump_json()) == summary


def test_resolution_and_issue_are_reused_from_step_01_contracts() -> None:
    # Step 02 不复制上游模型：RejectedDelta.issues 就是 domain.Issue。
    rejected = RejectedDelta(
        delta=IntentDelta(
            operation=DeltaOperation.SET,
            path="style.primary",
            value="x",
            resolution=Resolution.USER_SPECIFIED,
        ),
        issues=[Issue(code="validation.example", message="m")],
    )
    assert isinstance(rejected.issues[0], Issue)
    assert rejected.delta.resolution is Resolution.USER_SPECIFIED
