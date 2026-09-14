"""FakeLLMProvider 确定性行为测试（ARCHITECTURE.md 6.4）。"""

from __future__ import annotations

import pytest

from visual_intent_agent.providers.errors import PROVIDER_INVALID_REQUEST, ProviderError
from visual_intent_agent.providers.fake_llm import FAKE_LLM_MODEL, FakeLLMProvider
from visual_intent_agent.providers.llm import LLMMessage, LLMRequest, LLMResponse


def _request(text: str = "镜头拉远") -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content=text)])


def test_string_script_becomes_a_deterministic_response() -> None:
    provider = FakeLLMProvider(['{"candidate_deltas": []}'])
    first = provider.complete(_request())
    assert first.content == '{"candidate_deltas": []}'
    assert first.model == FAKE_LLM_MODEL
    assert first.provider_request_id is None
    assert first.usage is None


def test_llm_response_entries_are_returned_unchanged() -> None:
    scripted = LLMResponse(content="{}", model="scripted", provider_request_id="req_9")
    provider = FakeLLMProvider([scripted])
    assert provider.complete(_request()) == scripted


def test_callable_handler_receives_the_request() -> None:
    seen: list[LLMRequest] = []

    def handler(request: LLMRequest) -> str:
        seen.append(request)
        return '{"candidate_deltas": []}'

    provider = FakeLLMProvider(handler)
    assert provider.complete(_request("hello")).content == '{"candidate_deltas": []}'
    assert seen == provider.requests
    assert provider.requests[0].messages[0].content == "hello"


def test_callable_handler_can_raise_a_provider_error() -> None:
    def handler(request: LLMRequest) -> str:
        raise ProviderError.rate_limited()

    with pytest.raises(ProviderError) as excinfo:
        FakeLLMProvider(handler).complete(_request())
    assert excinfo.value.code == "provider.rate_limited"


def test_requests_are_recorded_in_order() -> None:
    provider = FakeLLMProvider(['{"candidate_deltas": []}'] * 2)
    provider.complete(_request("one"))
    provider.complete(_request("two"))
    assert [r.messages[0].content for r in provider.requests] == ["one", "two"]


def test_exhausted_script_raises_invalid_request() -> None:
    provider = FakeLLMProvider(["{}"])
    provider.complete(_request())
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.code == PROVIDER_INVALID_REQUEST
    assert excinfo.value.retryable is False


def test_script_entry_of_the_wrong_type_raises_invalid_request() -> None:
    provider = FakeLLMProvider([42])  # type: ignore[list-item]
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.code == PROVIDER_INVALID_REQUEST


def test_complete_rejects_non_request_input() -> None:
    provider = FakeLLMProvider(["{}"])
    with pytest.raises(ProviderError) as excinfo:
        provider.complete("not a request")  # type: ignore[arg-type]
    assert excinfo.value.code == PROVIDER_INVALID_REQUEST


def test_same_input_yields_byte_identical_output_across_instances() -> None:
    script = ['{"candidate_deltas": [{"operation": "PIN", "path": "subject.description"}]}']
    first = FakeLLMProvider(script).complete(_request("人物不变"))
    second = FakeLLMProvider(script).complete(_request("人物不变"))
    assert first.model_dump_json() == second.model_dump_json()
