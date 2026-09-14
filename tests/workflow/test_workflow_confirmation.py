"""Step 06 单元测试：ConfirmationSummary / compute_summary_hash / WorkflowError。"""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from visual_intent_agent.domain import (
    DeltaOperation,
    ExecutionRevision,
    IntentDelta,
    IntentRevision,
    Resolution,
    ResolutionRecord,
    SubjectFacet,
    StyleFacet,
    VisualIntent,
)
from visual_intent_agent.validation import ChangeSummary
from visual_intent_agent.workflow import (
    WORKFLOW_ERROR_CODES,
    WORKFLOW_INVALID_STATE,
    WORKFLOW_STALE_REVISION,
    WORKFLOW_SUMMARY_HASH_MISMATCH,
    ConfirmationSummary,
    WorkflowError,
    build_confirmation_summary,
    compute_summary_hash,
    derive_change_summary,
)


def _intent() -> VisualIntent:
    return VisualIntent(
        subject=SubjectFacet(description="a cat"),
        style=StyleFacet(primary="photorealistic"),
        resolutions={
            "style.primary": ResolutionRecord(
                resolution=Resolution.USER_DELEGATED, reason="user delegated style"
            ),
            "subject.description": ResolutionRecord(
                resolution=Resolution.USER_SPECIFIED
            ),
        },
        pinned_paths=frozenset({"subject.description", "environment.mode"}),
    )


def _deltas() -> list[IntentDelta]:
    return [
        IntentDelta(
            operation=DeltaOperation.SET,
            path="subject.description",
            value="a cat",
        ),
        IntentDelta(operation=DeltaOperation.PIN, path="subject.description"),
        IntentDelta(operation=DeltaOperation.PIN, path="subject.description"),
        IntentDelta(operation=DeltaOperation.SET, path="style.primary", value="photorealistic"),
        IntentDelta(operation=DeltaOperation.CLEAR, path="color.palette"),
        IntentDelta(operation=DeltaOperation.UNPIN, path="color.palette"),
    ]


def _revisions(
    *, output_size: str = "1024x1024", target_model: str = "qwen-image-3.0"
) -> tuple[IntentRevision, ExecutionRevision]:
    intent_revision = IntentRevision(
        intent_revision_id="irev_1",
        session_id="ses_1",
        parent_revision_id=None,
        intent=_intent(),
        applied_deltas=_deltas(),
    )
    execution_revision = ExecutionRevision(
        execution_revision_id="erev_1",
        session_id="ses_1",
        parent_revision_id=None,
        target_model=target_model,
        output_size=output_size,
    )
    return intent_revision, execution_revision


# ---------------------------------------------------------------------------
# diff-first 六要素
# ---------------------------------------------------------------------------


def test_summary_carries_all_six_diff_first_elements():
    intent_revision, execution_revision = _revisions()
    summary = build_confirmation_summary(intent_revision, execution_revision)

    # 1 本轮修改内容 2 明确保留 3 委托 4 目标模型 5 输出比例 6 完整 Intent
    assert summary.change_summary == derive_change_summary(_deltas())
    assert summary.pinned_paths == ["environment.mode", "subject.description"]
    assert summary.delegated_paths == ["style.primary"]
    assert summary.target_model == "qwen-image-3.0"
    assert summary.output_size == "1024x1024"
    assert summary.intent == intent_revision.intent


def test_change_summary_derivation_matches_reducer_semantics():
    summary = derive_change_summary(_deltas())

    assert summary.changed_paths == ["subject.description", "style.primary"]
    assert summary.cleared_paths == ["color.palette"]
    assert summary.pinned_paths == ["subject.description"]
    assert summary.unpinned_paths == ["color.palette"]
    assert summary.confirmation_invalidated is True


def test_change_summary_of_empty_deltas_is_neutral():
    summary = derive_change_summary([])

    assert summary.changed_paths == []
    assert summary.cleared_paths == []
    assert summary.confirmation_invalidated is False


def test_pinned_and_delegated_paths_are_sorted_for_hash_stability():
    intent_revision, execution_revision = _revisions()
    summary = build_confirmation_summary(intent_revision, execution_revision)

    assert summary.pinned_paths == sorted(summary.pinned_paths)
    assert summary.delegated_paths == sorted(summary.delegated_paths)


def test_explicit_change_summary_is_kept():
    intent_revision, execution_revision = _revisions()
    explicit = ChangeSummary(changed_paths=["style.primary"], confirmation_invalidated=True)
    summary = build_confirmation_summary(intent_revision, execution_revision, explicit)
    assert summary.change_summary is explicit
    assert summary.change_summary is not None
    assert summary.change_summary.changed_paths == ["style.primary"]


# ---------------------------------------------------------------------------
# 冻结哈希算法
# ---------------------------------------------------------------------------


def test_compute_summary_hash_matches_the_frozen_algorithm_verbatim():
    intent_revision, execution_revision = _revisions()
    summary = build_confirmation_summary(intent_revision, execution_revision)

    expected = hashlib.sha256(
        json.dumps(
            summary.model_dump(mode="json"), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    assert compute_summary_hash(summary) == expected
    assert len(compute_summary_hash(summary)) == 64


def test_compute_summary_hash_is_reproducible_for_the_same_snapshot():
    intent_revision, execution_revision = _revisions()
    first = build_confirmation_summary(intent_revision, execution_revision)
    second = build_confirmation_summary(intent_revision, execution_revision)

    assert compute_summary_hash(first) == compute_summary_hash(second)


def test_compute_summary_hash_changes_with_intent_output_ratio_and_model():
    intent_revision, execution_revision = _revisions()
    base = build_confirmation_summary(intent_revision, execution_revision)
    base_hash = compute_summary_hash(base)

    other_size = build_confirmation_summary(
        intent_revision, _revisions(output_size="1024x1536")[1]
    )
    other_model = build_confirmation_summary(
        intent_revision, _revisions(target_model="other-model")[1]
    )
    other_intent = build_confirmation_summary(
        intent_revision.model_copy(
            update={"intent": _intent().model_copy(update={"subject": SubjectFacet(description="a dog")})}
        ),
        execution_revision,
    )

    assert compute_summary_hash(other_size) != base_hash
    assert compute_summary_hash(other_model) != base_hash
    assert compute_summary_hash(other_intent) != base_hash


def test_hash_is_independent_of_revision_ids():
    """hash 绑定的是内容，不是 ID；绑定 ID 的职责在 ConfirmationRecord。"""
    intent_revision, execution_revision = _revisions()
    renamed = intent_revision.model_copy(update={"intent_revision_id": "irev_other"})
    summary = build_confirmation_summary(renamed, execution_revision)
    assert compute_summary_hash(summary) == compute_summary_hash(
        build_confirmation_summary(intent_revision, execution_revision)
    )


# ---------------------------------------------------------------------------
# 模型约束与错误类型
# ---------------------------------------------------------------------------


def test_confirmation_summary_is_frozen_and_forbids_extra_fields():
    summary = build_confirmation_summary(*_revisions())

    with pytest.raises(ValidationError):
        summary.output_size = "1x1"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ConfirmationSummary(
            intent=_intent(),
            target_model="m",
            output_size="1024x1024",
            extra_field=True,  # type: ignore[call-arg]
        )


def test_workflow_error_exposes_exactly_three_frozen_codes():
    assert WORKFLOW_ERROR_CODES == {
        "workflow.invalid_state",
        "workflow.stale_revision",
        "workflow.summary_hash_mismatch",
    }
    assert WORKFLOW_INVALID_STATE in WORKFLOW_ERROR_CODES
    assert WORKFLOW_STALE_REVISION in WORKFLOW_ERROR_CODES
    assert WORKFLOW_SUMMARY_HASH_MISMATCH in WORKFLOW_ERROR_CODES


@pytest.mark.parametrize(
    "code",
    [WORKFLOW_INVALID_STATE, WORKFLOW_STALE_REVISION, WORKFLOW_SUMMARY_HASH_MISMATCH],
)
def test_workflow_error_carries_code_and_message(code: str):
    error = WorkflowError(code, "boom")
    assert error.code == code
    assert code in str(error)
    assert "boom" in str(error)


def test_workflow_error_rejects_unknown_codes():
    with pytest.raises(ValueError):
        WorkflowError("workflow.something_else", "boom")
