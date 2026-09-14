"""图像 Provider 合同（Step 08；ARCHITECTURE.md 4「Step 08」、6.1、6.4）。

冻结形状（逐字对照冻结表）：

    ImageGenerationRequest  prompt / size / model(None -> Settings.image_model)
    GeneratedImage          content(bytes) / mime_type
    ImageGenerationResult   model / provider_request_id / images / seed
    ImageProvider(Protocol) generate(ImageGenerationRequest) -> ImageGenerationResult

冻结语义：

- **字节级结果**：`ImageGenerationResult` 只承载图片**字节**，没有 URL 字段。
  Provider 的临时签名 URL 属于 adapter 内部实现细节（ARCHITECTURE.md 6.2：
  "adapter 必须在响应内下载字节，系统任何位置不得只存 URL"）。
- `size` 必须是 `"<w>x<h>"`（ARCHITECTURE.md 6.2：该端点要求该形态）；
  非法尺寸在边界被显式拒绝，不做任何猜测修复。
- `seed`：Provider 不返回时为 `None`，**禁止伪造**。
- `provider_request_id`：Provider 不提供时为 `None`，**禁止伪造**。

本模块只定义合同与唯一 Protocol，不含任何 HTTP 实现（真实实现在
`providers/openai_image.py`，测试实现在 `providers/fake_image.py`）。
`providers/__init__.py` 保持空白（多 Step 拥有），因此下游一律从本模块完整路径导入。
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: `ImageGenerationRequest.size` 的唯一合法形态：`"<width>x<height>"`，正整数字面量、
#: 不允许前导零 / 空白 / 大小写 x（与 provider 事实 ARCHITECTURE.md 6.2 一致）。
IMAGE_SIZE_PATTERN = re.compile(r"^[1-9]\d{0,4}x[1-9]\d{0,4}$")

#: 默认图片 MIME（唯一真实 Provider 返回 PNG；Fake 也返回 PNG 常量）。
IMAGE_MIME_TYPE_PNG = "image/png"

#: 未知格式时的诚实回退（不猜测、不伪造图片格式）。
IMAGE_MIME_TYPE_OCTET_STREAM = "application/octet-stream"


def is_valid_image_size(size: str) -> bool:
    """`size` 是否为合法的 `"<w>x<h>"`（纯函数；不抛异常）。"""
    return isinstance(size, str) and IMAGE_SIZE_PATTERN.fullmatch(size) is not None


class ImageGenerationRequest(BaseModel):
    """一次图像生成请求：目标 Prompt + 尺寸 + 目标模型。

    - `prompt`：已确认 PromptArtifact 的文本，原样传递（adapter 不重写用户要求）；
    - `size`：`"<w>x<h>"`，来自 `PromptArtifact.parameters.size`；
    - `model`：`None` 表示使用 `Settings.image_model`（唯一目标图像模型）。
    """

    model_config = _FROZEN

    prompt: str
    size: str
    model: str | None = None

    @field_validator("prompt")
    @classmethod
    def _prompt_must_not_be_blank(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("prompt must be a non-empty string")
        return value

    @field_validator("size")
    @classmethod
    def _size_must_be_width_x_height(cls, value: str) -> str:
        if not is_valid_image_size(value):
            raise ValueError(
                'size must use the "<width>x<height>" form with positive integers, '
                f"got {value!r}"
            )
        return value

    @field_validator("model")
    @classmethod
    def _model_must_not_be_blank_when_set(cls, value: str | None) -> str | None:
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("model must be None or a non-empty string")
        return value


class GeneratedImage(BaseModel):
    """一张已下载到内存的生成图片（字节 + MIME）。

    只有字节：adapter 在响应内完成 URL→字节的下载，系统任何位置都不得保存临时 URL。
    """

    model_config = _FROZEN

    content: bytes
    mime_type: str

    @field_validator("content", mode="before")
    @classmethod
    def _content_must_be_raw_bytes(cls, value: object) -> bytes:
        # pydantic 的 lax 模式会把 str 编码成 bytes；这里显式拒绝，避免文本被当成图片。
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        raise ValueError("content must be raw bytes")

    @field_validator("mime_type")
    @classmethod
    def _mime_type_must_not_be_blank(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("mime_type must be a non-empty string")
        return value


class ImageGenerationResult(BaseModel):
    """一次图像生成的规范化结果（字节级；不含 URL）。

    - `model`：Provider 报告的模型标识（Provider 未报告时由 adapter 用请求/配置值回填，
      不伪造版本号）；
    - `provider_request_id`：上游 request id；Provider 不提供则 `None`；
    - `images`：至少一张，顺序即 Provider 返回顺序；
    - `seed`：Provider 不返回则 `None`，**禁止伪造**。
    """

    model_config = _FROZEN

    model: str
    provider_request_id: str | None = None
    images: list[GeneratedImage] = Field(min_length=1)
    seed: int | None = None

    @field_validator("model")
    @classmethod
    def _model_must_not_be_blank(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("model must be a non-empty string")
        return value

    @field_validator("seed", mode="before")
    @classmethod
    def _seed_must_be_an_int_when_present(cls, value: object) -> int | None:
        # mode="before"：pydantic 的 lax 模式会把 bool 强制转成 int，必须先显式拒绝。
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("seed must be None or an int (never fabricated, never a bool)")
        return value


@runtime_checkable
class ImageProvider(Protocol):
    """唯一图像生成接口（真实实现与 Fake 都必须满足它）。

    调用失败一律以 `providers.errors.ProviderError` 抛出（六分类见 ARCHITECTURE.md 6.3），
    绝不外泄 httpx 异常类型或敏感头。
    """

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...


__all__ = [
    "ImageGenerationRequest",
    "GeneratedImage",
    "ImageGenerationResult",
    "ImageProvider",
    "IMAGE_SIZE_PATTERN",
    "IMAGE_MIME_TYPE_PNG",
    "IMAGE_MIME_TYPE_OCTET_STREAM",
    "is_valid_image_size",
]
