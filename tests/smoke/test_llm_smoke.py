"""Step 05 真实 Provider smoke（`@pytest.mark.smoke`；默认运行被 pyproject 排除）。

规则（ARCHITECTURE.md 6.5）：

- 只在显式 `uv run pytest -m smoke` 时运行；无凭据时自动 skip；
- 总真实调用数刻意控制在 2 次：一次验证 adapter 的结构化输出格式，一次验证
  Interpreter 能把真实模型响应解析为 `InterpreterResult`；
- 断言与日志绝不出现明文 key；不做网络容错重试（adapter 内部重试策略除外）。

smoke 不是任何 Step 的验收条件；失败时记录错误分类与恢复行为，不伪造通过。
"""

from __future__ import annotations

import json

import pytest

from visual_intent_agent.config import has_provider_credentials, load_settings
from visual_intent_agent.domain import SubjectFacet, VisualIntent
from visual_intent_agent.intent_engine import Interpreter, IntentResolveRequest
from visual_intent_agent.intent_engine.prompts import INTERPRETER_SYSTEM_PROMPT_V1
from visual_intent_agent.providers.llm import LLMMessage, LLMRequest
from visual_intent_agent.providers.openai_llm import OpenAICompatibleLLMProvider

pytestmark = pytest.mark.smoke


@pytest.fixture
def settings():
    if not has_provider_credentials():
        pytest.skip("smoke credentials not configured (.env / env vars missing)")
    return load_settings()


def test_real_chat_completions_returns_json_text(settings) -> None:
    """真实端点能按 `response_format={'type': 'json_object'}` 返回可解析 JSON 文本。"""
    provider = OpenAICompatibleLLMProvider(settings)
    try:
        response = provider.complete(
            LLMRequest(
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            INTERPRETER_SYSTEM_PROMPT_V1
                            + "\nFor this smoke call, return the single JSON object "
                            '{"candidate_deltas": []} and nothing else.'
                        ),
                    ),
                    LLMMessage(role="user", content="保持现状，不做任何修改。"),
                ],
                temperature=0.0,
                max_tokens=256,
                response_format={"type": "json_object"},
            )
        )
    finally:
        provider.close()

    assert response.content.strip()
    assert response.model
    # 结构化输出合同：可被 JSON 解析（由 Interpreter 再做严格 Schema 校验）。
    payload = json.loads(response.content)
    assert isinstance(payload, dict)
    assert "candidate_deltas" in payload


def test_real_interpreter_parses_a_structured_response(settings) -> None:
    """真实模型响应能被 Interpreter 解析为有证据的 Candidate Delta。"""
    provider = OpenAICompatibleLLMProvider(settings)
    try:
        interpreter = Interpreter(provider)
        request = IntentResolveRequest(
            current_intent=VisualIntent(
                subject=SubjectFacet(description="a woman in a red coat")
            ),
            message_id="msg_smoke_0001",
            message_text="镜头拉远一点，人物保持不变。",
            pending_question=None,
            available_message_ids=frozenset({"msg_smoke_0001"}),
        )
        result = interpreter.interpret(request)
    finally:
        provider.close()

    assert isinstance(result.candidate_deltas, list)
    # 不强制结果条数（模型可能有不同切分），但每条 Delta 必须有系统重填的证据。
    for delta in result.candidate_deltas:
        assert delta.evidence_refs
        assert all(
            ref.message_id == "msg_smoke_0001" for ref in delta.evidence_refs
        )
