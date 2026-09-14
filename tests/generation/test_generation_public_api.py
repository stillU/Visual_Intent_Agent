"""Step 08 公开面与边界纪律的静态/运行时检查。"""

from __future__ import annotations

import ast
import typing
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "visual_intent_agent"

NEW_PRODUCT_FILES = (
    PACKAGE / "providers" / "image.py",
    PACKAGE / "providers" / "openai_image.py",
    PACKAGE / "providers" / "fake_image.py",
    PACKAGE / "generation" / "__init__.py",
    PACKAGE / "generation" / "models.py",
    PACKAGE / "generation" / "pipeline.py",
)


def _imported_modules(path: Path) -> set[str]:
    """模块内所有 import 的目标模块名（含相对 import 的字面模块部分）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
    return modules


def test_generation_package_exports_the_frozen_public_surface() -> None:
    import visual_intent_agent.generation as generation

    expected = {
        "GenerationPipeline",
        "GenerationArtifact",
        "OutputRef",
        "GenerationError",
        "GENERATION_FAILED_EVENT",
        "GENERATION_ID_PREFIX",
        "OUTPUT_FILE_NAME_TEMPLATE",
        "GENERATION_ERROR_CODES",
        "GENERATION_INVALID_STATE",
        "GENERATION_NO_VALID_CONFIRMATION",
        "GENERATION_ARTIFACT_NOT_FOUND",
        "GENERATION_SESSION_MISMATCH",
        "GENERATION_OUTPUT_WRITE_FAILED",
    }
    assert expected <= set(generation.__all__)
    for name in expected:
        assert hasattr(generation, name)


def test_image_provider_surface_is_importable_from_full_paths() -> None:
    from visual_intent_agent.providers.fake_image import FakeImageProvider
    from visual_intent_agent.providers.image import (
        GeneratedImage,
        ImageGenerationRequest,
        ImageGenerationResult,
        ImageProvider,
    )
    from visual_intent_agent.providers.openai_image import OpenAIImageProvider

    assert ImageProvider is not None
    assert ImageGenerationRequest is not None
    assert GeneratedImage is not None
    assert ImageGenerationResult is not None
    assert FakeImageProvider is not None
    assert OpenAIImageProvider is not None


def test_providers_init_stays_blank() -> None:
    # 多 Step 拥有的包：__init__.py 必须保持 0 字节，Step 08 不得修改。
    assert (PACKAGE / "providers" / "__init__.py").read_bytes() == b""


def test_only_one_real_image_adapter_exists() -> None:
    image_modules = sorted(
        path.name
        for path in (PACKAGE / "providers").glob("*image*.py")
        if path.name != "__init__.py"
    )
    assert image_modules == ["fake_image.py", "image.py", "openai_image.py"]


def test_no_plaintext_credentials_in_new_product_files() -> None:
    for path in NEW_PRODUCT_FILES:
        text = path.read_text(encoding="utf-8")
        assert "sk-" not in text, path


def _code_string_constants(path: Path) -> list[str]:
    """代码中的字符串常量（排除模块/类/函数 docstring）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_openai_image_never_sends_an_async_header_and_never_reads_env() -> None:
    path = PACKAGE / "providers" / "openai_image.py"
    text = path.read_text(encoding="utf-8")
    # 代码里不得出现任何 async 头常量（文档字符串可以说明为什么禁止）。
    assert not [
        value for value in _code_string_constants(path) if "async" in value.lower()
    ]
    assert "os.environ" not in text
    assert "load_settings" not in text
    assert "dotenv" not in text


def test_generation_never_imports_fake_providers_or_the_llm_side() -> None:
    forbidden = {
        "visual_intent_agent.providers.fake_image",
        "visual_intent_agent.providers.fake_llm",
        "visual_intent_agent.providers.openai_image",
        "visual_intent_agent.providers.openai_llm",
        "visual_intent_agent.intent_engine",
        "visual_intent_agent.feedback",
    }
    for name in ("pipeline.py", "models.py", "__init__.py"):
        imported = _imported_modules(PACKAGE / "generation" / name)
        assert not (imported & forbidden), (name, imported & forbidden)


def test_generation_does_not_import_httpx_directly() -> None:
    # HTTP 只允许存在于 providers adapter；generation 只依赖 ImageProvider Protocol。
    imported = _imported_modules(PACKAGE / "generation" / "pipeline.py")
    assert "httpx" not in imported
    assert "requests" not in imported


def test_openai_image_is_the_only_module_that_imports_httpx() -> None:
    assert "httpx" in _imported_modules(PACKAGE / "providers" / "openai_image.py")
    assert "httpx" not in _imported_modules(PACKAGE / "providers" / "image.py")
    assert "httpx" not in _imported_modules(PACKAGE / "providers" / "fake_image.py")


def test_pipeline_reuses_the_single_image_provider_protocol() -> None:
    import visual_intent_agent.generation.pipeline as pipeline_module
    from visual_intent_agent.providers.image import ImageProvider

    hints = typing.get_type_hints(pipeline_module.GenerationPipeline.__init__)
    assert hints["image_provider"] is ImageProvider


@pytest.mark.parametrize(
    "name", ["GenerationPipeline", "GenerationArtifact", "OutputRef", "GenerationError"]
)
def test_generation_public_names_are_declared(name: str) -> None:
    import visual_intent_agent.generation as generation

    assert name in generation.__all__
