"""Step 07 公开面与依赖边界测试（ARCHITECTURE.md 4/5.5/第 7 节）。"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import visual_intent_agent.prompt_engine as prompt_engine
from visual_intent_agent.prompt_engine import (
    PROMPT_ERROR_CODES,
    PROMPT_MISSING_SOURCE_BINDING,
    PROMPT_NO_VALID_CONFIRMATION,
    PROMPT_UNAUTHORIZED_ADDITION,
    PROMPT_UNSUPPORTED_REQUIREMENT,
    ModelRenderer,
    PromptArtifact,
    PromptCompilationError,
    PromptCompileRequest,
    PromptEngine,
    PromptParameters,
    QwenImageRenderer,
    SourceBinding,
)

_PACKAGE_DIR = Path(prompt_engine.__file__).resolve().parent
_PACKAGE_SOURCES = sorted(_PACKAGE_DIR.glob("*.py"))


def test_package_reexports_the_frozen_public_surface():
    expected = {
        "PromptEngine",
        "PromptArtifact",
        "SourceBinding",
        "PromptParameters",
        "PromptCompileRequest",
        "PromptCompilationError",
        "PROMPT_ERROR_CODES",
        "PROMPT_NO_VALID_CONFIRMATION",
        "PROMPT_UNAUTHORIZED_ADDITION",
        "PROMPT_MISSING_SOURCE_BINDING",
        "PROMPT_UNSUPPORTED_REQUIREMENT",
        "ModelRenderer",
        "QwenImageRenderer",
        "RENDERER_VERSION",
    }
    assert expected <= set(prompt_engine.__all__)
    for name in prompt_engine.__all__:
        assert hasattr(prompt_engine, name), name
    assert len(prompt_engine.__all__) == len(set(prompt_engine.__all__))


def test_compilation_spec_is_internal_and_not_exported():
    assert "CompilationSpec" not in prompt_engine.__all__
    assert "CompilationClause" not in prompt_engine.__all__
    assert not hasattr(prompt_engine, "CompilationSpec")
    assert not hasattr(prompt_engine, "CompilationClause")
    # 内部模块仍然存在，供引擎与测试直接使用（不是公开面）。
    from visual_intent_agent.prompt_engine.spec import (  # noqa: F401
        CompilationClause,
        CompilationSpec,
    )


def test_engine_constructor_and_compile_signature_match_the_frozen_table():
    signature = inspect.signature(PromptEngine.__init__)
    assert list(signature.parameters) == ["self", "renderer", "repo", "knowledge_engine"]
    # v0.4 Step 03：知识引擎是**可选 keyword-only** 依赖，默认 None（关闭 RAG）。
    assert signature.parameters["knowledge_engine"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["knowledge_engine"].default is None
    compile_params = list(inspect.signature(PromptEngine.compile).parameters)
    assert compile_params == ["self", "request"]


def test_renderer_protocol_is_runtime_checkable_and_only_qwen_implements_it():
    assert isinstance(QwenImageRenderer(), ModelRenderer)
    renderer_classes = [
        name
        for name in dir(prompt_engine)
        if name.endswith("Renderer") and isinstance(getattr(prompt_engine, name), type)
    ]
    assert renderer_classes == ["ModelRenderer", "QwenImageRenderer"]


def test_error_codes_use_the_prompt_namespace():
    for code in PROMPT_ERROR_CODES:
        assert code.startswith("prompt.")
    assert PROMPT_ERROR_CODES == {
        PROMPT_NO_VALID_CONFIRMATION,
        PROMPT_UNAUTHORIZED_ADDITION,
        PROMPT_MISSING_SOURCE_BINDING,
        PROMPT_UNSUPPORTED_REQUIREMENT,
    }
    with pytest.raises(ValueError):
        PromptCompilationError("workflow.invalid_state", "not a prompt code")


def test_public_models_are_frozen():
    for model in (SourceBinding, PromptParameters, PromptArtifact, PromptCompileRequest):
        assert model.model_config["frozen"] is True
        assert model.model_config["extra"] == "forbid"


@pytest.mark.parametrize("path", _PACKAGE_SOURCES, ids=lambda p: p.name)
def test_package_sources_contain_no_network_or_credential_tokens(path):
    source = path.read_text(encoding="utf-8")
    for forbidden in ("httpx", "requests", "urllib", "openai", "socket", "random", "uuid4"):
        assert forbidden not in source, f"{path.name} must not reference {forbidden!r}"
    for credential_token in ("api_key", "API_KEY", "Authorization", "Bearer "):
        assert credential_token not in source, f"{path.name} must not contain credentials"


def test_package_modules_are_only_the_frozen_ones():
    names = {path.name for path in _PACKAGE_SOURCES}
    assert names == {"__init__.py", "engine.py", "models.py", "renderer.py", "spec.py"}
