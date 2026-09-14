"""规则表测试：九项 Decision、路径对应、required_if 封闭枚举、冲突规则接线。"""

from __future__ import annotations

import pytest

from policy_helpers import empty_intent, intent_from, ready_intent
from visual_intent_agent.domain import INTENT_PATHS, VisualIntent
from visual_intent_agent.policy import DECISION_POLICIES, POLICY_VERSION, Materiality
from visual_intent_agent.policy.decision_policy import (
    CONFLICT_RULES,
    REQUIRED_IF_ALWAYS,
    REQUIRED_IF_CAMERA_SPECIFIED,
    REQUIRED_IF_CONDITIONS,
    REQUIRED_IF_ENVIRONMENT_SPECIFIED,
    REQUIRED_IF_NEVER,
    REQUIRED_IF_SUBJECT_SPECIFIED,
    required_if_holds,
)

#: 任务书「第一版 Decision 清单」与白名单路径的冻结对应。
EXPECTED_DECISIONS: dict[str, tuple[str, str, bool, tuple[str, ...]]] = {
    # path -> (materiality, required_if, delegatable, dependencies)
    "subject.description": ("core", "always", False, ()),
    "style.primary": ("core", "always", True, ()),
    "environment.mode": ("core", "always", True, ("environment.location",)),
    "composition.framing": ("perceptual", "when_subject_specified", True, ()),
    "subject.pose_action": ("perceptual", "when_subject_specified", True, ()),
    "lighting.character": ("perceptual", "when_environment_specified", True, ()),
    "camera.angle": ("perceptual", "when_camera_specified", True, ()),
    "color.palette": ("perceptual", "never", True, ()),
    "camera.depth_of_field": ("perceptual", "when_camera_specified", True, ()),
}


def test_policy_version_is_frozen_policy_v1() -> None:
    assert POLICY_VERSION == "policy.v1"


def test_decision_policies_contain_exactly_nine_entries() -> None:
    assert len(DECISION_POLICIES) == 9


def test_nine_decisions_map_to_the_frozen_paths() -> None:
    assert {policy.path for policy in DECISION_POLICIES} == set(EXPECTED_DECISIONS)


def test_every_decision_has_an_explicit_rule_row() -> None:
    by_path = {policy.path: policy for policy in DECISION_POLICIES}
    assert set(by_path) == set(EXPECTED_DECISIONS)
    for path, (materiality, required_if, delegatable, dependencies) in EXPECTED_DECISIONS.items():
        policy = by_path[path]
        assert policy.materiality.value == materiality, path
        assert policy.required_if == required_if, path
        assert policy.delegatable is delegatable, path
        assert tuple(policy.dependencies) == dependencies, path
        assert policy.invalidates_realization is True, path


def test_no_decision_extends_the_frozen_schema() -> None:
    # 只允许 12 条白名单路径与九个 Decision；不新增任何视觉维度。
    covered = {
        member for policy in DECISION_POLICIES for member in (policy.path, *policy.dependencies)
    }
    assert covered <= INTENT_PATHS
    assert covered == set(EXPECTED_DECISIONS) | {"environment.location"}
    # 未列为独立 Decision 的路径仍属于白名单，永不成为新的决策维度。
    assert set(EXPECTED_DECISIONS) | {"environment.location", "subject.count", "style.description"} == INTENT_PATHS


def test_required_if_values_form_a_closed_enumeration() -> None:
    assert REQUIRED_IF_CONDITIONS == {
        "always",
        "never",
        "when_subject_specified",
        "when_environment_specified",
        "when_camera_specified",
    }
    for policy in DECISION_POLICIES:
        assert policy.required_if in REQUIRED_IF_CONDITIONS, policy.path


def test_only_core_decisions_are_unconditionally_required() -> None:
    always = {policy.path for policy in DECISION_POLICIES if policy.required_if == REQUIRED_IF_ALWAYS}
    assert always == {"subject.description", "style.primary", "environment.mode"}
    for policy in DECISION_POLICIES:
        if policy.materiality is Materiality.CORE:
            assert policy.required_if == REQUIRED_IF_ALWAYS, policy.path


def test_perceptual_decisions_are_not_unconditionally_required() -> None:
    for policy in DECISION_POLICIES:
        if policy.materiality is Materiality.PERCEPTUAL:
            assert policy.required_if != REQUIRED_IF_ALWAYS, policy.path


def test_no_policy_produces_runtime_action() -> None:
    # 实现参数（runtime）不在 12 条 Intent 路径上；九个 Decision 全是视觉决策。
    assert {policy.materiality for policy in DECISION_POLICIES} == {
        Materiality.CORE,
        Materiality.PERCEPTUAL,
    }


@pytest.mark.parametrize(
    "condition,expected",
    [
        (REQUIRED_IF_ALWAYS, True),
        (REQUIRED_IF_NEVER, False),
    ],
)
def test_constant_conditions(condition: str, expected: bool) -> None:
    assert required_if_holds(condition, empty_intent()) is expected
    assert required_if_holds(condition, intent_from({"subject.description": "x"})) is expected


def test_subject_condition_tracks_subject_description_only() -> None:
    assert required_if_holds(REQUIRED_IF_SUBJECT_SPECIFIED, empty_intent()) is False
    assert (
        required_if_holds(
            REQUIRED_IF_SUBJECT_SPECIFIED, intent_from({"subject.pose_action": "standing"})
        )
        is False
    )
    assert (
        required_if_holds(
            REQUIRED_IF_SUBJECT_SPECIFIED, intent_from({"subject.description": "a cat"})
        )
        is True
    )


def test_environment_condition_tracks_either_environment_path() -> None:
    assert required_if_holds(REQUIRED_IF_ENVIRONMENT_SPECIFIED, empty_intent()) is False
    assert (
        required_if_holds(
            REQUIRED_IF_ENVIRONMENT_SPECIFIED, intent_from({"environment.mode": "studio"})
        )
        is True
    )
    assert (
        required_if_holds(
            REQUIRED_IF_ENVIRONMENT_SPECIFIED,
            intent_from({"environment.location": "a beach"}),
        )
        is True
    )


def test_camera_condition_tracks_either_camera_path() -> None:
    assert required_if_holds(REQUIRED_IF_CAMERA_SPECIFIED, empty_intent()) is False
    assert (
        required_if_holds(
            REQUIRED_IF_CAMERA_SPECIFIED, intent_from({"camera.angle": "low angle"})
        )
        is True
    )
    assert (
        required_if_holds(
            REQUIRED_IF_CAMERA_SPECIFIED, intent_from({"camera.depth_of_field": "shallow"})
        )
        is True
    )


def test_unknown_condition_is_a_program_error() -> None:
    with pytest.raises(ValueError):
        required_if_holds("when_the_user_looks_happy", empty_intent())


def test_conflict_rule_registry_is_wired_to_policies() -> None:
    declared = [rule for policy in DECISION_POLICIES for rule in policy.conflict_rules]
    assert set(declared) == set(CONFLICT_RULES)
    duplicates = {rule for rule in declared if declared.count(rule) > 1}
    assert duplicates == {"hard_conflict.lighting_environment_source"}
    for rule_id, rule in CONFLICT_RULES.items():
        assert rule.rule_id == rule_id
        assert rule.kind in {"hard", "execution"}
        assert rule.path in INTENT_PATHS


def test_conflict_rule_kinds_cover_hard_and_execution() -> None:
    kinds = {rule.kind for rule in CONFLICT_RULES.values()}
    assert kinds == {"hard", "execution"}


def test_rules_are_pure_data_and_do_not_read_environment_or_clock() -> None:
    # 规则表是静态元组；求值只依赖 Intent（+ 可选 ExecutionRevision）。
    assert isinstance(DECISION_POLICIES, tuple)
    assert all(not callable(policy.required_if) for policy in DECISION_POLICIES)
    assert VisualIntent() == empty_intent()
    # 已解决的规则表在 ready 输入上不产生任何缺失/冲突。
    from visual_intent_agent.policy import assess

    resolution = assess(ready_intent())
    assert resolution.ready_for_confirmation is True
