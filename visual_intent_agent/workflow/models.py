"""跨 workflow 用例的"可恢复失败"分类（MVP v0.3 更改书 002 · 工包 B）。

`WorkflowService.submit_message`（Step 06）与 `ReviewService.submit_feedback`（Step 09）
必须对同一类失败给出一致的、机器可读的判定：本轮**没有成功处理**，因此

- 用户消息保留（调用方已在判定前落库）；
- 原 Intent / 既有 PIN / 既有 revision 不变；
- 不用"空理解"结果去推进会话（不伪造新的澄清问题）；
- 客户端可显式重试；重试复用既有 Adapter / 引擎的有界重试，**不新增第三层**。

本模块只提供**纯分类函数**，不持有状态、不产生副作用。失败 code 全部复用既有冻结常量：

- `interpreter.unparseable_output*`（Step 05 `intent_engine.models`）：LLM 输出无法解析；
- `provider.*`（Step 05 `providers.errors`）：Provider 层失败（可重试或不可重试）；
- `workflow.stale_revision`（Step 06 `confirmation`）：请求绑定的 revision 已不是当前。

`failure_codes` 是**结果字段**（不是异常），绝不作为 `WorkflowError.code` 抛出。
`workflow.stale_revision` 复用的就是既有 `WorkflowError` 取值，语义一致：请求绑定的
revision 已过期，其结果不得应用到新 revision。
"""

from __future__ import annotations

from collections.abc import Iterable

from visual_intent_agent.domain import Issue, Severity
from visual_intent_agent.intent_engine.models import INTERPRETER_UNPARSEABLE_OUTPUT
from visual_intent_agent.providers.errors import PROVIDER_ERROR_CODES

from .confirmation import WORKFLOW_STALE_REVISION

#: 引擎层（Interpreter / FeedbackEngine）可恢复失败的 code 前缀判定。
INTERPRETER_UNPARSEABLE_PREFIX: str = f"{INTERPRETER_UNPARSEABLE_OUTPUT}."
#: FeedbackEngine 的解析失败前缀（Step 09 `feedback.models.FEEDBACK_UNPARSEABLE_OUTPUT`）。
#: 这里用字面量而不是 import：`workflow/models.py` 由 Step 06 的 `service.py` 导入，
#: 不得经 `feedback` 间接依赖 `generation` / `prompt_engine`（workflow 依赖边界）。
FEEDBACK_UNPARSEABLE_OUTPUT_PREFIX: str = "feedback.unparseable_output"


def is_engine_failure_code(code: str) -> bool:
    """code 是否表示"引擎没能产出可用理解"（解析失败或 Provider 失败）。

    只覆盖两类**可恢复失败**，其余 code（`policy.*` 观察、`interpreter.unresolved_language`
    / `interpreter.conflict_detected` 等观察）**不**算失败：

    - `interpreter.unparseable_output` 及其子 code（`*.empty` / `*.invalid_json` / ...）；
    - `feedback.unparseable_output` 及其子 code（Step 09 反馈解析失败）；
    - `providers.errors.PROVIDER_ERROR_CODES` 的全部 `provider.*`。

    `ProviderError` 的可重试与否由 `retryable` 决定（重试预算已被既有层用尽），
    分类本身不重试、不改状态。
    """
    return (
        code == INTERPRETER_UNPARSEABLE_OUTPUT
        or code.startswith(INTERPRETER_UNPARSEABLE_PREFIX)
        or code == FEEDBACK_UNPARSEABLE_OUTPUT_PREFIX
        or code.startswith(f"{FEEDBACK_UNPARSEABLE_OUTPUT_PREFIX}.")
        or code in PROVIDER_ERROR_CODES
    )


def engine_failure_codes(issues: Iterable[Issue]) -> tuple[str, ...]:
    """按出现顺序去重，提取"引擎失败"code（无则空元组）。"""
    return _unique(issue.code for issue in issues if is_engine_failure_code(issue.code))


def failure_codes_from_issues(issues: Iterable[Issue]) -> tuple[str, ...]:
    """按出现顺序去重，提取全部 ERROR 级 issue code（对外可读的失败原因）。

    用于 `SubmitMessageOutcome.failure_codes` / `FeedbackOutcome.failure_codes`：
    客户端据此显示"本轮未处理成功"的具体原因，而不是解析自由文本。
    """
    return _unique(issue.code for issue in issues if issue.severity is Severity.ERROR)


def turn_failure_codes(
    issues: Iterable[Issue], *, stale: bool = False
) -> tuple[str, ...]:
    """一轮可恢复失败的完整 code 列表（可选前置 `workflow.stale_revision`）。

    `stale=True` 时把既有 `WORKFLOW_STALE_REVISION` 放在最前：它表示"请求绑定的
    revision 已过期"，客户端应刷新会话后重试，而不是把本轮结果当作已应用。
    """
    codes = list(failure_codes_from_issues(issues))
    if stale and WORKFLOW_STALE_REVISION not in codes:
        codes.insert(0, WORKFLOW_STALE_REVISION)
    return tuple(codes)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    """按出现顺序去重（确定性；与 Step 06/09 的既有去重口径一致）。"""
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return tuple(unique)


__all__ = [
    "FEEDBACK_UNPARSEABLE_OUTPUT_PREFIX",
    "INTERPRETER_UNPARSEABLE_PREFIX",
    "WORKFLOW_STALE_REVISION",
    "engine_failure_codes",
    "failure_codes_from_issues",
    "is_engine_failure_code",
    "turn_failure_codes",
]
