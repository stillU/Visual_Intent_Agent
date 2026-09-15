"""IntentEngine：把 Interpreter、Validator、Reducer、DecisionPolicy 编排为一次 resolve。

    resolve(current_intent, message, pending_question)
        ↓ 1. 构建最小证据上下文（EvidenceContext）
        ↓ 2. Interpreter（LLM）→ InterpreterResult
        ↓ 3. Validator（确定性）→ accepted / rejected + issues
        ↓ 4. 被拒 Delta 的 issue 全部保留（不静默吞）
        ↓ 5. Reducer（仅 accepted）→ ReduceResult
        ↓ 6. DecisionPolicy.assess → IntentResolution（applied_deltas 恒空）
        ↓ 7. 回填 applied_deltas 与合并后的 issues → IntentResolution

IntentEngine **不拥有领域规则**：它只决定调用顺序与错误边界。

错误边界（ARCHITECTURE.md 4「Step 05」/ 5.6）：

- LLM 输出无法解析（`InterpreterParseError`）或 Provider 调用失败（`ProviderError`）
  → **不抛异常**；返回 `IntentResolution`（intent 原样、`applied_deltas=[]`、
  `ready_for_confirmation=False`、issues 含 `interpreter.*` / `provider.*`），由
  Step 06 决定重试或澄清。不猜测修复用户意图。
- 调用方上下文错误（`InterpreterError`，如 Pending Question 缺 question_id）→ 向上抛
  （程序级失败，带 `.code`）。
- Reducer 的 `ReducerError` 同样向上抛：它只会在"绕过 Validator"的编程错误下出现，
  不属于可恢复业务失败。
"""

from __future__ import annotations

from visual_intent_agent.domain import Issue, Severity
from visual_intent_agent.policy import IntentResolution, assess
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.validation import EvidenceContext, reduce, validate

from .interpreter import Interpreter, unresolved_language_issues
from .models import (
    InterpreterParseError,
    InterpreterResult,
    IntentResolveRequest,
)


class IntentEngine:
    """Intent 解析编排器（唯一入口 `resolve`）。"""

    def __init__(self, interpreter: Interpreter) -> None:
        self._interpreter = interpreter

    def resolve(self, request: IntentResolveRequest) -> IntentResolution:
        """把一条用户消息解析为 `IntentResolution`（纯编排，无 IO/持久化）。"""
        context = self._build_evidence_context(request)

        try:
            interpreted = self._interpreter.interpret(request)
        except InterpreterParseError as exc:
            return self._failure_resolution(request, self._interpreter_issue(exc))
        except ProviderError as exc:
            return self._failure_resolution(request, self._provider_issue(exc))

        validation = validate(
            interpreted.candidate_deltas, context, request.current_intent
        )
        reduced = reduce(request.current_intent, validation.accepted)
        policy_resolution = assess(reduced.intent, request.execution_context)

        issues: list[Issue] = []
        issues.extend(validation.issues)
        issues.extend(self._interpreter_observation_issues(interpreted))
        issues.extend(policy_resolution.issues)

        return policy_resolution.model_copy(
            update={
                "applied_deltas": list(validation.accepted),
                "issues": issues,
            }
        )

    # -- 内部实现 ----------------------------------------------------------

    @staticmethod
    def _build_evidence_context(request: IntentResolveRequest) -> EvidenceContext:
        """只从请求构造证据边界；Pending Question 的本地授权范围在此进入 Validator。"""
        pending = request.pending_question
        return EvidenceContext(
            available_message_ids=request.available_message_ids,
            pending_question_id=pending.question_id if pending is not None else None,
            pending_question_path=(
                pending.target_path if pending is not None else None
            ),
        )

    @staticmethod
    def _interpreter_observation_issues(result: InterpreterResult) -> list[Issue]:
        """把 Interpreter 的非 Delta 观察转成可观察 issue（LLM 冲突 + 未解析语言）。"""
        return [*result.detected_conflicts, *unresolved_language_issues(result)]

    @staticmethod
    def _failure_resolution(
        request: IntentResolveRequest, issue: Issue
    ) -> IntentResolution:
        """可恢复失败的统一返回：intent 原样、无 Delta、不可确认、issue 可观察。"""
        base = assess(request.current_intent, request.execution_context)
        return base.model_copy(
            update={
                "applied_deltas": [],
                "issues": [issue, *base.issues],
                "ready_for_confirmation": False,
            }
        )

    @staticmethod
    def _interpreter_issue(exc: InterpreterParseError) -> Issue:
        return Issue(
            code=exc.code,
            message=f"{exc.message} (retryable={exc.retryable})",
            severity=Severity.ERROR,
        )

    @staticmethod
    def _provider_issue(exc: ProviderError) -> Issue:
        status = "" if exc.status_code is None else f", status={exc.status_code}"
        return Issue(
            code=exc.code,
            message=(
                f"{exc.message} (retryable={exc.retryable}{status}); "
                "the intent is unchanged and no delta was applied"
            ),
            severity=Severity.ERROR,
        )


__all__ = ["IntentEngine"]
