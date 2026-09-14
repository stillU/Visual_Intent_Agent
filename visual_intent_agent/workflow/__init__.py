"""Step 06 公开面：澄清问题、确认摘要与 P1 工作流用例（ARCHITECTURE.md 4「Step 06」）。

    from visual_intent_agent.workflow import (
        WorkflowService, SubmitMessageOutcome,
        PendingQuestion, QuestionBuilder,
        ConfirmationSummary, WorkflowError,
        compute_summary_hash, build_confirmation_summary, derive_change_summary,
    )
    from visual_intent_agent.workflow.confirmation import (
        WORKFLOW_ERROR_CODES, WORKFLOW_INVALID_STATE, WORKFLOW_STALE_REVISION,
        WORKFLOW_SUMMARY_HASH_MISMATCH,
    )
    from visual_intent_agent.workflow.questions import render_question_text

本包把 Step 04（Repository / 状态机）与 Step 05（IntentEngine）集成为 P1 闭环：

    用户文本 → IntentEngine → 必要澄清（每轮一个问题）→ Ready
             → WAITING_CONFIRMATION → 用户确认当前 revision

Hard Confirmation Gate：`confirm_current_intent` 是唯一确认入口，只接受当前
intent/execution revision 与按当前快照重算一致的 `summary_hash`；本包不生成
Prompt、不调用图像 Provider、不迁移到 GENERATING（Step 07/08 的职责）。
"""

from __future__ import annotations

from .confirmation import (
    WORKFLOW_ERROR_CODES,
    WORKFLOW_INVALID_STATE,
    WORKFLOW_STALE_REVISION,
    WORKFLOW_SUMMARY_HASH_MISMATCH,
    ConfirmationSummary,
    WorkflowError,
    build_confirmation_summary,
    compute_summary_hash,
    derive_change_summary,
)
from .questions import PendingQuestion, QuestionBuilder, render_question_text
from .service import (
    DEFAULT_OUTPUT_SIZE,
    MAX_INTERPRETATION_ATTEMPTS,
    SubmitMessageOutcome,
    WorkflowService,
)

__all__ = [
    "WorkflowService",
    "SubmitMessageOutcome",
    "PendingQuestion",
    "QuestionBuilder",
    "render_question_text",
    "ConfirmationSummary",
    "WorkflowError",
    "compute_summary_hash",
    "build_confirmation_summary",
    "derive_change_summary",
    "WORKFLOW_ERROR_CODES",
    "WORKFLOW_INVALID_STATE",
    "WORKFLOW_STALE_REVISION",
    "WORKFLOW_SUMMARY_HASH_MISMATCH",
    "DEFAULT_OUTPUT_SIZE",
    "MAX_INTERPRETATION_ATTEMPTS",
]
