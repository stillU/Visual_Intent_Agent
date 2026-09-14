"""ChangeSummary 语义测试：五个字段的精确行为与保守失效规则。"""

from __future__ import annotations

import pytest

from validation_helpers import (
    ALL_PATHS,
    TYPICAL_VALUES,
    clear_delta,
    context,
    delta,
    full_intent,
    pin_delta,
    set_delta,
    unpin_delta,
    with_pinned,
    with_resolution,
)
from visual_intent_agent.domain import Resolution, VisualIntent
from visual_intent_agent.validation import ChangeSummary, reduce as reduce_intent, validate


def summary_of(deltas, intent: VisualIntent) -> ChangeSummary:
    return reduce_intent(intent, deltas).change_summary


class TestChangedPaths:
    def test_accepted_sets_are_listed_in_application_order(self) -> None:
        deltas = [
            set_delta("style.primary", "a"),
            set_delta("color.palette", "b"),
            set_delta("camera.angle", "c"),
        ]
        summary = summary_of(deltas, full_intent())
        assert summary.changed_paths == ["style.primary", "color.palette", "camera.angle"]
        assert summary.cleared_paths == []
        assert summary.pinned_paths == []
        assert summary.unpinned_paths == []
        assert summary.confirmation_invalidated is True

    def test_rejected_deltas_never_appear_in_the_summary(self) -> None:
        # 只把 validate 的 accepted 交给 reduce 是调用方契约；这里同时验证被拒
        # Delta 不会以任何形式出现在 summary 中。
        bad = set_delta("style.primry", "a")
        result = validate([bad], context("msg_1"), full_intent())
        assert result.rejected
        summary = summary_of(result.accepted, full_intent())
        assert summary == ChangeSummary()

    def test_repeated_sets_are_deduplicated_but_keep_first_position(self) -> None:
        deltas = [
            set_delta("style.primary", "a"),
            set_delta("color.palette", "b"),
            set_delta("style.primary", "c"),
        ]
        summary = summary_of(deltas, full_intent())
        assert summary.changed_paths == ["style.primary", "color.palette"]

    def test_resolution_only_set_counts_as_a_change(self) -> None:
        summary = summary_of(
            [set_delta("style.primary", resolution=Resolution.USER_DELEGATED)], full_intent()
        )
        assert summary.changed_paths == ["style.primary"]
        assert summary.confirmation_invalidated is True

    def test_clear_then_set_lists_the_path_in_both_fields(self) -> None:
        summary = summary_of(
            [clear_delta("style.primary"), set_delta("style.primary", "back again")],
            full_intent(),
        )
        assert summary.cleared_paths == ["style.primary"]
        assert summary.changed_paths == ["style.primary"]
        assert summary.confirmation_invalidated is True

    def test_set_then_clear_lists_the_path_in_both_fields(self) -> None:
        summary = summary_of(
            [set_delta("style.primary", "new"), clear_delta("style.primary")],
            full_intent(),
        )
        assert summary.changed_paths == ["style.primary"]
        assert summary.cleared_paths == ["style.primary"]


class TestPinnedAndUnpinnedPaths:
    def test_pin_is_listed_and_invalidates_confirmation(self) -> None:
        summary = summary_of([pin_delta("style.primary")], full_intent())
        assert summary.pinned_paths == ["style.primary"]
        assert summary.changed_paths == []
        assert summary.confirmation_invalidated is True

    def test_unpin_is_listed_and_invalidates_confirmation(self) -> None:
        intent = with_pinned(full_intent(), "color.palette")
        summary = summary_of([unpin_delta("color.palette")], intent)
        assert summary.unpinned_paths == ["color.palette"]
        assert summary.confirmation_invalidated is True

    def test_pin_then_unpin_lists_both_and_deduplicates(self) -> None:
        summary = summary_of(
            [pin_delta("style.primary"), pin_delta("style.primary"), unpin_delta("style.primary")],
            full_intent(),
        )
        assert summary.pinned_paths == ["style.primary"]
        assert summary.unpinned_paths == ["style.primary"]

    def test_pin_and_unpin_do_not_change_values(self) -> None:
        intent = full_intent()
        result = reduce_intent(
            intent, [pin_delta("style.primary"), unpin_delta("style.primary")]
        )
        assert result.intent == intent


class TestConfirmationInvalidated:
    """保守规则（任务书原文 + ARCHITECTURE.md 冻结）：任何生效修改都置 True。"""

    def test_no_deltas_means_no_invalidation(self) -> None:
        summary = summary_of([], full_intent())
        assert summary == ChangeSummary()
        assert summary.confirmation_invalidated is False

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_every_visual_set_invalidates(self, path: str) -> None:
        summary = summary_of([set_delta(path, TYPICAL_VALUES[path])], full_intent())
        assert summary.confirmation_invalidated is True

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_every_visual_clear_invalidates(self, path: str) -> None:
        summary = summary_of([clear_delta(path)], full_intent())
        assert summary.confirmation_invalidated is True

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_every_pin_invalidates(self, path: str) -> None:
        # 保守规则：保持约束变化后必须重新确认（不区分"仅保持"与"视觉值变化"）。
        summary = summary_of([pin_delta(path)], full_intent())
        assert summary.confirmation_invalidated is True

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_every_unpin_invalidates(self, path: str) -> None:
        intent = with_pinned(full_intent(), path)
        summary = summary_of([unpin_delta(path)], intent)
        assert summary.confirmation_invalidated is True

    def test_unpin_invalidates_even_though_it_only_relaxes_a_constraint(self) -> None:
        # 冻结为保守：UNPIN 也不得让旧确认继续有效。
        intent = with_resolution(
            with_pinned(full_intent(), "style.primary"), "style.primary", Resolution.USER_SPECIFIED
        )
        summary = summary_of([unpin_delta("style.primary")], intent)
        assert summary.confirmation_invalidated is True
        assert summary.unpinned_paths == ["style.primary"]
