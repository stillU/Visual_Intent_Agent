"""Step 08 图像 Provider 测试共享工具（唯一命名模块；禁止 `from conftest import ...`）。

自包含、全离线：只构造请求/响应字面量，HTTP 一律用 `httpx.MockTransport`
（fixture 由 `tests/providers/conftest.py` 提供：`fake_settings`、
`mock_client_factory`、`captured_requests`）。

注意：本模块的 key / URL 都是**测试占位值**，不是真实凭据；签名 URL 只在错误脱敏
测试里出现，断言它绝不进入结果或错误文本。
"""

from __future__ import annotations

from typing import Any

from visual_intent_agent.providers.fake_image import FAKE_PNG_BYTES
from visual_intent_agent.providers.image import ImageGenerationRequest

#: 测试占位 key（不是真实凭据；仅用于断言 Authorization 头与脱敏）。
TEST_IMAGE_KEY = "test-image-key-not-a-real-secret"

#: 测试 base_url（`.invalid` 保证永不可解析）。
TEST_IMAGE_BASE_URL = "https://provider.invalid/compatible-mode/v1"

#: 模拟的 OSS 临时签名 URL（带 Expires/signature；**不得**出现在任何结果或错误文本中）。
SIGNED_IMAGE_URL = (
    "https://oss-provider.invalid/generated/out.png"
    "?Expires=9999999999&OSSAccessKeyId=placeholder&Signature=signed-secret-value"
)

#: 测试用确定性 PNG（与 FakeImageProvider 常量相同，便于断言语义一致）。
PNG_BYTES = FAKE_PNG_BYTES

#: PNG magic（smoke / 合同断言共用）。
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: 一个极小的合法 JPEG 头（仅用于测试 magic 识别，不是完整可解码图片）。
JPEG_HEAD_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 8


def image_request(**overrides: Any) -> ImageGenerationRequest:
    """确定性图像请求。"""
    payload: dict[str, Any] = {"prompt": "subject: a cat", "size": "1024x1024"}
    payload.update(overrides)
    return ImageGenerationRequest(**payload)


def ok_body(
    url: str = SIGNED_IMAGE_URL,
    *,
    model: str | None = "qwen-image-3.0",
    seed: object | None = None,
    body_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict:
    """构造 `{created, data:[{url}], usage}` 形态的成功响应体。"""
    body: dict[str, Any] = {"created": 1710000000, "data": [{"url": url}], "usage": {}}
    if model is not None:
        body["model"] = model
    if seed is not None:
        body["seed"] = seed
    if body_id is not None:
        body["id"] = body_id
    if extra:
        body.update(extra)
    return body
