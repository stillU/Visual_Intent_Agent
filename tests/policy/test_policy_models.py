"""模型形状测试：Step 03 policy 数据模型的冻结字段面与默认值。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from visual_intent_agent.domain import Issue, VisualIntent
from visual_intent_agent.policy import (
    DecisionPolicy,
    IntentResolution,
    Materiality,
    PolicyAction,
    QuestionSpec,
    UnresolvedDecision,
)


def test_materiality_has_exactly_three_frozen_values() -> None:
    assert {member.value for member in Materiality} == {
        "core",
        "perceptual",
        "implementation",
    }


def test_policy_action_has_exactly_three_frozen_values() -> None:
    assert {member.value for member in PolicyAction} == {"block", "omit", "runtime"}


def test_decision_policy_has_exactly_the_seven_frozen_fields() -> None:
    assert set(DecisionPolicy.model_fields) == {
        "path",
        "materiality",
        "required_if",
        "delegatable",
        "dependencies",
        "conflict_rules",
        "invalidates_realization",
    }


def test_unresolved_decision_has_exactly_the_four_frozen_fields() -> None:
    assert set(UnresolvedDecision.model_fields) == {
        "path",
        "materiality",
        "action",
        "reason",
    }


def test_question_spec_has_exactly_the_frozen_fields_and_defaults() -> None:
    assert set(QuestionSpec.model_fields) == {
        "target_path",
        "reason",
        "allow_delegate",
        "allow_custom",
        "suggested_values",
        "question_text",
        "question_id",
    }
    spec = QuestionSpec(target_path="style.primary", reason="r", allow_delegate=True)
    assert spec.allow_custom is True
    assert spec.suggested_values == ()
    assert spec.question_text is None
    assert spec.question_id is None
    assert isinstance(spec.suggested_values, tuple)


def test_intent_resolution_has_exactly_the_frozen_fields_and_defaults() -> None:
    assert set(IntentResolution.model_fields) == {
        "intent",
        "applied_deltas",
        "issues",
        "unresolved_decisions",
        "conflicts",
        "question",
        "ready_for_confirmation",
    }
    resolution = IntentResolution(intent=VisualIntent())
    assert resolution.applied_deltas == []
    assert resolution.issues == []
    assert resolution.unresolved_decisions == []
    assert resolution.conflicts == []
    assert resolution.question is None
    assert resolution.ready_for_confirmation is False


def test_intent_resolution_requires_an_intent() -> None:
    with pytest.raises(ValidationError):
        IntentResolution()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "model,payload",
    [
        (
            DecisionPolicy,
            {
                "path": "subject.description",
                "materiality": "core",
                "required_if": "always",
                "delegatable": False,
                "invalidates_realization": True,
            },
        ),
        (
            UnresolvedDecision,
            {
                "path": "subject.description",
                "materiality": "core",
                "action": "block",
                "reason": "r",
            },
        ),
        (
            QuestionSpec,
            {"target_path": "style.primary", "reason": "r", "allow_delegate": True},
        ),
        (IntentResolution, {"intent": VisualIntent()}),
    ],
)
def test_policy_models_forbid_unknown_extra_fields(model: type, payload: dict) -> None:
    assert model(**payload) is not None  # 合法载荷先通过
    with pytest.raises(ValidationError):
        model(**payload, this_field_is_not_frozen=1)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: DecisionPolicy(
            path="subject.description",
            materiality=Materiality.CORE,
            required_if="always",
            delegatable=False,
            invalidates_realization=True,
        ),
        lambda: QuestionSpec(
            target_path="style.primary", reason="r", allow_delegate=True
        ),
        lambda: IntentResolution(intent=VisualIntent()),
    ],
)
def test_policy_models_are_frozen(factory) -> None:
    instance = factory()
    first_field = next(iter(type(instance).model_fields))
    with pytest.raises(ValidationError):
        setattr(instance, first_field, None)


def test_default_list_fields_are_not_shared_between_instances() -> None:
    first = IntentResolution(intent=VisualIntent())
    second = IntentResolution(intent=VisualIntent())
    assert first.applied_deltas is not second.applied_deltas
    assert first.unresolved_decisions is not second.unresolved_decisions
    assert first.conflicts is not second.conflicts

    policy_a = DecisionPolicy(
        path="subject.description",
        materiality=Materiality.CORE,
        required_if="always",
        delegatable=False,
        invalidates_realization=True,
    )
    policy_b = DecisionPolicy(
        path="style.primary",
        materiality=Materiality.CORE,
        required_if="always",
        delegatable=True,
        invalidates_realization=True,
    )
    assert policy_a.dependencies is not policy_b.dependencies
    assert policy_a.conflict_rules is not policy_b.conflict_rules


def test_decision_policy_round_trips_through_json() -> None:
    policy = DecisionPolicy(
        path="environment.mode",
        materiality=Materiality.CORE,
        required_if="always",
        delegatable=True,
        dependencies=["environment.location"],
        conflict_rules=["hard_conflict.environment_mode_location"],
        invalidates_realization=True,
    )
    assert DecisionPolicy.model_validate_json(policy.model_dump_json()) == policy


def test_intent_resolution_carries_plain_issues_for_conflicts() -> None:
    # conflicts 以 Issue 表达（与 issues 同一类型），question 为 QuestionSpec。
    conflict = Issue(code="policy.hard_conflict.x", message="m", path="environment.mode")
    resolution = IntentResolution(
        intent=VisualIntent(),
        conflicts=[conflict],
        question=QuestionSpec(
            target_path="environment.mode", reason="m", allow_delegate=True
        ),
    )
    assert resolution.conflicts[0].code == "policy.hard_conflict.x"
    assert resolution.question is not None
    assert resolution.question.question_text is None
