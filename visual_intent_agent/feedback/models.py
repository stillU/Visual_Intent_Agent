"""Step 09 反馈层数据模型：`FeedbackDecision` / `FeedbackRequest` / `FeedbackResult`。

冻结形状（ARCHITECTURE.md 4「Step 09 — feedback + realization/carry + workflow/review」）：

    FeedbackDecision  accept | revise | clarify
    FeedbackRequest   session_id / message_id / feedback_text / generation /
                      prompt_artifact / current_intent / realization_state|None
    FeedbackResult    schema_version / feedback_id / session_id / generation_id /
                      intent_revision_id / decision / candidate_deltas /
                      preserve_paths / compile_feedback / issues / evidence_refs /
                      created_at                                     （恰好 12 字段）

分层（README 不变量 7）：`FeedbackRequest` 是**一次性请求**，按冻结表携带本轮反馈所
针对的 Artifact 只读对象（Generation / Prompt / Intent / Realization）；`FeedbackResult`
是**可持久化合同**，只保存 ID 引用与候选内容，绝不内嵌其它层的对象。反馈经
`Repository.append_feedback_result` 落库（refs 必填键 `{"generation_id"}`），因此每条反馈
都能精确回溯到真实 `GenerationArtifact`。

LLM 边界（README 不变量 1/2）：`FeedbackEngine` 只能提出 Candidate Delta 与保持范围，
**不能**直接修改任何状态；结果一律再由 `validate` / `reduce` / `assess` 与重新确认裁决。

错误边界（ARCHITECTURE.md 5.6）：

- **可恢复业务失败**（LLM 输出无法解析、Provider 调用失败、模糊反馈不足以形成 Delta）
  → `FeedbackEngine.analyze` **不抛异常**，返回带 `feedback.*` / `provider.*` error issue 的
  `FeedbackResult`；由 `ReviewService` 决定不改变状态并等待重试。
- **程序级失败**（调用方上下文不一致）→ 抛 `FeedbackError`（带 `.code`）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer

from visual_intent_agent.domain import (
    INTENT_PATHS,
    EvidenceRef,
    IntentDelta,
    Issue,
    VisualIntent,
)
from visual_intent_agent.domain.constants import SCHEMA_VERSION
from visual_intent_agent.domain.identifiers import utc_now
from visual_intent_agent.generation import GenerationArtifact
from visual_intent_agent.prompt_engine import PromptArtifact
from visual_intent_agent.realization.models import RealizationState

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: 反馈 ID 前缀（ARCHITECTURE.md 5.1 冻结前缀表）。
FEEDBACK_ID_PREFIX = "fbk"

# ---------------------------------------------------------------------------
# Issue code（命名空间 `feedback.*`；ARCHITECTURE.md 5.5）
# ---------------------------------------------------------------------------

#: LLM 输出无法解析为 `FeedbackResult` 的**前缀**（不含 Provider 失败）。
FEEDBACK_UNPARSEABLE_OUTPUT = "feedback.unparseable_output"
#: 模型返回空文本。
FEEDBACK_EMPTY_OUTPUT = "feedback.unparseable_output.empty"
#: 文本不是合法 JSON。
FEEDBACK_INVALID_JSON = "feedback.unparseable_output.invalid_json"
#: JSON 合法但不符合输出 Schema（缺字段 / 多余字段 / 类型错误 / 完整 Intent）。
FEEDBACK_SCHEMA_VIOLATION = "feedback.unparseable_output.schema_violation"
#: 模型返回了完整 Intent / facet 对象而不是 Candidate Delta（禁止）。
FEEDBACK_FULL_INTENT = "feedback.unparseable_output.full_intent"
#: 单条候选 Delta 违反 `domain.IntentDelta` 冻结形状。
FEEDBACK_INVALID_DELTA = "feedback.unparseable_output.invalid_delta"
#: decision 与其它字段自相矛盾（如 revise 却没有任何 delta）。
FEEDBACK_INCONSISTENT_DECISION = "feedback.unparseable_output.inconsistent_decision"
#: clarify 声明的目标路径不在 Schema v1 白名单内（绝不修正 / 不猜测）。
FEEDBACK_INVALID_CLARIFY_PATH = "feedback.unparseable_output.invalid_clarify_path"

#: 全部解析失败 code（import 时由 `FeedbackParseError` 自检，避免文档与实现漂移）。
PARSE_FAILURE_CODES: frozenset[str] = frozenset(
    {
        FEEDBACK_EMPTY_OUTPUT,
        FEEDBACK_INVALID_JSON,
        FEEDBACK_SCHEMA_VIOLATION,
        FEEDBACK_FULL_INTENT,
        FEEDBACK_INVALID_DELTA,
        FEEDBACK_INCONSISTENT_DECISION,
        FEEDBACK_INVALID_CLARIFY_PATH,
    }
)

#: 可执行的 clarify 目标载体（`path` = 需要澄清的 Intent 路径；severity=warning）。
FEEDBACK_CLARIFICATION_REQUIRED = "feedback.clarification_required"
#: clarify 但没有可定位到白名单路径的目标 → 不可执行，工作流不得猜问题。
FEEDBACK_CLARIFICATION_TARGET_MISSING = "feedback.clarification_target_missing"
#: preserve 模式既不是白名单路径也不是 `<facet>.*` → 忽略并显式报告（不猜测）。
FEEDBACK_UNKNOWN_PRESERVE_PATH = "feedback.unknown_preserve_path"
#: 调用方上下文不一致（generation / prompt / realization 不属于本会话或互不匹配）。
FEEDBACK_CONTEXT_MISMATCH = "feedback.context_mismatch"

#: 全部 `FeedbackError.code` 取值（程序级失败）。
FEEDBACK_ERROR_CODES: frozenset[str] = frozenset({FEEDBACK_CONTEXT_MISMATCH})


def _ensure_utc(value: datetime) -> datetime:
    """拒绝 naive datetime；把 tz-aware 值归一到 UTC（与 Step 01/04/07 同约定）。"""
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


class FeedbackDecision(str, Enum):
    """反馈的三种裁决（冻结三值；不存在第四种）。"""

    ACCEPT = "accept"
    REVISE = "revise"
    CLARIFY = "clarify"


class FeedbackRequest(BaseModel):
    """一次反馈解释请求（只读上下文；FeedbackEngine 绝不修改其中任何对象）。

    - `message_id`：本轮反馈消息的已存 ID（证据引用的唯一来源，由系统填入）；
    - `generation` / `prompt_artifact`：反馈**精确关联**的真实 Artifact（只读快照）；
    - `current_intent`：当前 Draft Intent（只读；引擎不得修改）；
    - `realization_state`：当前 RealizationState（无则 None；系统已实现的委托选择，
      不是用户事实）。
    """

    model_config = _FROZEN

    session_id: str
    message_id: str
    feedback_text: str
    generation: GenerationArtifact
    prompt_artifact: PromptArtifact
    current_intent: VisualIntent
    realization_state: RealizationState | None = None


class FeedbackResult(BaseModel):
    """反馈解释结果（可持久化合同；恰好 12 字段，与冻结表逐字一致）。

    - `intent_revision_id`：本次反馈所针对的 IntentRevision。反馈入口只允许
      `WAITING_REVIEW`，此时当前 revision 必等于该次生成所依据的 revision
      （`prompt_artifact.based_on_intent_revision_id`），引擎据此确定性填写；
    - `candidate_deltas`：仍然只是**候选**，必须再过 Validator / Reducer /
      DecisionPolicy 与重新确认（README 不变量 2/5）；
    - `preserve_paths`：用户声明"保持不变"的范围，保留 LLM 声明的模式原文
      （如 `subject.*`）；可定位到当前有值路径的模式另转为合法 PIN 候选 Delta；
    - `compile_feedback`：给编译步骤的人读提示（仅记录；`PromptCompileRequest`
      冻结签名没有该通道，本步不把反馈注入编译）；
    - `issues`：可恢复失败与观察（`feedback.*` / `provider.*`）；
    - `evidence_refs`：整体证据（去重后的 Delta 证据并集，至少含本轮消息）。
    """

    model_config = _FROZEN

    schema_version: Literal["v1"] = SCHEMA_VERSION
    feedback_id: str
    session_id: str
    generation_id: str
    intent_revision_id: str
    decision: FeedbackDecision
    candidate_deltas: list[IntentDelta] = Field(default_factory=list)
    preserve_paths: list[str] = Field(default_factory=list)
    compile_feedback: str | None = None
    issues: list[Issue] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    created_at: _UtcDatetime = Field(default_factory=utc_now)


class FeedbackError(Exception):
    """反馈层的**程序级**失败（带 `.code`，命名空间 `feedback.*`）。

    仅用于调用方上下文不一致（如 generation 与 prompt_artifact 不匹配、跨会话）。
    LLM 输出无法解析、Provider 失败等可恢复业务失败**不抛异常**，走 `FeedbackResult.issues`。
    与 `intent_engine.models.InterpreterError` 同约定：基类不校验 code，
    子类 `FeedbackParseError` 只接受冻结的解析失败 code 集。
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - 由测试覆盖
        return f"{self.code}: {self.message}"


class FeedbackParseError(FeedbackError):
    """LLM 输出无法解析为 `FeedbackResult`（可恢复：不猜测修复）。

    `retryable=True` 表示调用方可以重新解释同一条消息。引擎自身**不重试解析失败、
    不修复**；仅可重试 Provider 错误在引擎内最多尝试 2 次（MVP v0.3 Step 06 patch 002，
    见 `docs/handoffs/architecture_decision_004.md`）。
    """

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        if code not in PARSE_FAILURE_CODES:
            raise ValueError(f"unknown feedback parse failure code: {code!r}")
        super().__init__(code, message, retryable=retryable)


def clarify_target_path(result: FeedbackResult) -> str | None:
    """从结果里取**可执行**的 clarify 目标路径（否则 None）。

    只有 `feedback.clarification_required` issue 显式携带白名单路径时才算可执行；
    解析失败 / Provider 失败 / 无法定位目标一律返回 None（工作流据此不改变状态、
    不猜问题、不猜具体设计）。
    """
    for issue in result.issues:
        if issue.code == FEEDBACK_CLARIFICATION_REQUIRED and issue.path in INTENT_PATHS:
            return issue.path
    return None


def is_unparseable(result: FeedbackResult) -> bool:
    """结果是否由 LLM 解析失败产生（`feedback.unparseable_output.*`）。"""
    return any(
        issue.code in PARSE_FAILURE_CODES
        or issue.code == FEEDBACK_UNPARSEABLE_OUTPUT
        or issue.code.startswith(f"{FEEDBACK_UNPARSEABLE_OUTPUT}.")
        for issue in result.issues
    )


__all__ = [
    "FeedbackDecision",
    "FeedbackRequest",
    "FeedbackResult",
    "FeedbackError",
    "FeedbackParseError",
    "clarify_target_path",
    "is_unparseable",
    "FEEDBACK_ID_PREFIX",
    "FEEDBACK_UNPARSEABLE_OUTPUT",
    "FEEDBACK_EMPTY_OUTPUT",
    "FEEDBACK_INVALID_JSON",
    "FEEDBACK_SCHEMA_VIOLATION",
    "FEEDBACK_FULL_INTENT",
    "FEEDBACK_INVALID_DELTA",
    "FEEDBACK_INCONSISTENT_DECISION",
    "FEEDBACK_INVALID_CLARIFY_PATH",
    "FEEDBACK_CLARIFICATION_REQUIRED",
    "FEEDBACK_CLARIFICATION_TARGET_MISSING",
    "FEEDBACK_UNKNOWN_PRESERVE_PATH",
    "FEEDBACK_CONTEXT_MISMATCH",
    "PARSE_FAILURE_CODES",
    "FEEDBACK_ERROR_CODES",
]
