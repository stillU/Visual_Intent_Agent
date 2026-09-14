"""Step 08 Generation 数据模型（ARCHITECTURE.md 4「Step 08 — generation」）。

    OutputRef          path / mime_type / byte_size
    GenerationArtifact schema_version / generation_id / session_id / prompt_artifact_id /
                       target_model / model_version / parameters / seed /
                       output_refs / provider_request_id / created_at
    GenerationError    带 `.code`（`generation.*` 命名空间）

分层（README 不变量 7）：GenerationArtifact 只引用 PromptArtifact / Intent 的 **ID**，
不内嵌 Intent / Prompt / Realization 对象，也不把 output 当作 RealizationState 的事实来源。

投影原则（任务书「输出保存」四问）：

- 哪个 Prompt 生成了这张图 → `prompt_artifact_id`；
- 使用哪个模型版本和参数 → `target_model` / `model_version` / `parameters`（复用 Step 07
  `PromptParameters`，不新增参数面）；
- 哪次 Provider 请求产生 → `provider_request_id`（Provider 未提供则 None，禁止伪造）；
- 用户后续反馈针对哪次生成 → `generation_id` 是 `feedback_results.refs["generation_id"]`
  的外键目标。

**不存临时 URL**：`OutputRef` 只有相对项目根的稳定路径、MIME 与字节数；Provider 的
一次性签名 URL 绝不进入合同（ARCHITECTURE.md 6.2）。

全部模型 `frozen=True, extra="forbid"`；`created_at` 一律 tz-aware UTC，JSON 序列化固定
`+00:00`（ARCHITECTURE.md 5.2 / 5.7）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from visual_intent_agent.domain.constants import SCHEMA_VERSION
from visual_intent_agent.domain.identifiers import utc_now
from visual_intent_agent.prompt_engine.models import PromptParameters

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: GenerationArtifact ID 前缀（ARCHITECTURE.md 5.1）。
GENERATION_ID_PREFIX = "gen"

#: 输出目录内的文件名模式（第 i 张图片从 1 开始计数）。
OUTPUT_FILE_NAME_TEMPLATE = "image_{index}.png"


def _ensure_utc(value: datetime) -> datetime:
    """拒绝 naive datetime；把 tz-aware 值归一到 UTC（保持同一时刻）。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware UTC; naive datetimes are rejected")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    """JSON 序列化形态：ISO 8601 带 `+00:00`（ARCHITECTURE.md 5.2）。"""
    return value.astimezone(timezone.utc).isoformat()


#: tz-aware UTC datetime：校验 + JSON 序列化形态在此一次冻结。
_UtcDatetime = Annotated[
    datetime,
    AfterValidator(_ensure_utc),
    PlainSerializer(_iso_utc, return_type=str, when_used="json"),
]


class OutputRef(BaseModel):
    """一个已保存生成结果的稳定引用（不是媒体资产平台，只需能重新定位）。

    - `path`：相对**项目根**的路径，形如 `outputs/generations/<generation_id>/image_1.png`；
      用 `PROJECT_ROOT / path` 即可重新定位（测试用 `tmp_path` 输出目录时为相对路径，
      仍可用同一规则解析）；
    - `mime_type`：图片 MIME（来自 Provider 响应头 / magic bytes，不猜测）；
    - `byte_size`：落盘字节数。
    """

    model_config = _FROZEN

    path: str
    mime_type: str
    byte_size: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def _path_must_be_relative_and_non_empty(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("path must be a non-empty string")
        if value.startswith(("/", "\\")) or (len(value) > 1 and value[1] == ":"):
            raise ValueError(
                "path must be relative to the project root (no absolute paths, no URLs)"
            )
        if "://" in value:
            raise ValueError("path must be a filesystem path, never a URL")
        return value

    @field_validator("mime_type")
    @classmethod
    def _mime_type_must_not_be_blank(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("mime_type must be a non-empty string")
        return value


class GenerationArtifact(BaseModel):
    """一次成功生成的不可变审计记录（append-only；历史不覆盖）。

    - `parameters` 直接复用 Step 07 `PromptParameters`（只有 `size`），不新增参数面；
    - `seed`：Provider 不返回则为 `None`，禁止伪造；
    - `model_version`：Provider 报告的模型标识；Provider 未报告时 adapter 用请求/配置值
      回填（不编造版本号）；
    - `output_refs`：至少一个（成功生成必然落盘至少一张图片）。
    """

    model_config = _FROZEN

    schema_version: Literal["v1"] = SCHEMA_VERSION
    generation_id: str
    session_id: str
    prompt_artifact_id: str
    target_model: str
    model_version: str | None = None
    parameters: PromptParameters = Field(default_factory=PromptParameters)
    seed: int | None = None
    output_refs: list[OutputRef] = Field(default_factory=list)
    provider_request_id: str | None = None
    created_at: _UtcDatetime = Field(default_factory=utc_now)

    @field_validator("generation_id", "session_id", "prompt_artifact_id", "target_model")
    @classmethod
    def _required_ids_must_not_be_blank(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("identifier fields must be non-empty strings")
        return value

    @field_validator("model_version", "provider_request_id")
    @classmethod
    def _optional_strings_must_not_be_blank_when_set(cls, value: str | None) -> str | None:
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("optional string fields must be None or a non-empty string")
        return value

    @field_validator("seed", mode="before")
    @classmethod
    def _seed_must_be_an_int_when_present(cls, value: object) -> int | None:
        # mode="before"：pydantic 的 lax 模式会把 bool 强制转成 int，必须先显式拒绝。
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("seed must be None or an int (never fabricated, never a bool)")
        return value


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------

#: 当前工作流状态不允许该生成用例（generate 需 WAITING_CONFIRMATION；retry 仅 FAILED）。
GENERATION_INVALID_STATE = "generation.invalid_state"
#: 缺少有效 Confirmation（缺失 / 已失效 / 未绑定当前两类 revision）。
GENERATION_NO_VALID_CONFIRMATION = "generation.no_valid_confirmation"
#: retry 找不到原 GenerationArtifact（Provider 失败时不伪造 Artifact，历史里可能确实没有）。
GENERATION_ARTIFACT_NOT_FOUND = "generation.artifact_not_found"
#: 引用的 Artifact / Confirmation 属于另一个会话（越权引用显式拒绝）。
GENERATION_SESSION_MISMATCH = "generation.session_mismatch"
#: 输出字节落盘失败（磁盘/权限等程序级失败）。
GENERATION_OUTPUT_WRITE_FAILED = "generation.output_write_failed"

#: 全部 code（import 时自检，防止文档与实现漂移）。
GENERATION_ERROR_CODES: frozenset[str] = frozenset(
    {
        GENERATION_INVALID_STATE,
        GENERATION_NO_VALID_CONFIRMATION,
        GENERATION_ARTIFACT_NOT_FOUND,
        GENERATION_SESSION_MISMATCH,
        GENERATION_OUTPUT_WRITE_FAILED,
    }
)


class GenerationError(Exception):
    """生成用例的硬失败（程序级 / 门禁失败），带 `generation.*` 命名空间的 `.code`。

    Provider 调用失败不是本异常：adapter 抛 `ProviderError`，由 `GenerationPipeline`
    转换为 `FAILED` 状态后**原样向上抛**（任务书 Workflow 规则 4）。
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in GENERATION_ERROR_CODES:
            raise ValueError(f"unknown generation error code: {code!r}")
        self.code = code
        super().__init__(f"[{code}] {message}")


__all__ = [
    "OutputRef",
    "GenerationArtifact",
    "GenerationError",
    "GENERATION_ID_PREFIX",
    "OUTPUT_FILE_NAME_TEMPLATE",
    "GENERATION_ERROR_CODES",
    "GENERATION_INVALID_STATE",
    "GENERATION_NO_VALID_CONFIRMATION",
    "GENERATION_ARTIFACT_NOT_FOUND",
    "GENERATION_SESSION_MISMATCH",
    "GENERATION_OUTPUT_WRITE_FAILED",
]
