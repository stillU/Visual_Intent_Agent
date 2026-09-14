"""Provider 错误分类（Step 05 建，Step 08 复用；ARCHITECTURE.md 6.3）。

真实 adapter 的 HTTP / 网络异常**一律**转换为本模块的 `ProviderError`，
绝不把 httpx 异常类型、响应体中的敏感内容或 Authorization 头泄露给调用方。

错误六分类（冻结，ARCHITECTURE.md 6.3）：

    provider.auth                 401/403                       不可重试
    provider.rate_limited         429                           可重试
    provider.timeout              连接/读/写/池超时              可重试
    provider.network              连接失败、DNS 失败等传输错误    可重试
    provider.server_error         5xx                           可重试
    provider.invalid_request      其余 4xx                      不可重试
    provider.unparseable_response 响应 JSON 结构不符             不可重试

重试策略（两个真实 adapter 一致的最小策略）：仅 `retryable=True` 的类别，最多
`Settings.http_max_retries` 次，指数退避 `0.5s × 2^n`；重试用尽后抛最后一次错误。

Issue code 命名空间 `provider.*`（ARCHITECTURE.md 5.5）：`ProviderError.code`
可直接作为可恢复 issue 的 code 使用（Step 05 IntentEngine / Step 08 pipeline）。
"""

from __future__ import annotations

#: 可恢复（可重试）错误类别。
PROVIDER_AUTH = "provider.auth"
PROVIDER_RATE_LIMITED = "provider.rate_limited"
PROVIDER_TIMEOUT = "provider.timeout"
PROVIDER_NETWORK = "provider.network"
PROVIDER_SERVER_ERROR = "provider.server_error"
PROVIDER_INVALID_REQUEST = "provider.invalid_request"
PROVIDER_UNPARSEABLE_RESPONSE = "provider.unparseable_response"

#: 全部 code（规则表在 import 时自检，防止文档与实现漂移）。
PROVIDER_ERROR_CODES: frozenset[str] = frozenset(
    {
        PROVIDER_AUTH,
        PROVIDER_RATE_LIMITED,
        PROVIDER_TIMEOUT,
        PROVIDER_NETWORK,
        PROVIDER_SERVER_ERROR,
        PROVIDER_INVALID_REQUEST,
        PROVIDER_UNPARSEABLE_RESPONSE,
    }
)

#: code -> 是否可重试（冻结映射）。
RETRYABLE_CODES: frozenset[str] = frozenset(
    {
        PROVIDER_RATE_LIMITED,
        PROVIDER_TIMEOUT,
        PROVIDER_NETWORK,
        PROVIDER_SERVER_ERROR,
    }
)


class ProviderError(Exception):
    """Provider 边界的唯一异常类型。

    字段（ARCHITECTURE.md 4「Step 05」）：

    - `code`：`provider.*` 命名空间下的稳定 code；
    - `retryable`：是否属于可重试类别（仅由 code 决定，构造时校验一致）；
    - `status_code`：HTTP 状态码；网络/超时/解析类为 None；
    - `provider_request_id`：上游 request id（若响应提供），便于排障。

    `str(error)` 只包含 code、人类可读说明与非敏感元数据；绝不包含 key、
    Authorization 头或响应体中的凭证。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool | None = None,
        status_code: int | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        if code not in PROVIDER_ERROR_CODES:
            raise ValueError(f"unknown provider error code: {code!r}")
        expected_retryable = code in RETRYABLE_CODES
        if retryable is None:
            retryable = expected_retryable
        elif retryable != expected_retryable:
            raise ValueError(
                f"retryable={retryable!r} contradicts code {code!r} "
                f"(expected {expected_retryable!r})"
            )
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.provider_request_id = provider_request_id
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - 由 __repr__ 测试覆盖
        parts = [f"{self.code}: {self.message}"]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.provider_request_id is not None:
            parts.append(f"request_id={self.provider_request_id}")
        return " | ".join(parts)

    # -- 工厂方法（ARCHITECTURE.md 4 冻结签名） ------------------------------

    @classmethod
    def auth(
        cls,
        message: str = "provider rejected the credentials",
        *,
        status_code: int | None = None,
        provider_request_id: str | None = None,
    ) -> "ProviderError":
        return cls(
            PROVIDER_AUTH,
            message,
            status_code=status_code,
            provider_request_id=provider_request_id,
        )

    @classmethod
    def rate_limited(
        cls,
        message: str = "provider rate limited the request",
        *,
        status_code: int | None = None,
        provider_request_id: str | None = None,
    ) -> "ProviderError":
        return cls(
            PROVIDER_RATE_LIMITED,
            message,
            status_code=status_code,
            provider_request_id=provider_request_id,
        )

    @classmethod
    def timeout(
        cls,
        message: str = "provider call timed out",
        *,
        provider_request_id: str | None = None,
    ) -> "ProviderError":
        return cls(
            PROVIDER_TIMEOUT,
            message,
            provider_request_id=provider_request_id,
        )

    @classmethod
    def network(
        cls,
        message: str = "provider network error",
        *,
        provider_request_id: str | None = None,
    ) -> "ProviderError":
        return cls(
            PROVIDER_NETWORK,
            message,
            provider_request_id=provider_request_id,
        )

    @classmethod
    def server(
        cls,
        message: str = "provider server error",
        *,
        status_code: int | None = None,
        provider_request_id: str | None = None,
    ) -> "ProviderError":
        return cls(
            PROVIDER_SERVER_ERROR,
            message,
            status_code=status_code,
            provider_request_id=provider_request_id,
        )

    @classmethod
    def invalid_request(
        cls,
        message: str = "provider rejected the request",
        *,
        status_code: int | None = None,
        provider_request_id: str | None = None,
    ) -> "ProviderError":
        return cls(
            PROVIDER_INVALID_REQUEST,
            message,
            status_code=status_code,
            provider_request_id=provider_request_id,
        )

    @classmethod
    def unparseable_response(
        cls,
        message: str = "provider response did not match the expected structure",
        *,
        status_code: int | None = None,
        provider_request_id: str | None = None,
    ) -> "ProviderError":
        return cls(
            PROVIDER_UNPARSEABLE_RESPONSE,
            message,
            status_code=status_code,
            provider_request_id=provider_request_id,
        )


__all__ = [
    "ProviderError",
    "PROVIDER_ERROR_CODES",
    "RETRYABLE_CODES",
    "PROVIDER_AUTH",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_TIMEOUT",
    "PROVIDER_NETWORK",
    "PROVIDER_SERVER_ERROR",
    "PROVIDER_INVALID_REQUEST",
    "PROVIDER_UNPARSEABLE_RESPONSE",
]
