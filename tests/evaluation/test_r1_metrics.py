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


def test_first_prompt_initial_changed_paths_do_not_pollute_next_comparison() -> None:
    """F1 复现：首轮初始 SET 不得留在 pending 里，把下一轮应保留项误当合法修改排除。

    t1 生成 `cat watercolor`（初始 SET 主体与风格）；t2 只合法改光，实际生成
    `dog watercolor`。正确结果是一个比较对：保留一组、丢失一组，得分 0.5。
    修复前 t1 的初始 changed_paths 一直未清空 → 两组被排除 → `not_applicable`。
    """
    sequence = [
        PromptSequenceTurn(
            turn_id="t1",
            prompt_text="cat watercolor",
            expected_prompt=True,
            changed_paths=["subject.description", "style.primary"],
        ),
        PromptSequenceTurn(
            turn_id="t2",
            prompt_text="dog watercolor",
            expected_prompt=True,
            changed_paths=["lighting.character"],
            preserve_path_groups={
                "subject.description": [["cat", "猫"]],
                "style.primary": [["watercolor", "水彩"]],
            },
        ),
    ]
    payload = evaluate_preservation_r1(sequence)
    assert payload.status == "ok"
    assert payload.counts["pairs"] == 1
    assert payload.counts["pairs_scored"] == 1
    assert payload.score == 0.5
    assert payload.counts["groups_kept"] == 1
    assert payload.counts["groups_lost"] == 1
    assert payload.details["pair_details"][0]["previous_turn_id"] == "t1"
    assert payload.details["pair_details"][0]["excluded_changed_paths"] == [
        "lighting.character"
    ]


def test_first_prompt_initial_changed_paths_allow_full_preservation_when_kept() -> None:
    """同上前提但 t2 完整保留主体与风格 → 1.0（初始 SET 不再被误排除）。"""
    sequence = [
        PromptSequenceTurn(
            turn_id="t1",
            prompt_text="cat watercolor",
            expected_prompt=True,
            changed_paths=["subject.description", "style.primary"],
        ),
        PromptSequenceTurn(
            turn_id="t2",
            prompt_text="cat watercolor warm",
            expected_prompt=True,
            changed_paths=["lighting.character"],
            preserve_path_groups={
                "subject.description": [["cat", "猫"]],
                "style.primary": [["watercolor", "水彩"]],
            },
        ),
    ]
    payload = evaluate_preservation_r1(sequence)
    assert payload.status == "ok"
    assert payload.score == 1.0
    assert payload.counts["groups_kept"] == 2
    assert payload.counts["groups_lost"] == 0


def test_initial_changed_paths_survive_clarifications_but_not_the_first_prompt() -> None:
    """首次生成前经历多个澄清轮：初始字段仍进入后续保留评分。

    t1/t2 澄清无 Prompt；t3 才首次生成并携带初始 SET；t4 改动主体 → 应检出丢失。
    修复前该初始 SET 会一直留在 pending，使 t4 的两组都被排除。
    """
    sequence = [
        PromptSequenceTurn(turn_id="t1", prompt_text=None, expected_prompt=False),
        PromptSequenceTurn(turn_id="t2", prompt_text=None, expected_prompt=False),
        PromptSequenceTurn(
            turn_id="t3",
            prompt_text="cat watercolor",
            expected_prompt=True,
            changed_paths=["subject.description", "style.primary"],
        ),
        PromptSequenceTurn(
            turn_id="t4",
            prompt_text="dog watercolor",
            expected_prompt=True,
            changed_paths=["lighting.character"],
            preserve_path_groups={
                "subject.description": [["cat", "猫"]],
                "style.primary": [["watercolor", "水彩"]],
            },
        ),
    ]
    payload = evaluate_preservation_r1(sequence)
    assert payload.status == "ok"
    assert payload.counts["no_prompt_not_applicable"] == 2
    assert payload.counts["pairs"] == 1
    assert payload.score == 0.5
    assert payload.counts["groups_kept"] == 1
    assert payload.counts["groups_lost"] == 1


def test_no_prompt_change_is_excluded_only_from_that_comparison() -> None:
    """两个有效 Prompt 之间的无 Prompt 合法 SET：仅本次比较排除，之后不沿用。"""
    sequence = [
        PromptSequenceTurn(
            turn_id="t1",
            prompt_text="watercolor medium shot",
            expected_prompt=True,
            changed_paths=["subject.description"],
        ),
        PromptSequenceTurn(
            turn_id="t2",
            prompt_text=None,
            expected_prompt=False,
            changed_paths=["composition.framing"],
        ),
        PromptSequenceTurn(
            turn_id="t3",
            prompt_text="watercolor closeup",
            expected_prompt=True,
            preserve_path_groups={
                "style.primary": [["watercolor", "水彩"]],
                "composition.framing": [["closeup", "特写"]],
            },
        ),
        PromptSequenceTurn(
            turn_id="t4",
            prompt_text="realism closeup",
            expected_prompt=True,
            preserve_path_groups={
                "style.primary": [["watercolor", "水彩"]],
                "composition.framing": [["closeup", "特写"]],
            },
        ),
    ]
    payload = evaluate_preservation_r1(sequence)
    assert payload.status == "ok"
    assert payload.counts["no_prompt_not_applicable"] == 1
    assert payload.counts["pairs"] == 2
    # t2 的合法改动只在 (t1,t3) 被排除；(t3,t4) 不再沿用更早的排除项。
    assert payload.details["pair_details"][0]["excluded_changed_paths"] == [
        "composition.framing"
    ]
    assert payload.details["pair_details"][0]["score"] == 1.0
    assert payload.details["pair_details"][1]["excluded_changed_paths"] == []
    assert payload.details["pair_details"][1]["score"] == 0.5


def test_prompt_without_preserve_labels_still_resets_baseline_and_exclusions() -> None:
    """中间有效 Prompt 无保留标签：仍更新基准并清空累计路径，下一轮不沿用排除项。"""
    sequence = [
        PromptSequenceTurn(
            turn_id="t1",
            prompt_text="realism medium shot",
            expected_prompt=True,
            changed_paths=["subject.description"],
        ),
        PromptSequenceTurn(
            turn_id="t2",
            prompt_text="watercolor medium shot",
            expected_prompt=True,
            preserve_path_groups={},
            changed_paths=["style.primary"],
        ),
        PromptSequenceTurn(
            turn_id="t3",
            prompt_text="realism medium shot warm",
            expected_prompt=True,
            preserve_path_groups={
                "style.primary": [["watercolor", "水彩"]],
                "composition.framing": [["medium shot", "中景"]],
            },
        ),
    ]
    payload = evaluate_preservation_r1(sequence)
    assert payload.status == "ok"
    assert payload.counts["pairs"] == 1
    # 基准更新为 t2（无标签也不阻塞），其 t2 合法改动不进入 (t2,t3) 的排除项。
    assert payload.details["pair_details"][0]["previous_turn_id"] == "t2"
    assert payload.details["pair_details"][0]["excluded_changed_paths"] == []
    assert payload.details["pair_details"][0]["score"] == 0.5
    assert payload.counts["groups_lost"] == 1


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


def test_completion_denominator_excludes_clarification_and_accept_turns() -> None:
    """F2：正常澄清/终局接受不要求生成，不进完成率分母。"""
    sequence = [
        PromptSequenceTurn(turn_id="t1", prompt_text=None, expected_prompt=False),
        PromptSequenceTurn(turn_id="t2", prompt_text="一只橘猫 中景", expected_prompt=True),
        PromptSequenceTurn(turn_id="t3", prompt_text=None, expected_prompt=False),
    ]
    completion = evaluate_generation_completion(sequence)
    assert completion.status == "ok"
    assert completion.counts == {"expected_prompt_turns": 1, "produced": 1, "failed": 0}
    assert completion.details["failed_turn_ids"] == []


def test_expected_generation_without_prompt_counts_incomplete_for_every_turn() -> None:
    """F2：预定生成轮未产出 Prompt 一律计未完成，级联阻断不缩减分母。"""
    sequence = [
        PromptSequenceTurn(turn_id="t1", prompt_text=None, expected_prompt=True),
        PromptSequenceTurn(turn_id="t2", prompt_text=None, expected_prompt=True),
    ]
    completion = evaluate_generation_completion(sequence)
    assert completion.status == "ok"
    assert completion.score == 0.0
    assert completion.counts == {"expected_prompt_turns": 2, "produced": 0, "failed": 2}
    assert completion.details["failed_turn_ids"] == ["t1", "t2"]


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
