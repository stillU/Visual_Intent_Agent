"""Step 03 公开面：DecisionPolicy 与 IntentResolution（ARCHITECTURE.md 4「Step 03」）。

    from visual_intent_agent.policy import (
        assess, IntentResolution, QuestionSpec, DecisionPolicy,
        Materiality, PolicyAction, UnresolvedDecision,
        DECISION_POLICIES, POLICY_VERSION,
    )

本包只做确定性规则判断：不理解自然语言、不调用 LLM、不生成澄清文案、不接触
数据库/网络、不编译 Prompt。它回答唯一核心问题：

    当前还有哪些重要视觉决策必须由用户解决？

`assess(intent, execution_context=None) -> IntentResolution` 的 `applied_deltas`
恒为空列表，由 Step 05 IntentEngine 回填。

规则表版本、`required_if` 封闭条件集与全部冲突规则 id 见
`visual_intent_agent/policy/decision_policy.py` 与 `docs/handoffs/step_03_handoff.md`。
"""

from __future__ import annotations

from .decision_policy import DECISION_POLICIES, POLICY_VERSION, assess
from .models import (
    DecisionPolicy,
    IntentResolution,
    Materiality,
    PolicyAction,
    QuestionSpec,
    UnresolvedDecision,
)

__all__ = [
    "assess",
    "IntentResolution",
    "QuestionSpec",
    "DecisionPolicy",
    "Materiality",
    "PolicyAction",
    "UnresolvedDecision",
    "DECISION_POLICIES",
    "POLICY_VERSION",
]
