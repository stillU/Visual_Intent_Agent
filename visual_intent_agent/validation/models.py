"""Step 02 公开结果模型：EvidenceContext、RejectedDelta、ValidationResult、
ChangeSummary、ReduceResult。

冻结形状（ARCHITECTURE.md 4「Step 02 — validation」）：

- `EvidenceContext` 是 Validator 的证据边界：只有 `available_message_ids` 中的消息
  可以作为证据，`pending_question_id` / `pending_question_path` 是本轮唯一的待答问题；
- `RejectedDelta` 把被拒 Delta 与其可观察 issue 绑在一起——非法 Delta 必须显式
  出现在 `ValidationResult.rejected` 中，不得静默忽略；
- `ChangeSummary` 是 Reducer 的最小修改影响报告（五字段，不多不少）；
- `ReduceResult` 把新 Intent 与本轮 ChangeSummary 一起返回（任务书要求 Reducer
  同时输出 change summary）。

全部模型 `frozen=True, extra="forbid"`。`frozen` 只阻止属性赋值；`list` 字段内容
仍可被原地修改，因此本模块的默认空列表一律用 `Field(default_factory=list)`，
避免多个实例共享同一个可变对象（`domain` 层的冻结语义同此，见 step_01_handoff
「已知限制 2」）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from visual_intent_agent.domain import IntentDelta, Issue, VisualIntent

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class EvidenceContext(BaseModel):
    """本轮验证可用的证据范围（由 Step 05 IntentEngine 从请求构造）。

    - `available_message_ids`：会话中已存消息 ID 的集合；Delta 的证据必须命中；
    - `pending_question_id`：当前待答问题 ID；无待答问题为 None；
    - `pending_question_path`：当前待答问题指向的 Intent 路径；无待答问题为 None。
    """

    model_config = _FROZEN

    available_message_ids: frozenset[str]
    pending_question_id: str | None = None
    pending_question_path: str | None = None


class RejectedDelta(BaseModel):
    """一个被显式拒绝的候选 Delta 及其全部可观察 issue。"""

    model_config = _FROZEN

    delta: IntentDelta
    issues: list[Issue] = Field(default_factory=list)


class ValidationResult(BaseModel):
    """验证结果：显式区分 accepted / rejected / issues。

    `issues` 是全部 issue 的扁平汇总（含 `rejected[i].issues`），方便调用方统一
    读取；被接受的 Delta 不产生 issue。
    """

    model_config = _FROZEN

    accepted: list[IntentDelta] = Field(default_factory=list)
    rejected: list[RejectedDelta] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class ChangeSummary(BaseModel):
    """Reducer 的最小修改影响报告（任务书五字段，不多不少）。

    - `changed_paths`：被接受 `SET` 的路径（按应用顺序去重）；
    - `cleared_paths`：被接受 `CLEAR` 的路径（按应用顺序去重）；
    - `pinned_paths` / `unpinned_paths`：真正改变保持状态的操作路径；
    - `confirmation_invalidated`：重要视觉字段 SET/CLEAR，或 PIN/UNPIN（保守规则）。
    """

    model_config = _FROZEN

    changed_paths: list[str] = Field(default_factory=list)
    pinned_paths: list[str] = Field(default_factory=list)
    unpinned_paths: list[str] = Field(default_factory=list)
    cleared_paths: list[str] = Field(default_factory=list)
    confirmation_invalidated: bool = False


class ReduceResult(BaseModel):
    """Reducer 输出：新 Intent（新对象）+ 本轮 ChangeSummary。

    Reducer **不**创建 IntentRevision（持久化与 revision 组装属 Step 06）。
    """

    model_config = _FROZEN

    intent: VisualIntent
    change_summary: ChangeSummary = Field(default_factory=ChangeSummary)


__all__ = [
    "EvidenceContext",
    "RejectedDelta",
    "ValidationResult",
    "ChangeSummary",
    "ReduceResult",
]
