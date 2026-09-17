"""Step 02：真实入口 fail-closed 门禁（全离线，不构造真实 Provider）。"""

from __future__ import annotations

import copy

import pytest
from v0_6_helpers import (
    FAKE_IMAGE_MODEL,
    REAL_V05_DIR,
    default_synthetic_cases,
    make_settings,
    write_fixture,
)

from evaluation.identity import IDENTITY_DIAGNOSTIC, IDENTITY_FORMAL, CodeIdentity
from evaluation.v0_6.budget import BudgetPolicy
from evaluation.v0_6.paired_runner import (
    PairedRunBlockedError,
    PairedRunner,
    real_run_blockers,
)


def _identity(*, dirty: bool = False, mode: str = IDENTITY_FORMAL) -> CodeIdentity:
    return CodeIdentity(
        commit="a" * 40,
        dirty=dirty,
        code_version="synthetic",
        detail="synthetic",
        mode=mode,
    )


def _satisfied_config() -> dict:
    return {
        "config_version": "synthetic",
        "real_run_authorized": True,
        "authorization": {"gate": "G1", "status": "granted"},
        "providers": {
            "llm": {
                "model": "qwen3.8-max",
                "model_identity_verified": True,
                "timeout_seconds": 60.0,
                "max_retries": 2,
            },
            "image": {
                "model_default": FAKE_IMAGE_MODEL,
                "model_identity_verified": True,
                "timeout_seconds": 120.0,
                "max_retries": 2,
            },
        },
        "thresholds": {
            "status": "confirmed",
            "minimum_meaningful_gain": 0.05,
            "allowed_completion_rate_difference": 0.1,
            "max_latency_seconds": 60.0,
            "max_cost": 10.0,
        },
    }


def _budget() -> BudgetPolicy:
    return BudgetPolicy(
        currency="USD",
        max_total_minor=10_000,
        max_requests=120,
        max_attempts=360,
        unit_price_minor=10,
        price_source="synthetic G1",
        max_internal_retries=2,
    )


def _blockers(config: dict, *, identity=None, budget=None, manifest=True, knowledge=True):
    return real_run_blockers(
        config=config,
        identity=identity if identity is not None else _identity(),
        settings=make_settings(),
        budget=budget or _budget(),
        manifest_verified=manifest,
        knowledge_verified=knowledge,
    )


def test_all_conditions_satisfied_has_no_blockers():
    assert _blockers(_satisfied_config()) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(real_run_authorized=False),
        lambda c: c.update(authorization={}),
        lambda c: c.update(authorization={"status": None}),
        lambda c: c.update(authorization={"status": "not_granted"}),
        lambda c: c["providers"]["llm"].update(model_identity_verified=False),
        lambda c: c["providers"]["llm"].update(timeout_seconds=None),
        lambda c: c["providers"]["llm"].update(timeout_seconds=99.0),
        lambda c: c["providers"]["llm"].update(max_retries=None),
        lambda c: c["providers"]["llm"].update(max_retries=0),
        lambda c: c["providers"]["llm"].update(model=None),
        lambda c: c["providers"]["llm"].update(model="unknown-model"),
        lambda c: c["providers"]["llm"].pop("model", None),
        lambda c: c["providers"]["image"].update(model_identity_verified=False),
        lambda c: c["providers"]["image"].update(model_default="other-model"),
        lambda c: c["providers"]["image"].update(timeout_seconds=None),
        lambda c: c["providers"]["image"].update(timeout_seconds=1.0),
        lambda c: c["providers"]["image"].update(max_retries=None),
        lambda c: c["providers"]["image"].update(max_retries=7),
        lambda c: c.update(thresholds={"status": "pending_user_confirmation_before_results"}),
        lambda c: c["thresholds"].update(minimum_meaningful_gain=None),
        lambda c: c["thresholds"].update(allowed_completion_rate_difference=None),
        lambda c: c["thresholds"].update(max_latency_seconds=None),
        lambda c: c["thresholds"].update(max_cost=None),
        lambda c: c["thresholds"].update(max_cost=-1),
        lambda c: c["thresholds"].update(max_latency_seconds="soon"),
    ],
)
def test_each_missing_condition_fails_closed(mutate):
    config = _satisfied_config()
    mutate(config)
    assert _blockers(config) != []


def test_identity_and_frozen_flags_fail_closed():
    config = _satisfied_config()
    assert _blockers(config, identity=_identity(dirty=True)) != []
    assert _blockers(config, identity=_identity(mode=IDENTITY_DIAGNOSTIC)) != []
    assert _blockers(config, identity=object()) != []  # 非 CodeIdentity
    assert _blockers(config, manifest=False) != []
    assert _blockers(config, knowledge=False) != []


def test_incomplete_budget_fails_closed():
    config = _satisfied_config()
    assert _blockers(config, budget=BudgetPolicy()) != []
    assert _blockers(config, budget=BudgetPolicy(currency="USD", unknown_counts_as_consumed=False)) != []


def test_runner_refuses_real_run_without_ever_constructing_a_provider(tmp_path):
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, real_run_authorized=False)
    called = {"n": 0}

    def forbidden_factory():
        called["n"] += 1
        raise AssertionError("real provider must never be constructed in Step 02")

    runner = PairedRunner(
        settings=make_settings(),
        image_factory=forbidden_factory,
        output_root=tmp_path / "runs",
        manifest_path=fixture.manifest_path,
        cases_path=fixture.cases_path,
        annotations_path=fixture.annotations_path,
        config_path=fixture.config_path,
        protocol_path=fixture.protocol_path,
        verify_frozen=False,
        knowledge_corpus_dir=REAL_V05_DIR,
        identity=_identity(dirty=True),
        dry_run=False,
    )
    with pytest.raises(PairedRunBlockedError) as excinfo:
        runner.assert_real_run_allowed()
    assert called["n"] == 0
    assert "authorization" in str(excinfo.value) or "real_run_authorized" in str(excinfo.value)


def test_runner_refuses_real_run_even_when_all_gates_pass(tmp_path):
    """门禁全过也不在 Step 02 构造真实 Provider（G1 后由 Step 03 接线）。"""
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(
        tmp_path,
        cases=cases,
        annotations=annotations,
        real_run_authorized=True,
        providers_overrides={
            "model_default": FAKE_IMAGE_MODEL,
            "model_identity_verified": True,
            "timeout_seconds": 120.0,
            "max_retries": 2,
        },
        providers_llm_overrides={
            "model": "qwen3.8-max",
            "model_identity_verified": True,
            "timeout_seconds": 60.0,
            "max_retries": 2,
        },
        thresholds_status="confirmed",
        thresholds_overrides={
            "minimum_meaningful_gain": 0.05,
            "allowed_completion_rate_difference": 0.1,
            "max_latency_seconds": 60.0,
            "max_cost": 10.0,
        },
    )

    runner = PairedRunner(
        settings=make_settings(),
        image_factory=lambda: pytest.fail("real provider must not be constructed"),
        output_root=tmp_path / "runs",
        manifest_path=fixture.manifest_path,
        cases_path=fixture.cases_path,
        annotations_path=fixture.annotations_path,
        config_path=fixture.config_path,
        protocol_path=fixture.protocol_path,
        verify_frozen=True,
        knowledge_corpus_dir=REAL_V05_DIR,
        identity=_identity(),
        budget_policy=_budget(),
        dry_run=False,
    )
    with pytest.raises(PairedRunBlockedError) as excinfo:
        runner.assert_real_run_allowed()
    assert "does not construct a real image provider" in str(excinfo.value)
