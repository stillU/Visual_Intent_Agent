"""ProviderError 错误分类测试（ARCHITECTURE.md 6.3 六分类 + 可重试性冻结映射）。"""

from __future__ import annotations

import pytest

from visual_intent_agent.providers.errors import (
    PROVIDER_AUTH,
    PROVIDER_ERROR_CODES,
    PROVIDER_INVALID_REQUEST,
    PROVIDER_NETWORK,
    PROVIDER_RATE_LIMITED,
    PROVIDER_SERVER_ERROR,
    PROVIDER_TIMEOUT,
    PROVIDER_UNPARSEABLE_RESPONSE,
    RETRYABLE_CODES,
    ProviderError,
)

FACTORIES = {
    "auth": (ProviderError.auth, PROVIDER_AUTH, False),
    "rate_limited": (ProviderError.rate_limited, PROVIDER_RATE_LIMITED, True),
    "timeout": (ProviderError.timeout, PROVIDER_TIMEOUT, True),
    "network": (ProviderError.network, PROVIDER_NETWORK, True),
    "server": (ProviderError.server, PROVIDER_SERVER_ERROR, True),
    "invalid_request": (ProviderError.invalid_request, PROVIDER_INVALID_REQUEST, False),
    "unparseable_response": (
        ProviderError.unparseable_response,
        PROVIDER_UNPARSEABLE_RESPONSE,
        False,
    ),
}


def test_error_taxonomy_is_exactly_seven_codes() -> None:
    assert PROVIDER_ERROR_CODES == {
        PROVIDER_AUTH,
        PROVIDER_RATE_LIMITED,
        PROVIDER_TIMEOUT,
        PROVIDER_NETWORK,
        PROVIDER_SERVER_ERROR,
        PROVIDER_INVALID_REQUEST,
        PROVIDER_UNPARSEABLE_RESPONSE,
    }
    assert len(PROVIDER_ERROR_CODES) == 7


@pytest.mark.parametrize("name", sorted(FACTORIES))
def test_factory_sets_code_and_retryability(name: str) -> None:
    factory, code, retryable = FACTORIES[name]
    error = factory()
    assert isinstance(error, ProviderError)
    assert error.code == code
    assert error.retryable is retryable
    assert error.retryable is (code in RETRYABLE_CODES)
    assert isinstance(error.message, str) and error.message


@pytest.mark.parametrize("name", sorted(FACTORIES))
def test_factory_carries_optional_metadata(name: str) -> None:
    factory, _, _ = FACTORIES[name]
    try:
        error = factory(
            "boom", status_code=429, provider_request_id="req_123"
        )
    except TypeError:
        # timeout / network 工厂不接受 status_code（网络类错误没有 HTTP 状态码）。
        error = factory("boom", provider_request_id="req_123")
        assert error.status_code is None
    else:
        assert error.status_code == 429
    assert error.provider_request_id == "req_123"
    assert "req_123" in str(error)
    assert "boom" in str(error)


def test_unknown_code_is_rejected() -> None:
    with pytest.raises(ValueError):
        ProviderError("provider.nope", "x")


def test_retryable_contradiction_is_rejected() -> None:
    with pytest.raises(ValueError):
        ProviderError(PROVIDER_AUTH, "x", retryable=True)
    with pytest.raises(ValueError):
        ProviderError(PROVIDER_RATE_LIMITED, "x", retryable=False)


def test_error_str_has_no_authorization_header_material() -> None:
    error = ProviderError.auth("credentials rejected", status_code=401)
    text = str(error)
    assert "Bearer" not in text
    assert "Authorization" not in text
