"""MVP v0.3 Step 03：L2 Intent Understanding 指标的离线单元测试。

五指标逐项覆盖 匹配 / 不匹配 / not_applicable（缺失观察）/ missing_data 分支；
value_match 五模式逐模式验证。全部纯函数、全离线。
"""

from __future__ import annotations

import pytest

from evaluation.metrics.intent import (
    TurnObservationInput,
    delta_matches,
    evaluate_clarification_precision,
    evaluate_conflict_detection,
    evaluate_delegation_scope,
    evaluate_delta_accuracy,
    evaluate_missing_decision_recall,
    normalize_conflict_rule_id,
    normalize_token,
    value_matches,
)
from evaluation.reporting import (
    ObservedDelta,
    ObservedIssue,
    ObservedQuestion,
    SystemBTurnObservation,
    TurnAnnotation,
    ValueMatchSpec,
)

# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------


def _anno(**overrides: object) -> TurnAnnotation:
    return TurnAnnotation.model_validate({"turn_id": "t1", **overrides})


def _obs(**overrides: object) -> SystemBTurnObservation:
    base: dict[str, object] = {"session_id": "ses_t", "state_before": "UNDERSTANDING",
                               "state_after": "UNDERSTANDING"}
    base.update(overrides)
    return SystemBTurnObservation(**base)  # type: ignore[arg-type]


def _turn(
    annotation: TurnAnnotation | None = None,
    observation: SystemBTurnObservation | None = None,
    *,
    turn_id: str = "t1",
    failed: bool = False,
) -> TurnObservationInput:
    return TurnObservationInput(
        turn_id=turn_id, turn_index=1, annotation=annotation,
        observation=observation, turn_failed=failed,
    )


def _delta(
    operation: str, path: str, value: str | int | None = None, resolution: str | None = None
) -> ObservedDelta:
    return ObservedDelta(operation=operation, path=path, value=value, resolution=resolution)


def _vm(mode: str, **kwargs: object) -> ValueMatchSpec:
    return ValueMatchSpec.model_validate({"mode": mode, **kwargs})


# ---------------------------------------------------------------------------
# value_match 五模式（协议第 2.3 节）
# ---------------------------------------------------------------------------


class TestValueMatch:
    def test_normalize_token(self) -> None:
        assert normalize_token("Medium_Shot") == "medium shot"
        assert normalize_token("  wide-shot  angle ") == "wide shot angle"

    def test_exact_token(self) -> None:
        spec = _vm("exact_token", keywords=["medium_shot"])
        assert value_matches(spec, "Medium Shot") is True
        assert value_matches(spec, "medium shot") is True
        assert value_matches(spec, "close_up") is False
        assert value_matches(spec, None) is False

    def test_contains_any(self) -> None:
        spec = _vm("contains_any", keywords=["橘", "orange"])
        assert value_matches(spec, "一只橘色虎斑猫") is True
        assert value_matches(spec, "an Orange cat") is True
        assert value_matches(spec, "黑猫") is False

    def test_int_equals(self) -> None:
        spec = _vm("int_equals", value=2)
        assert value_matches(spec, 2) is True
        assert value_matches(spec, "2") is True
        assert value_matches(spec, 3) is False
        assert value_matches(spec, "two") is False

    def test_any_non_empty(self) -> None:
        spec = _vm("any_non_empty")
        assert value_matches(spec, "x") is True
        assert value_matches(spec, "  ") is False
        assert value_matches(spec, None) is False

    def test_null_mode_and_absent_spec(self) -> None:
        assert value_matches(None, None) is True
        assert value_matches(None, "v") is False
        assert value_matches(_vm("null"), None) is True
        assert value_matches(_vm("null"), 1) is False


class TestDeltaMatches:
    def test_resolution_constraint(self) -> None:
        expected = {
            "operation": "SET", "path": "style.primary",
            "value_match": None, "resolution": "user_delegated",
        }
        from evaluation.reporting import ExpectedDeltaSpec

        spec = ExpectedDeltaSpec.model_validate(expected)
        assert delta_matches(spec, _delta("SET", "style.primary", None, "user_delegated"))
        assert not delta_matches(spec, _delta("SET", "style.primary", None, "user_specified"))
        # resolution=None → 不约束
        spec_free = ExpectedDeltaSpec.model_validate({**expected, "resolution": None})
        assert delta_matches(spec_free, _delta("SET", "style.primary", None, "user_specified"))


class TestNormalizeConflictRuleId:
    def test_policy_prefix_stripped(self) -> None:
        assert (
            normalize_conflict_rule_id("policy.hard_conflict.environment_mode_location")
            == "hard_conflict.environment_mode_location"
        )
        assert (
            normalize_conflict_rule_id("policy.execution_conflict.framing_aspect_mismatch")
            == "execution_conflict.framing_aspect_mismatch"
        )

    def test_bare_rule_id_and_non_conflict(self) -> None:
        assert (
            normalize_conflict_rule_id("hard_conflict.style_medium_mismatch")
            == "hard_conflict.style_medium_mismatch"
        )
        assert normalize_conflict_rule_id("validation.evidence_missing") is None
        assert normalize_conflict_rule_id("feedback.clarification_required") is None


# ---------------------------------------------------------------------------
# 1. Delta Accuracy
# ---------------------------------------------------------------------------


class TestDeltaAccuracy:
    def test_full_match_recall_and_precision(self) -> None:
        annotation = _anno(
            expected_deltas=[
                {"operation": "SET", "path": "subject.description",
                 "value_match": {"mode": "contains_any", "keywords": ["猫"]},
                 "resolution": None},
            ]
        )
        obs = _obs(applied_deltas=[_delta("SET", "subject.description", "一只橘猫")])
        payload = evaluate_delta_accuracy([_turn(annotation, obs)])
        assert payload.score == 1.0
        assert payload.counts["expected_deltas"] == 1
        assert payload.counts["matched_expected"] == 1
        assert payload.counts["in_scope"] == 1
        assert payload.counts["out_of_scope"] == 0
        assert payload.details["precision"] == 1.0

    def test_recall_miss_and_out_of_scope(self) -> None:
        annotation = _anno(
            expected_deltas=[
                {"operation": "SET", "path": "subject.description",
                 "value_match": {"mode": "contains_any", "keywords": ["橘"]},
                 "resolution": None},
                {"operation": "SET", "path": "style.primary",
                 "value_match": {"mode": "contains_any", "keywords": ["写实"]},
                 "resolution": None},
            ]
        )
        obs = _obs(
            applied_deltas=[
                _delta("SET", "subject.description", "一只黑猫"),  # 值不匹配 → 未命中
                _delta("SET", "color.palette", "warm"),            # 越界产生
            ]
        )
        payload = evaluate_delta_accuracy([_turn(annotation, obs)])
        assert payload.score == 0.0  # recall
        assert payload.counts["matched_expected"] == 0
        assert payload.counts["out_of_scope"] == 2
        assert len(payload.details["unmatched_expected"]) == 2

    def test_acceptable_extra_not_penalized(self) -> None:
        annotation = _anno(
            expected_deltas=[
                {"operation": "SET", "path": "composition.framing",
                 "value_match": {"mode": "contains_any", "keywords": ["特写"]},
                 "resolution": None},
            ],
            acceptable_extra_deltas=[
                {"operation": "PIN", "path": "subject.description",
                 "value_match": None, "resolution": None},
            ],
        )
        obs = _obs(
            applied_deltas=[
                _delta("SET", "composition.framing", "特写"),
                _delta("PIN", "subject.description"),
            ]
        )
        payload = evaluate_delta_accuracy([_turn(annotation, obs)])
        assert payload.score == 1.0
        assert payload.counts["out_of_scope"] == 0
        assert payload.details["precision"] == 1.0

    def test_expected_rejection_accepted_is_failure(self) -> None:
        annotation = _anno(
            expected_rejections=[
                {"operation": "CLEAR", "path": "subject.description",
                 "expected_issue_codes": ["validation.clear_requires_unpinned_path"],
                 "only_if_emitted": True, "resolution": None},
            ]
        )
        # 被接受（违规）
        obs_bad = _obs(applied_deltas=[_delta("CLEAR", "subject.description")])
        bad = evaluate_delta_accuracy([_turn(annotation, obs_bad)])
        assert bad.counts["rejection_failures"] == 1
        assert bad.counts["rejections_satisfied"] == 0
        # 被拒且 issue code 命中（符合）
        obs_ok = _obs(
            issues=[ObservedIssue(
                code="validation.clear_requires_unpinned_path", path="subject.description"
            )]
        )
        ok = evaluate_delta_accuracy([_turn(annotation, obs_ok)])
        assert ok.counts["rejections_satisfied"] == 1
        assert ok.counts["rejection_failures"] == 0
        # 未被提出（only_if_emitted → 条件不触发）
        obs_none = _obs()
        vacuous = evaluate_delta_accuracy([_turn(annotation, obs_none)])
        assert vacuous.counts["expected_rejections"] == 1
        assert vacuous.counts["rejections_satisfied"] == 0
        assert vacuous.counts["rejection_failures"] == 0

    def test_paths_must_remain_unset_violation(self) -> None:
        annotation = _anno(paths_must_remain_unset=["camera.angle"])
        obs = _obs(intent_values_after={"camera.angle": "low_angle"})
        payload = evaluate_delta_accuracy([_turn(annotation, obs)])
        assert payload.counts["must_remain_unset_violations"] == 1

    def test_missing_observation_keeps_denominator(self) -> None:
        annotation = _anno(
            expected_deltas=[
                {"operation": "SET", "path": "subject.description",
                 "value_match": {"mode": "any_non_empty"}, "resolution": None},
            ]
        )
        payload = evaluate_delta_accuracy([_turn(annotation, None, failed=True)])
        assert payload.score == 0.0
        assert payload.counts["expected_deltas"] == 1
        assert payload.counts["matched_expected"] == 0

    def test_empty_expectations_yield_none_score(self) -> None:
        payload = evaluate_delta_accuracy([_turn(_anno(), _obs())])
        assert payload.score is None
        assert payload.counts["expected_deltas"] == 0


# ---------------------------------------------------------------------------
# 2. Missing Decision Recall
# ---------------------------------------------------------------------------


class TestMissingDecisionRecall:
    def test_full_coverage(self) -> None:
        annotation = _anno(blocking_missing_paths_after_turn=["style.primary", "environment.mode"])
        obs = _obs(blocking_unresolved_paths=["style.primary", "environment.mode", "camera.angle"])
        payload = evaluate_missing_decision_recall([_turn(annotation, obs)])
        assert payload.score == 1.0
        assert payload.counts == {"expected_blocking": 2, "covered_blocking": 2}

    def test_partial_and_missed_listing(self) -> None:
        annotation = _anno(blocking_missing_paths_after_turn=["style.primary", "environment.mode"])
        obs = _obs(blocking_unresolved_paths=["style.primary"])
        payload = evaluate_missing_decision_recall([_turn(annotation, obs)])
        assert payload.score == 0.5
        assert payload.details["missed_blocking"] == [
            {"turn_id": "t1", "path": "environment.mode"}
        ]

    def test_failed_turn_counts_as_uncovered(self) -> None:
        annotation = _anno(blocking_missing_paths_after_turn=["style.primary"])
        payload = evaluate_missing_decision_recall([_turn(annotation, None, failed=True)])
        assert payload.score == 0.0
        assert payload.counts["expected_blocking"] == 1


# ---------------------------------------------------------------------------
# 3. Clarification Precision
# ---------------------------------------------------------------------------


def _question(path: str) -> ObservedQuestion:
    return ObservedQuestion(question_id="qst_x", target_path=path, allow_delegate=True)


class TestClarificationPrecision:
    def test_good_question_in_must_clarify(self) -> None:
        annotation = _anno(must_clarify=[{"target_path": "style.primary"}],
                           expected_outcome="clarification_expected")
        obs = _obs(question=_question("style.primary"))
        payload = evaluate_clarification_precision([_turn(annotation, obs)])
        assert payload.score == 1.0
        assert payload.counts["questions_bad"] == 0
        assert payload.counts["must_clarify_satisfied"] == 1

    def test_question_on_ready_turn_fails(self) -> None:
        annotation = _anno(expected_outcome="ready_and_generate")
        obs = _obs(question=_question("style.primary"))
        payload = evaluate_clarification_precision([_turn(annotation, obs)])
        assert payload.score == 0.0
        assert payload.counts["questions_on_ready_turns"] == 1
        assert payload.details["bad_questions"][0]["reason"] == "question_on_ready_turn"

    def test_forbidden_path_fails(self) -> None:
        annotation = _anno(must_not_clarify_paths=["subject.description"],
                           expected_outcome="clarification_expected")
        obs = _obs(question=_question("subject.description"))
        payload = evaluate_clarification_precision([_turn(annotation, obs)])
        assert payload.score == 0.0
        assert payload.details["bad_questions"][0]["reason"] == "forbidden_path"

    def test_unexpected_path_fails(self) -> None:
        annotation = _anno(must_clarify=[{"target_path": "style.primary"}],
                           acceptable_clarify_paths=["environment.mode"],
                           expected_outcome="clarification_expected")
        obs = _obs(question=_question("camera.angle"))
        payload = evaluate_clarification_precision([_turn(annotation, obs)])
        assert payload.score == 0.0
        assert payload.details["bad_questions"][0]["reason"] == "unexpected_path"

    def test_no_questions_yields_none_score_and_missed_must_clarify(self) -> None:
        annotation = _anno(must_clarify=[{"target_path": "style.primary"}],
                           expected_outcome="clarification_expected")
        payload = evaluate_clarification_precision([_turn(annotation, _obs())])
        assert payload.score is None
        assert payload.counts["questions_asked"] == 0
        assert payload.counts["must_clarify_total"] == 1
        assert payload.counts["must_clarify_satisfied"] == 0
        assert payload.details["missed_must_clarify"] == [
            {"turn_id": "t1", "target_path": "style.primary"}
        ]


# ---------------------------------------------------------------------------
# 4. Conflict Detection
# ---------------------------------------------------------------------------


class TestConflictDetection:
    def test_detected_conflict(self) -> None:
        annotation = _anno(
            expected_conflicts=[
                {"rule_id": "hard_conflict.environment_mode_location",
                 "kind": "hard", "blocking": True},
            ]
        )
        obs = _obs(conflict_rule_ids=["hard_conflict.environment_mode_location"])
        payload = evaluate_conflict_detection([_turn(annotation, obs)])
        assert payload.score == 1.0
        assert payload.counts["severe_failures"] == 0

    def test_missed_blocking_conflict_while_ready_is_severe(self) -> None:
        annotation = _anno(
            expected_conflicts=[
                {"rule_id": "hard_conflict.environment_mode_location",
                 "kind": "hard", "blocking": True},
            ]
        )
        obs = _obs(ready_for_confirmation=True, state_after="WAITING_CONFIRMATION")
        payload = evaluate_conflict_detection([_turn(annotation, obs)])
        assert payload.score == 0.0
        assert payload.counts["severe_failures"] == 1
        assert payload.details["severe_failures"][0]["rule_id"] == (
            "hard_conflict.environment_mode_location"
        )

    def test_missed_non_blocking_conflict_is_not_severe(self) -> None:
        annotation = _anno(
            expected_conflicts=[
                {"rule_id": "execution_conflict.framing_aspect_mismatch",
                 "kind": "execution", "blocking": False},
            ]
        )
        obs = _obs(ready_for_confirmation=True)
        payload = evaluate_conflict_detection([_turn(annotation, obs)])
        assert payload.score == 0.0
        assert payload.counts["severe_failures"] == 0

    def test_missed_blocking_but_not_ready_is_not_severe(self) -> None:
        annotation = _anno(
            expected_conflicts=[
                {"rule_id": "hard_conflict.environment_mode_location",
                 "kind": "hard", "blocking": True},
            ]
        )
        obs = _obs(ready_for_confirmation=False, state_after="WAITING_CLARIFICATION")
        payload = evaluate_conflict_detection([_turn(annotation, obs)])
        assert payload.counts["severe_failures"] == 0


# ---------------------------------------------------------------------------
# 5. Delegation Scope Accuracy
# ---------------------------------------------------------------------------


class TestDelegationScope:
    def test_exact_match(self) -> None:
        turns = [
            _turn(_anno(expected_resolution_records={"style.primary": "user_delegated"}),
                  _obs(delegated_paths_after=["style.primary"])),
        ]
        payload = evaluate_delegation_scope(turns)
        assert payload.score == 1.0
        assert payload.counts["over_delegated"] == 0
        assert payload.counts["under_delegated"] == 0

    def test_expected_union_across_turns(self) -> None:
        t1 = _turn(_anno(expected_resolution_records={"style.primary": "user_delegated"}),
                   _obs(delegated_paths_after=["style.primary"]), turn_id="t1")
        t2 = _turn(_anno(expected_resolution_records={"lighting.character": "user_delegated"}),
                   _obs(delegated_paths_after=["style.primary", "lighting.character"]),
                   turn_id="t2")
        payload = evaluate_delegation_scope([t1, t2])
        assert payload.score == 1.0
        assert payload.counts["expected_delegated"] == 2

    def test_over_and_under_delegation_counted_separately(self) -> None:
        turns = [
            _turn(_anno(expected_resolution_records={"style.primary": "user_delegated"}),
                  _obs(delegated_paths_after=["lighting.character", "environment.location"])),
        ]
        payload = evaluate_delegation_scope(turns)
        assert payload.counts["over_delegated"] == 2
        assert payload.counts["under_delegated"] == 1
        assert payload.score == pytest.approx(0.0)
        assert payload.details["over_delegated_paths"] == [
            "environment.location", "lighting.character",
        ]

    def test_no_observation_is_missing_data(self) -> None:
        turns = [_turn(_anno(), None, failed=True)]
        payload = evaluate_delegation_scope(turns)
        assert payload.status == "missing_data"
        assert payload.score is None

    def test_reducer_stale_delegated_record_is_not_anomalous(self) -> None:
        # 冻结语义：用户接管 delegated 路径后记录仍保留；标注按同口径书写。
        turns = [
            _turn(_anno(expected_resolution_records={"lighting.character": "user_delegated"}),
                  _obs(delegated_paths_after=["lighting.character"])),
        ]
        payload = evaluate_delegation_scope(turns)
        assert payload.score == 1.0
        assert "user_delegated" in payload.details["note"]
