"""Step 05 providers 单测共享工具。

全部离线：Settings 用伪造值构造（不读 `.env`），HTTP 用 `httpx.MockTransport`
拦截，绝不发起真实网络请求。
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from visual_intent_agent.config import Settings

#: 测试用占位 key（不是真实凭据；仅用于断言 Authorization 头存在与脱敏）。
TEST_PLACEHOLDER_KEY = "test-provider-key-not-a-real-secret"

#: 测试用 base_url（`.invalid` 保证永不可解析）。
TEST_BASE_URL = "https://provider.invalid/compatible-mode/v1"


@pytest.fixture
def fake_settings() -> Settings:
    """伪造 Settings：确定性的 base_url / key / 模型 / 重试次数 / 超时。"""
    return Settings(
        provider_base_url=TEST_BASE_URL,
        provider_api_key=TEST_PLACEHOLDER_KEY,
        llm_model="test-llm-model",
        llm_timeout_seconds=12.5,
        http_max_retries=2,
    )


@pytest.fixture
def captured_requests() -> list[httpx.Request]:
    return []


@pytest.fixture
def mock_client_factory(
    captured_requests: list[httpx.Request],
) -> Callable[[Callable[[httpx.Request], httpx.Response]], httpx.Client]:
    """返回工厂：`factory(handler) -> httpx.Client`，并把请求记录进 captured_requests。"""

    def _factory(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
        def _handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return handler(request)

        return httpx.Client(transport=httpx.MockTransport(_handler))

    return _factory
