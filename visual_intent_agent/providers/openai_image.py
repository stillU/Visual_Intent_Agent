"""OpenAI 兼容图像生成 adapter（Step 08 唯一的真实图像 Provider）。

    POST {base_url}/images/generations
    GET  <响应中的临时签名 URL>            # adapter 在响应内完成下载

冻结策略（ARCHITECTURE.md 4「Step 08」、6.2、6.3）：

- 唯一 HTTP 客户端是已安装的 `httpx`；不引入 openai SDK / 图像 SDK；
- 超时使用 `Settings.image_timeout_seconds`（**含下载**）；
- 响应 `{created, data:[{url}], usage}` 中的 `url` 是带 `Expires` 的 OSS 临时签名地址：
  **必须在本 adapter 内下载字节**，`ImageGenerationResult` 只承载字节，系统任何位置
  不得只存 URL；
- **禁止**发送 `X-DashScope-Async` 等异步头（该 key 403；本端点为同步调用）；
- 错误六分类见 `providers/errors.py`；仅可重试类别按 `0.5s × 2^n` 退避，最多
  `Settings.http_max_retries` 次；**下载失败只重试下载**（不重新 POST，避免重复计费/重复出图）；
- 下载签名 URL 时**绝不携带 Authorization 头**（签名 URL 属于第三方 OSS 主机，
  携带 key 等于把凭据泄露给该主机）；
- `seed`：响应没有该字段就是 `None`，禁止伪造；`provider_request_id` 同理；
- 异常与日志绝不包含 key、Authorization 头、完整临时 URL；错误信息中的响应体片段
  先做 key + URL 脱敏再截断。

本模块不读 `.env`、不直接读进程环境变量：全部配置经构造参数 `Settings` 注入。
"""

from __future__ import annotations

import json
import re
import time

import httpx

from visual_intent_agent.config import Settings

from .errors import ProviderError
from .image import (
    IMAGE_MIME_TYPE_OCTET_STREAM,
    GeneratedImage,
    ImageGenerationRequest,
    ImageGenerationResult,
)

#: 指数退避基数：0.5s × 2^n（ARCHITECTURE.md 6.3 冻结）。
BACKOFF_BASE_SECONDS: float = 0.5

#: 错误信息中允许出现的响应体片段长度上限（先脱敏再截断）。
_BODY_EXCERPT_LIMIT = 200

#: 任何出现在错误文本里的 http(s) URL（含签名 query）都替换掉：临时 URL 不得外泄。
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>\\]+")

#: 下载时的请求头：只声明可接受的媒体类型，**不带** Authorization。
_DOWNLOAD_HEADERS = {"Accept": "image/*, application/octet-stream"}

#: 常见图片格式 magic（仅在响应头不可信/缺失时用于诚实标注，不伪造）。
_MAGIC_MIME_TYPES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _request_id_from(response: httpx.Response) -> str | None:
    """取上游 request id：优先响应头，其次响应体的 `id` 字段。"""
    for header in ("x-request-id", "x-dashscope-request-id", "request-id"):
        value = response.headers.get(header)
        if value:
            return value
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        identifier = body.get("id")
        if isinstance(identifier, str) and identifier:
            return identifier
    return None


def _detect_mime_type(content: bytes, content_type: str | None) -> str:
    """确定图片 MIME：优先响应头，其次 magic bytes，最后诚实的 octet-stream 回退。

    只依据响应头与字节本身，不做任何格式猜测；PNG/JPEG/GIF 的 magic 是确定事实。
    """
    if content_type:
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type.startswith("image/"):
            return media_type
    for magic, mime_type in _MAGIC_MIME_TYPES:
        if content.startswith(magic):
            return mime_type
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return IMAGE_MIME_TYPE_OCTET_STREAM


class OpenAIImageProvider:
    """唯一真实图像 adapter（实现 `ImageProvider` Protocol）。

    `client` 由调用方注入时（测试的 `httpx.MockTransport`）adapter 不拥有它，
    也不会关闭它；为 None 时按 `Settings.image_timeout_seconds` 新建 `httpx.Client`。
    """

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.Client(timeout=settings.image_timeout_seconds)
        )

    # -- 公开面 ------------------------------------------------------------

    @property
    def images_generations_url(self) -> str:
        """本 adapter 唯一 POST 的端点（供测试与日志使用，不含凭据）。"""
        return f"{self._settings.provider_base_url}/images/generations"

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """执行一次图像生成并下载字节；失败抛 `ProviderError`（不泄露 httpx 异常）。"""
        if not isinstance(request, ImageGenerationRequest):
            raise ProviderError.invalid_request(
                "OpenAIImageProvider.generate expects an ImageGenerationRequest"
            )
        payload = self._build_payload(request)
        headers = self._build_headers()
        url = self.images_generations_url
        max_retries = self._settings.http_max_retries
        attempt = 0

        while True:
            error: ProviderError | None = None
            try:
                response = self._client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException:
                error = ProviderError.timeout(
                    "image generation timed out "
                    f"after {self._settings.image_timeout_seconds}s"
                )
            except httpx.TransportError as exc:
                error = ProviderError.network(
                    f"image generation transport error ({type(exc).__name__})"
                )
            except httpx.HTTPError as exc:
                error = ProviderError.network(
                    f"image generation HTTP client error ({type(exc).__name__})"
                )
            else:
                error = self._classify_status(response, what="image generation")
                if error is None:
                    return self._parse_and_download(response, request)

            if error.retryable and attempt < max_retries:
                time.sleep(BACKOFF_BASE_SECONDS * (2**attempt))
                attempt += 1
                continue
            raise error

    def close(self) -> None:
        """关闭内部持有的 httpx client（注入的 client 不关闭）。"""
        if self._owns_client:
            self._client.close()

    # -- 内部实现 ----------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        """生成端点请求头。

        **只在这里**读取一次 key；不添加任何 `async` 头（该 key 会 403）。
        """
        return {
            "Authorization": f"Bearer {self._settings.provider_api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _build_payload(self, request: ImageGenerationRequest) -> dict:
        return {
            "model": request.model or self._settings.image_model,
            "prompt": request.prompt,
            "size": request.size,
        }

    def _redact(self, text: str) -> str:
        """脱敏：先替换 key，再替换任何 URL（含签名 query），最后截断。"""
        secret = self._settings.provider_api_key.get_secret_value()
        if secret and secret in text:
            text = text.replace(secret, "***")
        text = _URL_PATTERN.sub("<url>", text)
        return text[:_BODY_EXCERPT_LIMIT]

    def _body_excerpt(self, response: httpx.Response) -> str:
        try:
            text = response.text
        except Exception:  # pragma: no cover - 防御：读取响应体失败不应掩盖主错误
            return ""
        return self._redact(text)

    def _classify_status(self, response: httpx.Response, *, what: str) -> ProviderError | None:
        """2xx → None；其余按冻结六分类返回 `ProviderError`（错误文本已脱敏）。"""
        status = response.status_code
        if 200 <= status < 300:
            return None
        request_id = _request_id_from(response)
        excerpt = self._body_excerpt(response)
        detail = f"; body={excerpt!r}" if excerpt else ""
        if status in (401, 403):
            return ProviderError.auth(
                f"{what} rejected the credentials (status={status}){detail}",
                status_code=status,
                provider_request_id=request_id,
            )
        if status == 429:
            return ProviderError.rate_limited(
                f"{what} was rate limited (status=429){detail}",
                status_code=status,
                provider_request_id=request_id,
            )
        if 500 <= status < 600:
            return ProviderError.server(
                f"{what} failed on the provider side (status={status}){detail}",
                status_code=status,
                provider_request_id=request_id,
            )
        return ProviderError.invalid_request(
            f"{what} request was rejected (status={status}){detail}",
            status_code=status,
            provider_request_id=request_id,
        )

    def _parse_and_download(
        self, response: httpx.Response, request: ImageGenerationRequest
    ) -> ImageGenerationResult:
        """解析图像响应并在**同一 adapter 内**下载每一张图片的字节。"""
        request_id = _request_id_from(response)
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            raise ProviderError.unparseable_response(
                "image generation response body is not valid JSON",
                status_code=response.status_code,
                provider_request_id=request_id,
            ) from None
        if not isinstance(body, dict):
            raise ProviderError.unparseable_response(
                "image generation response is not a JSON object",
                status_code=response.status_code,
                provider_request_id=request_id,
            )
        data = body.get("data")
        if not isinstance(data, list) or not data:
            raise ProviderError.unparseable_response(
                "image generation response carries no data entries",
                status_code=response.status_code,
                provider_request_id=request_id,
            )
        urls: list[str] = []
        for entry in data:
            url = entry.get("url") if isinstance(entry, dict) else None
            if not isinstance(url, str) or not url.strip():
                raise ProviderError.unparseable_response(
                    "image generation response entry has no usable url",
                    status_code=response.status_code,
                    provider_request_id=request_id,
                )
            urls.append(url)

        reported_model = body.get("model")
        model = (
            reported_model
            if isinstance(reported_model, str) and reported_model.strip()
            else (request.model or self._settings.image_model)
        )
        raw_seed = body.get("seed")
        seed = raw_seed if isinstance(raw_seed, int) and not isinstance(raw_seed, bool) else None

        images = [self._download(url, request_id=request_id) for url in urls]
        return ImageGenerationResult(
            model=model,
            provider_request_id=request_id,
            images=images,
            seed=seed,
        )

    def _download(self, url: str, *, request_id: str | None) -> GeneratedImage:
        """下载一张图片的字节（独立重试；绝不携带 Authorization）。

        失败不重新 POST：POST 会再次出图/计费，重试只针对下载本身。
        """
        max_retries = self._settings.http_max_retries
        attempt = 0
        while True:
            error: ProviderError | None = None
            try:
                response = self._client.get(url, headers=_DOWNLOAD_HEADERS)
            except httpx.TimeoutException:
                error = ProviderError.timeout(
                    "image download timed out "
                    f"after {self._settings.image_timeout_seconds}s",
                    provider_request_id=request_id,
                )
            except httpx.TransportError as exc:
                error = ProviderError.network(
                    f"image download transport error ({type(exc).__name__})",
                    provider_request_id=request_id,
                )
            except httpx.HTTPError as exc:
                error = ProviderError.network(
                    f"image download HTTP client error ({type(exc).__name__})",
                    provider_request_id=request_id,
                )
            else:
                error = self._classify_status(response, what="image download")
                if error is None:
                    content = response.content
                    if not content:
                        raise ProviderError.unparseable_response(
                            "image download returned no bytes",
                            status_code=response.status_code,
                            provider_request_id=request_id,
                        )
                    return GeneratedImage(
                        content=content,
                        mime_type=_detect_mime_type(
                            content, response.headers.get("content-type")
                        ),
                    )

            if error.retryable and attempt < max_retries:
                time.sleep(BACKOFF_BASE_SECONDS * (2**attempt))
                attempt += 1
                continue
            raise error


__all__ = ["OpenAIImageProvider", "BACKOFF_BASE_SECONDS"]
