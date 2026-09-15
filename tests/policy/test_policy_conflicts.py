"""冲突检测测试（policy.v2 / R1-A）：registry 合同保留、四条启发式全部停用。

背景（`REVISION_001_SHORTEST_PATH.md` §R1-A 3/4）：v1 注册的四条冲突规则都建立在
"两个词同时出现就矛盾"的关键词启发式上，而这些组合其实都能成立：

    studio + beach            摄影棚可以搭海滩布景（"摄影棚 + 海滩"是布景组合）；
    室内 + 自然光             室内有窗/天窗/采光顶；
    写实摄影 + 水彩质感        "摄影质感的水彩"是真实可实现的混合风格；
    远景/宽幅构图 + 方形输出   构图词表达景别，不是宽高比要求。

v2 的处理是**停用判定、保留 registry**：四个 rule id 仍在 `CONFLICT_RULES` 与规则表
`conflict_rules` 中（评测/后续版本可稳定引用 `kind` / `path` / `message`），但
`DISABLED_CONFLICT_RULES` 让谓词恒为 False，因此 `assess().conflicts` 恒为空。

本文件的正向断言（v1 反向）：
1. registry 完整且元数据不变；两个前缀判定函数仍与 `kind` 对应；
2. 四类组合**不再**产生任何冲突、不再阻断 ready、字段不被改写；
3. 停用不影响 Resolution 合法性等其余 policy 语义（后者见
   `test_policy_resolution_legality.py`，未放宽）。
"""

from __future__ import annotations

import pytest

from policy_helpers import execution, intent_from, ready_intent, with_value
from visual_intent_agent.domain import Issue
from visual_intent_agent.policy import assess
from visual_intent_agent.policy.decision_policy import (
    CONFLICT_RULES,
    DISABLED_CONFLICT_RULES,
    EXECUTION_CONFLICT_PREFIX,
    HARD_CONFLICT_PREFIX,
    is_execution_conflict,
    is_hard_conflict,
)

ENV_MODE_LOCATION = "policy.hard_conflict.environment_mode_location"
LIGHTING_ENV_SOURCE = "policy.hard_conflict.lighting_environment_source"
STYLE_MEDIUM = "policy.hard_conflict.style_medium_mismatch"
FRAMING_ASPECT = "policy.execution_conflict.framing_aspect_mismatch"

#: R1-A 停用后仍保留在 registry 里的全部 rule id（元数据不变）。
REGISTERED_RULE_IDS = {
    "hard_conflict.environment_mode_location",
    "hard_conflict.lighting_environment_source",
    "hard_conflict.style_medium_mismatch",
    "execution_conflict.framing_aspect_mismatch",
}

#: 曾经会被 v1 判成冲突、R1-A 判定为"可以成立"的组合（值与 execution_context）。
RETIRED_CONFLICT_CASES: list[tuple[dict, str | None]] = [
    ({"environment.mode": "studio", "environment.location": "the beach"}, None),
    ({"environment.mode": "摄影棚环境", "environment.location": "海边沙滩"}, None),
    ({"environment.mode": "indoor", "environment.location": "an outdoor street"}, None),
    ({"environment.mode": "studio", "lighting.character": "natural light"}, None),
    ({"environment.mode": "室内环境", "lighting.character": "自然光照明"}, None),
    ({"style.primary": "photorealistic", "style.description": "anime"}, None),
    ({"style.primary": "写实摄影", "style.description": "画面整体要水彩画的质感"}, None),
    ({"composition.framing": "wide shot"}, "1024x1024"),
    ({"composition.framing": "全景构图"}, "1024x1024"),
    ({"composition.framing": "远景构图"}, "1024x1024"),
]


def _conflict_codes(result) -> list[str]:
    """停用后恒为空；保留 helper 以显式断言"没有任何冲突 code"。"""
    return [issue.code for issue in result.conflicts]


def test_the_registered_rule_codes_never_appear_in_assess_output() -> None:
    """四条旧规则 code 在任何输入下都不再出现（覆盖全部注册 id）。"""
    result = assess(
        intent_from(
            {
                "environment.mode": "studio",
                "environment.location": "the beach",
                "lighting.character": "natural light",
                "style.primary": "photorealistic",
                "style.description": "anime",
                "composition.framing": "wide shot",
            }
        ),
        execution("1024x1024"),
    )
    codes = _conflict_codes(result)
    assert codes == []
    assert ENV_MODE_LOCATION not in codes
    assert LIGHTING_ENV_SOURCE not in codes
    assert STYLE_MEDIUM not in codes
    assert FRAMING_ASPECT not in codes


# --- registry 合同（停用 ≠ 删除） --------------------------------------------


def test_registry_keeps_every_rule_id_with_stable_metadata() -> None:
    assert set(CONFLICT_RULES) == REGISTERED_RULE_IDS
    assert set(DISABLED_CONFLICT_RULES) == REGISTERED_RULE_IDS
    assert CONFLICT_RULES["hard_conflict.environment_mode_location"].kind == "hard"
    assert CONFLICT_RULES["hard_conflict.environment_mode_location"].path == "environment.mode"
    assert CONFLICT_RULES["hard_conflict.lighting_environment_source"].path == "lighting.character"
    assert CONFLICT_RULES["hard_conflict.style_medium_mismatch"].path == "style.primary"
    assert (
        CONFLICT_RULES["execution_conflict.framing_aspect_mismatch"].kind == "execution"
    )
    assert (
        CONFLICT_RULES["execution_conflict.framing_aspect_mismatch"].path
        == "composition.framing"
    )


def test_every_registered_predicate_is_disabled_and_returns_false() -> None:
    """停用谓词必须对"已知会命中"的输入也返回 False（不是靠缺少输入侥幸通过）。"""
    cases = {
        "hard_conflict.environment_mode_location": {
            "environment.mode": "studio",
            "environment.location": "the beach",
        },
        "hard_conflict.lighting_environment_source": {
            "environment.mode": "studio",
            "lighting.character": "natural light",
        },
        "hard_conflict.style_medium_mismatch": {
            "style.primary": "photorealistic",
            "style.description": "anime",
        },
        "execution_conflict.framing_aspect_mismatch": {"composition.framing": "wide shot"},
    }
    for rule_id, values in cases.items():
        rule = CONFLICT_RULES[rule_id]
        assert (
            rule.predicate(intent_from(values), execution("1024x1024")) is False
        ), rule_id


@pytest.mark.parametrize(
    "rule_id,expected_hard,expected_execution",
    [
        ("hard_conflict.environment_mode_location", True, False),
        ("hard_conflict.lighting_environment_source", True, False),
        ("hard_conflict.style_medium_mismatch", True, False),
        ("execution_conflict.framing_aspect_mismatch", False, True),
    ],
)
def test_prefix_helpers_still_follow_the_registry_kind(
    rule_id: str, expected_hard: bool, expected_execution: bool
) -> None:
    kind = CONFLICT_RULES[rule_id].kind
    prefix = HARD_CONFLICT_PREFIX if kind == "hard" else EXECUTION_CONFLICT_PREFIX
    issue = Issue(code=f"{prefix}.{rule_id.split('.', 1)[1]}", message="m", path="p")
    assert is_hard_conflict(issue) is expected_hard
    assert is_execution_conflict(issue) is expected_execution


# --- 停用行为：四类组合都不再冲突 --------------------------------------------


@pytest.mark.parametrize("values,output_size", RETIRED_CONFLICT_CASES)
def test_retired_combinations_report_no_conflict(
    values: dict, output_size: str | None
) -> None:
    context = execution(output_size) if output_size is not None else None
    result = assess(intent_from(values), context)
    assert result.conflicts == [], values


@pytest.mark.parametrize("values,output_size", RETIRED_CONFLICT_CASES)
def test_retired_combinations_do_not_block_or_rewrite_the_values(
    values: dict, output_size: str | None
) -> None:
    context = execution(output_size) if output_size is not None else None
    intent = intent_from(values)
    result = assess(intent, context)
    # 字段逐值保留（停用不是通过抹掉一个值来"消解"冲突）。
    for path, value in values.items():
        facet, field = path.split(".", 1)
        assert getattr(getattr(result.intent, facet), field) == value, path
    # 停用后这些组合只是普通取值：无冲突、不因冲突产生 issue。
    assert result.conflicts == []
    assert result.issues == []


def test_conflicts_are_always_empty_for_the_four_former_rule_shapes() -> None:
    """一次性覆盖四条规则的"组合"形态（英文 + 中文），conflicts 必须为空。"""
    intent = intent_from(
        {
            "environment.mode": "studio",
            "environment.location": "an outdoor street",
            "lighting.character": "natural light",
            "style.primary": "photorealistic",
            "style.description": "anime",
            "composition.framing": "wide shot",
        }
    )
    result = assess(intent, execution("1024x1024"))
    assert result.conflicts == []
    assert result.intent.environment.mode == "studio"
    assert result.intent.lighting.character == "natural light"
    assert result.intent.style.description == "anime"


def test_ready_intent_with_a_former_execution_conflict_stays_ready() -> None:
    intent = with_value(ready_intent(), "composition.framing", "wide shot")
    result = assess(intent, execution("1024x1024"))
    assert result.conflicts == []
    assert result.ready_for_confirmation is True
    assert result.question is None


# --- 确定性 ---------------------------------------------------------------


def test_no_conflict_evaluation_is_still_deterministic() -> None:
    intent = intent_from(
        {"environment.mode": "studio", "environment.location": "the beach"}
    )
    first = assess(intent).model_dump_json()
    second = assess(intent).model_dump_json()
    assert first == second
