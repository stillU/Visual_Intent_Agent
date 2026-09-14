"""Candidate IntentDelta 的确定性验证器（Step 02 的核心边界）。

    validate(candidate_deltas, evidence_context, current_intent) -> ValidationResult

完成内容：任务书「Validator 责任」的 7 项检查，全部纯代码、确定性、无 LLM /
数据库 / 网络依赖。非法 Delta **整个**进入 `rejected`，并携带至少一条
`severity=error` 的 issue；Validator 绝不改写路径、绝不静默忽略、绝不隐式填充。

检查顺序（任务书原文顺序）：

1. `operation` 合法（SET / CLEAR / PIN / UNPIN）；
2. `path` 是否属于 Schema v1 白名单（`domain.paths.INTENT_PATHS`，只 import）；
3. `value` 与目标路径类型兼容（`subject.count` → `int` 且 > 0；其余 11 条 → `str`）；
4. 证据存在性（非空 `evidence_refs`；`message_id ∈ available_message_ids`；
   回答 Pending Question 时 `pending_question_id` 必须等于当前值且 `path` 必须
   等于 `pending_question_path`）；
5. 授权范围是否越界（resolution 自报的授权强度必须被证据支持）；
6. 系统字段保护（`domain.paths.SYSTEM_FIELD_NAMES`）；
7. PIN / UNPIN 目标合法性（PIN 要求当前有值可保持；UNPIN 要求当前确实 pinned）。

**实现选择（已在 step_02_handoff.md 写明）**：一次 `validate` 调用对每个 Delta
运行全部适用检查（而非命中第一条即停止），因此一个 Delta 可以带多条 issue。
不变量是「拒绝 ⇒ rejected 非空且该 Delta 至少一条 issue」，测试断言用
「issue code ∈ 期望集合」，不依赖 issue 条数。

不检查（按任务书禁止范围）：

- 不判断哪些视觉字段必须补齐、不判断 ready_for_confirmation（Step 03）；
- 不生成澄清问题、不解释自然语言（Step 05/06）；
- 不持久化、不创建 revision（Step 04/06）。
"""

from __future__ import annotations

from visual_intent_agent.domain import (
    INTENT_PATHS,
    SYSTEM_FIELD_NAMES,
    DeltaOperation,
    EvidenceRef,
    IntentDelta,
    Issue,
    Resolution,
    ResolutionRecord,
    Severity,
    VisualIntent,
)

from .models import EvidenceContext, RejectedDelta, ValidationResult

#: 合法 operation（与 domain.DeltaOperation 的成员一一对应，只 import 不另维护清单）。
DELTA_OPERATIONS: frozenset[DeltaOperation] = frozenset(DeltaOperation)

#: operation 非法。
OPERATION_INVALID = "validation.operation_invalid"
#: path 不在 Schema v1 白名单。
PATH_NOT_WHITELISTED = "validation.path_not_whitelisted"
#: path 命中系统字段名（revision ID、创建时间、workflow state 等）。
SYSTEM_FIELD_PROTECTED = "validation.system_field_protected"
#: value 与路径类型不兼容。
VALUE_TYPE_MISMATCH = "validation.value_type_mismatch"
#: 未携带任何 evidence_refs。
EVIDENCE_MISSING = "validation.evidence_missing"
#: 证据引用的消息不在 available_message_ids 中。
EVIDENCE_MESSAGE_NOT_AVAILABLE = "validation.evidence_message_not_available"
#: 证据引用了 Pending Question，但当前没有待答问题。
EVIDENCE_PENDING_QUESTION_UNKNOWN = "validation.evidence_pending_question_unknown"
#: 证据的 pending_question_id 不是当前待答问题。
EVIDENCE_PENDING_QUESTION_MISMATCH = "validation.evidence_pending_question_mismatch"
#: 回答 Pending Question 的 Delta 指向了其他路径（授权不扩大）。
EVIDENCE_PENDING_QUESTION_PATH_MISMATCH = "validation.evidence_pending_question_path_mismatch"
#: resolution 自报的授权强度缺少证据支持（越界）。
UNAUTHORIZED_RESOLUTION_CLAIM = "validation.unauthorized_resolution_claim"
#: 期望有值（PIN）的路径当前没有任何值。
PIN_REQUIRES_EXISTING_VALUE = "validation.pin_requires_existing_value"
#: UNPIN 的路径当前未被 pinned。
UNPIN_REQUIRES_PINNED_PATH = "validation.unpin_requires_pinned_path"
#: CLEAR 的路径当前被 pinned（保持约束与删除要求冲突）。
CLEAR_REQUIRES_UNPINNED_PATH = "validation.clear_requires_unpinned_path"
#: Delta 应用到当前状态不产生任何可观察效果。
NO_STATE_EFFECT = "validation.no_state_effect"

#: 具有非 str 类型的路径：`subject.count` 是 int（domain.SubjectFacet.count: int|None, gt=0）。
_INT_VALUE_PATHS: frozenset[str] = frozenset({"subject.count"})


def _issue(code: str, message: str, path: str | None) -> Issue:
    """构造本命名空间（validation.*）下的 error issue。"""
    return Issue(code=code, message=message, path=path)


def _is_int(value: object) -> bool:
    """严格 int：bool 不算（`True` 不是数量），float 不算。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _operation_issues(delta: IntentDelta) -> list[Issue]:
    """检查 1：operation 合法。"""
    if delta.operation not in DELTA_OPERATIONS:
        return [
            _issue(
                OPERATION_INVALID,
                f"unsupported operation {delta.operation!r}; "
                f"expected one of {sorted(op.value for op in DELTA_OPERATIONS)}",
                delta.path,
            )
        ]
    return []


def _path_issues(delta: IntentDelta) -> list[Issue]:
    """检查 2：path 属于 Schema v1 白名单（唯一定义在 domain.paths）。"""
    if delta.path not in INTENT_PATHS:
        return [
            _issue(
                PATH_NOT_WHITELISTED,
                f"path {delta.path!r} is not in the Schema v1 whitelist",
                delta.path,
            )
        ]
    return []


def _value_issues(delta: IntentDelta) -> list[Issue]:
    """检查 3：value 与目标路径类型兼容。

    仅对携带 value 的 Delta 生效；resolution-only SET 没有 value 可校验
    （其 shape 由 domain.IntentDelta 的 model_validator 保证）。
    """
    if delta.value is None:
        return []
    issues: list[Issue] = []
    if delta.path in _INT_VALUE_PATHS:
        if not _is_int(delta.value) or delta.value <= 0:
            issues.append(
                _issue(
                    VALUE_TYPE_MISMATCH,
                    f"path {delta.path!r} requires a positive int; got {delta.value!r}",
                    delta.path,
                )
            )
    elif not isinstance(delta.value, str):
        issues.append(
            _issue(
                VALUE_TYPE_MISMATCH,
                f"path {delta.path!r} requires str; got {type(delta.value).__name__}",
                delta.path,
            )
        )
    return issues


def _evidence_issues(delta: IntentDelta, context: EvidenceContext) -> list[Issue]:
    """检查 4：证据存在性 + 消息可用性 + Pending Question 绑定。"""
    issues: list[Issue] = []
    if not delta.evidence_refs:
        issues.append(
            _issue(
                EVIDENCE_MISSING,
                "delta carries no evidence_refs; every change needs a user message "
                "or a current pending-question answer",
                delta.path,
            )
        )
        return issues

    unknown_message_ids = sorted(
        {
            ref.message_id
            for ref in delta.evidence_refs
            if ref.message_id not in context.available_message_ids
        }
    )
    if unknown_message_ids:
        issues.append(
            _issue(
                EVIDENCE_MESSAGE_NOT_AVAILABLE,
                f"evidence message_ids are not in available_message_ids: {unknown_message_ids}",
                delta.path,
            )
        )

    answered_ids = {
        ref.pending_question_id for ref in delta.evidence_refs if ref.pending_question_id
    }
    current_id = context.pending_question_id
    for answered_id in sorted(answered_ids):
        if current_id is None:
            issues.append(
                _issue(
                    EVIDENCE_PENDING_QUESTION_UNKNOWN,
                    f"evidence claims to answer pending question {answered_id!r}, "
                    "but there is no current pending question",
                    delta.path,
                )
            )
        elif answered_id != current_id:
            issues.append(
                _issue(
                    EVIDENCE_PENDING_QUESTION_MISMATCH,
                    f"evidence answers pending question {answered_id!r}, "
                    f"but the current pending question is {current_id!r}",
                    delta.path,
                )
            )
        if (
            context.pending_question_path is not None
            and delta.path != context.pending_question_path
        ):
            issues.append(
                _issue(
                    EVIDENCE_PENDING_QUESTION_PATH_MISMATCH,
                    f"pending question targets {context.pending_question_path!r}, "
                    f"so its answer may not change {delta.path!r}",
                    delta.path,
                )
            )
    return issues


def _has_answered_pending_question(delta: IntentDelta) -> bool:
    return any(ref.pending_question_id for ref in delta.evidence_refs)


def _has_path_scoped_evidence(delta: IntentDelta) -> bool:
    """证据是否显式绑定到本 Delta 的路径。

    目前只有 Pending Question 证据携带路径（`EvidenceContext.pending_question_path`）；
    `EvidenceRef` 没有路径字段，因此普通消息证据无法证明路径范围。委托（
    `user_delegated`）必须是"明确授权"，所以要求带待答问题绑定，否则无法验证范围。
    """
    return _has_answered_pending_question(delta)


def _authorization_issues(delta: IntentDelta, context: EvidenceContext) -> list[Issue]:
    """检查 5：operation 是否越过证据所覆盖的授权范围。

    冻结规则（只收紧、不放宽）：
    - `user_specified` / `user_confirmed_proposal`：Delta 必须携带具体 value——
      系统不得替用户凭空产生值；
    - `user_confirmed_proposal`：必须绑定当前待答问题（确认的是已展示的具体提案）；
    - `user_delegated`：证据必须绑定当前待答问题（"明确授权"必须落在问题范围内）；
    - `not_applicable`：必须携带具体 value（domain 语义：not_applicable 必须有
      可检查理由或用户证据，不能用来掩盖遗漏）；`not_applicable` + 具体值即用户
      证据本身。
    """
    resolution = delta.resolution
    if resolution is None:
        return []

    issues: list[Issue] = []
    if resolution in (Resolution.USER_SPECIFIED, Resolution.NOT_APPLICABLE):
        if delta.value is None:
            issues.append(
                _issue(
                    UNAUTHORIZED_RESOLUTION_CLAIM,
                    f"resolution {resolution.value!r} requires an explicit value for "
                    f"{delta.path!r}",
                    delta.path,
                )
            )
    elif resolution is Resolution.USER_CONFIRMED_PROPOSAL:
        if not _has_answered_pending_question(delta):
            issues.append(
                _issue(
                    UNAUTHORIZED_RESOLUTION_CLAIM,
                    "resolution 'user_confirmed_proposal' requires evidence bound to "
                    "the current pending question (the user confirms a shown proposal)",
                    delta.path,
                )
            )
    elif resolution is Resolution.USER_DELEGATED:
        if not _has_path_scoped_evidence(delta):
            issues.append(
                _issue(
                    UNAUTHORIZED_RESOLUTION_CLAIM,
                    "resolution 'user_delegated' requires evidence scoped to a pending "
                    f"question for {delta.path!r}; a bare message cannot prove the "
                    "delegation scope",
                    delta.path,
                )
            )
    return issues


def _system_field_issues(delta: IntentDelta) -> list[Issue]:
    """检查 6：系统字段保护（revision ID、创建时间、workflow state 等）。

    命中根字段名（`workflow_state`）或以其为前缀的路径（`workflow_state.x`）都拒绝。
    """
    root = delta.path.split(".", 1)[0]
    if root in SYSTEM_FIELD_NAMES or delta.path in SYSTEM_FIELD_NAMES:
        return [
            _issue(
                SYSTEM_FIELD_PROTECTED,
                f"path {delta.path!r} targets a system-managed field; system fields are "
                "written by deterministic code, never by deltas",
                delta.path,
            )
        ]
    return []


def _current_value(intent: VisualIntent, path: str) -> object | None:
    """按白名单路径读取 Intent 上的当前值（只读，不触发任何修改）。"""
    facet_name, _, field_name = path.partition(".")
    facet = getattr(intent, facet_name, None)
    if facet is None:
        return None
    return getattr(facet, field_name, None)


def _pin_target_issues(delta: IntentDelta, current_intent: VisualIntent) -> list[Issue]:
    """检查 7：PIN / UNPIN 是否针对可保持的 Intent 路径。

    冻结规则（细化任务书"PIN/UNPIN 是否针对可保持的 Intent 路径"）：

    - PIN 要求该路径当前有值（"保持"的对象必须已存在）；
    - UNPIN 要求该路径当前确实 pinned（否则是无效果操作，显式拒绝）；
    - CLEAR 要求该路径当前未 pinned（"保持"与"删除要求"是冲突指令，先 UNPIN
      再 CLEAR 才成立）。

    这三条都对 `current_intent`（本批次开始前的状态）判定，不模拟批次内的中间
    状态；因此"先 PIN 再 UNPIN 再 CLEAR"这类同批次序列里，末尾的 CLEAR 仍会因
    初始状态 pinned 而被拒。该顺序语义已在 step_02_handoff.md 中写明。
    """
    if delta.operation is DeltaOperation.PIN:
        if _current_value(current_intent, delta.path) is None:
            return [
                _issue(
                    PIN_REQUIRES_EXISTING_VALUE,
                    f"cannot PIN {delta.path!r}: the path currently has no value to preserve",
                    delta.path,
                )
            ]
    elif delta.operation is DeltaOperation.UNPIN:
        if delta.path not in current_intent.pinned_paths:
            return [
                _issue(
                    UNPIN_REQUIRES_PINNED_PATH,
                    f"cannot UNPIN {delta.path!r}: the path is not currently pinned",
                    delta.path,
                )
            ]
    elif delta.operation is DeltaOperation.CLEAR:
        if delta.path in current_intent.pinned_paths:
            return [
                _issue(
                    CLEAR_REQUIRES_UNPINNED_PATH,
                    f"cannot CLEAR pinned path {delta.path!r}; UNPIN it first, then clear",
                    delta.path,
                )
            ]
    return []


def _no_state_effect_issues(delta: IntentDelta, current_intent: VisualIntent) -> list[Issue]:
    """额外检查：Delta 不产生任何可观察效果时显式拒绝（不静默接受 no-op）。

    - 纯 resolution 的 SET：当前 Resolution 已经是同一个值；
    - CLEAR：该路径当前既没有值、也没有 Resolution 记录。

    值型 SET 不在此列：显式重新赋同一个值是合法操作（last-write-wins），
    Validator 不比较值的相等性。
    """
    if delta.operation is DeltaOperation.SET and delta.value is None:
        existing = current_intent.resolutions.get(delta.path)
        if existing is not None and existing.resolution is delta.resolution:
            return [
                _issue(
                    NO_STATE_EFFECT,
                    f"SET resolution for {delta.path!r} is already "
                    f"{delta.resolution.value!r}; the delta would change nothing",
                    delta.path,
                )
            ]
    elif delta.operation is DeltaOperation.CLEAR:
        has_value = _current_value(current_intent, delta.path) is not None
        has_resolution = delta.path in current_intent.resolutions
        if not has_value and not has_resolution:
            return [
                _issue(
                    NO_STATE_EFFECT,
                    f"CLEAR {delta.path!r} would change nothing: the path currently has "
                    "neither a value nor a Resolution record",
                    delta.path,
                )
            ]
    return []


def _delta_issues(
    delta: IntentDelta, context: EvidenceContext, current_intent: VisualIntent
) -> list[Issue]:
    """按任务书顺序运行该 Delta 的全部适用检查。"""
    issues: list[Issue] = []
    issues += _operation_issues(delta)
    issues += _path_issues(delta)
    issues += _value_issues(delta)
    issues += _evidence_issues(delta, context)
    issues += _authorization_issues(delta, context)
    issues += _system_field_issues(delta)
    issues += _pin_target_issues(delta, current_intent)
    issues += _no_state_effect_issues(delta, current_intent)
    return issues


def validate(
    candidate_deltas: list[IntentDelta],
    evidence_context: EvidenceContext,
    current_intent: VisualIntent,
) -> ValidationResult:
    """验证候选 Delta，显式区分 accepted / rejected / issues。

    纯函数：不修改 `candidate_deltas`、`evidence_context`、`current_intent`。

    - `accepted`：通过全部检查的 Delta，按输入顺序保留（Reducer 按此顺序应用）；
    - `rejected`：`RejectedDelta(delta, issues)`，每个被拒 Delta 至少一条 issue；
    - `issues`：所有 issue 的扁平汇总（等于各 rejected 的 issues 连接），
      `severity` 一律为 `error`；被接受的 Delta 不产生 issue。
    """
    accepted: list[IntentDelta] = []
    rejected: list[RejectedDelta] = []
    all_issues: list[Issue] = []

    for delta in candidate_deltas:
        issues = _delta_issues(delta, evidence_context, current_intent)
        if issues:
            rejected.append(RejectedDelta(delta=delta, issues=issues))
            all_issues.extend(issues)
        else:
            accepted.append(delta)

    return ValidationResult(accepted=accepted, rejected=rejected, issues=all_issues)


__all__ = [
    "validate",
    "DELTA_OPERATIONS",
    "OPERATION_INVALID",
    "PATH_NOT_WHITELISTED",
    "SYSTEM_FIELD_PROTECTED",
    "VALUE_TYPE_MISMATCH",
    "EVIDENCE_MISSING",
    "EVIDENCE_MESSAGE_NOT_AVAILABLE",
    "EVIDENCE_PENDING_QUESTION_UNKNOWN",
    "EVIDENCE_PENDING_QUESTION_MISMATCH",
    "EVIDENCE_PENDING_QUESTION_PATH_MISMATCH",
    "UNAUTHORIZED_RESOLUTION_CLAIM",
    "PIN_REQUIRES_EXISTING_VALUE",
    "UNPIN_REQUIRES_PINNED_PATH",
    "CLEAR_REQUIRES_UNPINNED_PATH",
    "NO_STATE_EFFECT",
]
