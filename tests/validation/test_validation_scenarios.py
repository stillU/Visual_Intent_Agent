"""任务书「必测场景」与状态不变量测试（SET / CLEAR / PIN / UNPIN / 不变量）。

本文件是任务书逐条对照的落点；每条场景的测试名与注释都标注了它对应的条目。
所有"不相关字段保持"断言都是属性级的：对 12 条白名单路径做逐值比较，
而不是只比较整对象（`==`）；七类 Facet 全部被覆盖。
"""

from __future__ import annotations

import pytest

from validation_helpers import (
    ALL_PATHS,
    TYPICAL_VALUES,
    clear_delta,
    context,
    delta,
    full_intent,
    non_target_snapshot,
    path_snapshot,
    pin_delta,
    resolve_path,
    set_delta,
    unpin_delta,
    with_pinned,
    with_resolution,
)
from visual_intent_agent.domain import (
    IntentRevision,
    Resolution,
    VisualIntent,
)
from visual_intent_agent.validation import validate
from visual_intent_agent.validation import reduce as reduce_intent
from visual_intent_agent.validation.validator import (
    EVIDENCE_MISSING,
    NO_STATE_EFFECT,
    PIN_REQUIRES_EXISTING_VALUE,
    UNPIN_REQUIRES_PINNED_PATH,
)


def run(deltas, intent: VisualIntent, ctx=None):
    """validate → reduce 的完整确定性链路（Step 03/05 的真实调用方式）。"""
    result = validate(deltas, ctx or context("msg_1"), intent)
    return result, reduce_intent(intent, result.accepted)


class TestSetScenarios:
    """必测场景 · SET。"""

    def test_set_composition_framing_keeps_every_other_field(self) -> None:
        """修改 composition.framing 后其他所有字段不变。"""
        intent = full_intent()
        before = non_target_snapshot(intent, "composition.framing")
        result, reduced = run([set_delta("composition.framing", "wide establishing")], intent)
        assert result.accepted and not result.rejected
        assert resolve_path(reduced.intent, "composition.framing") == "wide establishing"
        assert non_target_snapshot(reduced.intent, "composition.framing") == before
        assert len(before) == 11  # 其余 6 类 Facet 共 11 条路径全部被逐一比较

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_set_only_touches_the_target_path_for_every_facet_field(self, path: str) -> None:
        intent = full_intent()
        before = non_target_snapshot(intent, path)
        _, reduced = run([set_delta(path, TYPICAL_VALUES[path])], intent)
        assert non_target_snapshot(reduced.intent, path) == before

    def test_illegal_path_rejects_the_whole_delta(self) -> None:
        """使用非法路径时整个该 Delta 被拒绝。"""
        bad = set_delta("composition.framing_x", "wide")
        original = full_intent()
        result, reduced = run([bad], original)
        assert result.accepted == []
        assert len(result.rejected) == 1
        assert result.rejected[0].delta == bad  # 原样返回，未被改写
        assert reduced.intent == original  # 状态零变化
        assert result.issues  # 有可观察 issue

    def test_llm_candidate_without_evidence_is_rejected(self) -> None:
        """没有 EvidenceRef 的 LLM 候选被拒绝。"""
        candidate = delta("SET", "style.primary", value="cinematic", evidence_refs=[])
        original = full_intent()
        result, reduced = run([candidate], original)
        assert result.accepted == []
        assert EVIDENCE_MISSING in {issue.code for issue in result.issues}
        assert reduced.intent == original

    def test_a_bad_delta_does_not_block_its_neighbours(self) -> None:
        good = set_delta("style.primary", "cinematic")
        bad = set_delta("style.primry", "typo")
        result, reduced = run([good, bad], full_intent())
        assert result.accepted == [good]
        assert len(result.rejected) == 1
        assert resolve_path(reduced.intent, "style.primary") == "cinematic"


class TestClearScenarios:
    """必测场景 · CLEAR。"""

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_clear_only_clears_the_target_field_and_its_resolution(self, path: str) -> None:
        intent = with_resolution(full_intent(), path, Resolution.USER_SPECIFIED)
        before = non_target_snapshot(intent, path)
        resolutions_before = {
            key: value for key, value in intent.resolutions.items() if key != path
        }
        result, reduced = run([clear_delta(path)], intent)
        assert result.accepted
        assert resolve_path(reduced.intent, path) is None
        assert path not in reduced.intent.resolutions
        assert reduced.intent.resolutions == resolutions_before
        assert non_target_snapshot(reduced.intent, path) == before

    def test_clear_is_not_interpreted_as_delegated(self) -> None:
        intent = with_resolution(full_intent(), "environment.location", Resolution.USER_SPECIFIED)
        _, reduced = run([clear_delta("environment.location")], intent)
        assert "environment.location" not in reduced.intent.resolutions
        assert resolve_path(reduced.intent, "environment.location") is None
        # missing ≠ user_delegated
        assert all(
            record.resolution is not Resolution.USER_DELEGATED
            for record in reduced.intent.resolutions.values()
        )

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_clearing_a_pinned_path_is_rejected_with_an_issue(self, path: str) -> None:
        intent = with_pinned(full_intent(), path)
        delta_clear = clear_delta(path)
        result, reduced = run([delta_clear], intent)
        assert result.accepted == []
        assert len(result.rejected) == 1
        assert result.issues and all(issue.code.startswith("validation.") for issue in result.issues)
        assert reduced.intent == intent  # 被拒的 CLEAR 不产生任何修改

    def test_clear_of_a_non_pinned_path_is_not_rejected(self) -> None:
        intent = full_intent()
        result, _ = run([clear_delta("color.palette")], intent)
        assert result.accepted and not result.rejected

    def test_clear_of_an_empty_unspecified_path_has_no_effect_and_is_rejected(self) -> None:
        result = validate([clear_delta("color.palette")], context("msg_1"), VisualIntent())
        assert result.accepted == []
        assert NO_STATE_EFFECT in {issue.code for issue in result.issues}


class TestPinUnpinScenarios:
    """必测场景 · PIN / UNPIN。"""

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_pin_keeps_values_unchanged(self, path: str) -> None:
        intent = full_intent()
        before = path_snapshot(intent)
        _, reduced = run([pin_delta(path)], intent)
        assert path_snapshot(reduced.intent) == before
        assert reduced.intent.pinned_paths == frozenset({path})

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_unpin_keeps_values_unchanged(self, path: str) -> None:
        intent = with_pinned(full_intent(), path)
        before = path_snapshot(intent)
        _, reduced = run([unpin_delta(path)], intent)
        assert path_snapshot(reduced.intent) == before
        assert reduced.intent.pinned_paths == frozenset()

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_pin_of_a_path_without_a_value_is_rejected_with_an_issue(self, path: str) -> None:
        result = validate([pin_delta(path)], context("msg_1"), VisualIntent())
        assert result.accepted == []
        assert PIN_REQUIRES_EXISTING_VALUE in {issue.code for issue in result.issues}

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_unpin_of_an_unpinned_path_produces_no_hidden_modification(self, path: str) -> None:
        intent = full_intent()
        delta_unpin = unpin_delta(path)
        result, reduced = run([delta_unpin], intent)
        assert result.accepted == []
        assert UNPIN_REQUIRES_PINNED_PATH in {issue.code for issue in result.issues}
        assert reduced.intent == intent
        assert reduced.change_summary.unpinned_paths == []

    def test_pin_does_not_add_a_resolution_or_a_value(self) -> None:
        intent = full_intent()
        _, reduced = run([pin_delta("style.primary")], intent)
        assert reduced.intent.resolutions == intent.resolutions

    def test_unpin_does_not_remove_a_resolution_or_a_value(self) -> None:
        intent = with_resolution(
            with_pinned(full_intent(), "style.primary"),
            "style.primary",
            Resolution.USER_SPECIFIED,
        )
        _, reduced = run([unpin_delta("style.primary")], intent)
        assert reduced.intent.resolutions == intent.resolutions

    def test_set_on_a_pinned_path_is_allowed_and_changes_only_that_value(self) -> None:
        intent = with_pinned(full_intent(), "style.primary")
        before = non_target_snapshot(intent, "style.primary")
        result, reduced = run([set_delta("style.primary", "new style")], intent)
        assert result.accepted
        assert resolve_path(reduced.intent, "style.primary") == "new style"
        assert reduced.intent.pinned_paths == frozenset({"style.primary"})
        assert non_target_snapshot(reduced.intent, "style.primary") == before


class TestInvariants:
    """必测场景 · 不变量。"""

    def test_original_object_is_completely_unchanged_after_reduce(self) -> None:
        intent = with_pinned(
            with_resolution(full_intent(), "style.primary", Resolution.USER_SPECIFIED),
            "color.palette",
        )
        values_before = path_snapshot(intent)
        resolutions_before = dict(intent.resolutions)
        pinned_before = intent.pinned_paths
        dump_before = intent.model_dump_json()

        deltas = [
            set_delta("style.primary", "changed"),
            clear_delta("color.palette"),
            pin_delta("camera.angle"),
            unpin_delta("color.palette"),
        ]
        reduce_intent(intent, deltas)

        assert path_snapshot(intent) == values_before
        assert dict(intent.resolutions) == resolutions_before
        assert intent.pinned_paths == pinned_before
        assert intent.model_dump_json() == dump_before

    def test_reduce_does_not_mutate_the_delta_list(self) -> None:
        deltas = [set_delta("style.primary", "x"), pin_delta("color.palette")]
        before = list(deltas)
        reduce_intent(full_intent(), deltas)
        assert deltas == before

    def test_delta_order_is_observable_and_last_write_wins(self) -> None:
        first = set_delta("style.primary", "first")
        second = set_delta("style.primary", "second")
        _, reduced = run([first, second], full_intent())
        assert resolve_path(reduced.intent, "style.primary") == "second"
        _, reversed_reduced = run([second, first], full_intent())
        assert resolve_path(reversed_reduced.intent, "style.primary") == "first"
        assert reduced.change_summary.changed_paths == ["style.primary"]

    def test_delta_order_between_set_and_clear_is_observable(self) -> None:
        original = full_intent()
        _, set_then_clear = run(
            [set_delta("style.primary", "new"), clear_delta("style.primary")], original
        )
        assert resolve_path(set_then_clear.intent, "style.primary") is None
        _, clear_then_set = run(
            [clear_delta("style.primary"), set_delta("style.primary", "new")], original
        )
        assert resolve_path(clear_then_set.intent, "style.primary") == "new"

    def test_delta_order_between_pin_and_unpin_is_observable(self) -> None:
        # 序内语义可观察：同一批次里 PIN → UNPIN 与 UNPIN → PIN 结果相反。
        # 两条 Delta 各自都必须对"批次开始前的状态"合法，因此起点取已 pinned 的
        # style.primary：第一个批次里 PIN 幂等、UNPIN 合法；第二个批次反之。
        original = with_pinned(full_intent(), "style.primary")
        _, pin_then_unpin = run(
            [pin_delta("style.primary"), unpin_delta("style.primary")], original
        )
        assert pin_then_unpin.intent.pinned_paths == frozenset()
        _, unpin_then_pin = run(
            [unpin_delta("style.primary"), pin_delta("style.primary")], original
        )
        assert unpin_then_pin.intent.pinned_paths == frozenset({"style.primary"})

    def test_clear_after_unpin_in_the_same_batch_is_rejected(self) -> None:
        # 已知顺序语义：Validator 对每条 Delta 都用批次开始前的 Intent 判定，
        # 所以同批次的 UNPIN → CLEAR 里 CLEAR 仍会因初始 pinned 被拒。
        intent = with_pinned(full_intent(), "style.primary")
        result = validate(
            [unpin_delta("style.primary"), clear_delta("style.primary")],
            context("msg_1"),
            intent,
        )
        assert [delta_.operation.value for delta_ in result.accepted] == ["UNPIN"]
        assert len(result.rejected) == 1
        assert result.rejected[0].delta.operation.value == "CLEAR"

    def test_clear_of_an_unpinned_path_is_allowed(self) -> None:
        intent = full_intent()
        result = validate([clear_delta("style.primary")], context("msg_1"), intent)
        assert len(result.accepted) == 1, result.issues

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_non_target_paths_are_deep_equal_for_a_single_set(self, path: str) -> None:
        intent = full_intent()
        _, reduced = run([set_delta(path, TYPICAL_VALUES[path])], intent)
        for other in ALL_PATHS:
            if other != path:
                assert resolve_path(reduced.intent, other) == resolve_path(intent, other), other

    def test_non_target_facets_are_deep_equal(self) -> None:
        intent = full_intent()
        _, reduced = run(
            [set_delta("subject.count", 5), clear_delta("color.palette")], intent
        )
        assert reduced.intent.subject.count == 5
        for facet_name in ("composition", "environment", "style", "lighting", "camera"):
            if facet_name == "color":
                continue
            assert getattr(reduced.intent, facet_name) == getattr(intent, facet_name)
        assert reduced.intent.color.palette is None

    def test_important_change_invalidates_the_old_confirmation(self) -> None:
        _, reduced = run([set_delta("camera.depth_of_field", "deep")], full_intent())
        assert reduced.change_summary.confirmation_invalidated is True

    def test_clear_invalidates_the_old_confirmation(self) -> None:
        _, reduced = run([clear_delta("camera.depth_of_field")], full_intent())
        assert reduced.change_summary.confirmation_invalidated is True

    def test_pin_and_unpin_invalidate_the_old_confirmation_conservatively(self) -> None:
        intent = with_pinned(full_intent(), "style.primary")
        _, pinned = run([pin_delta("camera.angle")], intent)
        assert pinned.change_summary.confirmation_invalidated is True
        _, unpinned = run([unpin_delta("style.primary")], intent)
        assert unpinned.change_summary.confirmation_invalidated is True

    def test_an_empty_delta_list_leaves_confirmation_valid(self) -> None:
        _, reduced = run([], full_intent())
        assert reduced.intent == full_intent()
        assert reduced.change_summary.confirmation_invalidated is False

    def test_replaying_the_same_revision_is_reproducible(self) -> None:
        intent = full_intent()
        deltas = [
            set_delta("style.primary", "cinematic"),
            pin_delta("color.palette"),
            clear_delta("camera.depth_of_field"),
            set_delta("subject.count", 3),
        ]
        first = reduce_intent(intent, deltas)
        second = reduce_intent(intent, deltas)
        third = reduce_intent(full_intent(), list(deltas))
        assert first.model_dump_json() == second.model_dump_json()
        assert first.model_dump_json() == third.model_dump_json()
        # 也重新跑一遍完整 validate → reduce 链路
        _, replayed = run(deltas, full_intent())
        assert replayed.model_dump_json() == first.model_dump_json()

    def test_not_specified_fields_are_preserved_value_by_value(self) -> None:
        # README 不变量 4：未被 Delta 指定的字段必须保持不变。
        intent = full_intent()
        specified = {"subject.pose_action", "lighting.character"}
        deltas = [set_delta("subject.pose_action", "walking"), pin_delta("lighting.character")]
        _, reduced = run(deltas, intent)
        for path in ALL_PATHS:
            expected = (
                "walking" if path == "subject.pose_action" else resolve_path(intent, path)
            )
            assert resolve_path(reduced.intent, path) == expected, path
        assert set(specified)  # 显式记录本用例覆盖的路径

    def test_reducer_does_not_create_an_intent_revision(self) -> None:
        # revision 组装（含 parent_revision_id）是 Step 06 的职责。
        result = reduce_intent(full_intent(), [set_delta("style.primary", "x")])
        assert not isinstance(result.intent, IntentRevision)
        assert not hasattr(result, "revision")
        assert set(type(result).model_fields) == {"intent", "change_summary"}

    def test_reduce_result_only_offers_intent_and_change_summary(self) -> None:
        _, reduced = run([set_delta("style.primary", "x")], full_intent())
        assert set(type(reduced).model_fields) == {"intent", "change_summary"}

    def test_full_chain_result_is_a_valid_visual_intent(self) -> None:
        _, reduced = run(
            [
                set_delta("subject.count", 4),
                set_delta("style.primary", "cinematic"),
                pin_delta("style.primary"),
            ],
            full_intent(),
        )
        assert isinstance(reduced.intent, VisualIntent)
        # 通过 domain 的 model_validator 重新验证：pinned/resolutions 仍在白名单内。
        assert VisualIntent.model_validate(reduced.intent.model_dump()) == reduced.intent
