"""P1 端到端测试：多轮澄清 → Ready → 硬确认门禁（全 Fake、全离线、tmp_path SQLite）。

覆盖任务书 Step 06「必测场景」全部 10 条，外加完整多轮剧本：

    模糊需求 → 多轮单问题澄清 → Ready → WAITING_CONFIRMATION → 确认
    确认页发修改消息 → UNDERSTANDING → 旧确认失效 → 重新 Ready → 再确认

以及"本步不生成 Prompt / 图片"的持久化证据（四张 Artifact 信封表计数为 0）。
"""

from __future__ import annotations

import pytest

from visual_intent_agent.domain import ExecutionRevision, Resolution, new_id
from visual_intent_agent.persistence import InvalidStateTransitionError, WorkflowState
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.workflow import (
    WORKFLOW_INVALID_STATE,
    WORKFLOW_STALE_REVISION,
    WORKFLOW_SUMMARY_HASH_MISMATCH,
    WorkflowError,
    compute_summary_hash,
)

from p1_e2e_helpers import (
    USER_TURNS,
    clarification_responses,
    delegate_entry,
    empty_response,
    make_provider,
    make_repo,
    make_service,
    ready_responses,
    response,
    set_entry,
)


def _ready(tmp_path, extra_responses=()):
    """标准剧本：7 轮单问题澄清后 Ready。"""
    repo = make_repo(tmp_path)
    llm = make_provider([*ready_responses(), *extra_responses])
    service = make_service(repo, llm)
    session_id = service.create_session().session_id
    outcome = None
    for text in USER_TURNS:
        outcome = service.submit_message(session_id, text)
    assert outcome is not None
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
    return service, repo, llm, session_id, outcome


def _hash(outcome) -> str:
    assert outcome.confirmation_summary is not None
    return compute_summary_hash(outcome.confirmation_summary)


def _artifact_counts(repo) -> dict[str, int]:
    tables = (
        "prompt_artifacts",
        "generation_artifacts",
        "feedback_results",
        "realization_states",
    )
    return {
        table: int(
            repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        )
        for table in tables
    }


# ---------------------------------------------------------------------------
# 任务书「必测场景」1～10
# ---------------------------------------------------------------------------


def test_scenario_01_empty_requirement_enters_clarification_not_confirmation(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([empty_response()]))
    session_id = service.create_session().session_id

    outcome = service.submit_message(session_id, "")

    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CLARIFICATION
    assert outcome.snapshot.workflow_state is not WorkflowState.WAITING_CONFIRMATION
    assert outcome.confirmation_summary is None
    assert outcome.resolution.ready_for_confirmation is False
    assert outcome.pending_question is not None
    assert outcome.pending_question.target_path == "subject.description"


def test_scenario_02_one_round_returns_only_the_highest_priority_question(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([empty_response()]))
    session_id = service.create_session().session_id

    outcome = service.submit_message(session_id, "")

    assert outcome.resolution.question is not None
    assert outcome.resolution.question.target_path == "subject.description"
    # core missing 优先于 perceptual missing，且只返回一个 question
    assert {d.path for d in outcome.resolution.unresolved_decisions} >= {
        "subject.description",
        "composition.framing",
    }
    assert sum(
        1
        for decision in outcome.resolution.unresolved_decisions
        if decision.path == outcome.resolution.question.target_path
    ) == 1


def test_scenario_03_answering_a_question_continues_with_the_next_unresolved(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(
        repo,
        make_provider(
            [
                response(set_entry("subject.description", "a cat")),
                response(set_entry("style.primary", "photorealistic", answers=True)),
                response(set_entry("environment.mode", "studio", answers=True)),
            ]
        ),
    )
    session_id = service.create_session().session_id

    asked = []
    for text in ("a cat", "photorealistic", "studio"):
        outcome = service.submit_message(session_id, text)
        asked.append(
            outcome.pending_question.target_path
            if outcome.pending_question is not None
            else None
        )

    assert asked == ["style.primary", "environment.mode", "environment.location"]


def test_scenario_04_ready_moves_to_waiting_confirmation(tmp_path):
    _service, _repo, _llm, _session_id, outcome = _ready(tmp_path)

    assert outcome.resolution.ready_for_confirmation is True
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
    assert outcome.pending_question is None
    assert outcome.confirmation_summary is not None


def test_scenario_05_unconfirmed_session_cannot_enter_generating(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([empty_response()]))
    session_id = service.create_session().session_id

    with pytest.raises(InvalidStateTransitionError):
        repo.transition_state(session_id, WorkflowState.GENERATING)  # UNDERSTANDING

    service.submit_message(session_id, "")
    with pytest.raises(InvalidStateTransitionError):
        repo.transition_state(session_id, WorkflowState.GENERATING)  # WAITING_CLARIFICATION
    assert service.get_session(session_id).workflow_state is WorkflowState.WAITING_CLARIFICATION


def test_scenario_06_confirming_an_old_revision_is_rejected(tmp_path):
    service, _repo, _llm, session_id, outcome = _ready(
        tmp_path, extra_responses=[response(set_entry("style.primary", "cinematic"))]
    )
    service.confirm_current_intent(
        session_id,
        outcome.snapshot.current_intent_revision_id,
        outcome.snapshot.current_execution_revision_id,
        _hash(outcome),
    )
    modified = service.submit_message(session_id, "make it cinematic")

    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            outcome.snapshot.current_intent_revision_id,  # 旧 revision
            outcome.snapshot.current_execution_revision_id,
            _hash(outcome),
        )

    assert excinfo.value.code == WORKFLOW_STALE_REVISION
    assert modified.snapshot.current_intent_revision_id != (
        outcome.snapshot.current_intent_revision_id
    )


def test_scenario_07_modifying_the_intent_invalidates_the_old_confirmation(tmp_path):
    service, repo, _llm, session_id, outcome = _ready(
        tmp_path, extra_responses=[response(set_entry("style.primary", "cinematic"))]
    )
    old = service.confirm_current_intent(
        session_id,
        outcome.snapshot.current_intent_revision_id,
        outcome.snapshot.current_execution_revision_id,
        _hash(outcome),
    )
    assert repo.is_confirmation_valid(old.confirmation_id) is True

    service.submit_message(session_id, "make it cinematic")

    assert repo.is_confirmation_valid(old.confirmation_id) is False
    assert repo.get_confirmation(old.confirmation_id) == old  # 历史保留


def test_scenario_08_changing_the_output_ratio_invalidates_the_old_confirmation(tmp_path):
    service, repo, _llm, session_id, outcome = _ready(tmp_path)
    old = service.confirm_current_intent(
        session_id,
        outcome.snapshot.current_intent_revision_id,
        outcome.snapshot.current_execution_revision_id,
        _hash(outcome),
    )

    snapshot = service.get_session(session_id)
    assert outcome.confirmation_summary is not None
    new_execution = ExecutionRevision(
        execution_revision_id=new_id("erev"),
        session_id=session_id,
        parent_revision_id=snapshot.current_execution_revision_id,
        target_model=outcome.confirmation_summary.target_model,
        output_size="1024x1536",
    )
    repo.append_execution_revision(new_execution)

    assert repo.is_confirmation_valid(old.confirmation_id) is False
    # 旧 execution revision → 过期绑定
    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            old.intent_revision_id,
            old.execution_revision_id,
            old.summary_hash,
        )
    assert excinfo.value.code == WORKFLOW_STALE_REVISION
    # 新 execution revision + 旧 hash → 摘要（含 output_size）已变
    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            old.intent_revision_id,
            new_execution.execution_revision_id,
            old.summary_hash,
        )
    assert excinfo.value.code == WORKFLOW_SUMMARY_HASH_MISMATCH


def test_scenario_09_summary_hash_mismatch_is_rejected(tmp_path):
    service, repo, _llm, session_id, outcome = _ready(tmp_path)
    summary = service.get_confirmation_summary(session_id)

    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            outcome.snapshot.current_intent_revision_id,
            outcome.snapshot.current_execution_revision_id,
            "f" * 64,
        )

    assert excinfo.value.code == WORKFLOW_SUMMARY_HASH_MISMATCH
    assert service.get_session(session_id).latest_confirmation_id is None
    assert compute_summary_hash(summary) != "f" * 64


def test_scenario_10_recommended_options_are_not_written_into_the_intent(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(
        repo,
        make_provider([response(set_entry("subject.description", "a cat"))]),
    )
    session_id = service.create_session().session_id

    outcome = service.submit_message(session_id, "a cat")

    assert outcome.pending_question is not None
    assert outcome.pending_question.target_path == "style.primary"
    assert outcome.pending_question.suggested_values == (
        "photorealistic",
        "cinematic",
        "illustration",
    )
    intent = repo.get_intent_revision(outcome.snapshot.current_intent_revision_id).intent
    assert intent.style.primary is None
    assert "style.primary" not in intent.resolutions
    # 推荐不产生 revision 变化，也不会进入确认摘要
    assert outcome.confirmation_summary is None


# ---------------------------------------------------------------------------
# 完整多轮剧本（P1 端到端）
# ---------------------------------------------------------------------------


def test_p1_full_multiturn_play_from_vague_request_to_reconfirmation(tmp_path):
    repo = make_repo(tmp_path)
    holder: dict[str, str] = {}
    responses = [
        empty_response(),
        *clarification_responses(),
        response(set_entry("style.primary", "cinematic")),
    ]
    observed_states: list[WorkflowState] = []

    def handler(_request):
        observed_states.append(
            repo.get_current_session_snapshot(holder["sid"]).workflow_state
        )
        return responses.pop(0)

    service = make_service(repo, FakeLLMProvider(handler))
    session_id = service.create_session().session_id
    holder["sid"] = session_id

    # 1. 模糊需求：进入澄清，不进入确认，也不产生 Intent revision
    vague = service.submit_message(session_id, "make me something nice")
    assert vague.snapshot.workflow_state is WorkflowState.WAITING_CLARIFICATION
    assert vague.pending_question is not None
    assert vague.pending_question.target_path == "subject.description"
    assert vague.snapshot.current_intent_revision_id is None
    assert vague.confirmation_summary is None

    # 2. 多轮单问题澄清：每轮一个新问题，绝不批量提问
    asked: list[str] = []
    outcome = vague
    for text in USER_TURNS:
        outcome = service.submit_message(session_id, text)
        if outcome.pending_question is not None:
            asked.append(outcome.pending_question.target_path)
    assert asked == [
        "style.primary",
        "environment.mode",
        "environment.location",
        "composition.framing",
        "subject.pose_action",
        "lighting.character",
    ]
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
    assert outcome.resolution.ready_for_confirmation is True
    assert outcome.pending_question is None

    # 3. 确认当前 revision
    summary = outcome.confirmation_summary
    assert summary is not None
    first_confirmation = service.confirm_current_intent(
        session_id,
        outcome.snapshot.current_intent_revision_id,
        outcome.snapshot.current_execution_revision_id,
        compute_summary_hash(summary),
    )
    assert repo.is_confirmation_valid(first_confirmation.confirmation_id) is True
    assert service.get_session(session_id).workflow_state is WorkflowState.WAITING_CONFIRMATION

    # 4. 确认页发修改消息：先回 UNDERSTANDING，再重新理解
    observed_states.clear()
    modified = service.submit_message(session_id, "make it cinematic")
    assert observed_states == [WorkflowState.UNDERSTANDING]
    assert modified.snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION

    # 5. 旧确认失效，但历史仍可审计读回
    assert repo.is_confirmation_valid(first_confirmation.confirmation_id) is False
    assert repo.get_confirmation(first_confirmation.confirmation_id) == first_confirmation
    assert modified.snapshot.current_intent_revision_id != (
        first_confirmation.intent_revision_id
    )

    # 6. 重新 Ready → 再确认
    new_summary = modified.confirmation_summary
    assert new_summary is not None
    assert new_summary.intent.style.primary == "cinematic"
    second_confirmation = service.confirm_current_intent(
        session_id,
        modified.snapshot.current_intent_revision_id,
        modified.snapshot.current_execution_revision_id,
        compute_summary_hash(new_summary),
    )
    assert second_confirmation.confirmation_id != first_confirmation.confirmation_id
    assert repo.is_confirmation_valid(second_confirmation.confirmation_id) is True
    assert repo.is_confirmation_valid(first_confirmation.confirmation_id) is False

    # 7. 本步没有 Prompt / 图片 / 反馈 / Realization 产物
    assert _artifact_counts(repo) == {
        "prompt_artifacts": 0,
        "generation_artifacts": 0,
        "feedback_results": 0,
        "realization_states": 0,
    }
    assert service.get_session(session_id).workflow_state is WorkflowState.WAITING_CONFIRMATION


def test_p1_delegated_answer_is_bound_to_the_pending_question(tmp_path):
    """「交给系统决定」只在回答当前可委托问题时生效，且绝不写入预设选项值。"""
    repo = make_repo(tmp_path)
    service = make_service(
        repo,
        make_provider(
            [
                response(set_entry("subject.description", "a cat")),
                response(delegate_entry("style.primary", answers=True)),
            ]
        ),
    )
    session_id = service.create_session().session_id

    first = service.submit_message(session_id, "a cat")
    assert first.pending_question is not None
    assert first.pending_question.target_path == "style.primary"
    assert first.pending_question.allow_delegate is True

    delegated = service.submit_message(session_id, "you decide")

    revision = repo.get_intent_revision(delegated.snapshot.current_intent_revision_id)
    assert revision.intent.style.primary is None  # 授权 ≠ 赋值
    assert (
        revision.intent.resolutions["style.primary"].resolution
        is Resolution.USER_DELEGATED
    )
    assert delegated.pending_question is not None
    assert delegated.pending_question.target_path == "environment.mode"


def test_p1_state_sequence_is_persisted_across_reads(tmp_path):
    """每一步状态都落库：序列确定，且重新打开数据库（新 Repository）读回同一快照。"""
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider(ready_responses()))
    session_id = service.create_session().session_id

    states = [service.get_session(session_id).workflow_state]
    for text in USER_TURNS:
        service.submit_message(session_id, text)
        states.append(service.get_session(session_id).workflow_state)

    assert states == [
        WorkflowState.UNDERSTANDING,
        *([WorkflowState.WAITING_CLARIFICATION] * 6),
        WorkflowState.WAITING_CONFIRMATION,
    ]
    final = service.get_session(session_id)
    repo.close()

    reopened = make_repo(tmp_path)
    try:
        restored = reopened.get_current_session_snapshot(session_id)
        assert restored == final
        assert reopened.get_intent_revision(restored.current_intent_revision_id).session_id == (
            session_id
        )
    finally:
        reopened.close()
