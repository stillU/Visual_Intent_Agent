"""Step 07 公开面：受限 Prompt 编译（ARCHITECTURE.md 4「Step 07 — prompt_engine」）。

    from visual_intent_agent.prompt_engine import (
        PromptEngine, PromptArtifact, SourceBinding, PromptParameters,
        PromptCompileRequest, PromptCompilationError,
        PROMPT_ERROR_CODES, PROMPT_NO_VALID_CONFIRMATION,
        PROMPT_UNAUTHORIZED_ADDITION, PROMPT_MISSING_SOURCE_BINDING,
        PROMPT_UNSUPPORTED_REQUIREMENT,
        ModelRenderer, QwenImageRenderer, RENDERER_VERSION, QWEN_IMAGE_MODEL,
    )

    编译：PromptEngine(renderer=QwenImageRenderer(), repo=repo).compile(
        PromptCompileRequest(session_id=..., confirmation_id=...))

边界（任务书「授权边界 / 禁止范围」）：

- 只从已确认 Intent + ExecutionRevision + 合法 Realization 编译；无有效 Confirmation
  绝不生成 Prompt（`prompt.no_valid_confirmation`）；
- 每个重要视觉 clause 必须有 source binding；无法回溯的重大内容
  → `prompt.unauthorized_addition`（编译失败，不继续生成）；
- unspecified + policy `omit` 的字段保持不具体化（不进 Prompt、不产生 binding/Realization）；
- 只有**一个**目标模型 Renderer（`QwenImageRenderer`，`RENDERER_VERSION = "qwen_image.v1"`）；
- 不调用图像 Provider、不接 RAG、不引入 Prompt AST、不把 Prompt 文本当生成结果。

`CompilationSpec`（`spec.py`）是**内部轻量结构**，按架构冻结要求**不从本包导出**。
"""

from __future__ import annotations

from .engine import PromptEngine
from .models import (
    PROMPT_ERROR_CODES,
    PROMPT_MISSING_SOURCE_BINDING,
    PROMPT_NO_VALID_CONFIRMATION,
    PROMPT_UNAUTHORIZED_ADDITION,
    PROMPT_UNSUPPORTED_REQUIREMENT,
    SOURCE_KINDS,
    SOURCE_KIND_DELEGATION,
    SOURCE_KIND_INTENT,
    SOURCE_KIND_REALIZATION,
    SOURCE_KIND_RUNTIME,
    PromptArtifact,
    PromptCompilationError,
    PromptCompileRequest,
    PromptParameters,
    SourceBinding,
)
from .renderer import (
    QWEN_IMAGE_MODEL,
    RENDERER_VERSION,
    ModelRenderer,
    QwenImageRenderer,
    parse_output_size,
)

__all__ = [
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
    "SOURCE_KINDS",
    "SOURCE_KIND_INTENT",
    "SOURCE_KIND_DELEGATION",
    "SOURCE_KIND_REALIZATION",
    "SOURCE_KIND_RUNTIME",
    "ModelRenderer",
    "QwenImageRenderer",
    "RENDERER_VERSION",
    "QWEN_IMAGE_MODEL",
    "parse_output_size",
]
