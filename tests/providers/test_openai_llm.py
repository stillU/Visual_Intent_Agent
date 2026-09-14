"""OpenAICompatibleLLMProvider 测试：全部用 `httpx.MockTransport`，零真实网络。

覆盖：请求构造（URL / 头 / body / 不发异步头）、响应解析、错误六分类、重试与退避、
凭据不泄露。真实端点的 smoke 在 `tests/smoke/`。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from visual_intent_agent.config import Settings
from visual_intent_agent.providers import openai_llm
from visual_intent_agent.providers.errors import (
    PROVIDER_AUTH,
    PROVIDER_INVALID_REQUEST,
    PROVIDER_NETWORK,
    PROVIDER_RATE_LIMITED,
    PROVIDER_SERVER_ERROR,
    PROVIDER_TIMEOUT,
    PROVIDER_UNPARSEABLE_RESPONSE,
    ProviderError,
)
from visual_intent_agent.providers.llm import LLMMessage, LLMProvider, LLMRequest
from visual_intent_agent.providers.openai_llm import (
    BACKOFF_BASE_SECONDS,
    OpenAICompatibleLLMProvider,
)

#: 与 tests/providers/conftest.py 的伪造 key 保持一致（测试目录不是 package，
#: 因此这里重复一个字面量而不是相对导入）。
TEST_PLACEHOLDER_KEY = "test-provider-key-not-a-real-secret"


def _request(**overrides: object) -> LLMRequest:
    payload = {"messages": [LLMMessage(role="user", content="镜头拉远")]}
    payload.update(overrides)
    return LLMRequest(**payload)  # type: ignore[arg-type]


def _ok_body(
    content: str = '{"candidate_deltas": []}',
    *,
    model: str = "upstream-model",
    usage: object | None = None,
    body_id: str = "chatcmpl_1",
) -> dict:
    body: dict = {
        "id": body_id,
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }
    if usage is not None:
        body["usage"] = usage
    return body


def _provider(
    settings: Settings, factory: Callable, handler: Callable
) -> OpenAICompatibleLLMProvider:
    return OpenAICompatibleLLMProvider(settings, client=factory(handler))


class _SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> _SleepRecorder:
    recorder = _SleepRecorder()
    monkeypatch.setattr(openai_llm.time, "sleep", recorder)
    return recorder


# ---------------------------------------------------------------------------
# 请求构造
# ---------------------------------------------------------------------------


def test_successful_call_posts_to_chat_completions(
    fake_settings: Settings, mock_client_factory, captured_requests
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_ok_body(), headers={"x-request-id": "req_header_1"}
        )

    provider = _provider(fake_settings, mock_client_factory, handler)
    response = provider.complete(_request())

    assert response.content == '{"candidate_deltas": []}'
    assert response.model == "upstream-model"
    assert response.provider_request_id == "req_header_1"
    assert response.usage is None

    sent = captured_requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == f"{fake_settings.provider_base_url}/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {TEST_PLACEHOLDER_KEY}"
    # 该 key 不支持异步头（ARCHITECTURE.md 6.2：403）。
    assert not any("async" in name.lower() for name in sent.headers)

    body = json.loads(sent.content)
    assert body["model"] == "test-llm-model"
    assert body["messages"] == [{"role": "user", "content": "镜头拉远"}]
    assert "temperature" not in body
    assert "max_tokens" not in body
    assert "response_format" not in body


def test_optional_fields_are_sent_only_when_set(
    fake_settings: Settings, mock_client_factory, captured_requests
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_body())

    provider = _provider(fake_settings, mock_client_factory, handler)
    provider.complete(
        _request(
            model="explicit-model",
            temperature=0.25,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
    )
    body = json.loads(captured_requests[0].content)
    assert body["model"] == "explicit-model"
    assert body["temperature"] == 0.25
    assert body["max_tokens"] == 700
    assert body["response_format"] == {"type": "json_object"}


def test_request_id_falls_back_to_body_id(
    fake_settings: Settings, mock_client_factory
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_body(body_id="chatcmpl_body"))

    provider = _provider(fake_settings, mock_client_factory, handler)
    assert provider.complete(_request()).provider_request_id == "chatcmpl_body"


def test_model_falls_back_to_the_requested_model(
    fake_settings: Settings, mock_client_factory
) -> None:
    body = _ok_body()
    del body["model"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = _provider(fake_settings, mock_client_factory, handler)
    assert provider.complete(_request(model="explicit-model")).model == "explicit-model"


@pytest.mark.parametrize(
    "usage,expected",
    [
        ({"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}, (3, 5, 8)),
        ({"prompt_tokens": 3}, (3, None, None)),
        ({}, (None, None, None)),
        ("not-a-dict", None),
        (None, None),
    ],
)
def test_usage_parsing_never_fabricates_values(
    fake_settings: Settings, mock_client_factory, usage: object, expected
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_body(usage=usage or {}) if usage is not None else _ok_body())

    provider = _provider(fake_settings, mock_client_factory, handler)
    parsed = provider.complete(_request()).usage
    if expected is None:
        assert parsed is None
    else:
        assert parsed is not None
        assert (parsed.prompt_tokens, parsed.completion_tokens, parsed.total_tokens) == expected


# ---------------------------------------------------------------------------
# 错误六分类与重试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_are_not_retried(
    fake_settings: Settings, mock_client_factory, captured_requests, slept, status: int
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "denied"})

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.code == PROVIDER_AUTH
    assert excinfo.value.retryable is False
    assert excinfo.value.status_code == status
    assert len(captured_requests) == 1
    assert slept.calls == []


def test_rate_limited_is_retried_then_succeeds(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(200, json=_ok_body())

    provider = _provider(fake_settings, mock_client_factory, handler)
    assert provider.complete(_request()).content == '{"candidate_deltas": []}'
    assert len(captured_requests) == 2
    assert slept.calls == [BACKOFF_BASE_SECONDS]


def test_server_errors_are_retried_up_to_the_configured_maximum(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.code == PROVIDER_SERVER_ERROR
    assert excinfo.value.status_code == 503
    assert len(captured_requests) == fake_settings.http_max_retries + 1
    assert slept.calls == [0.5, 1.0]


def test_timeouts_are_classified_and_retried(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.code == PROVIDER_TIMEOUT
    assert excinfo.value.retryable is True
    assert excinfo.value.status_code is None
    assert len(captured_requests) == fake_settings.http_max_retries + 1
    assert slept.calls == [0.5, 1.0]


@pytest.mark.parametrize(
    "exception",
    [httpx.ConnectError("dns failure"), httpx.ConnectTimeout("connect timed out")],
)
def test_network_errors_are_classified(
    fake_settings: Settings,
    mock_client_factory,
    captured_requests,
    slept,
    exception: Exception,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.code in {PROVIDER_NETWORK, PROVIDER_TIMEOUT}
    assert excinfo.value.retryable is True
    assert len(captured_requests) == fake_settings.http_max_retries + 1


def test_other_4xx_is_invalid_request_and_not_retried(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad request"})

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.code == PROVIDER_INVALID_REQUEST
    assert excinfo.value.retryable is False
    assert len(captured_requests) == 1
    assert slept.calls == []


def test_malformed_json_is_unparseable_and_not_retried(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>not json</html>", headers={"content-type": "text/html"}
        )

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.code == PROVIDER_UNPARSEABLE_RESPONSE
    assert excinfo.value.retryable is False
    assert len(captured_requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "   "}}]},
        {"choices": [{"message": {"content": None}}]},
    ],
)
def test_structural_mismatches_are_unparseable(
    fake_settings: Settings, mock_client_factory, body: dict
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.code == PROVIDER_UNPARSEABLE_RESPONSE


def test_unparseable_error_body_is_classified_as_auth_not_parse_error(
    fake_settings: Settings, mock_client_factory
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b"not json either")

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.code == PROVIDER_AUTH


# ---------------------------------------------------------------------------
# 调用前校验与凭据保护
# ---------------------------------------------------------------------------


def test_empty_messages_are_rejected_without_an_http_call(
    fake_settings: Settings, mock_client_factory, captured_requests
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json=_ok_body())

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(LLMRequest(messages=[]))
    assert excinfo.value.code == PROVIDER_INVALID_REQUEST
    assert captured_requests == []


def test_unsupported_role_is_rejected_without_an_http_call(
    fake_settings: Settings, mock_client_factory, captured_requests
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json=_ok_body())

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(LLMRequest(messages=[LLMMessage(role="tool", content="x")]))
    assert excinfo.value.code == PROVIDER_INVALID_REQUEST
    assert captured_requests == []


def test_default_client_uses_the_configured_timeout(fake_settings: Settings) -> None:
    provider = OpenAICompatibleLLMProvider(fake_settings)
    try:
        assert provider._client.timeout == httpx.Timeout(
            fake_settings.llm_timeout_seconds
        )
    finally:
        provider.close()


def test_the_api_key_never_leaks_into_error_messages(
    fake_settings: Settings, mock_client_factory
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 模拟一个回显请求内容的坏上游：错误文本里出现 key 也必须被脱敏。
        return httpx.Response(400, text=f"bad request, saw {TEST_PLACEHOLDER_KEY}")

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_request())
    assert TEST_PLACEHOLDER_KEY not in str(excinfo.value)
    assert "***" in str(excinfo.value)


def test_injected_client_is_not_closed_by_the_provider(
    fake_settings: Settings, mock_client_factory
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json=_ok_body())

    client = mock_client_factory(handler)
    provider = OpenAICompatibleLLMProvider(fake_settings, client=client)
    provider.close()
    # 注入的 client 归调用方所有：关闭后再用仍应可用（MockTransport 无连接状态）。
    assert client.is_closed is False
    client.close()


def test_adapter_satisfies_the_provider_protocol(
    fake_settings: Settings, mock_client_factory
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json=_ok_body())

    provider = _provider(fake_settings, mock_client_factory, handler)
    assert isinstance(provider, LLMProvider)
