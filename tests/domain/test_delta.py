"""IntentDelta 合同测试：四值 operation 与形状约束（ARCHITECTURE.md 4）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from visual_intent_agent.domain import DeltaOperation, IntentDelta, Resolution
from visual_intent_agent.domain.issue import EvidenceRef


def test_exactly_four_delta_operations() -> None:
    assert set(DeltaOperation) == {
        DeltaOperation.SET,
        DeltaOperation.CLEAR,
        DeltaOperation.PIN,
        DeltaOperation.UNPIN,
    }
    assert {member.value for member in DeltaOperation} == {"SET", "CLEAR", "PIN", "UNPIN"}


def test_illegal_delta_operation_is_rejected() -> None:
    for bad in ("DELETE", "set", "Set", "REMOVE", "UPDATE", "", None, 1):
        with pytest.raises(ValidationError):
            IntentDelta(operation=bad, path="subject.description", value="a cat")


def test_set_missing_value_and_resolution_is_rejected() -> None:
    with pytest.raises(ValidationError):
        IntentDelta(operation="SET", path="subject.count")
    # 显式传 null 等价于缺失
    with pytest.raises(ValidationError):
        IntentDelta(operation="SET", path="subject.count", value=None, resolution=None)


def test_set_with_value_only_is_accepted() -> None:
    delta = IntentDelta(operation="SET", path="subject.description", value="a cat")
    assert delta.value == "a cat"
    assert delta.resolution is None


def test_set_with_resolution_only_is_accepted() -> None:
    delta = IntentDelta(operation="SET", path="environment.location", resolution="user_delegated")
    assert delta.value is None
    assert delta.resolution is Resolution.USER_DELEGATED


def test_set_with_both_value_and_resolution_is_accepted() -> None:
    delta = IntentDelta(
        operation="SET",
        path="subject.count",
        value=2,
        resolution="user_specified",
    )
    assert delta.value == 2
    assert delta.resolution is Resolution.USER_SPECIFIED


@pytest.mark.parametrize("operation", ["CLEAR", "PIN", "UNPIN"])
def test_clear_pin_unpin_must_not_carry_value(operation: str) -> None:
    with pytest.raises(ValidationError):
        IntentDelta(operation=operation, path="color.palette", value="teal")


@pytest.mark.parametrize("operation", ["CLEAR", "PIN", "UNPIN"])
def test_clear_pin_unpin_must_not_carry_resolution(operation: str) -> None:
    with pytest.raises(ValidationError):
        IntentDelta(operation=operation, path="color.palette", resolution="user_delegated")


@pytest.mark.parametrize("operation", ["CLEAR", "PIN", "UNPIN"])
def test_clear_pin_unpin_without_payload_are_accepted(operation: str) -> None:
    delta = IntentDelta(operation=operation, path="color.palette")
    assert delta.value is None
    assert delta.resolution is None


def test_delta_json_round_trip_preserves_int_value_type() -> None:
    delta = IntentDelta(operation="SET", path="subject.count", value=2)
    restored = IntentDelta.model_validate_json(delta.model_dump_json())
    assert restored == delta
    assert isinstance(restored.value, int)


def test_evidence_refs_default_empty_and_round_trip() -> None:
    bare = IntentDelta(operation="PIN", path="color.palette")
    assert bare.evidence_refs == []

    ref = EvidenceRef(message_id="msg_abc", fragment="keep the palette", pending_question_id="qst_1")
    delta = IntentDelta(
        operation="SET",
        path="subject.description",
        value="a fox",
        evidence_refs=[ref],
    )
    restored = IntentDelta.model_validate_json(delta.model_dump_json())
    assert restored == delta
    assert restored.evidence_refs[0].message_id == "msg_abc"


def test_delta_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        IntentDelta.model_validate(
            {"operation": "SET", "path": "subject.description", "value": "a cat", "confidence": 0.9}
        )


def test_delta_path_is_a_plain_str_at_model_layer() -> None:
    # 白名单校验属 Step 02 Validator；本步骤不在此拒绝非白名单路径
    delta = IntentDelta(operation="SET", path="subject.mood", value="joyful")
    assert delta.path == "subject.mood"


def test_delta_is_frozen() -> None:
    delta = IntentDelta(operation="SET", path="subject.description", value="a cat")
    with pytest.raises(ValidationError):
        delta.value = "a dog"  # type: ignore[misc]
