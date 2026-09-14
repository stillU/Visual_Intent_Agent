"""冲突检测测试：Hard Conflict 与 Execution Conflict 的显式输出。"""

from __future__ import annotations

import pytest

from policy_helpers import execution, intent_from, ready_intent, with_value
from visual_intent_agent.domain import Issue
from visual_intent_agent.policy import assess
from visual_intent_agent.policy.decision_policy import (
    CONFLICT_RULES,
    EXECUTION_CONFLICT_PREFIX,
    HARD_CONFLICT_PREFIX,
    is_execution_conflict,
    is_hard_conflict,
)

ENV_MODE_LOCATION = "policy.hard_conflict.environment_mode_location"
LIGHTING_ENV_SOURCE = "policy.hard_conflict.lighting_environment_source"
STYLE_MEDIUM = "policy.hard_conflict.style_medium_mismatch"
FRAMING_ASPECT = "policy.execution_conflict.framing_aspect_mismatch"


def _conflict_codes(result) -> list[str]:
    return [issue.code for issue in result.conflicts]


# --- Hard Conflict: environment.mode vs environment.location -----------------


@pytest.mark.parametrize("mode", ["studio", "indoor", "interior"])
@pytest.mark.parametrize("location", ["an outdoor street", "a forest clearing", "the beach"])
def test_enclosed_mode_with_an_open_air_location_conflicts(mode: str, location: str) -> None:
    result = assess(intent_from({"environment.mode": mode, "environment.location": location}))
    assert ENV_MODE_LOCATION in _conflict_codes(result)
    assert is_hard_conflict(result.conflicts[0]) is True


@pytest.mark.parametrize(
    "mode,location",
    [
        ("outdoor", "an outdoor street"),  # 一致
        ("studio", "seamless grey backdrop"),  # 地点不是户外
        ("outdoor", "seamless grey backdrop"),  # 无矛盾
        ("photographic", "an outdoor street"),  # 模式不是封闭
    ],
)
def test_non_conflicting_environment_combinations(mode: str, location: str) -> None:
    result = assess(intent_from({"environment.mode": mode, "environment.location": location}))
    assert ENV_MODE_LOCATION not in _conflict_codes(result)


def test_conflict_is_not_reported_when_one_side_is_missing() -> None:
    assert ENV_MODE_LOCATION not in _conflict_codes(assess(intent_from({"environment.mode": "studio"})))
    assert ENV_MODE_LOCATION not in _conflict_codes(
        assess(intent_from({"environment.location": "the beach"}))
    )


# --- Hard Conflict: lighting.character vs environment.mode -------------------


@pytest.mark.parametrize("lighting", ["natural light", "sunlight", "golden hour"])
def test_natural_light_in_an_enclosed_setting_conflicts(lighting: str) -> None:
    result = assess(
        intent_from({"environment.mode": "studio", "lighting.character": lighting})
    )
    assert LIGHTING_ENV_SOURCE in _conflict_codes(result)


def test_the_lighting_rule_is_evaluated_once_even_if_declared_twice() -> None:
    # 该规则同时声明在 environment.mode 与 lighting.character 上；去重后只报一次。
    result = assess(
        intent_from({"environment.mode": "studio", "lighting.character": "natural light"})
    )
    assert _conflict_codes(result).count(LIGHTING_ENV_SOURCE) == 1


def test_artificial_light_in_a_studio_does_not_conflict() -> None:
    result = assess(
        intent_from({"environment.mode": "studio", "lighting.character": "softbox key light"})
    )
    assert LIGHTING_ENV_SOURCE not in _conflict_codes(result)


# --- Hard Conflict: style.primary vs style.description ----------------------


def test_photographic_primary_with_a_non_photographic_medium_conflicts() -> None:
    result = assess(
        intent_from({"style.primary": "photorealistic", "style.description": "anime"})
    )
    assert STYLE_MEDIUM in _conflict_codes(result)


@pytest.mark.parametrize(
    "primary,description",
    [
        ("photorealistic", "soft film grain"),  # 描述不是非摄影媒介
        ("cinematic realism", "anime"),  # 主值不是摄影
        ("oil painting", "anime"),  # 主值不是摄影
    ],
)
def test_non_conflicting_style_combinations(primary: str, description: str) -> None:
    result = assess(intent_from({"style.primary": primary, "style.description": description}))
    assert STYLE_MEDIUM not in _conflict_codes(result)


# --- Execution Conflict: framing vs output aspect ratio ----------------------


@pytest.mark.parametrize("framing", ["wide shot", "panorama", "landscape", "establishing shot"])
def test_wide_framing_with_a_non_wide_output_conflicts(framing: str) -> None:
    result = assess(
        intent_from({"composition.framing": framing}), execution("1024x1024")
    )
    assert FRAMING_ASPECT in _conflict_codes(result)
    assert is_execution_conflict(result.conflicts[0]) is True


@pytest.mark.parametrize(
    "framing,output_size",
    [
        ("wide shot", "1536x1024"),  # 1.5 恰好满足
        ("wide shot", "1920x1080"),  # 16:9 满足
        ("medium shot", "1024x1024"),  # 非宽幅构图
        ("close-up", "1024x1536"),  # 非宽幅构图
    ],
)
def test_non_conflicting_framing_and_output(framing: str, output_size: str) -> None:
    result = assess(intent_from({"composition.framing": framing}), execution(output_size))
    assert FRAMING_ASPECT not in _conflict_codes(result)


def test_execution_conflict_requires_an_execution_context() -> None:
    result = assess(intent_from({"composition.framing": "wide shot"}))
    assert FRAMING_ASPECT not in _conflict_codes(result)


def test_unparseable_output_size_is_left_to_step_08_not_treated_as_a_conflict() -> None:
    result = assess(intent_from({"composition.framing": "wide shot"}), execution("wide"))
    assert FRAMING_ASPECT not in _conflict_codes(result)


# --- 冲突语义 ---------------------------------------------------------------


def test_execution_conflict_does_not_block_confirmation() -> None:
    intent = with_value(ready_intent(), "composition.framing", "wide shot")
    result = assess(intent, execution("1024x1024"))
    assert FRAMING_ASPECT in _conflict_codes(result)
    assert result.ready_for_confirmation is True
    assert result.question is None


def test_hard_conflict_blocks_confirmation_even_when_every_block_is_resolved() -> None:
    intent = with_value(ready_intent(), "environment.location", "an outdoor street")
    result = assess(intent)
    assert result.ready_for_confirmation is False
    assert ENV_MODE_LOCATION in _conflict_codes(result)
    assert result.question is not None
    assert result.question.target_path == "environment.mode"


def test_conflicts_never_overwrite_the_fields_that_caused_them() -> None:
    intent = intent_from(
        {"environment.mode": "studio", "environment.location": "an outdoor street"}
    )
    result = assess(intent)
    assert result.intent is intent
    assert result.intent.environment.mode == "studio"
    assert result.intent.environment.location == "an outdoor street"
    # 冲突的两个字段都保持"已解决"（有值），不通过抹掉其中一个来消解冲突。
    unresolved = [decision.path for decision in result.unresolved_decisions]
    assert "environment.mode" not in unresolved
    assert "environment.location" not in unresolved


def test_conflict_issues_carry_the_declaring_path_and_are_errors() -> None:
    result = assess(
        intent_from({"environment.mode": "studio", "environment.location": "the beach"})
    )
    issue = next(item for item in result.conflicts if item.code == ENV_MODE_LOCATION)
    assert issue.path == "environment.mode"
    assert issue.severity.value == "error"


def test_all_registered_rules_have_a_stable_code_prefix() -> None:
    for rule_id, rule in CONFLICT_RULES.items():
        prefix = HARD_CONFLICT_PREFIX if rule.kind == "hard" else EXECUTION_CONFLICT_PREFIX
        code = f"{prefix}.{rule_id.split('.', 1)[1]}"
        issue = Issue(code=code, message="m", path=rule.path)
        assert is_hard_conflict(issue) is (rule.kind == "hard")
        assert is_execution_conflict(issue) is (rule.kind == "execution")


def test_multiple_hard_conflicts_are_all_reported() -> None:
    intent = intent_from(
        {
            "environment.mode": "studio",
            "environment.location": "an outdoor street",
            "lighting.character": "natural light",
            "style.primary": "photorealistic",
            "style.description": "anime",
        }
    )
    codes = _conflict_codes(assess(intent))
    assert set(codes) == {ENV_MODE_LOCATION, LIGHTING_ENV_SOURCE, STYLE_MEDIUM}


def test_conflict_order_is_stable_and_follows_the_rule_table() -> None:
    intent = intent_from(
        {
            "environment.mode": "studio",
            "environment.location": "an outdoor street",
            "lighting.character": "natural light",
            "style.primary": "photorealistic",
            "style.description": "anime",
        }
    )
    first = _conflict_codes(assess(intent))
    second = _conflict_codes(assess(intent))
    assert first == second
    # 顺序由规则表决定：style.primary 声明在 environment.mode 之前。
    assert first == [STYLE_MEDIUM, ENV_MODE_LOCATION, LIGHTING_ENV_SOURCE]
