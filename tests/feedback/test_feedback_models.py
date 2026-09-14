"""Step 09 反馈模型测试：冻结字段面、不可变、时间戳、code 集合与语义辅助函数。

`feedback/` 是**单 Step 拥有**的包，测试直接从 `visual_intent_agent.feedback` 导入。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from visual_intent_agent.domain import (
    DeltaOperation,
    EvidenceRef,
    IntentDelta,
    Issue,
    Severity,
    VisualIntent,
)
from visual_intent_agent.feedback import (
    FEEDBACK_CLARIFICATION_REQUIRED,
    FEEDBACK_CLARIFICATION_TARGET_MISSING,
    FEEDBACK_CONTEXT_MISMATCH,
    FEEDBACK_EMPTY_OUTPUT,
    FEEDBACK_ERROR_CODES,
    FEEDBACK_FULL_INTENT,
    FEEDBACK_ID_PREFIX,
    FEEDBACK_INCONSISTENT_DECISION,
    FEEDBACK_INVALID_CLARIFY_PATH,
    FEEDBACK_INVALID_DELTA,
    FEEDBACK_INVALID_JSON,
    FEEDBACK_SCHEMA_VIOLATION,
    FEEDBACK_UNKNOWN_PRESERVE_PATH,
    FEEDBACK_UNPARSEABLE_OUTPUT,
    PARSE_FAILURE_CODES,
    FeedbackDecision,
    FeedbackError,
    FeedbackParseError,
    FeedbackRequest,
    FeedbackResult,
    clarify_target_path,
    is_unparseable,
)
from visual_intent_agent.generation import GenerationArtifact
from visual_intent_agent.prompt_engine import PromptArtifact, PromptParameters


def _prompt_artifact(**overrides) -> PromptArtifact:
    fields = {
        "prompt_artifact_id": "pra_1",
        "session_id": "ses_1",
        "based_on_intent_revision_id": "irev_1",
        "based_on_confirmation_id": "cnf_1",
        "target_model": "qwen-image-3.0",
        "prompt": "subject: a cat",
        "parameters": PromptParameters(size="1024x1024"),
        "source_bindings": [],
        "realization_refs": [],
    }
    fields.update(overrides)
    return PromptArtifact(**fields)


def _generation(**overrides) -> GenerationArtifact:
    fields = {
        "generation_id": "gen_1",
        "session_id": "ses_1",
        "prompt_artifact_id": "pra_1",
        "target_model": "qwen-image-3.0",
        "model_version": "qwen-image-3.0",
        "parameters": PromptParameters(size="1024x1024"),
        "seed": None,
        "output_refs": [],
        "provider_request_id": None,
    }
    fields.update(overrides)
    return GenerationArtifact(**fields)


def _request(**overrides) -> FeedbackRequest:
    fields = {
        "session_id": "ses_1",
        "message_id": "msg_1",
        "feedback_text": "pull the camera back",
        "generation": _generation(),
        "prompt_artifact": _prompt_artifact(),
        "current_intent": VisualIntent(),
        "realization_state": None,
    }
    fields.update(overrides)
    return FeedbackRequest(**fields)


def _result(**overrides) -> FeedbackResult:
    fields = {
        "feedback_id": "fbk_1",
        "session_id": "ses_1",
        "generation_id": "gen_1",
        "intent_revision_id": "irev_1",
        "decision": FeedbackDecision.REVISE,
    }
    fields.update(overrides)
    return FeedbackResult(**fields)


# ---------------------------------------------------------------------------
# 形状
# ---------------------------------------------------------------------------


def test_feedback_decision_has_exactly_three_frozen_values():
    assert [decision.value for decision in FeedbackDecision] == [
        "accept",
        "revise",
        "clarify",
    ]


def test_feedback_request_has_exactly_the_seven_frozen_fields():
    assert list(FeedbackRequest.model_fields) == [
        "session_id",
        "message_id",
        "feedback_text",
        "generation",
        "prompt_artifact",
        "current_intent",
        "realization_state",
    ]


def test_feedback_result_has_exactly_the_twelve_frozen_fields_in_order():
    assert list(FeedbackResult.model_fields) == [
        "schema_version",
        "feedback_id",
        "session_id",
        "generation_id",
        "intent_revision_id",
        "decision",
        "candidate_deltas",
        "preserve_paths",
        "compile_feedback",
        "issues",
        "evidence_refs",
        "created_at",
    ]


def test_result_defaults_are_frozen_values():
    result = _result()
    assert result.schema_version == "v1"
    assert result.candidate_deltas == []
    assert result.preserve_paths == []
    assert result.compile_feedback is None
    assert result.issues == []
    assert result.evidence_refs == []
    assert result.created_at.tzinfo is not None


def test_default_lists_are_not_shared_between_instances():
    first = _result()
    second = _result()
    assert first.candidate_deltas is not second.candidate_deltas
    assert first.issues is not second.issues
    assert first.preserve_paths is not second.preserve_paths


def test_models_are_frozen_and_reject_unknown_fields():
    result = _result()
    with pytest.raises(ValidationError):
        result.decision = FeedbackDecision.ACCEPT  # type: ignore[misc]
    with pytest.raises(ValidationError):
        FeedbackResult(**{**_result().model_dump(), "unexpected": 1})
    with pytest.raises(ValidationError):
        FeedbackRequest(**{**_request().model_dump(), "unexpected": 1})


def test_created_at_rejects_naive_datetimes_and_normalizes_to_utc():
    with pytest.raises(ValidationError):
        _result(created_at=datetime(2026, 1, 1, 12, 0, 0))
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    result = _result(created_at=aware)
    assert result.created_at == aware
    assert result.created_at.utcoffset() == timedelta(0)
    assert result.model_dump(mode="json")["created_at"].endswith("+00:00")


def test_result_round_trips_through_json():
    payload = _result(
        candidate_deltas=[
            IntentDelta(
                operation=DeltaOperation.SET,
                path="composition.framing",
                value="wide_shot",
                evidence_refs=[EvidenceRef(message_id="msg_1", fragment="pull back")],
            )
        ],
        preserve_paths=["subject.*"],
        compile_feedback="only framing",
        evidence_refs=[EvidenceRef(message_id="msg_1")],
    )
    restored = FeedbackResult.model_validate_json(payload.model_dump_json())
    assert restored == payload


def test_request_round_trips_through_json():
    request = _request()
    restored = FeedbackRequest.model_validate_json(request.model_dump_json())
    assert restored == request


# ---------------------------------------------------------------------------
# code 命名空间
# ---------------------------------------------------------------------------


def test_every_feedback_code_is_in_the_feedback_namespace():
    codes = {
        FEEDBACK_UNPARSEABLE_OUTPUT,
        FEEDBACK_EMPTY_OUTPUT,
        FEEDBACK_INVALID_JSON,
        FEEDBACK_SCHEMA_VIOLATION,
        FEEDBACK_FULL_INTENT,
        FEEDBACK_INVALID_DELTA,
        FEEDBACK_INCONSISTENT_DECISION,
        FEEDBACK_INVALID_CLARIFY_PATH,
        FEEDBACK_CLARIFICATION_REQUIRED,
        FEEDBACK_CLARIFICATION_TARGET_MISSING,
        FEEDBACK_UNKNOWN_PRESERVE_PATH,
        FEEDBACK_CONTEXT_MISMATCH,
    }
    assert all(code.startswith("feedback.") for code in codes)
    assert FEEDBACK_ID_PREFIX == "fbk"


def test_parse_failure_codes_are_exactly_the_parse_constants():
    assert PARSE_FAILURE_CODES == {
        FEEDBACK_EMPTY_OUTPUT,
        FEEDBACK_INVALID_JSON,
        FEEDBACK_SCHEMA_VIOLATION,
        FEEDBACK_FULL_INTENT,
        FEEDBACK_INVALID_DELTA,
        FEEDBACK_INCONSISTENT_DECISION,
        FEEDBACK_INVALID_CLARIFY_PATH,
    }
    assert all(code.startswith(f"{FEEDBACK_UNPARSEABLE_OUTPUT}.") for code in PARSE_FAILURE_CODES)
    assert FEEDBACK_ERROR_CODES == {FEEDBACK_CONTEXT_MISMATCH}


def test_parse_error_only_accepts_frozen_parse_codes():
    error = FeedbackParseError(FEEDBACK_INVALID_JSON, "bad json")
    assert error.code == FEEDBACK_INVALID_JSON
    assert error.retryable is True
    with pytest.raises(ValueError):
        FeedbackParseError(FEEDBACK_CONTEXT_MISMATCH, "wrong namespace for a parse error")


def test_base_error_carries_code_and_message():
    error = FeedbackError(FEEDBACK_CONTEXT_MISMATCH, "mismatch")
    assert error.code == FEEDBACK_CONTEXT_MISMATCH
    assert error.retryable is False
    assert str(error) == f"{FEEDBACK_CONTEXT_MISMATCH}: mismatch"


# ---------------------------------------------------------------------------
# 语义辅助
# ---------------------------------------------------------------------------


def test_clarify_target_path_requires_a_whitelisted_path_carrier():
    assert clarify_target_path(_result()) is None
    assert (
        clarify_target_path(
            _result(
                decision=FeedbackDecision.CLARIFY,
                issues=[
                    Issue(
                        code=FEEDBACK_CLARIFICATION_TARGET_MISSING,
                        message="no target",
                        severity=Severity.ERROR,
                    )
                ],
            )
        )
        is None
    )
    assert (
        clarify_target_path(
            _result(
                decision=FeedbackDecision.CLARIFY,
                issues=[
                    Issue(
                        code=FEEDBACK_CLARIFICATION_REQUIRED,
                        message="ask",
                        path="environment.mode",
                        severity=Severity.WARNING,
                    )
                ],
            )
        )
        == "environment.mode"
    )
    # 非白名单路径绝不作为可执行目标（不修正、不猜测）。
    assert (
        clarify_target_path(
            _result(
                decision=FeedbackDecision.CLARIFY,
                issues=[
                    Issue(
                        code=FEEDBACK_CLARIFICATION_REQUIRED,
                        message="ask",
                        path="subject.clothing",
                        severity=Severity.WARNING,
                    )
                ],
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "code",
    sorted(PARSE_FAILURE_CODES),
)
def test_is_unparseable_recognizes_every_parse_code(code):
    result = _result(
        decision=FeedbackDecision.CLARIFY,
        issues=[Issue(code=code, message="failed")],
    )
    assert is_unparseable(result) is True


def test_is_unparseable_is_false_for_clean_results():
    assert is_unparseable(_result()) is False
    assert is_unparseable(_result(issues=[Issue(code="provider.timeout", message="slow")])) is False
