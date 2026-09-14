"""VisualIntent / Resolution / ResolutionRecord 合同测试。

覆盖任务书必测场景：空 Intent 可创建且缺失不被自动标记 delegated；七类 Facet
序列化/反序列化 round-trip；未知顶层字段被拒绝；非法 Resolution 被拒绝。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from visual_intent_agent.domain import INTENT_PATHS, VisualIntent
from visual_intent_agent.domain.intent import (
    CameraFacet,
    ColorFacet,
    CompositionFacet,
    EnvironmentFacet,
    LightingFacet,
    Resolution,
    ResolutionRecord,
    StyleFacet,
    SubjectFacet,
)
from visual_intent_agent.domain.issue import EvidenceRef

FACET_FIELD_NAMES = (
    "subject",
    "composition",
    "environment",
    "style",
    "lighting",
    "camera",
    "color",
)

#: (facet, field, value) —— 逐字段覆盖全部 12 条路径
FACET_CASES = [
    ("subject", "description", "a red fox on a mossy rock"),
    ("subject", "count", 2),
    ("subject", "pose_action", "leaping over a fallen log"),
    ("composition", "framing", "medium shot, subject centered"),
    ("environment", "mode", "outdoor forest at dawn"),
    ("environment", "location", "temperate woodland clearing"),
    ("style", "primary", "watercolor illustration"),
    ("style", "description", "soft edges with paper texture"),
    ("lighting", "character", "soft diffused morning light"),
    ("camera", "angle", "eye level"),
    ("camera", "depth_of_field", "shallow, background blurred"),
    ("color", "palette", "muted earth tones with amber accent"),
]


def _evidence(message_id: str = "msg_a", fragment: str | None = None) -> EvidenceRef:
    return EvidenceRef(message_id=message_id, fragment=fragment)


def _complete_intent() -> VisualIntent:
    return VisualIntent(
        subject={"description": "a red fox", "count": 1, "pose_action": "sitting"},
        composition={"framing": "medium shot"},
        environment={"mode": "outdoor forest", "location": "woodland clearing"},
        style={"primary": "watercolor", "description": "soft edges"},
        lighting={"character": "soft morning light"},
        camera={"angle": "eye level", "depth_of_field": "shallow"},
        color={"palette": "muted earth tones"},
        resolutions={
            path: ResolutionRecord(resolution=Resolution.USER_SPECIFIED, evidence_refs=[_evidence()])
            for path in sorted(INTENT_PATHS)
        },
        pinned_paths={"color.palette", "style.primary"},
    )


def test_empty_intent_can_be_created() -> None:
    intent = VisualIntent()
    assert intent.schema_version == "v1"
    assert intent.intent_id is None
    assert intent.resolutions == {}
    assert intent.pinned_paths == frozenset()


def test_missing_is_not_auto_marked_as_user_delegated() -> None:
    intent = VisualIntent()
    assert intent.resolutions == {}
    for path in INTENT_PATHS:
        assert path not in intent.resolutions

    # 只设置一个路径，不会为其它路径合成任何 Resolution 记录
    partial = VisualIntent(subject={"description": "a cat"})
    assert partial.resolutions == {}
    assert partial.environment.location is None
    assert "environment.location" not in partial.resolutions
    assert Resolution.USER_DELEGATED.value == "user_delegated"


@pytest.mark.parametrize(("facet", "field", "value"), FACET_CASES)
def test_each_facet_field_round_trips(facet: str, field: str, value: object) -> None:
    intent = VisualIntent(**{facet: {field: value}})
    assert getattr(getattr(intent, facet), field) == value
    restored = VisualIntent.model_validate_json(intent.model_dump_json())
    assert restored == intent
    assert restored.model_dump(mode="json") == intent.model_dump(mode="json")


@pytest.mark.parametrize("facet_name", FACET_FIELD_NAMES)
def test_each_facet_model_round_trips(facet_name: str) -> None:
    facet_model = VisualIntent.model_fields[facet_name].annotation
    filled = {
        field: (1 if field == "count" else f"{facet_name}.{field}")
        for field in facet_model.model_fields
    }
    facet = facet_model(**filled)
    restored = facet_model.model_validate_json(facet.model_dump_json())
    assert restored == facet


def test_all_seven_facets_round_trip_together() -> None:
    intent = _complete_intent()
    restored = VisualIntent.model_validate_json(intent.model_dump_json())
    assert restored == intent
    # 七类 Facet 的值全部保留
    for facet_name in FACET_FIELD_NAMES:
        assert getattr(restored, facet_name) == getattr(intent, facet_name)


def test_complete_intent_covers_all_twelve_paths_with_values() -> None:
    intent = _complete_intent()
    values = {
        f"{facet}.{field}": getattr(getattr(intent, facet), field)
        for facet in FACET_FIELD_NAMES
        for field in VisualIntent.model_fields[facet].annotation.model_fields
    }
    assert set(values) == set(INTENT_PATHS)
    assert all(value is not None for value in values.values())
    assert set(intent.resolutions) == set(INTENT_PATHS)


def test_unknown_top_level_field_is_rejected() -> None:
    for payload in (
        {"mood": "joyful"},
        {"lens_model": "50mm f/1.4"},
        {"wardrobe": "red raincoat"},
        {"material": "brushed aluminum"},
        {"rag_context": "anything"},
    ):
        with pytest.raises(ValidationError):
            VisualIntent.model_validate(payload)


def test_unknown_facet_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VisualIntent.model_validate({"subject": {"mood": "joyful"}})
    with pytest.raises(ValidationError):
        SubjectFacet.model_validate({"description": "a cat", "species": "fox"})
    with pytest.raises(ValidationError):
        CameraFacet.model_validate({"lens_model": "50mm"})


def test_illegal_resolution_is_rejected() -> None:
    for bad in ("delegated", "user_delegated ", "specified", "", "USER_DELEGATED", 3):
        with pytest.raises(ValidationError):
            ResolutionRecord(resolution=bad)
    with pytest.raises(ValidationError):
        VisualIntent.model_validate(
            {"resolutions": {"subject.description": {"resolution": "assumed_by_system"}}}
        )


def test_all_four_resolutions_are_accepted_and_bound_to_paths() -> None:
    expected = {
        Resolution.USER_SPECIFIED,
        Resolution.USER_CONFIRMED_PROPOSAL,
        Resolution.USER_DELEGATED,
        Resolution.NOT_APPLICABLE,
    }
    assert set(Resolution) == expected
    assert {member.value for member in Resolution} == {
        "user_specified",
        "user_confirmed_proposal",
        "user_delegated",
        "not_applicable",
    }

    intent = VisualIntent(
        resolutions={
            "subject.description": {"resolution": "user_specified"},
            "subject.count": {"resolution": "user_confirmed_proposal"},
            "environment.location": {"resolution": "user_delegated"},
            "lighting.character": {"resolution": "not_applicable"},
        }
    )
    assert intent.resolutions["environment.location"].resolution is Resolution.USER_DELEGATED
    assert intent.resolutions["subject.description"].resolution is Resolution.USER_SPECIFIED
    # Resolution 绑定在具体路径上，而不是 Intent 顶层的全局状态
    assert "resolution" not in VisualIntent.model_fields


def test_resolution_key_outside_whitelist_is_rejected() -> None:
    for bad_path in ("subject.mood", "wardrobe.color", "subject", "subject.description.extra"):
        with pytest.raises(ValidationError):
            VisualIntent.model_validate({"resolutions": {bad_path: {"resolution": "user_specified"}}})


def test_pinned_path_outside_whitelist_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VisualIntent(pinned_paths={"subject.mood"})
    assert VisualIntent(pinned_paths={"subject.description"}).pinned_paths == frozenset(
        {"subject.description"}
    )


def test_subject_count_must_be_positive() -> None:
    assert SubjectFacet(count=1).count == 1
    for bad in (0, -3):
        with pytest.raises(ValidationError):
            SubjectFacet(count=bad)


def test_facet_models_accept_only_their_frozen_fields() -> None:
    assert set(SubjectFacet.model_fields) == {"description", "count", "pose_action"}
    assert set(CompositionFacet.model_fields) == {"framing"}
    assert set(EnvironmentFacet.model_fields) == {"mode", "location"}
    assert set(StyleFacet.model_fields) == {"primary", "description"}
    assert set(LightingFacet.model_fields) == {"character"}
    assert set(CameraFacet.model_fields) == {"angle", "depth_of_field"}
    assert set(ColorFacet.model_fields) == {"palette"}
    assert set(VisualIntent.model_fields) == {
        "schema_version",
        "intent_id",
        "subject",
        "composition",
        "environment",
        "style",
        "lighting",
        "camera",
        "color",
        "resolutions",
        "pinned_paths",
    }


def test_unsupported_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VisualIntent.model_validate({"schema_version": "v2"})


def test_intent_is_frozen() -> None:
    intent = VisualIntent()
    with pytest.raises(ValidationError):
        intent.intent_id = "intent_1"  # type: ignore[misc]


def test_mutable_default_containers_are_not_shared_between_instances() -> None:
    first_record = ResolutionRecord(resolution="user_specified")
    second_record = ResolutionRecord(resolution="user_specified")
    assert first_record.evidence_refs is not second_record.evidence_refs

    first_intent = VisualIntent()
    second_intent = VisualIntent()
    assert first_intent.resolutions is not second_intent.resolutions
