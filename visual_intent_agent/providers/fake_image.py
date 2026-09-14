"""FakeImageProvider：默认测试唯一图像依赖（ARCHITECTURE.md 6.4）。

- 与真实 adapter 实现同一 `ImageProvider` Protocol，构造函数不读 `Settings`、不触网
  （模块 import 也不触网）；
- 记录全部入参（`.requests`）供断言；
- 默认返回**确定性小 PNG 字节常量** `FAKE_PNG_BYTES`（70 字节、1×1 RGBA、
  magic `\\x89PNG\\r\\n\\x1a\\n`），对相同输入逐字节确定；
- 没有 `seed`：`ImageGenerationResult.seed` 恒为 `None`（Provider 不返回就不得伪造，
  Fake 也遵守同一合同）；
- 脚本耗尽或脚本返回非法类型时抛 `ProviderError.invalid_request`（不静默返回空）。

位置冻结：`visual_intent_agent/providers/fake_image.py`（Step 08）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .errors import ProviderError
from .image import (
    IMAGE_MIME_TYPE_PNG,
    GeneratedImage,
    ImageGenerationRequest,
    ImageGenerationResult,
)

#: Fake 结果的固定模型名（确定性；不冒充真实模型版本）。
FAKE_IMAGE_MODEL = "fake-image"

#: Fake 结果的固定 MIME。
FAKE_IMAGE_MIME_TYPE = IMAGE_MIME_TYPE_PNG

#: Fake 的固定 provider request id（确定性；仅测试可见，不是真实上游 id）。
FAKE_PROVIDER_REQUEST_ID = "fake-image-request-0001"

#: 确定性小 PNG 常量：70 字节、1×1 RGBA（红色像素）、合法 CRC。
#: 验证过 IHDR/IDAT/IEND 三个 chunk 的 CRC，且 magic 为 `\\x89PNG\\r\\n\\x1a\\n`。
FAKE_PNG_BYTES: bytes = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f00050001ff56c72f0d"
    "0000000049454e44ae426082"
)


class FakeImageProvider:
    """脚本化图像 Provider（默认常量；队列或回调）。

    - `responses is None`（默认）：每次调用都返回确定性默认结果；
    - `responses` 为可迭代对象：按顺序返回（元素可以是 `ImageGenerationResult` 或
      `bytes`），耗尽后抛 `ProviderError.invalid_request`；
    - `responses` 为回调：完全由测试控制返回，也可在其中抛 `ProviderError` 模拟失败。
    """

    def __init__(
        self,
        responses: (
            Iterable[ImageGenerationResult | bytes]
            | Callable[[ImageGenerationRequest], ImageGenerationResult | bytes]
            | None
        ) = None,
        *,
        model: str = FAKE_IMAGE_MODEL,
        mime_type: str = FAKE_IMAGE_MIME_TYPE,
        provider_request_id: str | None = FAKE_PROVIDER_REQUEST_ID,
        seed: int | None = None,
    ) -> None:
        self.requests: list[ImageGenerationRequest] = []
        self._model = model
        self._mime_type = mime_type
        self._provider_request_id = provider_request_id
        self._seed = seed
        if callable(responses):
            self._handler: (
                Callable[[ImageGenerationRequest], ImageGenerationResult | bytes] | None
            ) = responses
            self._queue: list[ImageGenerationResult | bytes] | None = None
        elif responses is None:
            self._handler = None
            self._queue = None  # None = 每次都返回默认常量
        else:
            self._handler = None
            self._queue = list(responses)
        self._index = 0

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """返回脚本中的下一张图片（或默认常量）；脚本耗尽抛 `ProviderError`。"""
        if not isinstance(request, ImageGenerationRequest):
            raise ProviderError.invalid_request(
                "FakeImageProvider.generate expects an ImageGenerationRequest"
            )
        self.requests.append(request)

        if self._handler is not None:
            return self._coerce(self._handler(request))
        if self._queue is None:
            return self._default_result()
        if self._index >= len(self._queue):
            raise ProviderError.invalid_request(
                "FakeImageProvider script is exhausted; "
                f"received {self._index + 1} call(s) for {len(self._queue)} response(s)"
            )
        result = self._queue[self._index]
        self._index += 1
        return self._coerce(result)

    # -- 内部实现 ----------------------------------------------------------

    def _default_result(self) -> ImageGenerationResult:
        return ImageGenerationResult(
            model=self._model,
            provider_request_id=self._provider_request_id,
            images=self._image_from_bytes(FAKE_PNG_BYTES),
            seed=self._seed,
        )

    def _image_from_bytes(self, content: bytes) -> list[GeneratedImage]:
        return [GeneratedImage(content=bytes(content), mime_type=self._mime_type)]

    def _coerce(self, result: ImageGenerationResult | bytes) -> ImageGenerationResult:
        if isinstance(result, ImageGenerationResult):
            return result
        if isinstance(result, (bytes, bytearray, memoryview)):
            return ImageGenerationResult(
                model=self._model,
                provider_request_id=self._provider_request_id,
                images=self._image_from_bytes(bytes(result)),
                seed=self._seed,
            )
        raise ProviderError.invalid_request(
            "FakeImageProvider script entries must be bytes or ImageGenerationResult; "
            f"got {type(result).__name__}"
        )


__all__ = [
    "FakeImageProvider",
    "FAKE_PNG_BYTES",
    "FAKE_IMAGE_MODEL",
    "FAKE_IMAGE_MIME_TYPE",
    "FAKE_PROVIDER_REQUEST_ID",
]
