"""L2 Intent Understanding（协议第 4 节 Layer 2；仅 System B）。

五个指标（口径逐条对齐协议）：

1. **Delta Accuracy**（`delta_accuracy`）：逐轮对金标准——`expected_deltas` 按
   `(operation, path) + value_match + resolution` 匹配得 recall；系统被接受 Delta
   中落入 `expected ∪ acceptable_extra` 的比例得 precision；其余被接受 Delta 计为
   **越界产生**（含命中 `expected_rejections` 却被接受、命中
   `paths_must_remain_unset` 的 Delta，分别另计归因数）。
2. **Missing Decision Recall**（`missing_decision_recall`）：
   `blocking_missing_paths_after_turn` 中被系统 `unresolved_decisions`
   （action=block）覆盖的比例（与 `assess` 同口径：每 Decision 第一个未解决成员）。
3. **Clarification Precision**（`clarification_precision`）：实际提问的
   `target_path ∈ must_clarify ∪ acceptable_clarify_paths` 且
   `∉ must_not_clarify_paths`；不该问而问、问错路径均计失败；
   `expected_outcome=ready_and_generate` 的轮次提出任何问题即失败。
4. **Conflict Detection**（`conflict_detection`）：`expected_conflicts` 中被系统
   `conflicts` / `detected_conflicts`（同 rule_id）检出的比例；`blocking=true`
   未检出且系统进入 ready 记**严重失败**。
5. **Delegation Scope Accuracy**（`delegation_scope_accuracy`）：案例终态 Intent 中
   `user_delegated` 记录的路径集合 == 标注期望（`expected_resolution_records` 中
   delegated 项的逐轮并集）；多标/漏标分别计数。注意冻结语义：用户以具体值接管
   delegated 路径后 Reducer **保留**其 `user_delegated` 记录（Step 01 已知限制 4），
   标注已按此口径书写，评测不得把它判为异常。

纯函数：不触库、不触网；观察缺失（轮失败）的期望**不剔分母**，按未覆盖计。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from evaluation.reporting import (
    ExpectedDeltaSpec,
    MetricPayload,
    ObservedDelta,
    SystemBTurnObservation,
    TurnAnnotation,
    ValueMatchSpec,
)

_FROZEN = ConfigDict(frozen=True, extra="forbid")

_WHITESPACE = re.compile(r"\s+")

#: 冲突 rule_id 的合法命名空间（标注与系统 issue code 的归一化目标）。
_CONFLICT_NAMESPACES = ("hard_conflict.", "execution_conflict.")


class TurnObservationInput(BaseModel):
    """一轮的 L2 输入：标注真值 + 系统观察（观察缺失 = 该轮执行失败）。"""

    model_config = _FROZEN

    turn_id: str
    turn_index: int = Field(ge=1)
    annotation: TurnAnnotation | None = None
    observation: SystemBTurnObservation | None = None
    turn_failed: bool = False


def normalize_token(text: str) -> str:
    """`exact_token` 规范化（协议第 2.3 节：小写、`_`/`-` 视空格、压缩空白）。"""
    return _WHITESPACE.sub(" ", text.replace("_", " ").replace("-", " ").lower()).strip()


def value_matches(spec: ValueMatchSpec | None, value: str | int | None) -> bool:
    """按 `value_match` 模式判定系统 Delta 的值是否符合金标准（协议第 2.3 节）。

    - `spec is None` 或 `mode="null"`：该 Delta 不应携带值（CLEAR/PIN/UNPIN 或
      纯 resolution SET）→ 值为 None 才匹配；
    - `exact_token`：规范化后与任一关键词相等；
    - `contains_any`：值（不区分大小写）包含任一关键词；
    - `int_equals`：整数相等（`subject.count` 唯一 int 路径）；
    - `any_non_empty`：任意非空值。
    """
    if spec is None or spec.mode == "null":
        return value is None
    if spec.mode == "any_non_empty":
        return value is not None and str(value).strip() != ""
    if spec.mode == "int_equals":
        if spec.value is None:
            return False
        try:
            return int(value) == spec.value  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
    if value is None:
        return False
    text = str(value)
    if spec.mode == "exact_token":
        normalized = normalize_token(text)
        return any(normalized == normalize_token(keyword) for keyword in spec.keywords)
    if spec.mode == "contains_any":
        lowered = text.lower()
        return any(keyword.lower() in lowered for keyword in spec.keywords)
    raise ValueError(f"unknown value_match mode: {spec.mode!r}")


def delta_matches(expected: ExpectedDeltaSpec, observed: ObservedDelta) -> bool:
    """金标准 Delta 匹配：`(operation, path)` + `value_match` + `resolution`。"""
    if expected.operation != observed.operation or expected.path != observed.path:
        return False
    if not value_matches(expected.value_match, observed.value):
        return False
    if expected.resolution is not None and observed.resolution != expected.resolution:
        return False
    return True


def normalize_conflict_rule_id(code: str) -> str | None:
    """把系统 issue code 归一化为冲突 rule_id（剥离 `policy.` 前缀）。

    `assess` 产出的冲突 code 形如 `policy.hard_conflict.environment_mode_location`
    （rule_id 为 `hard_conflict.environment_mode_location`）；Interpreter 报告的
    `detected_conflicts` code 原样进入 issues，若恰为 rule_id 也命中。
    非冲突 code 返回 None。
    """
    candidate = code[len("policy."):] if code.startswith("policy.") else code
    if candidate.startswith(_CONFLICT_NAMESPACES):
        return candidate
    return None


# ---------------------------------------------------------------------------
# 1. Delta Accuracy
# ---------------------------------------------------------------------------


def evaluate_delta_accuracy(turns: list[TurnObservationInput]) -> MetricPayload:
    """Delta Accuracy：recall（主 score）/ precision / 越界产生计数。"""
    expected_total = 0
    matched_total = 0
    applied_total = 0
    in_scope_total = 0
    rejection_expected = 0
    rejection_satisfied = 0
    rejection_failures = 0
    unset_violations = 0
    out_of_scope: list[dict[str, str]] = []
    unmatched_expected: list[dict[str, str]] = []
    rejection_failure_details: list[dict[str, str]] = []
    unset_violation_details: list[dict[str, str]] = []

    for turn in turns:
        annotation = turn.annotation
        if annotation is None:
            continue
        observed = list(turn.observation.applied_deltas) if turn.observation else []
        issues = list(turn.observation.issues) if turn.observation else []

        # recall：每条 expected 找一条未消费的 applied 匹配（贪婪一对一）。
        remaining = list(observed)
        for expected in annotation.expected_deltas:
            expected_total += 1
            hit = next((d for d in remaining if delta_matches(expected, d)), None)
            if hit is not None:
                matched_total += 1
                remaining.remove(hit)
            else:
                unmatched_expected.append(
                    {
                        "turn_id": turn.turn_id,
                        "operation": expected.operation,
                        "path": expected.path,
                    }
                )

        # precision / 越界产生：applied 落入 expected ∪ acceptable_extra 即合规。
        in_scope_specs = [
            *annotation.expected_deltas,
            *annotation.acceptable_extra_deltas,
        ]
        for delta in observed:
            applied_total += 1
            if any(delta_matches(spec, delta) for spec in in_scope_specs):
                in_scope_total += 1
            else:
                out_of_scope.append(
                    {
                        "turn_id": turn.turn_id,
                        "operation": delta.operation,
                        "path": delta.path,
                    }
                )

        # 条件性拒绝：被提出且被接受 = 失败；被提出且按期望 code 被拒 = 符合；
        # 未被提出 = 条件不触发（only_if_emitted，记 vacuous）。
        for rejection in annotation.expected_rejections:
            rejection_expected += 1
            accepted_hit = any(
                delta.operation == rejection.operation and delta.path == rejection.path
                for delta in observed
            )
            if accepted_hit:
                rejection_failures += 1
                rejection_failure_details.append(
                    {
                        "turn_id": turn.turn_id,
                        "operation": rejection.operation,
                        "path": rejection.path,
                    }
                )
                continue
            rejected_ok = any(
                issue.path == rejection.path
                and issue.code in set(rejection.expected_issue_codes)
                for issue in issues
            )
            if rejected_ok:
                rejection_satisfied += 1

        # paths_must_remain_unset：该轮结束后仍须无值且无 Resolution 记录。
        if turn.observation is not None:
            for path in annotation.paths_must_remain_unset:
                if (
                    turn.observation.intent_values_after.get(path) is not None
                    or path in turn.observation.resolutions_after
                ):
                    unset_violations += 1
                    unset_violation_details.append(
                        {"turn_id": turn.turn_id, "path": path}
                    )

    recall = (matched_total / expected_total) if expected_total else None
    precision = (in_scope_total / applied_total) if applied_total else None
    return MetricPayload(
        status="ok",
        score=recall,
        counts={
            "expected_deltas": expected_total,
            "matched_expected": matched_total,
            "applied_deltas": applied_total,
            "in_scope": in_scope_total,
            "out_of_scope": applied_total - in_scope_total,
            "expected_rejections": rejection_expected,
            "rejections_satisfied": rejection_satisfied,
            "rejection_failures": rejection_failures,
            "must_remain_unset_violations": unset_violations,
        },
        details={
            "precision": precision,
            "unmatched_expected": unmatched_expected,
            "out_of_scope_deltas": out_of_scope,
            "rejection_failures": rejection_failure_details,
            "must_remain_unset_violations": unset_violation_details,
        },
    )


# ---------------------------------------------------------------------------
# 2. Missing Decision Recall
# ---------------------------------------------------------------------------


def evaluate_missing_decision_recall(turns: list[TurnObservationInput]) -> MetricPayload:
    """`blocking_missing_paths_after_turn` 被系统 block 级未决覆盖的比例。"""
    expected_total = 0
    covered_total = 0
    missed: list[dict[str, str]] = []
    for turn in turns:
        annotation = turn.annotation
        if annotation is None:
            continue
        observed = (
            set(turn.observation.blocking_unresolved_paths) if turn.observation else set()
        )
        for path in annotation.blocking_missing_paths_after_turn:
            expected_total += 1
            if path in observed:
                covered_total += 1
            else:
                missed.append({"turn_id": turn.turn_id, "path": path})
    return MetricPayload(
        status="ok",
        score=(covered_total / expected_total) if expected_total else None,
        counts={"expected_blocking": expected_total, "covered_blocking": covered_total},
        details={"missed_blocking": missed},
    )


# ---------------------------------------------------------------------------
# 3. Clarification Precision
# ---------------------------------------------------------------------------


def evaluate_clarification_precision(turns: list[TurnObservationInput]) -> MetricPayload:
    """提问精确性：问错路径/不该问而问/ready 轮提问均计失败。

    `must_clarify` 的满足情况只作证据计数（协议未定义 Clarification Recall；
    未提问的缺失由 Missing Decision Recall 与 outcome 偏离承载）。
    """
    questions_asked = 0
    questions_bad = 0
    ready_turn_questions = 0
    must_clarify_total = 0
    must_clarify_satisfied = 0
    bad_questions: list[dict[str, str]] = []
    missed_must_clarify: list[dict[str, str]] = []

    for turn in turns:
        annotation = turn.annotation
        if annotation is None:
            continue
        asked = turn.observation.question if turn.observation else None
        allowed = {spec.target_path for spec in annotation.must_clarify} | set(
            annotation.acceptable_clarify_paths
        )
        forbidden = set(annotation.must_not_clarify_paths)
        if asked is not None:
            questions_asked += 1
            on_ready_turn = annotation.expected_outcome == "ready_and_generate"
            legitimate = (
                asked.target_path in allowed
                and asked.target_path not in forbidden
                and not on_ready_turn
            )
            if on_ready_turn:
                ready_turn_questions += 1
            if not legitimate:
                questions_bad += 1
                bad_questions.append(
                    {
                        "turn_id": turn.turn_id,
                        "target_path": asked.target_path,
                        "reason": (
                            "question_on_ready_turn"
                            if on_ready_turn
                            else (
                                "forbidden_path"
                                if asked.target_path in forbidden
                                else "unexpected_path"
                            )
                        ),
                    }
                )
        for spec in annotation.must_clarify:
            must_clarify_total += 1
            if asked is not None and asked.target_path == spec.target_path:
                must_clarify_satisfied += 1
            else:
                missed_must_clarify.append(
                    {"turn_id": turn.turn_id, "target_path": spec.target_path}
                )

    return MetricPayload(
        status="ok",
        score=(
            (questions_asked - questions_bad) / questions_asked if questions_asked else None
        ),
        counts={
            "questions_asked": questions_asked,
            "questions_bad": questions_bad,
            "questions_on_ready_turns": ready_turn_questions,
            "must_clarify_total": must_clarify_total,
            "must_clarify_satisfied": must_clarify_satisfied,
        },
        details={
            "bad_questions": bad_questions,
            "missed_must_clarify": missed_must_clarify,
        },
    )


# ---------------------------------------------------------------------------
# 4. Conflict Detection
# ---------------------------------------------------------------------------


def evaluate_conflict_detection(turns: list[TurnObservationInput]) -> MetricPayload:
    """expected_conflicts 的同 rule_id 检出率；blocking 未检出且进入 ready 记严重失败。"""
    expected_total = 0
    detected_total = 0
    severe_failures = 0
    missed: list[dict[str, str]] = []
    severe: list[dict[str, str]] = []
    for turn in turns:
        annotation = turn.annotation
        if annotation is None:
            continue
        observed_ids = (
            set(turn.observation.conflict_rule_ids) if turn.observation else set()
        )
        reached_ready = bool(
            turn.observation is not None
            and (
                turn.observation.ready_for_confirmation
                or turn.observation.generations
                or turn.observation.state_after == "WAITING_CONFIRMATION"
            )
        )
        for conflict in annotation.expected_conflicts:
            expected_total += 1
            if conflict.rule_id in observed_ids:
                detected_total += 1
                continue
            missed.append({"turn_id": turn.turn_id, "rule_id": conflict.rule_id})
            if conflict.blocking and reached_ready:
                severe_failures += 1
                severe.append(
                    {
                        "turn_id": turn.turn_id,
                        "rule_id": conflict.rule_id,
                        "detail": (
                            "blocking conflict missed while the system reached ready"
                        ),
                    }
                )
    return MetricPayload(
        status="ok",
        score=(detected_total / expected_total) if expected_total else None,
        counts={
            "expected_conflicts": expected_total,
            "detected_conflicts": detected_total,
            "severe_failures": severe_failures,
        },
        details={"missed_conflicts": missed, "severe_failures": severe},
    )


# ---------------------------------------------------------------------------
# 5. Delegation Scope Accuracy
# ---------------------------------------------------------------------------


def evaluate_delegation_scope(turns: list[TurnObservationInput]) -> MetricPayload:
    """案例终态 `user_delegated` 路径集合 == 标注期望（逐轮并集）；多标/漏标分计。"""
    expected: set[str] = set()
    for turn in turns:
        if turn.annotation is None:
            continue
        for path, resolution in turn.annotation.expected_resolution_records.items():
            if resolution == "user_delegated":
                expected.add(path)

    final_observation = next(
        (turn.observation for turn in reversed(turns) if turn.observation is not None),
        None,
    )
    if final_observation is None:
        return MetricPayload(
            status="missing_data",
            counts={"expected_delegated": len(expected)},
            details={"reason": "no executed turn produced an observable intent state"},
        )

    actual = set(final_observation.delegated_paths_after)
    over = sorted(actual - expected)
    under = sorted(expected - actual)
    union = expected | actual
    score = 1.0 if not over and not under else (len(expected & actual) / len(union) if union else None)
    return MetricPayload(
        status="ok",
        score=score,
        counts={
            "expected_delegated": len(expected),
            "actual_delegated": len(actual),
            "over_delegated": len(over),
            "under_delegated": len(under),
        },
        details={
            "expected": sorted(expected),
            "actual": sorted(actual),
            "over_delegated_paths": over,
            "under_delegated_paths": under,
            "note": (
                "Reducer 冻结语义：用户接管 delegated 路径后其记录仍保留为 "
                "user_delegated（Step 01 已知限制 4），标注按同一口径书写"
            ),
        },
    )


__all__ = [
    "TurnObservationInput",
    "normalize_token",
    "value_matches",
    "delta_matches",
    "normalize_conflict_rule_id",
    "evaluate_delta_accuracy",
    "evaluate_missing_decision_recall",
    "evaluate_clarification_precision",
    "evaluate_conflict_detection",
    "evaluate_delegation_scope",
]
