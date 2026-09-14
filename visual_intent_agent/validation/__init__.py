"""Step 02 公开面：Validator 与 Reducer（ARCHITECTURE.md 4「Step 02 — validation」）。

    from visual_intent_agent.validation import (
        validate, reduce,
        ValidationResult, ChangeSummary, ReduceResult,
        EvidenceContext, RejectedDelta,
    )

本包是"LLM 不能直接管理状态"的核心边界：LLM 只能提出 Candidate `IntentDelta`，
经 `validate` 显式接受/拒绝后，才由 `reduce` 确定性归并为新 `VisualIntent`。

本包只含确定性代码：无 LLM、无数据库、无网络、无 Prompt 编译、无澄清问题生成。
"""

from __future__ import annotations

from .models import (
    ChangeSummary,
    EvidenceContext,
    ReduceResult,
    RejectedDelta,
    ValidationResult,
)
from .reducer import ReducerError, reduce
from .validator import validate

__all__ = [
    "validate",
    "reduce",
    "ValidationResult",
    "ChangeSummary",
    "ReduceResult",
    "EvidenceContext",
    "RejectedDelta",
    "ReducerError",
]
