"""Step 02：预算账本与最坏预留（全离线）。"""

from __future__ import annotations

import pytest

from evaluation.v0_6.budget import (
    BudgetExceededError,
    BudgetLedger,
    BudgetPolicy,
    read_ledger_entries,
)
from v0_6_helpers import fixed_clock


def _policy(**overrides) -> BudgetPolicy:
    values = {
        "currency": "USD",
        "max_total_minor": 100,
        "max_requests": 10,
        "max_attempts": 30,
        "unit_price_minor": 10,
        "price_source": "synthetic",
        "max_internal_retries": 2,
    }
    values.update(overrides)
    return BudgetPolicy(**values)


def test_worst_case_reservation_covers_internal_retries(tmp_path):
    policy = _policy()
    assert policy.worst_case_attempts == 3
    assert policy.worst_case_cost_minor == 30
    ledger = BudgetLedger(tmp_path / "ledger.jsonl", policy, clock=fixed_clock())
    entry = ledger.reserve(reservation_id="r1", pair_key="c/rep1/B", attempt_index=1)
    assert entry.worst_case_attempts == 3
    assert entry.worst_case_cost_minor == 30
    # 预留先于调用落盘：文件在 settle 之前已可读。
    rows = read_ledger_entries(tmp_path / "ledger.jsonl")
    assert [row.event for row in rows] == ["reserve"]


def test_settle_and_unknown_accounting(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger.jsonl", _policy(), clock=fixed_clock())
    ledger.reserve(reservation_id="r1", pair_key="p1", attempt_index=1)
    ledger.settle("r1", status="ok", charged_cost_minor=30, cost_source="worst_case")
    snapshot = ledger.snapshot()
    assert snapshot.charged_minor == 30
    assert snapshot.settled_ok == 1
    assert snapshot.outstanding_minor == 0

    ledger.reserve(reservation_id="r2", pair_key="p2", attempt_index=1)
    ledger.settle("r2", status="unknown", charged_cost_minor=30, cost_source="unknown")
    snapshot = ledger.snapshot()
    assert snapshot.settled_unknown == 1
    assert snapshot.charged_minor == 60
    # 已知与未知成本分列：unknown 绝不并入 known。
    assert snapshot.charged_known_minor == 30
    assert snapshot.charged_unknown_minor == 30
    assert ledger.unknown_pair_keys() == {"p2"}


def test_unsettled_reservation_from_interruption_is_unknown(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = BudgetLedger(path, _policy(), clock=fixed_clock())
    ledger.reserve(reservation_id="r1", pair_key="p1", attempt_index=1)
    # 模拟进程中断：重新打开账本（无 settle 残留）。
    replayed = BudgetLedger(path, _policy(), clock=fixed_clock())
    assert replayed.unknown_pair_keys() == {"p1"}
    assert replayed.snapshot().outstanding_minor == 30
    assert replayed.snapshot().unsettled == 1


def test_request_and_attempt_caps_stop_before_reserve(tmp_path):
    policy = _policy(max_requests=1)
    ledger = BudgetLedger(tmp_path / "ledger.jsonl", policy, clock=fixed_clock())
    ledger.reserve(reservation_id="r1", pair_key="p1", attempt_index=1)
    allowed, reason = ledger.can_reserve()
    assert allowed is False and "request cap" in reason
    with pytest.raises(BudgetExceededError):
        ledger.reserve(reservation_id="r2", pair_key="p2", attempt_index=1)

    attempt_policy = _policy(
        max_requests=100, max_attempts=1, unit_price_minor=1, max_internal_retries=0
    )
    attempt_ledger = BudgetLedger(tmp_path / "attempts.jsonl", attempt_policy, clock=fixed_clock())
    attempt_ledger.reserve(reservation_id="a1", pair_key="p1", attempt_index=1)
    allowed, reason = attempt_ledger.can_reserve()
    assert allowed is False and "attempt cap" in reason


def test_cost_cap_uses_charged_plus_outstanding_plus_next_worst(tmp_path):
    policy = _policy(max_total_minor=50, max_requests=100, max_attempts=100)
    ledger = BudgetLedger(tmp_path / "ledger.jsonl", policy, clock=fixed_clock())
    ledger.reserve(reservation_id="r1", pair_key="p1", attempt_index=1)  # worst 30
    allowed, reason = ledger.can_reserve()  # 0 charged + 30 outstanding + 30 next = 60 > 50
    assert allowed is False and "cost cap" in reason

    ledger.settle("r1", status="ok", charged_cost_minor=0, cost_source="synthetic_free")
    allowed, _ = ledger.can_reserve()  # 0 + 0 + 30 <= 50
    assert allowed is True


def test_policy_completeness_reports_missing_fields():
    assert _policy().is_real_ready is True
    draft = BudgetPolicy()
    assert draft.is_real_ready is False
    assert set(draft.missing_fields()) == {
        "currency",
        "max_total_minor",
        "max_requests",
        "max_attempts",
        "unit_price_minor",
        "price_source",
    }
