"""Step 06 单元测试：QuestionBuilder / PendingQuestion（确定性模板 + LLM 仅措辞）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from visual_intent_agent.domain import utc_now
from visual_intent_agent.policy import QuestionSpec
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.workflow import (
    PendingQuestion,
    QuestionBuilder,
    render_question_text,
)
from visual_intent_agent.workflow.questions import MAX_SUGGESTED_OPTIONS

from workflow_helpers import make_provider

SPEC = QuestionSpec(
    target_path="style.primary",
    reason="blocking decision 'style.primary' (core) is unresolved",
    allow_delegate=True,
    allow_custom=True,
    suggested_values=("photorealistic", "cinematic", "illustration"),
)


def _pending(builder: QuestionBuilder) -> PendingQuestion:
    return builder.build(SPEC, "ses_test_1")


# ---------------------------------------------------------------------------
# 确定性模板
# ---------------------------------------------------------------------------


def test_default_template_is_deterministic_and_mentions_all_required_parts():
    question = QuestionBuilder().build(SPEC, "ses_test_1")

    assert question.question_text == render_question_text(SPEC)
    assert "style.primary" in question.question_text
    for option in SPEC.suggested_values:
        assert option in question.question_text
    assert "your own words" in question.question_text  # 自定义输入入口
    assert "you decide" in question.question_text  # 交给系统决定
    assert question.question_text.strip()


def test_template_limits_options_to_three():
    spec = QuestionSpec(
        target_path="color.palette",
        reason="r",
        allow_delegate=False,
        suggested_values=("warm", "cool", "monochrome", "sepia", "neon"),
    )
    question = QuestionBuilder().build(spec, "ses_test_1")

    assert "warm" in question.question_text
    assert "cool" in question.question_text
    assert "monochrome" in question.question_text
    assert "sepia" not in question.question_text
    assert "neon" not in question.question_text
    assert MAX_SUGGESTED_OPTIONS == 3


def test_template_without_options_still_offers_custom_and_delegate_entry():
    spec = QuestionSpec(
        target_path="environment.location",
        reason="r",
        allow_delegate=True,
        suggested_values=(),
    )
    text = render_question_text(spec)

    assert "environment.location" in text
    assert "your own words" in text
    assert "you decide" in text
    assert "Suggested options" not in text


def test_template_omits_delegate_and_custom_phrases_when_not_allowed():
    spec = QuestionSpec(
        target_path="subject.description",
        reason="r",
        allow_delegate=False,
        allow_custom=False,
        suggested_values=(),
    )
    text = render_question_text(spec)

    assert "you decide" not in text
    assert "your own words" not in text


# ---------------------------------------------------------------------------
# 结构化字段原样来自 QuestionSpec
# ---------------------------------------------------------------------------


def test_structured_fields_come_verbatim_from_the_spec():
    question = _pending(QuestionBuilder())

    assert question.question_id.startswith("qst_")
    assert question.session_id == "ses_test_1"
    assert question.target_path == SPEC.target_path
    assert question.reason == SPEC.reason
    assert question.allow_delegate is True
    assert question.allow_custom is True
    assert question.suggested_values == SPEC.suggested_values


def test_to_spec_round_trips_with_a_non_empty_question_id():
    question = _pending(QuestionBuilder())
    spec = question.to_spec()

    assert spec.question_id == question.question_id
    assert spec.question_id  # Step 05 前置条件：非空 question_id
    assert spec.question_text == question.question_text
    assert spec.target_path == question.target_path
    assert spec.allow_delegate == question.allow_delegate
    assert spec.allow_custom == question.allow_custom
    assert spec.suggested_values == question.suggested_values
    assert spec.reason == question.reason


def test_llm_may_only_change_the_wording_never_the_scope():
    """LLM 试图改目标路径/选项/替用户作答，结构化字段仍然逐值来自 QuestionSpec。"""
    llm = make_provider(
        ["Should the subject be a dragon? Options: dragon, robot. Use lighting: neon."]
    )
    question = QuestionBuilder(llm).build(SPEC, "ses_test_1")

    assert question.question_text.startswith("Should the subject be a dragon?")
    # 范围字段未被改写：
    assert question.target_path == "style.primary"
    assert question.suggested_values == SPEC.suggested_values
    assert question.allow_delegate is True
    assert question.allow_custom is True
    # 只有一次 LLM 调用，且提示词包含目标字段与选项边界：
    assert len(llm.requests) == 1
    prompt = llm.requests[0].messages[-1].content
    assert "style.primary" in prompt
    assert "photorealistic" in prompt


def test_llm_failure_falls_back_to_the_deterministic_template():
    llm = FakeLLMProvider([])  # 队列耗尽 → ProviderError.invalid_request
    question = QuestionBuilder(llm).build(SPEC, "ses_test_1")

    assert question.question_text == render_question_text(SPEC)
    assert question.target_path == SPEC.target_path


def test_blank_llm_output_falls_back_to_the_deterministic_template():
    question = QuestionBuilder(make_provider(["   \n"])).build(SPEC, "ses_test_1")

    assert question.question_text == render_question_text(SPEC)


def test_builder_without_llm_never_calls_a_provider():
    builder = QuestionBuilder(None)
    question = builder.build(SPEC, "ses_test_1")
    assert question.question_text == render_question_text(SPEC)


# ---------------------------------------------------------------------------
# 模型约束
# ---------------------------------------------------------------------------


def test_pending_question_is_frozen_and_forbids_extra_fields():
    question = _pending(QuestionBuilder())

    with pytest.raises(ValidationError):
        question.question_text = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PendingQuestion(
            question_id="qst_1",
            session_id="ses_1",
            target_path="style.primary",
            reason="r",
            allow_delegate=True,
            allow_custom=True,
            suggested_values=(),
            question_text="t",
            unexpected=True,  # type: ignore[call-arg]
        )


def test_pending_question_json_round_trip_is_stable():
    question = _pending(QuestionBuilder())
    payload = question.model_dump_json()
    restored = PendingQuestion.model_validate_json(payload)

    assert restored == question
    assert restored.model_dump_json() == payload


def test_pending_question_rejects_naive_timestamp_and_requires_text():
    with pytest.raises(ValidationError):
        PendingQuestion(
            question_id="qst_1",
            session_id="ses_1",
            target_path="style.primary",
            reason="r",
            allow_delegate=True,
            allow_custom=True,
            suggested_values=(),
            question_text="t",
            created_at=utc_now().replace(tzinfo=None),
        )
    with pytest.raises(ValidationError):
        PendingQuestion(
            question_id="qst_1",
            session_id="ses_1",
            target_path="style.primary",
            reason="r",
            allow_delegate=True,
            allow_custom=True,
            suggested_values=(),
        )


def test_provider_error_type_is_the_only_swallowed_failure():
    """确认回退逻辑只吞 ProviderError（措辞失败），不吞其它异常。"""
    class Boom(FakeLLMProvider):
        def complete(self, request):  # type: ignore[override]
            raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError):
        QuestionBuilder(Boom([])).build(SPEC, "ses_test_1")
    assert issubclass(ProviderError, Exception)
