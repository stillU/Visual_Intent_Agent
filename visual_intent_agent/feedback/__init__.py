"""Step 09 公开面：反馈解释（ARCHITECTURE.md 4「Step 09 — feedback」）。

    from visual_intent_agent.feedback import (
        FeedbackEngine, FeedbackDecision, FeedbackRequest, FeedbackResult,
        FeedbackError, FeedbackParseError,
        clarify_target_path, is_unparseable,
        FEEDBACK_PROMPT_VERSION, FEEDBACK_SYSTEM_PROMPT_V1, FEEDBACK_OUTPUT_SCHEMA,
    )

边界（任务书「FeedbackEngine 输入输出」「反馈处理规则」）：

- `FeedbackEngine.analyze(request) -> FeedbackResult` 只提出 Candidate Delta 与
  保持范围，**不修改任何状态**；结果仍必须经 Validator / Reducer / DecisionPolicy
  与重新确认（README 不变量 2/5）；
- 反馈精确关联真实 `GenerationArtifact`（`FeedbackRequest.generation`；
  `FeedbackResult.generation_id` 与 `intent_revision_id` 皆由系统确定性填写）；
- 模糊反馈 → `clarify`（`feedback.clarification_required` issue 的 `path` 即待澄清路径），
  绝不猜具体值；
- LLM 解析失败 / Provider 失败 → 可恢复 issue（`feedback.*` / `provider.*`），不抛异常；
- 唯一 LLM 依赖是 Step 05 的 `providers.llm.LLMProvider`（默认离线测试用 `FakeLLMProvider`）。

`feedback/` 是**单 Step 拥有**的包，因此本文件按冻结表再导出公开面。
"""

from __future__ import annotations

from .engine import (
    CANONICAL_PATH_ORDER,
    FEEDBACK_OUTPUT_SCHEMA,
    FEEDBACK_PROMPT_VERSION,
    FEEDBACK_RESPONSE_FORMAT,
    FEEDBACK_SYSTEM_PROMPT_V1,
    MAX_FEEDBACK_ATTEMPTS,
    FeedbackEngine,
    build_feedback_user_prompt,
    default_compile_feedback,
    expand_preserve_paths,
    match_preserve_pattern,
    preserve_pin_deltas,
    read_intent_value,
)
from .models import (
    FEEDBACK_CLARIFICATION_REQUIRED,
    FEEDBACK_CLARIFICATION_TARGET_MISSING,
    FEEDBACK_CONTEXT_MISMATCH,
    FEEDBACK_EMPTY_OUTPUT,
    FEEDBACK_ERROR_CODES,
    FEEDBACK_FULL_INTENT,
    FEEDBACK_ID_PREFIX,
    FEEDBACK_INCONSISTENT_DECISION,
    FEEDBACK_INVALID_CLARIFY_PATH,
    FEEDBACK_INVALID_DELTA,
    FEEDBACK_INVALID_JSON,
    FEEDBACK_SCHEMA_VIOLATION,
    FEEDBACK_UNKNOWN_PRESERVE_PATH,
    FEEDBACK_UNPARSEABLE_OUTPUT,
    PARSE_FAILURE_CODES,
    FeedbackDecision,
    FeedbackError,
    FeedbackParseError,
    FeedbackRequest,
    FeedbackResult,
    clarify_target_path,
    is_unparseable,
)

__all__ = [
    "FeedbackEngine",
    "FeedbackDecision",
    "FeedbackRequest",
    "FeedbackResult",
    "FeedbackError",
    "FeedbackParseError",
    "clarify_target_path",
    "is_unparseable",
    "FEEDBACK_PROMPT_VERSION",
    "MAX_FEEDBACK_ATTEMPTS",
    "FEEDBACK_RESPONSE_FORMAT",
    "FEEDBACK_SYSTEM_PROMPT_V1",
    "FEEDBACK_OUTPUT_SCHEMA",
    "CANONICAL_PATH_ORDER",
    "build_feedback_user_prompt",
    "read_intent_value",
    "match_preserve_pattern",
    "expand_preserve_paths",
    "preserve_pin_deltas",
    "default_compile_feedback",
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
