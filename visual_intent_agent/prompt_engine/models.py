"""Step 07 PromptEngine 公开数据模型（ARCHITECTURE.md 4「Step 07 — prompt_engine」）。

    SourceBinding          clause_id / text / source_kind /
                           intent_path / rule_id / realization_id
    PromptParameters       size（本模型唯一参数面，对应 /images/generations 的 "<w>x<h>"）
    PromptArtifact         schema_version / prompt_artifact_id / session_id /
                           based_on_intent_revision_id / based_on_confirmation_id /
                           target_model / prompt / parameters / source_bindings /
                           realization_refs / created_at
    PromptCompilationError 带 `.code`（`prompt.*` 命名空间，恰好四个冻结值）
    PromptCompileRequest   session_id / confirmation_id

分层（README 不变量 7）：PromptArtifact 只引用 Intent revision / Confirmation /
Realization 的 **ID**，绝不内嵌 Intent/Realization 对象；Prompt 文本不是生成结果
（不变量/禁止范围：不把 Prompt 当图片）。

全部模型 `frozen=True, extra="forbid"`；`created_at` 一律 tz-aware UTC，JSON 序列化
固定 `+00:00`（ARCHITECTURE.md 5.2 / 5.7）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer

from visual_intent_agent.domain.constants import SCHEMA_VERSION
from visual_intent_agent.domain.identifiers import utc_now

_FROZEN = ConfigDict(frozen=True, extra="forbid")


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


# ---------------------------------------------------------------------------
# SourceBinding
# ---------------------------------------------------------------------------

#: Prompt 中一项重要视觉 clause 的合法来源类别（ARCHITECTURE.md 4「Step 07」）。
#:
#: - `intent`：值来自已确认 Intent 的某个白名单路径（`intent_path`）；
#: - `delegation`：路径被用户显式 `user_delegated`，本步在该路径局部范围内新选定实现
#:   （`intent_path` + `realization_id`）；
#: - `realization`：复用已有且未失效的 Realization 值（`intent_path` + `realization_id`）；
#: - `runtime`：实现层决策，由稳定 `rule_id` 说明（本步不产生，仅保留合法类别）。
SOURCE_KIND_INTENT = "intent"
SOURCE_KIND_DELEGATION = "delegation"
SOURCE_KIND_REALIZATION = "realization"
SOURCE_KIND_RUNTIME = "runtime"

SOURCE_KINDS: frozenset[str] = frozenset(
    {SOURCE_KIND_INTENT, SOURCE_KIND_DELEGATION, SOURCE_KIND_REALIZATION, SOURCE_KIND_RUNTIME}
)

#: 需要 `realization_id` 的来源类别。
_REALIZATION_SOURCE_KINDS: frozenset[str] = frozenset(
    {SOURCE_KIND_DELEGATION, SOURCE_KIND_REALIZATION}
)


class SourceBinding(BaseModel):
    """一个 Prompt clause 与其来源的绑定（可回溯性证据，不变量 3/7）。

    每个重要视觉 clause 必须至少命中 `intent_path` / `rule_id` / `realization_id`
    之一；`source_kind` 决定哪一组字段是强制的（见 `engine.check_source_bindings`）。
    """

    model_config = _FROZEN

    clause_id: str
    text: str
    source_kind: Literal["intent", "delegation", "realization", "runtime"]
    intent_path: str | None = None
    rule_id: str | None = None
    realization_id: str | None = None


# ---------------------------------------------------------------------------
# Parameters / Artifact
# ---------------------------------------------------------------------------


class PromptParameters(BaseModel):
    """目标模型参数面（冻结为**只有** `size`）。

    `size` 对应 `/images/generations` 的 `"<w>x<h>"`，从已确认 `ExecutionRevision.output_size`
    原样取得；本步不引入 seed / negative_prompt / style 等任何额外参数。
    """

    model_config = _FROZEN

    size: str = "1024x1024"


class PromptArtifact(BaseModel):
    """一份已保存的、可完整追溯的编译结果（append-only）。

    - `based_on_intent_revision_id` / `based_on_confirmation_id`：硬门禁绑定；
    - `target_model`：唯一目标图像模型（取自 ExecutionRevision.target_model）；
    - `prompt`：目标模型 Prompt 文本（**不是**生成结果）；
    - `parameters`：仅 `size`；
    - `source_bindings`：每个重要 clause 的来源；
    - `realization_refs`：本次编译引用/产生的 RealizationState id（无则为空列表）。
    """

    model_config = _FROZEN

    schema_version: Literal["v1"] = SCHEMA_VERSION
    prompt_artifact_id: str
    session_id: str
    based_on_intent_revision_id: str
    based_on_confirmation_id: str
    target_model: str
    prompt: str
    parameters: PromptParameters = Field(default_factory=PromptParameters)
    source_bindings: list[SourceBinding] = Field(default_factory=list)
    realization_refs: list[str] = Field(default_factory=list)
    created_at: _UtcDatetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------

#: 无有效 Confirmation（不存在 / 已失效 / 绑定非当前 revision / 不属于该会话）。
PROMPT_NO_VALID_CONFIRMATION = "prompt.no_valid_confirmation"
#: Prompt 中出现无法回溯的重要视觉内容（无任何合法来源）。
PROMPT_UNAUTHORIZED_ADDITION = "prompt.unauthorized_addition"
#: clause 声明了来源类别，却缺少该类别强制要求的绑定字段（或绑定字段非法）。
PROMPT_MISSING_SOURCE_BINDING = "prompt.missing_source_binding"
#: 已确认要求超出本步/目标模型的能力范围（例如非唯一目标模型、无法解析的尺寸、
#: 委托路径没有确定性实现）。
PROMPT_UNSUPPORTED_REQUIREMENT = "prompt.unsupported_requirement"

PROMPT_ERROR_CODES: frozenset[str] = frozenset(
    {
        PROMPT_NO_VALID_CONFIRMATION,
        PROMPT_UNAUTHORIZED_ADDITION,
        PROMPT_MISSING_SOURCE_BINDING,
        PROMPT_UNSUPPORTED_REQUIREMENT,
    }
)


class PromptCompilationError(Exception):
    """Prompt 编译的硬失败（硬门禁 / 程序级失败），带 `prompt.*` 命名空间的 `.code`。

    硬失败一律**不产生** PromptArtifact（编译失败而不是继续生成）。
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in PROMPT_ERROR_CODES:
            raise ValueError(f"unknown prompt compilation error code: {code!r}")
        self.code = code
        super().__init__(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------


class PromptCompileRequest(BaseModel):
    """一次编译请求：只接受会话与 Confirmation ID（不含任何草稿内容）。

    `confirmation_id` 应由 `WorkflowService.confirm_current_intent` 返回，或取
    `SessionSnapshot.latest_confirmation_id`；引擎不接收 Prompt 文本、不接收 Intent 对象。
    """

    model_config = _FROZEN

    session_id: str
    confirmation_id: str


__all__ = [
    "SourceBinding",
    "PromptParameters",
    "PromptArtifact",
    "PromptCompilationError",
    "PromptCompileRequest",
    "SOURCE_KINDS",
    "SOURCE_KIND_INTENT",
    "SOURCE_KIND_DELEGATION",
    "SOURCE_KIND_REALIZATION",
    "SOURCE_KIND_RUNTIME",
    "PROMPT_ERROR_CODES",
    "PROMPT_NO_VALID_CONFIRMATION",
    "PROMPT_UNAUTHORIZED_ADDITION",
    "PROMPT_MISSING_SOURCE_BINDING",
    "PROMPT_UNSUPPORTED_REQUIREMENT",
]
