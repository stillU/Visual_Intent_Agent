"""路径白名单与系统字段名（ARCHITECTURE.md 5.4）。

测试不维护第二份路径清单：以 `VisualIntent` 的七类 Facet 字段定义为唯一期望来源，
再单独钉住每类 Facet 的字段数量与总数 12。
"""

from __future__ import annotations

from visual_intent_agent.domain import INTENT_PATHS, SYSTEM_FIELD_NAMES, VisualIntent

FACET_FIELD_NAMES = (
    "subject",
    "composition",
    "environment",
    "style",
    "lighting",
    "camera",
    "color",
)

EXPECTED_FIELDS_PER_FACET = {
    "subject": 3,
    "composition": 1,
    "environment": 2,
    "style": 2,
    "lighting": 1,
    "camera": 2,
    "color": 1,
}


def _declared_facet_paths() -> set[str]:
    paths: set[str] = set()
    for facet_name in FACET_FIELD_NAMES:
        facet_model = VisualIntent.model_fields[facet_name].annotation
        paths.update(f"{facet_name}.{field}" for field in facet_model.model_fields)
    return paths


def test_intent_paths_is_a_frozenset_of_twelve_paths() -> None:
    assert isinstance(INTENT_PATHS, frozenset)
    assert len(INTENT_PATHS) == 12
    assert all(isinstance(path, str) and path.count(".") == 1 for path in INTENT_PATHS)


def test_intent_paths_match_declared_facet_fields_exactly() -> None:
    assert set(INTENT_PATHS) == _declared_facet_paths()


def test_intent_paths_are_grouped_into_the_seven_frozen_facets() -> None:
    counts: dict[str, int] = {}
    for path in INTENT_PATHS:
        facet, _, _ = path.partition(".")
        counts[facet] = counts.get(facet, 0) + 1
    assert counts == EXPECTED_FIELDS_PER_FACET
    assert set(counts) == set(FACET_FIELD_NAMES)


def test_intent_paths_cannot_be_mutated() -> None:
    assert not hasattr(INTENT_PATHS, "add")
    assert not hasattr(INTENT_PATHS, "remove")


def test_system_field_names_are_disjoint_from_intent_paths() -> None:
    assert isinstance(SYSTEM_FIELD_NAMES, frozenset)
    assert SYSTEM_FIELD_NAMES
    assert not (SYSTEM_FIELD_NAMES & INTENT_PATHS)
    assert all(name == name.lower() for name in SYSTEM_FIELD_NAMES)
    assert "schema_version" in SYSTEM_FIELD_NAMES
