"""Interpreter 解析与错误分类测试（交付物「结构化响应解析和错误分类」）。"""

from __future__ import annotations

import pytest

import ie_helpers as h

from visual_intent_agent.domain import VisualIntent
from visual_intent_agent.intent_engine import Interpreter
from visual_intent_agent.intent_engine.models import (
    INTERPRETER_EMPTY_OUTPUT,
    INTERPRETER_FULL_INTENT,
    INTERPRETER_INVALID_DELTA,
    INTERPRETER_INVALID_JSON,
    INTERPRETER_PENDING_QUESTION_MISSING_ID,
    INTERPRETER_PENDING_QUESTION_PATH_INVALID,
    INTERPRETER_SCHEMA_VIOLATION,
    INTERPRETER_UNPARSEABLE_OUTPUT,
    InterpreterError,
    InterpreterParseError,
)
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_llm import FakeLLMProvider


def _parse_error(request, text: str) -> InterpreterParseError:
    with pytest.raises(InterpreterParseError) as excinfo:
        h.interpret(request, text)
    return excinfo.value


# ---------------------------------------------------------------------------
# 正常解析
# ---------------------------------------------------------------------------


def test_minimal_valid_output_parses() -> None:
    result = h.interpret(h.make_request(), h.script())
    assert result.candidate_deltas == []
    assert result.detected_conflicts == []
    assert result.unresolved_language == []
    assert result.evidence_refs == []


def test_markdown_fences_are_stripped() -> None:
    text = "```json\n" + h.script([h.delta("SET", "composition.framing", value="wide")]) + "\n```"
    result = h.interpret(h.make_request(), text)
    assert len(result.candidate_deltas) == 1
    assert result.candidate_deltas[0].path == "composition.framing"


def test_plain_code_fence_is_stripped() -> None:
    text = "```\n" + h.script() + "\n```"
    assert h.interpret(h.make_request(), text).candidate_deltas == []


def test_delta_fields_are_preserved_exactly() -> None:
    text = h.script(
        [
            h.delta(
                "SET",
                "subject.count",
                value=3,
                evidence_fragment="三个人",
            )
        ]
    )
    request = h.make_request(message_text="画面里三个人")
    delta = h.interpret(request, text).candidate_deltas[0]
    assert delta.operation.value == "SET"
    assert delta.path == "subject.count"
    assert delta.value == 3
    assert delta.resolution is None


def test_unknown_path_is_not_a_parse_failure() -> None:
    """非法路径必须作为 Candidate Delta 交给 Validator 拒绝，而不是解析层修复/吞掉。"""
    text = h.script([h.delta("SET", "subject.hair_color", value="red")])
    result = h.interpret(h.make_request(), text)
    assert len(result.candidate_deltas) == 1
    assert result.candidate_deltas[0].path == "subject.hair_color"


def test_interpreter_does_not_modify_the_input_intent() -> None:
    intent = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    before = intent.model_dump_json()
    request = h.make_request(intent, message_text="镜头拉远")
    h.interpret(request, h.script([h.delta("SET", "composition.framing", value="wide")]))
    assert intent.model_dump_json() == before


# ---------------------------------------------------------------------------
# 证据构造（系统重填 ID，LLM 不产出 ID）
# ---------------------------------------------------------------------------


def test_evidence_uses_the_request_message_id_and_llm_fragment() -> None:
    text = h.script(
        [h.delta("SET", "composition.framing", value="wide", evidence_fragment="镜头拉远")]
    )
    result = h.interpret(h.make_request(message_text="镜头拉远"), text)
    refs = result.candidate_deltas[0].evidence_refs
    assert len(refs) == 1
    assert refs[0].message_id == h.MESSAGE_ID
    assert refs[0].fragment == "镜头拉远"
    assert refs[0].pending_question_id is None
    assert result.evidence_refs == refs


def test_answering_the_pending_question_attaches_its_id() -> None:
    pending = h.pending_question("lighting.character", suggested_values=("warm", "cool"))
    request = h.make_request(message_text="第二个", pending_question=pending)
    text = h.script(
        [
            h.delta(
                "SET",
                "lighting.character",
                value="cool",
                answers_pending_question=True,
            )
        ]
    )
    ref = h.interpret(request, text).candidate_deltas[0].evidence_refs[0]
    assert ref.pending_question_id == h.QUESTION_ID


def test_pending_flag_without_a_pending_question_is_ignored() -> None:
    text = h.script(
        [
            h.delta(
                "SET",
                "composition.framing",
                value="wide",
                answers_pending_question=True,
            )
        ]
    )
    ref = h.interpret(h.make_request(), text).candidate_deltas[0].evidence_refs[0]
    assert ref.pending_question_id is None


def test_evidence_refs_are_deduplicated_in_order() -> None:
    text = h.script(
        [
            h.delta("SET", "composition.framing", value="wide", evidence_fragment="拉远"),
            h.delta("SET", "style.primary", value="noir", evidence_fragment="拉远"),
        ]
    )
    result = h.interpret(h.make_request(), text)
    assert len(result.evidence_refs) == 1
    assert len(result.candidate_deltas) == 2


# ---------------------------------------------------------------------------
# 解析失败（可恢复，不猜测修复）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_empty_output_is_a_parse_failure(text: str) -> None:
    error = _parse_error(h.make_request(), text)
    assert error.code == INTERPRETER_EMPTY_OUTPUT
    assert error.retryable is True


def test_invalid_json_is_a_parse_failure() -> None:
    error = _parse_error(h.make_request(), "not json at all")
    assert error.code == INTERPRETER_INVALID_JSON


def test_non_object_json_is_a_schema_failure() -> None:
    error = _parse_error(h.make_request(), "[1, 2, 3]")
    assert error.code == INTERPRETER_SCHEMA_VIOLATION


def test_full_intent_output_is_rejected_as_a_parse_failure() -> None:
    text = VisualIntent(subject={"description": "a woman"}).model_dump_json()  # type: ignore[arg-type]
    error = _parse_error(h.make_request(), text)
    assert error.code == INTERPRETER_FULL_INTENT
    assert error.code.startswith(INTERPRETER_UNPARSEABLE_OUTPUT)


def test_wrapped_full_intent_is_rejected_as_a_parse_failure() -> None:
    inner = VisualIntent(subject={"description": "a woman"}).model_dump_json()  # type: ignore[arg-type]
    error = _parse_error(h.make_request(), '{"intent": ' + inner + "}")
    assert error.code == INTERPRETER_FULL_INTENT


def test_full_intent_with_candidate_deltas_is_treated_as_normal_output() -> None:
    """同时带 candidate_deltas 的对象按正常输出解析（facet 键会被 extra=forbid 拒绝）。"""
    text = '{"candidate_deltas": [], "subject": {"description": "x"}}'
    error = _parse_error(h.make_request(), text)
    assert error.code == INTERPRETER_SCHEMA_VIOLATION


def test_unknown_top_level_key_is_a_schema_failure() -> None:
    error = _parse_error(h.make_request(), '{"candidate_deltas": [], "extra": 1}')
    assert error.code == INTERPRETER_SCHEMA_VIOLATION


def test_unknown_delta_key_is_a_schema_failure() -> None:
    text = '{"candidate_deltas": [{"operation": "SET", "path": "x.y", "note": "hi"}]}'
    error = _parse_error(h.make_request(), text)
    assert error.code == INTERPRETER_SCHEMA_VIOLATION


def test_unknown_operation_is_a_schema_failure() -> None:
    text = h.script([{"operation": "ADD", "path": "composition.framing"}])
    error = _parse_error(h.make_request(), text)
    assert error.code == INTERPRETER_SCHEMA_VIOLATION


def test_unknown_resolution_is_a_schema_failure() -> None:
    text = h.script(
        [
            h.delta(
                "SET",
                "composition.framing",
                value="wide",
                resolution="system_decided",
            )
        ]
    )
    error = _parse_error(h.make_request(), text)
    assert error.code == INTERPRETER_SCHEMA_VIOLATION


def test_set_without_payload_is_an_invalid_delta() -> None:
    text = h.script([{"operation": "SET", "path": "composition.framing"}])
    error = _parse_error(h.make_request(), text)
    assert error.code == INTERPRETER_INVALID_DELTA


def test_clear_with_payload_is_an_invalid_delta() -> None:
    text = h.script([h.delta("CLEAR", "composition.framing", value="wide")])
    error = _parse_error(h.make_request(), text)
    assert error.code == INTERPRETER_INVALID_DELTA


# ---------------------------------------------------------------------------
# 冲突与未解析语言
# ---------------------------------------------------------------------------


def test_detected_conflicts_get_a_system_owned_code() -> None:
    text = h.script(
        detected_conflicts=[
            {
                "code": "policy.hard_conflict",  # LLM 自报 code 不被采信为主 code
                "message": "wide framing conflicts with the requested portrait style",
                "path": "composition.framing",
            }
        ]
    )
    result = h.interpret(h.make_request(), text)
    assert len(result.detected_conflicts) == 1
    issue = result.detected_conflicts[0]
    assert issue.code == "interpreter.conflict_detected"
    assert "policy.hard_conflict" in issue.message
    assert issue.path == "composition.framing"


def test_unresolved_language_is_deduplicated_and_blank_filtered() -> None:
    text = h.script(
        unresolved_language=["背景不要海边（但没说想要什么）", "  ", "背景不要海边（但没说想要什么）"]
    )
    result = h.interpret(h.make_request(), text)
    assert result.unresolved_language == ["背景不要海边（但没说想要什么）"]


# ---------------------------------------------------------------------------
# 上下文构造失败（程序级）与 Provider 失败（向上抛）
# ---------------------------------------------------------------------------


def test_pending_question_without_id_is_a_program_error() -> None:
    pending = h.pending_question("lighting.character", question_id=None)
    request = h.make_request(pending_question=pending)
    with pytest.raises(InterpreterError) as excinfo:
        h.interpret(request, h.script())
    assert excinfo.value.code == INTERPRETER_PENDING_QUESTION_MISSING_ID
    assert excinfo.value.retryable is False
    assert not isinstance(excinfo.value, InterpreterParseError)


def test_pending_question_with_non_whitelisted_path_is_a_program_error() -> None:
    pending = h.pending_question("subject.hair_color")
    request = h.make_request(pending_question=pending)
    with pytest.raises(InterpreterError) as excinfo:
        h.interpret(request, h.script())
    assert excinfo.value.code == INTERPRETER_PENDING_QUESTION_PATH_INVALID


def test_provider_errors_propagate_unchanged() -> None:
    def handler(request):  # noqa: ANN001 - 测试用简单函数
        raise ProviderError.rate_limited()

    interpreter = Interpreter(FakeLLMProvider(handler))
    with pytest.raises(ProviderError) as excinfo:
        interpreter.interpret(h.make_request())
    assert excinfo.value.code == "provider.rate_limited"


def test_llm_request_uses_the_system_prompt_and_json_response_format() -> None:
    provider = FakeLLMProvider([h.script()])
    Interpreter(provider).interpret(h.make_request(message_text="镜头拉远"))
    request = provider.requests[0]
    assert [message.role for message in request.messages] == ["system", "user"]
    assert request.response_format == {"type": "json_object"}
    assert request.messages[0].content.startswith("You are the Intent Interpreter")
    assert "镜头拉远" in request.messages[1].content
