"""L1 State Correctness（协议第 4 节 Layer 1；仅 System B；目标 100%）。

四条核心不变量逐轮检查（任一失败 → 该案 `l1_failed = true` → 摘要 Gate A 阻断）：

1. **未授权字段不改变**（`unauthorized_fields_unchanged`）：
   - 标注 `forbidden_change_paths` 中的路径，值与 Resolution 逐值不变；
   - 本轮发生值/Resolution 变化的路径必须 ⊆ 系统实际 `applied_deltas` 的路径集合
     （Reducer 保留既有 ResolutionRecord 是**保持**而非变化，不会被误判）。
2. **旧确认不复用**（`no_stale_confirmation`）：
   - 每次生成的 PromptArtifact 必须绑定**本轮新建**的确认，且确认绑定的
     intent/execution revision 等于生成时刻的当前 revision；
   - Runner 探针：过期 revision 确认必须被拒（`workflow.stale_revision`），
     篡改 hash 确认必须被拒（`workflow.summary_hash_mismatch`）；被接受即违规。
3. **pinned 不被覆盖**（`pinned_not_overwritten`）：
   - 轮次开始时 pinned 的路径，其值在本轮不被 SET/CLEAR 改变（CLEAR 必被拒）；
   - PIN/UNPIN 状态变化必须来自本轮被接受的 PIN/UNPIN Delta（用户显式操作）；
   - 对 pinned 路径的 SET 是冻结允许行为（Validator 只拒 CLEAR-pinned）：
     不改变本不变量结论，但逐例记入 `set_on_pinned_events` 供归因
     （Step 01 handoff 已知限制 5 的口径）。
4. **历史不被修改**（`history_append_only`）：
   - 八张 append-only 表（messages / intent_revisions / execution_revisions /
     confirmations / prompt_artifacts / generation_artifacts / feedback_results /
     realization_states）计数单调不减、既有行 payload 逐字节不变；
   - `sessions` 是当前指针表（设计上唯一允许 UPDATE），不在本不变量范围。

本模块全部是纯函数：输入为 `SystemBTurnObservation` + 逐表快照（`TableSnapshot`），
不触库、不触网；反例可直接构造数据喂入（任务书测试口径）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from evaluation.reporting import (
    L1_INVARIANT_CONFIRMATION,
    L1_INVARIANT_HISTORY,
    L1_INVARIANT_PINNED,
    L1_INVARIANT_UNAUTHORIZED,
    SystemBTurnObservation,
    TurnAnnotation,
)

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: 参与"历史不被修改"检查的 append-only 表（`sessions` 是唯一允许 UPDATE 的指针表）。
HISTORY_TABLES: tuple[str, ...] = (
    "messages",
    "intent_revisions",
    "execution_revisions",
    "confirmations",
    "prompt_artifacts",
    "generation_artifacts",
    "feedback_results",
    "realization_states",
)


class TableSnapshot(BaseModel):
    """一轮开始前/后的逐表快照（计数 + 逐行 payload 哈希）。"""

    model_config = _FROZEN

    counts: dict[str, int] = Field(default_factory=dict)
    #: table -> row_id -> 行内容的 sha256（Runner 用确定性列集规范化后哈希）。
    row_hashes: dict[str, dict[str, str]] = Field(default_factory=dict)


class L1TurnEvidence(BaseModel):
    """一轮的 L1 证据：标注 + 观察 + 逐表前后快照。"""

    model_config = _FROZEN

    turn_id: str
    turn_index: int = Field(ge=1)
    annotation: TurnAnnotation | None = None
    observation: SystemBTurnObservation | None = None
    tables_before: TableSnapshot | None = None
    tables_after: TableSnapshot | None = None


class L1Violation(BaseModel):
    """一条 L1 违例（可追溯到 turn / path / table / row）。"""

    model_config = _FROZEN

    invariant: str
    turn_id: str
    kind: str
    path: str | None = None
    table: str | None = None
    row_id: str | None = None
    detail: str


class L1InvariantOutcome(BaseModel):
    """单条不变量在一个案例上的结论。"""

    model_config = _FROZEN

    invariant: str
    passed: bool
    violations: list[L1Violation] = Field(default_factory=list)


class L1CaseEvaluation(BaseModel):
    """一个案例的 L1 总评（四不变量结论 + pinned-SET 归因事件）。"""

    model_config = _FROZEN

    case_id: str
    l1_failed: bool
    outcomes: list[L1InvariantOutcome] = Field(default_factory=list)
    #: pinned 路径被 SET 的逐例记录（冻结允许行为；只归因、不改变结论）。
    set_on_pinned_events: list[dict[str, object]] = Field(default_factory=list)

    @property
    def violations(self) -> list[L1Violation]:
        return [v for outcome in self.outcomes for v in outcome.violations]


# ---------------------------------------------------------------------------
# 不变量 1：未授权字段不改变
# ---------------------------------------------------------------------------


def check_unauthorized_fields_unchanged(
    evidence: L1TurnEvidence,
) -> list[L1Violation]:
    """forbidden_change_paths 逐值不变 + 变化路径 ⊆ applied_deltas 路径。"""
    observation = evidence.observation
    if observation is None:
        return []
    violations: list[L1Violation] = []
    forbidden = (
        list(evidence.annotation.forbidden_change_paths)
        if evidence.annotation is not None
        else []
    )
    for path in forbidden:
        before = observation.intent_values_before.get(path)
        after = observation.intent_values_after.get(path)
        if before != after:
            violations.append(
                L1Violation(
                    invariant=L1_INVARIANT_UNAUTHORIZED,
                    turn_id=evidence.turn_id,
                    kind="forbidden_value_changed",
                    path=path,
                    detail=f"forbidden path value changed: {before!r} -> {after!r}",
                    )
            )
        res_before = observation.resolutions_before.get(path)
        res_after = observation.resolutions_after.get(path)
        if res_before != res_after:
            violations.append(
                L1Violation(
                    invariant=L1_INVARIANT_UNAUTHORIZED,
                    turn_id=evidence.turn_id,
                    kind="forbidden_resolution_changed",
                    path=path,
                    detail=(
                        f"forbidden path resolution changed: {res_before!r} -> {res_after!r}"
                    ),
                )
            )

    applied_paths = {delta.path for delta in observation.applied_deltas}
    changed_value_paths = {
        path
        for path in set(observation.intent_values_before) | set(observation.intent_values_after)
        if observation.intent_values_before.get(path) != observation.intent_values_after.get(path)
    }
    changed_resolution_paths = {
        path
        for path in set(observation.resolutions_before) | set(observation.resolutions_after)
        if observation.resolutions_before.get(path) != observation.resolutions_after.get(path)
    }
    for path in sorted(changed_value_paths | changed_resolution_paths):
        if path not in applied_paths:
            violations.append(
                L1Violation(
                    invariant=L1_INVARIANT_UNAUTHORIZED,
                    turn_id=evidence.turn_id,
                    kind="change_outside_applied_deltas",
                    path=path,
                    detail="state changed on a path with no accepted delta this turn",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# 不变量 2：旧确认不复用
# ---------------------------------------------------------------------------


def check_no_stale_confirmation(evidence: L1TurnEvidence) -> list[L1Violation]:
    """每次生成都绑定本轮新确认 + 当前 revision；过期确认探针必须被拒。"""
    observation = evidence.observation
    if observation is None:
        return []
    violations: list[L1Violation] = []
    confirmation = observation.confirmation
    for generation in observation.generations:
        if confirmation is None:
            violations.append(
                L1Violation(
                    invariant=L1_INVARIANT_CONFIRMATION,
                    turn_id=evidence.turn_id,
                    kind="generation_without_fresh_confirmation",
                    detail=(
                        f"generation {generation.generation_id!r} happened in a turn "
                        "where no new confirmation was created"
                    ),
                )
            )
            continue
        if generation.based_on_confirmation_id != confirmation.confirmation_id:
            violations.append(
                L1Violation(
                    invariant=L1_INVARIANT_CONFIRMATION,
                    turn_id=evidence.turn_id,
                    kind="generation_bound_to_earlier_confirmation",
                    detail=(
                        f"prompt binds confirmation {generation.based_on_confirmation_id!r} "
                        f"but this turn created {confirmation.confirmation_id!r}"
                    ),
                )
            )
        if (
            generation.current_intent_revision_id is not None
            and confirmation.intent_revision_id != generation.current_intent_revision_id
        ):
            violations.append(
                L1Violation(
                    invariant=L1_INVARIANT_CONFIRMATION,
                    turn_id=evidence.turn_id,
                    kind="confirmation_binds_stale_intent_revision",
                    detail=(
                        f"confirmation binds {confirmation.intent_revision_id!r} but the "
                        f"current intent revision at generation was "
                        f"{generation.current_intent_revision_id!r}"
                    ),
                )
            )
        if (
            generation.current_execution_revision_id is not None
            and confirmation.execution_revision_id
            != generation.current_execution_revision_id
        ):
            violations.append(
                L1Violation(
                    invariant=L1_INVARIANT_CONFIRMATION,
                    turn_id=evidence.turn_id,
                    kind="confirmation_binds_stale_execution_revision",
                    detail="confirmation binds a stale execution revision",
                )
            )
    if observation.stale_revision_probe == "accepted":
        violations.append(
            L1Violation(
                invariant=L1_INVARIANT_CONFIRMATION,
                turn_id=evidence.turn_id,
                kind="stale_revision_confirmation_accepted",
                detail="confirm_current_intent accepted a stale revision binding",
            )
        )
    if observation.bad_hash_probe == "accepted":
        violations.append(
            L1Violation(
                invariant=L1_INVARIANT_CONFIRMATION,
                turn_id=evidence.turn_id,
                kind="bad_hash_confirmation_accepted",
                detail="confirm_current_intent accepted a mismatched summary_hash",
            )
        )
    return violations


# ---------------------------------------------------------------------------
# 不变量 3：pinned 不被覆盖
# ---------------------------------------------------------------------------


def check_pinned_not_overwritten(
    evidence: L1TurnEvidence,
) -> tuple[list[L1Violation], list[dict[str, object]]]:
    """pinned 值不被 SET/CLEAR 改变；PIN/UNPIN 变化必须来自被接受的对应 Delta。

    返回 (violations, set_on_pinned_events)：后者是冻结允许行为的逐例归因记录
    （不变量本身按"值不变"严格测量）。
    """
    observation = evidence.observation
    if observation is None:
        return [], []
    violations: list[L1Violation] = []
    events: list[dict[str, object]] = []
    pinned_before = set(observation.pinned_before)
    pinned_after = set(observation.pinned_after)
    applied = observation.applied_deltas
    pin_ops = {d.path for d in applied if d.operation == "PIN"}
    unpin_ops = {d.path for d in applied if d.operation == "UNPIN"}

    for path in sorted(pinned_before):
        before = observation.intent_values_before.get(path)
        after = observation.intent_values_after.get(path)
        if before != after:
            violations.append(
                L1Violation(
                    invariant=L1_INVARIANT_PINNED,
                    turn_id=evidence.turn_id,
                    kind="pinned_value_overwritten",
                    path=path,
                    detail=f"pinned path value changed: {before!r} -> {after!r}",
                )
            )
    for path in sorted(pinned_after - pinned_before):
        if path not in pin_ops:
            violations.append(
                L1Violation(
                    invariant=L1_INVARIANT_PINNED,
                    turn_id=evidence.turn_id,
                    kind="pin_added_without_user_pin",
                    path=path,
                    detail="path became pinned without an accepted PIN delta this turn",
                )
            )
    for path in sorted(pinned_before - pinned_after):
        if path not in unpin_ops:
            violations.append(
                L1Violation(
                    invariant=L1_INVARIANT_PINNED,
                    turn_id=evidence.turn_id,
                    kind="pin_removed_without_user_unpin",
                    path=path,
                    detail="path lost its pin without an accepted UNPIN delta this turn",
                )
            )
    for delta in applied:
        if delta.operation == "SET" and delta.path in pinned_before:
            events.append(
                {
                    "turn_id": evidence.turn_id,
                    "path": delta.path,
                    "value_changed": (
                        observation.intent_values_before.get(delta.path)
                        != observation.intent_values_after.get(delta.path)
                    ),
                }
            )
    return violations, events


# ---------------------------------------------------------------------------
# 不变量 4：历史不被修改
# ---------------------------------------------------------------------------


def check_history_append_only(
    turn_id: str,
    before: TableSnapshot,
    after: TableSnapshot,
) -> list[L1Violation]:
    """各 append-only 表：计数单调不减、既有行 payload 逐字节不变。"""
    violations: list[L1Violation] = []
    tables = set(before.counts) | set(after.counts)
    for table in sorted(tables):
        count_before = before.counts.get(table, 0)
        count_after = after.counts.get(table, 0)
        if count_after < count_before:
            violations.append(
                L1Violation(
                    invariant=L1_INVARIANT_HISTORY,
                    turn_id=turn_id,
                    kind="table_count_decreased",
                    table=table,
                    detail=f"row count decreased: {count_before} -> {count_after}",
                )
            )
        rows_before = before.row_hashes.get(table, {})
        rows_after = after.row_hashes.get(table, {})
        for row_id in sorted(rows_before):
            if row_id not in rows_after:
                violations.append(
                    L1Violation(
                        invariant=L1_INVARIANT_HISTORY,
                        turn_id=turn_id,
                        kind="history_row_removed",
                        table=table,
                        row_id=row_id,
                        detail="a previously present history row disappeared",
                    )
                )
            elif rows_after[row_id] != rows_before[row_id]:
                violations.append(
                    L1Violation(
                        invariant=L1_INVARIANT_HISTORY,
                        turn_id=turn_id,
                        kind="history_row_payload_modified",
                        table=table,
                        row_id=row_id,
                        detail="a previously present history row payload changed",
                    )
                )
    return violations


# ---------------------------------------------------------------------------
# 案例级汇总
# ---------------------------------------------------------------------------


def evaluate_case_l1(case_id: str, turns: list[L1TurnEvidence]) -> L1CaseEvaluation:
    """对一个案例的全部轮次执行四不变量检查并汇总。"""
    violations_by_invariant: dict[str, list[L1Violation]] = {
        invariant: [] for invariant in (
            L1_INVARIANT_UNAUTHORIZED,
            L1_INVARIANT_CONFIRMATION,
            L1_INVARIANT_PINNED,
            L1_INVARIANT_HISTORY,
        )
    }
    set_on_pinned_events: list[dict[str, object]] = []
    for evidence in turns:
        violations_by_invariant[L1_INVARIANT_UNAUTHORIZED].extend(
            check_unauthorized_fields_unchanged(evidence)
        )
        violations_by_invariant[L1_INVARIANT_CONFIRMATION].extend(
            check_no_stale_confirmation(evidence)
        )
        pinned_violations, events = check_pinned_not_overwritten(evidence)
        violations_by_invariant[L1_INVARIANT_PINNED].extend(pinned_violations)
        set_on_pinned_events.extend(events)
        if evidence.tables_before is not None and evidence.tables_after is not None:
            violations_by_invariant[L1_INVARIANT_HISTORY].extend(
                check_history_append_only(
                    evidence.turn_id, evidence.tables_before, evidence.tables_after
                )
            )
    outcomes = [
        L1InvariantOutcome(
            invariant=invariant,
            passed=not violations,
            violations=violations,
        )
        for invariant, violations in violations_by_invariant.items()
    ]
    return L1CaseEvaluation(
        case_id=case_id,
        l1_failed=any(not outcome.passed for outcome in outcomes),
        outcomes=outcomes,
        set_on_pinned_events=set_on_pinned_events,
    )


__all__ = [
    "HISTORY_TABLES",
    "TableSnapshot",
    "L1TurnEvidence",
    "L1Violation",
    "L1InvariantOutcome",
    "L1CaseEvaluation",
    "check_unauthorized_fields_unchanged",
    "check_no_stale_confirmation",
    "check_pinned_not_overwritten",
    "check_history_append_only",
    "evaluate_case_l1",
]
