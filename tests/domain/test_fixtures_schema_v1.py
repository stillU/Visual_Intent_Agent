"""Schema v1 fixture 测试：每个 fixture 可加载、可 round-trip、重复加载稳定。

fixture 目录：`tests/fixtures/schema_v1/`（版本化 JSON，禁止真实凭据与临时 URL）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest
from pydantic import BaseModel

from visual_intent_agent.domain import (
    ExecutionRevision,
    IntentDelta,
    IntentRevision,
    Issue,
    Resolution,
    VisualIntent,
)

FIXTURE_MODELS: dict[str, type[BaseModel]] = {
    "empty_intent.json": VisualIntent,
    "complete_intent.json": VisualIntent,
    "partial_intent_with_delegation.json": VisualIntent,
    "delta_set.json": IntentDelta,
    "delta_set_resolution_only.json": IntentDelta,
    "delta_clear.json": IntentDelta,
    "delta_pin.json": IntentDelta,
    "delta_unpin.json": IntentDelta,
    "intent_revision_genesis.json": IntentRevision,
    "intent_revision.json": IntentRevision,
    "execution_revision.json": ExecutionRevision,
    "issue.json": Issue,
}

FIXTURE_NAMES = sorted(FIXTURE_MODELS)


def test_fixture_directory_contains_exactly_the_covered_files(
    schema_fixture_dir: Path,
) -> None:
    on_disk = sorted(path.name for path in schema_fixture_dir.glob("*.json"))
    assert on_disk == FIXTURE_NAMES


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_loads_and_round_trips(
    name: str, load_schema_fixture: Callable[[str], Any]
) -> None:
    model = FIXTURE_MODELS[name]
    parsed = model.model_validate(load_schema_fixture(name))
    dumped = parsed.model_dump(mode="json")
    reparsed = model.model_validate(dumped)
    assert reparsed == parsed
    assert reparsed.model_dump(mode="json") == dumped
    # JSON 文本 round-trip 也稳定
    assert model.model_validate_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_repeated_loading_is_stable(
    name: str, load_schema_fixture: Callable[[str], Any]
) -> None:
    model = FIXTURE_MODELS[name]
    first = model.model_validate(load_schema_fixture(name))
    for _ in range(3):
        again = model.model_validate(load_schema_fixture(name))
        assert again == first
        assert again.model_dump_json() == first.model_dump_json()
        assert again.model_dump(mode="json") == first.model_dump(mode="json")


def test_fixture_files_are_valid_json_objects(load_schema_fixture: Callable[[str], Any]) -> None:
    for name in FIXTURE_NAMES:
        assert isinstance(load_schema_fixture(name), dict)


def test_empty_intent_fixture_is_empty_and_not_delegated(
    load_schema_fixture: Callable[[str], Any],
) -> None:
    intent = VisualIntent.model_validate(load_schema_fixture("empty_intent.json"))
    assert intent.schema_version == "v1"
    assert intent.resolutions == {}
    assert intent.pinned_paths == frozenset()
    assert intent.subject.description is None


def test_complete_intent_fixture_covers_all_twelve_paths(
    load_schema_fixture: Callable[[str], Any],
) -> None:
    intent = VisualIntent.model_validate(load_schema_fixture("complete_intent.json"))
    assert set(intent.resolutions) == {f"{facet}.{field}" for facet, field in _facet_field_pairs()}
    assert len(intent.resolutions) == 12
    assert intent.subject.count == 1
    assert intent.pinned_paths == frozenset({"color.palette", "style.primary"})


def test_partial_intent_fixture_distinguishes_missing_from_delegated(
    load_schema_fixture: Callable[[str], Any],
) -> None:
    intent = VisualIntent.model_validate(load_schema_fixture("partial_intent_with_delegation.json"))
    # 显式委派：有 ResolutionRecord 且值可以为空
    assert intent.resolutions["environment.location"].resolution is Resolution.USER_DELEGATED
    assert intent.environment.location is None
    # not_applicable 也是显式记录
    assert intent.resolutions["lighting.character"].resolution is Resolution.NOT_APPLICABLE
    # 真正缺失：既无值也无记录，绝不被当作 delegated
    assert "subject.count" not in intent.resolutions
    assert intent.subject.count is None
    assert intent.composition.framing is None
    assert "composition.framing" not in intent.resolutions


def test_delta_fixtures_have_the_expected_operations(
    load_schema_fixture: Callable[[str], Any],
) -> None:
    expected = {
        "delta_set.json": "SET",
        "delta_set_resolution_only.json": "SET",
        "delta_clear.json": "CLEAR",
        "delta_pin.json": "PIN",
        "delta_unpin.json": "UNPIN",
    }
    for name, operation in expected.items():
        delta = IntentDelta.model_validate(load_schema_fixture(name))
        assert delta.operation.value == operation
        assert delta.path
        assert delta.evidence_refs and delta.evidence_refs[0].message_id.startswith("msg_")

    resolution_only = IntentDelta.model_validate(
        load_schema_fixture("delta_set_resolution_only.json")
    )
    assert resolution_only.value is None
    assert resolution_only.resolution is Resolution.USER_DELEGATED
    assert resolution_only.evidence_refs[0].pending_question_id is not None


def test_revision_fixture_preserves_chain_and_instant(
    load_schema_fixture: Callable[[str], Any],
) -> None:
    genesis = IntentRevision.model_validate(load_schema_fixture("intent_revision_genesis.json"))
    revision = IntentRevision.model_validate(load_schema_fixture("intent_revision.json"))
    assert genesis.parent_revision_id is None
    assert genesis.intent.resolutions == {}
    assert revision.parent_revision_id == genesis.intent_revision_id
    assert revision.created_at > genesis.created_at
    assert [delta.operation.value for delta in revision.applied_deltas] == ["SET", "PIN"]
    assert revision.created_at.utcoffset().total_seconds() == 0


def test_execution_revision_fixture_minimal_shape(
    load_schema_fixture: Callable[[str], Any],
) -> None:
    revision = ExecutionRevision.model_validate(load_schema_fixture("execution_revision.json"))
    assert revision.target_model == "qwen-image-3.0"
    assert revision.output_size == "1024x1024"
    assert revision.parent_revision_id is None


def test_fixture_pinned_paths_serialize_canonically(
    load_schema_fixture: Callable[[str], Any],
) -> None:
    intent = VisualIntent.model_validate(load_schema_fixture("complete_intent.json"))
    payload = json.loads(intent.model_dump_json())
    # frozenset 按字典序输出，跨进程（PYTHONHASHSEED）逐字节稳定
    assert payload["pinned_paths"] == sorted(intent.pinned_paths)


def test_issue_fixture_shape(load_schema_fixture: Callable[[str], Any]) -> None:
    issue = Issue.model_validate(load_schema_fixture("issue.json"))
    assert issue.code == "validation.unknown_path"
    assert issue.path == "subject.mood"
    assert issue.severity.value == "warning"


def _facet_field_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for facet in ("subject", "composition", "environment", "style", "lighting", "camera", "color"):
        facet_model = VisualIntent.model_fields[facet].annotation
        pairs.extend((facet, field) for field in facet_model.model_fields)
    return pairs
