"""Step 02：B/C 配对执行器端到端（Fake、零网络）。"""

from __future__ import annotations

import json
import types
from hashlib import sha256 as _sha256
from pathlib import Path

import pytest
from v0_6_helpers import (
    EXPECTED_CANDIDATES,
    POSITIVE_VALUES,
    default_synthetic_cases,
    fake_image_factory,
    make_runner,
    make_settings,
    read_prompt_text,
    write_fixture,
)

from evaluation.v0_6.budget import BudgetPolicy
from evaluation.v0_6.context import seed_confirmed_session, session_id_for
from evaluation.v0_6.paired_runner import PairedRunBlockedError
from evaluation.v0_6.records import CaseSpec
from evaluation.v0_6.reporting import TruncatedRecordsError, read_records
from visual_intent_agent.domain import utc_now
from visual_intent_agent.persistence import ConfirmationRecord, SQLiteRepository
from visual_intent_agent.prompt_engine.engine import RENDER_LABELS
from visual_intent_agent.providers.errors import ProviderError


class ScriptedImageProvider:
    """共享调用日志的可脚本化 Fake（离线；不触网）。"""

    def __init__(self, log: list, script) -> None:
        self._log = log
        self._script = script

    def generate(self, request):
        self._log.append(request)
        return self._script(len(self._log), request)


def _factory(log: list, script):
    return lambda: ScriptedImageProvider(log, script)


def _always_ok(count, request):
    from visual_intent_agent.providers.fake_image import FAKE_PNG_BYTES
    from visual_intent_agent.providers.image import GeneratedImage, ImageGenerationResult

    return ImageGenerationResult(
        model="fake-image",
        provider_request_id="fake-req",
        images=[GeneratedImage(content=FAKE_PNG_BYTES, mime_type="image/png")],
        seed=None,
    )


def _run(tmp_path, *, fixture=None, image_factory=None, repetitions=1, **kwargs):
    if fixture is None:
        cases, annotations = default_synthetic_cases(repetitions=repetitions)
        fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=repetitions)
    runner = make_runner(
        tmp_path,
        fixture,
        image_factory=image_factory or fake_image_factory(),
        **kwargs,
    )
    return runner, runner.run()


def test_full_fake_run_denominators_and_pair_outcomes(tmp_path):
    runner, result = _run(tmp_path, repetitions=1)
    summary = result.summary
    assert summary.planned_comparison_pairs == 4
    assert summary.planned_arm_runs == 8
    assert summary.records_total == 8
    assert sum(summary.pair_outcomes.values()) == summary.planned_comparison_pairs
    assert summary.pair_outcomes["both_ok"] == 4
    assert summary.status_counts == {"B:ok": 4, "C:ok": 4}
    assert summary.safety_violations == []
    assert summary.unattempted_arm_runs == 0
    assert len(result.records) == 8
    assert all(record.confirmation_valid for record in result.records)


def test_call_order_is_executed_not_only_recorded(tmp_path):
    cases, annotations = default_synthetic_cases(repetitions=2)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=2)
    runner, result = _run(tmp_path, fixture=fixture, repetitions=2)
    rows = [
        json.loads(line)
        for line in (runner.run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_pair: dict[tuple[str, int], list[str]] = {}
    for row in rows:
        by_pair.setdefault((row["case_id"], row["repetition"]), []).append(row["arm"])
    assert len(by_pair) == 8  # 4 cases × 2 repetitions
    for (case_id, repetition), arms in by_pair.items():
        record = next(
            r for r in result.records if r.case_id == case_id and r.repetition == repetition
        )
        expected = ["C", "B"] if record.call_order == "C_first" else ["B", "C"]
        assert arms == expected
    # 两个方向都真实出现，避免"只写不排"。
    assert {record.call_order for record in result.records} == {"B_first", "C_first"}


def test_no_fabricated_seed_and_prompt_traceable(tmp_path):
    runner, result = _run(tmp_path)
    for record in result.records:
        assert record.status == "ok"
        assert record.provider_calls, "successful arm must record its provider call"
        for call in record.provider_calls:
            assert call.seed_requested is None
            assert call.seed_returned is None
            assert call.internal_attempts_observed is None
            assert call.worst_case_attempts == 1 + runner.budget_policy.max_internal_retries
        db_path = runner.run_dir / record.db_rel_path
        prompt_text = read_prompt_text(db_path, record.prompt_artifact_id)
        from evaluation.v0_6.records import sha256_text

        assert sha256_text(prompt_text) == record.prompt_sha256
        assert record.generation_id is not None
        # 明确值路径不进入委托实现 → 不产生 RealizationState（合法）。
        if record.retrieval.actual_value_source != "explicit_user_value":
            assert record.realization_id is not None
        assert record.image_rel_paths
        # 请求面结构上不存在 seed 字段：绝不把 revision seed 当图片 seed。
        from visual_intent_agent.providers.image import ImageGenerationRequest

        assert "seed" not in ImageGenerationRequest.model_fields


def test_b_arm_has_no_bundles_and_c_arm_adopts_the_release_unit(tmp_path):
    runner, result = _run(tmp_path)
    by_key = {(r.case_id, r.arm): r for r in result.records}
    b = by_key[("syn-lit-01", "B")]
    c = by_key[("syn-lit-01", "C")]
    assert b.retrieval.queried is False
    assert b.retrieval.bundle_ids == []
    assert c.retrieval.queried is True
    assert c.retrieval.adoption_outcome == "adopted"
    assert c.retrieval.knowledge_id == "lighting.character.soft_for_tight_framing"
    assert c.retrieval.candidate_value == EXPECTED_CANDIDATES["lighting.character"]
    assert c.retrieval.actual_value == EXPECTED_CANDIDATES["lighting.character"]
    assert c.retrieval.actual_value_source == "knowledge_unit"
    # 同 revision seed：B 的回退值与 C 记录的回退值一致。
    assert b.retrieval.fallback_value == c.retrieval.fallback_value
    assert c.retrieval.adopted_without_prompt_delta == (
        c.retrieval.actual_value == c.retrieval.fallback_value
    )
    assert c.retrieval.bundle_ids


def test_explicit_and_pinned_controls_are_preserved(tmp_path):
    _runner, result = _run(tmp_path)
    by_key = {(r.case_id, r.arm): r for r in result.records}
    explicit_b = by_key[("syn-explicit-01", "B")]
    explicit_c = by_key[("syn-explicit-01", "C")]
    assert explicit_b.retrieval.actual_value == "deep"
    assert explicit_c.retrieval.actual_value == "deep"
    assert explicit_c.retrieval.queried is False
    assert explicit_c.retrieval.adoption_rejection_reason == "path_already_resolved"

    pinned_b = by_key[("syn-pinned-01", "B")]
    pinned_c = by_key[("syn-pinned-01", "C")]
    assert pinned_c.retrieval.queried is False
    assert pinned_c.retrieval.adoption_rejection_reason == "path_pinned"
    # 合法 PIN = 现存值 + PIN：B/C 两侧值都必须原样保留（不因知识改写）。
    assert pinned_b.retrieval.actual_value == "close_up"
    assert pinned_c.retrieval.actual_value == "close_up"
    assert pinned_b.retrieval.actual_value == pinned_c.retrieval.actual_value
    assert pinned_c.retrieval.actual_value_source == "explicit_user_value"


def test_no_hit_falls_back_identically(tmp_path):
    _runner, result = _run(tmp_path)
    by_key = {(r.case_id, r.arm): r for r in result.records}
    b = by_key[("syn-nohit-01", "B")]
    c = by_key[("syn-nohit-01", "C")]
    assert c.retrieval.queried is True
    assert c.retrieval.outcome == "no_hit"
    assert c.retrieval.adoption_outcome == "not_adopted_fallback"
    assert b.retrieval.fallback_value == c.retrieval.actual_value
    assert c.retrieval.adopted_without_prompt_delta is None  # 未采用


def test_bc_prompts_differ_only_in_the_authorized_path(tmp_path):
    runner, result = _run(tmp_path)
    by_key = {(r.case_id, r.arm): r for r in result.records}
    for case_id, primary in (
        ("syn-lit-01", "lighting.character"),
        ("syn-explicit-01", "camera.depth_of_field"),
        ("syn-pinned-01", "composition.framing"),
        ("syn-nohit-01", "camera.depth_of_field"),
    ):
        b = by_key[(case_id, "B")]
        c = by_key[(case_id, "C")]
        b_prompt = read_prompt_text(runner.run_dir / b.db_rel_path, b.prompt_artifact_id)
        c_prompt = read_prompt_text(runner.run_dir / c.db_rel_path, c.prompt_artifact_id)
        label = f"{RENDER_LABELS[primary]}:"
        b_other = [clause for clause in b_prompt.split(", ") if not clause.startswith(label)]
        c_other = [clause for clause in c_prompt.split(", ") if not clause.startswith(label)]
        assert b_other == c_other, case_id


def test_resume_skips_successful_pairs_without_appending(tmp_path):
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)
    log: list = []
    runner = make_runner(tmp_path, fixture, image_factory=_factory(log, _always_ok), retry_deterministic_failures=False)
    first = runner.run()
    records_path = runner.run_dir / "records.jsonl"
    before = len(log)
    second = runner.run()
    assert second.summary.resumed_ok_skipped == 8
    assert len(log) == before  # 未重复调用 Provider
    assert len(records_path.read_text(encoding="utf-8").splitlines()) == 8
    assert first.summary.pair_outcomes == second.summary.pair_outcomes


def test_unknown_timeout_is_recorded_never_resent(tmp_path):
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)

    def timeout_script(count, request):
        raise ProviderError.timeout("simulated timeout")

    log: list = []
    runner = make_runner(tmp_path, fixture, image_factory=_factory(log, timeout_script))
    first = runner.run()
    assert first.summary.unknown_calls > 0
    assert first.summary.pair_outcomes["unknown"] == 4
    assert first.summary.pair_outcomes["both_ok"] == 0
    decisions = [
        json.loads(line)
        for line in (runner.run_dir / "decisions_required.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(decisions) == 8
    calls_before = len(log)
    second = runner.run()
    assert len(log) == calls_before  # unknown 绝不自动重发
    assert second.summary.decisions_required == 8
    assert second.summary.unknown_pair_keys == first.summary.unknown_pair_keys


def test_budget_boundary_stops_before_request_and_keeps_denominator(tmp_path):
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)
    policy = BudgetPolicy(
        currency="USD",
        max_total_minor=1000,
        max_requests=1,
        max_attempts=10,
        unit_price_minor=10,
        price_source="synthetic",
        max_internal_retries=0,
    )
    _runner, result = _run(tmp_path, fixture=fixture, budget_policy=policy)
    summary = result.summary
    assert summary.budget_exhausted is True
    # 调用顺序固定随机化：第一个真实执行的 arm 成功，同对的第二个 arm 被预算停止。
    statuses = sorted(summary.status_counts.items())
    assert statuses == [("B:skipped", 1), ("C:ok", 1)] or statuses == [
        ("B:ok", 1),
        ("C:skipped", 1),
    ]
    assert summary.unattempted_arm_runs == 6
    assert summary.records_total + summary.unattempted_arm_runs == summary.planned_arm_runs
    assert sum(summary.pair_outcomes.values()) == summary.planned_comparison_pairs == 4
    skipped = next(r for r in result.records if r.status == "skipped")
    assert skipped.error is not None
    assert skipped.error.code == "evaluation.budget_exhausted"


def test_deterministic_failure_is_failed_and_kept_in_denominator(tmp_path):
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)

    def reject(count, request):
        raise ProviderError.invalid_request("simulated rejection")

    _runner, result = _run(tmp_path, fixture=fixture, image_factory=_factory([], reject))
    summary = result.summary
    assert summary.status_counts == {"B:failed": 4, "C:failed": 4}
    assert summary.pair_outcomes == {
        "both_ok": 0,
        "b_failed": 0,
        "c_failed": 0,
        "both_failed": 4,
        "unknown": 0,
        "not_run": 0,
    }
    assert len(summary.failed_pair_keys) == 8
    assert summary.known_cost_minor == 0  # 确定性拒绝不计费


def test_deterministic_retry_reuses_the_same_prompt(tmp_path):
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)

    def fail_then_ok(count, request):
        # 每个不同 Prompt（即每个 arm）的第一次调用失败，重试（同一 Prompt）成功。
        if request.prompt not in seen_prompts:
            seen_prompts.add(request.prompt)
            raise ProviderError.invalid_request("simulated first-attempt rejection")
        return _always_ok(count, request)

    seen_prompts: set[str] = set()
    log: list = []
    _runner, result = _run(
        tmp_path,
        fixture=fixture,
        image_factory=_factory(log, fail_then_ok),
        retry_deterministic_failures=True,
        max_attempts_per_call=2,
    )
    assert result.summary.status_counts == {"B:ok": 4, "C:ok": 4}
    retried = [r for r in result.records if len(r.provider_calls) == 2]
    # 至少有一条 arm 走了确定性失败后的重试；每条重试都必须复用同一 Prompt。
    assert retried
    for record in retried:
        assert record.provider_calls[0].status == "failed"
        assert record.provider_calls[1].status == "ok"
        assert record.provider_calls[0].prompt_sha256 == record.provider_calls[1].prompt_sha256
        assert record.status == "ok"


def test_retry_failed_on_resume_uses_a_fresh_database(tmp_path):
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)

    def fail_first_round(count, request):
        if request.prompt not in seen_prompts:
            seen_prompts.add(request.prompt)
            raise ProviderError.invalid_request("simulated first-run rejection")
        return _always_ok(count, request)

    seen_prompts: set[str] = set()
    log: list = []
    runner = make_runner(
        tmp_path,
        fixture,
        image_factory=_factory(log, fail_first_round),
        retry_failed_on_resume=True,
    )
    first = runner.run()
    assert first.summary.status_counts.get("B:failed", 0) + first.summary.status_counts.get(
        "C:failed", 0
    ) > 0
    second = runner.run()
    assert second.summary.status_counts.get("B:ok", 0) > 0
    retried = [r for r in second.records if r.attempt == 2]
    assert retried, "resume must append new attempt records"
    for record in retried:
        assert "_r2" in record.db_rel_path
    # 旧 attempt 记录仍在（append-only），最新者生效。
    raw = read_records(runner.run_dir / "records.jsonl")
    assert any(r.attempt == 1 for r in raw)
    # 同一 revision seed 仍然一致。
    seeds = {r.intent_revision_id for r in second.records if r.case_id == "syn-lit-01"}
    assert len(seeds) == 1


def test_constraint_violation_detects_extra_confirmation(tmp_path):
    """安全不变量检查真实生效：历史 append-only 被破坏时报告违例。"""
    fixture_dir = tmp_path / "fixture"
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(fixture_dir, cases=cases, annotations=annotations, repetitions=1)
    runner = make_runner(tmp_path, fixture)

    case = CaseSpec.model_validate(cases[0])
    repo = SQLiteRepository(tmp_path / "violation.db")
    try:
        context = seed_confirmed_session(
            repo=repo, case=case, repetition=1, arm="B", settings=make_settings()
        )
        snapshot = repo.get_current_session_snapshot(context.session_id)
        repo.save_confirmation(
            ConfirmationRecord(
                confirmation_id="cnf_extra",
                session_id=context.session_id,
                intent_revision_id=snapshot.current_intent_revision_id,
                execution_revision_id=snapshot.current_execution_revision_id,
                summary_hash=context.summary_hash,
                confirmed_at=utc_now(),
            )
        )
        record = types.SimpleNamespace(repetition=1, arm="B", prompt_artifact_id=None)
        violations = runner._constraint_violations(
            repo=repo, case=case, context=context, record=record
        )
        assert any("exactly 1 confirmation" in violation for violation in violations)
    finally:
        repo.close()


def test_runtime_error_when_session_id_reused_is_avoided(tmp_path):
    """续跑 attempt 使用不同会话 ID（避免 persistence.session_exists）。"""
    assert session_id_for(case_id="c", repetition=1, arm="B", attempt=1) != session_id_for(
        case_id="c", repetition=1, arm="B", attempt=2
    )


def test_resume_is_append_only_for_run_summary_records_and_db(tmp_path):
    """续跑不覆盖 run.json / summary.json / records.jsonl / session.db；只追加快照与记录。"""
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)
    runner = make_runner(tmp_path, fixture)
    runner.run()

    run_json = runner.run_dir / "run.json"
    summary_json = runner.run_dir / "summary.json"
    records_path = runner.run_dir / "records.jsonl"
    db_path = runner.run_dir / "cases" / "syn-lit-01" / "rep1" / "B" / "session.db"
    snapshots_before = sorted((runner.run_dir / "summaries").glob("summary_*.json"))
    snapshot_hashes = {
        path: _sha256(path.read_bytes()).hexdigest() for path in snapshots_before
    }
    fingerprints = {
        path: _sha256(path.read_bytes()).hexdigest()
        for path in (run_json, summary_json, db_path)
    }
    records_before = records_path.read_bytes()
    first_summary = json.loads(summary_json.read_text(encoding="utf-8"))
    assert first_summary["resumed_ok_skipped"] == 0
    assert snapshots_before

    resumed_runner = make_runner(tmp_path, fixture)
    assert resumed_runner.run_id == runner.run_id  # 同身份/配置 → 同 run 目录
    second = resumed_runner.run()
    assert second.summary.resumed_ok_skipped == 8

    # 已有 run/summary/session.db 字节不变。
    for path, expected in fingerprints.items():
        assert _sha256(path.read_bytes()).hexdigest() == expected, path
    # 首次 summary.json 仍是首份快照，不被最新覆盖。
    assert json.loads(summary_json.read_text(encoding="utf-8")) == first_summary
    # records 只追加：全 ok 的续跑不新增记录（旧字节保持为完整前缀）；
    # 需要重跑 failed 时的新增记录由 test_retry_failed_on_resume_uses_a_fresh_database 覆盖。
    assert records_path.read_bytes() == records_before
    # 新增恰好一个 summary 快照，旧快照字节不变；最新快照反映续跑结果。
    snapshots_after = sorted((runner.run_dir / "summaries").glob("summary_*.json"))
    assert len(snapshots_after) == len(snapshots_before) + 1
    for path, expected in snapshot_hashes.items():
        assert _sha256(path.read_bytes()).hexdigest() == expected, path
    latest = json.loads(snapshots_after[-1].read_text(encoding="utf-8"))
    assert latest["resumed_ok_skipped"] == 8
    assert snapshots_after[-1].name == "summary_0002.json"


def test_resume_refuses_a_run_directory_with_drifted_identity(tmp_path):
    """同 run 目录但身份/配置漂移 → 拒绝覆盖已有 run.json。"""
    from evaluation.v0_6.paired_runner import PairedRunBlockedError

    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)
    runner = make_runner(tmp_path, fixture)
    canonical = runner.run().run

    drifted = make_runner(tmp_path, fixture, repetitions=2)
    # repetitions 进入 run_id：不同配置应落不同 run 目录。
    assert drifted.run_id != runner.run_id

    run_json = runner.run_dir / "run.json"
    payload = json.loads(run_json.read_text(encoding="utf-8"))
    payload["code_version"] = "drifted"
    run_json.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PairedRunBlockedError):
        runner._write_run_record_once(runner.run_dir, canonical)


def test_keyboard_interrupt_resume_records_unknown_and_never_resends(tmp_path):
    """C1：崩溃留下的未结算预留必须重建为 unknown，绝不重发。"""
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases[:1], annotations=annotations[:1], repetitions=1)

    def crash_on_second(count, request):
        if count == 2:
            raise KeyboardInterrupt
        return _always_ok(count, request)

    log: list = []
    runner = make_runner(tmp_path, fixture, image_factory=_factory(log, crash_on_second))
    with pytest.raises(KeyboardInterrupt):
        runner.run()
    calls_after_crash = len(log)
    assert calls_after_crash == 2  # 第一个 arm 成功，第二个 arm 在 reserve 后中断

    resumed = make_runner(tmp_path, fixture, image_factory=_factory(log, _always_ok))
    result = resumed.run()
    # 中断 arm 不再调用 Provider；只剩第一条 ok 记录 + 重建的 unknown 记录。
    assert len(log) == calls_after_crash
    interrupted = [record for record in result.records if record.interrupted]
    assert len(interrupted) == 1
    assert interrupted[0].status == "unknown"
    assert interrupted[0].attempt == 1
    assert result.summary.pair_outcomes["unknown"] == 1
    assert result.summary.decisions_required == 1
    assert result.summary.resumed_ok_skipped == 1  # 崩溃前已成功的那个 arm 被跳过


def test_subset_case_id_run_keeps_denominators_consistent(tmp_path):
    """H1：全跑后按 --case-id 子集续跑，分母只按选中计划，不给负数 not_run。"""
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)
    make_runner(tmp_path, fixture).run()
    subset = make_runner(tmp_path, fixture).run(case_ids=["syn-lit-01"])
    summary = subset.summary
    assert summary.planned_comparison_pairs == 1
    assert summary.planned_arm_runs == 2
    assert sum(summary.pair_outcomes.values()) == 1
    assert all(value >= 0 for value in summary.pair_outcomes.values())
    assert summary.records_total == 2
    assert summary.unattempted_arm_runs == 0


def test_run_id_binds_provider_config_and_factory_mode(tmp_path):
    """D：Provider 有效配置/工厂模式进入 run_id，防止不同配置落同一目录。"""
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)
    base = make_runner(tmp_path, fixture)
    same = make_runner(tmp_path, fixture)
    assert base.run_id == same.run_id

    different_timeout = make_runner(
        tmp_path, fixture, settings=make_settings(image_timeout_seconds=1.0)
    )
    different_mode = make_runner(tmp_path, fixture, factory_mode="fake")
    assert different_timeout.run_id != base.run_id
    assert different_mode.run_id != base.run_id


def test_unknown_cost_is_not_reported_as_known(tmp_path):
    """C：known 成本与 unknown 上界分列，绝不把 unknown 记成 known。"""
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)

    def timeout(count, request):
        raise ProviderError.timeout("simulated")

    policy = BudgetPolicy(
        currency="USD",
        max_total_minor=100_000,
        max_requests=100,
        max_attempts=100,
        unit_price_minor=7,
        price_source="synthetic",
        max_internal_retries=2,
    )
    _runner, result = _run(tmp_path, fixture=fixture, image_factory=_factory([], timeout), budget_policy=policy)
    assert result.summary.known_cost_minor == 0
    # 8 个 unknown 调用 × 7×3 最坏上界。
    assert result.summary.unknown_cost_upper_bound_minor == 8 * 21


def test_dry_run_refuses_a_non_offline_factory_mode(tmp_path):
    """H：dry_run 只接受显式 offline/fake/test factory，避免真实 factory 绕门禁。"""
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)
    runner = make_runner(tmp_path, fixture, factory_mode="real")
    with pytest.raises(PairedRunBlockedError):
        runner.run()


def test_mixed_per_case_repetition_plan_fails_closed(tmp_path):
    """E：逐 case repetitions 与全局不一致 → fail-closed。"""
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=2)
    runner = make_runner(tmp_path, fixture)
    with pytest.raises(PairedRunBlockedError):
        runner.run()


def test_truncated_records_tail_fails_closed(tmp_path):
    """F：截断的 JSONL 尾行必须 fail-closed，绝不静默跳过或猜测。"""
    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases, annotations=annotations, repetitions=1)
    runner = make_runner(tmp_path, fixture)
    runner.run()
    records_path = runner.run_dir / "records.jsonl"
    with records_path.open("a", encoding="utf-8") as handle:
        handle.write('{"schema_version":"bc_records_v0_6","record_id":"trunc')
    with pytest.raises(TruncatedRecordsError):
        make_runner(tmp_path, fixture).run()


def test_l2_overreach_check_is_recorded(tmp_path):
    """B：L2 预注册越权检查写入记录（可追溯）。"""
    _runner, result = _run(tmp_path)
    by_key = {(record.case_id, record.arm): record for record in result.records}
    explicit_c = by_key[("syn-explicit-01", "C")]
    pinned_c = by_key[("syn-pinned-01", "C")]
    assert explicit_c.retrieval.overreach_check is not None
    assert explicit_c.retrieval.overreach_check["differs"] is True
    assert pinned_c.retrieval.overreach_check is not None
    assert pinned_c.retrieval.reason_code == "pinned_existing_value_not_queried"
    assert pinned_c.retrieval.adoption_rejection_reason == "path_pinned"


def test_crash_between_settle_and_append_is_recovered(tmp_path, monkeypatch):
    """settle 后、PairRecord append 前崩溃：resume 不重发、不 session_exists，重建记录。"""
    import evaluation.v0_6.paired_runner as paired_runner_module

    cases, annotations = default_synthetic_cases(repetitions=1)
    fixture = write_fixture(tmp_path, cases=cases[:1], annotations=annotations[:1], repetitions=1)
    log: list = []
    runner = make_runner(tmp_path, fixture, image_factory=_factory(log, _always_ok))

    real_append = paired_runner_module.append_jsonl
    calls = {"n": 0}

    def flaky_append(path, payload):
        calls["n"] += 1
        if calls["n"] == 2:  # 第二个 arm：settle 已完成，append 前崩溃
            raise RuntimeError("simulated crash between settle and append")
        return real_append(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(paired_runner_module, "append_jsonl", flaky_append)
        with pytest.raises(RuntimeError):
            runner.run()

    calls_after_crash = len(log)
    assert calls_after_crash == 2  # 两个 arm 都真实调用过 Provider
    records_path = runner.run_dir / "records.jsonl"
    assert len(records_path.read_text(encoding="utf-8").splitlines()) == 1  # 只有第一个 arm 落盘

    resumed = make_runner(tmp_path, fixture, image_factory=_factory(log, _always_ok))
    result = resumed.run()
    # 不重发：Provider 调用数不再增加；不 session_exists：run 正常返回。
    assert len(log) == calls_after_crash
    recovered = [record for record in result.records if record.interrupted]
    assert len(recovered) == 1
    assert recovered[0].status == "ok"
    assert recovered[0].generation_id is not None
    assert recovered[0].prompt_sha256 is not None
    assert result.summary.pair_outcomes["both_ok"] == 1
    assert result.summary.planned_comparison_pairs == 1
    assert result.summary.decisions_required >= 1
    assert len(records_path.read_text(encoding="utf-8").splitlines()) == 2
