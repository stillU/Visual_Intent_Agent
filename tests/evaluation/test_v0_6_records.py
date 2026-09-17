"""Step 02：记录合同与配对结局分类（全离线）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evaluation.v0_6.records import (
    PAIR_OUTCOMES,
    ProviderCallRecord,
    classify_pair_outcome,
    make_record_id,
    sha256_text,
)


def test_make_record_id_is_deterministic_and_namespaced():
    assert make_record_id("bcrun", "a", "b") == make_record_id("bcrun", "a", "b")
    assert make_record_id("bcrun", "a", "b") != make_record_id("bcrun", "a", "c")
    assert make_record_id("bcrun", "a", "b").startswith("bcrun_")
    assert sha256_text("x") == sha256_text("x")


@pytest.mark.parametrize(
    ("b_status", "c_status", "expected"),
    [
        ("ok", "ok", "both_ok"),
        ("ok", "failed", "c_failed"),
        ("failed", "ok", "b_failed"),
        ("failed", "failed", "both_failed"),
        ("ok", "unknown", "unknown"),
        ("unknown", "ok", "unknown"),
        ("failed", "unknown", "unknown"),
        (None, "ok", "not_run"),
        ("ok", None, "not_run"),
        ("skipped", "skipped", "not_run"),
    ],
)
def test_classify_pair_outcome(b_status, c_status, expected):
    assert classify_pair_outcome(b_status, c_status) == expected
    assert expected in PAIR_OUTCOMES


def test_provider_call_record_forbids_extra_and_unknown_seed_fabrication():
    record = ProviderCallRecord(
        call_id="bccall_1",
        attempt_index=1,
        prompt_sha256=sha256_text("prompt"),
        size="1024x1024",
        status="ok",
        latency_ms=1.0,
        worst_case_attempts=3,
        cost_source="worst_case_upper_bound",
    )
    assert record.seed_requested is None
    assert record.seed_returned is None
    assert record.internal_attempts_observed is None
    with pytest.raises(ValidationError):
        ProviderCallRecord(
            call_id="bccall_2",
            attempt_index=1,
            prompt_sha256=sha256_text("prompt"),
            size="1024x1024",
            status="ok",
            latency_ms=1.0,
            worst_case_attempts=3,
            cost_source="x",
            seed_requested=7,  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        ProviderCallRecord(
            call_id="bccall_3",
            attempt_index=1,
            prompt_sha256=sha256_text("prompt"),
            size="1024x1024",
            status="ok",
            latency_ms=1.0,
            worst_case_attempts=3,
            cost_source="x",
            unexpected_field=1,  # type: ignore[call-arg]
        )
