"""LLM Provider 合同模型测试（冻结形状、frozen/extra=forbid、Protocol 一致性）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.providers.llm import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)


def test_llm_message_shape_and_payload() -> None:
    message = LLMMessage(role="user", content="镜头拉远")
    assert message.as_payload() == {"role": "user", "content": "镜头拉远"}


def test_llm_request_defaults_are_all_optional() -> None:
    request = LLMRequest(messages=[LLMMessage(role="user", content="hi")])
    assert request.model is None
    assert request.temperature is None
    assert request.max_tokens is None
    assert request.response_format is None


def test_llm_response_carries_model_and_optional_metadata() -> None:
    response = LLMResponse(
        content="{}",
        model="test-llm-model",
        provider_request_id="req_1",
        usage=LLMUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )
    assert response.usage is not None and response.usage.total_tokens == 3
    assert LLMResponse(content="{}", model="m").usage is None


@pytest.mark.parametrize(
    "factory",
    [
        lambda: LLMMessage(role="user", content="x"),
        lambda: LLMRequest(messages=[]),
        lambda: LLMUsage(),
        lambda: LLMResponse(content="{}", model="m"),
    ],
)
def test_models_are_frozen_and_forbid_extra(factory) -> None:
    model = factory()
    with pytest.raises(ValidationError):
        model.__class__(**{**model.model_dump(), "unexpected": 1})


def test_models_reject_attribute_assignment() -> None:
    message = LLMMessage(role="user", content="x")
    with pytest.raises(ValidationError):
        message.content = "y"  # type: ignore[misc]


def test_request_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        LLMRequest(messages=[], stream=True)  # type: ignore[call-arg]


def test_fake_llm_satisfies_the_provider_protocol() -> None:
    assert isinstance(FakeLLMProvider([]), LLMProvider)


def test_request_accepts_dict_response_format_only() -> None:
    request = LLMRequest(
        messages=[LLMMessage(role="user", content="x")],
        response_format={"type": "json_object"},
    )
    assert request.response_format == {"type": "json_object"}
