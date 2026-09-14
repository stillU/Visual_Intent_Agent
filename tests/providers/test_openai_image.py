"""OpenAIImageProvider 测试（Step 08）：全部用 `httpx.MockTransport`，零真实网络。

覆盖：请求构造（URL / 头 / body / 不发异步头 / 下载不带 Authorization）、响应解析与
**adapter 内下载字节**、seed/request id 不伪造、错误六分类、重试与退避、下载失败不重新
POST、临时 URL 与 key 脱敏。真实端点 smoke 在 `tests/smoke/test_image_smoke.py`。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from visual_intent_agent.config import Settings
from visual_intent_agent.providers import openai_image
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
from visual_intent_agent.providers.image import (
    IMAGE_MIME_TYPE_OCTET_STREAM,
    IMAGE_MIME_TYPE_PNG,
    ImageProvider,
)
from visual_intent_agent.providers.openai_image import (
    BACKOFF_BASE_SECONDS,
    OpenAIImageProvider,
)

from image_helpers import (
    JPEG_HEAD_BYTES,
    PNG_BYTES,
    PNG_MAGIC,
    SIGNED_IMAGE_URL,
    TEST_IMAGE_KEY,
    image_request,
    ok_body,
)


class _SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> _SleepRecorder:
    recorder = _SleepRecorder()
    monkeypatch.setattr(openai_image.time, "sleep", recorder)
    return recorder


def _provider(
    settings: Settings, factory: Callable, handler: Callable
) -> OpenAIImageProvider:
    return OpenAIImageProvider(settings, client=factory(handler))


def _posts(requests: list[httpx.Request]) -> list[httpx.Request]:
    return [request for request in requests if request.method == "POST"]


def _gets(requests: list[httpx.Request]) -> list[httpx.Request]:
    return [request for request in requests if request.method == "GET"]


def _image_handler(post_response: httpx.Response, download: Callable[[httpx.Request], httpx.Response]):
    """POST 固定响应；GET 交给 download 回调。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return post_response
        return download(request)

    return handler


# ---------------------------------------------------------------------------
# 请求构造
# ---------------------------------------------------------------------------


def test_successful_call_posts_to_images_generations_and_downloads_bytes(
    fake_settings: Settings, mock_client_factory, captured_requests
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json=ok_body(),
                headers={"x-request-id": "req_image_1"},
            )
        return httpx.Response(
            200, content=PNG_BYTES, headers={"content-type": "image/png"}
        )

    provider = _provider(fake_settings, mock_client_factory, handler)
    result = provider.generate(
        image_request(prompt="subject: a cat", size="1024x1024", model="qwen-image-3.0")
    )

    assert result.model == "qwen-image-3.0"
    assert result.provider_request_id == "req_image_1"
    assert result.seed is None
    assert len(result.images) == 1
    assert result.images[0].content == PNG_BYTES
    assert result.images[0].mime_type == IMAGE_MIME_TYPE_PNG
    # 成功结果的**非字节元数据**里绝不能出现临时 URL（字节本身无法 JSON 序列化）。
    metadata = json.dumps(result.model_dump(mode="json", exclude={"images"}))
    assert SIGNED_IMAGE_URL not in metadata

    posts = _posts(captured_requests)
    gets = _gets(captured_requests)
    assert len(posts) == 1
    assert len(gets) == 1

    sent = posts[0]
    assert str(sent.url) == f"{fake_settings.provider_base_url}/images/generations"
    assert (
        sent.headers["authorization"]
        == f"Bearer {fake_settings.provider_api_key.get_secret_value()}"
    )
    # 该 key 不支持异步头（ARCHITECTURE.md 6.2：403）。
    assert not any("async" in name.lower() for name in sent.headers)
    body = json.loads(sent.content)
    assert body == {
        "model": "qwen-image-3.0",
        "prompt": "subject: a cat",
        "size": "1024x1024",
    }

    # 下载签名 URL 属于第三方主机：绝不携带 Authorization。
    assert str(gets[0].url) == SIGNED_IMAGE_URL
    assert "authorization" not in gets[0].headers


def test_model_falls_back_to_settings_when_request_model_is_none(
    fake_settings: Settings, mock_client_factory, captured_requests
) -> None:
    body = ok_body()
    del body["model"]

    provider = _provider(
        fake_settings,
        mock_client_factory,
        _image_handler(
            httpx.Response(200, json=body),
            lambda request: httpx.Response(200, content=PNG_BYTES),
        ),
    )
    result = provider.generate(image_request())
    assert result.model == fake_settings.image_model
    assert json.loads(_posts(captured_requests)[0].content)["model"] == fake_settings.image_model


def test_model_from_body_wins_over_the_requested_model(
    fake_settings: Settings, mock_client_factory
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        _image_handler(
            httpx.Response(200, json=ok_body(model="upstream-reported-model")),
            lambda request: httpx.Response(200, content=PNG_BYTES),
        ),
    )
    assert provider.generate(image_request()).model == "upstream-reported-model"


def test_provider_request_id_falls_back_to_the_body_id(
    fake_settings: Settings, mock_client_factory
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        _image_handler(
            httpx.Response(200, json=ok_body(body_id="body_request_id")),
            lambda request: httpx.Response(200, content=PNG_BYTES),
        ),
    )
    assert provider.generate(image_request()).provider_request_id == "body_request_id"


def test_provider_request_id_is_none_when_absent(
    fake_settings: Settings, mock_client_factory
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        _image_handler(
            httpx.Response(200, json=ok_body()),
            lambda request: httpx.Response(200, content=PNG_BYTES),
        ),
    )
    assert provider.generate(image_request()).provider_request_id is None


def test_seed_is_parsed_when_reported_and_never_fabricated(
    fake_settings: Settings, mock_client_factory
) -> None:
    def build(seed: object):
        provider = _provider(
            fake_settings,
            mock_client_factory,
            _image_handler(
                httpx.Response(200, json=ok_body(seed=seed)),
                lambda request: httpx.Response(200, content=PNG_BYTES),
            ),
        )
        return provider.generate(image_request()).seed

    assert build(1234) == 1234
    assert build("1234") is None
    assert build(True) is None


def test_multiple_data_entries_produce_multiple_images_in_order(
    fake_settings: Settings, mock_client_factory, captured_requests
) -> None:
    body = {
        "created": 1,
        "data": [{"url": "https://oss.invalid/a.png"}, {"url": "https://oss.invalid/b.png"}],
        "usage": {},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=body)
        return httpx.Response(200, content=b"bytes-for-" + request.url.path.encode())

    provider = _provider(fake_settings, mock_client_factory, handler)
    result = provider.generate(image_request())
    assert [image.content for image in result.images] == [b"bytes-for-/a.png", b"bytes-for-/b.png"]


# ---------------------------------------------------------------------------
# MIME 识别（诚实标注，不伪造）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type,content,expected",
    [
        ("image/png", PNG_BYTES, IMAGE_MIME_TYPE_PNG),
        ("image/png; charset=binary", PNG_BYTES, IMAGE_MIME_TYPE_PNG),
        ("image/jpeg", JPEG_HEAD_BYTES, "image/jpeg"),
        (None, PNG_BYTES, IMAGE_MIME_TYPE_PNG),  # 头缺失 → magic
        (None, JPEG_HEAD_BYTES, "image/jpeg"),
        (None, b"not-an-image", IMAGE_MIME_TYPE_OCTET_STREAM),  # 诚实回退
        ("text/plain", b"not-an-image", IMAGE_MIME_TYPE_OCTET_STREAM),
    ],
)
def test_mime_type_is_detected_from_the_header_then_magic(
    fake_settings: Settings,
    mock_client_factory,
    content_type: str | None,
    content: bytes,
    expected: str,
) -> None:
    headers = {} if content_type is None else {"content-type": content_type}
    provider = _provider(
        fake_settings,
        mock_client_factory,
        _image_handler(
            httpx.Response(200, json=ok_body()),
            lambda request: httpx.Response(200, content=content, headers=headers),
        ),
    )
    assert provider.generate(image_request()).images[0].mime_type == expected


def test_downloaded_bytes_keep_the_png_magic(fake_settings: Settings, mock_client_factory) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        _image_handler(
            httpx.Response(200, json=ok_body()),
            lambda request: httpx.Response(200, content=PNG_BYTES),
        ),
    )
    content = provider.generate(image_request()).images[0].content
    assert content.startswith(PNG_MAGIC)


# ---------------------------------------------------------------------------
# 错误六分类与重试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_are_not_retried_and_never_download(
    fake_settings: Settings, mock_client_factory, captured_requests, slept, status: int
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        lambda request: httpx.Response(status, json={"error": "denied"}),
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    assert excinfo.value.code == PROVIDER_AUTH
    assert excinfo.value.retryable is False
    assert excinfo.value.status_code == status
    assert len(_posts(captured_requests)) == 1
    assert _gets(captured_requests) == []
    assert slept.calls == []


def test_rate_limited_generation_is_retried_then_succeeds(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, json={"error": "slow down"})
            return httpx.Response(200, json=ok_body())
        return httpx.Response(200, content=PNG_BYTES)

    provider = _provider(fake_settings, mock_client_factory, handler)
    assert provider.generate(image_request()).images[0].content == PNG_BYTES
    assert len(_posts(captured_requests)) == 2
    assert slept.calls == [BACKOFF_BASE_SECONDS]


def test_server_errors_are_retried_up_to_the_configured_maximum(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        lambda request: httpx.Response(503, text="upstream unavailable"),
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    assert excinfo.value.code == PROVIDER_SERVER_ERROR
    assert excinfo.value.status_code == 503
    assert len(_posts(captured_requests)) == fake_settings.http_max_retries + 1
    assert slept.calls == [0.5, 1.0]


def test_timeouts_are_classified_and_retried(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    assert excinfo.value.code == PROVIDER_TIMEOUT
    assert excinfo.value.retryable is True
    assert excinfo.value.status_code is None
    assert len(_posts(captured_requests)) == fake_settings.http_max_retries + 1
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
        provider.generate(image_request())
    assert excinfo.value.code in {PROVIDER_NETWORK, PROVIDER_TIMEOUT}
    assert excinfo.value.retryable is True
    assert len(_posts(captured_requests)) == fake_settings.http_max_retries + 1


def test_other_4xx_is_invalid_request_and_not_retried(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        lambda request: httpx.Response(400, json={"error": "bad request"}),
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    assert excinfo.value.code == PROVIDER_INVALID_REQUEST
    assert excinfo.value.retryable is False
    assert len(_posts(captured_requests)) == 1
    assert slept.calls == []


def test_malformed_json_is_unparseable_and_not_retried(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        lambda request: httpx.Response(
            200, content=b"<html>not json</html>", headers={"content-type": "text/html"}
        ),
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    assert excinfo.value.code == PROVIDER_UNPARSEABLE_RESPONSE
    assert excinfo.value.retryable is False
    assert len(_posts(captured_requests)) == 1
    assert slept.calls == []


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"data": []},
        {"data": "not-a-list"},
        {"data": [{}]},
        {"data": [{"url": ""}]},
        {"data": [{"url": None}]},
        {"data": [{"url": 42}]},
        {"data": ["not-a-dict"]},
    ],
)
def test_structural_mismatches_are_unparseable(
    fake_settings: Settings, mock_client_factory, body: object
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        lambda request: httpx.Response(200, json=body),
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    assert excinfo.value.code == PROVIDER_UNPARSEABLE_RESPONSE


def test_empty_download_bytes_are_unparseable(
    fake_settings: Settings, mock_client_factory
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        _image_handler(
            httpx.Response(200, json=ok_body()),
            lambda request: httpx.Response(200, content=b""),
        ),
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    assert excinfo.value.code == PROVIDER_UNPARSEABLE_RESPONSE


# ---------------------------------------------------------------------------
# 下载失败：独立重试，不重新 POST
# ---------------------------------------------------------------------------


def test_download_failure_retries_the_download_without_reposting(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    download_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=ok_body())
        download_calls["n"] += 1
        if download_calls["n"] == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(200, content=PNG_BYTES)

    provider = _provider(fake_settings, mock_client_factory, handler)
    result = provider.generate(image_request())
    assert result.images[0].content == PNG_BYTES
    assert len(_posts(captured_requests)) == 1  # 没有重复出图/计费
    assert len(_gets(captured_requests)) == 2
    assert slept.calls == [BACKOFF_BASE_SECONDS]


def test_download_auth_failure_is_not_retried_and_is_classified(
    fake_settings: Settings, mock_client_factory, captured_requests, slept
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        _image_handler(
            httpx.Response(200, json=ok_body()),
            lambda request: httpx.Response(403, text="signature expired"),
        ),
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    assert excinfo.value.code == PROVIDER_AUTH
    assert excinfo.value.retryable is False
    assert len(_gets(captured_requests)) == 1
    assert slept.calls == []


# ---------------------------------------------------------------------------
# 凭据 / 临时 URL 脱敏与客户端所有权
# ---------------------------------------------------------------------------


def test_the_api_key_and_the_signed_url_never_leak_into_error_messages(
    fake_settings: Settings, mock_client_factory
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 恶意上游回显请求上下文：key 与签名 URL 都必须被脱敏。
        secret = fake_settings.provider_api_key.get_secret_value()
        return httpx.Response(
            400, text=f"bad request, saw {secret} at {SIGNED_IMAGE_URL}"
        )

    provider = _provider(fake_settings, mock_client_factory, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    text = str(excinfo.value)
    assert fake_settings.provider_api_key.get_secret_value() not in text
    assert TEST_IMAGE_KEY not in text
    assert SIGNED_IMAGE_URL not in text
    assert "***" in text
    assert "<url>" in text


def test_wrong_request_type_is_rejected_without_an_http_call(
    fake_settings: Settings, mock_client_factory, captured_requests
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        lambda request: httpx.Response(200, json=ok_body()),  # pragma: no cover
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.generate("not-a-request")  # type: ignore[arg-type]
    assert excinfo.value.code == PROVIDER_INVALID_REQUEST
    assert captured_requests == []


def test_default_client_uses_the_configured_image_timeout() -> None:
    settings = Settings(
        provider_base_url="https://provider.invalid/v1",
        provider_api_key=TEST_IMAGE_KEY,
        image_timeout_seconds=33.5,
    )
    provider = OpenAIImageProvider(settings)
    try:
        assert provider._client.timeout == httpx.Timeout(33.5)
    finally:
        provider.close()


def test_injected_client_is_not_closed_by_the_provider(
    fake_settings: Settings, mock_client_factory
) -> None:
    client = mock_client_factory(
        lambda request: httpx.Response(200, json=ok_body())  # pragma: no cover
    )
    provider = OpenAIImageProvider(fake_settings, client=client)
    provider.close()
    assert client.is_closed is False
    client.close()


def test_adapter_satisfies_the_provider_protocol(
    fake_settings: Settings, mock_client_factory
) -> None:
    provider = _provider(
        fake_settings,
        mock_client_factory,
        lambda request: httpx.Response(200, json=ok_body()),  # pragma: no cover
    )
    assert isinstance(provider, ImageProvider)
