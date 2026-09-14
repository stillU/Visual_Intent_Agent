"""Step 06 补丁 001（工单 D）：`submit_message` 落新 IntentRevision 后接入 `evaluate_carry`。

依据 `docs/handoffs/architecture_decision_003.md`「工单 D」冻结范围，逐条覆盖用例
(a)～(h)：dependency 失效、同路径 SET/CLEAR 失效、UNPIN 成员路径失效、无 state /
无失效零写入、无 delta 不评估，以及 `derive_change_summary` 与 Reducer 的等价钉住。

装配复用 `workflow_helpers.py`；`RealizationState` 种子在测试内构造
（`RealizationState` / `RealizationValue` 直接实例化）并经
`repo.append_realization_state` 落库，refs 指向当前 revision。全离线、零真实凭据。
"""

from __future__ import annotations

import visual_intent_agent.workflow.service as workflow_service_module
from visual_intent_agent.domain import (
    CompositionFacet,
    DeltaOperation,
    EnvironmentFacet,
    IntentDelta,
    IntentRevision,
    Resolution,
    ResolutionRecord,
    SubjectFacet,
    VisualIntent,
    new_id,
)
from visual_intent_agent.realization.carry import (
    CARRY_REASON_DEPENDENCY_CHANGED,
    CARRY_REASON_UNPINNED,
    CARRY_REASON_USER_CHANGED_PATH,
)
from visual_intent_agent.realization.models import (
    REALIZATION_STATUS_ACTIVE,
    REALIZATION_STATUS_INVALIDATED,
    RealizationState,
    RealizationValue,
)
from visual_intent_agent.validation import reduce
from visual_intent_agent.workflow import derive_change_summary

from workflow_helpers import (
    empty_response,
    invalid_json_response,
    make_provider,
    make_repo,
    make_service,
    response,
    set_entry,
)

MODE = "environment.mode"
LOCATION = "environment.location"
FRAMING = "composition.framing"
SEEDED_LOCATION = "a wooden table"
REVISION_PREFIX = "irev"
REALIZATION_PREFIX = "rlz"


# ---------------------------------------------------------------------------
# 装配（create_session + 直接 seed 一条当前 IntentRevision / RealizationState）
# ---------------------------------------------------------------------------


def _intent(
    *,
    mode: str = "studio",
    framing: str | None = None,
    delegated: tuple[str, ...] = (LOCATION,),
    pinned: tuple[str, ...] = (),
) -> VisualIntent:
    """构造测试 Intent：`environment.mode` 有值，`environment.location` 被显式委托。"""
    return VisualIntent(
        composition=CompositionFacet(framing=framing),
        environment=EnvironmentFacet(mode=mode),
        resolutions={
            path: ResolutionRecord(resolution=Resolution.USER_DELEGATED)
            for path in delegated
        },
        pinned_paths=frozenset(pinned),
    )


def _seeded_session(tmp_path, intent: VisualIntent, responses, *, name: str = "carry.db"):
    """建会话 → 直接落一条当前 IntentRevision（不消耗 LLM 脚本）。"""
    repo = make_repo(tmp_path, name)
    service = make_service(repo, make_provider(responses))
    session_id = service.create_session().session_id
    revision = IntentRevision(
        intent_revision_id=new_id(REVISION_PREFIX),
        session_id=session_id,
        parent_revision_id=None,
        intent=intent,
    )
    repo.append_intent_revision(revision)
    return repo, service, session_id, revision


def _seed_realization(
    repo, session_id: str, revision_id: str, *values: RealizationValue
) -> RealizationState:
    """落一条 active RealizationState（refs 指向当前 revision）。"""
    state = RealizationState(
        realization_id=new_id(REALIZATION_PREFIX),
        session_id=session_id,
        based_on_intent_revision_id=revision_id,
        values=list(values),
    )
    repo.append_realization_state(
        state.realization_id,
        session_id,
        {"based_on_intent_revision_id": revision_id},
        state.model_dump_json(),
    )
    return state


def _seeded_location_value(value: str = SEEDED_LOCATION) -> RealizationValue:
    return RealizationValue(
        path=LOCATION,
        value=value,
        source="user_delegated",
        first_prompt_artifact_id="pra_seed",
    )


def _current_state(repo, session_id: str) -> RealizationState | None:
    stored = repo.get_current_realization_state(session_id)
    if stored is None:
        return None
    return RealizationState.model_validate_json(stored.payload)


def _row_count(repo, table: str) -> int:
    return int(repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _stored_payload(repo, realization_id: str) -> str:
    return str(
        repo.connection.execute(
            "SELECT payload FROM realization_states WHERE realization_id = ?",
            (realization_id,),
        ).fetchone()[0]
    )


def _invalidated(state: RealizationState, path: str) -> RealizationValue:
    value = next(entry for entry in state.values if entry.path == path)
    assert value.status == REALIZATION_STATUS_INVALIDATED
    return value


# ---------------------------------------------------------------------------
# (a) dependency 失效：回答 SET 某 Decision 的 dependency 路径
# ---------------------------------------------------------------------------


def test_a_setting_a_dependency_path_lands_a_new_state_with_an_explicit_invalidation(
    tmp_path,
):
    repo, service, session_id, revision = _seeded_session(
        tmp_path, _intent(), [response(set_entry(MODE, "indoor"))]
    )
    old = _seed_realization(
        repo, session_id, revision.intent_revision_id, _seeded_location_value()
    )
    old_payload = _stored_payload(repo, old.realization_id)
    states_before = _row_count(repo, "realization_states")

    outcome = service.submit_message(session_id, "make it an indoor scene")

    assert {delta.path for delta in outcome.resolution.applied_deltas} == {MODE}
    new_revision_id = outcome.snapshot.current_intent_revision_id
    assert new_revision_id is not None and new_revision_id != revision.intent_revision_id
    assert _row_count(repo, "realization_states") == states_before + 1

    stored = repo.get_current_realization_state(session_id)
    assert stored is not None
    assert stored.artifact_id != old.realization_id
    assert stored.artifact_id.startswith(f"{REALIZATION_PREFIX}_")
    assert set(stored.refs) == {"based_on_intent_revision_id"}
    assert stored.refs["based_on_intent_revision_id"] == new_revision_id

    state = RealizationState.model_validate_json(stored.payload)
    assert state.based_on_intent_revision_id == new_revision_id
    invalidated = _invalidated(state, LOCATION)
    assert invalidated.invalidated_reason == CARRY_REASON_DEPENDENCY_CHANGED
    assert invalidated.invalidated_at is not None
    assert invalidated.value == SEEDED_LOCATION

    # 历史 state 不被覆盖：旧 payload 逐字节保留在表里。
    assert _stored_payload(repo, old.realization_id) == old_payload


# ---------------------------------------------------------------------------
# (b) 同路径 SET 失效
# ---------------------------------------------------------------------------


def test_b_setting_the_delegated_path_itself_invalidates_that_value(tmp_path):
    repo, service, session_id, revision = _seeded_session(
        tmp_path, _intent(), [response(set_entry(LOCATION, "a beach"))]
    )
    _seed_realization(repo, session_id, revision.intent_revision_id, _seeded_location_value())

    service.submit_message(session_id, "put it on a beach")

    state = _current_state(repo, session_id)
    assert state is not None
    invalidated = _invalidated(state, LOCATION)
    assert invalidated.invalidated_reason == CARRY_REASON_USER_CHANGED_PATH
    assert invalidated.invalidated_at is not None
    assert invalidated.value == SEEDED_LOCATION


# ---------------------------------------------------------------------------
# (c) 同路径 CLEAR 失效
# ---------------------------------------------------------------------------


def test_c_clearing_the_delegated_path_invalidates_that_value(tmp_path):
    clear_entry = {
        "operation": "CLEAR",
        "path": LOCATION,
        "answers_pending_question": False,
        "evidence_fragment": "never mind the location",
    }
    repo, service, session_id, revision = _seeded_session(
        tmp_path, _intent(), [response(clear_entry)]
    )
    _seed_realization(repo, session_id, revision.intent_revision_id, _seeded_location_value())

    outcome = service.submit_message(session_id, "forget the location")

    assert [(delta.operation, delta.path) for delta in outcome.resolution.applied_deltas] == [
        (DeltaOperation.CLEAR, LOCATION)
    ]
    state = _current_state(repo, session_id)
    assert state is not None
    invalidated = _invalidated(state, LOCATION)
    assert invalidated.invalidated_reason == CARRY_REASON_USER_CHANGED_PATH


# ---------------------------------------------------------------------------
# (g) UNPIN 命中 Decision 成员路径 (path ∪ dependencies) → 失效
# ---------------------------------------------------------------------------


def test_g_unpinning_a_decision_member_invalidates_the_realization(tmp_path):
    unpin_entry = {
        "operation": "UNPIN",
        "path": MODE,
        "answers_pending_question": False,
        "evidence_fragment": "stop holding the environment mode",
    }
    intent = _intent(pinned=(MODE,))
    repo, service, session_id, revision = _seeded_session(
        tmp_path, intent, [response(unpin_entry)]
    )
    _seed_realization(repo, session_id, revision.intent_revision_id, _seeded_location_value())

    outcome = service.submit_message(session_id, "stop holding the environment mode")

    assert {delta.path for delta in outcome.resolution.applied_deltas} == {MODE}
    state = _current_state(repo, session_id)
    assert state is not None
    invalidated = _invalidated(state, LOCATION)
    assert invalidated.invalidated_reason == CARRY_REASON_UNPINNED


# ---------------------------------------------------------------------------
# (d) 会话无 RealizationState → 零写入、零异常
# ---------------------------------------------------------------------------


def test_d_without_a_realization_state_the_carry_step_writes_nothing(tmp_path):
    repo, service, session_id, _revision = _seeded_session(
        tmp_path, _intent(), [response(set_entry(MODE, "outdoor"))]
    )
    revisions_before = _row_count(repo, "intent_revisions")

    outcome = service.submit_message(session_id, "make it outdoor")

    assert outcome.resolution.applied_deltas
    assert _row_count(repo, "intent_revisions") == revisions_before + 1
    assert _row_count(repo, "realization_states") == 0
    assert repo.get_current_realization_state(session_id) is None
    assert outcome.snapshot.current_intent_revision_id is not None
    assert outcome.snapshot.current_intent_revision_id.startswith(f"{REVISION_PREFIX}_")


# ---------------------------------------------------------------------------
# (e) 修改不命中任何 Realization 路径及其 Decision 成员 → 稳定继承、零写入
# ---------------------------------------------------------------------------


def test_e_an_unrelated_change_keeps_the_realization_and_lands_no_new_state(tmp_path):
    intent = _intent(framing="medium_shot")
    repo, service, session_id, revision = _seeded_session(
        tmp_path, intent, [response(set_entry(FRAMING, "wide_shot"))]
    )
    seeded = _seed_realization(
        repo, session_id, revision.intent_revision_id, _seeded_location_value()
    )

    outcome = service.submit_message(session_id, "pull the camera back")

    assert {delta.path for delta in outcome.resolution.applied_deltas} == {FRAMING}
    assert _row_count(repo, "realization_states") == 1
    state = _current_state(repo, session_id)
    assert state is not None
    assert state.realization_id == seeded.realization_id
    carried = state.active_value_for(LOCATION)
    assert carried is not None
    assert carried.status == REALIZATION_STATUS_ACTIVE
    assert carried.value == SEEDED_LOCATION


# ---------------------------------------------------------------------------
# (f) 解析失败 / 空 delta（无新 revision）→ 零写入、不评估
# ---------------------------------------------------------------------------


def test_f_empty_delta_does_not_create_a_revision_or_evaluate_carry(tmp_path, monkeypatch):
    repo, service, session_id, revision = _seeded_session(
        tmp_path, _intent(), [empty_response()]
    )
    _seed_realization(repo, session_id, revision.intent_revision_id, _seeded_location_value())
    revisions_before = _row_count(repo, "intent_revisions")
    states_before = _row_count(repo, "realization_states")

    calls: list = []
    original = workflow_service_module.evaluate_carry

    def _spy(state, change_summary):
        calls.append(change_summary)
        return original(state, change_summary)

    monkeypatch.setattr(workflow_service_module, "evaluate_carry", _spy)

    outcome = service.submit_message(session_id, "just thinking out loud")

    assert outcome.resolution.applied_deltas == []
    assert _row_count(repo, "intent_revisions") == revisions_before
    assert _row_count(repo, "realization_states") == states_before
    assert calls == []


def test_f_parse_failure_does_not_create_a_revision_or_evaluate_carry(tmp_path, monkeypatch):
    repo, service, session_id, revision = _seeded_session(
        tmp_path, _intent(), [invalid_json_response(), invalid_json_response()]
    )
    _seed_realization(repo, session_id, revision.intent_revision_id, _seeded_location_value())
    revisions_before = _row_count(repo, "intent_revisions")
    states_before = _row_count(repo, "realization_states")

    calls: list = []
    original = workflow_service_module.evaluate_carry

    def _spy(state, change_summary):
        calls.append(change_summary)
        return original(state, change_summary)

    monkeypatch.setattr(workflow_service_module, "evaluate_carry", _spy)

    outcome = service.submit_message(session_id, "???  not-json  ???")

    assert outcome.resolution.applied_deltas == []
    assert outcome.resolution.issues  # 解析失败仍显式暴露，不被静默吞掉
    assert _row_count(repo, "intent_revisions") == revisions_before
    assert _row_count(repo, "realization_states") == states_before
    assert calls == []


# ---------------------------------------------------------------------------
# (h) derive_change_summary ↔ Reducer 的 ChangeSummary 语义等价钉住
# ---------------------------------------------------------------------------


def test_h_derive_change_summary_matches_the_reducer_for_a_mixed_batch():
    intent = VisualIntent(
        subject=SubjectFacet(description="a cat"),
        environment=EnvironmentFacet(mode="studio"),
        pinned_paths=frozenset({MODE}),
    )
    deltas = [
        IntentDelta(operation=DeltaOperation.SET, path=MODE, value="outdoor"),
        IntentDelta(operation=DeltaOperation.SET, path=MODE, value="outdoor"),
        IntentDelta(operation=DeltaOperation.CLEAR, path=LOCATION),
        IntentDelta(operation=DeltaOperation.PIN, path="subject.description"),
        IntentDelta(operation=DeltaOperation.UNPIN, path=MODE),
        IntentDelta(operation=DeltaOperation.UNPIN, path=MODE),
    ]

    derived = derive_change_summary(list(deltas))
    reduced = reduce(intent, deltas).change_summary

    assert derived.model_dump() == reduced.model_dump()
    assert derived.changed_paths == [MODE]
    assert derived.cleared_paths == [LOCATION]
    assert derived.pinned_paths == ["subject.description"]
    assert derived.unpinned_paths == [MODE]
    assert derived.confirmation_invalidated is True
