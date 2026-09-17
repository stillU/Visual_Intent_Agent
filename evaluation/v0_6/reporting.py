"""MVP v0.6 Step 02：Run 汇总与只追加落盘工具。

汇总口径（协议第 4.3 / 5 节）：

- **两种分母严格区分**：
  - `planned_comparison_pairs = case_count × repetitions`（真正的 B/C 比较对数，
    净胜率等配对指标的分母）；
  - `planned_arm_runs = case_count × repetitions × 2`（基础 arm 运行 / 图片请求数）。
- **分母完整**：`pair_outcomes` 之和恒等于 `planned_comparison_pairs`；
  `ok` / `failed` / `unknown` / `skipped` / `not_attempted` 每层之和恒等于该层
  `planned_arm_runs`，失败与未知**绝不剔除**；
- `failed_pair_keys` / `unknown_pair_keys` 逐条列出，便于 Step 03/04 追溯；
- 分层（`L1_applicable` / `L2_explicit_pin_control` / `L3_no_hit_fallback`）分别保留
  计划对数与状态计数；
- 成本分列：`known_cost_minor`（已结算已知）与 `unknown_cost_upper_bound_minor`
  （unknown + 未结算预留上界），绝不把 unknown 叫 known。

本模块不复用旧 A/B 的 `evaluation.reporting.aggregate_run`（其系统标识与 L1~L3 指标
绑定 v0.3 口径），避免把 B/C 结论混入旧标签。
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from .budget import BudgetSnapshot
from .records import (
    ARMS,
    PAIR_OUTCOMES,
    Arm,
    LayerSummary,
    PairRecord,
    RunSummaryV06,
    classify_pair_outcome,
    utc_now,
)

RECORDS_FILENAME = "records.jsonl"
DECISIONS_FILENAME = "decisions_required.jsonl"


def planned_pair_keys(
    case_ids_by_layer: dict[str, Sequence[str]], repetitions: int
) -> list[tuple[str, int, Arm]]:
    """全部计划配对键（顺序稳定：层 → case → repetition → 臂）。"""
    keys: list[tuple[str, int, Arm]] = []
    for layer in case_ids_by_layer:
        for case_id in case_ids_by_layer[layer]:
            for repetition in range(1, repetitions + 1):
                for arm in ARMS:
                    keys.append((case_id, repetition, arm))
    return keys


def planned_counts_by_layer(
    case_ids_by_layer: dict[str, Sequence[str]], repetitions: int
) -> dict[str, int]:
    """每层计划的 **arm 运行数**（`case × repetition × arm`）。"""
    return {
        layer: len(case_ids) * repetitions * len(ARMS)
        for layer, case_ids in case_ids_by_layer.items()
    }


def planned_comparison_counts_by_layer(
    case_ids_by_layer: dict[str, Sequence[str]], repetitions: int
) -> dict[str, int]:
    """每层计划的 **B/C 比较对数**（`case × repetition`）。"""
    return {
        layer: len(case_ids) * repetitions for layer, case_ids in case_ids_by_layer.items()
    }


def summarize(
    *,
    run_id: str,
    planned_counts: dict[str, int],
    records: Iterable[PairRecord],
    ledger: BudgetSnapshot,
    decisions_required: int,
    resumed_ok_skipped: int,
    budget_exhausted: bool,
    cost_basis: str,
    currency: str | None,
    unattempted_keys: Sequence[str] = (),
    safety_violations: Sequence[str] = (),
    clock=None,
) -> RunSummaryV06:
    """由全部记录（含续跑前已有记录）聚合 Run 汇总。

    两种分母严格区分：

    - `planned_arm_runs = Σ 每层 case × repetition × 2`（基础 arm 运行 / 图片请求）；
    - `planned_comparison_pairs = Σ 每层 case × repetition`（B/C 比较对数，配对指标分母）。

    `pair_outcomes` 之和恒等于 `planned_comparison_pairs`（未开始的比较对计 `not_run`）。
    `ok+failed+unknown+skipped+not_attempted` 每层之和恒等于该层 `planned_arm_runs`。
    """
    record_list = list(records)
    status_counts: Counter[str] = Counter()
    retrieval_counts: Counter[str] = Counter()
    adoption_counts: Counter[str] = Counter()
    fallback_reasons: Counter[str] = Counter()
    failed_keys: list[str] = []
    unknown_keys: list[str] = []
    layer_status: dict[str, Counter[str]] = {layer: Counter() for layer in planned_counts}

    provider_calls_total = 0
    provider_calls_ok = 0
    unknown_calls = 0
    for record in record_list:
        status_counts[f"{record.arm}:{record.status}"] += 1
        layer_status.setdefault(record.layer, Counter())[record.status] += 1
        provider_calls_total += len(record.provider_calls)
        provider_calls_ok += sum(1 for call in record.provider_calls if call.status == "ok")
        unknown_calls += sum(1 for call in record.provider_calls if call.status == "unknown")
        retrieval = record.retrieval
        if retrieval.queried:
            retrieval_counts["queried"] += 1
        else:
            retrieval_counts["not_queried"] += 1
        retrieval_counts[f"outcome:{retrieval.outcome}"] += 1
        if retrieval.adoption_outcome is not None:
            adoption_counts[retrieval.adoption_outcome] += 1
        reason = retrieval.adoption_rejection_reason or retrieval.reason_code
        if reason is not None:
            fallback_reasons[reason] += 1
        pair_key = f"{record.case_id}/rep{record.repetition}/{record.arm}"
        if record.status == "failed":
            failed_keys.append(pair_key)
        elif record.status == "unknown":
            unknown_keys.append(pair_key)

    # 比较对分组（case × repetition）。
    arms_by_pair: dict[tuple[str, int], dict[str, PairRecord]] = {}
    layer_of_pair: dict[tuple[str, int], str] = {}
    for record in record_list:
        key = (record.case_id, record.repetition)
        arms_by_pair.setdefault(key, {})[record.arm] = record
        layer_of_pair[key] = record.layer

    overall_outcomes: Counter[str] = Counter()
    layer_outcomes: dict[str, Counter[str]] = {layer: Counter() for layer in planned_counts}
    for key, arms in arms_by_pair.items():
        b_status = arms["B"].status if "B" in arms else None
        c_status = arms["C"].status if "C" in arms else None
        outcome = classify_pair_outcome(b_status, c_status)
        overall_outcomes[outcome] += 1
        layer_outcomes.setdefault(layer_of_pair[key], Counter())[outcome] += 1

    planned_arm_runs = sum(planned_counts.values())
    planned_comparison_pairs = sum(count // len(ARMS) for count in planned_counts.values())
    # 无任何 arm 记录的计划比较对 → not_run。
    overall_outcomes["not_run"] += planned_comparison_pairs - len(arms_by_pair)

    recorded_arm_keys = {(record.case_id, record.repetition, record.arm) for record in record_list}

    layers: list[LayerSummary] = []
    for layer, arm_planned in planned_counts.items():
        comparison_planned = arm_planned // len(ARMS)
        outcomes = layer_outcomes.get(layer, Counter())
        recorded_arms = sum(
            1 for key in recorded_arm_keys if layer_of_pair.get((key[0], key[1])) == layer
        )
        layers.append(
            LayerSummary(
                layer=layer,
                planned_comparison_pairs=comparison_planned,
                planned_arm_runs=arm_planned,
                ok=layer_status.get(layer, Counter())["ok"],
                failed=layer_status.get(layer, Counter())["failed"],
                unknown=layer_status.get(layer, Counter())["unknown"],
                skipped=layer_status.get(layer, Counter())["skipped"],
                not_attempted=max(0, arm_planned - recorded_arms),
                both_ok=outcomes["both_ok"],
                b_failed=outcomes["b_failed"],
                c_failed=outcomes["c_failed"],
                both_failed=outcomes["both_failed"],
                pair_unknown=outcomes["unknown"],
                not_run=outcomes["not_run"]
                + max(0, comparison_planned - sum(outcomes.values())),
            )
        )

    return RunSummaryV06(
        run_id=run_id,
        planned_comparison_pairs=planned_comparison_pairs,
        planned_arm_runs=planned_arm_runs,
        records_total=len(record_list),
        status_counts=dict(sorted(status_counts.items())),
        pair_outcomes={name: overall_outcomes[name] for name in PAIR_OUTCOMES},
        layers=layers,
        retrieval_counts=dict(sorted(retrieval_counts.items())),
        adoption_counts=dict(sorted(adoption_counts.items())),
        fallback_reasons=dict(sorted(fallback_reasons.items())),
        provider_calls_total=provider_calls_total,
        provider_calls_ok=provider_calls_ok,
        unknown_calls=unknown_calls,
        known_cost_minor=ledger.charged_known_minor,
        unknown_cost_upper_bound_minor=ledger.charged_unknown_minor + ledger.outstanding_minor,
        currency=currency,
        cost_basis=cost_basis,
        decisions_required=decisions_required,
        resumed_ok_skipped=resumed_ok_skipped,
        failed_pair_keys=sorted(failed_keys),
        unknown_pair_keys=sorted(unknown_keys),
        unattempted_arm_runs=max(0, planned_arm_runs - len(recorded_arm_keys)),
        unattempted_pair_keys=sorted(unattempted_keys),
        safety_violations=list(safety_violations),
        budget_exhausted=budget_exhausted,
        created_at=(clock or utc_now)(),
    )


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------


def write_json(path: Path, payload: BaseModel) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: BaseModel) -> None:
    """只追加一行 JSON 并 `flush + fsync`（不重写历史，崩溃后前缀仍完整）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload.model_dump_json())
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


class TruncatedRecordsError(ValueError):
    """`records.jsonl` 末行被截断（进程中断）——fail-closed，需人工决定。

    执行器**不**猜测缺失记录、不把截断行当成功：调用方应核对预算账本中的未知预留，
    用 `decisions_required.jsonl` 请求人工决定后再续跑。
    """


def read_records(path: Path) -> list[PairRecord]:
    """读取逐行记录；末行截断时抛 `TruncatedRecordsError`（fail-closed）。"""
    path = Path(path)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    records: list[PairRecord] = []
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            records.append(PairRecord.model_validate_json(raw))
        except Exception as exc:  # noqa: BLE001 - 损坏/截断必须显式失败，绝不静默跳过
            if line_number == len(lines) and not text.endswith("\n"):
                raise TruncatedRecordsError(
                    f"{path}:{line_number} looks like a truncated JSONL tail (process "
                    "interruption). Records are append-only and never auto-repaired: reconcile "
                    "the unknown reservation in budget_ledger.jsonl and record a human decision "
                    "before resuming."
                ) from exc
            raise ValueError(f"{path}:{line_number} is not a PairRecord: {exc}") from exc
    return records


__all__ = [
    "RECORDS_FILENAME",
    "DECISIONS_FILENAME",
    "planned_pair_keys",
    "planned_counts_by_layer",
    "planned_comparison_counts_by_layer",
    "summarize",
    "write_json",
    "append_jsonl",
    "read_records",
    "TruncatedRecordsError",
]
