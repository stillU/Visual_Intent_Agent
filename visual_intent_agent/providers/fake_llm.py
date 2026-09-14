"""FakeLLMProvider：默认测试唯一依赖（ARCHITECTURE.md 6.4）。

- 与真实 adapter 实现同一 `LLMProvider` Protocol，构造函数不读 `Settings`、不触网；
- 记录全部入参（`.requests`）供断言；
- 对相同输入逐字节确定（脚本化响应，无随机数、无时钟、无环境依赖）；
- 队列耗尽或脚本返回非法类型时抛 `ProviderError.invalid_request`（不静默返回空）；
- 传入 `Callable` 时由测试完全控制返回（也可在其中抛 `ProviderError` 模拟失败）。

位置冻结：`visual_intent_agent/providers/fake_llm.py`（Step 08 的 FakeImageProvider
是另一个类，二者不共享实现细节）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .errors import ProviderError
from .llm import LLMRequest, LLMResponse

#: Fake 响应的固定模型名（确定性）。
FAKE_LLM_MODEL = "fake-llm"


class FakeLLMProvider:
    """脚本化 LLM Provider（队列或回调）。"""

    def __init__(
        self,
        responses: Iterable[LLMResponse | str] | Callable[[LLMRequest], LLMResponse | str],
    ) -> None:
        self.requests: list[LLMRequest] = []
        if callable(responses):
            self._handler: Callable[[LLMRequest], LLMResponse | str] | None = responses
            self._queue: list[LLMResponse | str] | None = None
        else:
            self._handler = None
            self._queue = list(responses)
        self._index = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        """返回脚本中的下一条响应；脚本耗尽抛 `ProviderError.invalid_request`。"""
        if not isinstance(request, LLMRequest):
            raise ProviderError.invalid_request(
                "FakeLLMProvider.complete expects an LLMRequest"
            )
        self.requests.append(request)

        if self._handler is not None:
            result = self._handler(request)
        else:
            assert self._queue is not None
            if self._index >= len(self._queue):
                raise ProviderError.invalid_request(
                    "FakeLLMProvider script is exhausted; "
                    f"received {self._index + 1} call(s) for {len(self._queue)} response(s)"
                )
            result = self._queue[self._index]
            self._index += 1

        return self._coerce(result)

    @staticmethod
    def _coerce(result: LLMResponse | str) -> LLMResponse:
        if isinstance(result, LLMResponse):
            return result
        if isinstance(result, str):
            return LLMResponse(content=result, model=FAKE_LLM_MODEL)
        raise ProviderError.invalid_request(
            "FakeLLMProvider script entries must be str or LLMResponse; "
            f"got {type(result).__name__}"
        )


__all__ = ["FakeLLMProvider", "FAKE_LLM_MODEL"]
