"""MVP v0.3 Step 03：L1~L3 自动指标子包（协议第 4 节的唯一实现）。

- `evaluation.metrics.state`  —— L1 State Correctness（仅 System B；四不变量）；
- `evaluation.metrics.intent` —— L2 Intent Understanding（仅 System B；五指标）；
- `evaluation.metrics.prompt` —— L3 Prompt Semantics（A/B 同口径；四指标）。

指标函数全部是**纯函数**：输入为 `evaluation.reporting` 的观察/标注合同与
纯数据快照，输出为 `MetricPayload`；三态（`not_applicable` / `missing_data` /
`failed`）由 Runner 按协议第 5 节包装进 `MetricRecord`，绝不从分母静默剔除。
L4 图片盲评不在本子包（Step 04）。
"""

from evaluation.metrics.intent import (
    evaluate_clarification_precision,
    evaluate_conflict_detection,
    evaluate_delegation_scope,
    evaluate_delta_accuracy,
    evaluate_missing_decision_recall,
    normalize_token,
    value_matches,
)
from evaluation.metrics.prompt import (
    evaluate_intent_coverage,
    evaluate_model_compatibility,
    evaluate_preservation,
    evaluate_unauthorized_addition,
    keyword_hit,
)
from evaluation.metrics.state import (
    L1CaseEvaluation,
    L1TurnEvidence,
    L1Violation,
    TableSnapshot,
    check_history_append_only,
    check_no_stale_confirmation,
    check_pinned_not_overwritten,
    check_unauthorized_fields_unchanged,
    evaluate_case_l1,
)
from evaluation.reporting import MetricPayload

__all__ = [
    "MetricPayload",
    "TableSnapshot",
    "L1TurnEvidence",
    "L1Violation",
    "L1CaseEvaluation",
    "check_unauthorized_fields_unchanged",
    "check_no_stale_confirmation",
    "check_pinned_not_overwritten",
    "check_history_append_only",
    "evaluate_case_l1",
    "normalize_token",
    "value_matches",
    "evaluate_delta_accuracy",
    "evaluate_missing_decision_recall",
    "evaluate_clarification_precision",
    "evaluate_conflict_detection",
    "evaluate_delegation_scope",
    "keyword_hit",
    "evaluate_intent_coverage",
    "evaluate_unauthorized_addition",
    "evaluate_preservation",
    "evaluate_model_compatibility",
]
