"""Validator 单元测试（table-driven）：任务书 7 项检查。

每个检查一个 Test 类；参数化表是主要断言手段。拒绝断言统一用
「issue code ∈ 期望集合」，不依赖一个 Delta 携带多少条 issue。
"""

from __future__ import annotations

import pytest

from validation_helpers import (
    ALL_PATHS,
    TYPICAL_VALUES,
    clear_delta,
    context,
    delta,
    evidence,
    full_intent,
    intent_with,
    issue_codes,
    path_snapshot,
    pin_delta,
    rejected_codes,
    set_delta,
    unpin_delta,
    with_pinned,
    with_resolution,
)
from visual_intent_agent.domain import (
    SYSTEM_FIELD_NAMES,
    DeltaOperation,
    IntentDelta,
    Resolution,
    VisualIntent,
)
from visual_intent_agent.validation import validate
from visual_intent_agent.validation.validator import (
    EVIDENCE_MESSAGE_NOT_AVAILABLE,
    EVIDENCE_MISSING,
    EVIDENCE_PENDING_QUESTION_MISMATCH,
    EVIDENCE_PENDING_QUESTION_PATH_MISMATCH,
    EVIDENCE_PENDING_QUESTION_UNKNOWN,
    CLEAR_REQUIRES_UNPINNED_PATH,
    NO_STATE_EFFECT,
    OPERATION_INVALID,
    PATH_NOT_WHITELISTED,
    PIN_REQUIRES_EXISTING_VALUE,
    SYSTEM_FIELD_PROTECTED,
    UNAUTHORIZED_RESOLUTION_CLAIM,
    UNPIN_REQUIRES_PINNED_PATH,
    VALUE_TYPE_MISMATCH,
)

VALIDATION_ISSUE_CODES = {
    OPERATION_INVALID,
    PATH_NOT_WHITELISTED,
    SYSTEM_FIELD_PROTECTED,
    VALUE_TYPE_MISMATCH,
    EVIDENCE_MISSING,
    EVIDENCE_MESSAGE_NOT_AVAILABLE,
    EVIDENCE_PENDING_QUESTION_UNKNOWN,
    EVIDENCE_PENDING_QUESTION_MISMATCH,
    EVIDENCE_PENDING_QUESTION_PATH_MISMATCH,
    UNAUTHORIZED_RESOLUTION_CLAIM,
    PIN_REQUIRES_EXISTING_VALUE,
    UNPIN_REQUIRES_PINNED_PATH,
    CLEAR_REQUIRES_UNPINNED_PATH,
    NO_STATE_EFFECT,
}


def forged_set(path: str, value: object) -> IntentDelta:
    """绕过模型层类型校验构造违规值 Delta（模拟 LLM 原始输出的最坏情况）。

    `IntentDelta.value` 是 `str | int | None`，pydantic 宽松模式会把 3.5 这类
    值直接拒绝在构造期。Validator 的检查 3 仍然必须独立成立（防御纵深），
    因此用 `model_construct` 构造"模型层放行但语义非法"的 Delta。
    """
    return IntentDelta.model_construct(
        operation=DeltaOperation.SET,
        path=path,
        value=value,
        resolution=None,
        evidence_refs=[evidence("msg_1")],
    )


class TestOperationValidity:
    """检查 1：operation 是否属于 SET / CLEAR / PIN / UNPIN。"""

    @pytest.mark.parametrize("operation", [op.value for op in DeltaOperation])
    def test_every_frozen_operation_is_accepted_with_valid_shape(self, operation: str) -> None:
        intent = full_intent()
        payload: dict = {"operation": operation, "path": "style.primary"}
        if operation == "SET":
            payload["value"] = "cinematic realism"
        elif operation == "UNPIN":
            intent = with_pinned(intent, "style.primary")
        result = validate([delta(**payload)], context("msg_1"), intent)
        assert len(result.accepted) == 1
        assert result.rejected == []
        assert result.issues == []

    def test_unknown_operation_cannot_even_be_constructed(self) -> None:
        with pytest.raises(ValueError):
            IntentDelta(operation="REPLACE", path="style.primary", value="x")  # type: ignore[arg-type]

    def test_unknown_operation_value_on_a_forged_delta_is_rejected_with_issue(self) -> None:
        # 防御纵深：篡改后的 operation 不落进任何分支，必须显式拒绝而非静默忽略。
        forged = IntentDelta.model_construct(
            operation="SOMETHING_ELSE",
            path="style.primary",
            value=None,
            resolution=None,
            evidence_refs=[evidence("msg_1")],
        )
        result = validate([forged], context("msg_1"), VisualIntent())
        assert result.accepted == []
        assert [item.delta for item in result.rejected] == [forged]
        assert OPERATION_INVALID in rejected_codes(result)

    @pytest.mark.parametrize("operation", ["SET", "CLEAR", "PIN", "UNPIN"])
    def test_every_issue_code_lives_in_the_validation_namespace(self, operation: str) -> None:
        # 用一个必然非法的 Delta（未知路径）触发 issue，检查命名空间。
        payload: dict = {"operation": operation, "path": "unknown.path"}
        if operation == "SET":
            payload["value"] = "x"
        result = validate([delta(**payload)], context("msg_1"), full_intent())
        assert result.issues, operation
        for issue in result.issues:
            assert issue.code.startswith("validation.")
            assert issue.code in VALIDATION_ISSUE_CODES


class TestPathWhitelist:
    """检查 2：path ∈ Schema v1 白名单（只 import domain 的唯一定义）。"""

    @pytest.mark.parametrize("path", ALL_PATHS)
    @pytest.mark.parametrize("operation", ["SET", "CLEAR", "PIN", "UNPIN"])
    def test_all_12_whitelisted_paths_are_accepted(self, path: str, operation: str) -> None:
        base = full_intent()
        # 每条 operation 的附加前提：PIN 需要有值；UNPIN 需要已 pinned；
        # CLEAR 需要未 pinned。
        intent = with_pinned(base, path) if operation == "UNPIN" else base
        payload: dict = {"operation": operation, "path": path}
        if operation == "SET":
            payload["value"] = TYPICAL_VALUES[path]
        result = validate([delta(**payload)], context("msg_1"), intent)
        assert len(result.accepted) == 1, result.issues
        assert result.rejected == []

    @pytest.mark.parametrize(
        "path",
        [
            "subject",  # Facet 本身不是路径
            "subject.",  # 缺字段名
            "subject.unknown_field",
            "unknown_facet.field",
            "subject_count",  # 非 '<facet>.<field>' 形态
            "",
            "Style.primary",  # 大小写敏感
            "style.primary ",  # 尾随空格
            "subject.subject.count",
        ],
    )
    @pytest.mark.parametrize("operation", ["SET", "CLEAR", "PIN", "UNPIN"])
    def test_non_whitelisted_paths_are_rejected_for_every_operation(
        self, path: str, operation: str
    ) -> None:
        payload: dict = {"operation": operation, "path": path}
        if operation == "SET":
            payload["value"] = "x"
        result = validate([delta(**payload)], context("msg_1"), full_intent())
        assert result.accepted == []
        assert len(result.rejected) == 1
        assert PATH_NOT_WHITELISTED in rejected_codes(result)

    def test_rejected_path_is_never_rewritten_by_the_validator(self) -> None:
        bad = set_delta("style.primry", value="cinematic realism")
        result = validate([bad], context("msg_1"), full_intent())
        assert result.rejected[0].delta.path == "style.primry"
        assert result.rejected[0].delta == bad  # 逐值不变，没有被"纠正"


class TestValueType:
    """检查 3：value 与目标路径类型兼容。"""

    @pytest.mark.parametrize("path", [p for p in ALL_PATHS if p != "subject.count"])
    @pytest.mark.parametrize("value", ["x", "a longer phrase", "1", ""])
    def test_str_paths_accept_str_values(self, path: str, value: str) -> None:
        result = validate([set_delta(path, value)], context("msg_1"), full_intent())
        assert len(result.accepted) == 1, result.issues

    @pytest.mark.parametrize("path", [p for p in ALL_PATHS if p != "subject.count"])
    @pytest.mark.parametrize("value", [3, 3.5, True, ["x"], {"a": 1}, b"bytes"])
    def test_str_paths_reject_non_str_values(self, path: str, value: object) -> None:
        result = validate([forged_set(path, value)], context("msg_1"), full_intent())
        assert result.accepted == []
        assert VALUE_TYPE_MISMATCH in rejected_codes(result)
        assert result.rejected[0].delta.path == path

    @pytest.mark.parametrize("value", [1, 2, 999])
    def test_subject_count_accepts_positive_ints(self, value: int) -> None:
        result = validate([set_delta("subject.count", value)], context("msg_1"), full_intent())
        assert len(result.accepted) == 1, result.issues
        assert result.accepted[0].value == value

    @pytest.mark.parametrize("value", [0, -1, -100])
    def test_subject_count_rejects_non_positive_ints(self, value: int) -> None:
        result = validate([set_delta("subject.count", value)], context("msg_1"), full_intent())
        assert result.accepted == []
        assert VALUE_TYPE_MISMATCH in rejected_codes(result)

    @pytest.mark.parametrize("value", [3.0, 2.5, "3", True, False])
    def test_subject_count_rejects_coercible_and_non_int_values(self, value: object) -> None:
        # 严格类型：不把 "3" / 3.0 / True 静默修成 3。
        result = validate([forged_set("subject.count", value)], context("msg_1"), full_intent())
        assert result.accepted == []
        assert VALUE_TYPE_MISMATCH in rejected_codes(result)

    def test_resolution_only_set_has_no_value_to_type_check(self) -> None:
        intent = full_intent()
        d = set_delta("style.primary", resolution=Resolution.USER_DELEGATED)
        ctx = context("msg_1", pending_question_id="qst_1", pending_question_path="style.primary")
        d = d.model_copy(
            update={"evidence_refs": [evidence("msg_1", pending_question_id="qst_1")]}
        )
        result = validate([d], ctx, intent)
        assert len(result.accepted) == 1, result.issues


class TestEvidenceExistence:
    """检查 4：证据存在性与 Pending Question 绑定。"""

    def test_empty_evidence_refs_is_rejected(self) -> None:
        result = validate(
            [delta("SET", "style.primary", value="x", evidence_refs=[])],
            context("msg_1"),
            full_intent(),
        )
        assert result.accepted == []
        assert rejected_codes(result) == {EVIDENCE_MISSING}

    def test_evidence_message_must_be_available(self) -> None:
        result = validate(
            [delta("SET", "style.primary", value="x", message_id="msg_999")],
            context("msg_1", "msg_2"),
            full_intent(),
        )
        assert result.accepted == []
        assert EVIDENCE_MESSAGE_NOT_AVAILABLE in rejected_codes(result)

    def test_one_of_several_evidence_refs_being_unavailable_rejects_the_delta(self) -> None:
        refs = [evidence("msg_1"), evidence("msg_999")]
        result = validate(
            [delta("SET", "style.primary", value="x", evidence_refs=refs)],
            context("msg_1"),
            full_intent(),
        )
        assert result.accepted == []
        assert EVIDENCE_MESSAGE_NOT_AVAILABLE in rejected_codes(result)

    def test_evidence_bound_to_the_current_pending_question_is_accepted(self) -> None:
        ctx = context("msg_9", pending_question_id="qst_7", pending_question_path="style.primary")
        d = delta(
            "SET",
            "style.primary",
            value="cinematic realism",
            message_id="msg_9",
            pending_question_id="qst_7",
        )
        result = validate([d], ctx, full_intent())
        assert len(result.accepted) == 1, result.issues

    def test_stale_pending_question_id_is_rejected(self) -> None:
        ctx = context("msg_9", pending_question_id="qst_7", pending_question_path="style.primary")
        d = delta(
            "SET",
            "style.primary",
            value="x",
            message_id="msg_9",
            pending_question_id="qst_OLD",
        )
        result = validate([d], ctx, full_intent())
        assert result.accepted == []
        assert EVIDENCE_PENDING_QUESTION_MISMATCH in rejected_codes(result)

    def test_pending_question_evidence_without_a_current_question_is_rejected(self) -> None:
        d = delta(
            "SET",
            "style.primary",
            value="x",
            message_id="msg_9",
            pending_question_id="qst_7",
        )
        result = validate([d], context("msg_9"), full_intent())
        assert result.accepted == []
        assert EVIDENCE_PENDING_QUESTION_UNKNOWN in rejected_codes(result)

    def test_answering_a_question_may_not_change_another_path(self) -> None:
        ctx = context("msg_9", pending_question_id="qst_7", pending_question_path="style.primary")
        d = delta(
            "SET",
            "color.palette",
            value="teal",
            message_id="msg_9",
            pending_question_id="qst_7",
        )
        result = validate([d], ctx, full_intent())
        assert result.accepted == []
        assert EVIDENCE_PENDING_QUESTION_PATH_MISMATCH in rejected_codes(result)

    def test_current_question_does_not_freeze_unrelated_paths(self) -> None:
        # 当前有待答问题时，普通消息证据（未绑定问题）仍可修改其他路径：
        # 待答问题只限制"回答它"的证据，不限制同时到达的其他用户指令。
        ctx = context("msg_9", pending_question_id="qst_7", pending_question_path="style.primary")
        d = delta("SET", "color.palette", value="teal", message_id="msg_9")
        result = validate([d], ctx, full_intent())
        assert len(result.accepted) == 1, result.issues

    def test_pending_question_evidence_requires_the_path_to_match(self) -> None:
        # pending_question_path 是问题指向的路径；证据绑定问题但修改别的路径 → 越权。
        ctx = context("msg_9", pending_question_id="qst_7", pending_question_path="style.primary")
        d = delta(
            "SET",
            "lighting.character",
            value="soft",
            message_id="msg_9",
            pending_question_id="qst_7",
        )
        result = validate([d], ctx, full_intent())
        assert EVIDENCE_PENDING_QUESTION_PATH_MISMATCH in rejected_codes(result)

    def test_evidence_ref_without_pending_question_id_is_not_path_bound(self) -> None:
        ctx = context("msg_9", pending_question_id="qst_7", pending_question_path="style.primary")
        d = delta("SET", "lighting.character", value="soft", message_id="msg_9")
        result = validate([d], ctx, full_intent())
        assert len(result.accepted) == 1, result.issues


class TestAuthorizationScope:
    """检查 5：resolution 自报的授权强度必须被证据支持（不越界）。"""

    def test_user_specified_requires_a_concrete_value(self) -> None:
        ctx = context("msg_1", pending_question_id="qst_1", pending_question_path="style.primary")
        d = delta(
            "SET",
            "style.primary",
            resolution=Resolution.USER_SPECIFIED,
            message_id="msg_1",
            pending_question_id="qst_1",
        )
        result = validate([d], ctx, full_intent())
        assert result.accepted == []
        assert UNAUTHORIZED_RESOLUTION_CLAIM in rejected_codes(result)

    def test_user_specified_with_value_is_accepted(self) -> None:
        d = set_delta("style.primary", "cinematic realism", resolution=Resolution.USER_SPECIFIED)
        result = validate([d], context("msg_1"), full_intent())
        assert len(result.accepted) == 1, result.issues

    def test_not_applicable_requires_a_concrete_value(self) -> None:
        ctx = context("msg_1", pending_question_id="qst_1", pending_question_path="style.primary")
        d = delta(
            "SET",
            "style.primary",
            resolution=Resolution.NOT_APPLICABLE,
            message_id="msg_1",
            pending_question_id="qst_1",
        )
        result = validate([d], ctx, full_intent())
        assert result.accepted == []
        assert UNAUTHORIZED_RESOLUTION_CLAIM in rejected_codes(result)

    def test_user_confirmed_proposal_requires_pending_question_evidence(self) -> None:
        d = set_delta(
            "style.primary", "cinematic realism", resolution=Resolution.USER_CONFIRMED_PROPOSAL
        )
        result = validate([d], context("msg_1"), full_intent())
        assert result.accepted == []
        assert UNAUTHORIZED_RESOLUTION_CLAIM in rejected_codes(result)

    def test_user_confirmed_proposal_with_question_evidence_is_accepted(self) -> None:
        ctx = context("msg_1", pending_question_id="qst_1", pending_question_path="style.primary")
        d = delta(
            "SET",
            "style.primary",
            value="cinematic realism",
            resolution=Resolution.USER_CONFIRMED_PROPOSAL,
            message_id="msg_1",
            pending_question_id="qst_1",
        )
        result = validate([d], ctx, full_intent())
        assert len(result.accepted) == 1, result.issues

    def test_user_delegated_requires_question_scoped_evidence(self) -> None:
        d = set_delta("style.primary", resolution=Resolution.USER_DELEGATED)
        result = validate([d], context("msg_1"), full_intent())
        assert result.accepted == []
        assert UNAUTHORIZED_RESOLUTION_CLAIM in rejected_codes(result)

    def test_user_delegated_with_question_evidence_is_accepted(self) -> None:
        ctx = context("msg_1", pending_question_id="qst_1", pending_question_path="style.primary")
        d = delta(
            "SET",
            "style.primary",
            resolution=Resolution.USER_DELEGATED,
            message_id="msg_1",
            pending_question_id="qst_1",
        )
        result = validate([d], ctx, full_intent())
        assert len(result.accepted) == 1, result.issues

    @pytest.mark.parametrize(
        "resolution", [Resolution.USER_SPECIFIED, Resolution.USER_CONFIRMED_PROPOSAL]
    )
    def test_set_with_value_and_resolution_is_accepted_when_evidence_supports_it(
        self, resolution: Resolution
    ) -> None:
        ctx = context("msg_1", pending_question_id="qst_1", pending_question_path="color.palette")
        d = delta(
            "SET",
            "color.palette",
            value="muted teal",
            resolution=resolution,
            message_id="msg_1",
            pending_question_id="qst_1",
        )
        result = validate([d], ctx, full_intent())
        assert len(result.accepted) == 1, result.issues

    def test_set_value_upgrades_a_delegated_path_to_user_specified(self) -> None:
        intent = with_resolution(full_intent(), "style.primary", Resolution.USER_DELEGATED)
        d = set_delta("style.primary", "cinematic realism")
        result = validate([d], context("msg_1"), intent)
        assert len(result.accepted) == 1, result.issues

    def test_redundant_delegation_is_rejected_as_no_op(self) -> None:
        intent = with_resolution(full_intent(), "style.primary", Resolution.USER_DELEGATED)
        ctx = context("msg_1", pending_question_id="qst_1", pending_question_path="style.primary")
        d = delta(
            "SET",
            "style.primary",
            resolution=Resolution.USER_DELEGATED,
            message_id="msg_1",
            pending_question_id="qst_1",
        )
        result = validate([d], ctx, intent)
        assert result.accepted == []
        assert NO_STATE_EFFECT in rejected_codes(result)

    def test_delegation_change_on_a_delegated_path_is_accepted(self) -> None:
        intent = with_resolution(full_intent(), "style.primary", Resolution.USER_DELEGATED)
        ctx = context("msg_1", pending_question_id="qst_1", pending_question_path="style.primary")
        d = delta(
            "SET",
            "style.primary",
            resolution=Resolution.NOT_APPLICABLE,
            value="not applicable for this brief",
            message_id="msg_1",
            pending_question_id="qst_1",
        )
        result = validate([d], ctx, intent)
        assert len(result.accepted) == 1, result.issues

    def test_setting_the_same_value_again_is_not_treated_as_no_effect(self) -> None:
        # 值型 SET 总是显式写入，Validator 不判断"值是否相同"。
        intent = full_intent()
        d = set_delta("style.primary", TYPICAL_VALUES["style.primary"])
        result = validate([d], context("msg_1"), intent)
        assert len(result.accepted) == 1, result.issues


class TestSystemFieldProtection:
    """检查 6：系统字段保护（SYSTEM_FIELD_NAMES）。"""

    @pytest.mark.parametrize("field_name", sorted(SYSTEM_FIELD_NAMES))
    def test_every_system_field_name_is_rejected(self, field_name: str) -> None:
        result = validate(
            [set_delta(field_name, value="x")], context("msg_1"), full_intent()
        )
        assert result.accepted == []
        assert SYSTEM_FIELD_PROTECTED in rejected_codes(result)

    @pytest.mark.parametrize(
        "path",
        [
            "workflow_state.pending",
            "created_at.value",
            "parent_revision_id.0",
            "summary_hash.inner",
        ],
    )
    def test_paths_rooted_at_a_system_field_are_rejected(self, path: str) -> None:
        result = validate([clear_delta(path)], context("msg_1"), full_intent())
        assert result.accepted == []
        assert SYSTEM_FIELD_PROTECTED in rejected_codes(result)

    @pytest.mark.parametrize("field_name", sorted(SYSTEM_FIELD_NAMES))
    def test_system_field_protection_also_reports_the_whitelist_violation(
        self, field_name: str
    ) -> None:
        # 系统字段名同样不在白名单内；两个检查独立运行，都产生可观察 issue。
        result = validate([set_delta(field_name, value="x")], context("msg_1"), full_intent())
        assert {SYSTEM_FIELD_PROTECTED, PATH_NOT_WHITELISTED} <= rejected_codes(result)
        assert result.rejected[0].delta.path == field_name


class TestPinUnpinTargets:
    """检查 7：PIN / UNPIN 是否针对可保持的 Intent 路径。"""

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_pin_requires_a_current_value(self, path: str) -> None:
        result = validate([pin_delta(path)], context("msg_1"), VisualIntent())
        assert result.accepted == []
        assert PIN_REQUIRES_EXISTING_VALUE in rejected_codes(result)

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_pin_is_accepted_for_a_path_with_a_value(self, path: str) -> None:
        intent = with_resolution(intent_with(path, TYPICAL_VALUES[path]), path, Resolution.USER_DELEGATED)
        result = validate([pin_delta(path)], context("msg_1"), intent)
        assert len(result.accepted) == 1, (path, result.issues)

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_unpin_requires_the_path_to_be_pinned(self, path: str) -> None:
        intent = full_intent()
        result = validate([unpin_delta(path)], context("msg_1"), intent)
        assert result.accepted == []
        assert UNPIN_REQUIRES_PINNED_PATH in rejected_codes(result)

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_unpin_is_accepted_for_a_pinned_path(self, path: str) -> None:
        intent = with_pinned(full_intent(), path)
        result = validate([unpin_delta(path)], context("msg_1"), intent)
        assert len(result.accepted) == 1, (path, result.issues)

    def test_pinning_an_already_pinned_path_is_idempotent_and_accepted(self) -> None:
        intent = with_pinned(full_intent(), "color.palette")
        result = validate([pin_delta("color.palette")], context("msg_1"), intent)
        assert len(result.accepted) == 1, result.issues

    def test_pin_cannot_be_used_to_create_a_value(self) -> None:
        # PIN 只加保持标记；没有值可保持时必须拒绝，不能隐式创建值。
        result = validate([pin_delta("style.primary")], context("msg_1"), VisualIntent())
        assert result.accepted == []
        assert PIN_REQUIRES_EXISTING_VALUE in rejected_codes(result)

    def test_pin_and_unpin_require_evidence(self) -> None:
        intent = with_pinned(full_intent(), "style.primary")
        for d in (
            delta("PIN", "style.primary", evidence_refs=[]),
            delta("UNPIN", "style.primary", evidence_refs=[]),
        ):
            result = validate([d], context("msg_1"), intent)
            assert result.accepted == []
            assert EVIDENCE_MISSING in rejected_codes(result)

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_clear_of_a_pinned_path_is_rejected(self, path: str) -> None:
        intent = with_pinned(full_intent(), path)
        result = validate([clear_delta(path)], context("msg_1"), intent)
        assert result.accepted == []
        assert CLEAR_REQUIRES_UNPINNED_PATH in rejected_codes(result)

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_clear_of_an_unpinned_path_is_accepted(self, path: str) -> None:
        result = validate([clear_delta(path)], context("msg_1"), full_intent())
        assert len(result.accepted) == 1, result.issues


class TestNoEffect:
    """额外检查：无效果 Delta 显式拒绝（不静默接受 no-op）。"""

    def test_clear_without_value_or_resolution_is_rejected(self) -> None:
        result = validate([clear_delta("color.palette")], context("msg_1"), VisualIntent())
        assert result.accepted == []
        assert rejected_codes(result) == {NO_STATE_EFFECT}

    def test_clear_of_a_delegated_path_is_accepted(self) -> None:
        intent = with_resolution(VisualIntent(), "style.primary", Resolution.USER_DELEGATED)
        result = validate([clear_delta("style.primary")], context("msg_1"), intent)
        assert len(result.accepted) == 1, result.issues

    def test_set_same_value_again_is_accepted(self) -> None:
        intent = full_intent()
        result = validate(
            [set_delta("style.primary", TYPICAL_VALUES["style.primary"])],
            context("msg_1"),
            intent,
        )
        assert len(result.accepted) == 1, result.issues


class TestValidatorContract:
    """Validator 的输出契约与纯函数性质。"""

    def test_accepted_deltas_keep_input_order(self) -> None:
        deltas = [
            set_delta("style.primary", "a"),
            set_delta("color.palette", "b"),
            set_delta("camera.angle", "c"),
        ]
        result = validate(deltas, context("msg_1"), full_intent())
        assert result.accepted == deltas

    def test_rejected_and_accepted_are_disjoint_and_explicit(self) -> None:
        good = set_delta("style.primary", "a")
        bad = set_delta("style.primry", "b")
        result = validate([good, bad], context("msg_1"), full_intent())
        assert result.accepted == [good]
        assert len(result.rejected) == 1
        assert result.rejected[0].delta == bad
        assert result.issues == list(result.rejected[0].issues)

    def test_every_rejected_delta_carries_at_least_one_error_issue(self) -> None:
        intent = full_intent()
        candidates = [
            set_delta("style.primry", "a"),  # 非白名单路径
            delta("SET", "style.primary", value="a", evidence_refs=[]),  # 无证据
            set_delta("style.primary", 42),  # 类型不符
            clear_delta("color.palette"),  # 合法：该路径当前有值
            unpin_delta("camera.angle"),  # 未 pinned
            set_delta("workflow_state", "x"),  # 系统字段
        ]
        result = validate(candidates, context("msg_1"), intent)
        assert len(result.accepted) == 1
        assert result.accepted[0] == candidates[3]
        for item in result.rejected:
            assert item.issues
            assert all(issue.severity.value == "error" for issue in item.issues)
            assert all(issue.code.startswith("validation.") for issue in item.issues)

    def test_every_issue_is_an_error_severity_issue_from_the_namespace(self) -> None:
        result = validate(
            [
                set_delta("nope.path", "x"),
                delta("SET", "style.primary", value="x", evidence_refs=[]),
                set_delta("workflow_state", "x"),
            ],
            context("msg_1"),
            full_intent(),
        )
        assert result.issues
        for issue in result.issues:
            assert issue.severity.value == "error"
            assert issue.code in VALIDATION_ISSUE_CODES
            assert issue.path is not None

    def test_validator_does_not_mutate_its_inputs(self) -> None:
        intent = full_intent()
        snapshot = path_snapshot(intent)
        resolutions_before = dict(intent.resolutions)
        pinned_before = intent.pinned_paths
        deltas = [
            set_delta("style.primary", "new"),
            clear_delta("color.palette"),
            pin_delta("camera.angle"),
            unpin_delta("lighting.character"),
        ]
        deltas_before = list(deltas)
        ctx = context("msg_1")
        validate(deltas, ctx, intent)
        assert path_snapshot(intent) == snapshot
        assert dict(intent.resolutions) == resolutions_before
        assert intent.pinned_paths == pinned_before
        assert deltas == deltas_before

    def test_empty_candidate_list_yields_an_empty_result(self) -> None:
        result = validate([], context("msg_1"), full_intent())
        assert result.accepted == []
        assert result.rejected == []
        assert result.issues == []

    def test_validate_is_deterministic(self) -> None:
        deltas = [set_delta("style.primary", "a"), set_delta("bad.path", "b")]
        ctx = context("msg_1")
        intent = full_intent()
        first = validate(deltas, ctx, intent)
        second = validate(deltas, ctx, intent)
        assert first.model_dump() == second.model_dump()
