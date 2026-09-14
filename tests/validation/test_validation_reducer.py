"""Reducer 操作语义测试：SET / CLEAR / PIN / UNPIN 只做被指定的事。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from validation_helpers import (
    ALL_PATHS,
    TYPICAL_VALUES,
    clear_delta,
    full_intent,
    intent_with,
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
    DeltaOperation,
    IntentDelta,
    Resolution,
    ResolutionRecord,
    VisualIntent,
)
from visual_intent_agent.validation import ReducerError, reduce


class TestSetOperation:
    """SET 只改指定路径（值 + 该路径的 Resolution 绑定）。"""

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_set_changes_exactly_one_path(self, path: str) -> None:
        intent = full_intent()
        before = non_target_snapshot(intent, path)
        result = reduce(intent, [set_delta(path, TYPICAL_VALUES[path])])
        assert result.intent is not intent
        assert len(non_target_snapshot(result.intent, path)) == 11
        assert non_target_snapshot(result.intent, path) == before

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_set_writes_the_new_value(self, path: str) -> None:
        new_value: str | int = "changed" if path != "subject.count" else 7
        result = reduce(full_intent(), [set_delta(path, new_value)])
        assert resolve_path(result.intent, path) == new_value

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_set_on_an_empty_intent_only_fills_that_path(self, path: str) -> None:
        intent = VisualIntent()
        result = reduce(intent, [set_delta(path, TYPICAL_VALUES[path])])
        assert resolve_path(result.intent, path) == TYPICAL_VALUES[path]
        before = non_target_snapshot(intent, path)
        assert non_target_snapshot(result.intent, path) == before
        assert all(value is None for value in before.values())

    def test_set_with_resolution_binds_the_resolution_record(self) -> None:
        delta_ref = set_delta(
            "style.primary", "cinematic realism", resolution=Resolution.USER_SPECIFIED
        )
        result = reduce(full_intent(), [delta_ref])
        record = result.intent.resolutions["style.primary"]
        assert record.resolution is Resolution.USER_SPECIFIED
        assert record.evidence_refs == delta_ref.evidence_refs

    def test_resolution_only_set_does_not_change_any_value(self) -> None:
        intent = full_intent()
        before = path_snapshot(intent)
        d = set_delta("style.primary", resolution=Resolution.USER_DELEGATED)
        result = reduce(intent, [d])
        assert path_snapshot(result.intent) == before
        assert result.intent.resolutions["style.primary"].resolution is Resolution.USER_DELEGATED

    def test_resolution_only_set_on_a_valueless_path_keeps_the_path_valueless(self) -> None:
        intent = VisualIntent()
        d = set_delta("environment.mode", resolution=Resolution.USER_DELEGATED)
        result = reduce(intent, [d])
        assert resolve_path(result.intent, "environment.mode") is None
        assert "environment.mode" in result.intent.resolutions
        assert result.intent.resolutions["environment.mode"].resolution is Resolution.USER_DELEGATED

    def test_value_only_set_marks_the_path_user_specified(self) -> None:
        result = reduce(VisualIntent(), [set_delta("style.primary", "cinematic realism")])
        assert result.intent.resolutions["style.primary"].resolution is Resolution.USER_SPECIFIED

    def test_value_only_set_keeps_an_existing_resolution_record(self) -> None:
        # Reducer 不隐式改写已有授权语义：值型 SET 只在"该路径没有 ResolutionRecord"
        # 时才新建 user_specified。若调用方认为新值应升级授权，应在 Delta 中显式
        # 携带 resolution（Validator 检查 5 会核验其证据）。
        intent = with_resolution(VisualIntent(), "style.primary", Resolution.USER_DELEGATED)
        result = reduce(intent, [set_delta("style.primary", "cinematic realism")])
        assert result.intent.resolutions["style.primary"].resolution is Resolution.USER_DELEGATED
        assert resolve_path(result.intent, "style.primary") == "cinematic realism"

    def test_value_and_resolution_set_upgrades_a_delegated_path_explicitly(self) -> None:
        intent = with_resolution(VisualIntent(), "style.primary", Resolution.USER_DELEGATED)
        result = reduce(
            intent,
            [
                set_delta(
                    "style.primary",
                    "cinematic realism",
                    resolution=Resolution.USER_SPECIFIED,
                )
            ],
        )
        assert result.intent.resolutions["style.primary"].resolution is Resolution.USER_SPECIFIED
        assert resolve_path(result.intent, "style.primary") == "cinematic realism"

    def test_set_replaces_the_resolution_of_a_pinned_path_without_unpinning(self) -> None:
        intent = with_pinned(full_intent(), "style.primary")
        result = reduce(intent, [set_delta("style.primary", "gritty documentary")])
        assert resolve_path(result.intent, "style.primary") == "gritty documentary"
        assert result.intent.pinned_paths == frozenset({"style.primary"})

    def test_set_does_not_touch_unrelated_resolutions(self) -> None:
        intent = with_resolution(full_intent(), "color.palette", Resolution.USER_DELEGATED)
        result = reduce(intent, [set_delta("style.primary", "x")])
        assert result.intent.resolutions["color.palette"].resolution is Resolution.USER_DELEGATED
        assert set(result.intent.resolutions) == {"color.palette", "style.primary"}


class TestClearOperation:
    """CLEAR 只清除目标字段及其 Resolution；不得被解释成 delegated。"""

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_clear_removes_the_value_and_its_resolution_only(self, path: str) -> None:
        intent = with_resolution(full_intent(), path, Resolution.USER_SPECIFIED)
        before = non_target_snapshot(intent, path)
        result = reduce(intent, [clear_delta(path)])
        assert resolve_path(result.intent, path) is None
        assert path not in result.intent.resolutions
        assert non_target_snapshot(result.intent, path) == before

    def test_clear_does_not_create_a_delegated_resolution(self) -> None:
        intent = with_resolution(VisualIntent(), "color.palette", Resolution.USER_SPECIFIED)
        result = reduce(intent, [clear_delta("color.palette")])
        assert "color.palette" not in result.intent.resolutions
        assert resolve_path(result.intent, "color.palette") is None
        # missing ≠ user_delegated：清除后该路径回到 missing，没有任何 ResolutionRecord。
        assert result.intent.resolutions.get("color.palette") is None

    def test_clear_of_a_delegated_path_removes_the_delegation(self) -> None:
        intent = with_resolution(VisualIntent(), "composition.framing", Resolution.USER_DELEGATED)
        result = reduce(intent, [clear_delta("composition.framing")])
        assert result.intent.resolutions == {}

    def test_clear_keeps_pinned_marker_of_the_path(self) -> None:
        # pinned 路径的 CLEAR 由 Validator 拒绝；Reducer 本身不改变保持标记
        # （只清值与 Resolution），保持"PIN 只管保持"的语义边界。
        intent = with_pinned(with_resolution(full_intent(), "style.primary", Resolution.USER_SPECIFIED), "style.primary")
        result = reduce(intent, [clear_delta("style.primary")])
        assert result.intent.pinned_paths == frozenset({"style.primary"})
        assert resolve_path(result.intent, "style.primary") is None

    def test_clear_does_not_touch_other_resolutions(self) -> None:
        intent = full_intent()
        intent = with_resolution(intent, "style.primary", Resolution.USER_SPECIFIED)
        intent = with_resolution(intent, "color.palette", Resolution.USER_DELEGATED)
        result = reduce(intent, [clear_delta("style.primary")])
        assert result.intent.resolutions["color.palette"].resolution is Resolution.USER_DELEGATED
        assert "style.primary" not in result.intent.resolutions


class TestPinUnpinOperations:
    """PIN 不改值；UNPIN 不改值也不授权重新设计。"""

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_pin_keeps_every_value(self, path: str) -> None:
        intent = full_intent()
        before = path_snapshot(intent)
        result = reduce(intent, [pin_delta(path)])
        assert path_snapshot(result.intent) == before
        assert result.intent.pinned_paths == frozenset({path})
        assert result.intent.resolutions == intent.resolutions

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_unpin_keeps_every_value(self, path: str) -> None:
        intent = with_pinned(full_intent(), path)
        before = path_snapshot(intent)
        result = reduce(intent, [unpin_delta(path)])
        assert path_snapshot(result.intent) == before
        assert result.intent.pinned_paths == frozenset()

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_unpin_does_not_create_or_change_a_resolution(self, path: str) -> None:
        intent = with_pinned(full_intent(), path)
        result = reduce(intent, [unpin_delta(path)])
        assert result.intent.resolutions == intent.resolutions

    def test_pin_is_idempotent(self) -> None:
        intent = full_intent()
        result = reduce(intent, [pin_delta("style.primary"), pin_delta("style.primary")])
        assert result.intent.pinned_paths == frozenset({"style.primary"})
        assert result.change_summary.pinned_paths == ["style.primary"]

    def test_pin_and_unpin_other_paths_are_untouched(self) -> None:
        intent = with_pinned(full_intent(), "color.palette")
        result = reduce(intent, [pin_delta("style.primary")])
        assert result.intent.pinned_paths == frozenset({"color.palette", "style.primary"})
        result = reduce(result.intent, [unpin_delta("style.primary")])
        assert result.intent.pinned_paths == frozenset({"color.palette"})

    def test_unpin_does_not_authorize_redesign(self) -> None:
        # UNPIN 后没有任何 Resolution 变化：它只解除保持，不写 user_delegated。
        intent = with_pinned(
            with_resolution(full_intent(), "style.primary", Resolution.USER_SPECIFIED),
            "style.primary",
        )
        result = reduce(intent, [unpin_delta("style.primary")])
        assert result.intent.resolutions["style.primary"].resolution is Resolution.USER_SPECIFIED
        assert result.intent.pinned_paths == frozenset()

    def test_pin_does_not_convert_a_delegation(self) -> None:
        intent = with_resolution(VisualIntent(), "style.primary", Resolution.USER_DELEGATED)
        result = reduce(intent, [pin_delta("style.primary")])
        assert result.intent.resolutions["style.primary"].resolution is Resolution.USER_DELEGATED
        assert resolve_path(result.intent, "style.primary") is None


class TestReducerPreconditions:
    """Reducer 不修复不合法输入：绕过 Validator 的 Delta 必须显式失败。"""

    def test_set_with_a_non_str_value_on_a_str_path_raises(self) -> None:
        forged = IntentDelta.model_construct(
            operation=DeltaOperation.SET,
            path="style.primary",
            value=42,
            resolution=None,
            evidence_refs=[],
        )
        with pytest.raises(ReducerError) as excinfo:
            reduce(full_intent(), [forged])
        assert excinfo.value.code == "validation.value_type_mismatch"

    @pytest.mark.parametrize("value", [3.5, "3", True, 0, -2])
    def test_set_with_an_invalid_count_value_raises(self, value: object) -> None:
        forged = IntentDelta.model_construct(
            operation=DeltaOperation.SET,
            path="subject.count",
            value=value,
            resolution=None,
            evidence_refs=[],
        )
        with pytest.raises(ReducerError) as excinfo:
            reduce(full_intent(), [forged])
        assert excinfo.value.code == "validation.value_type_mismatch"

    def test_set_with_an_unknown_facet_raises(self) -> None:
        forged = IntentDelta.model_construct(
            operation=DeltaOperation.SET,
            path="subject.primary",  # subject Facet 没有 primary 字段
            value="x",
            resolution=None,
            evidence_refs=[],
        )
        with pytest.raises(ReducerError) as excinfo:
            reduce(full_intent(), [forged])
        assert excinfo.value.code == "validation.invalid_delta_path"

    def test_non_path_shaped_delta_raises(self) -> None:
        forged = IntentDelta.model_construct(
            operation=DeltaOperation.PIN,
            path="style",
            value=None,
            resolution=None,
            evidence_refs=[],
        )
        with pytest.raises(ReducerError) as excinfo:
            reduce(full_intent(), [forged])
        assert excinfo.value.code == "validation.invalid_delta_path"

    def test_unknown_operation_raises(self) -> None:
        forged = IntentDelta.model_construct(
            operation="REPLACE",
            path="style.primary",
            value="x",
            resolution=None,
            evidence_refs=[],
        )
        with pytest.raises(ReducerError) as excinfo:
            reduce(full_intent(), [forged])
        assert excinfo.value.code == "validation.operation_invalid"

    def test_reducer_error_is_a_normal_exception_with_a_code(self) -> None:
        assert issubclass(ReducerError, Exception)
        with pytest.raises(ReducerError):
            raise ReducerError("validation.example", "boom")

    def test_reducer_does_not_validate_the_whitelist_by_itself(self) -> None:
        # 白名单校验是 Validator 的职责；Reducer 只保证不会把坏值静默写进状态。
        forged = IntentDelta.model_construct(
            operation=DeltaOperation.PIN,
            path="style.primary",
            value=None,
            resolution=None,
            evidence_refs=[],
        )
        result = reduce(VisualIntent(), [forged])
        assert result.intent.pinned_paths == frozenset({"style.primary"})


def test_delta_construction_still_rejects_set_without_payload() -> None:
    # 前置合同（Step 01）仍然生效：Reducer 的输入集合本身已被收窄。
    with pytest.raises(ValidationError):
        IntentDelta(operation=DeltaOperation.SET, path="style.primary")


def test_reduce_rejects_a_resolution_record_that_would_be_invalid() -> None:
    # ReduceResult 只装真实模型；构造非法 ResolutionRecord 会被 pydantic 拒绝。
    with pytest.raises(ValidationError):
        ResolutionRecord(resolution="not_a_resolution")  # type: ignore[arg-type]
