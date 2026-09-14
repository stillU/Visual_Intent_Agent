"""FakeImageProvider 测试（Step 08）：确定性、脚本化、离线、耗尽行为。

全部离线：构造函数不读 `Settings`、不触网（本文件不 import httpx）。
"""

from __future__ import annotations

import pytest

from visual_intent_agent.providers.errors import PROVIDER_INVALID_REQUEST, ProviderError
from visual_intent_agent.providers.fake_image import (
    FAKE_PNG_BYTES,
    FakeImageProvider,
)
from visual_intent_agent.providers.image import (
    GeneratedImage,
    ImageGenerationResult,
    IMAGE_MIME_TYPE_PNG,
)

from image_helpers import PNG_BYTES, image_request


def test_default_mode_returns_the_constant_on_every_call() -> None:
    provider = FakeImageProvider()
    for _ in range(3):
        result = provider.generate(image_request())
        assert result.images[0].content == FAKE_PNG_BYTES
    assert len(provider.requests) == 3


def test_requests_record_the_exact_input_object() -> None:
    provider = FakeImageProvider()
    request = image_request(prompt="a dog", size="512x512", model="qwen-image-3.0")
    provider.generate(request)
    assert provider.requests == [request]
    assert provider.requests[0].prompt == "a dog"
    assert provider.requests[0].size == "512x512"


def test_same_input_is_byte_identical_across_instances() -> None:
    first = FakeImageProvider().generate(image_request())
    second = FakeImageProvider().generate(image_request())
    assert first.images[0].content == second.images[0].content == PNG_BYTES


def test_scripted_bytes_are_wrapped_into_a_deterministic_result() -> None:
    provider = FakeImageProvider([b"one", b"two"])
    assert provider.generate(image_request()).images[0].content == b"one"
    assert provider.generate(image_request()).images[0].content == b"two"


def test_scripted_results_are_returned_verbatim() -> None:
    result = ImageGenerationResult(
        model="scripted-model",
        provider_request_id="req_scripted",
        images=[GeneratedImage(content=b"payload", mime_type=IMAGE_MIME_TYPE_PNG)],
        seed=7,
    )
    provider = FakeImageProvider([result])
    assert provider.generate(image_request()) is result


def test_callable_script_can_raise_a_provider_error() -> None:
    def handler(request):  # noqa: ANN001 - 测试内联回调
        raise ProviderError.rate_limited("simulated upstream throttling")

    provider = FakeImageProvider(handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    assert excinfo.value.code == "provider.rate_limited"
    assert excinfo.value.retryable is True


def test_exhausted_script_raises_instead_of_silently_succeeding() -> None:
    provider = FakeImageProvider([])  # 显式空脚本 != 默认常量模式
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    assert excinfo.value.code == PROVIDER_INVALID_REQUEST


def test_invalid_script_entry_raises_invalid_request() -> None:
    provider = FakeImageProvider(["not-bytes"])  # type: ignore[list-item]
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(image_request())
    assert excinfo.value.code == PROVIDER_INVALID_REQUEST


def test_wrong_request_type_is_rejected_without_recording() -> None:
    provider = FakeImageProvider()
    with pytest.raises(ProviderError) as excinfo:
        provider.generate({"prompt": "x"})  # type: ignore[arg-type]
    assert excinfo.value.code == PROVIDER_INVALID_REQUEST
    assert provider.requests == []


def test_fake_never_fabricates_a_seed_even_when_scripted() -> None:
    provider = FakeImageProvider([FAKE_PNG_BYTES])
    assert provider.generate(image_request()).seed is None
