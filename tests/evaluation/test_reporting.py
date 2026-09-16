"""MVP v0.3 Step 03：统一结果合同 + 落盘 + 聚合的离线单元测试。

覆盖任务书验收口径：

- 缺失数据三态（`not_applicable` / `missing_data` / `failed`）在聚合中计数、
  **不从分母剔除**；非 ok 记录不得携带 score/passed（不伪装零分/满分）；
- 单轮与多轮**分开**聚合，且 `all` 视图可继续聚合（counts 求和、样本量相加）；
- L1 任一不变量失败 → `gate_a_blocked = true` 且阻断条目可追溯；
- 逐案例 JSONL 落盘/读回、确定性行序；
- 冻结标注文件可载入（真实 annotations 逐行通过合同校验）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.reporting import (
    L1_INVARIANT_HISTORY,
    L1_INVARIANT_UNAUTHORIZED,
    CaseAnnotation,
    EvalCaseRecord,
    EvalFailureRecord,
    EvalRunRecord,
    EvalTurnRecord,
    MetricAggregate,
    MetricRecord,
    RunSummary,
    aggregate_run,
    load_annotations,
    make_eval_record_id,
    read_case_jsonl,
    write_case_jsonl,
    write_json,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FROZEN_ANNOTATIONS = PROJECT_ROOT / "evaluation" / "annotations" / "core_v0_3.jsonl"

NOW = "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------


def _run_record() -> EvalRunRecord:
    from evaluation.reporting import EvalProviderSnapshot

    return EvalRunRecord(
        run_id="erun_test",
        harness_version="evaluation_harness_v2",
        id_scheme_version="eval_ids_sha256_v1",
        code_version="test",
        protocol_version="gate_a_protocol_v0_3",
        dataset_version="core_v0_3",
        config_version="gate_a_v0_3",
        manifest_version="frozen_manifest_v0_3",
        dataset_sha256="0" * 64,
        annotations_sha256="0" * 64,
        config_sha256="0" * 64,
        protocol_sha256="0" * 64,
        manifest_sha256="0" * 64,
        provider=EvalProviderSnapshot(
            llm_model="qwen3.8-max",
            image_model="qwen-image-3.0",
            llm_timeout_seconds=60.0,
            image_timeout_seconds=120.0,
            http_max_retries=2,
            image_size="1024x1024",
            images_per_generation=1,
            base_url_env="VIA_PROVIDER_BASE_URL",
            api_key_env="VIA_PROVIDER_API_KEY",
            system_b_response_format={"type": "json_object"},
        ),
        repetitions=2,
        l4_repetition=1,
        systems=["baseline_a", "system_b"],
        case_ids=["c1"],
        case_count=1,
        created_at=NOW,  # type: ignore[arg-type]
    )


def _metric(
    metric: str = "delta_accuracy",
    *,
    layer: str = "L2",
    system: str = "system_b",
    turn_type: str = "multi",
    status: str = "ok",
    score: float | None = 0.5,
    passed: bool | None = None,
    counts: dict[str, int] | None = None,
    case_id: str = "c1",
    repetition: int = 1,
) -> MetricRecord:
    return MetricRecord(
        record_id=make_eval_record_id("emet", case_id, system, str(repetition), metric),
        run_id="erun_test",
        case_id=case_id,
        system=system,  # type: ignore[arg-type]
        repetition=repetition,
        turn_type=turn_type,  # type: ignore[arg-type]
        layer=layer,  # type: ignore[arg-type]
        metric=metric,
        status=status,  # type: ignore[arg-type]
        score=score if status == "ok" else None,
        passed=passed if status == "ok" else None,
        counts=counts or {},
        failure=(
            EvalFailureRecord(code="provider.timeout", message="t", stage="llm")
            if status == "failed"
            else None
        ),
        created_at=NOW,  # type: ignore[arg-type]
    )


def _case(
    case_id: str = "c1",
    *,
    system: str = "system_b",
    turn_type: str = "multi",
    status: str = "completed",
    l1_failed: bool | None = None,
) -> EvalCaseRecord:
    return EvalCaseRecord(
        record_id=make_eval_record_id("ecase", case_id, system),
        run_id="erun_test",
        case_id=case_id,
        system=system,  # type: ignore[arg-type]
        repetition=1,
        scenario="s",
        turn_type=turn_type,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        l1_failed=l1_failed,
        created_at=NOW,  # type: ignore[arg-type]
    )


def _turn(case_id: str = "c1", *, system: str = "system_b", turn_id: str = "t1") -> EvalTurnRecord:
    return EvalTurnRecord(
        record_id=make_eval_record_id("eturn", case_id, system, turn_id),
        run_id="erun_test",
        case_id=case_id,
        system=system,  # type: ignore[arg-type]
        repetition=1,
        turn_id=turn_id,
        turn_index=1,
        turn_kind="user_message",
        input_text="你好",
        input_source="user_text",
        status="completed",
        created_at=NOW,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 合同模型不变量
# ---------------------------------------------------------------------------


class TestContractInvariants:
    def test_metric_record_rejects_score_on_non_ok(self) -> None:
        with pytest.raises(ValidationError):
            MetricRecord(
                record_id="emet_x", run_id="erun_test", case_id="c1", system="baseline_a",
                repetition=1, turn_type="single", layer="L1", metric="pinned_not_overwritten",
                status="not_applicable", score=0.0, created_at=NOW,
            )

    def test_metric_record_failed_requires_failure(self) -> None:
        base = _metric(status="failed").model_dump(mode="json")
        base["failure"] = None
        with pytest.raises(ValidationError):
            MetricRecord.model_validate(base)

    def test_turn_failed_requires_error(self) -> None:
        base = _turn().model_dump(mode="json")
        base["status"] = "failed"
        with pytest.raises(ValidationError):
            EvalTurnRecord.model_validate(base)

    def test_flow_deviation_requires_reason(self) -> None:
        base = _turn().model_dump(mode="json")
        base["flow_deviation"] = True
        with pytest.raises(ValidationError):
            EvalTurnRecord.model_validate(base)

    def test_baseline_turn_must_not_carry_system_b_observation(self) -> None:
        base = _turn(system="baseline_a").model_dump(mode="json")
        base["system_b"] = {
            "session_id": "ses_x", "state_before": "UNDERSTANDING",
            "state_after": "UNDERSTANDING",
        }
        with pytest.raises(ValidationError):
            EvalTurnRecord.model_validate(base)

    def test_baseline_case_must_not_carry_l1_failed(self) -> None:
        base = _case(system="baseline_a").model_dump(mode="json")
        base["l1_failed"] = True
        with pytest.raises(ValidationError):
            EvalCaseRecord.model_validate(base)

    def test_extra_fields_forbidden_and_models_frozen(self) -> None:
        record = _metric()
        with pytest.raises(ValidationError):
            MetricRecord(**{**record.model_dump(mode="json"), "unexpected": 1})
        with pytest.raises(ValidationError):
            record.metric = "other"  # type: ignore[misc]

    def test_failure_code_must_be_namespaced(self) -> None:
        with pytest.raises(ValidationError):
            EvalFailureRecord(code="NoDotsHere", message="m", stage="llm")


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


class TestAggregation:
    def test_three_states_counted_and_never_dropped_from_denominator(self) -> None:
        metrics = [
            _metric(status="ok", score=1.0),
            _metric(case_id="c2", status="not_applicable"),
            _metric(case_id="c3", status="missing_data"),
            _metric(case_id="c4", status="failed"),
        ]
        summary = aggregate_run(_run_record(), [], metrics, created_at=_now())
        agg = _find(summary, "L2", "delta_accuracy", "system_b", "all")
        # 分母 = 全部 4 条记录；三态逐一计数。
        assert agg.n_cases == 4
        assert agg.n_ok == 1
        assert agg.n_not_applicable == 1
        assert agg.n_missing_data == 1
        assert agg.n_failed == 1
        assert agg.score_mean == 1.0  # 只在 ok 且带 score 的记录上平均
        assert len(agg.metric_record_ids) == 4  # 可追溯到原始记录

    def test_single_and_multi_kept_separate_and_composable(self) -> None:
        metrics = [
            _metric(turn_type="single", score=1.0, counts={"hits": 2}),
            _metric(turn_type="multi", score=0.0, counts={"hits": 4}),
            _metric(turn_type="multi", score=1.0, counts={"hits": 6}, case_id="c2"),
        ]
        summary = aggregate_run(_run_record(), [], metrics, created_at=_now())
        single = _find(summary, "L2", "delta_accuracy", "system_b", "single")
        multi = _find(summary, "L2", "delta_accuracy", "system_b", "multi")
        both = _find(summary, "L2", "delta_accuracy", "system_b", "all")
        assert single.n_cases == 1 and single.score_mean == 1.0
        assert multi.n_cases == 2 and multi.score_mean == 0.5
        # all 视图 = 两个分组的可继续聚合（样本量相加、计数求和）。
        assert both.n_cases == single.n_cases + multi.n_cases
        assert both.counts_sum["hits"] == 12
        assert both.counts_sum["hits"] == (
            single.counts_sum["hits"] + multi.counts_sum["hits"]
        )

    def test_gate_a_blocked_on_any_l1_failure(self) -> None:
        metrics = [
            _metric(metric=L1_INVARIANT_UNAUTHORIZED, layer="L1", passed=True, score=None),
            _metric(metric=L1_INVARIANT_HISTORY, layer="L1", passed=False, score=None,
                    counts={"violations": 1}),
        ]
        summary = aggregate_run(_run_record(), [], metrics, created_at=_now())
        assert summary.gate_a_blocked is True
        assert len(summary.l1_blocking) == 1
        entry = summary.l1_blocking[0]
        assert entry.case_id == "c1" and entry.repetition == 1
        assert entry.failed_invariants == [L1_INVARIANT_HISTORY]
        assert entry.metric_record_ids  # 可追溯到原始 MetricRecord

    def test_l1_metric_failed_state_also_blocks(self) -> None:
        metrics = [
            _metric(metric=L1_INVARIANT_UNAUTHORIZED, layer="L1", status="failed",
                    score=None),
        ]
        summary = aggregate_run(_run_record(), [], metrics, created_at=_now())
        assert summary.gate_a_blocked is True
        assert summary.l1_blocking[0].failed_invariants == [L1_INVARIANT_UNAUTHORIZED]

    def test_clean_run_not_blocked(self) -> None:
        metrics = [
            _metric(metric=L1_INVARIANT_UNAUTHORIZED, layer="L1", passed=True, score=None),
        ]
        summary = aggregate_run(_run_record(), [], metrics, created_at=_now())
        assert summary.gate_a_blocked is False
        assert summary.l1_blocking == []

    def test_baseline_l1_l2_not_applicable_records_aggregate_without_scores(self) -> None:
        metrics = [
            _metric(metric=L1_INVARIANT_UNAUTHORIZED, layer="L1", system="baseline_a",
                    status="not_applicable"),
            _metric(metric="delta_accuracy", layer="L2", system="baseline_a",
                    status="not_applicable"),
        ]
        summary = aggregate_run(_run_record(), [], metrics, created_at=_now())
        l1 = _find(summary, "L1", L1_INVARIANT_UNAUTHORIZED, "baseline_a", "all")
        assert l1.n_not_applicable == 1
        assert l1.score_mean is None
        assert summary.gate_a_blocked is False  # Baseline 的 L1 不参与 Gate 阻断

    def test_case_status_counts_per_system(self) -> None:
        cases = [
            _case("c1", system="baseline_a"),
            _case("c2", system="baseline_a", status="failed"),
            _case("c3", system="system_b", status="flow_deviation", l1_failed=False),
        ]
        summary = aggregate_run(_run_record(), cases, [], created_at=_now())
        assert summary.case_status_counts == {
            "baseline_a": {"completed": 1, "failed": 1},
            "system_b": {"flow_deviation": 1},
        }

    def test_counts_sum_only_over_ok_records(self) -> None:
        metrics = [
            _metric(status="ok", counts={"hits": 3}),
            _metric(case_id="c2", status="missing_data"),
        ]
        summary = aggregate_run(_run_record(), [], metrics, created_at=_now())
        agg = _find(summary, "L2", "delta_accuracy", "system_b", "all")
        assert agg.counts_sum == {"hits": 3}


# ---------------------------------------------------------------------------
# 落盘与读回
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_case_jsonl_round_trip_and_ordering(self, tmp_path: Path) -> None:
        case_records = [
            _case("c1", system="system_b", l1_failed=False),
            _case("c1", system="baseline_a"),
        ]
        turn_records = [
            _turn("c1", system="system_b", turn_id="t2").model_copy(
                update={"turn_index": 2}
            ),
            _turn("c1", system="system_b", turn_id="t1"),
            _turn("c1", system="baseline_a", turn_id="t1"),
        ]
        metric_records = [_metric()]
        path = tmp_path / "cases" / "c1.jsonl"
        write_case_jsonl(path, case_records, turn_records, metric_records)

        entries = read_case_jsonl(path)
        assert [e["record_type"] for e in entries] == [
            "case", "case", "turn", "turn", "turn", "metric",
        ]
        # 确定性行序：case 按 system；turn 按 (system, turn_index)。
        systems = [e["record"]["system"] for e in entries[:2]]
        assert systems == ["baseline_a", "system_b"]
        turn_keys = [
            (e["record"]["system"], e["record"]["turn_index"]) for e in entries[2:5]
        ]
        assert turn_keys == [("baseline_a", 1), ("system_b", 1), ("system_b", 2)]
        # 回读内容逐字节可解析且字段完整。
        assert entries[2]["record"]["record_id"] == turn_records[2].record_id

    def test_write_json_is_deterministic(self, tmp_path: Path) -> None:
        record = _run_record()
        first = tmp_path / "a.json"
        second = tmp_path / "b.json"
        write_json(first, record)
        write_json(second, record)
        assert first.read_bytes() == second.read_bytes()

    def test_run_json_contains_no_credentials(self, tmp_path: Path) -> None:
        path = tmp_path / "run.json"
        write_json(path, _run_record())
        text = path.read_text(encoding="utf-8")
        # 只记环境变量名；绝不出现 base_url / key 的值。
        assert "provider.invalid" not in text
        assert '"base_url"' not in text
        assert "VIA_PROVIDER_API_KEY" in text


# ---------------------------------------------------------------------------
# 冻结标注载入
# ---------------------------------------------------------------------------


class TestFrozenAnnotationsLoad:
    def test_real_annotations_load_and_align(self) -> None:
        annotations = load_annotations(FROZEN_ANNOTATIONS)
        assert len(annotations) == 22
        assert all(isinstance(a, CaseAnnotation) for a in annotations)
        by_id = {a.case_id: a for a in annotations}
        s01 = by_id["s01-complete-001"]
        assert s01.turn_annotations[0].expected_outcome == "ready_and_generate"
        assert s01.prompt_expectations.must_mention_groups
        s04 = by_id["s04-conflict-001"]
        conflict = s04.turn_annotations[0].expected_conflicts[0]
        assert conflict.rule_id == "hard_conflict.environment_mode_location"
        assert conflict.blocking is True

    def test_turn_annotation_lookup(self) -> None:
        annotations = load_annotations(FROZEN_ANNOTATIONS)
        case = annotations[0]
        assert case.turn_annotation("t1") is not None
        assert case.turn_annotation("nope") is None


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _now():
    from datetime import datetime, timezone

    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _find(
    summary: RunSummary, layer: str, metric: str, system: str, scope: str
) -> MetricAggregate:
    for agg in summary.metrics:
        if (
            agg.layer == layer
            and agg.metric == metric
            and agg.system == system
            and agg.turn_scope == scope
        ):
            return agg
    raise AssertionError(f"aggregate not found: {layer}/{metric}/{system}/{scope}")
