"""Step 06 澄清问题：`PendingQuestion` 与 `QuestionBuilder`（ARCHITECTURE.md 4「Step 06」）。

    用户消息 → IntentEngine → IntentResolution.question: QuestionSpec
             → QuestionBuilder.build(spec, session_id) → PendingQuestion（可持久化）

边界（任务书「澄清策略」）：

- 默认一轮只问**一个**最高优先级问题；优先级完全来自 Step 03 `DecisionPolicy`
  （`IntentResolution.question` 每轮至多一个），本模块**不**参与优先级判定；
- 默认使用确定性模板生成问题文本；只有注入 `LLMProvider` 时才用 LLM 改写措辞，
  且改写结果只作为 `question_text` 使用；
- `target_path` / `allow_delegate` / `allow_custom` / `suggested_values` **原样**
  来自 `QuestionSpec`，LLM 无法改变问题指向、委托许可或选项边界；
- **系统推荐 ≠ 用户已经选择**：`suggested_values` 只是候选选项，绝不写入 Intent；
  Intent 的唯一变化来源是经 Validator 接受的 Candidate Delta（README 不变量 2/3）。

问题文本尽量包含（任务书要求）：

1. 2～3 个具体选项（来自 `QuestionSpec.suggested_values`，最多取 3 个）；
2. 自定义输入入口（`allow_custom=True` 时）；
3. 该路径允许时提供"交给系统决定"（`allow_delegate=True` 时）。

`PendingQuestion.to_spec()` 返回带非空 `question_id` 的 `QuestionSpec`：这是把问题
交回 Step 05 `IntentEngine` 做"回答授权范围"校验的前置条件
（`interpreter.pending_question_missing_id`）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer

from visual_intent_agent.domain import new_id, utc_now
from visual_intent_agent.policy import QuestionSpec
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.llm import LLMMessage, LLMProvider, LLMRequest

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: 问题文本中最多展示的预设选项数（任务书：2～3 个具体选项）。
MAX_SUGGESTED_OPTIONS = 3

#: 问题 ID 前缀（ARCHITECTURE.md 5.1 冻结前缀表）。
QUESTION_ID_PREFIX = "qst"


def _ensure_utc(value: datetime) -> datetime:
    """拒绝 naive datetime；把 tz-aware 值归一到 UTC（与 domain/records 同约定）。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware UTC; naive datetimes are rejected")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    """JSON 序列化形态：ISO 8601 带 `+00:00`（ARCHITECTURE.md 5.2）。"""
    return value.astimezone(timezone.utc).isoformat()


_UtcDatetime = Annotated[
    datetime,
    AfterValidator(_ensure_utc),
    PlainSerializer(_iso_utc, return_type=str, when_used="json"),
]


class PendingQuestion(BaseModel):
    """一个已渲染、已持久化的待答问题（会话当前至多一个）。

    字段面与 ARCHITECTURE.md 4 逐字一致。`to_spec()` 把问题还原为 `QuestionSpec`
    （带 `question_text` 与 `question_id`），供下一轮 `IntentResolveRequest` 使用。
    """

    model_config = _FROZEN

    question_id: str
    session_id: str
    target_path: str
    reason: str
    allow_delegate: bool
    allow_custom: bool
    suggested_values: tuple[str, ...] = ()
    question_text: str
    created_at: _UtcDatetime = Field(default_factory=utc_now)

    def to_spec(self) -> QuestionSpec:
        """还原为 Step 03 的 `QuestionSpec`（`question_id` 必须非空）。"""
        return QuestionSpec(
            target_path=self.target_path,
            reason=self.reason,
            allow_delegate=self.allow_delegate,
            allow_custom=self.allow_custom,
            suggested_values=self.suggested_values,
            question_text=self.question_text,
            question_id=self.question_id,
        )


def render_question_text(spec: QuestionSpec) -> str:
    """确定性模板：路径 + 最多 3 个选项 + 自定义入口 +（可选）交给系统决定。"""
    parts = [f"Please clarify {spec.target_path!r}."]
    options = tuple(spec.suggested_values[:MAX_SUGGESTED_OPTIONS])
    if options:
        parts.append("Suggested options: " + ", ".join(options) + ".")
    if spec.allow_custom:
        parts.append("You can also describe your own preference in your own words.")
    if spec.allow_delegate:
        parts.append('Or say "you decide" to let the system choose.')
    return " ".join(parts)


#: LLM 仅被允许改写措辞：不得增删选项、不得改变目标字段、不得替用户作答。
_QUESTION_REWRITE_SYSTEM_PROMPT = (
    "You polish exactly one clarification question for a text-to-image assistant. "
    "Keep the same target field and the same options; never add, remove or reorder "
    "options; never answer the question yourself; never introduce new visual facts. "
    "Reply with the rewritten question text only."
)


class QuestionBuilder:
    """`QuestionSpec` → `PendingQuestion` 的唯一构造器。

    无 `LLMProvider` 时使用确定性模板（默认）。注入 `LLMProvider` 时只用于改写
    措辞；LLM 调用失败或返回空白时回退到确定性模板（问题仍然可问，状态不受影响）。
    """

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self._llm = llm

    def build(self, spec: QuestionSpec, session_id: str) -> PendingQuestion:
        """构造并渲染一个待答问题；结构化字段逐值取自 `spec`。"""
        text = self._rewrite(spec)
        if text is None:
            text = render_question_text(spec)
        return PendingQuestion(
            question_id=new_id(QUESTION_ID_PREFIX),
            session_id=session_id,
            target_path=spec.target_path,
            reason=spec.reason,
            allow_delegate=spec.allow_delegate,
            allow_custom=spec.allow_custom,
            suggested_values=tuple(spec.suggested_values),
            question_text=text,
        )

    # -- 内部实现 ----------------------------------------------------------

    def _rewrite(self, spec: QuestionSpec) -> str | None:
        """用 LLM 改写措辞；不可用时返回 None（调用方回退到模板）。"""
        if self._llm is None:
            return None
        draft = render_question_text(spec)
        request = LLMRequest(
            messages=[
                LLMMessage(role="system", content=_QUESTION_REWRITE_SYSTEM_PROMPT),
                LLMMessage(role="user", content=self._rewrite_user_prompt(spec, draft)),
            ]
        )
        try:
            response = self._llm.complete(request)
        except ProviderError:
            return None
        text = response.content.strip()
        return text or None

    @staticmethod
    def _rewrite_user_prompt(spec: QuestionSpec, draft: str) -> str:
        options = tuple(spec.suggested_values[:MAX_SUGGESTED_OPTIONS])
        return (
            f"target field: {spec.target_path}\n"
            f"options: {list(options)}\n"
            f"allow custom answer: {spec.allow_custom}\n"
            f"allow delegate to the system: {spec.allow_delegate}\n"
            f"draft question: {draft}\n"
        )


__all__ = [
    "PendingQuestion",
    "QuestionBuilder",
    "render_question_text",
    "MAX_SUGGESTED_OPTIONS",
    "QUESTION_ID_PREFIX",
]
