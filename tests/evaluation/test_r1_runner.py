"""R1-B：r1 Runner 口径的离线集成测试（Fake Provider，零网络）。

覆盖：

- `--manifest` 解析协议/配置/数据集/标注 + 全量哈希校验；
- 同一冻结用户消息：r1 数据集出现 `answer_variants` 即拒绝启动；
- 级联失败：首次根因记 `failed`，其后脚本轮记 `blocked` 并关联来源，
  不重复记独立根因、不自动改送反馈入口；
- 运行身份：正式 Run 拒绝 dirty 工作区，诊断 Run 显式标记；
- 指标版本标识与 r1 `generation_completion`；旧 v1 口径默认不变。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from visual_intent_agent.providers.fake_image import FakeImageProvider

from evaluation.identity import (
    IDENTITY_DIAGNOSTIC,
    IDENTITY_FORMAL,
    CodeIdentity,
    IdentityError,
)
from evaluation.reporting import (
    EVALUATION_HARNESS_VERSION,
    METRIC_VERSION_R1,
    METRIC_VERSION_V1,
)
from evaluation.runner import (
    PROTOCOL_VERSION_R1,
    EvaluationRunner,
    FormalRunBlockedError,
    main,
)

from evaluation_harness_helpers import (
    BASELINE_PROMPT_TEXT,
    FULL_INTENT_RESPONSE,
    feedback_response,
    fixed_clock,
    fixed_monotonic,
    make_annotation,
    make_fixture_case,
    make_llm_factory,
    make_settings,
    make_turn,
    make_turn_annotation,
    revise_entry,
    write_config,
    write_jsonl,
)


# ---------------------------------------------------------------------------
# 合成输入
# ---------------------------------------------------------------------------


def _write_inputs(
    root: Path,
    cases: list[dict],
    annotations: list[dict],
    *,
    repetitions: int = 1,
) -> tuple[Path, Path, Path, Path]:
    dataset = write_jsonl(root / "inputs" / "dataset.jsonl", cases)
    annos = write_jsonl(root / "inputs" / "annotations.jsonl", annotations)
    config = write_config(root / "inputs" / "config.json", repetitions=repetitions)
    protocol = root / "inputs" / "protocol.md"
    protocol.write_text("# synthetic r1 protocol\n", encoding="utf-8")
    return dataset, annos, config, protocol


def _r1_runner(
    root: Path,
    cases: list[dict],
    annotations: list[dict],
    *,
    llm_factory,
    repetitions: int = 1,
    identity: CodeIdentity | None = None,
    gate_thresholds_status: str | None = None,
) -> EvaluationRunner:
    dataset, annos, config, protocol = _write_inputs(
        root, cases, annotations, repetitions=repetitions
    )
    if gate_thresholds_status is not None:
        payload = json.loads(config.read_text(encoding="utf-8"))
        payload.setdefault("metrics", {})["gate_thresholds"] = {
            "status": gate_thresholds_status
        }
        config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return EvaluationRunner(
        settings=make_settings(),
        llm_factory=llm_factory,
        image_factory=lambda: FakeImageProvider(),
        output_root=root / "runs",
        config_path=config,
        dataset_path=dataset,
        annotations_path=annos,
        protocol_path=protocol,
        repetitions=repetitions,
        verify_frozen=False,
        protocol_version=PROTOCOL_VERSION_R1,
        metric_version=METRIC_VERSION_R1,
        identity=identity,
        clock=fixed_clock(),
        monotonic=fixed_monotonic(),
    )


def _manifest_r1_runner(
    root: Path,
    *,
    identity: CodeIdentity | None,
    gate_thresholds_status: str | None = None,
    llm_factory=None,
) -> EvaluationRunner:
    """清单驱动 + `verify_frozen=True` 的 r1 Runner（正式模式可用）。"""
    cases, annotations = _single_turn_case()
    dataset, annos, config, protocol = _write_inputs(root, cases, annotations)
    if gate_thresholds_status is not None:
        payload = json.loads(config.read_text(encoding="utf-8"))
        payload.setdefault("metrics", {})["gate_thresholds"] = {
            "status": gate_thresholds_status
        }
        config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    manifest_path = _write_synthetic_manifest(root, dataset, annos, config, protocol)
    return EvaluationRunner(
        settings=make_settings(),
        llm_factory=llm_factory
        or make_llm_factory(
            baseline=[BASELINE_PROMPT_TEXT], interpreter=[FULL_INTENT_RESPONSE]
        ),
        image_factory=lambda: FakeImageProvider(),
        output_root=root / "runs",
        manifest_path=manifest_path,
        resolve_from_manifest=True,
        verify_frozen=True,
        repetitions=1,
        identity=identity,
        clock=fixed_clock(),
        monotonic=fixed_monotonic(),
    )


def _single_turn_case() -> tuple[list[dict], list[dict]]:
    case = make_fixture_case(
        "sx-r1-single-001",
        [make_turn("t1", "user_message", "一只橘猫蜷在客厅沙发上，写实，中景，暖光")],
    )
    anno = make_annotation(
        "sx-r1-single-001",
        [
            make_turn_annotation(
                "t1",
                expected_deltas=[
                    {
                        "operation": "SET",
                        "path": "subject.description",
                        "value_match": {"mode": "contains_any", "keywords": ["橘", "猫"]},
                        "resolution": None,
                    }
                ],
                expected_outcome="ready_and_generate",
            )
        ],
    )
    return [case], [anno]


def _blocked_cascade_case() -> tuple[list[dict], list[dict]]:
    """t1 期望对图片反馈但此刻无生成 → 首次根因；t2/t3 无法执行 → blocked。"""
    case = make_fixture_case(
        "sx-r1-cascade-001",
        [
            make_turn("t1", "image_feedback", "把光线改成戏剧光"),
            make_turn("t2", "user_message", "那换成暖光吧"),
            make_turn("t3", "accept", "可以了"),
        ],
        scenario="single_field_modification",
        turn_type="multi",
    )
    anno = make_annotation(
        "sx-r1-cascade-001",
        [
            make_turn_annotation(
                "t1",
                expected_outcome="ready_and_generate",
                expected_feedback_decision="revise",
                forbidden_change_paths=["subject.description"],
            ),
            make_turn_annotation("t2", expected_outcome="ready_and_generate"),
            make_turn_annotation("t3", expected_outcome="accepted_completed"),
        ],
        scenario="single_field_modification",
    )
    return [case], [anno]


def _preservation_baseline_case() -> tuple[list[dict], list[dict]]:
    """F1 Runner 集成：真实标注驱动初始 SET + 保留标签，复现基准污染缺陷。

    t1 首次生成同时 SET 主体/风格（即初始 changed_paths）；t2 只合法改光，标注
    要求保留主体与风格，但实际生成把 cat 换成 dog。修复前 t1 的初始路径一直留在
    pending，t2 的保留组被全部排除 → preservation `not_applicable`；修复后应得到
    一个比较对、保留一组、丢失一组、得分 0.5。
    """
    case = make_fixture_case(
        "sx-r1-preserve-001",
        [
            make_turn("t1", "user_message", "一只橘猫，水彩风格，中景"),
            make_turn("t2", "image_feedback", "只把光线改成暖光"),
            make_turn("t3", "accept", "可以了"),
        ],
        scenario="single_field_modification",
        turn_type="multi",
    )
    anno = make_annotation(
        "sx-r1-preserve-001",
        [
            make_turn_annotation(
                "t1",
                expected_deltas=[
                    {
                        "operation": "SET",
                        "path": "subject.description",
                        "value_match": {"mode": "contains_any", "keywords": ["猫", "cat"]},
                        "resolution": None,
                    },
                    {
                        "operation": "SET",
                        "path": "style.primary",
                        "value_match": {
                            "mode": "contains_any",
                            "keywords": ["水彩", "watercolor"],
                        },
                        "resolution": None,
                    },
                ],
                expected_outcome="ready_and_generate",
            ),
            make_turn_annotation(
                "t2",
                expected_deltas=[
                    {
                        "operation": "SET",
                        "path": "lighting.character",
                        "value_match": {"mode": "contains_any", "keywords": ["暖", "warm"]},
                        "resolution": None,
                    }
                ],
                expected_outcome="ready_and_generate",
                expected_feedback_decision="revise",
                forbidden_change_paths=["subject.description", "style.primary"],
                preserve_path_groups={
                    "subject.description": [["猫", "cat"]],
                    "style.primary": [["水彩", "watercolor"]],
                },
            ),
            make_turn_annotation("t3", expected_outcome="accepted_completed"),
        ],
        scenario="single_field_modification",
    )
    return [case], [anno]


def _success_failure_blocked_case() -> tuple[list[dict], list[dict]]:
    """F2：三轮预定生成——成功、失败、blocked，完成率必须是 1/3 而非 1/2。

    t1 正常生成；t2 预定生成但此刻会话已在 WAITING_REVIEW（新消息被工作流拒绝）
    → 首次独立根因；t3 无法执行 → blocked。
    """
    case = make_fixture_case(
        "sx-r1-completion-001",
        [
            make_turn("t1", "user_message", "一只橘猫蜷在沙发上，写实，中景，暖光"),
            make_turn("t2", "user_message", "再把光线改成戏剧光"),
            make_turn("t3", "user_message", "再换成水彩风格"),
        ],
        scenario="single_field_modification",
        turn_type="multi",
    )
    anno = make_annotation(
        "sx-r1-completion-001",
        [
            make_turn_annotation("t1", expected_outcome="ready_and_generate"),
            make_turn_annotation("t2", expected_outcome="ready_and_generate"),
            make_turn_annotation("t3", expected_outcome="ready_and_generate"),
        ],
        scenario="single_field_modification",
    )
    return [case], [anno]


def _clarification_failure_then_blocked_case() -> tuple[list[dict], list[dict]]:
    """F2：首轮预定澄清但引擎失败；后续预定生成轮 blocked。

    分母只剩 t2（ready_and_generate）→ 完成率 0；不得因为没有未阻断的预定生成轮
    而报 `not_applicable`（那样会让级联阻断整段消失）。
    """
    case = make_fixture_case(
        "sx-r1-completion-002",
        [
            make_turn("t1", "user_message", "帮我随便画一张"),
            make_turn("t2", "user_message", "一只橘猫蜷在沙发上，写实，中景，暖光"),
        ],
        scenario="missing_core_decision",
        turn_type="multi",
    )
    anno = make_annotation(
        "sx-r1-completion-002",
        [
            make_turn_annotation("t1", expected_outcome="clarification_expected"),
            make_turn_annotation("t2", expected_outcome="ready_and_generate"),
        ],
        scenario="missing_core_decision",
    )
    return [case], [anno]


# ---------------------------------------------------------------------------
# 指标版本与 r1 指标
# ---------------------------------------------------------------------------


class TestR1RunRecording:
    def test_metric_version_recorded_on_run_and_records(self, tmp_path: Path) -> None:
        cases, annos = _single_turn_case()
        runner = _r1_runner(
            tmp_path / "run",
            cases,
            annos,
            llm_factory=make_llm_factory(
                baseline=[BASELINE_PROMPT_TEXT],
                interpreter=[FULL_INTENT_RESPONSE],
            ),
        )
        result = runner.run()
        assert result.run.metric_version == METRIC_VERSION_R1
        assert result.run.protocol_version == PROTOCOL_VERSION_R1
        assert result.metrics
        assert all(record.metric_version == METRIC_VERSION_R1 for record in result.metrics)
        l3_names = {record.metric for record in result.metrics if record.layer == "L3"}
        assert "generation_completion" in l3_names

    def test_legacy_default_keeps_v1_metrics(self, tmp_path: Path) -> None:
        """不传 protocol/metric version 时保持旧 v1 口径与四个 L3 指标。"""
        cases, annos = _single_turn_case()
        dataset, annos_path, config, protocol = _write_inputs(tmp_path / "legacy", cases, annos)
        runner = EvaluationRunner(
            settings=make_settings(),
            llm_factory=make_llm_factory(
                baseline=[BASELINE_PROMPT_TEXT], interpreter=[FULL_INTENT_RESPONSE]
            ),
            image_factory=lambda: FakeImageProvider(),
            output_root=tmp_path / "legacy" / "runs",
            config_path=config,
            dataset_path=dataset,
            annotations_path=annos_path,
            protocol_path=protocol,
            repetitions=1,
            verify_frozen=False,
            clock=fixed_clock(),
            monotonic=fixed_monotonic(),
        )
        result = runner.run()
        assert result.run.metric_version == METRIC_VERSION_V1
        l3_names = {record.metric for record in result.metrics if record.layer == "L3"}
        assert "generation_completion" not in l3_names

    def test_harness_version_marks_post_v0_3_scoring_fix(self, tmp_path: Path) -> None:
        """F1/F2 改变评分实现：Run 必须记录 harness v2，使修复前后 Run 可区分。

        指标口径版本仍是 r1（`v0_3_r1`），此处只升级框架实现版本；默认
        `code_version` 与 Run ID 均由该常量派生，故修复前后不共享同一身份。
        """
        assert EVALUATION_HARNESS_VERSION == "evaluation_harness_v2"
        cases, annos = _single_turn_case()
        runner = _r1_runner(
            tmp_path / "harness-version",
            cases,
            annos,
            llm_factory=make_llm_factory(
                baseline=[BASELINE_PROMPT_TEXT],
                interpreter=[FULL_INTENT_RESPONSE],
            ),
        )
        result = runner.run()
        assert result.run.harness_version == EVALUATION_HARNESS_VERSION
        assert result.run.code_version.endswith(f"+{EVALUATION_HARNESS_VERSION}")
        assert result.run.metric_version == METRIC_VERSION_R1


class TestPreservationBaselineIntegration:
    """F1：用 Runner 真实标注（含首轮初始 SET）端到端验证 Preservation 基准。"""

    def test_initial_set_does_not_pollute_next_comparison(self, tmp_path: Path) -> None:
        cases, annos = _preservation_baseline_case()
        runner = _r1_runner(
            tmp_path / "preserve",
            cases,
            annos,
            llm_factory=make_llm_factory(
                baseline=[
                    "写实摄影：一只橘色虎斑猫，水彩，中景 medium shot",
                    "写实摄影：一只狗，水彩，中景 medium shot，暖光 warm",
                ],
                interpreter=[FULL_INTENT_RESPONSE],
                feedback=[
                    feedback_response(
                        "revise",
                        candidate_deltas=[revise_entry("lighting.character", "warm")],
                    )
                ],
            ),
        )
        result = runner.run()
        baseline_metrics = {
            m.metric: m
            for m in result.metrics
            if m.system == "baseline_a" and m.repetition == 1
        }
        preservation = baseline_metrics["preservation"]
        # 首轮初始 SET（subject/style）不得被当作 t2 的“合法修改”排除。
        assert preservation.status == "ok"
        assert preservation.counts["pairs"] == 1
        assert preservation.score == 0.5
        assert preservation.counts["groups_kept"] == 1
        assert preservation.counts["groups_lost"] == 1
        pair = preservation.details["pair_details"][0]
        assert pair["previous_turn_id"] == "t1"
        assert pair["turn_id"] == "t2"
        assert pair["excluded_changed_paths"] == ["lighting.character"]
        # 终局 accept 轮无 Prompt：只计数 not_applicable，不伪造保留 0。
        assert preservation.counts["no_prompt_not_applicable"] == 1


# ---------------------------------------------------------------------------
# 同一冻结用户消息
# ---------------------------------------------------------------------------


class TestSameFrozenUserMessage:
    def test_r1_rejects_answer_variants(self, tmp_path: Path) -> None:
        case = make_fixture_case(
            "sx-r1-variant-001",
            [
                make_turn("t1", "user_message", "一只橘猫在客厅"),
                make_turn(
                    "t2",
                    "clarification_answer",
                    "就用电影感吧",
                    answer_variants={"style.primary": "那用水彩吧"},
                ),
            ],
            scenario="missing_core_decision",
            turn_type="multi",
        )
        anno = make_annotation(
            "sx-r1-variant-001",
            [
                make_turn_annotation("t1", expected_outcome="clarification_expected"),
                make_turn_annotation("t2", expected_outcome="ready_and_generate"),
            ],
            scenario="missing_core_decision",
        )
        runner = _r1_runner(
            tmp_path / "variant",
            [case],
            [anno],
            llm_factory=make_llm_factory(
                baseline=[BASELINE_PROMPT_TEXT], interpreter=[FULL_INTENT_RESPONSE]
            ),
        )
        with pytest.raises(ValueError, match="answer_variants is not allowed"):
            runner.run()


# ---------------------------------------------------------------------------
# 级联失败：首次根因与 blocked 分开
# ---------------------------------------------------------------------------


class TestBlockedCascade:
    def test_first_root_cause_then_blocked_turns(self, tmp_path: Path) -> None:
        cases, annos = _blocked_cascade_case()
        runner = _r1_runner(
            tmp_path / "cascade",
            cases,
            annos,
            llm_factory=make_llm_factory(
                baseline=[BASELINE_PROMPT_TEXT] * 3,
                interpreter=[FULL_INTENT_RESPONSE],
                feedback=[feedback_response("revise")],
            ),
        )
        result = runner.run()
        sb_turns = sorted(
            (t for t in result.turns if t.system == "system_b"),
            key=lambda t: t.turn_index,
        )
        assert [t.status for t in sb_turns] == ["failed", "blocked", "blocked"]
        root = sb_turns[0]
        assert root.error is not None
        assert root.error.code == "evaluation.no_generation_under_review"
        for blocked in sb_turns[1:]:
            assert blocked.blocked_by_prior_failure is True
            assert blocked.blocked_by_turn_id == "t1"
            assert blocked.error is None
            assert blocked.system_b is None
        # 只有一个独立根因；案例仍进入失败分母。
        case_record = next(c for c in result.cases if c.system == "system_b")
        assert case_record.status == "failed"
        assert sum(1 for t in result.turns if t.status == "failed" and t.system == "system_b") == 1


# ---------------------------------------------------------------------------
# F2：级联阻断轮仍进完成率分母
# ---------------------------------------------------------------------------


class TestCascadeCompletionDenominator:
    """F2：`ready_and_generate` 轮无论是否 blocked 都进分母；根因与级联分开统计。"""

    @staticmethod
    def _completion(result, system: str):
        return next(
            m
            for m in result.metrics
            if m.system == system
            and m.metric == "generation_completion"
            and m.repetition == 1
        )

    def test_two_expected_turns_first_failed_second_blocked(self, tmp_path: Path) -> None:
        cases, annos = _blocked_cascade_case()
        runner = _r1_runner(
            tmp_path / "cascade-denominator",
            cases,
            annos,
            llm_factory=make_llm_factory(
                baseline=[BASELINE_PROMPT_TEXT] * 3,
                interpreter=[FULL_INTENT_RESPONSE],
                feedback=[feedback_response("revise")],
            ),
        )
        result = runner.run()
        completion = self._completion(result, "system_b")
        # 级联阻断不缩减分母：t1、t2 都是预定生成目标，均未产出 Prompt。
        assert completion.status == "ok"
        assert completion.counts == {
            "expected_prompt_turns": 2,
            "produced": 0,
            "failed": 2,
        }
        assert completion.score == 0.0
        assert completion.details["failed_turn_ids"] == ["t1", "t2"]

        sb_turns = sorted(
            (t for t in result.turns if t.system == "system_b"),
            key=lambda t: t.turn_index,
        )
        root_causes = [t for t in sb_turns if t.status == "failed"]
        cascade = [t for t in sb_turns if t.status == "blocked"]
        # 一个独立故障根因；级联阻断不伪造异常、不执行调用。
        assert [t.turn_id for t in root_causes] == ["t1"]
        assert root_causes[0].error is not None
        assert {t.blocked_by_turn_id for t in cascade} == {"t1"}
        for turn in cascade:
            assert turn.blocked_by_prior_failure is True
            assert turn.error is None
            assert turn.input_source == "not_executed"
            assert turn.llm_calls == []
            assert turn.image_calls == []
        # 逐轮记录可把完成率里的未完成轮拆成根因失败与级联阻断，无需新计数。
        # t3 是 blocked 的 accept 轮：不进分母，也不出现在 failed_turn_ids。
        blocked_expected = [
            t for t in cascade if t.expected_outcome == "ready_and_generate"
        ]
        blocked_not_expected = [
            t for t in cascade if t.expected_outcome != "ready_and_generate"
        ]
        assert [t.turn_id for t in blocked_not_expected] == ["t3"]
        assert completion.details["failed_turn_ids"] == [
            *[t.turn_id for t in root_causes],
            *[t.turn_id for t in blocked_expected],
        ]

    def test_three_expected_turns_success_failure_blocked_is_one_third(
        self, tmp_path: Path
    ) -> None:
        cases, annos = _success_failure_blocked_case()
        runner = _r1_runner(
            tmp_path / "completion-third",
            cases,
            annos,
            llm_factory=make_llm_factory(
                baseline=[BASELINE_PROMPT_TEXT],
                interpreter=[FULL_INTENT_RESPONSE],
            ),
        )
        result = runner.run()
        completion = self._completion(result, "system_b")
        assert completion.status == "ok"
        assert completion.counts == {
            "expected_prompt_turns": 3,
            "produced": 1,
            "failed": 2,
        }
        assert completion.score == pytest.approx(1 / 3)
        assert completion.details["failed_turn_ids"] == ["t2", "t3"]

        sb_turns = sorted(
            (t for t in result.turns if t.system == "system_b"),
            key=lambda t: t.turn_index,
        )
        assert [t.status for t in sb_turns] == ["completed", "failed", "blocked"]
        assert [t.prompt_text is not None for t in sb_turns] == [True, False, False]
        assert sum(1 for t in sb_turns if t.status == "failed") == 1

    def test_clarification_failure_then_blocked_is_zero_not_not_applicable(
        self, tmp_path: Path
    ) -> None:
        cases, annos = _clarification_failure_then_blocked_case()
        runner = _r1_runner(
            tmp_path / "clarify-then-blocked",
            cases,
            annos,
            # 引擎脚本为空：t1 预定澄清却可恢复失败 → 首次独立根因。
            llm_factory=make_llm_factory(),
        )
        result = runner.run()
        completion = self._completion(result, "system_b")
        # 只有 t2 是预定生成轮（blocked）；分母不为空，完成率是 0 而非 not_applicable。
        assert completion.status == "ok"
        assert completion.counts == {
            "expected_prompt_turns": 1,
            "produced": 0,
            "failed": 1,
        }
        assert completion.score == 0.0
        assert completion.details["failed_turn_ids"] == ["t2"]

        sb_turns = sorted(
            (t for t in result.turns if t.system == "system_b"),
            key=lambda t: t.turn_index,
        )
        assert [t.status for t in sb_turns] == ["failed", "blocked"]
        # t1 是预定澄清轮：即使失败也不冒充“应生成而失败”。
        assert sb_turns[0].expected_outcome == "clarification_expected"
        assert sb_turns[1].blocked_by_turn_id == "t1"

    def test_clarification_and_accept_excluded_and_ab_share_denominator(
        self, tmp_path: Path
    ) -> None:
        """A/B 同口径：只有 ready_and_generate 进分母，澄清/接受均排除。"""
        cases, annos = _preservation_baseline_case()
        runner = _r1_runner(
            tmp_path / "ab-denominator",
            cases,
            annos,
            llm_factory=make_llm_factory(
                baseline=[
                    "写实摄影：一只橘色虎斑猫，水彩，中景 medium shot",
                    "写实摄影：一只狗，水彩，中景 medium shot，暖光 warm",
                ],
                interpreter=[FULL_INTENT_RESPONSE],
                feedback=[
                    feedback_response(
                        "revise",
                        candidate_deltas=[revise_entry("lighting.character", "warm")],
                    )
                ],
            ),
        )
        result = runner.run()
        baseline_a = self._completion(result, "baseline_a")
        system_b = self._completion(result, "system_b")
        # t1/t2 是 ready_and_generate；t3 是 accept（accepted_completed）不进分母。
        for completion in (baseline_a, system_b):
            assert completion.status == "ok"
            assert completion.counts["expected_prompt_turns"] == 2
            assert completion.counts["produced"] == 2
            assert completion.counts["failed"] == 0
            assert completion.details["failed_turn_ids"] == []
            assert completion.score == 1.0

    def test_legacy_v1_run_unchanged_for_blocked_cascade(self, tmp_path: Path) -> None:
        """旧 v1 口径不新增完成率，也不改写 blocked 记录（F2 只动 r1 派生）。"""
        cases, annos = _blocked_cascade_case()
        dataset, annos_path, config, protocol = _write_inputs(
            tmp_path / "legacy-cascade", cases, annos
        )
        runner = EvaluationRunner(
            settings=make_settings(),
            llm_factory=make_llm_factory(
                baseline=[BASELINE_PROMPT_TEXT] * 3,
                interpreter=[FULL_INTENT_RESPONSE],
                feedback=[feedback_response("revise")],
            ),
            image_factory=lambda: FakeImageProvider(),
            output_root=tmp_path / "legacy-cascade" / "runs",
            config_path=config,
            dataset_path=dataset,
            annotations_path=annos_path,
            protocol_path=protocol,
            repetitions=1,
            verify_frozen=False,
            clock=fixed_clock(),
            monotonic=fixed_monotonic(),
        )
        result = runner.run()
        assert result.run.metric_version == METRIC_VERSION_V1
        l3_names = {record.metric for record in result.metrics if record.layer == "L3"}
        assert "generation_completion" not in l3_names
        sb_turns = sorted(
            (t for t in result.turns if t.system == "system_b"),
            key=lambda t: t.turn_index,
        )
        assert [t.status for t in sb_turns] == ["failed", "blocked", "blocked"]


# ---------------------------------------------------------------------------
# 运行身份
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_formal_construction_rejects_verify_frozen_false(self, tmp_path: Path) -> None:
        """正式模式构造时即拒绝 verify_frozen=False：正式哈希校验不可关闭。"""
        clean = CodeIdentity(
            commit="e" * 40,
            dirty=False,
            code_version="pkg+harness+eeee",
            detail="test",
            mode=IDENTITY_FORMAL,
        )
        cases, annos = _single_turn_case()
        with pytest.raises(FormalRunBlockedError, match="verify_frozen=False"):
            _r1_runner(
                tmp_path / "no-verify",
                cases,
                annos,
                llm_factory=make_llm_factory(baseline=[BASELINE_PROMPT_TEXT]),
                identity=clean,
            )

    def test_formal_run_refuses_dirty_worktree(self, tmp_path: Path) -> None:
        dirty = CodeIdentity(
            commit="a" * 40,
            dirty=True,
            code_version="pkg+harness+aaaa-dirty",
            detail="test",
            mode=IDENTITY_FORMAL,
        )
        runner = _manifest_r1_runner(tmp_path / "dirty", identity=dirty)
        with pytest.raises(IdentityError, match="dirty worktree"):
            runner.run()

    def test_diagnostic_run_records_dirty_and_mode(self, tmp_path: Path) -> None:
        cases, annos = _single_turn_case()
        diagnostic = CodeIdentity(
            commit="b" * 40,
            dirty=True,
            code_version="pkg+harness+bbbb-dirty",
            detail="test",
            mode=IDENTITY_DIAGNOSTIC,
        )
        runner = _r1_runner(
            tmp_path / "diag",
            cases,
            annos,
            llm_factory=make_llm_factory(
                baseline=[BASELINE_PROMPT_TEXT], interpreter=[FULL_INTENT_RESPONSE]
            ),
            identity=diagnostic,
        )
        result = runner.run()
        assert result.run.identity_mode == IDENTITY_DIAGNOSTIC
        assert result.run.code_dirty is True
        assert result.run.code_commit == "b" * 40


class TestFormalThresholdGate:
    """§7：判定阈值未冻结时，正式 Run 必须在驱动任何案例前拒绝（代码级门禁）。"""

    def _clean_identity(self) -> CodeIdentity:
        return CodeIdentity(
            commit="c" * 40,
            dirty=False,
            code_version="pkg+harness+cccc",
            detail="test",
            mode=IDENTITY_FORMAL,
        )

    def test_formal_run_blocked_when_thresholds_not_frozen(self, tmp_path: Path) -> None:
        runner = _manifest_r1_runner(
            tmp_path / "gate",
            identity=self._clean_identity(),
            gate_thresholds_status="not_frozen_in_r1",
        )
        with pytest.raises(FormalRunBlockedError, match="thresholds are not frozen"):
            runner.run()

    def test_formal_run_allowed_after_thresholds_frozen(self, tmp_path: Path) -> None:
        runner = _manifest_r1_runner(
            tmp_path / "gate-ok",
            identity=self._clean_identity(),
            gate_thresholds_status="frozen",
        )
        result = runner.run()
        assert result.run.identity_mode == IDENTITY_FORMAL
        assert result.run.code_dirty is False

    def test_diagnostic_run_allowed_when_thresholds_not_frozen(self, tmp_path: Path) -> None:
        cases, annos = _single_turn_case()
        runner = _r1_runner(
            tmp_path / "gate-diag",
            cases,
            annos,
            llm_factory=make_llm_factory(
                baseline=[BASELINE_PROMPT_TEXT], interpreter=[FULL_INTENT_RESPONSE]
            ),
            identity=CodeIdentity(
                commit="d" * 40,
                dirty=False,
                code_version="pkg+harness+dddd",
                detail="test",
                mode=IDENTITY_DIAGNOSTIC,
            ),
            gate_thresholds_status="not_frozen_in_r1",
        )
        result = runner.run()
        assert result.run.identity_mode == IDENTITY_DIAGNOSTIC


# ---------------------------------------------------------------------------
# --manifest 驱动
# ---------------------------------------------------------------------------


def _write_synthetic_manifest(root: Path, dataset: Path, annos: Path, config: Path, protocol: Path) -> Path:
    roles = {
        "protocol": str(protocol),
        "config": str(config),
        "dataset": str(dataset),
        "annotations": str(annos),
    }
    files = {role_path: hashlib.sha256(Path(role_path).read_bytes()).hexdigest() for role_path in roles.values()}
    manifest = {
        "manifest_version": "synthetic_manifest_r1",
        "dataset_version": "synthetic",
        "protocol_version": PROTOCOL_VERSION_R1,
        "config_version": "synthetic_config_v1",
        "algorithm": "sha256",
        "roles": roles,
        "files": files,
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class TestManifestDrivenRunner:
    def test_manifest_resolves_inputs_and_verifies(self, tmp_path: Path) -> None:
        cases, annos = _single_turn_case()
        dataset, annos_path, config, protocol = _write_inputs(tmp_path / "m", cases, annos)
        manifest_path = _write_synthetic_manifest(
            tmp_path / "m", dataset, annos_path, config, protocol
        )
        runner = EvaluationRunner(
            settings=make_settings(),
            llm_factory=make_llm_factory(
                baseline=[BASELINE_PROMPT_TEXT], interpreter=[FULL_INTENT_RESPONSE]
            ),
            image_factory=lambda: FakeImageProvider(),
            output_root=tmp_path / "m" / "runs",
            manifest_path=manifest_path,
            resolve_from_manifest=True,
            verify_frozen=True,
            repetitions=1,
            clock=fixed_clock(),
            monotonic=fixed_monotonic(),
        )
        assert runner.protocol_version == PROTOCOL_VERSION_R1
        result = runner.run()
        assert result.run.manifest_version == "synthetic_manifest_r1"
        assert result.run.dataset_sha256 == hashlib.sha256(dataset.read_bytes()).hexdigest()

    def test_manifest_hash_mismatch_refuses_to_run(self, tmp_path: Path) -> None:
        cases, annos = _single_turn_case()
        dataset, annos_path, config, protocol = _write_inputs(tmp_path / "bad", cases, annos)
        manifest_path = _write_synthetic_manifest(
            tmp_path / "bad", dataset, annos_path, config, protocol
        )
        dataset.write_text(dataset.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(Exception, match="verification failed"):
            EvaluationRunner(
                settings=make_settings(),
                llm_factory=make_llm_factory(baseline=[BASELINE_PROMPT_TEXT]),
                image_factory=lambda: FakeImageProvider(),
                output_root=tmp_path / "bad" / "runs",
                manifest_path=manifest_path,
                resolve_from_manifest=True,
                verify_frozen=True,
                repetitions=1,
            )

    def test_cli_list_cases_via_manifest(self, tmp_path: Path, capsys) -> None:
        cases, annos = _single_turn_case()
        dataset, annos_path, config, protocol = _write_inputs(tmp_path / "cli", cases, annos)
        manifest_path = _write_synthetic_manifest(
            tmp_path / "cli", dataset, annos_path, config, protocol
        )
        exit_code = main(["--list-cases", "--manifest", str(manifest_path)])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "sx-r1-single-001" in out


class TestCliFlags:
    def test_diagnostic_flag_parses(self) -> None:
        from evaluation.runner import build_arg_parser

        args = build_arg_parser().parse_args(["--diagnostic"])
        assert args.diagnostic is True
        assert args.manifest is None
