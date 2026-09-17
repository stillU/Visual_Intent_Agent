"""Step 02：对 Step 01 冻结 30 上下文跑完整 Fake B/C 配对（只读冻结物、零网络）。

本模块是执行器对**预注册候选集**的端到端验收：

- 分母严格区分：`planned_comparison_pairs = 60`（30 case × 2 repetition）、
  `planned_arm_runs = 120`（× 2 arm）；
- `L1_applicable` 的 C 臂实际采用 v0.5-approved-1 的对应单元；
- `L2_explicit_pin_control` 零检索 / 明确值与 PIN 保持，B/C 在目标路径一致；
- `L3_no_hit_fallback` 检索为 `no_hit` 并回退到与 B 相同的确定性选值；
- 两侧 `intent_revision_id` 相同（纯 case_id+repetition 哈希，不搜索）；
- B/C Prompt 除被授权路径外逐 clause 一致；无安全违例、无 unknown。
"""

from __future__ import annotations

import pytest
from v0_6_helpers import (
    REAL_V05_DIR,
    fake_image_factory,
    make_settings,
    read_prompt_text,
)

from evaluation.v0_6.context import load_annotations, load_cases
from evaluation.v0_6.paired_runner import DEFAULT_CASES_PATH, DEFAULT_ANNOTATIONS_PATH, PairedRunner
from visual_intent_agent.prompt_engine.engine import RENDER_LABELS


@pytest.fixture(scope="module")
def frozen_run(tmp_path_factory):
    output_root = tmp_path_factory.mktemp("v06_frozen")
    runner = PairedRunner(
        settings=make_settings(),
        image_factory=fake_image_factory(),
        output_root=output_root,
        verify_frozen=True,
        knowledge_corpus_dir=REAL_V05_DIR,
        factory_mode="fake",
    )
    return runner, runner.run()


def test_c_arm_retrieval_matches_frozen_annotation_vocabulary(frozen_run):
    """逐 C 臂记录与冻结标注的 outcome/reason/adoption/overreach 完全对齐。"""
    _runner, result = frozen_run
    annotations = load_annotations(DEFAULT_ANNOTATIONS_PATH)
    checked = 0
    for record in result.records:
        if record.arm != "C":
            continue
        annotation = annotations[record.case_id]
        assert record.retrieval.outcome == annotation.expected_retrieval.outcome, record.case_id
        assert (
            record.retrieval.reason_code == annotation.expected_retrieval.reason_code
        ), record.case_id
        assert (
            record.retrieval.adoption_outcome == annotation.expected_adoption.outcome
        ), record.case_id
        assert (
            record.retrieval.adoption_rejection_reason
            == annotation.expected_adoption.adoption_rejection_reason
        ), record.case_id
        if annotation.overreach_check is not None:
            assert record.retrieval.overreach_check == annotation.overreach_check, record.case_id
            checked += 1
    assert checked == 12  # 6 个 L2 上下文 × 2 次重复


def test_denominators_are_separated_and_complete(frozen_run):
    runner, result = frozen_run
    summary = result.summary
    assert summary.planned_comparison_pairs == 60  # 30 case × 2 repetition
    assert summary.planned_arm_runs == 120  # × 2 arms
    assert summary.records_total == 120
    assert sum(summary.pair_outcomes.values()) == 60
    assert summary.pair_outcomes["both_ok"] == 60
    assert summary.status_counts == {"B:ok": 60, "C:ok": 60}
    assert summary.unknown_calls == 0
    assert summary.unknown_cost_upper_bound_minor == 0
    assert summary.safety_violations == []
    assert summary.unattempted_arm_runs == 0


def test_l1_applicable_c_arm_actually_adopts_the_release_unit(frozen_run):
    _runner, result = frozen_run
    annotations = load_annotations(DEFAULT_ANNOTATIONS_PATH)
    l1_cases = {a.case_id for a in annotations.values() if a.layer == "L1_applicable"}
    assert len(l1_cases) == 18
    for record in result.records:
        if record.case_id not in l1_cases or record.arm != "C":
            continue
        annotation = annotations[record.case_id]
        assert record.retrieval.adoption_outcome == "adopted", record.case_id
        assert record.retrieval.knowledge_id == annotation.expected_retrieval.knowledge_id
        assert record.retrieval.candidate_value == annotation.expected_retrieval.candidate_value
        assert record.retrieval.actual_value == annotation.expected_retrieval.candidate_value
        assert record.retrieval.actual_value_source == "knowledge_unit"
        assert record.retrieval.bundle_ids
        # 纯哈希可能与候选值相同：如实记录“采用但 Prompt 无可观察差异”。
        assert record.retrieval.adopted_without_prompt_delta == (
            record.retrieval.actual_value == record.retrieval.fallback_value
        )


def test_l2_controls_do_not_query_and_stay_identical(frozen_run):
    _runner, result = frozen_run
    annotations = load_annotations(DEFAULT_ANNOTATIONS_PATH)
    cases = {case.case_id: case for case in load_cases(DEFAULT_CASES_PATH)}
    l2_cases = {a.case_id for a in annotations.values() if a.layer == "L2_explicit_pin_control"}
    assert len(l2_cases) == 6
    by_case: dict[str, dict[str, object]] = {}
    for record in result.records:
        if record.case_id in l2_cases:
            by_case.setdefault(record.case_id, {})[record.arm] = record
    for case_id, arms in by_case.items():
        case = cases[case_id]
        b, c = arms["B"], arms["C"]  # type: ignore[index]
        assert c.retrieval.queried is False
        assert c.retrieval.bundle_ids == []
        assert b.retrieval.actual_value == c.retrieval.actual_value
        existing_value = case.confirmed_intent.get(case.primary_path)
        if case.primary_path in case.pinned_paths:
            assert c.retrieval.adoption_rejection_reason == "path_pinned"
            if existing_value is not None:
                # 合法 PIN = 现存值 + PIN：值必须原样保留且可观察。
                assert c.retrieval.actual_value == existing_value
        else:
            assert c.retrieval.adoption_rejection_reason == "path_already_resolved"
            assert existing_value is not None
            assert c.retrieval.actual_value == existing_value


def test_l3_no_hit_falls_back_to_the_same_deterministic_value(frozen_run):
    _runner, result = frozen_run
    annotations = load_annotations(DEFAULT_ANNOTATIONS_PATH)
    l3_cases = {a.case_id for a in annotations.values() if a.layer == "L3_no_hit_fallback"}
    assert len(l3_cases) == 6
    for record in result.records:
        if record.case_id not in l3_cases or record.arm != "C":
            continue
        assert record.retrieval.queried is True
        assert record.retrieval.outcome == "no_hit"
        assert record.retrieval.adoption_outcome == "not_adopted_fallback"
        assert record.retrieval.adopted_without_prompt_delta is None


def test_each_pair_shares_the_revision_seed_and_keeps_other_clauses(frozen_run):
    runner, result = frozen_run
    by_pair: dict[tuple[str, int], dict[str, object]] = {}
    for record in result.records:
        by_pair.setdefault((record.case_id, record.repetition), {})[record.arm] = record
    cases = {case.case_id: case for case in load_cases(DEFAULT_CASES_PATH)}
    assert len(by_pair) == 60
    for (case_id, _repetition), arms in by_pair.items():
        b, c = arms["B"], arms["C"]  # type: ignore[index]
        assert b.intent_revision_id == c.intent_revision_id
        assert b.summary_hash == c.summary_hash
        assert b.confirmation_valid and c.confirmation_valid
        primary = cases[case_id].primary_path
        label = f"{RENDER_LABELS[primary]}:"
        b_prompt = read_prompt_text(runner.run_dir / b.db_rel_path, b.prompt_artifact_id)
        c_prompt = read_prompt_text(runner.run_dir / c.db_rel_path, c.prompt_artifact_id)
        b_other = [clause for clause in b_prompt.split(", ") if not clause.startswith(label)]
        c_other = [clause for clause in c_prompt.split(", ") if not clause.startswith(label)]
        assert b_other == c_other, case_id
