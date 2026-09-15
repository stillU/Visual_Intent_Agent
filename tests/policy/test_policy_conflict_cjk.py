"""回归测试（MVP v0.3 · Step 06 P2 词表 + 更改书 001 R1-A 停用）：中文命中与停用。

本文件曾把四条**不成立**的关键词冲突固化成正例（摄影棚环境+海边沙滩、室内环境+
自然光照明、写实摄影+水彩质感、远景构图+方形输出）。R1-A 判定这些组合都能成立
（摄影棚可搭海滩布景、室内可有自然光、摄影与水彩质感可混合、构图词不是宽高比要求），
因此：

- 四条规则在 v2 中**停用**（registry 保留、判定恒 False，见 `decision_policy.py`）；
- 本文件改为断言"停用后不再冲突、取值原样保留"，并**保留** `_words` /
  `_contains_phrase` 的 CJK 匹配能力回归（这是 R1-A 要求继续复用的既有支持）。

英文对照、中文分词边界与确定性断言继续保留；安全不变量测试不在本文件。
"""

from __future__ import annotations

import pytest

from policy_helpers import execution, intent_from
from visual_intent_agent.policy import assess
from visual_intent_agent.policy.decision_policy import (
    CONFLICT_RULES,
    DISABLED_CONFLICT_RULES,
    _contains_any,
    _contains_phrase,
    _NON_PHOTO_STYLE_TERMS,
    _words,
)

ENV_MODE_LOCATION = "policy.hard_conflict.environment_mode_location"
LIGHTING_ENV_SOURCE = "policy.hard_conflict.lighting_environment_source"
STYLE_MEDIUM = "policy.hard_conflict.style_medium_mismatch"
FRAMING_ASPECT = "policy.execution_conflict.framing_aspect_mismatch"


def _conflict_codes(result) -> list[str]:
    return [issue.code for issue in result.conflicts]


# --- R1-A 停用：中文组合不再冲突 ---------------------------------------------


@pytest.mark.parametrize(
    "mode,location",
    [
        ("摄影棚环境", "海边沙滩"),
        ("摄影棚环境", "公园草坪"),
        ("室内环境", "海边沙滩"),
        ("室内", "户外"),
        ("摄影棚环境", "无缝灰幕"),
        ("自然光环境", "海边沙滩"),
    ],
)
def test_cjk_environment_combinations_are_preserved_without_conflict(
    mode: str, location: str
) -> None:
    """摄影棚 + 海滩是布景组合而非矛盾：不报冲突、不改写、不阻断。"""
    intent = intent_from({"environment.mode": mode, "environment.location": location})
    result = assess(intent)
    assert _conflict_codes(result) == []
    assert result.intent.environment.mode == mode
    assert result.intent.environment.location == location


@pytest.mark.parametrize(
    "mode,lighting",
    [
        ("室内环境", "自然光照明"),
        ("摄影棚环境", "自然光"),
        ("室内", "日光"),
    ],
)
def test_cjk_natural_light_in_an_enclosed_setting_no_longer_conflicts(
    mode: str, lighting: str
) -> None:
    """室内有窗/天窗，自然光成立：保留用户的两种表达，不再阻断。"""
    result = assess(
        intent_from({"environment.mode": mode, "lighting.character": lighting})
    )
    assert _conflict_codes(result) == []
    assert result.intent.lighting.character == lighting


@pytest.mark.parametrize(
    "primary,description",
    [
        ("写实摄影", "画面整体要水彩画的质感"),
        ("写实摄影风格", "水彩画"),
        ("照片写实", "动画风格"),
        ("写实摄影", "柔和胶片颗粒"),
        ("油画风格", "水彩画"),
        ("插画风格", "水彩画"),
    ],
)
def test_cjk_style_combinations_are_preserved_without_conflict(
    primary: str, description: str
) -> None:
    """摄影与水彩质感可以混合：两种风格表达都保留。"""
    result = assess(
        intent_from({"style.primary": primary, "style.description": description})
    )
    assert _conflict_codes(result) == []
    assert result.intent.style.primary == primary
    assert result.intent.style.description == description


@pytest.mark.parametrize("framing", ["远景构图", "全景", "wide_shot", "中景", "特写"])
def test_cjk_and_token_framing_never_conflicts_with_a_square_output(framing: str) -> None:
    """构图词表达景别，不是宽高比要求：方形输出下也不报执行冲突。"""
    result = assess(intent_from({"composition.framing": framing}), execution("1024x1024"))
    assert FRAMING_ASPECT not in _conflict_codes(result)
    assert _conflict_codes(result) == []


def test_english_behaviour_is_unchanged_by_the_cjk_vocabulary() -> None:
    """英文对照：停用对中英文一视同仁（原先命中的英文组合同样不再冲突）。"""
    result = assess(
        intent_from({"environment.mode": "studio", "environment.location": "the beach"})
    )
    assert ENV_MODE_LOCATION not in _conflict_codes(result)
    assert result.intent.environment.location == "the beach"


def test_disabled_registry_still_lists_all_four_rule_ids() -> None:
    """停用 ≠ 删除：rule id 仍在 registry（评测/后续版本可稳定引用）。"""
    assert set(CONFLICT_RULES) == {
        "hard_conflict.environment_mode_location",
        "hard_conflict.lighting_environment_source",
        "hard_conflict.style_medium_mismatch",
        "execution_conflict.framing_aspect_mismatch",
    }
    assert DISABLED_CONFLICT_RULES == frozenset(CONFLICT_RULES)


# --- 中文分词边界（保留：CJK 匹配能力仍被复用）--------------------------------


def test_cjk_terms_match_inside_a_contiguous_cjk_run() -> None:
    """中文没有词间空格：`_words` 会把整段中文当一个 token。

    因此中文词表必须对**非空格分隔文字**做子串匹配（"画面整体要水彩画的质感" ⊇
    "水彩画"）。这是既有能力的回归；词表本身随规则停用而不再影响 `assess`。
    英文短语匹配语义保持不变（"photo" 不得命中 "photograph"）。
    """
    assert _words("画面整体要水彩画的质感") == ["画面整体要水彩画的质感"]
    assert _contains_any("画面整体要水彩画的质感", _NON_PHOTO_STYLE_TERMS) is True
    # 英文词序列语义不变：短语仍按完整 token 序列匹配。
    assert _contains_phrase(_words("photograph"), "photo") is False
