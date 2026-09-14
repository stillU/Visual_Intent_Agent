"""Step 07 Renderer 测试：唯一目标模型、无新增 token、确定性。

任务书「必测场景 7：Renderer 只处理一个目标模型」。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from visual_intent_agent.prompt_engine import (
    QWEN_IMAGE_MODEL,
    RENDERER_VERSION,
    ModelRenderer,
    QwenImageRenderer,
    parse_output_size,
)
from visual_intent_agent.prompt_engine.spec import CompilationClause, CompilationSpec

_RENDERER_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "visual_intent_agent"
    / "prompt_engine"
    / "renderer.py"
)


def _spec(*texts: str) -> CompilationSpec:
    return CompilationSpec(
        session_id="ses_1",
        target_model=QWEN_IMAGE_MODEL,
        clauses=[
            CompilationClause(
                clause_id=f"clause_{index:02d}",
                text=text,
                intent_path="subject.description",
                source_kind="intent",
            )
            for index, text in enumerate(texts)
        ],
    )


def test_renderer_version_is_the_frozen_value():
    assert RENDERER_VERSION == "qwen_image.v1"
    assert QwenImageRenderer().renderer_version == RENDERER_VERSION


def test_there_is_exactly_one_renderer_class_in_the_module():
    source = _RENDERER_SOURCE.read_text(encoding="utf-8")
    classes = re.findall(r"^class\s+(\w*Renderer\w*)", source, flags=re.MULTILINE)
    # 恰好：一个 ModelRenderer Protocol + 一个具体实现，不得出现第二个 renderer。
    assert classes == ["ModelRenderer", "QwenImageRenderer"]


def test_renderer_satisfies_the_model_renderer_protocol():
    renderer = QwenImageRenderer()
    assert isinstance(renderer, ModelRenderer)
    assert renderer.target_model == QWEN_IMAGE_MODEL
    assert QWEN_IMAGE_MODEL == "qwen-image-3.0"


def test_renderer_supports_only_the_single_target_model():
    renderer = QwenImageRenderer()
    assert renderer.supports(QWEN_IMAGE_MODEL, "1024x1024") is True
    assert renderer.supports(QWEN_IMAGE_MODEL, "512x768") is True
    assert renderer.supports("some-other-model", "1024x1024") is False
    assert renderer.supports("", "1024x1024") is False


@pytest.mark.parametrize(
    "size",
    ["1024x1024", "512x768", "2048x2048"],
)
def test_renderer_accepts_well_formed_sizes(size):
    assert QwenImageRenderer().supports(QWEN_IMAGE_MODEL, size) is True


@pytest.mark.parametrize(
    "size",
    ["1024", "1024*1024", "1024x", "x1024", "0x1024", "1024x0", "-4x4", "a x b", ""],
)
def test_renderer_rejects_malformed_sizes(size):
    assert QwenImageRenderer().supports(QWEN_IMAGE_MODEL, size) is False
    assert parse_output_size(size) is None


def test_parse_output_size_returns_dimensions():
    assert parse_output_size("1024x1536") == (1024, 1536)


def test_render_joins_clause_texts_without_adding_any_token():
    prompt = QwenImageRenderer().render(_spec("subject: a cat", "framing: medium_shot"))
    assert prompt == "subject: a cat, framing: medium_shot"


def test_render_adds_no_quality_or_style_padding():
    renderer = QwenImageRenderer()
    prompt = renderer.render(_spec("subject: a cat"))
    assert prompt == "subject: a cat"
    for forbidden in ("masterpiece", "best quality", "8k", "highly detailed", "4k"):
        assert forbidden not in prompt


def test_render_of_an_empty_spec_is_empty():
    assert QwenImageRenderer().render(_spec()) == ""


def test_render_rejects_a_non_spec_argument():
    with pytest.raises(TypeError):
        QwenImageRenderer().render("subject: a cat")


def test_renderer_module_is_offline_and_has_no_provider_dependency():
    source = _RENDERER_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("httpx", "requests", "urllib", "openai", "socket", "http://", "https://"):
        assert forbidden not in source
    assert "RENDERER_VERSION" in source
