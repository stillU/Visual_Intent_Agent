"""Step 06 确认摘要与摘要哈希（ARCHITECTURE.md 4「Step 06」；任务书「Confirmation 摘要」）。

确认采用 **diff-first** 顺序，`ConfirmationSummary` 的六个字段与任务书六要素一一对应：

1. 本轮修改内容 → `change_summary`（`ChangeSummary.changed_paths` / `cleared_paths`）
2. 明确保留内容 → `pinned_paths`
3. 用户授权系统决定的内容 → `delegated_paths`
4. 目标模型 → `target_model`
5. 输出比例 → `output_size`
6. 可展开的完整 Intent → `intent`

`compute_summary_hash` 是冻结算法（ARCHITECTURE.md 4）：

    sha256(json.dumps(summary.model_dump(mode="json"), sort_keys=True,
                      ensure_ascii=False).encode("utf-8")).hexdigest()

`WorkflowError` 承载三个 `workflow.*` 硬失败 code（本包内唯一错误类型）：

- `workflow.invalid_state`：当前状态不允许该用例（如确认仅限 `WAITING_CONFIRMATION`）；
- `workflow.stale_revision`：请求绑定的 intent/execution revision 不是当前快照；
- `workflow.summary_hash_mismatch`：请求 hash 与按当前快照重算的 hash 不一致。

摘要必须是**持久化状态的纯函数**：`build_confirmation_summary` 只读取当前两类
revision（`change_summary` 由当前 `IntentRevision.applied_deltas` 确定性派生），
因此同一快照在任何时刻重算都得到同一 hash，迟到的确认无法套用到新 revision。
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from visual_intent_agent.domain import (
    DeltaOperation,
    IntentDelta,
    IntentRevision,
    ExecutionRevision,
    Resolution,
    VisualIntent,
)
from visual_intent_agent.validation import ChangeSummary

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: `WorkflowError.code` 的冻结取值（ARCHITECTURE.md 4「Step 06 — workflow」）。
WORKFLOW_INVALID_STATE = "workflow.invalid_state"
WORKFLOW_STALE_REVISION = "workflow.stale_revision"
WORKFLOW_SUMMARY_HASH_MISMATCH = "workflow.summary_hash_mismatch"

WORKFLOW_ERROR_CODES: frozenset[str] = frozenset(
    {
        WORKFLOW_INVALID_STATE,
        WORKFLOW_STALE_REVISION,
        WORKFLOW_SUMMARY_HASH_MISMATCH,
    }
)


class WorkflowError(Exception):
    """工作流硬失败（硬门禁 / 程序级失败），带 `workflow.*` 命名空间的 `.code`。"""

    def __init__(self, code: str, message: str) -> None:
        if code not in WORKFLOW_ERROR_CODES:
            raise ValueError(f"unknown workflow error code: {code!r}")
        self.code = code
        super().__init__(f"[{code}] {message}")


class ConfirmationSummary(BaseModel):
    """确认页展示的摘要（diff-first 六要素；frozen）。

    摘要本身不落库：它是**当前两类 revision + 本轮 delta**的确定性函数，确认用例
    按同一函数重算并比对请求中的 `summary_hash`。因此"客户端展示的摘要"与"服务端
    重算的摘要"必然一致，任何 revision 变化都会改变 hash。
    """

    model_config = _FROZEN

    intent: VisualIntent
    change_summary: ChangeSummary | None = None
    delegated_paths: list[str] = Field(default_factory=list)
    pinned_paths: list[str] = Field(default_factory=list)
    target_model: str
    output_size: str


def compute_summary_hash(summary: ConfirmationSummary) -> str:
    """冻结的摘要哈希算法（逐字节稳定：keys 排序、非 ASCII 原样）。"""
    payload = summary.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unique_in_order(values: list[str]) -> list[str]:
    """按出现顺序去重（确定性；Reducer 的 ChangeSummary 语义同此）。"""
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def derive_change_summary(deltas: list[IntentDelta]) -> ChangeSummary:
    """由**已接受的** applied deltas 确定性派生本轮 diff（diff-first 第 1 要素）。

    `ChangeSummary` 没有进入 `IntentResolution`（Step 02/05 冻结面），而摘要必须
    可由持久化的 `IntentRevision.applied_deltas` 重算，因此这里按 Reducer 的同类
    语义派生：SET→changed、CLEAR→cleared、PIN→pinned、UNPIN→unpinned；
    任一批量变化都保守地置 `confirmation_invalidated=True`。
    """
    changed: list[str] = []
    cleared: list[str] = []
    pinned: list[str] = []
    unpinned: list[str] = []
    for delta in deltas:
        if delta.operation is DeltaOperation.SET:
            changed.append(delta.path)
        elif delta.operation is DeltaOperation.CLEAR:
            cleared.append(delta.path)
        elif delta.operation is DeltaOperation.PIN:
            pinned.append(delta.path)
        elif delta.operation is DeltaOperation.UNPIN:
            unpinned.append(delta.path)
    return ChangeSummary(
        changed_paths=_unique_in_order(changed),
        pinned_paths=_unique_in_order(pinned),
        unpinned_paths=_unique_in_order(unpinned),
        cleared_paths=_unique_in_order(cleared),
        confirmation_invalidated=bool(deltas),
    )


def build_confirmation_summary(
    intent_revision: IntentRevision,
    execution_revision: ExecutionRevision,
    change_summary: ChangeSummary | None = None,
) -> ConfirmationSummary:
    """由当前两类 revision 组装摘要（确定性；默认派生本轮 diff）。

    `delegated_paths` / `pinned_paths` 按字典序排序：`resolutions` 与 `pinned_paths`
    在内存中的迭代顺序不稳定（`frozenset` 随 PYTHONHASHSEED 变化），排序保证
    `compute_summary_hash` 跨进程逐字节一致。
    """
    intent = intent_revision.intent
    delegated_paths = sorted(
        path
        for path, record in intent.resolutions.items()
        if record.resolution is Resolution.USER_DELEGATED
    )
    pinned_paths = sorted(intent.pinned_paths)
    if change_summary is None:
        change_summary = derive_change_summary(list(intent_revision.applied_deltas))
    return ConfirmationSummary(
        intent=intent,
        change_summary=change_summary,
        delegated_paths=delegated_paths,
        pinned_paths=pinned_paths,
        target_model=execution_revision.target_model,
        output_size=execution_revision.output_size,
    )


__all__ = [
    "ConfirmationSummary",
    "WorkflowError",
    "compute_summary_hash",
    "build_confirmation_summary",
    "derive_change_summary",
    "WORKFLOW_ERROR_CODES",
    "WORKFLOW_INVALID_STATE",
    "WORKFLOW_STALE_REVISION",
    "WORKFLOW_SUMMARY_HASH_MISMATCH",
]
