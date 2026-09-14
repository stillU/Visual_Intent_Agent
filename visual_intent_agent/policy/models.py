"""Step 03 公开数据模型：Materiality、PolicyAction、DecisionPolicy、
UnresolvedDecision、QuestionSpec、IntentResolution。

冻结形状（ARCHITECTURE.md 4「Step 03 — policy」）：

- `DecisionPolicy` 的七个字段是规则表的唯一载体：一个 Decision 的路径、
  重要性、显式 `required_if` 条件名、是否可委托、依赖路径、冲突规则 id、
  以及是否使既有 Realization 失效；
- `PolicyAction` 只有 `block` / `omit` / `runtime` 三种策略结果；
- `QuestionSpec` 只表达"问题需求"（target_path / reason / 允许的选项类型 /
  建议选项）；`question_text` 与 `question_id` 由 Step 06 的 QuestionBuilder 填充，
  Step 03 产生时一律为 `None`；
- `IntentResolution` 是本步骤的统一输出：`applied_deltas` 恒为空列表，由
  Step 05 IntentEngine 用 `model_copy(update={"applied_deltas": ...})` 回填。

全部模型 `frozen=True, extra="forbid"`（ARCHITECTURE.md 5.7）。`frozen` 只阻止
属性赋值，`list` 字段内容仍可原地修改，因此默认空列表一律使用
`Field(default_factory=list)`，避免实例间共享可变对象（与 Step 01/02 同约定）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from visual_intent_agent.domain import IntentDelta, Issue, VisualIntent

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class Materiality(str, Enum):
    """Decision 的重要性分级（任务书「决策分类」）。

    - `core`：直接定义用户目标；适用时缺失阻塞确认；
    - `perceptual`：明显影响视觉结果；仅当显式 `required_if` 成立时阻塞；
    - `implementation`：Prompt token 顺序、sampling 参数等实现细节；
      标记为 `runtime`，从不需要用户确认。
    """

    CORE = "core"
    PERCEPTUAL = "perceptual"
    IMPLEMENTATION = "implementation"


class PolicyAction(str, Enum):
    """缺失决策的策略结果（任务书「策略结果」）。

    - `block`：必须解决后才能确认；
    - `omit`：允许 unspecified，且 PromptEngine 也不得自行补成具体重大要求；
    - `runtime`：仅限实现参数（由运行时决定，不需要用户确认）。
    """

    BLOCK = "block"
    OMIT = "omit"
    RUNTIME = "runtime"


class DecisionPolicy(BaseModel):
    """一个 Decision 的确定性规则条目（规则表元素，静态数据）。

    字段面冻结为七个（ARCHITECTURE.md 4）：

    - `path`：该 Decision 的规范路径（必须 ∈ `domain.INTENT_PATHS`）；
    - `materiality`：`core` / `perceptual` / `implementation`；
    - `required_if`：**封闭枚举字符串集**中的条件名（见
      `decision_policy.REQUIRED_IF_CONDITIONS`）；不得是自由表达式；
    - `delegatable`：该路径是否允许 `user_delegated` 作为已解决；
    - `dependencies`：属于同一 Decision 的其它路径（Decision 跨多路径时使用，
      例如 Environment = `environment.mode` + `environment.location`）；
      它们与本 Decision 共享 `required_if` / `materiality` / `delegatable`；
    - `conflict_rules`：本 Decision 参与的冲突规则 id（见
      `decision_policy.CONFLICT_RULES`）；冲突只显式输出，绝不覆盖字段解决；
    - `invalidates_realization`：该 Decision 的重要变更是否使既有 Realization
      失效（供 Step 09 `evaluate_carry` 使用）。
    """

    model_config = _FROZEN

    path: str
    materiality: Materiality
    required_if: str
    delegatable: bool
    dependencies: list[str] = Field(default_factory=list)
    conflict_rules: list[str] = Field(default_factory=list)
    invalidates_realization: bool


class UnresolvedDecision(BaseModel):
    """一个尚未解决的 Decision（含其策略结果与确定性原因）。

    `path` 是该 Decision 当前应被询问的**具体缺失路径**：当 Decision 跨多条路径
    时（如 Environment），它是按声明顺序第一个未解决的成员路径。
    """

    model_config = _FROZEN

    path: str
    materiality: Materiality
    action: PolicyAction
    reason: str


class QuestionSpec(BaseModel):
    """问题需求（不是澄清文案）。

    Step 03 只填 `target_path` / `reason` / `allow_delegate` / `allow_custom` /
    `suggested_values`；`question_text` 与 `question_id` 由 Step 06 的
    QuestionBuilder 填充。`allow_delegate` 仅当该 path 可委托时为 True。
    """

    model_config = _FROZEN

    target_path: str
    reason: str
    allow_delegate: bool
    allow_custom: bool = True
    suggested_values: tuple[str, ...] = ()
    question_text: str | None = None
    question_id: str | None = None


class IntentResolution(BaseModel):
    """Step 03 的统一输出（任务书 IntentResolution）。

    - `intent`：被评估的 Intent 原样回传（assess 绝不修改输入）；
    - `applied_deltas`：恒为空列表；由 Step 05 IntentEngine 回填本轮被接受的
      Delta（`model_copy(update={"applied_deltas": accepted})`）；
    - `issues`：Resolution 合法性检查产生的 `policy.*` issue；
    - `unresolved_decisions`：全部未解决的 Decision（含 `omit`）；只有
      `action=block` 的条目阻塞确认；
    - `conflicts`：显式冲突（以 `Issue` 表达，code 用 `policy.hard_conflict.*`
      或 `policy.execution_conflict.*`）；
    - `question`：最高优先级的**唯一**待询问目标；ready 时为 `None`；
    - `ready_for_confirmation`：所有 `block` 项已解决且无 Hard Conflict 时为 True。
    """

    model_config = _FROZEN

    intent: VisualIntent
    applied_deltas: list[IntentDelta] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    unresolved_decisions: list[UnresolvedDecision] = Field(default_factory=list)
    conflicts: list[Issue] = Field(default_factory=list)
    question: QuestionSpec | None = None
    ready_for_confirmation: bool = False


__all__ = [
    "Materiality",
    "PolicyAction",
    "DecisionPolicy",
    "UnresolvedDecision",
    "QuestionSpec",
    "IntentResolution",
]
