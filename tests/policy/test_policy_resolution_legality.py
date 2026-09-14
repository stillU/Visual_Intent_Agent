"""Resolution 合法性测试：delegated / not_applicable / 值型 Resolution 的规则验证。"""

from __future__ import annotations

import pytest

from policy_helpers import (
    ALL_PATHS,
    empty_intent,
    full_intent,
    intent_from,
    ready_intent,
    with_resolution,
    with_value,
)
from visual_intent_agent.policy import DECISION_POLICIES, assess
from visual_intent_agent.policy.decision_policy import (
    DELEGATION_NOT_ALLOWED,
    NOT_APPLICABLE_NOT_ALLOWED,
    RESOLUTION_REQUIRES_VALUE,
)


def _decision_members(path: str) -> tuple[str, ...]:
    """覆盖 `path` 的 Decision 的全部成员路径（未覆盖则为空）。"""
    for policy in DECISION_POLICIES:
        if path in (policy.path, *policy.dependencies):
            return (policy.path, *policy.dependencies)
    return ()

#: 规则表中 delegatable=True 的全部路径（含 Environment 的成员路径）。
DELEGATABLE_PATHS = (
    "style.primary",
    "environment.mode",
    "environment.location",
    "composition.framing",
    "subject.pose_action",
    "lighting.character",
    "camera.angle",
    "color.palette",
    "camera.depth_of_field",
)

#: 没有规则条目的补充路径（永不阻塞，但也不具备委托/不适用授权）。
SUPPLEMENTARY_PATHS = ("subject.count", "style.description")

#: 规则表中 required_if=always 的路径（not_applicable 永远不成立）。
ALWAYS_REQUIRED_PATHS = ("subject.description", "style.primary", "environment.mode")

#: 被某个 Decision 覆盖的路径（可能是 primary，也可能是 dependency）。
COVERED_PATHS = frozenset(DELEGATABLE_PATHS) | {"subject.description"}


def _issue_codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def _unresolved_paths(result) -> list[str]:
    return [decision.path for decision in result.unresolved_decisions]


@pytest.mark.parametrize("path", DELEGATABLE_PATHS)
def test_user_delegated_on_a_delegatable_path_is_resolved(path: str) -> None:
    result = assess(with_resolution(empty_intent(), path, "user_delegated"))
    assert DELEGATION_NOT_ALLOWED not in _issue_codes(result)
    assert path not in _unresolved_paths(result)


def test_user_delegated_on_the_primary_subject_is_rejected() -> None:
    result = assess(with_resolution(empty_intent(), "subject.description", "user_delegated"))
    assert _issue_codes(result) == [DELEGATION_NOT_ALLOWED]
    issue = result.issues[0]
    assert issue.path == "subject.description"
    assert issue.severity.value == "error"
    # 仍然缺失：delegated 不能把不可委托的核心决策变成已解决。
    assert "subject.description" in _unresolved_paths(result)
    assert result.ready_for_confirmation is False


@pytest.mark.parametrize("path", SUPPLEMENTARY_PATHS)
def test_user_delegated_on_a_path_without_rule_is_rejected(path: str) -> None:
    # 补充路径没有 delegatable=True 的规则条目 => 不具备委托授权（显式 issue）。
    result = assess(with_resolution(empty_intent(), path, "user_delegated"))
    assert DELEGATION_NOT_ALLOWED in _issue_codes(result)
    assert any(issue.path == path for issue in result.issues)


@pytest.mark.parametrize("path", ALWAYS_REQUIRED_PATHS)
def test_not_applicable_on_an_always_required_path_is_rejected(path: str) -> None:
    result = assess(with_resolution(empty_intent(), path, "not_applicable"))
    assert NOT_APPLICABLE_NOT_ALLOWED in _issue_codes(result)
    assert any(issue.path == path for issue in result.issues)
    assert path in _unresolved_paths(result)


def test_not_applicable_is_accepted_when_the_rule_condition_does_not_hold() -> None:
    # color.palette 的 required_if=never => 不适用规则条件成立 => 已解决。
    result = assess(with_resolution(empty_intent(), "color.palette", "not_applicable"))
    assert NOT_APPLICABLE_NOT_ALLOWED not in _issue_codes(result)
    assert "color.palette" not in _unresolved_paths(result)


def test_not_applicable_on_a_contextual_path_flips_with_the_condition() -> None:
    # camera.angle 的 required_if=when_camera_specified。
    optional = assess(with_resolution(empty_intent(), "camera.angle", "not_applicable"))
    assert NOT_APPLICABLE_NOT_ALLOWED not in _issue_codes(optional)

    engaged = with_value(empty_intent(), "camera.depth_of_field", "shallow")
    required = assess(with_resolution(engaged, "camera.angle", "not_applicable"))
    assert NOT_APPLICABLE_NOT_ALLOWED in _issue_codes(required)


@pytest.mark.parametrize("path", SUPPLEMENTARY_PATHS)
def test_not_applicable_on_a_path_without_rule_is_rejected(path: str) -> None:
    result = assess(with_resolution(empty_intent(), path, "not_applicable"))
    assert NOT_APPLICABLE_NOT_ALLOWED in _issue_codes(result)


@pytest.mark.parametrize("resolution", ["user_specified", "user_confirmed_proposal"])
@pytest.mark.parametrize("path", ALL_PATHS)
def test_value_carrying_resolution_without_a_value_is_rejected(
    path: str, resolution: str
) -> None:
    result = assess(with_resolution(empty_intent(), path, resolution))
    assert RESOLUTION_REQUIRES_VALUE in _issue_codes(result)
    assert any(issue.path == path for issue in result.issues)
    if path in COVERED_PATHS:
        # Decision 只报第一条未解决的成员路径，因此断言该 Decision 至少有一条。
        assert set(_decision_members(path)) & set(_unresolved_paths(result))


@pytest.mark.parametrize("resolution", ["user_specified", "user_confirmed_proposal"])
@pytest.mark.parametrize("path", ALL_PATHS)
def test_value_carrying_resolution_with_a_value_is_accepted(
    path: str, resolution: str
) -> None:
    intent = with_value(empty_intent(), path, 1 if path == "subject.count" else "concrete")
    result = assess(with_resolution(intent, path, resolution))
    assert RESOLUTION_REQUIRES_VALUE not in _issue_codes(result)
    assert path not in _unresolved_paths(result)


def test_every_resolution_record_is_rule_checked() -> None:
    # 12 条路径全部带 user_delegated：subject.description 与两条补充路径报错，
    # 其余 9 条视为已解决 —— 没有任何记录被静默跳过。
    intent = empty_intent()
    for path in ALL_PATHS:
        intent = with_resolution(intent, path, "user_delegated")
    result = assess(intent)
    checked = {issue.path for issue in result.issues if issue.code == DELEGATION_NOT_ALLOWED}
    assert checked == {"subject.description", "subject.count", "style.description"}


def test_user_delegated_does_not_create_a_value() -> None:
    intent = with_resolution(empty_intent(), "style.primary", "user_delegated")
    result = assess(intent)
    assert intent.style.primary is None
    assert result.intent.style.primary is None


def test_illegal_delegation_on_a_specified_path_is_flagged_but_not_reopened() -> None:
    # subject.description 已有具体值：非法委托仍报 issue，但不会把一个已指定的
    # 核心决策重新变成"缺失"（避免无意义地重复提问）。
    intent = with_value(empty_intent(), "subject.description", "a lone astronaut")
    intent = with_resolution(intent, "subject.description", "user_delegated")
    result = assess(intent)
    assert DELEGATION_NOT_ALLOWED in _issue_codes(result)
    assert "subject.description" not in _unresolved_paths(result)


def test_not_applicable_does_not_create_a_value() -> None:
    intent = with_resolution(empty_intent(), "color.palette", "not_applicable")
    result = assess(intent)
    assert result.intent.color.palette is None
    assert "color.palette" not in _unresolved_paths(result)


def test_delegation_of_the_environment_member_is_covered_by_the_decision() -> None:
    intent = with_value(empty_intent(), "environment.mode", "studio")
    intent = with_resolution(intent, "environment.location", "user_delegated")
    result = assess(intent)
    assert DELEGATION_NOT_ALLOWED not in _issue_codes(result)
    assert "environment.location" not in _unresolved_paths(result)


def test_confirmed_proposal_and_specified_values_never_block_ready_inputs() -> None:
    result = assess(full_intent())
    assert result.issues == []
    assert result.ready_for_confirmation is True


def test_ready_intent_with_a_valid_delegation_stays_ready() -> None:
    intent = with_resolution(ready_intent(), "camera.angle", "user_delegated")
    result = assess(intent)
    assert result.issues == []
    assert "camera.angle" not in _unresolved_paths(result)
    assert result.ready_for_confirmation is True


def test_intent_from_values_round_trips_to_the_same_resolution_input() -> None:
    # 规则判断只读 Intent 的公开字段，不做任何隐式归一化。
    intent = intent_from({"style.primary": "watercolor"})
    result = assess(intent)
    assert result.intent == intent
