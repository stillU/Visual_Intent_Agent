"""R1-B：r1 L3 指标口径的离线单元测试（无 Provider、无网络、无产品状态）。

覆盖更改书 001 §R1-B 第 2/3/8 条：

- Preservation 只比较“仍被要求保留”的路径关联属性，跨澄清轮累计合法修改；
  正常澄清/拒绝无 Prompt → `not_applicable`，不再按“保留 0”误罚；
  应生成而失败 → 单独计数并由 `generation_completion` 报完成率。
- 关键词违例（`forbidden_keyword_hit`）与来源防护（`source_guard_*`）分开报告。
"""

from __future__ import annotations

import pytest

from evaluation.metrics.prompt import (
    PromptSequenceTurn,
    evaluate_generation_completion,
    evaluate_preservation_r1,
    evaluate_unauthorized_addition_r1,
)


def test_clarification_turn_without_prompt_is_not_a_preservation_failure() -> None:
    """Q1：正常澄清轮不产生 Prompt，不得按“保留 0”计入 Preservation 分母。"""
    sequence = [
        PromptSequenceTurn(
            turn_id="t1",
            prompt_text="一只橘猫 客厅 中景 soft warm",
            expected_prompt=True,
        ),
        PromptSequenceTurn(
            turn_id="t2",
            prompt_text=None,
            expected_prompt=False,
            preserve_path_groups={"composition.framing": [["中景", "medium shot"]]},
        ),
        PromptSequenceTurn(
            turn_id="t3",
            prompt_text="一只橘猫 客厅 中景 soft warm dramatic",
            expected_prompt=True,
            preserve_path_groups={"composition.framing": [["中景", "medium shot"]]},
        ),
    ]
    payload = evaluate_preservation_r1(sequence)
    assert payload.status == "ok"
    assert payload.score == 1.0
    assert payload.counts["no_prompt_not_applicable"] == 1
    assert payload.counts["failed_generation"] == 0
    assert payload.counts["groups_lost"] == 0


def test_no_prompt_clarification_counted_even_without_preserve_labels() -> None:
    """正常澄清/拒绝无 Prompt 一律计数，不受是否携带保留标签影响（R1-B #3）。"""
    with_labels = [
        PromptSequenceTurn(turn_id="t1", prompt_text="一只橘猫 中景", expected_prompt=True),
        PromptSequenceTurn(turn_id="t2", prompt_text=None, expected_prompt=False),
        PromptSequenceTurn(
            turn_id="t3",
            prompt_text="一只橘猫 中景 soft",
            expected_prompt=True,
            preserve_path_groups={"composition.framing": [["中景", "medium shot"]]},
        ),
    ]
    payload = evaluate_preservation_r1(with_labels)
    assert payload.status == "ok"
    assert payload.counts["no_prompt_not_applicable"] == 1
    assert payload.counts["pairs_scored"] == 1

    # 全程无标签也没关系：无 Prompt 轮仍进入 not_applicable 计数，不误记为保留 0。
    no_labels = [
        PromptSequenceTurn(turn_id="t1", prompt_text="一只橘猫 中景", expected_prompt=True),
        PromptSequenceTurn(turn_id="t2", prompt_text=None, expected_prompt=False),
    ]
    payload2 = evaluate_preservation_r1(no_labels)
    assert payload2.status == "not_applicable"
    assert payload2.counts["no_prompt_not_applicable"] == 1
    assert payload2.counts["failed_generation"] == 0


def test_changed_paths_accumulate_across_no_prompt_and_are_excluded_then_cleared() -> None:
    """跨无 Prompt 轮累计合法 SET/CLEAR，在下一有效 pair 排除对应路径后清空。"""
    sequence = [
        PromptSequenceTurn(turn_id="t1", prompt_text="写实 中景", expected_prompt=True),
        PromptSequenceTurn(
            turn_id="t2",
            prompt_text=None,
            expected_prompt=False,
            changed_paths=["style.primary"],
        ),
        PromptSequenceTurn(
            turn_id="t3",
            prompt_text="水彩 中景",
            expected_prompt=True,
            preserve_path_groups={
                "style.primary": [["水彩", "watercolor"]],
                "composition.framing": [["中景", "medium shot"]],
            },
        ),
        PromptSequenceTurn(
            turn_id="t4",
            prompt_text="水彩 中景 暖光",
            expected_prompt=True,
            preserve_path_groups={
                "style.primary": [["水彩", "watercolor"]],
                "composition.framing": [["中景", "medium shot"]],
            },
        ),
    ]
    payload = evaluate_preservation_r1(sequence)
    assert payload.status == "ok"
    assert payload.score == 1.0
    assert payload.counts["groups_lost"] == 0
    # t2 的合法改动在 t3 对中显式排除；清空后 t4 对不再排除。
    assert payload.details["pair_details"][0]["excluded_changed_paths"] == ["style.primary"]
    assert payload.details["pair_details"][1]["excluded_changed_paths"] == []


def test_missing_prompt_when_generation_expected_is_failed_not_zero() -> None:
    """应生成而失败单独记失败，不伪造 0 分；完成率指标保留在分母。"""
    sequence = [
        PromptSequenceTurn(turn_id="t1", prompt_text="一只橘猫 中景", expected_prompt=True),
        PromptSequenceTurn(
            turn_id="t2",
            prompt_text=None,
            expected_prompt=True,
            preserve_path_groups={"composition.framing": [["中景", "medium shot"]]},
        ),
    ]
    preservation = evaluate_preservation_r1(sequence)
    assert preservation.status == "not_applicable"
    assert preservation.counts["failed_generation"] == 1
    assert preservation.score is None

    completion = evaluate_generation_completion(sequence)
    assert completion.status == "ok"
    assert completion.score == 0.5
    assert completion.counts == {"expected_prompt_turns": 2, "produced": 1, "failed": 1}
    assert completion.details["failed_turn_ids"] == ["t2"]


def test_legally_changed_path_is_not_counted_as_loss_across_clarifications() -> None:
    """R1-B #2：用户明确改掉的旧值不算丢失；后续轮按最新值检查保留。"""
    sequence = [
        PromptSequenceTurn(turn_id="t1", prompt_text="写实摄影 中景", expected_prompt=True),
        # t2 合法修改 style（不再要求保留“写实”），只保留构图。
        PromptSequenceTurn(
            turn_id="t2",
            prompt_text="水彩插画 中景",
            expected_prompt=True,
            preserve_path_groups={"composition.framing": [["中景", "medium shot"]]},
        ),
        # t3 继续保留最新值（水彩 + 中景）。
        PromptSequenceTurn(
            turn_id="t3",
            prompt_text="水彩插画 中景 暖光",
            expected_prompt=True,
            preserve_path_groups={
                "composition.framing": [["中景", "medium shot"]],
                "style.primary": [["水彩", "watercolor"]],
            },
        ),
    ]
    payload = evaluate_preservation_r1(sequence)
    assert payload.status == "ok"
    assert payload.score == 1.0
    assert payload.counts["groups_lost"] == 0


def test_preservation_detects_actual_loss_of_required_attribute() -> None:
    sequence = [
        PromptSequenceTurn(turn_id="t1", prompt_text="一只橘猫 中景 soft", expected_prompt=True),
        PromptSequenceTurn(
            turn_id="t2",
            prompt_text="一只橘猫 soft",
            expected_prompt=True,
            preserve_path_groups={"composition.framing": [["中景", "medium shot"]]},
        ),
    ]
    payload = evaluate_preservation_r1(sequence)
    assert payload.status == "ok"
    assert payload.score == 0.0
    assert payload.counts["groups_lost"] == 1
    assert payload.details["pair_details"][0]["lost_groups"] == [["中景", "medium shot"]]


def test_forbidden_keyword_hit_and_source_guard_are_reported_separately() -> None:
    """Q4：关键词命中不得再被命名为 guard_bypassed，也不得派生出 guard 事实。"""
    hit = evaluate_unauthorized_addition_r1(["狗", "dog"], "一只橘猫和一只狗", guard_triggered=0)
    assert hit.counts["forbidden_keyword_hit"] == 1
    assert hit.counts["source_guard_triggered"] == 0
    # 关键词命中不是来源校验事实：bypassed 必须不可观测，禁止由 hit 派生。
    assert "source_guard_bypassed" not in hit.counts
    assert hit.details["source_guard_bypassed"] is None
    assert hit.details["source_guard_bypassed_observability"] == "not_observable"

    guarded = evaluate_unauthorized_addition_r1(
        ["狗", "dog"], "一只橘猫和一只狗", guard_triggered=2
    )
    assert guarded.counts["forbidden_keyword_hit"] == 1
    assert guarded.counts["source_guard_triggered"] == 2
    assert guarded.details["source_guard_bypassed"] is None

    clean = evaluate_unauthorized_addition_r1(["狗", "dog"], "一只橘猫", guard_triggered=0)
    assert clean.counts["forbidden_keyword_hit"] == 0
    assert clean.details["source_guard_bypassed"] is None


def test_legacy_metric_functions_still_available() -> None:
    """复用旧接口：v1 函数保留，旧 Run 可继续重算。"""
    from evaluation.metrics.prompt import (
        evaluate_preservation,
        evaluate_unauthorized_addition,
    )

    legacy = evaluate_unauthorized_addition(["狗"], "一只橘猫和狗", guard_triggered=0)
    assert legacy.counts["hits"] == 1
    assert legacy.counts["guard_bypassed"] == 1
    assert evaluate_preservation([], []) is not None
