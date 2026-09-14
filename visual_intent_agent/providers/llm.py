"""LLM Provider 合同（Step 05；ARCHITECTURE.md 4/6.1）。

冻结面：

    LLMProvider (Protocol)
      ├ OpenAICompatibleLLMProvider   ← 唯一真实实现（openai_llm.py）
      └ FakeLLMProvider               ← 默认测试唯一依赖（fake_llm.py）

- 本模块**只**定义数据合同与 Protocol，不含任何 HTTP / Settings / 业务逻辑；
- `LLMRequest.model=None` 表示"使用 `Settings.llm_model`"（由真实 adapter 解析，
  合同层不读配置）；
- LLM 只产生业务候选内容（候选 Delta / 问题措辞 / Prompt）：ID、revision 与
  时间戳由确定性代码生成，因此本模块不承载任何 ID / 时间字段。

全部模型 `frozen=True, extra="forbid"`（ARCHITECTURE.md 5.7）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: 合法消息角色（OpenAI 兼容 chat completions 的最小集合）。
LLM_ROLES: frozenset[str] = frozenset({"system", "user", "assistant"})


class LLMMessage(BaseModel):
    """一条 chat 消息。"""

    model_config = _FROZEN

    role: str
    content: str

    def as_payload(self) -> dict[str, str]:
        """转换为 OpenAI 兼容请求体中的消息对象（确定性字段顺序）。"""
        return {"role": self.role, "content": self.content}


class LLMUsage(BaseModel):
    """token 用量；Provider 不返回时为 None（禁止伪造）。"""

    model_config = _FROZEN

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class LLMRequest(BaseModel):
    """一次受限 LLM 调用。

    - `model=None` → 真实 adapter 使用 `Settings.llm_model`；
    - `temperature` / `max_tokens` / `response_format` 为 None 时不发送对应字段
      （避免向上游传空值改变默认行为）。
    """

    model_config = _FROZEN

    messages: list[LLMMessage] = Field(default_factory=list)
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: dict | None = None


class LLMResponse(BaseModel):
    """一次 LLM 调用的结构化返回。

    `content` 是模型文本（结构化输出时即 JSON 文本）；`model` 是实际上游模型名
    （响应未提供时由 adapter 回填请求模型）；`provider_request_id` 便于排障；
    `usage` 可为 None。
    """

    model_config = _FROZEN

    content: str
    model: str
    provider_request_id: str | None = None
    usage: LLMUsage | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """唯一 LLM Provider interface（Step 05 冻结；Step 09 复用同一接口）。

    `runtime_checkable` 仅用于合同测试（Fake 与真实 adapter 都能通过
    `isinstance(x, LLMProvider)`），不改变调用面。
    """

    def complete(self, request: LLMRequest) -> LLMResponse:
        """执行一次 chat completion；失败抛 `ProviderError`。"""
        ...


__all__ = [
    "LLMMessage",
    "LLMUsage",
    "LLMRequest",
    "LLMResponse",
    "LLMProvider",
    "LLM_ROLES",
]
