"""OpenAI 兼容 chat completions adapter（Step 05 唯一的真实 LLM Provider）。

    POST {base_url}/chat/completions

冻结策略（ARCHITECTURE.md 6.2 / 6.3）：

- 唯一 HTTP 客户端是已安装的 `httpx`；不引入 openai SDK；
- 超时使用 `Settings.llm_timeout_seconds`；
- 错误六分类见 `providers/errors.py`；仅可重试类别按 `0.5s × 2^n` 退避，
  最多 `Settings.http_max_retries` 次；
- **禁止**发送 `X-DashScope-Async` 等异步头（该 key 403；本端点为同步调用）；
- `Authorization` 头只在构造请求时调用一次 `get_secret_value()`；异常与日志
  绝不包含 key 或 Authorization 头（错误信息中的响应体片段会先做 key 脱敏）。

本模块不读 `.env`、不直接读进程环境变量：全部配置经构造参数 `Settings` 注入。
"""

from __future__ import annotations

import json
import time

import httpx

from visual_intent_agent.config import Settings

from .errors import ProviderError
from .llm import LLMRequest, LLMResponse, LLMUsage

#: 指数退避基数：0.5s × 2^n（ARCHITECTURE.md 6.3 冻结）。
BACKOFF_BASE_SECONDS: float = 0.5

#: 错误信息中允许出现的响应体片段长度上限（先脱敏再截断）。
_BODY_EXCERPT_LIMIT = 200


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


def _parse_usage(raw: object) -> LLMUsage | None:
    """把上游 usage 转成 `LLMUsage`；结构不符或缺失返回 None（禁止伪造）。"""
    if not isinstance(raw, dict):
        return None

    def _int_or_none(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return LLMUsage(
        prompt_tokens=_int_or_none(raw.get("prompt_tokens")),
        completion_tokens=_int_or_none(raw.get("completion_tokens")),
        total_tokens=_int_or_none(raw.get("total_tokens")),
    )


class OpenAICompatibleLLMProvider:
    """唯一真实 LLM adapter（实现 `LLMProvider` Protocol）。

    `client` 由调用方注入时（测试的 `httpx.MockTransport`）adapter 不拥有它，
    也不会关闭它；为 None 时按 `Settings.llm_timeout_seconds` 新建 `httpx.Client`。
    """

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.Client(timeout=settings.llm_timeout_seconds)
        )

    # -- 公开面 ------------------------------------------------------------

    @property
    def chat_completions_url(self) -> str:
        """本 adapter 唯一调用的端点（供测试与日志使用，不含凭据）。"""
        return f"{self._settings.provider_base_url}/chat/completions"

    def complete(self, request: LLMRequest) -> LLMResponse:
        """执行一次 chat completion；失败抛 `ProviderError`（不泄露 httpx 异常）。"""
        payload = self._build_payload(request)
        headers = {
            "Authorization": (
                f"Bearer {self._settings.provider_api_key.get_secret_value()}"
            ),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = self.chat_completions_url
        max_retries = self._settings.http_max_retries
        attempt = 0

        while True:
            error: ProviderError | None = None
            try:
                response = self._client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException:
                error = ProviderError.timeout(
                    "chat completion timed out "
                    f"after {self._settings.llm_timeout_seconds}s"
                )
            except httpx.TransportError as exc:
                error = ProviderError.network(
                    f"chat completion transport error ({type(exc).__name__})"
                )
            except httpx.HTTPError as exc:
                error = ProviderError.network(
                    f"chat completion HTTP client error ({type(exc).__name__})"
                )
            else:
                error = self._classify_status(response)
                if error is None:
                    return self._parse_response(response, request)

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

    def _build_payload(self, request: LLMRequest) -> dict:
        if not request.messages:
            raise ProviderError.invalid_request(
                "LLMRequest.messages must contain at least one message"
            )
        for message in request.messages:
            if message.role not in {"system", "user", "assistant"}:
                raise ProviderError.invalid_request(
                    f"unsupported chat role {message.role!r}"
                )
        payload: dict = {
            "model": request.model or self._settings.llm_model,
            "messages": [message.as_payload() for message in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format is not None:
            payload["response_format"] = request.response_format
        return payload

    def _redact(self, text: str) -> str:
        secret = self._settings.provider_api_key.get_secret_value()
        if secret and secret in text:
            text = text.replace(secret, "***")
        return text[:_BODY_EXCERPT_LIMIT]

    def _body_excerpt(self, response: httpx.Response) -> str:
        try:
            text = response.text
        except Exception:  # pragma: no cover - 防御：读取响应体失败不应掩盖主错误
            return ""
        return self._redact(text)

    def _classify_status(self, response: httpx.Response) -> ProviderError | None:
        """2xx → None；其余按冻结六分类返回 `ProviderError`。"""
        status = response.status_code
        if 200 <= status < 300:
            return None
        request_id = _request_id_from(response)
        excerpt = self._body_excerpt(response)
        detail = f"; body={excerpt!r}" if excerpt else ""
        if status in (401, 403):
            return ProviderError.auth(
                f"chat completion rejected the credentials (status={status}){detail}",
                status_code=status,
                provider_request_id=request_id,
            )
        if status == 429:
            return ProviderError.rate_limited(
                f"chat completion was rate limited (status=429){detail}",
                status_code=status,
                provider_request_id=request_id,
            )
        if 500 <= status < 600:
            return ProviderError.server(
                f"chat completion failed on the provider side (status={status}){detail}",
                status_code=status,
                provider_request_id=request_id,
            )
        return ProviderError.invalid_request(
            f"chat completion request was rejected (status={status}){detail}",
            status_code=status,
            provider_request_id=request_id,
        )

    def _parse_response(
        self, response: httpx.Response, request: LLMRequest
    ) -> LLMResponse:
        request_id = _request_id_from(response)
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            raise ProviderError.unparseable_response(
                "chat completion response body is not valid JSON",
                status_code=response.status_code,
                provider_request_id=request_id,
            ) from None
        if not isinstance(body, dict):
            raise ProviderError.unparseable_response(
                "chat completion response is not a JSON object",
                status_code=response.status_code,
                provider_request_id=request_id,
            )
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError.unparseable_response(
                "chat completion response has no choices",
                status_code=response.status_code,
                provider_request_id=request_id,
            )
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError.unparseable_response(
                "chat completion response carries no textual content "
                "(reasoning-only or truncated output)",
                status_code=response.status_code,
                provider_request_id=request_id,
            )
        model = body.get("model")
        if not isinstance(model, str) or not model:
            model = request.model or self._settings.llm_model
        return LLMResponse(
            content=content,
            model=model,
            provider_request_id=request_id or (
                body.get("id") if isinstance(body.get("id"), str) else None
            ),
            usage=_parse_usage(body.get("usage")),
        )


__all__ = ["OpenAICompatibleLLMProvider", "BACKOFF_BASE_SECONDS"]
