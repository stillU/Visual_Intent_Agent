"""Step 04 状态机：7 个持久状态与冻结的合法迁移表（ARCHITECTURE.md 4「Step 04」）。

- 持久状态恰好 7 个；`RETRIEVING` / `REFINING` **不是**持久状态。
- `ALLOWED_TRANSITIONS` 恰好 11 条：任务书"至少覆盖"的 10 条 + **Rev.1 增补**
  `WAITING_CONFIRMATION → UNDERSTANDING`（架构裁定见
  `docs/handoffs/architecture_decision_001.md` 提案 1；ARCHITECTURE.md 4 已按 Rev.1 更新）。
- "new session → UNDERSTANDING" 是会话的初始状态（由 `create_session` 直接写入），
  不是两条持久状态之间的迁移，因此**不在**迁移表内。
- 非法迁移一律由 `InvalidStateTransitionError` 显式拒绝，且不落库（见 repository）。
- `to_state == 当前状态` 不是迁移，而是幂等重复请求：`transition_state` 返回成功且零写入
  （Step 06 冻结流程每轮用户消息都会调用 "状态进入 UNDERSTANDING"）。
"""

from __future__ import annotations

from enum import Enum


class WorkflowState(str, Enum):
    """会话的 7 个持久状态（无 RETRIEVING / REFINING）。"""

    UNDERSTANDING = "UNDERSTANDING"
    WAITING_CLARIFICATION = "WAITING_CLARIFICATION"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    GENERATING = "GENERATING"
    WAITING_REVIEW = "WAITING_REVIEW"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


#: 任务书「必须支持的状态迁移」10 条 + Rev.1 增补第 11 条，逐条冻结。
#:
#: UNDERSTANDING              -> WAITING_CLARIFICATION
#: UNDERSTANDING              -> WAITING_CONFIRMATION
#: WAITING_CLARIFICATION      -> UNDERSTANDING
#: WAITING_CONFIRMATION       -> GENERATING
#: GENERATING                 -> WAITING_REVIEW
#: GENERATING                 -> FAILED
#: FAILED                     -> GENERATING
#: FAILED                     -> WAITING_CONFIRMATION
#: WAITING_REVIEW             -> UNDERSTANDING
#: WAITING_REVIEW             -> COMPLETED
#: WAITING_CONFIRMATION       -> UNDERSTANDING   # 第 11 条：Rev.1 增补（确认页收到修改消息）
ALLOWED_TRANSITIONS: frozenset[tuple[WorkflowState, WorkflowState]] = frozenset(
    {
        (WorkflowState.UNDERSTANDING, WorkflowState.WAITING_CLARIFICATION),
        (WorkflowState.UNDERSTANDING, WorkflowState.WAITING_CONFIRMATION),
        (WorkflowState.WAITING_CLARIFICATION, WorkflowState.UNDERSTANDING),
        (WorkflowState.WAITING_CONFIRMATION, WorkflowState.GENERATING),
        # Rev.1 增补（architecture_decision_001.md 提案 1）：
        # 确认页收到用户修改消息时回到 UNDERSTANDING 重新理解。
        (WorkflowState.WAITING_CONFIRMATION, WorkflowState.UNDERSTANDING),
        (WorkflowState.GENERATING, WorkflowState.WAITING_REVIEW),
        (WorkflowState.GENERATING, WorkflowState.FAILED),
        (WorkflowState.FAILED, WorkflowState.GENERATING),
        (WorkflowState.FAILED, WorkflowState.WAITING_CONFIRMATION),
        (WorkflowState.WAITING_REVIEW, WorkflowState.UNDERSTANDING),
        (WorkflowState.WAITING_REVIEW, WorkflowState.COMPLETED),
    }
)


def _state_label(state: WorkflowState | str | None) -> str:
    """稳定、可读的状态名（用于错误消息；不参与任何业务判定）。"""
    if state is None:
        return "<none>"
    if isinstance(state, WorkflowState):
        return state.value
    return str(state)


class InvalidStateTransitionError(Exception):
    """非法状态迁移：显式拒绝，绝不落库。

    `.code` 固定为 `persistence.invalid_state_transition`（ARCHITECTURE.md 5.5）。
    """

    code = "persistence.invalid_state_transition"

    def __init__(
        self,
        session_id: str,
        from_state: WorkflowState | str | None,
        to_state: WorkflowState | str | None,
        *,
        reason: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.from_state = from_state
        self.to_state = to_state
        detail = (
            f"illegal workflow state transition for session {session_id!r}: "
            f"{_state_label(from_state)} -> {_state_label(to_state)}"
        )
        if reason:
            detail = f"{detail} ({reason})"
        detail = f"{detail}; allowed transitions are declared in ALLOWED_TRANSITIONS"
        super().__init__(detail)


__all__ = ["WorkflowState", "ALLOWED_TRANSITIONS", "InvalidStateTransitionError"]
