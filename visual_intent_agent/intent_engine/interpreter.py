"""Interpreter：`LLM → Candidate IntentDelta` 的唯一边界（Step 05 核心）。

    IntentResolveRequest ──▶ Interpreter.interpret() ──▶ InterpreterResult

职责与边界（任务书「Interpreter 边界」）：

可以：
- 识别本轮用户表达的一个或多个修改，输出 Candidate Delta；
- 为每个 Delta 标注用户原文证据；
- 识别用户是否回答 Pending Question、是否明确委托 / 明确保持 / 解除保持。

不可以：
- 返回完整 Intent 替代 Delta（解析失败，不猜测修复）；
- 直接设置 Workflow 状态 / 计算 `ready_for_confirmation` / 自动确认；
- 创建超出 Schema 白名单的路径（交 Validator 显式拒绝）；
- 把"用户没说"解释为 delegated。

实现约定：

- **证据**：LLM 不输出 ID；每条 Delta 的 `EvidenceRef.message_id` 由系统填入
  `request.message_id`，`fragment` 取 LLM 回填的用户原文片段；当 LLM 声明该 Delta
  回答了当前 Pending Question 时，系统附上 `pending_question_id`——这样 Step 02
  Validator 才能核验"授权不扩大"。系统只做**标注**，绝不改写 operation / path / value。
- **不修复**：任何解析失败（空文本、非法 JSON、Schema 不符、完整 Intent、单条 Delta
  违反冻结形状）都抛 `InterpreterParseError`；由 IntentEngine 转为可恢复 issue。
  唯一的"容错"是剥离模型常见的 Markdown 代码围栏（纯文本规范化，不改变业务语义）。
- **不重试**：重试与"最多修复一次"由 Step 06 依据 `InterpreterError.retryable` 决定。
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from visual_intent_agent.domain import (
    INTENT_PATHS,
    DeltaOperation,
    EvidenceRef,
    IntentDelta,
    Issue,
    Resolution,
    Severity,
)
from visual_intent_agent.providers.llm import LLMMessage, LLMProvider, LLMRequest

from .models import (
    INTERPRETER_CONFLICT_DETECTED,
    INTERPRETER_EMPTY_OUTPUT,
    INTERPRETER_FULL_INTENT,
    INTERPRETER_INVALID_DELTA,
    INTERPRETER_INVALID_JSON,
    INTERPRETER_PENDING_QUESTION_MISSING_ID,
    INTERPRETER_PENDING_QUESTION_PATH_INVALID,
    INTERPRETER_SCHEMA_VIOLATION,
    INTERPRETER_UNRESOLVED_LANGUAGE,
    InterpreterError,
    InterpreterParseError,
    InterpreterResult,
    IntentResolveRequest,
)
from .prompts import (
    INTERPRETER_SYSTEM_PROMPT_V1,
    build_interpreter_user_prompt,
)

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: 发送给 OpenAI 兼容端点的结构化输出开关（不改变 LLMProvider 合同）。
INTERPRETER_RESPONSE_FORMAT: dict = {"type": "json_object"}

#: 剥离 Markdown 代码围栏（模型常见包装；纯规范化，不解释业务语义）。
_FENCE_RE = re.compile(r"\A```[^\n]*\r?\n(?P<body>.*?)\r?\n?```\s*\Z", re.DOTALL)

#: 若模型返回完整 Intent，payload 会出现这些键（且没有 candidate_deltas）。
_FULL_INTENT_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "intent_id",
        "subject",
        "composition",
        "environment",
        "style",
        "lighting",
        "camera",
        "color",
        "resolutions",
        "pinned_paths",
        "facets",
        "intent",
    }
)


# ---------------------------------------------------------------------------
# LLM 输出的内部人读 Schema（与 prompts.INTERPRETER_OUTPUT_SCHEMA 一一对应）
# ---------------------------------------------------------------------------


class _LLMConflict(BaseModel):
    """LLM 自报冲突（code 由系统覆盖为 `interpreter.conflict_detected`）。"""

    model_config = _FROZEN

    code: str | None = None
    message: str
    path: str | None = None


class _LLMDelta(BaseModel):
    """LLM 输出的单条候选 Delta（系统只重填证据，不改业务字段）。"""

    model_config = _FROZEN

    operation: DeltaOperation
    path: str
    value: str | int | None = None
    resolution: Resolution | None = None
    answers_pending_question: bool = False
    evidence_fragment: str | None = None


class _LLMOutput(BaseModel):
    """LLM 输出的顶层结构（`candidate_deltas` 必填）。"""

    model_config = _FROZEN

    candidate_deltas: list[_LLMDelta] = Field(default_factory=list)
    detected_conflicts: list[_LLMConflict] = Field(default_factory=list)
    unresolved_language: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


class Interpreter:
    """把一次用户消息解释为 `InterpreterResult`（LLM 的唯一调用点）。"""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    # -- 公开面 ------------------------------------------------------------

    def interpret(self, request: IntentResolveRequest) -> InterpreterResult:
        """调用一次 LLM 并解析为 `InterpreterResult`。

        失败语义：

        - `ProviderError`：向上抛（由 IntentEngine 转为 `provider.*` issue）；
        - `InterpreterParseError`：向上抛（由 IntentEngine 转为 `interpreter.*` issue）；
        - `InterpreterError`（如 Pending Question 缺 question_id）：向上抛（调用方错误）。
        """
        self._check_context(request)
        llm_request = LLMRequest(
            messages=[
                LLMMessage(role="system", content=INTERPRETER_SYSTEM_PROMPT_V1),
                LLMMessage(role="user", content=build_interpreter_user_prompt(request)),
            ],
            response_format=dict(INTERPRETER_RESPONSE_FORMAT),
        )
        response = self._llm.complete(llm_request)
        payload = self._load_payload(response.content)
        output = self._validate_payload(payload)
        return self._to_result(output, request)

    # -- 上下文校验（程序级失败） -------------------------------------------

    @staticmethod
    def _check_context(request: IntentResolveRequest) -> None:
        pending = request.pending_question
        if pending is None:
            return
        if not pending.question_id:
            raise InterpreterError(
                INTERPRETER_PENDING_QUESTION_MISSING_ID,
                "pending_question has no question_id; the delegation scope of an answer "
                "cannot be proven without it (Step 02 evidence binding requires an id)",
            )
        if pending.target_path not in INTENT_PATHS:
            raise InterpreterError(
                INTERPRETER_PENDING_QUESTION_PATH_INVALID,
                f"pending_question.target_path {pending.target_path!r} is not in the "
                "Schema v1 whitelist",
            )

    # -- 解析 --------------------------------------------------------------

    @classmethod
    def _load_payload(cls, content: str) -> dict:
        text = content.strip()
        if not text:
            raise InterpreterParseError(
                INTERPRETER_EMPTY_OUTPUT,
                "LLM returned an empty response body",
            )
        fence = _FENCE_RE.match(text)
        if fence is not None:
            text = fence.group("body").strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InterpreterParseError(
                INTERPRETER_INVALID_JSON,
                f"LLM output is not valid JSON ({exc.msg} at line {exc.lineno} "
                f"column {exc.colno})",
            ) from None
        if not isinstance(payload, dict):
            raise InterpreterParseError(
                INTERPRETER_SCHEMA_VIOLATION,
                f"LLM output must be a JSON object; got {type(payload).__name__}",
            )
        if "candidate_deltas" not in payload and (
            _FULL_INTENT_KEYS & set(payload)
        ):
            raise InterpreterParseError(
                INTERPRETER_FULL_INTENT,
                "LLM returned a full Intent instead of candidate deltas; "
                "the Interpreter never accepts a replacement Intent",
            )
        return payload

    @staticmethod
    def _validate_payload(payload: dict) -> _LLMOutput:
        try:
            return _LLMOutput.model_validate(payload)
        except ValidationError as exc:
            details = "; ".join(
                f"{'/'.join(str(part) for part in error['loc']) or '<root>'}: "
                f"{error['type']}"
                for error in exc.errors()[:5]
            )
            raise InterpreterParseError(
                INTERPRETER_SCHEMA_VIOLATION,
                "LLM output does not match INTERPRETER_OUTPUT_SCHEMA "
                f"({exc.error_count()} error(s): {details})",
            ) from None

    # -- 结果构造 ----------------------------------------------------------

    def _to_result(
        self, output: _LLMOutput, request: IntentResolveRequest
    ) -> InterpreterResult:
        pending = request.pending_question

        deltas: list[IntentDelta] = []
        evidence: list[EvidenceRef] = []
        for candidate in output.candidate_deltas:
            answers_pending = bool(
                candidate.answers_pending_question and pending is not None
            )
            ref = EvidenceRef(
                message_id=request.message_id,
                fragment=candidate.evidence_fragment,
                pending_question_id=pending.question_id if answers_pending else None,
            )
            try:
                delta = IntentDelta(
                    operation=candidate.operation,
                    path=candidate.path,
                    value=candidate.value,
                    resolution=candidate.resolution,
                    evidence_refs=[ref],
                )
            except ValidationError as exc:
                raise InterpreterParseError(
                    INTERPRETER_INVALID_DELTA,
                    f"candidate delta for path {candidate.path!r} violates the frozen "
                    f"IntentDelta shape ({exc.error_count()} error(s))",
                ) from None
            deltas.append(delta)
            evidence.append(ref)

        conflicts = [
            Issue(
                code=INTERPRETER_CONFLICT_DETECTED,
                message=(
                    conflict.message
                    if conflict.code is None
                    else f"{conflict.message} (llm_code={conflict.code})"
                ),
                path=conflict.path,
            )
            for conflict in output.detected_conflicts
        ]

        unresolved = [
            fragment
            for fragment in dict.fromkeys(output.unresolved_language)
            if fragment.strip()
        ]

        return InterpreterResult(
            candidate_deltas=deltas,
            detected_conflicts=conflicts,
            unresolved_language=unresolved,
            evidence_refs=_dedupe_evidence(evidence),
        )


def _dedupe_evidence(refs: list[EvidenceRef]) -> list[EvidenceRef]:
    """按出现顺序去重（保留确定性顺序；frozen 模型可哈希比较字段）。"""
    seen: set[tuple[str, str | None, str | None]] = set()
    unique: list[EvidenceRef] = []
    for ref in refs:
        key = (ref.message_id, ref.fragment, ref.pending_question_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique


#: 供测试与调用方读取的"可观察 issue"构造器（unresolved_language → warning issue）。
def unresolved_language_issues(result: InterpreterResult) -> list[Issue]:
    """把 `unresolved_language` 转成可观察的 warning issue（不改变状态）。"""
    return [
        Issue(
            code=INTERPRETER_UNRESOLVED_LANGUAGE,
            message=f"unresolved language, needs clarification: {fragment}",
            severity=Severity.WARNING,
        )
        for fragment in result.unresolved_language
    ]


__all__ = [
    "Interpreter",
    "INTERPRETER_RESPONSE_FORMAT",
    "unresolved_language_issues",
]
