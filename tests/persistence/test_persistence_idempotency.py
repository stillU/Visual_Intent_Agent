"""幂等与并发保护测试：重复请求显式拒绝、并发写不产生双重效果。"""

from __future__ import annotations

import threading

import pytest
from persistence_helpers import (
    FIXED_TIME,
    make_confirmation,
    make_intent_revision,
    make_summary_hash,
    seed_context,
    seed_session,
)

from visual_intent_agent.persistence import (
    InvalidStateTransitionError,
    RepositoryError,
    SQLiteRepository,
)


def run_concurrently(workers):
    """每个 worker 自建连接，在 barrier 处同步后同时开始写；收集全部结果。"""
    barrier = threading.Barrier(len(workers), timeout=10)
    outcomes: list = [None] * len(workers)

    def run(index, worker):
        try:
            outcomes[index] = ("ok", worker(barrier))
        except BaseException as exc:  # noqa: BLE001 - 测试需要收集全部异常
            outcomes[index] = ("error", exc)

    threads = [threading.Thread(target=run, args=(index, worker)) for index, worker in enumerate(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(outcome is not None for outcome in outcomes), "a worker did not finish"
    return outcomes


def test_duplicate_create_session_is_rejected_without_touching_data(repo) -> None:
    session_id = seed_session(repo)
    before = repo.get_current_session_snapshot(session_id)

    with pytest.raises(RepositoryError) as excinfo:
        repo.create_session(session_id)

    assert excinfo.value.code == "persistence.session_exists"
    assert repo.get_current_session_snapshot(session_id) == before


def test_duplicate_message_is_rejected_and_stored_once(repo) -> None:
    session_id = seed_session(repo)
    repo.append_message(session_id, "msg_0001", "user", "hello", FIXED_TIME)

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_message(session_id, "msg_0001", "user", "hello again", FIXED_TIME)

    assert excinfo.value.code == "persistence.duplicate_id"
    assert repo.get_current_session_snapshot(session_id).message_ids == ("msg_0001",)


def test_duplicate_revision_and_confirmation_are_rejected(repo) -> None:
    intent_revision, execution_revision, confirmation = seed_context(repo)
    assert confirmation is not None

    with pytest.raises(RepositoryError) as revision_error:
        repo.append_intent_revision(make_intent_revision("ses_0001", "irev_0001"))
    assert revision_error.value.code == "persistence.duplicate_id"

    duplicate_confirmation = make_confirmation(
        "ses_0001",
        "cnf_0001",
        intent_revision.intent_revision_id,
        execution_revision.execution_revision_id,
        summary_hash=make_summary_hash("other"),
    )
    with pytest.raises(RepositoryError) as confirmation_error:
        repo.save_confirmation(duplicate_confirmation)
    assert confirmation_error.value.code == "persistence.duplicate_id"
    assert repo.get_confirmation("cnf_0001") == confirmation


def test_repeating_the_same_transition_is_an_idempotent_noop(repo) -> None:
    session_id = seed_session(repo)
    repo.transition_state(session_id, "WAITING_CONFIRMATION")
    after_first = repo.get_current_session_snapshot(session_id)

    repo.transition_state(session_id, "WAITING_CONFIRMATION")

    assert repo.get_current_session_snapshot(session_id) == after_first


def test_illegal_state_change_is_still_rejected_after_a_repeat(repo) -> None:
    session_id = seed_session(repo)
    repo.transition_state(session_id, "WAITING_CONFIRMATION")

    with pytest.raises(InvalidStateTransitionError):
        repo.transition_state(session_id, "COMPLETED")

    assert repo.get_current_session_snapshot(session_id).workflow_state.value == "WAITING_CONFIRMATION"


def test_saving_the_same_pending_question_twice_is_idempotent(repo) -> None:
    session_id = seed_session(repo)
    payload = '{"target_path": "style.primary"}'
    repo.save_pending_question(session_id, "qst_0001", payload)
    first = repo.get_current_session_snapshot(session_id)
    repo.save_pending_question(session_id, "qst_0001", payload)
    assert repo.get_current_session_snapshot(session_id) == first


def test_concurrent_same_message_id_has_exactly_one_winner(repo, db_path) -> None:
    session_id = seed_session(repo)

    def worker(barrier):
        instance = SQLiteRepository(db_path)
        try:
            barrier.wait()
            instance.append_message(session_id, "msg_race", "user", "hi", FIXED_TIME)
            return "appended"
        finally:
            instance.close()

    outcomes = run_concurrently([worker] * 4)

    kinds = [kind for kind, _ in outcomes]
    assert kinds.count("ok") == 1
    errors = [value for kind, value in outcomes if kind == "error"]
    assert all(isinstance(error, RepositoryError) for error in errors)
    assert {error.code for error in errors} == {"persistence.duplicate_id"}
    assert repo.get_current_session_snapshot(session_id).message_ids == ("msg_race",)


def test_concurrent_transitions_leave_a_legal_state(repo, db_path) -> None:
    session_id = seed_session(repo)

    def worker(target):
        def run(barrier):
            instance = SQLiteRepository(db_path)
            try:
                barrier.wait()
                instance.transition_state(session_id, target)
                return target
            finally:
                instance.close()

        return run

    outcomes = run_concurrently(
        [worker("WAITING_CLARIFICATION"), worker("WAITING_CONFIRMATION")]
    )

    kinds = [kind for kind, _ in outcomes]
    assert kinds.count("ok") == 1
    errors = [value for kind, value in outcomes if kind == "error"]
    assert all(isinstance(error, InvalidStateTransitionError) for error in errors)
    final_state = repo.get_current_session_snapshot(session_id).workflow_state
    assert final_state.value in {"WAITING_CLARIFICATION", "WAITING_CONFIRMATION"}


def test_concurrent_child_revisions_cannot_fork_the_chain(repo, db_path) -> None:
    session_id = seed_session(repo)
    repo.append_intent_revision(make_intent_revision(session_id, "irev_0001"))

    def worker(revision_id):
        def run(barrier):
            instance = SQLiteRepository(db_path)
            try:
                barrier.wait()
                instance.append_intent_revision(
                    make_intent_revision(session_id, revision_id, "irev_0001")
                )
                return revision_id
            finally:
                instance.close()

        return run

    outcomes = run_concurrently([worker("irev_0002"), worker("irev_0003")])

    kinds = [kind for kind, _ in outcomes]
    assert kinds.count("ok") == 1
    errors = [value for kind, value in outcomes if kind == "error"]
    assert all(isinstance(error, RepositoryError) for error in errors)
    assert {error.code for error in errors} == {"persistence.stale_revision"}
    head = repo.get_current_session_snapshot(session_id).current_intent_revision_id
    assert head in {"irev_0002", "irev_0003"}
    # 只有胜者落库；败者的行不存在
    loser = "irev_0003" if head == "irev_0002" else "irev_0002"
    with pytest.raises(RepositoryError) as excinfo:
        repo.get_intent_revision(loser)
    assert excinfo.value.code == "persistence.intent_revision_not_found"
