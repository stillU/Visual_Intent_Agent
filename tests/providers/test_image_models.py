"""图像 Provider 合同测试（Step 08）：模型校验、Protocol 一致性、无 URL 泄漏。

全部离线：Fake 不触网；真实 adapter 只在 `isinstance` / timeout 断言里用
`httpx.MockTransport` 构造（不发出真实请求）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from visual_intent_agent.config import Settings
from visual_intent_agent.providers.fake_image import (
    FAKE_IMAGE_MIME_TYPE,
    FAKE_IMAGE_MODEL,
    FAKE_PNG_BYTES,
    FAKE_PROVIDER_REQUEST_ID,
    FakeImageProvider,
)
from visual_intent_agent.providers.image import (
    GeneratedImage,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageProvider,
    IMAGE_MIME_TYPE_OCTET_STREAM,
    IMAGE_MIME_TYPE_PNG,
    is_valid_image_size,
)
from visual_intent_agent.providers.openai_image import OpenAIImageProvider

from image_helpers import PNG_MAGIC, image_request


# ---------------------------------------------------------------------------
# ImageGenerationRequest
# ---------------------------------------------------------------------------


def test_request_defaults_to_the_configured_model() -> None:
    request = image_request()
    assert request.model is None
    assert request.prompt == "subject: a cat"
    assert request.size == "1024x1024"


@pytest.mark.parametrize("size", ["1024x1024", "1x1", "4096x4096", "512x768"])
def test_valid_sizes_are_accepted(size: str) -> None:
    assert image_request(size=size).size == size
    assert is_valid_image_size(size) is True


@pytest.mark.parametrize(
    "size",
    [
        "1024",
        "1024X1024",
        "0x1024",
        "1024x0",
        "x1024",
        "1024x",
        "01024x1024",
        " 1024x1024",
        "1024x1024 ",
        "1024 x 1024",
        "axb",
        "1024x1024x1024",
    ],
)
def test_invalid_sizes_are_rejected_at_the_boundary(size: str) -> None:
    with pytest.raises(ValidationError):
        image_request(size=size)
    assert is_valid_image_size(size) is False


def test_blank_prompt_is_rejected() -> None:
    with pytest.raises(ValidationError):
        image_request(prompt="   ")


def test_blank_explicit_model_is_rejected() -> None:
    with pytest.raises(ValidationError):
        image_request(model="")


def test_request_is_frozen_and_forbids_extra_fields() -> None:
    request = image_request()
    with pytest.raises(ValidationError):
        request.prompt = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ImageGenerationRequest(prompt="x", size="1x1", negative_prompt="no")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# GeneratedImage / ImageGenerationResult
# ---------------------------------------------------------------------------


def test_generated_image_requires_raw_bytes() -> None:
    assert GeneratedImage(content=b"abc", mime_type="image/png").content == b"abc"
    assert GeneratedImage(content=bytearray(b"abc"), mime_type="image/png").content == b"abc"
    with pytest.raises(ValidationError):
        GeneratedImage(content="abc", mime_type="image/png")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        GeneratedImage(content=b"abc", mime_type="  ")


def test_result_requires_at_least_one_image() -> None:
    with pytest.raises(ValidationError):
        ImageGenerationResult(model="m", images=[])
    result = ImageGenerationResult(
        model="m",
        images=[GeneratedImage(content=b"x", mime_type=IMAGE_MIME_TYPE_PNG)],
    )
    assert result.provider_request_id is None
    assert result.seed is None


def test_result_seed_is_never_fabricated_and_never_a_bool() -> None:
    image = GeneratedImage(content=b"x", mime_type=IMAGE_MIME_TYPE_PNG)
    assert ImageGenerationResult(model="m", images=[image], seed=None).seed is None
    assert ImageGenerationResult(model="m", images=[image], seed=42).seed == 42
    with pytest.raises(ValidationError):
        ImageGenerationResult(model="m", images=[image], seed=True)  # type: ignore[arg-type]


def test_result_has_no_url_field_anywhere() -> None:
    assert "url" not in ImageGenerationResult.model_fields
    assert "url" not in GeneratedImage.model_fields
    assert "url" not in ImageGenerationRequest.model_fields


def test_result_is_frozen_and_forbids_extra_fields() -> None:
    image = GeneratedImage(content=b"x", mime_type=IMAGE_MIME_TYPE_PNG)
    result = ImageGenerationResult(model="m", images=[image])
    with pytest.raises(ValidationError):
        result.seed = 1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ImageGenerationResult(model="m", images=[image], urls=["http://x"])  # type: ignore[call-arg]


def test_octet_stream_constant_is_the_honest_fallback() -> None:
    assert IMAGE_MIME_TYPE_OCTET_STREAM == "application/octet-stream"
    assert IMAGE_MIME_TYPE_PNG == "image/png"


# ---------------------------------------------------------------------------
# Fake 常量与 Protocol 一致性
# ---------------------------------------------------------------------------


def test_fake_png_constant_is_a_valid_small_png() -> None:
    assert FAKE_PNG_BYTES.startswith(PNG_MAGIC)
    assert FAKE_PNG_BYTES.endswith(b"IEND\xaeB`\x82")
    assert len(FAKE_PNG_BYTES) == 70
    assert len(FAKE_PNG_BYTES) < 256  # 小常量，适合逐字节断言


def test_both_providers_satisfy_the_image_provider_protocol() -> None:
    settings = Settings(
        provider_base_url="https://provider.invalid/v1",
        provider_api_key="test-image-key-not-a-real-secret",
    )
    real = OpenAIImageProvider(settings)
    try:
        assert isinstance(real, ImageProvider)
    finally:
        real.close()
    assert isinstance(FakeImageProvider(), ImageProvider)


def test_fake_default_result_is_deterministic_and_carries_no_fabricated_seed() -> None:
    provider = FakeImageProvider()
    request = image_request()
    first = provider.generate(request)
    second = provider.generate(request)
    assert first.model == FAKE_IMAGE_MODEL
    assert first.provider_request_id == FAKE_PROVIDER_REQUEST_ID
    assert first.seed is None
    assert first.images[0].mime_type == FAKE_IMAGE_MIME_TYPE
    assert first.images[0].content == FAKE_PNG_BYTES
    assert first == second
    assert provider.requests == [request, request]
