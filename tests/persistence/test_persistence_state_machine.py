"""状态机测试：7 个持久状态、冻结的 10 条迁移 + Rev.1 增补第 11 条、非法迁移显式拒绝且不落库。"""

from __future__ import annotations

import pytest
from persistence_helpers import reach_state, seed_session

from visual_intent_agent.persistence import (
    ALLOWED_TRANSITIONS,
    InvalidStateTransitionError,
    RepositoryError,
    WorkflowState,
)

EXPECTED_STATE_VALUES = (
    "UNDERSTANDING",
    "WAITING_CLARIFICATION",
    "WAITING_CONFIRMATION",
    "GENERATING",
    "WAITING_REVIEW",
    "FAILED",
    "COMPLETED",
)

#: 任务书「必须支持的状态迁移」10 条 + Rev.1 增补 `WAITING_CONFIRMATION → UNDERSTANDING`
#: 为第 11 条（"new session → UNDERSTANDING" 是初始态，不在表内）。
EXPECTED_TRANSITIONS = frozenset(
    {
        (WorkflowState.UNDERSTANDING, WorkflowState.WAITING_CLARIFICATION),
        (WorkflowState.UNDERSTANDING, WorkflowState.WAITING_CONFIRMATION),
        (WorkflowState.WAITING_CLARIFICATION, WorkflowState.UNDERSTANDING),
        (WorkflowState.WAITING_CONFIRMATION, WorkflowState.GENERATING),
        (WorkflowState.GENERATING, WorkflowState.WAITING_REVIEW),
        (WorkflowState.GENERATING, WorkflowState.FAILED),
        (WorkflowState.FAILED, WorkflowState.GENERATING),
        (WorkflowState.FAILED, WorkflowState.WAITING_CONFIRMATION),
        (WorkflowState.WAITING_REVIEW, WorkflowState.UNDERSTANDING),
        (WorkflowState.WAITING_REVIEW, WorkflowState.COMPLETED),
        # Rev.1 增补（architecture_decision_001.md 提案 1）。
        (WorkflowState.WAITING_CONFIRMATION, WorkflowState.UNDERSTANDING),
    }
)

ALL_PAIRS = tuple(
    (source, destination)
    for source in WorkflowState
    for destination in WorkflowState
)
ILLEGAL_PAIRS = tuple(
    pair
    for pair in ALL_PAIRS
    if pair not in EXPECTED_TRANSITIONS and pair[0] is not pair[1]
)


def _pair_id(pair: tuple[WorkflowState, WorkflowState]) -> str:
    return f"{pair[0].value}__{pair[1].value}"


def test_workflow_state_exposes_exactly_seven_persistent_states() -> None:
    assert tuple(state.value for state in WorkflowState) == EXPECTED_STATE_VALUES
    assert len(WorkflowState) == 7


def test_retrieving_and_refining_are_not_persistent_states() -> None:
    names = {state.name for state in WorkflowState}
    assert "RETRIEVING" not in names
    assert "REFINING" not in names
    for transient in ("RETRIEVING", "REFINING"):
        with pytest.raises(ValueError):
            WorkflowState(transient)


def test_allowed_transitions_exactly_match_the_task_book() -> None:
    assert isinstance(ALLOWED_TRANSITIONS, frozenset)
    assert ALLOWED_TRANSITIONS == EXPECTED_TRANSITIONS
    assert len(ALLOWED_TRANSITIONS) == 11


def test_allowed_transitions_only_contain_workflow_states() -> None:
    for source, destination in ALLOWED_TRANSITIONS:
        assert isinstance(source, WorkflowState)
        assert isinstance(destination, WorkflowState)


def test_new_session_starts_in_understanding(repo) -> None:
    session_id = seed_session(repo)
    snapshot = repo.get_current_session_snapshot(session_id)
    assert snapshot.workflow_state is WorkflowState.UNDERSTANDING


@pytest.mark.parametrize("pair", sorted(EXPECTED_TRANSITIONS, key=_pair_id), ids=_pair_id)
def test_every_frozen_transition_can_be_applied_and_persisted(repo, pair) -> None:
    source, destination = pair
    session_id = seed_session(repo)
    reach_state(repo, session_id, source)
    assert repo.get_current_session_snapshot(session_id).workflow_state is source

    repo.transition_state(session_id, destination)

    assert repo.get_current_session_snapshot(session_id).workflow_state is destination


@pytest.mark.parametrize("pair", ILLEGAL_PAIRS, ids=_pair_id)
def test_illegal_transition_is_rejected_and_changes_nothing(repo, pair) -> None:
    source, destination = pair
    session_id = seed_session(repo)
    reach_state(repo, session_id, source)
    before = repo.get_current_session_snapshot(session_id)

    with pytest.raises(InvalidStateTransitionError) as excinfo:
        repo.transition_state(session_id, destination)

    error = excinfo.value
    assert error.code == "persistence.invalid_state_transition"
    assert error.session_id == session_id
    assert error.from_state is source
    assert error.to_state is destination
    assert source.value in str(error)
    assert destination.value in str(error)
    assert repo.get_current_session_snapshot(session_id) == before


def test_rev1_confirmation_page_message_returns_to_understanding(repo) -> None:
    """Rev.1 第 11 条：确认页收到修改消息时 `WAITING_CONFIRMATION → UNDERSTANDING` 可落库。

    同时断言增补是纯 additive：原 10 条仍在表中，`WAITING_CONFIRMATION` 的其余未冻结
    目标状态仍被拒绝（异常且零写入），随后该迁移本身可正常落库。
    """
    original_ten = EXPECTED_TRANSITIONS - {
        (WorkflowState.WAITING_CONFIRMATION, WorkflowState.UNDERSTANDING)
    }
    assert len(original_ten) == 10
    assert original_ten < ALLOWED_TRANSITIONS
    assert len(ALLOWED_TRANSITIONS) == 11

    session_id = seed_session(repo)
    reach_state(repo, session_id, WorkflowState.WAITING_CONFIRMATION)
    before = repo.get_current_session_snapshot(session_id)

    for illegal in (
        WorkflowState.WAITING_CLARIFICATION,
        WorkflowState.WAITING_REVIEW,
        WorkflowState.COMPLETED,
        WorkflowState.FAILED,
    ):
        with pytest.raises(InvalidStateTransitionError):
            repo.transition_state(session_id, illegal)
    assert repo.get_current_session_snapshot(session_id) == before

    repo.transition_state(session_id, WorkflowState.UNDERSTANDING)

    assert repo.get_current_session_snapshot(session_id).workflow_state is WorkflowState.UNDERSTANDING


def test_transition_error_exposes_the_frozen_code_attribute() -> None:
    assert InvalidStateTransitionError.code == "persistence.invalid_state_transition"


def test_unknown_state_string_is_rejected_as_an_illegal_transition(repo) -> None:
    session_id = seed_session(repo)
    with pytest.raises(InvalidStateTransitionError) as excinfo:
        repo.transition_state(session_id, "REFINING")
    assert excinfo.value.code == "persistence.invalid_state_transition"
    assert "REFINING" in str(excinfo.value)
    assert repo.get_current_session_snapshot(session_id).workflow_state is WorkflowState.UNDERSTANDING


def test_state_name_string_is_accepted_for_convenience(repo) -> None:
    session_id = seed_session(repo)
    repo.transition_state(session_id, "WAITING_CLARIFICATION")
    assert (
        repo.get_current_session_snapshot(session_id).workflow_state
        is WorkflowState.WAITING_CLARIFICATION
    )


def test_transition_on_missing_session_raises_repository_error(repo) -> None:
    with pytest.raises(RepositoryError) as excinfo:
        repo.transition_state("ses_missing", WorkflowState.WAITING_CLARIFICATION)
    assert excinfo.value.code == "persistence.session_not_found"


def test_completed_is_final(repo) -> None:
    session_id = seed_session(repo)
    reach_state(repo, session_id, WorkflowState.COMPLETED)
    for destination in WorkflowState:
        if destination is WorkflowState.COMPLETED:
            continue  # 同状态 = 幂等重复请求（见 test_repeating_the_current_state_...）
        with pytest.raises(InvalidStateTransitionError):
            repo.transition_state(session_id, destination)
    assert repo.get_current_session_snapshot(session_id).workflow_state is WorkflowState.COMPLETED


@pytest.mark.parametrize("state", list(WorkflowState), ids=lambda state: state.value)
def test_repeating_the_current_state_is_an_idempotent_noop(repo, state) -> None:
    """Step 06 每轮用户消息都会调用 "状态进入 UNDERSTANDING"，同状态必须是成功的零写入。"""
    session_id = seed_session(repo)
    reach_state(repo, session_id, state)
    before = repo.get_current_session_snapshot(session_id)

    repo.transition_state(session_id, state)

    assert repo.get_current_session_snapshot(session_id) == before


def test_failed_can_resume_generation_or_return_to_confirmation(repo) -> None:
    session_id = seed_session(repo)
    reach_state(repo, session_id, WorkflowState.FAILED)
    repo.transition_state(session_id, WorkflowState.GENERATING)
    assert repo.get_current_session_snapshot(session_id).workflow_state is WorkflowState.GENERATING

    other = seed_session(repo, "ses_0002")
    reach_state(repo, other, WorkflowState.FAILED)
    repo.transition_state(other, WorkflowState.WAITING_CONFIRMATION)
    assert (
        repo.get_current_session_snapshot(other).workflow_state
        is WorkflowState.WAITING_CONFIRMATION
    )


def test_review_can_return_to_understanding_for_revision(repo) -> None:
    session_id = seed_session(repo)
    reach_state(repo, session_id, WorkflowState.WAITING_REVIEW)
    repo.transition_state(session_id, WorkflowState.UNDERSTANDING)
    assert repo.get_current_session_snapshot(session_id).workflow_state is WorkflowState.UNDERSTANDING


def test_generation_requires_confirmation_state_first(repo) -> None:
    session_id = seed_session(repo)
    with pytest.raises(InvalidStateTransitionError):
        repo.transition_state(session_id, WorkflowState.GENERATING)
    repo.transition_state(session_id, WorkflowState.WAITING_CONFIRMATION)
    repo.transition_state(session_id, WorkflowState.GENERATING)
    assert repo.get_current_session_snapshot(session_id).workflow_state is WorkflowState.GENERATING


def test_clarification_must_return_to_understanding_before_confirmation(repo) -> None:
    session_id = seed_session(repo)
    repo.transition_state(session_id, WorkflowState.WAITING_CLARIFICATION)
    with pytest.raises(InvalidStateTransitionError):
        repo.transition_state(session_id, WorkflowState.WAITING_CONFIRMATION)
    repo.transition_state(session_id, WorkflowState.UNDERSTANDING)
    repo.transition_state(session_id, WorkflowState.WAITING_CONFIRMATION)
    assert (
        repo.get_current_session_snapshot(session_id).workflow_state
        is WorkflowState.WAITING_CONFIRMATION
    )
