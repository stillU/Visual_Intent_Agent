"""IntentEngine 错误边界：解析失败 / Provider 失败必须"可观察、可恢复"，不抛异常。"""

from __future__ import annotations

import pytest
import ie_helpers as h

from visual_intent_agent.domain import VisualIntent
from visual_intent_agent.intent_engine import IntentEngine, Interpreter
from visual_intent_agent.intent_engine.models import (
    INTERPRETER_INVALID_JSON,
    InterpreterError,
)
from visual_intent_agent.policy import assess
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_llm import FakeLLMProvider


def _ready_intent() -> VisualIntent:
    return VisualIntent(
        subject={"description": "a woman", "pose_action": "standing"},  # type: ignore[arg-type]
        composition={"framing": "medium"},  # type: ignore[arg-type]
        environment={"mode": "studio", "location": "a grey room"},  # type: ignore[arg-type]
        style={"primary": "cinematic"},  # type: ignore[arg-type]
        lighting={"character": "soft light"},  # type: ignore[arg-type]
    )


def test_ready_baseline_is_actually_ready() -> None:
    assert assess(_ready_intent()).ready_for_confirmation is True


def test_invalid_json_returns_a_recoverable_issue_instead_of_raising() -> None:
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    resolution, _ = h.run(h.make_request(before, message_text="镜头拉远"), "not json")

    assert resolution.intent.model_dump_json() == before.model_dump_json()
    assert resolution.applied_deltas == []
    assert resolution.ready_for_confirmation is False
    assert INTERPRETER_INVALID_JSON in h.issue_codes(resolution)


def test_parse_failure_forces_not_ready_even_when_the_current_intent_is_ready() -> None:
    before = _ready_intent()
    resolution, _ = h.run(h.make_request(before, message_text="随便改改"), "not json")

    assert resolution.ready_for_confirmation is False
    assert resolution.intent.model_dump_json() == before.model_dump_json()
    assert resolution.issues[0].code == INTERPRETER_INVALID_JSON


def test_full_intent_failure_keeps_the_original_intent_and_is_observable() -> None:
    before = _ready_intent()
    replacement = VisualIntent(subject={"description": "a cat"})  # type: ignore[arg-type]
    resolution, _ = h.run(
        h.make_request(before, message_text="换成猫"), replacement.model_dump_json()
    )
    assert resolution.intent.model_dump_json() == before.model_dump_json()
    assert resolution.ready_for_confirmation is False
    assert "interpreter.unparseable_output.full_intent" in h.issue_codes(resolution)


def test_provider_auth_failure_is_reported_as_a_provider_issue() -> None:
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]

    def handler(request):  # noqa: ANN001 - 测试用简单函数
        raise ProviderError.auth(status_code=401)

    resolution, _ = h.run_with(h.make_request(before), handler)
    assert resolution.intent.model_dump_json() == before.model_dump_json()
    assert resolution.applied_deltas == []
    assert resolution.ready_for_confirmation is False
    assert h.issue_codes(resolution) == ["provider.auth"]
    assert "retryable=False" in resolution.issues[0].message


def test_provider_rate_limit_failure_is_marked_retryable() -> None:
    def handler(request):  # noqa: ANN001
        raise ProviderError.rate_limited(status_code=429)

    resolution, _ = h.run_with(h.make_request(), handler)
    assert h.issue_codes(resolution) == ["provider.rate_limited"]
    assert "retryable=True" in resolution.issues[0].message


def test_failure_resolution_still_exposes_policy_state() -> None:
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    resolution, _ = h.run(h.make_request(before), "not json")
    baseline = assess(before)
    assert resolution.unresolved_decisions == baseline.unresolved_decisions
    assert resolution.conflicts == baseline.conflicts
    assert resolution.question == baseline.question
    assert resolution.issues[1:] == baseline.issues


def test_context_errors_are_not_recoverable_and_propagate() -> None:
    pending = h.pending_question("lighting.character", question_id=None)
    request = h.make_request(pending_question=pending)
    engine = IntentEngine(Interpreter(FakeLLMProvider([h.script()])))
    with pytest.raises(InterpreterError):
        engine.resolve(request)


def test_parser_never_retries_the_provider_by_itself() -> None:
    provider = FakeLLMProvider(["not json", h.script()])
    engine = IntentEngine(Interpreter(provider))
    resolution = engine.resolve(h.make_request())
    assert INTERPRETER_INVALID_JSON in h.issue_codes(resolution)
    # 只调用一次：重试策略由 Step 06 依据 retryable 决定，Step 05 不自行修复。
    assert len(provider.requests) == 1


def test_engine_does_not_mutate_the_request_intent_on_any_path() -> None:
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    snapshot = before.model_dump_json()
    text = h.script(
        [h.delta("SET", "composition.framing", value="wide", evidence_fragment="镜头拉远")]
    )
    h.run(h.make_request(before, message_text="镜头拉远"), text)
    assert before.model_dump_json() == snapshot
