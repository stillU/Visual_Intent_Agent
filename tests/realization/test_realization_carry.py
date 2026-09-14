"""Step 09 Realization carry / invalidation 测试（`realization/carry.py`）。

表驱动案例来自 `tests/fixtures/multiturn/p3_carry_cases_v1.json`（版本化 JSON），
外加直接单元测试与 Repository 集成（新 state 落库、历史不覆盖、外键约束）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from visual_intent_agent.domain import (
    ExecutionRevision,
    IntentRevision,
    VisualIntent,
    new_id,
)
from visual_intent_agent.persistence import RepositoryError, SQLiteRepository
from visual_intent_agent.policy import DECISION_POLICIES
from visual_intent_agent.realization.carry import (
    CARRY_REASON_DEPENDENCY_CHANGED,
    CARRY_REASON_UNPINNED,
    CARRY_REASON_USER_CHANGED_PATH,
    CarryEvaluation,
    build_carry_state,
    evaluate_carry,
    owner_policy_for_path,
)
from visual_intent_agent.realization.models import (
    REALIZATION_STATUS_ACTIVE,
    REALIZATION_STATUS_INVALIDATED,
    RealizationState,
    RealizationValue,
)
from visual_intent_agent.validation import ChangeSummary

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "multiturn"
CARRY_FIXTURE = FIXTURES / "p3_carry_cases_v1.json"


# ---------------------------------------------------------------------------
# 构造
# ---------------------------------------------------------------------------


def _change_summary(**overrides) -> ChangeSummary:
    fields = {
        "changed_paths": [],
        "pinned_paths": [],
        "unpinned_paths": [],
        "cleared_paths": [],
        "confirmation_invalidated": False,
    }
    fields.update(overrides)
    return ChangeSummary(**fields)


def _value(path: str, value: str = "soft", **overrides) -> RealizationValue:
    fields = {
        "path": path,
        "value": value,
        "source": "user_delegated",
        "first_prompt_artifact_id": "pra_1",
    }
    fields.update(overrides)
    return RealizationValue(**fields)


def _state(values=None, **overrides) -> RealizationState:
    fields = {
        "realization_id": "rlz_1",
        "session_id": "ses_1",
        "based_on_intent_revision_id": "irev_1",
        "values": list(values) if values is not None else [],
    }
    fields.update(overrides)
    return RealizationState(**fields)


def _fixture_cases() -> list[dict]:
    return json.loads(CARRY_FIXTURE.read_text(encoding="utf-8"))["cases"]


# ---------------------------------------------------------------------------
# 表驱动：失效四条件
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["name"])
def test_carry_fixture_cases(case):
    if case.get("state_absent"):
        state = None
    else:
        state = _state([_value(**entry) for entry in case["values"]])
    summary = ChangeSummary(**case["change_summary"])

    evaluation = evaluate_carry(state, summary)

    assert evaluation.carried_paths() == case["expect_carried"]
    assert evaluation.invalidated_paths() == [
        entry["path"] for entry in case["expect_invalidated"]
    ]
    by_path = {value.path: value for value in evaluation.invalidated_values}
    for entry in case["expect_invalidated"]:
        invalidated = by_path[entry["path"]]
        assert invalidated.status == REALIZATION_STATUS_INVALIDATED
        assert invalidated.invalidated_reason == entry["reason"]
        assert invalidated.invalidated_at is not None
        assert invalidated.invalidated_at.utcoffset() == timedelta(0)


def test_evaluate_carry_never_mutates_the_input_state():
    original = _value("lighting.character")
    state = _state([original])
    before = state.model_dump_json()

    evaluation = evaluate_carry(state, _change_summary(changed_paths=["lighting.character"]))

    assert state.model_dump_json() == before
    assert evaluation.carried_values == []
    assert evaluation.invalidated_values[0] is not original
    assert original.status == REALIZATION_STATUS_ACTIVE
    assert original.invalidated_reason is None


def test_evaluate_carry_without_a_state_is_empty():
    evaluation = evaluate_carry(None, _change_summary(changed_paths=["composition.framing"]))
    assert evaluation == CarryEvaluation()
    assert evaluation.has_invalidations() is False


def test_all_invalidations_share_one_timestamp():
    state = _state([_value("lighting.character"), _value("environment.location")])
    evaluation = evaluate_carry(
        state,
        _change_summary(changed_paths=["lighting.character", "environment.location"]),
    )
    assert len(evaluation.invalidated_values) == 2
    assert (
        evaluation.invalidated_values[0].invalidated_at
        == evaluation.invalidated_values[1].invalidated_at
    )


def test_pin_never_invalidates_and_unpin_does():
    state = _state([_value("lighting.character")])
    pinned = evaluate_carry(state, _change_summary(pinned_paths=["lighting.character"]))
    assert pinned.carried_paths() == ["lighting.character"]
    assert pinned.invalidated_values == []

    unpinned = evaluate_carry(state, _change_summary(unpinned_paths=["lighting.character"]))
    assert unpinned.invalidated_values[0].invalidated_reason == CARRY_REASON_UNPINNED


def test_same_path_clear_is_invalidated_with_the_user_changed_reason():
    state = _state([_value("lighting.character")])
    evaluation = evaluate_carry(state, _change_summary(cleared_paths=["lighting.character"]))
    assert evaluation.invalidated_values[0].invalidated_reason == CARRY_REASON_USER_CHANGED_PATH


def test_pure_resolution_set_on_the_path_also_invalidates():
    # Step 02 冻结：changed_paths 包含纯 resolution 的 SET（授权范围变化）
    state = _state([_value("subject.pose_action")])
    evaluation = evaluate_carry(state, _change_summary(changed_paths=["subject.pose_action"]))
    assert evaluation.invalidated_paths() == ["subject.pose_action"]
    assert evaluation.invalidated_values[0].invalidated_reason == CARRY_REASON_USER_CHANGED_PATH


def test_carry_with_an_empty_policy_table_still_handles_the_same_path():
    state = _state([_value("lighting.character")])
    evaluation = evaluate_carry(
        state, _change_summary(changed_paths=["lighting.character"]), policies=()
    )
    assert evaluation.invalidated_paths() == ["lighting.character"]
    assert evaluation.invalidated_values[0].invalidated_reason == CARRY_REASON_USER_CHANGED_PATH


def test_dependency_change_uses_the_decision_member_relationship():
    state = _state([_value("environment.location", "a cozy interior")])
    evaluation = evaluate_carry(state, _change_summary(changed_paths=["environment.mode"]))
    assert evaluation.invalidated_values[0].invalidated_reason == CARRY_REASON_DEPENDENCY_CHANGED


def test_owner_policy_lookup_covers_dependency_members():
    owner = owner_policy_for_path("environment.location")
    assert owner is not None
    assert owner.path == "environment.mode"
    assert owner_policy_for_path("composition.framing") is not None
    assert owner_policy_for_path("subject.count") is None


def test_all_nine_decisions_are_declared_invalidating_in_policy_v1():
    assert len(DECISION_POLICIES) == 9
    assert all(policy.invalidates_realization for policy in DECISION_POLICIES)


# ---------------------------------------------------------------------------
# build_carry_state
# ---------------------------------------------------------------------------


def test_build_carry_state_creates_a_new_snapshot_in_original_order():
    carried = _value("lighting.character", "soft")
    invalidated = _value("environment.location", "a cozy interior")
    state = _state([invalidated, carried])
    evaluation = evaluate_carry(
        state, _change_summary(changed_paths=["environment.location"])
    )

    new_state = build_carry_state(
        evaluation,
        state,
        session_id="ses_1",
        based_on_intent_revision_id="irev_2",
        realization_id="rlz_2",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert new_state.realization_id == "rlz_2"
    assert new_state.based_on_intent_revision_id == "irev_2"
    assert [value.path for value in new_state.values] == [
        "environment.location",
        "lighting.character",
    ]
    assert new_state.values[0].status == REALIZATION_STATUS_INVALIDATED
    assert new_state.values[1].status == REALIZATION_STATUS_ACTIVE
    assert new_state.values[1].first_prompt_artifact_id == carried.first_prompt_artifact_id
    # 默认生成新的 `rlz` ID
    auto = build_carry_state(
        evaluation, state, session_id="ses_1", based_on_intent_revision_id="irev_2"
    )
    assert auto.realization_id.startswith("rlz_")
    assert auto.realization_id != state.realization_id


def test_build_carry_state_drops_previously_invalidated_values_but_keeps_active_ones():
    already = _value("style.primary", "cinematic", status=REALIZATION_STATUS_INVALIDATED)
    active = _value("lighting.character", "soft")
    state = _state([already, active])

    evaluation = evaluate_carry(state, _change_summary())
    new_state = build_carry_state(
        evaluation, state, session_id="ses_1", based_on_intent_revision_id="irev_2"
    )

    assert evaluation.carried_paths() == ["lighting.character"]
    assert [value.path for value in new_state.values] == ["lighting.character"]


# ---------------------------------------------------------------------------
# Repository 集成：落库、外键、历史不覆盖
# ---------------------------------------------------------------------------


def _seeded_repo(tmp_path) -> tuple[SQLiteRepository, str, str]:
    repo = SQLiteRepository(tmp_path / "carry.db")
    session_id = new_id("ses")
    repo.create_session(session_id)
    repo.append_execution_revision(
        ExecutionRevision(
            execution_revision_id=new_id("erev"),
            session_id=session_id,
            parent_revision_id=None,
            target_model="qwen-image-3.0",
        )
    )
    revision = IntentRevision(
        intent_revision_id=new_id("irev"),
        session_id=session_id,
        parent_revision_id=None,
        intent=VisualIntent(),
    )
    repo.append_intent_revision(revision)
    return repo, session_id, revision.intent_revision_id


def test_new_state_is_appended_and_history_is_never_overwritten(tmp_path):
    repo, session_id, revision_id = _seeded_repo(tmp_path)
    original = _state(
        [_value("lighting.character")],
        realization_id=new_id("rlz"),
        session_id=session_id,
        based_on_intent_revision_id=revision_id,
    )
    repo.append_realization_state(
        original.realization_id,
        session_id,
        {"based_on_intent_revision_id": revision_id},
        original.model_dump_json(),
    )

    evaluation = evaluate_carry(
        original, _change_summary(changed_paths=["lighting.character"])
    )
    new_state = build_carry_state(
        evaluation, original, session_id=session_id, based_on_intent_revision_id=revision_id
    )
    repo.append_realization_state(
        new_state.realization_id,
        session_id,
        {"based_on_intent_revision_id": revision_id},
        new_state.model_dump_json(),
    )

    stored = repo.get_current_realization_state(session_id)
    assert stored is not None
    assert stored.artifact_id == new_state.realization_id
    assert RealizationState.model_validate_json(stored.payload) == new_state

    rows = repo.connection.execute(
        "SELECT realization_id, payload FROM realization_states WHERE session_id = ? "
        "ORDER BY created_at, rowid",
        (session_id,),
    ).fetchall()
    assert [row[0] for row in rows] == [original.realization_id, new_state.realization_id]
    assert RealizationState.model_validate_json(rows[0][1]) == original  # 旧值逐字节保留
    repo.close()


def test_append_realization_state_requires_an_existing_revision(tmp_path):
    repo, session_id, _ = _seeded_repo(tmp_path)
    state = _state([], realization_id=new_id("rlz"), session_id=session_id)
    with pytest.raises(RepositoryError):
        repo.append_realization_state(
            state.realization_id,
            session_id,
            {"based_on_intent_revision_id": "irev_missing"},
            state.model_dump_json(),
        )
    repo.close()


def test_carry_evaluation_is_importable_from_the_multi_step_package_path():
    # realization/ 是多 Step 拥有的包：carry 只能从模块路径导入（__init__ 保持空白）
    import visual_intent_agent.realization as realization_package

    assert Path(realization_package.__file__).read_text(encoding="utf-8").strip() == ""
    assert not hasattr(realization_package, "evaluate_carry")
