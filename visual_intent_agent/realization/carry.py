"""Step 09 Realization carry/invalidation 评估（`realization/carry.py`）。

冻结形状（ARCHITECTURE.md 4「Step 09」）：

    CarryEvaluation  carried_values / invalidated_values（status 已置 invalidated + reason）
    evaluate_carry(state: RealizationState | None,
                   change_summary: ChangeSummary,
                   policies: tuple[DecisionPolicy, ...] = DECISION_POLICIES)
        -> CarryEvaluation

语义（任务书「继承与失效」+ 冻结表）：

- **默认继承 active**：`state` 中每个 `status == "active"` 的值原样进入
  `carried_values`（保持原顺序、原 `first_prompt_artifact_id`、原 `carry_policy`）；
- **失效四条件的落地**（全部由 `ChangeSummary` 判定，不读取新 Intent）：
  1. 用户 SET/CLEAR 同一路径 → `user_changed_delegated_path`；
  2. 该路径所属 Decision 的 dependency 路径发生变化 →
     `decision_dependency_changed`（例如 `environment.mode` 变化使
     `environment.location` 的实现失效；`DecisionPolicy.dependencies` 表达成员关系）；
  3. 新 Intent 与旧 Realization 冲突 → 冻结签名只接收 `ChangeSummary`，
     而"冲突"在确定性层面的可判定形态就是该路径（含纯 resolution）的 SET/CLEAR，
     因此由条件 1/4 覆盖；`evaluate_carry` 不读新 Intent、不做语义猜测；
  4. 用户 CLEAR 对应 delegated path → 同样由 `cleared_paths` 命中条件 1 记录；
- **PIN/UNPIN 影响**（ARCHITECTURE.md 冻结补充）：
  - PIN 只强化"保持"（用户显式要求不变）→ **不**使 Realization 失效，仍继承；
  - UNPIN 释放了该 Decision 的保持约束（`unpinned_paths` 命中该路径或其 Decision
    成员路径）→ `unpinned_delegated_path` 失效；
- **失效必须显式**：被失效的值是 `model_copy(update={status="invalidated",
  invalidated_reason, invalidated_at})` 的**新对象**；历史 value 不覆盖、不删除，
  新快照经 `build_carry_state` + `Repository.append_realization_state` 落库
  （refs 必填键 `{"based_on_intent_revision_id"}`）。

`realization/` 是多 Step 拥有的包，`__init__.py` **保持空白**；下游一律
`from visual_intent_agent.realization.carry import ...`。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from visual_intent_agent.domain import new_id, utc_now
from visual_intent_agent.policy import DECISION_POLICIES, DecisionPolicy
from visual_intent_agent.validation import ChangeSummary

from .models import (
    REALIZATION_STATUS_ACTIVE,
    REALIZATION_STATUS_INVALIDATED,
    RealizationState,
    RealizationValue,
)

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: RealizationState ID 前缀（ARCHITECTURE.md 5.1 冻结前缀表）。
REALIZATION_ID_PREFIX = "rlz"

#: 失效原因（稳定字符串；写入 `RealizationValue.invalidated_reason`）。
CARRY_REASON_USER_CHANGED_PATH = "user_changed_delegated_path"
CARRY_REASON_DEPENDENCY_CHANGED = "decision_dependency_changed"
CARRY_REASON_UNPINNED = "unpinned_delegated_path"

#: 全部失效原因（handoff 与测试引用）。
CARRY_INVALIDATION_REASONS: frozenset[str] = frozenset(
    {
        CARRY_REASON_USER_CHANGED_PATH,
        CARRY_REASON_DEPENDENCY_CHANGED,
        CARRY_REASON_UNPINNED,
    }
)


class CarryEvaluation(BaseModel):
    """一次 carry 评估的结果（frozen；恰好两个字段，与冻结表一致）。

    - `carried_values`：仍可复用的 active 值（原对象，未修改）；
    - `invalidated_values`：本轮被显式失效的值（新对象，`status="invalidated"` 且
      带 `invalidated_reason` / `invalidated_at`）。
    """

    model_config = _FROZEN

    carried_values: list[RealizationValue] = Field(default_factory=list)
    invalidated_values: list[RealizationValue] = Field(default_factory=list)

    def carried_paths(self) -> list[str]:
        """仍继承的路径（声明顺序）。"""
        return [value.path for value in self.carried_values]

    def invalidated_paths(self) -> list[str]:
        """本轮失效的路径（声明顺序）。"""
        return [value.path for value in self.invalidated_values]

    def has_invalidations(self) -> bool:
        """是否存在本轮显式失效（决定是否需要落一条新 RealizationState）。"""
        return bool(self.invalidated_values)


def owner_policy_for_path(
    path: str, policies: tuple[DecisionPolicy, ...] = DECISION_POLICIES
) -> DecisionPolicy | None:
    """返回把 `path` 作为成员（`policy.path` 或 `policy.dependencies`）的 Decision。

    纯只读查表，确定性；未命中返回 None（`evaluate_carry` 仍按同路径规则保守处理）。
    """
    for policy in policies:
        if path == policy.path or path in policy.dependencies:
            return policy
    return None


def _invalidation_reason(
    path: str,
    changed_paths: frozenset[str],
    unpinned_paths: frozenset[str],
    policies: tuple[DecisionPolicy, ...],
) -> str | None:
    """按冻结条件判定一条 active 值是否失效；不失效返回 None。"""
    policy = owner_policy_for_path(path, policies)
    if policy is not None and not policy.invalidates_realization:
        return None

    if path in changed_paths:
        return CARRY_REASON_USER_CHANGED_PATH

    members = {path} if policy is None else {policy.path, *policy.dependencies}
    if (members & changed_paths) - {path}:
        return CARRY_REASON_DEPENDENCY_CHANGED

    if path in unpinned_paths or (members & unpinned_paths):
        return CARRY_REASON_UNPINNED

    # PIN（或未命中任何条件）→ 显式保持：默认继承。
    return None


def evaluate_carry(
    state: RealizationState | None,
    change_summary: ChangeSummary,
    policies: tuple[DecisionPolicy, ...] = DECISION_POLICIES,
) -> CarryEvaluation:
    """评估一次修改后的 Realization 继承与失效（纯函数，不写状态）。

    - `state is None` → 空评估（会话从未产生委托实现）；
    - 只处理 `status == "active"` 的值；已失效值不再进入新快照（历史仍保留在旧 state）；
    - 失效时间在同一调用内统一取一次（同一批次的值拥有相同 `invalidated_at`）。
    """
    if state is None:
        return CarryEvaluation()

    changed_paths = frozenset(change_summary.changed_paths) | frozenset(
        change_summary.cleared_paths
    )
    unpinned_paths = frozenset(change_summary.unpinned_paths)
    invalidated_at = utc_now()

    carried: list[RealizationValue] = []
    invalidated: list[RealizationValue] = []
    for value in state.values:
        if value.status != REALIZATION_STATUS_ACTIVE:
            continue
        reason = _invalidation_reason(value.path, changed_paths, unpinned_paths, policies)
        if reason is None:
            carried.append(value)
            continue
        invalidated.append(
            value.model_copy(
                update={
                    "status": REALIZATION_STATUS_INVALIDATED,
                    "invalidated_reason": reason,
                    "invalidated_at": invalidated_at,
                }
            )
        )
    return CarryEvaluation(carried_values=carried, invalidated_values=invalidated)


def build_carry_state(
    evaluation: CarryEvaluation,
    state: RealizationState,
    *,
    session_id: str,
    based_on_intent_revision_id: str,
    realization_id: str | None = None,
    created_at: datetime | None = None,
) -> RealizationState:
    """由评估结果构造**新** `RealizationState`（历史不覆盖；`rlz` 前缀）。

    - 值的顺序沿用原 `state.values` 的顺序（确定性；同一路径只保留一次）；
    - 之前已失效的值不再进入新快照（旧 state 仍可审计），未失效的 active 值全部带上；
    - `based_on_intent_revision_id` 必须是**新** revision（调用方先落 IntentRevision，
      再由 `Repository.append_realization_state` 落库，外键才能成立）。
    """
    updated: dict[str, RealizationValue] = {}
    for value in (*evaluation.carried_values, *evaluation.invalidated_values):
        updated[value.path] = value

    values: list[RealizationValue] = []
    seen: set[str] = set()
    for value in state.values:
        if value.path in seen or value.path not in updated:
            continue
        seen.add(value.path)
        values.append(updated[value.path])

    return RealizationState(
        realization_id=realization_id or new_id(REALIZATION_ID_PREFIX),
        session_id=session_id,
        based_on_intent_revision_id=based_on_intent_revision_id,
        values=values,
        created_at=created_at or utc_now(),
    )


__all__ = [
    "CarryEvaluation",
    "evaluate_carry",
    "build_carry_state",
    "owner_policy_for_path",
    "REALIZATION_ID_PREFIX",
    "CARRY_REASON_USER_CHANGED_PATH",
    "CARRY_REASON_DEPENDENCY_CHANGED",
    "CARRY_REASON_UNPINNED",
    "CARRY_INVALIDATION_REASONS",
]
