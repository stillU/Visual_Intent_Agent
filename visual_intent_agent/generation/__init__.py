"""Step 08 公开面：图像生成闭环（ARCHITECTURE.md 4「Step 08 — generation」）。

    from visual_intent_agent.generation import (
        GenerationPipeline, GenerationArtifact, OutputRef, GenerationError,
        GENERATION_ERROR_CODES, GENERATION_INVALID_STATE, GENERATION_NO_VALID_CONFIRMATION,
        GENERATION_ARTIFACT_NOT_FOUND, GENERATION_SESSION_MISMATCH,
        GENERATION_OUTPUT_WRITE_FAILED,
    )

    用例：GenerationPipeline(repo, prompt_engine, image_provider, output_dir).generate(sid)
          GenerationPipeline(...).retry(sid, generation_id)

边界：

- 只编排"已确认 PromptArtifact → 唯一图像 Provider → 字节落盘 → GenerationArtifact"；
- **不修改 Intent、不重新编译 Prompt**（retry 复用同一 PromptArtifact）；
- Provider 失败 → `FAILED` + 结构化日志，不伪造 Artifact，`ProviderError` 原样上抛；
- 不实现多模型路由、图片质量判断、队列集群、Reference Image；
- output 只是本地下载件，不是 RealizationState 的事实来源。

图像 Provider 合同从 `visual_intent_agent.providers.image` / `providers.fake_image` /
`providers.openai_image` 导入（`providers/__init__.py` 必须保持空白，多 Step 拥有）。
"""

from __future__ import annotations

from .models import (
    GENERATION_ARTIFACT_NOT_FOUND,
    GENERATION_ERROR_CODES,
    GENERATION_ID_PREFIX,
    GENERATION_INVALID_STATE,
    GENERATION_NO_VALID_CONFIRMATION,
    GENERATION_OUTPUT_WRITE_FAILED,
    GENERATION_SESSION_MISMATCH,
    OUTPUT_FILE_NAME_TEMPLATE,
    GenerationArtifact,
    GenerationError,
    OutputRef,
)
from .pipeline import GENERATION_FAILED_EVENT, GenerationPipeline

__all__ = [
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
]
