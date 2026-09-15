"""MVP v0.3 Step 03：A/B Runner 的离线集成测试（Fake Provider，零网络）。

覆盖任务书验收口径：

- 同一 Fake 配置重放两次 → 结构一致（产品 uuid4 ID / 含 uuid 的哈希经结构投影掩码）；
- 重复 2 次的记录结构（每案例 × 2 系统 × 2 重复）；
- 协议第 3 节驱动规则：自动确认+立即生成、answer_variants 选择、flow_deviation 记录、
  accept 轮（System B 走 ReviewService；Baseline 跳过不执行）；
- 失败保留在分母（Provider 失败 → 轮 failed / 案例 failed / 指标 missing_data）；
- L1 任一失败 → 摘要显式 `gate_a_blocked`（此处由指标级注入验证聚合链路；
  不变量级正反例见 test_metrics_state.py）；
- 每案例全新 SQLite（跨案例无状态泄漏）；
- 真实运行命令存在（CLI 解析 / --list-cases / 缺凭据退出码），默认测试不触发真实
  Provider（ast 静态扫描：runner 的真实 adapter 只在函数内延迟 import；测试目录
  不 import 任何真实 adapter / httpx）。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_image import FakeImageProvider

from evaluation.reporting import L1_INVARIANTS, L2_METRICS, L3_METRICS, read_case_jsonl
from evaluation.runner import (
    DEFAULT_DATASET_PATH,
    EvaluationRunner,
    build_arg_parser,
    main,
)

from evaluation_harness_helpers import (
    BASELINE_PROMPT_TEXT,
    FULL_INTENT_RESPONSE,
    clear_entry,
    delegate_entry,
    feedback_response,
    fixed_clock,
    fixed_monotonic,
    interpreter_response,
    make_annotation,
    make_fixture_case,
    make_llm_factory,
    make_settings,
    make_turn,
    make_turn_annotation,
    revise_entry,
    set_entry,
    structural_projection,
    write_config,
    write_jsonl,
    ScriptedLLM,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 装配工具
# ---------------------------------------------------------------------------


def _write_inputs(
    root: Path,
    cases: list[dict],
    annotations: list[dict],
    *,
    repetitions: int = 2,
) -> tuple[Path, Path, Path]:
    dataset = write_jsonl(root / "dataset.jsonl", cases)
    annos = write_jsonl(root / "annotations.jsonl", annotations)
    config = write_config(root / "config.json", repetitions=repetitions)
    return dataset, annos, config


def _runner_for(
    root: Path,
    cases: list[dict],
    annotations: list[dict],
    *,
    llm_factory,
    image_factory=None,
    repetitions: int = 2,
) -> EvaluationRunner:
    dataset, annos, config = _write_inputs(
        root / "inputs", cases, annotations, repetitions=repetitions
    )
    return EvaluationRunner(
        settings=make_settings(),
        llm_factory=llm_factory,
        image_factory=image_factory or (lambda: FakeImageProvider()),
        output_root=root / "runs",
        config_path=config,
        dataset_path=dataset,
        annotations_path=annos,
        repetitions=repetitions,
        verify_frozen=False,
        clock=fixed_clock(),
        monotonic=fixed_monotonic(),
    )


def _single_turn_case() -> tuple[list[dict], list[dict]]:
    case = make_fixture_case(
        "sx-single-001",
        [make_turn("t1", "user_message", "一只橘猫蜷在客厅沙发扶手上睡觉，写实，中景，暖光")],
    )
    anno = make_annotation(
        "sx-single-001",
        [
            make_turn_annotation(
                "t1",
                expected_deltas=[
                    {"operation": "SET", "path": "subject.description",
                     "value_match": {"mode": "contains_any", "keywords": ["橘", "猫"]},
                     "resolution": None},
                ],
                expected_outcome="ready_and_generate",
                paths_must_remain_unset=["camera.angle"],
            )
        ],
    )
    return [case], [anno]


def _multiturn_case() -> tuple[list[dict], list[dict]]:
    case = make_fixture_case(
        "sx-multi-001",
        [
            make_turn("t1", "user_message", "一只橘猫在客厅，写实，中景"),
            make_turn("t2", "image_feedback", "只把光线改成戏剧光"),
            make_turn("t3", "accept", "可以了，完成"),
        ],
        scenario="single_field_modification",
        turn_type="multi",
    )
    anno = make_annotation(
        "sx-multi-001",
        [
            make_turn_annotation("t1", expected_outcome="ready_and_generate"),
            make_turn_annotation(
                "t2",
                expected_outcome="ready_and_generate",
                expected_feedback_decision="revise",
                forbidden_change_paths=[
                    "subject.description", "subject.pose_action", "style.primary",
                ],
            ),
            make_turn_annotation(
                "t3",
                expected_outcome="accepted_completed",
                expected_feedback_decision="accept",
                forbidden_change_paths=["subject.description", "lighting.character"],
            ),
        ],
        scenario="single_field_modification",
    )
    return [case], [anno]


def _multiturn_llm_factory():
    return make_llm_factory(
        baseline=[BASELINE_PROMPT_TEXT, BASELINE_PROMPT_TEXT + "，戏剧性光线"],
        interpreter=[FULL_INTENT_RESPONSE],
        feedback=[
            feedback_response(
                "revise", candidate_deltas=[revise_entry("lighting.character", "dramatic")]
            ),
            feedback_response("accept"),
        ],
    )


# ---------------------------------------------------------------------------
# 单轮全链路 + 重复结构 + 确定性重放
# ---------------------------------------------------------------------------


class TestSingleTurnRun:
    def test_structure_two_systems_two_repetitions(self, tmp_path: Path) -> None:
        cases, annos = _single_turn_case()
        runner = _runner_for(
            tmp_path / "r1", cases, annos,
            llm_factory=make_llm_factory(
                interpreter=[FULL_INTENT_RESPONSE], baseline=[BASELINE_PROMPT_TEXT]
            ),
        )
        result = runner.run()

        # 每案例 × 2 系统 × 2 重复 = 4 条案例记录。
        assert len(result.cases) == 4
        combos = sorted((c.system, c.repetition) for c in result.cases)
        assert combos == [
            ("baseline_a", 1), ("baseline_a", 2), ("system_b", 1), ("system_b", 2),
        ]
        assert all(c.status == "completed" for c in result.cases)
        assert result.run.repetitions == 2
        assert result.run.l4_repetition == 1

        # System B：L1 四条不变量全过；Baseline：L1/L2 一律 not_applicable。
        for rep in (1, 2):
            sb = {m.metric: m for m in result.metrics
                  if m.system == "system_b" and m.repetition == rep}
            assert set(sb) == set(L1_INVARIANTS) | set(L2_METRICS) | set(L3_METRICS)
            assert all(sb[i].passed is True for i in L1_INVARIANTS)
            ba = [m for m in result.metrics
                  if m.system == "baseline_a" and m.repetition == rep]
            by_layer = {(m.layer, m.metric): m for m in ba}
            for invariant in L1_INVARIANTS:
                assert by_layer[("L1", invariant)].status == "not_applicable"
            for metric in L2_METRICS:
                assert by_layer[("L2", metric)].status == "not_applicable"
            # 不伪装零分/满分：not_applicable 记录无 score/passed。
            assert all(
                m.score is None and m.passed is None
                for m in ba if m.status == "not_applicable"
            )

        # System B 单轮：自动确认 + 立即生成（一轮内确认+生成计入该轮）。
        turn = next(
            t for t in result.turns if t.system == "system_b" and t.repetition == 1
        )
        obs = turn.system_b
        assert obs is not None
        assert obs.auto_confirmed is True
        assert len(obs.generations) == 1
        assert obs.confirmation is not None
        assert turn.actual_outcome == "ready_and_generate"
        assert turn.outcome_matches is True
        # 随机性记录：Interpreter 调用携带冻结 response_format；图像调用 seed=None
        #（Fake 不返回 seed，禁止伪造）。
        assert len(turn.llm_calls) == 1
        assert turn.llm_calls[0].response_format_sent == {"type": "json_object"}
        assert turn.llm_calls[0].temperature_sent is None
        assert len(turn.image_calls) == 1
        assert turn.image_calls[0].seed is None
        # 确认探针：hash 篡改被拒。
        assert obs.bad_hash_probe == "rejected"

        # 落盘布局：run.json / summary.json / cases/<case_id>.jsonl /
        # baseline_a/rep<N>/ / system_b/rep<N>/cases/<case_id>/session.db。
        run_dir = runner.run_dir
        assert (run_dir / "run.json").is_file()
        assert (run_dir / "summary.json").is_file()
        entries = read_case_jsonl(run_dir / "cases" / "sx-single-001.jsonl")
        assert {e["record_type"] for e in entries} == {"case", "turn", "metric"}
        # 统一 Artifact 引用合同：Baseline 每轮都带 Step 02 逐轮原始 JSON 的
        # 相对路径引用（随机性字段的落盘位置），且该路径在 run 目录内可解析。
        for turn in (t for t in result.turns if t.system == "baseline_a"):
            turn_refs = [r for r in turn.artifact_refs if r.kind == "baseline_turn_record"]
            assert len(turn_refs) == 1
            assert turn_refs[0].path is not None
            assert (run_dir / turn_refs[0].path).is_file()
        for rep in (1, 2):
            assert (run_dir / "baseline_a" / f"rep{rep}" / "run.json").is_file()
            assert (
                run_dir / "system_b" / f"rep{rep}" / "cases" / "sx-single-001" / "session.db"
            ).is_file()

        # 摘要：L1 无阻断；聚合含 single/multi/all 三个维度键。
        assert result.summary.gate_a_blocked is False
        scopes = {(a.system, a.metric, a.turn_scope) for a in result.summary.metrics}
        assert ("system_b", "intent_coverage", "single") in scopes
        assert ("system_b", "intent_coverage", "all") in scopes

    def test_l4_designation_only_on_first_repetition(self, tmp_path: Path) -> None:
        cases, annos = _single_turn_case()
        runner = _runner_for(
            tmp_path / "r2", cases, annos,
            llm_factory=make_llm_factory(
                interpreter=[FULL_INTENT_RESPONSE], baseline=[BASELINE_PROMPT_TEXT]
            ),
        )
        result = runner.run()
        for turn in result.turns:
            image_refs = [r for r in turn.artifact_refs if r.kind in ("baseline_image", "image_file")]
            assert image_refs, "each executed turn should reference an image"
            for ref in image_refs:
                assert ref.designated_for_l4 is (turn.repetition == 1)

    def test_deterministic_replay_produces_structurally_identical_results(
        self, tmp_path: Path
    ) -> None:
        cases, annos = _multiturn_case()
        results = []
        for name in ("replay_a", "replay_b"):
            runner = _runner_for(
                tmp_path / name, cases, annos, llm_factory=_multiturn_llm_factory()
            )
            results.append(runner.run())
        first = structural_projection(results[0].model_dump(mode="json"))
        second = structural_projection(results[1].model_dump(mode="json"))
        assert first == second
        # run_id 内容寻址：同配置两次运行 run_id 相同。
        assert results[0].run.run_id == results[1].run.run_id

    def test_run_id_changes_with_nonce(self, tmp_path: Path) -> None:
        cases, annos = _single_turn_case()
        factory = make_llm_factory(
            interpreter=[FULL_INTENT_RESPONSE], baseline=[BASELINE_PROMPT_TEXT]
        )
        dataset, annos_path, config = _write_inputs(tmp_path / "in", cases, annos)
        common = dict(
            settings=make_settings(), llm_factory=factory,
            image_factory=lambda: FakeImageProvider(), output_root=tmp_path / "runs",
            config_path=config, dataset_path=dataset, annotations_path=annos_path,
            verify_frozen=False, clock=fixed_clock(), monotonic=fixed_monotonic(),
        )
        runner_a = EvaluationRunner(run_nonce="exp1", **common)
        runner_b = EvaluationRunner(run_nonce="exp2", **common)
        assert runner_a.run_id != runner_b.run_id


# ---------------------------------------------------------------------------
# 多轮驱动：反馈 / accept / 探针 / 状态隔离
# ---------------------------------------------------------------------------


class TestMultiTurnDriving:
    def test_revise_reconfirm_accept_flow(self, tmp_path: Path) -> None:
        cases, annos = _multiturn_case()
        runner = _runner_for(tmp_path / "flow", cases, annos,
                             llm_factory=_multiturn_llm_factory())
        result = runner.run()
        turns = {
            t.turn_id: t
            for t in result.turns if t.system == "system_b" and t.repetition == 1
        }

        # t1：首轮即可确认 → 自动确认 + 生成。
        assert turns["t1"].actual_outcome == "ready_and_generate"
        assert turns["t1"].system_b.state_after == "WAITING_REVIEW"

        # t2：revise → 新 revision → 旧确认失效（探针拒绝过期绑定）→ 重新确认 → 再生成。
        t2 = turns["t2"].system_b
        assert turns["t2"].actual_outcome == "ready_and_generate"
        assert t2.feedback_decision == "revise"
        assert t2.stale_revision_probe == "rejected"  # 过期 revision 确认必须失败
        assert t2.bad_hash_probe == "rejected"        # 篡改 hash 确认必须失败
        assert t2.intent_revision_after != t2.intent_revision_before
        assert t2.confirmation is not None
        assert t2.confirmation.intent_revision_id == t2.intent_revision_after
        assert [d.path for d in t2.applied_deltas] == ["lighting.character"]

        # t3：accept → COMPLETED，无新生成。
        t3 = turns["t3"].system_b
        assert turns["t3"].actual_outcome == "accepted_completed"
        assert t3.feedback_decision == "accept"
        assert t3.state_after == "COMPLETED"
        assert not t3.generations

        # Baseline：accept 轮不执行（skipped），其余轮逐字执行。
        baseline_turns = {
            t.turn_id: t
            for t in result.turns if t.system == "baseline_a" and t.repetition == 1
        }
        assert baseline_turns["t3"].status == "skipped"
        assert baseline_turns["t3"].input_source == "not_executed"
        assert baseline_turns["t3"].prompt_text is None
        assert baseline_turns["t1"].status == "completed"
        assert baseline_turns["t1"].input_text == "一只橘猫在客厅，写实，中景"

        # L2/L3 证据：delta_accuracy 记录 carry 观察存在；preservation 有配对。
        sb_metrics = {
            m.metric: m for m in result.metrics
            if m.system == "system_b" and m.repetition == 1
        }
        assert sb_metrics["preservation"].status == "ok"
        assert sb_metrics["preservation"].counts["pairs"] == 1
        assert sb_metrics["intent_coverage"].score == 1.0
        # L1 全过、案例级 l1_failed=False、摘要无阻断。
        case = next(
            c for c in result.cases if c.system == "system_b" and c.repetition == 1
        )
        assert case.l1_failed is False
        assert result.summary.gate_a_blocked is False

    def test_fresh_sqlite_per_case_no_cross_case_leak(self, tmp_path: Path) -> None:
        cases_a, annos_a = _single_turn_case()
        case_b = make_fixture_case(
            "sx-single-002",
            [make_turn("t1", "user_message", "一只黑狗在沙滩上奔跑")],
        )
        anno_b = make_annotation(
            "sx-single-002", [make_turn_annotation("t1", expected_outcome="ready_and_generate")]
        )
        runner = _runner_for(
            tmp_path / "iso", cases_a + [case_b], annos_a + [anno_b],
            llm_factory=make_llm_factory(
                interpreter_default=FULL_INTENT_RESPONSE, baseline_default=BASELINE_PROMPT_TEXT
            ),
        )
        result = runner.run()
        sessions = {
            t.system_b.session_id
            for t in result.turns
            if t.system == "system_b" and t.system_b is not None
        }
        # 2 案例 × 2 重复 = 4 个互不相同的产品会话（每案例全新 SQLite + 全新会话）。
        assert len(sessions) == 4
        run_dir = runner.run_dir
        dbs = sorted(run_dir.glob("system_b/rep*/cases/*/session.db"))
        assert len(dbs) == 4

    def test_unknown_case_id_rejected(self, tmp_path: Path) -> None:
        cases, annos = _single_turn_case()
        runner = _runner_for(
            tmp_path / "unknown", cases, annos,
            llm_factory=make_llm_factory(interpreter_default=FULL_INTENT_RESPONSE),
        )
        with pytest.raises(ValueError, match="unknown case_ids"):
            runner.run(case_ids=["no-such-case"])

    def test_case_subset_run(self, tmp_path: Path) -> None:
        cases_a, annos_a = _single_turn_case()
        case_b = make_fixture_case(
            "sx-single-002", [make_turn("t1", "user_message", "一只黑狗")]
        )
        anno_b = make_annotation(
            "sx-single-002", [make_turn_annotation("t1", expected_outcome="ready_and_generate")]
        )
        runner = _runner_for(
            tmp_path / "subset", cases_a + [case_b], annos_a + [anno_b],
            llm_factory=make_llm_factory(
                interpreter_default=FULL_INTENT_RESPONSE, baseline_default=BASELINE_PROMPT_TEXT
            ),
        )
        result = runner.run(case_ids=["sx-single-002"])
        assert result.run.case_ids == ["sx-single-002"]
        assert {c.case_id for c in result.cases} == {"sx-single-002"}


# ---------------------------------------------------------------------------
# 澄清驱动：answer_variants 与 flow_deviation
# ---------------------------------------------------------------------------


class TestClarificationDriving:
    def _clarify_case(
        self, answer_turn: dict, *, case_id: str = "sx-clarify-001"
    ) -> tuple[list[dict], list[dict]]:
        case = make_fixture_case(
            case_id,
            [make_turn("t1", "user_message", "帮我画个东西"), answer_turn],
            scenario="missing_core_decision",
            turn_type="multi",
        )
        anno = make_annotation(
            case_id,
            [
                make_turn_annotation("t1", expected_outcome="clarification_expected"),
                make_turn_annotation("t2", expected_outcome="ready_and_generate"),
            ],
            scenario="missing_core_decision",
        )
        return [case], [anno]

    def test_answer_variant_selected_by_pending_question_target(
        self, tmp_path: Path
    ) -> None:
        # t1：Interpreter 空候选 → 系统提问（空 Intent 的首个 core 缺失 =
        # subject.description）；t2 的 answer_variants 按该 target_path 选择文本。
        cases, annos = self._clarify_case(
            make_turn(
                "t2", "clarification_answer", "随便画画",
                answer_variants={"subject.description": "一只橘猫"},
            )
        )
        answer_response = interpreter_response(
            set_entry("subject.description", "一只橘猫", answers=True),
            set_entry("subject.pose_action", "端坐"),
            set_entry("style.primary", "photorealistic"),
            set_entry("environment.mode", "indoor"),
            set_entry("environment.location", "客厅"),
            set_entry("composition.framing", "medium_shot"),
            set_entry("lighting.character", "soft"),
        )
        runner = _runner_for(
            tmp_path / "variant", cases, annos,
            llm_factory=make_llm_factory(
                interpreter=[interpreter_response(), answer_response],
                baseline=[BASELINE_PROMPT_TEXT, BASELINE_PROMPT_TEXT],
            ),
        )
        result = runner.run()
        turns = {
            t.turn_id: t
            for t in result.turns if t.system == "system_b" and t.repetition == 1
        }
        t1 = turns["t1"]
        assert t1.actual_outcome == "clarification_expected"
        assert t1.system_b.question is not None
        assert t1.system_b.question.target_path == "subject.description"
        t2 = turns["t2"]
        assert t2.input_source == "answer_variant"
        assert t2.input_text == "一只橘猫"
        assert t2.flow_deviation is False
        assert t2.actual_outcome == "ready_and_generate"
        case = next(
            c for c in result.cases if c.system == "system_b" and c.repetition == 1
        )
        assert case.status == "completed"

    def test_unmatched_answer_variants_fall_back_and_mark_deviation(
        self, tmp_path: Path
    ) -> None:
        cases, annos = self._clarify_case(
            make_turn(
                "t2", "clarification_answer", "橘色的猫",
                # 变体键与实际待答问题（subject.description）不匹配。
                answer_variants={"style.primary": "写实"},
            ),
            case_id="sx-clarify-002",
        )
        answer_response = interpreter_response(
            set_entry("subject.description", "橘色的猫", answers=True),
            set_entry("subject.pose_action", "端坐"),
            set_entry("style.primary", "photorealistic"),
            set_entry("environment.mode", "indoor"),
            set_entry("environment.location", "客厅"),
            set_entry("composition.framing", "medium_shot"),
            set_entry("lighting.character", "soft"),
        )
        runner = _runner_for(
            tmp_path / "variant_miss", cases, annos,
            llm_factory=make_llm_factory(
                interpreter=[interpreter_response(), answer_response],
                baseline=[BASELINE_PROMPT_TEXT, BASELINE_PROMPT_TEXT],
            ),
        )
        result = runner.run()
        t2 = next(
            t for t in result.turns
            if t.system == "system_b" and t.repetition == 1 and t.turn_id == "t2"
        )
        assert t2.input_source == "user_text"
        assert t2.input_text == "橘色的猫"
        assert t2.flow_deviation is True
        assert "answer_variants" in (t2.flow_deviation_reason or "")
        case = next(
            c for c in result.cases if c.system == "system_b" and c.repetition == 1
        )
        assert case.status == "flow_deviation"
        assert case.flow_deviation_turns == ["t2"]

    def test_scripted_answer_while_not_waiting_is_flow_deviation(
        self, tmp_path: Path
    ) -> None:
        # t1 首轮即可确认并生成（WAITING_REVIEW）；t2 脚本却是 clarification_answer
        # → 按协议第 3 节仍按脚本提交，记录 flow_deviation（submit 被状态机拒绝）。
        case = make_fixture_case(
            "sx-deviation-001",
            [
                make_turn("t1", "user_message", "一只橘猫，写实，中景"),
                make_turn("t2", "clarification_answer", "橘色"),
            ],
            scenario="missing_core_decision",
            turn_type="multi",
        )
        anno = make_annotation(
            "sx-deviation-001",
            [
                make_turn_annotation("t1", expected_outcome="ready_and_generate"),
                make_turn_annotation("t2", expected_outcome="clarification_expected"),
            ],
            scenario="missing_core_decision",
        )
        runner = _runner_for(
            tmp_path / "deviation", [case], [anno],
            llm_factory=make_llm_factory(
                interpreter=[FULL_INTENT_RESPONSE],
                baseline=[BASELINE_PROMPT_TEXT, BASELINE_PROMPT_TEXT],
            ),
        )
        result = runner.run()
        t2 = next(
            t for t in result.turns
            if t.system == "system_b" and t.repetition == 1 and t.turn_id == "t2"
        )
        assert t2.flow_deviation is True
        assert t2.status == "failed"
        assert t2.error is not None and t2.error.code == "workflow.invalid_state"
        case_record = next(
            c for c in result.cases if c.system == "system_b" and c.repetition == 1
        )
        assert case_record.status == "failed"


# ---------------------------------------------------------------------------
# 失败口径：Provider 失败保留在分母
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_image_provider_failure_is_recorded_and_kept_in_denominator(
        self, tmp_path: Path
    ) -> None:
        def failing_image():
            def handler(request):  # noqa: ANN001
                raise ProviderError.server("simulated outage")

            return FakeImageProvider(handler)

        cases, annos = _single_turn_case()
        runner = _runner_for(
            tmp_path / "fail", cases, annos,
            llm_factory=make_llm_factory(
                interpreter=[FULL_INTENT_RESPONSE], baseline=[BASELINE_PROMPT_TEXT]
            ),
            image_factory=failing_image,
            repetitions=1,
        )
        result = runner.run()

        # 两侧首个案例均 failed；失败轮携带 provider.* code；不从分母剔除。
        assert all(c.status == "failed" for c in result.cases)
        sb_turn = next(t for t in result.turns if t.system == "system_b")
        assert sb_turn.status == "failed"
        assert sb_turn.error is not None
        assert sb_turn.error.code == "provider.server_error"
        assert sb_turn.error.stage == "image"

        # 生成失败但编译已落库：本轮新编译的 PromptArtifact 仍计入 L3
        #（coverage 可评估；请求面合法 → model_compatibility ok）。
        sb_metrics = {m.metric: m for m in result.metrics if m.system == "system_b"}
        assert sb_metrics["intent_coverage"].status == "ok"
        assert sb_metrics["model_compatibility"].status == "ok"
        assert sb_metrics["model_compatibility"].score == 1.0
        assert sb_turn.prompt_text is not None  # 编译产物被捕获
        # 图像调用观察记录了失败的 Provider 调用。
        assert sb_turn.image_calls[0].status == "failed"

        summary = result.summary
        assert summary.case_status_counts["system_b"]["failed"] == 1
        # Baseline 侧：图像失败轮仍有 Prompt（coverage ok）、无 Artifact。
        ba_metrics = {m.metric: m for m in result.metrics if m.system == "baseline_a"}
        assert ba_metrics["intent_coverage"].status == "ok"

    def test_llm_provider_failure_surfaces_as_failed_turn(self, tmp_path: Path) -> None:
        def failing_llm():
            def handler(request):  # noqa: ANN001
                raise ProviderError.timeout("simulated timeout")

            from visual_intent_agent.providers.fake_llm import FakeLLMProvider

            return FakeLLMProvider(handler)

        cases, annos = _single_turn_case()
        runner = _runner_for(
            tmp_path / "llmfail", cases, annos,
            llm_factory=failing_llm, repetitions=1,
        )
        result = runner.run()
        # Baseline：LLM 失败 → 轮 failed。
        ba_turn = next(t for t in result.turns if t.system == "baseline_a")
        assert ba_turn.status == "failed"
        assert ba_turn.error is not None and ba_turn.error.code == "provider.timeout"
        # System B：Interpreter 的 Provider 失败经产品可恢复 issue 路径（两次尝试后仍
        # 失败 → 不确认、不生成；轮执行本身完成，结局偏离原样记录）。
        sb_turn = next(t for t in result.turns if t.system == "system_b")
        assert sb_turn.status == "completed"
        assert sb_turn.outcome_matches is False
        assert len(sb_turn.llm_calls) == 2  # 冻结的"最多修复一次"
        assert all(c.status == "failed" for c in sb_turn.llm_calls)
        assert sb_turn.llm_calls[0].error.code == "provider.timeout"

    def test_baseline_empty_llm_output_is_recorded_without_crashing(
        self, tmp_path: Path
    ) -> None:
        # Step 02 会把"Provider 成功但输出空白"判为 `baseline.empty_llm_output`
        #（`BaselineErrorStage = baseline`）。统一失败合同必须原样承接该 stage，
        # 否则 Baseline 的真实可达失败路径会在记录装配期崩溃（审计发现并修复）。
        class EmptyBaselineLLM(ScriptedLLM):
            def complete(self, request):  # noqa: ANN001
                from visual_intent_agent.providers.llm import LLMResponse

                system = request.messages[0].content if request.messages else ""
                if "图像 Prompt 生成器" in system:
                    return LLMResponse(content="   ", model="fake-llm")
                return super().complete(request)

        cases, annos = _single_turn_case()
        runner = _runner_for(
            tmp_path / "empty", cases, annos,
            llm_factory=lambda: EmptyBaselineLLM(
                interpreter_default=FULL_INTENT_RESPONSE,
                baseline_default="ignored",
            ),
            repetitions=1,
        )
        result = runner.run()
        ba_turn = next(t for t in result.turns if t.system == "baseline_a")
        assert ba_turn.status == "failed"
        assert ba_turn.error is not None
        assert ba_turn.error.code == "baseline.empty_llm_output"
        assert ba_turn.error.stage == "baseline"  # 原样透传，不做有损映射
        # 失败保留在分母：案例仍产生记录并进入摘要，未被静默剔除。
        assert any(c.system == "baseline_a" for c in result.cases)
        # System B 侧不受影响（同一案例的 Baseline 失败不污染 System B）。
        assert any(
            t.system == "system_b" and t.status == "completed" for t in result.turns
        )


# ---------------------------------------------------------------------------
# 冻结数据集全量冒烟（真实冻结输入 + 启发式 Fake；offline）
# ---------------------------------------------------------------------------


class TestFrozenDatasetSmoke:
    def test_full_dataset_runs_end_to_end_offline(self, tmp_path: Path) -> None:
        # 启发式 Fake（只追求机械完备，不追求好分数）：Interpreter 恒返回
        # 七路径全 SET；反馈按文本特征分派 accept / revise；Baseline 恒出同一 Prompt。
        accept_markers = ("就这样", "完成", "就这张", "可以了", "我要的", "好多了", "很好")

        class HeuristicLLM(ScriptedLLM):
            def complete(self, request):  # noqa: ANN001
                from visual_intent_agent.providers.llm import LLMResponse

                system = request.messages[0].content if request.messages else ""
                if "Feedback Interpreter" in system:
                    text = "\n".join(m.content for m in request.messages)
                    if any(marker in text for marker in accept_markers):
                        return LLMResponse(
                            content=feedback_response("accept"), model="fake-llm"
                        )
                    return LLMResponse(
                        content=feedback_response(
                            "revise",
                            candidate_deltas=[
                                revise_entry("lighting.character", "dramatic")
                            ],
                        ),
                        model="fake-llm",
                    )
                return super().complete(request)

        runner = EvaluationRunner(
            settings=make_settings(),
            llm_factory=lambda: HeuristicLLM(
                baseline_default=BASELINE_PROMPT_TEXT,
                interpreter_default=FULL_INTENT_RESPONSE,
            ),
            image_factory=lambda: FakeImageProvider(),
            output_root=tmp_path / "runs",
            repetitions=2,
            clock=fixed_clock(),
            monotonic=fixed_monotonic(),
        )
        result = runner.run()

        # 22 案例 × 2 系统 × 2 重复 = 88 条案例记录；全部轮次都有记录。
        assert result.run.case_count == 22
        assert len(result.cases) == 88
        total_turns = 0
        from evaluation.direct_baseline import load_dataset

        for case in load_dataset(DEFAULT_DATASET_PATH):
            total_turns += len(case.turns)
        assert len(result.turns) == total_turns * 2 * 2

        # 每案例每系统每重复：System B 13 条指标（4+5+4），Baseline 13 条
        #（L1/L2 not_applicable + L3 计算）。
        for case_record in result.cases:
            metrics = [
                m for m in result.metrics
                if m.case_id == case_record.case_id
                and m.system == case_record.system
                and m.repetition == case_record.repetition
            ]
            assert len(metrics) == len(L1_INVARIANTS) + len(L2_METRICS) + len(L3_METRICS)

        # 冻结校验路径生效（verify_frozen=True 默认）+ 落盘完整。
        assert (runner.run_dir / "run.json").is_file()
        case_jsonls = list((runner.run_dir / "cases").glob("*.jsonl"))
        assert len(case_jsonls) == 22
        # 摘要三态计数与聚合完整。
        assert isinstance(result.summary.gate_a_blocked, bool)
        assert result.summary.metrics


class TestFrozenManifestEnforcement:
    def test_modified_frozen_input_is_refused(self, tmp_path: Path) -> None:
        # 复制冻结物到 tmp 并篡改数据集 → verify_frozen 拒绝运行。
        import shutil

        for rel in (
            "evaluation/fixtures/core_v0_3.jsonl",
            "evaluation/annotations/core_v0_3.jsonl",
            "evaluation/configs/gate_a_v0_3.json",
            "evaluation/protocol.md",
        ):
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(PROJECT_ROOT / rel, target)
        dataset = tmp_path / "evaluation" / "fixtures" / "core_v0_3.jsonl"
        dataset.write_text(
            dataset.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )

        def build() -> EvaluationRunner:
            return EvaluationRunner(
                settings=make_settings(),
                llm_factory=make_llm_factory(),
                image_factory=lambda: FakeImageProvider(),
                output_root=tmp_path / "runs",
                config_path=tmp_path / "evaluation" / "configs" / "gate_a_v0_3.json",
                dataset_path=dataset,
                annotations_path=tmp_path / "evaluation" / "annotations" / "core_v0_3.jsonl",
                protocol_path=tmp_path / "evaluation" / "protocol.md",
                verify_frozen=True,
            )

        # Runner 的冻结校验发生在**构造期**（fail fast：`_compute_hashes` 在
        # `__init__` 执行，见 runner.py `verify_frozen` 文档）。因此把构造与运行
        # 一并放进 raises：无论校验落在构造期还是 run() 期，"篡改冻结输入必被拒绝、
        # 绝不产生任何评测记录"的语义都成立，不弱化。
        with pytest.raises(ValueError, match="frozen file hash mismatch"):
            build().run()
        assert not (tmp_path / "runs").exists(), "被拒的运行不得落盘任何产物"


# ---------------------------------------------------------------------------
# CLI 与离线证明
# ---------------------------------------------------------------------------


class TestCli:
    def test_list_cases_needs_no_credentials(self, capsys) -> None:  # noqa: ANN001
        exit_code = main(["--list-cases"])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "s01-complete-001" in out
        assert len([line for line in out.splitlines() if line.strip()]) == 22

    def test_missing_credentials_exit_with_code_2(
        self, monkeypatch: pytest.MonkeyPatch, capsys  # noqa: ANN001
    ) -> None:
        from visual_intent_agent import config as config_module

        def _raise(env=None, env_file=config_module.DEFAULT_ENV_FILE):  # noqa: ANN001
            raise config_module.ConfigurationError(
                "invalid configuration; missing required env vars: VIA_PROVIDER_BASE_URL"
            )

        monkeypatch.setattr(config_module, "load_settings", _raise)
        assert main(["--output-root", "unused"]) == 2
        assert "配置错误" in capsys.readouterr().err

    def test_parser_defaults(self) -> None:
        args = build_arg_parser().parse_args([])
        assert args.repetitions is None  # 默认读冻结配置
        assert args.nonce == ""
        assert args.list_cases is False


def _forbidden_top_level_imports(
    tree: ast.Module, forbidden: tuple[str, ...]
) -> list[str]:
    """返回模块**顶层**（`tree.body`）违规 import 的名字。

    只扫顶层是刻意的：真实 adapter 只在测试函数内部**延迟 import**（不触网、
    也为断言 Fake/真实同接口），函数体内的 import 不是离线违规。模块顶层的
    真实 adapter / 网络库 import 必须被捕获（反例见测试内断言）。
    """
    offenders: list[str] = []
    for node in tree.body:  # 只扫模块顶层（函数/类体内的延迟 import 放行）
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if any(fragment in module for fragment in forbidden):
                offenders.append(module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if any(fragment in alias.name for fragment in forbidden):
                    offenders.append(alias.name)
    return offenders


class TestOfflineProof:
    def test_real_adapters_are_only_lazy_imports_in_runner(self) -> None:
        source = (PROJECT_ROOT / "evaluation" / "runner.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:  # 只扫模块顶层
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                text = ast.dump(node)
                assert "openai" not in text and "httpx" not in text, (
                    "真实 adapter 只允许在函数内延迟 import（默认导入不触网）"
                )

    def test_tests_directory_has_no_real_adapter_or_network_imports(self) -> None:
        tests_dir = Path(__file__).resolve().parent
        forbidden = ("openai_llm", "openai_image", "httpx", "socket", "requests")
        for path in tests_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders = _forbidden_top_level_imports(tree, forbidden)
            assert not offenders, f"{path.name} has top-level forbidden imports: {offenders}"

        # 反例：扫描器必须能捕获模块顶层违规（证明规则不是空转）。
        counterexample = ast.parse(
            "import httpx\n"
            "import socket\n"
            "from visual_intent_agent.providers.openai_image import OpenAIImageProvider\n"
        )
        assert _forbidden_top_level_imports(counterexample, forbidden) == [
            "httpx",
            "socket",
            "visual_intent_agent.providers.openai_image",
        ]
        # 函数体内的延迟 import 合法（`test_direct_baseline.py` 的真实/Fake 同接口
        # 验证正走这条路径；离线意图不因此弱化——顶层仍必须为零违规）。
        deferred = ast.parse("def check() -> None:\n    import httpx\n")
        assert _forbidden_top_level_imports(deferred, forbidden) == []
