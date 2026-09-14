"""Step 05 公开数据模型：IntentResolveRequest、InterpreterResult、InterpreterError。

冻结形状（ARCHITECTURE.md 4「Step 05」）：

- `IntentResolveRequest` 承载任务书 `resolve(current_intent, message, pending_question)`
  的三要素，外加证据边界（`available_message_ids`）；
- `InterpreterResult` 是 Interpreter 的唯一输出：候选 Delta（仍须过 Validator）、
  LLM 自报的冲突、未能映射为 Delta 的语言片段、以及整体证据引用；
- `InterpreterError` 仅用于**程序级失败**（上下文构造失败等）；LLM 输出无法解析
  使用其子类 `InterpreterParseError`，由 IntentEngine 转为可恢复 issue（不抛异常）。

全部模型 `frozen=True, extra="forbid"`；默认空列表一律 `default_factory`，避免
实例间共享可变对象（与 Step 01～03 同约定）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from visual_intent_agent.domain import EvidenceRef, IntentDelta, Issue, VisualIntent
from visual_intent_agent.policy import QuestionSpec

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# ---------------------------------------------------------------------------
# Interpreter 错误 code（命名空间 interpreter.*；ARCHITECTURE.md 5.5）
# ---------------------------------------------------------------------------

#: LLM 输出无法解析为 InterpreterResult 的**前缀**（Step 06 可按前缀分类）。
INTERPRETER_UNPARSEABLE_OUTPUT = "interpreter.unparseable_output"
#: 模型返回空文本。
INTERPRETER_EMPTY_OUTPUT = "interpreter.unparseable_output.empty"
#: 文本不是合法 JSON。
INTERPRETER_INVALID_JSON = "interpreter.unparseable_output.invalid_json"
#: JSON 合法但不符合输出 Schema（缺字段 / 多余字段 / 类型错误）。
INTERPRETER_SCHEMA_VIOLATION = "interpreter.unparseable_output.schema_violation"
#: 模型返回了完整 Intent 而不是 Candidate Delta（任务书明令禁止）。
INTERPRETER_FULL_INTENT = "interpreter.unparseable_output.full_intent"
#: 单条候选 Delta 违反 domain.IntentDelta 冻结形状（如 SET 无 value/resolution）。
INTERPRETER_INVALID_DELTA = "interpreter.unparseable_output.invalid_delta"
#: 上下文提供 Pending Question 但没有 question_id，无法证明授权范围（程序级失败）。
INTERPRETER_PENDING_QUESTION_MISSING_ID = "interpreter.pending_question_missing_id"
#: 上下文提供的 Pending Question 指向白名单之外的路径（程序级失败）。
INTERPRETER_PENDING_QUESTION_PATH_INVALID = "interpreter.pending_question_path_invalid"
#: LLM 自报冲突的可观察 issue code。
INTERPRETER_CONFLICT_DETECTED = "interpreter.conflict_detected"
#: LLM 自报"无法映射为明确值"的语言片段的可观察 issue code。
INTERPRETER_UNRESOLVED_LANGUAGE = "interpreter.unresolved_language"

#: 全部解析失败 code（import 时自检，避免文档与实现漂移）。
PARSE_FAILURE_CODES: frozenset[str] = frozenset(
    {
        INTERPRETER_EMPTY_OUTPUT,
        INTERPRETER_INVALID_JSON,
        INTERPRETER_SCHEMA_VIOLATION,
        INTERPRETER_FULL_INTENT,
        INTERPRETER_INVALID_DELTA,
    }
)


class IntentResolveRequest(BaseModel):
    """一次 Intent 解析请求（Step 05 冻结；Step 09 FeedbackEngine 复用同一形状约定）。

    - `current_intent`：当前 Draft（只读；Interpreter 不得修改）；
    - `message_id`：本轮用户消息的已存 ID（证据引用的唯一来源）；
    - `message_text`：本轮用户原文；
    - `pending_question`：当前**唯一**待答问题（Step 03 `QuestionSpec`，Step 06 经
      `PendingQuestion.to_spec()` 填充 `question_text` / `question_id`）；无则 None；
    - `available_message_ids`：会话中可作为证据的消息 ID 集合（Validator 证据边界）。
    """

    model_config = _FROZEN

    current_intent: VisualIntent
    message_id: str
    message_text: str
    pending_question: QuestionSpec | None = None
    available_message_ids: frozenset[str]


class InterpreterResult(BaseModel):
    """Interpreter 的唯一输出（LLM 输出合同，任务书「LLM 输出合同」）。

    - `candidate_deltas`：只含 Candidate Delta；**仍必须**通过 Step 02 Validator；
    - `detected_conflicts`：LLM 观察到的冲突（可观察 issue，确定性代码不据此改状态）；
    - `unresolved_language`：无法映射为明确 Delta 的语言片段（需澄清，不得猜测补值）；
    - `evidence_refs`：本次解释的整体证据（去重后的 Delta 证据并集）。
    """

    model_config = _FROZEN

    candidate_deltas: list[IntentDelta] = Field(default_factory=list)
    detected_conflicts: list[Issue] = Field(default_factory=list)
    unresolved_language: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class InterpreterError(Exception):
    """Interpreter 的程序级失败（带 `.code` 与 `.retryable`）。

    仅用于**上下文构造失败**等调用方错误（如 Pending Question 缺 question_id）；
    `IntentEngine.resolve` 不捕获本类，异常向上抛（ARCHITECTURE.md 5.6 硬门禁）。
    LLM 输出无法解析用子类 `InterpreterParseError`，由 IntentEngine 转为可恢复 issue。
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - 由测试覆盖
        return f"{self.code}: {self.message}"


class InterpreterParseError(InterpreterError):
    """LLM 输出无法解析（可恢复：不猜测修复，交 Step 06 决定重试或澄清）。

    `retryable=True` 表示允许 Step 06 做**最多一次**重试（设计书「不合法输出最多
    修复一次」）；Step 05 自身不重试、不修复。
    """

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        if code not in PARSE_FAILURE_CODES:
            raise ValueError(f"unknown interpreter parse failure code: {code!r}")
        super().__init__(code, message, retryable=retryable)


__all__ = [
    "IntentResolveRequest",
    "InterpreterResult",
    "InterpreterError",
    "InterpreterParseError",
    "INTERPRETER_UNPARSEABLE_OUTPUT",
    "INTERPRETER_EMPTY_OUTPUT",
    "INTERPRETER_INVALID_JSON",
    "INTERPRETER_SCHEMA_VIOLATION",
    "INTERPRETER_FULL_INTENT",
    "INTERPRETER_INVALID_DELTA",
    "INTERPRETER_PENDING_QUESTION_MISSING_ID",
    "INTERPRETER_PENDING_QUESTION_PATH_INVALID",
    "INTERPRETER_CONFLICT_DETECTED",
    "INTERPRETER_UNRESOLVED_LANGUAGE",
    "PARSE_FAILURE_CODES",
]
