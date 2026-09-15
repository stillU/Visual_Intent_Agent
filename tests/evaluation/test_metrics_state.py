"""MVP v0.3 Step 03：L1 State Correctness 指标的离线单元测试。

四不变量各有**正例 + 反例**（反例按任务书口径用构造的记录数据直接喂指标函数，
不经由真实系统制造失败）。全部纯函数、全离线。
"""

from __future__ import annotations

import pytest

from evaluation.metrics.state import (
    HISTORY_TABLES,
    L1TurnEvidence,
    TableSnapshot,
    check_history_append_only,
    check_no_stale_confirmation,
    check_pinned_not_overwritten,
    check_unauthorized_fields_unchanged,
    evaluate_case_l1,
)
from evaluation.reporting import (
    L1_INVARIANT_CONFIRMATION,
    L1_INVARIANT_HISTORY,
    L1_INVARIANT_PINNED,
    L1_INVARIANT_UNAUTHORIZED,
    ObservedConfirmation,
    ObservedDelta,
    ObservedGeneration,
    SystemBTurnObservation,
    TurnAnnotation,
)

# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------


def _observation(**overrides: object) -> SystemBTurnObservation:
    values = {
        "session_id": "ses_test",
        "state_before": "UNDERSTANDING",
        "state_after": "WAITING_CONFIRMATION",
        "intent_values_before": {},
        "intent_values_after": {},
        "resolutions_before": {},
        "resolutions_after": {},
        "pinned_before": [],
        "pinned_after": [],
    }
    values.update(overrides)
    return SystemBTurnObservation(**values)  # type: ignore[arg-type]


def _evidence(
    annotation: TurnAnnotation | None = None,
    observation: SystemBTurnObservation | None = None,
    tables_before: TableSnapshot | None = None,
    tables_after: TableSnapshot | None = None,
    *,
    turn_id: str = "t1",
) -> L1TurnEvidence:
    return L1TurnEvidence(
        turn_id=turn_id,
        turn_index=1,
        annotation=annotation,
        observation=observation,
        tables_before=tables_before,
        tables_after=tables_after,
    )


def _annotation(**overrides: object) -> TurnAnnotation:
    return TurnAnnotation.model_validate({"turn_id": "t1", **overrides})


def _delta(operation: str, path: str, value: str | int | None = None) -> ObservedDelta:
    return ObservedDelta(operation=operation, path=path, value=value)


# ---------------------------------------------------------------------------
# 不变量 1：未授权字段不改变
# ---------------------------------------------------------------------------


class TestUnauthorizedFieldsUnchanged:
    def test_positive_forbidden_paths_unchanged(self) -> None:
        evidence = _evidence(
            annotation=_annotation(forbidden_change_paths=["subject.description"]),
            observation=_observation(
                intent_values_before={"subject.description": "a cat"},
                intent_values_after={"subject.description": "a cat"},
            ),
        )
        assert check_unauthorized_fields_unchanged(evidence) == []

    def test_negative_forbidden_value_changed(self) -> None:
        evidence = _evidence(
            annotation=_annotation(forbidden_change_paths=["subject.description"]),
            observation=_observation(
                intent_values_before={"subject.description": "a cat"},
                intent_values_after={"subject.description": "a dog"},
            ),
        )
        violations = check_unauthorized_fields_unchanged(evidence)
        kinds = {v.kind for v in violations}
        # forbidden 违例 + 同一变化不在 applied_deltas 内的越界违例（双重证据）。
        assert kinds == {"forbidden_value_changed", "change_outside_applied_deltas"}
        value_violation = next(v for v in violations if v.kind == "forbidden_value_changed")
        assert value_violation.path == "subject.description"
        assert value_violation.invariant == L1_INVARIANT_UNAUTHORIZED

    def test_negative_forbidden_resolution_changed(self) -> None:
        evidence = _evidence(
            annotation=_annotation(forbidden_change_paths=["style.primary"]),
            observation=_observation(
                resolutions_before={"style.primary": "user_delegated"},
                resolutions_after={"style.primary": "user_specified"},
            ),
        )
        violations = check_unauthorized_fields_unchanged(evidence)
        assert "forbidden_resolution_changed" in {v.kind for v in violations}

    def test_positive_change_inside_applied_deltas(self) -> None:
        evidence = _evidence(
            annotation=_annotation(),
            observation=_observation(
                intent_values_before={"lighting.character": None},
                intent_values_after={"lighting.character": "dramatic"},
                applied_deltas=[_delta("SET", "lighting.character", "dramatic")],
            ),
        )
        assert check_unauthorized_fields_unchanged(evidence) == []

    def test_negative_change_outside_applied_deltas(self) -> None:
        evidence = _evidence(
            annotation=_annotation(),
            observation=_observation(
                intent_values_before={"lighting.character": None},
                intent_values_after={"lighting.character": "dramatic"},
                applied_deltas=[_delta("SET", "composition.framing", "wide_shot")],
            ),
        )
        violations = check_unauthorized_fields_unchanged(evidence)
        assert [v.kind for v in violations] == ["change_outside_applied_deltas"]
        assert violations[0].path == "lighting.character"

    def test_no_observation_is_skipped(self) -> None:
        evidence = _evidence(annotation=_annotation(forbidden_change_paths=["subject.description"]))
        assert check_unauthorized_fields_unchanged(evidence) == []


# ---------------------------------------------------------------------------
# 不变量 2：旧确认不复用
# ---------------------------------------------------------------------------


def _confirmation(
    confirmation_id: str = "cnf_new",
    intent_revision_id: str = "irev_new",
    execution_revision_id: str = "erev_1",
) -> ObservedConfirmation:
    return ObservedConfirmation(
        confirmation_id=confirmation_id,
        intent_revision_id=intent_revision_id,
        execution_revision_id=execution_revision_id,
        summary_hash="ab" * 32,
        confirmation_invalidated=True,
    )


def _generation(
    confirmation_id: str = "cnf_new",
    intent_revision_id: str = "irev_new",
    current_intent_revision_id: str = "irev_new",
    current_execution_revision_id: str = "erev_1",
) -> ObservedGeneration:
    return ObservedGeneration(
        generation_id="gen_1",
        prompt_artifact_id="pra_1",
        based_on_confirmation_id=confirmation_id,
        based_on_intent_revision_id=intent_revision_id,
        current_intent_revision_id=current_intent_revision_id,
        current_execution_revision_id=current_execution_revision_id,
        prompt_text="a cat",
        prompt_sha256="cd" * 32,
        target_model="qwen-image-3.0",
        size="1024x1024",
    )


class TestNoStaleConfirmation:
    def test_positive_fresh_confirmation_binds_current_revisions(self) -> None:
        evidence = _evidence(
            observation=_observation(
                confirmation=_confirmation(),
                generations=[_generation()],
                stale_revision_probe="rejected",
                bad_hash_probe="rejected",
            )
        )
        assert check_no_stale_confirmation(evidence) == []

    def test_negative_generation_without_new_confirmation(self) -> None:
        evidence = _evidence(
            observation=_observation(confirmation=None, generations=[_generation()])
        )
        violations = check_no_stale_confirmation(evidence)
        assert [v.kind for v in violations] == ["generation_without_fresh_confirmation"]

    def test_negative_generation_binds_earlier_confirmation(self) -> None:
        evidence = _evidence(
            observation=_observation(
                confirmation=_confirmation(confirmation_id="cnf_new"),
                generations=[_generation(confirmation_id="cnf_old")],
            )
        )
        violations = check_no_stale_confirmation(evidence)
        assert [v.kind for v in violations] == ["generation_bound_to_earlier_confirmation"]

    def test_negative_confirmation_binds_stale_intent_revision(self) -> None:
        evidence = _evidence(
            observation=_observation(
                confirmation=_confirmation(intent_revision_id="irev_old"),
                generations=[_generation()],
            )
        )
        violations = check_no_stale_confirmation(evidence)
        assert [v.kind for v in violations] == ["confirmation_binds_stale_intent_revision"]

    def test_negative_stale_revision_probe_accepted(self) -> None:
        evidence = _evidence(
            observation=_observation(stale_revision_probe="accepted", bad_hash_probe="rejected")
        )
        violations = check_no_stale_confirmation(evidence)
        assert [v.kind for v in violations] == ["stale_revision_confirmation_accepted"]

    def test_negative_bad_hash_probe_accepted(self) -> None:
        evidence = _evidence(
            observation=_observation(stale_revision_probe="rejected", bad_hash_probe="accepted")
        )
        violations = check_no_stale_confirmation(evidence)
        assert [v.kind for v in violations] == ["bad_hash_confirmation_accepted"]


# ---------------------------------------------------------------------------
# 不变量 3：pinned 不被覆盖
# ---------------------------------------------------------------------------


class TestPinnedNotOverwritten:
    def test_positive_pinned_value_unchanged(self) -> None:
        evidence = _evidence(
            observation=_observation(
                pinned_before=["subject.description"],
                pinned_after=["subject.description"],
                intent_values_before={"subject.description": "a cat"},
                intent_values_after={"subject.description": "a cat"},
            )
        )
        violations, events = check_pinned_not_overwritten(evidence)
        assert violations == [] and events == []

    def test_negative_pinned_value_overwritten_by_set(self) -> None:
        evidence = _evidence(
            observation=_observation(
                pinned_before=["subject.description"],
                pinned_after=["subject.description"],
                intent_values_before={"subject.description": "a cat"},
                intent_values_after={"subject.description": "a dog"},
                applied_deltas=[_delta("SET", "subject.description", "a dog")],
            )
        )
        violations, events = check_pinned_not_overwritten(evidence)
        assert [v.kind for v in violations] == ["pinned_value_overwritten"]
        # SET-on-pinned 是冻结允许行为：同时逐例记录供归因（不改变违例结论）。
        assert events == [
            {"turn_id": "t1", "path": "subject.description", "value_changed": True}
        ]

    def test_negative_pinned_value_cleared(self) -> None:
        evidence = _evidence(
            observation=_observation(
                pinned_before=["subject.description"],
                pinned_after=["subject.description"],
                intent_values_before={"subject.description": "a cat"},
                intent_values_after={"subject.description": None},
                applied_deltas=[_delta("CLEAR", "subject.description")],
            )
        )
        violations, _ = check_pinned_not_overwritten(evidence)
        assert [v.kind for v in violations] == ["pinned_value_overwritten"]

    def test_positive_pin_change_from_user_delta(self) -> None:
        evidence = _evidence(
            observation=_observation(
                pinned_before=[],
                pinned_after=["subject.pose_action"],
                applied_deltas=[_delta("PIN", "subject.pose_action")],
            )
        )
        violations, _ = check_pinned_not_overwritten(evidence)
        assert violations == []

    def test_negative_pin_added_without_user_pin(self) -> None:
        evidence = _evidence(
            observation=_observation(pinned_before=[], pinned_after=["subject.pose_action"])
        )
        violations, _ = check_pinned_not_overwritten(evidence)
        assert [v.kind for v in violations] == ["pin_added_without_user_pin"]

    def test_negative_pin_removed_without_user_unpin(self) -> None:
        evidence = _evidence(
            observation=_observation(
                pinned_before=["subject.pose_action"],
                pinned_after=[],
                intent_values_before={"subject.pose_action": "sitting"},
                intent_values_after={"subject.pose_action": "sitting"},
            )
        )
        violations, _ = check_pinned_not_overwritten(evidence)
        assert [v.kind for v in violations] == ["pin_removed_without_user_unpin"]

    def test_set_on_pinned_same_value_is_attribution_only(self) -> None:
        # 冻结允许行为：SET pinned 但值未变 → 不违例、仅归因记录。
        evidence = _evidence(
            observation=_observation(
                pinned_before=["subject.description"],
                pinned_after=["subject.description"],
                intent_values_before={"subject.description": "a cat"},
                intent_values_after={"subject.description": "a cat"},
                applied_deltas=[_delta("SET", "subject.description", "a cat")],
            )
        )
        violations, events = check_pinned_not_overwritten(evidence)
        assert violations == []
        assert events == [
            {"turn_id": "t1", "path": "subject.description", "value_changed": False}
        ]


# ---------------------------------------------------------------------------
# 不变量 4：历史不被修改
# ---------------------------------------------------------------------------


def _tables(counts: dict[str, int], rows: dict[str, dict[str, str]]) -> TableSnapshot:
    return TableSnapshot(counts=counts, row_hashes=rows)


class TestHistoryAppendOnly:
    def test_positive_appends_keep_counts_and_payloads(self) -> None:
        before = _tables({"intent_revisions": 1}, {"intent_revisions": {"irev_1": "h1"}})
        after = _tables(
            {"intent_revisions": 2}, {"intent_revisions": {"irev_1": "h1", "irev_2": "h2"}}
        )
        assert check_history_append_only("t1", before, after) == []

    def test_negative_count_decreased(self) -> None:
        before = _tables({"messages": 2}, {"messages": {"m1": "h1", "m2": "h2"}})
        after = _tables({"messages": 1}, {"messages": {"m1": "h1"}})
        violations = check_history_append_only("t1", before, after)
        assert [v.kind for v in violations] == ["table_count_decreased", "history_row_removed"]

    def test_negative_payload_modified(self) -> None:
        before = _tables({"feedback_results": 1}, {"feedback_results": {"fbk_1": "h1"}})
        after = _tables({"feedback_results": 1}, {"feedback_results": {"fbk_1": "h2"}})
        violations = check_history_append_only("t1", before, after)
        assert [v.kind for v in violations] == ["history_row_payload_modified"]
        assert violations[0].table == "feedback_results"
        assert violations[0].row_id == "fbk_1"

    def test_all_history_tables_are_covered_by_projection(self) -> None:
        # 八张 append-only 表（sessions 是指针表、不在列）。
        assert set(HISTORY_TABLES) == {
            "messages",
            "intent_revisions",
            "execution_revisions",
            "confirmations",
            "prompt_artifacts",
            "generation_artifacts",
            "feedback_results",
            "realization_states",
        }
        assert "sessions" not in HISTORY_TABLES


# ---------------------------------------------------------------------------
# 案例级汇总
# ---------------------------------------------------------------------------


class TestEvaluateCaseL1:
    def test_all_invariants_pass(self) -> None:
        evidence = _evidence(
            annotation=_annotation(forbidden_change_paths=["subject.description"]),
            observation=_observation(
                intent_values_before={"subject.description": "a cat"},
                intent_values_after={"subject.description": "a cat"},
            ),
            tables_before=_tables({}, {}),
            tables_after=_tables({"messages": 1}, {"messages": {"m1": "h1"}}),
        )
        result = evaluate_case_l1("case-1", [evidence])
        assert result.l1_failed is False
        assert [o.invariant for o in result.outcomes] == list(
            [
                L1_INVARIANT_UNAUTHORIZED,
                L1_INVARIANT_CONFIRMATION,
                L1_INVARIANT_PINNED,
                L1_INVARIANT_HISTORY,
            ]
        )
        assert all(o.passed for o in result.outcomes)

    @pytest.mark.parametrize(
        "invariant",
        [
            L1_INVARIANT_UNAUTHORIZED,
            L1_INVARIANT_CONFIRMATION,
            L1_INVARIANT_PINNED,
            L1_INVARIANT_HISTORY,
        ],
    )
    def test_any_invariant_failure_marks_case_failed(self, invariant: str) -> None:
        kwargs: dict[str, object] = {}
        if invariant == L1_INVARIANT_UNAUTHORIZED:
            kwargs["observation"] = _observation(
                intent_values_before={"style.primary": None},
                intent_values_after={"style.primary": "illustration"},
            )
        elif invariant == L1_INVARIANT_CONFIRMATION:
            kwargs["observation"] = _observation(
                confirmation=None, generations=[_generation()]
            )
        elif invariant == L1_INVARIANT_PINNED:
            # applied SET 让不变量 1 保持干净，只触发 pinned 违例。
            kwargs["observation"] = _observation(
                pinned_before=["subject.description"],
                pinned_after=["subject.description"],
                intent_values_before={"subject.description": "a cat"},
                intent_values_after={"subject.description": "a dog"},
                applied_deltas=[_delta("SET", "subject.description", "a dog")],
            )
        else:
            kwargs["tables_before"] = _tables({"messages": 1}, {"messages": {"m1": "h1"}})
            kwargs["tables_after"] = _tables({"messages": 1}, {"messages": {"m1": "hX"}})
        kwargs.setdefault("observation", _observation())
        result = evaluate_case_l1("case-1", [_evidence(**kwargs)])  # type: ignore[arg-type]
        assert result.l1_failed is True
        failed = [o.invariant for o in result.outcomes if not o.passed]
        assert failed == [invariant]
