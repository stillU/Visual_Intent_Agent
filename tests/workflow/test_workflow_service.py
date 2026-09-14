"""Step 06 单元/集成测试：WorkflowService 的四个用例与 Hard Confirmation Gate。"""

from __future__ import annotations

import pytest

from visual_intent_agent.domain import ExecutionRevision, new_id, utc_now
from visual_intent_agent.persistence import (
    InvalidStateTransitionError,
    WorkflowState,
)
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.workflow import (
    DEFAULT_OUTPUT_SIZE,
    MAX_INTERPRETATION_ATTEMPTS,
    SubmitMessageOutcome,
    WORKFLOW_INVALID_STATE,
    WORKFLOW_STALE_REVISION,
    WORKFLOW_SUMMARY_HASH_MISMATCH,
    WorkflowError,
    compute_summary_hash,
)

from workflow_helpers import (
    FAKE_IMAGE_MODEL,
    empty_response,
    full_intent_response,
    invalid_json_response,
    make_provider,
    make_repo,
    make_service,
    ready_responses,
    response,
    set_entry,
)

USER_TURNS = (
    "a photo of a cat",
    "photorealistic style",
    "in a studio",
    "on a wooden table",
    "medium shot",
    "sitting",
    "soft lighting",
)


def _ready(tmp_path, extra_responses=()):
    """跑到 Ready 的会话（7 轮单问题澄清），可选追加脚本响应。"""
    repo = make_repo(tmp_path)
    llm = make_provider([*ready_responses(), *extra_responses])
    service = make_service(repo, llm)
    snapshot = service.create_session()
    session_id = snapshot.session_id
    outcome = None
    for text in USER_TURNS:
        outcome = service.submit_message(session_id, text)
    assert outcome is not None
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
    return service, repo, llm, session_id, outcome


def _confirm(service, session_id: str, outcome: SubmitMessageOutcome):
    assert outcome.confirmation_summary is not None
    summary = outcome.confirmation_summary
    return service.confirm_current_intent(
        session_id,
        outcome.snapshot.current_intent_revision_id,
        outcome.snapshot.current_execution_revision_id,
        compute_summary_hash(summary),
    )


# ---------------------------------------------------------------------------
# create_session / get_session
# ---------------------------------------------------------------------------


def test_create_session_starts_understanding_with_an_initial_execution_revision(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([]))

    snapshot = service.create_session()

    assert snapshot.session_id.startswith("ses_")
    assert snapshot.workflow_state is WorkflowState.UNDERSTANDING
    assert snapshot.current_intent_revision_id is None
    assert snapshot.current_execution_revision_id is not None
    assert snapshot.current_execution_revision_id.startswith("erev_")
    assert snapshot.latest_confirmation_id is None
    assert snapshot.pending_question_id is None
    assert snapshot.message_ids == ()

    execution = repo.get_execution_revision(snapshot.current_execution_revision_id)
    assert execution.parent_revision_id is None
    assert execution.target_model == FAKE_IMAGE_MODEL
    assert execution.output_size == DEFAULT_OUTPUT_SIZE
    assert service.get_session(snapshot.session_id) == snapshot


def test_get_session_reads_back_persisted_state(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([empty_response()]))
    session_id = service.create_session().session_id

    service.submit_message(session_id, "")

    snapshot = service.get_session(session_id)
    assert snapshot.session_id == session_id
    assert len(snapshot.message_ids) == 1
    assert snapshot.message_ids[0].startswith("msg_")


# ---------------------------------------------------------------------------
# submit_message：澄清流程
# ---------------------------------------------------------------------------


def test_empty_requirement_enters_clarification_not_confirmation(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([empty_response()]))
    session_id = service.create_session().session_id

    outcome = service.submit_message(session_id, "")

    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CLARIFICATION
    assert outcome.confirmation_summary is None
    assert outcome.pending_question is not None
    assert outcome.pending_question.target_path == "subject.description"
    # 没有 delta → 不产生 IntentRevision；系统绝不替用户决定主体
    assert outcome.snapshot.current_intent_revision_id is None
    assert len(outcome.snapshot.message_ids) == 1


def test_one_round_returns_exactly_the_highest_priority_question(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([empty_response()]))
    session_id = service.create_session().session_id

    outcome = service.submit_message(session_id, "")

    # DecisionPolicy 同时报告多个 unresolved decision，但本轮只问一个
    unresolved_paths = {d.path for d in outcome.resolution.unresolved_decisions}
    assert len(unresolved_paths) > 1
    assert outcome.resolution.ready_for_confirmation is False
    assert outcome.resolution.question is not None
    assert outcome.resolution.question.target_path == "subject.description"
    assert outcome.pending_question is not None
    assert outcome.pending_question.target_path == "subject.description"
    # core missing > perceptual missing：subject.description 先于 composition.framing
    assert "composition.framing" in unresolved_paths


def test_answering_a_question_moves_to_the_next_unresolved_decision(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(
        repo,
        make_provider(
            [
                response(set_entry("subject.description", "a cat")),
                response(set_entry("style.primary", "photorealistic", answers=True)),
            ]
        ),
    )
    session_id = service.create_session().session_id

    first = service.submit_message(session_id, "a cat")
    assert first.pending_question is not None
    assert first.pending_question.target_path == "style.primary"

    second = service.submit_message(session_id, "photorealistic")
    assert second.pending_question is not None
    assert second.pending_question.target_path == "environment.mode"
    assert second.snapshot.workflow_state is WorkflowState.WAITING_CLARIFICATION


def test_pending_question_is_persisted_and_restorable(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([empty_response()]))
    session_id = service.create_session().session_id

    outcome = service.submit_message(session_id, "")
    snapshot = service.get_session(session_id)

    assert snapshot.pending_question_id == outcome.pending_question.question_id
    assert snapshot.pending_question_payload is not None
    from visual_intent_agent.workflow import PendingQuestion

    restored = PendingQuestion.model_validate_json(snapshot.pending_question_payload)
    assert restored == outcome.pending_question
    assert restored.to_spec().question_id == outcome.pending_question.question_id


def test_pending_question_is_cleared_once_ready(tmp_path):
    _service, _repo, _llm, session_id, outcome = _ready(tmp_path)

    assert outcome.pending_question is None
    assert outcome.snapshot.pending_question_id is None
    assert outcome.snapshot.pending_question_payload is None
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION


def test_ready_turn_enters_waiting_confirmation_with_a_summary(tmp_path):
    service, _repo, _llm, _session_id, outcome = _ready(tmp_path)

    assert outcome.resolution.ready_for_confirmation is True
    assert outcome.resolution.question is None
    assert outcome.confirmation_summary is not None
    assert outcome.confirmation_summary.target_model == FAKE_IMAGE_MODEL
    assert outcome.confirmation_summary.output_size == DEFAULT_OUTPUT_SIZE
    assert outcome.confirmation_summary.intent.subject.description == "a cat"


def test_every_turn_with_a_delta_appends_exactly_one_revision_with_parent_chain(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider(ready_responses()))
    session_id = service.create_session().session_id

    parents = []
    revisions = []
    for text in USER_TURNS:
        outcome = service.submit_message(session_id, text)
        revision_id = outcome.snapshot.current_intent_revision_id
        assert revision_id is not None and revision_id.startswith("irev_")
        revision = repo.get_intent_revision(revision_id)
        parents.append(revision.parent_revision_id)
        revisions.append(revision)
        # intent_id 在 P1 无消费方：Step 06 不赋值（dispatch 明确要求）
        assert revision.intent.intent_id is None

    assert parents[0] is None
    for index in range(1, len(revisions)):
        assert parents[index] == revisions[index - 1].intent_revision_id


def test_a_turn_without_deltas_does_not_create_a_revision(tmp_path):
    service, repo, _llm, session_id, ready = _ready(tmp_path, extra_responses=[empty_response()])
    revision_before = ready.snapshot.current_intent_revision_id

    outcome = service.submit_message(session_id, "ok")

    assert outcome.resolution.applied_deltas == []
    assert outcome.snapshot.current_intent_revision_id == revision_before
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION


def test_suggested_options_are_never_written_into_the_intent(tmp_path):
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
    # 推荐只是选项：既没有值，也没有 resolution 记录
    intent = repo.get_intent_revision(outcome.snapshot.current_intent_revision_id).intent
    assert intent.style.primary is None
    assert "style.primary" not in intent.resolutions


def test_transition_to_understanding_happens_before_resolution(tmp_path):
    repo = make_repo(tmp_path)
    holder: dict[str, str] = {}
    responses = ready_responses()
    observed: list[WorkflowState] = []

    def handler(_request):
        observed.append(repo.get_current_session_snapshot(holder["sid"]).workflow_state)
        return responses.pop(0)

    llm = FakeLLMProvider(handler)
    service = make_service(repo, llm)
    session_id = service.create_session().session_id
    holder["sid"] = session_id

    for text in USER_TURNS:
        service.submit_message(session_id, text)
    assert observed == [WorkflowState.UNDERSTANDING] * len(USER_TURNS)

    # 确认页收到修改消息：状态先回到 UNDERSTANDING，再重新理解
    responses.append(response(set_entry("style.primary", "cinematic")))
    service.submit_message(session_id, "make it cinematic")
    assert observed[-1] is WorkflowState.UNDERSTANDING


# ---------------------------------------------------------------------------
# submit_message：状态与失败
# ---------------------------------------------------------------------------


def test_submit_message_rejects_non_understanding_states_without_storing(tmp_path):
    service, repo, _llm, session_id, ready = _ready(tmp_path)
    repo.transition_state(session_id, WorkflowState.GENERATING)
    before = service.get_session(session_id).message_ids

    with pytest.raises(WorkflowError) as excinfo:
        service.submit_message(session_id, "hello")

    assert excinfo.value.code == WORKFLOW_INVALID_STATE
    snapshot = service.get_session(session_id)
    assert snapshot.workflow_state is WorkflowState.GENERATING
    assert snapshot.message_ids == before  # 拒绝时零写入


def test_submit_message_is_rejected_after_completion(tmp_path):
    service, repo, _llm, session_id, _ready_outcome = _ready(tmp_path)
    repo.transition_state(session_id, WorkflowState.GENERATING)
    repo.transition_state(session_id, WorkflowState.WAITING_REVIEW)
    repo.transition_state(session_id, WorkflowState.COMPLETED)

    with pytest.raises(WorkflowError) as excinfo:
        service.submit_message(session_id, "one more")
    assert excinfo.value.code == WORKFLOW_INVALID_STATE


def test_non_string_message_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([]))
    session_id = service.create_session().session_id

    with pytest.raises(TypeError):
        service.submit_message(session_id, 42)  # type: ignore[arg-type]


def test_retryable_parse_failure_is_retried_exactly_once(tmp_path):
    repo = make_repo(tmp_path)
    llm = make_provider([invalid_json_response(), response(set_entry("subject.description", "a cat"))])
    service = make_service(repo, llm)
    session_id = service.create_session().session_id

    outcome = service.submit_message(session_id, "a cat")

    assert len(llm.requests) == 2
    assert MAX_INTERPRETATION_ATTEMPTS == 2
    assert outcome.resolution.applied_deltas != []
    assert outcome.pending_question is not None
    assert outcome.pending_question.target_path == "style.primary"


def test_failure_that_keeps_failing_is_reported_and_asks_for_clarification(tmp_path):
    repo = make_repo(tmp_path)
    llm = make_provider([invalid_json_response(), full_intent_response()])
    service = make_service(repo, llm)
    session_id = service.create_session().session_id

    outcome = service.submit_message(session_id, "something")

    assert len(llm.requests) == 2  # 初次 + 一次修复，不会无限重试
    codes = [issue.code for issue in outcome.resolution.issues]
    assert "interpreter.unparseable_output.full_intent" in codes
    assert outcome.resolution.ready_for_confirmation is False
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CLARIFICATION
    assert outcome.pending_question is not None
    assert outcome.pending_question.target_path == "subject.description"


def test_non_retryable_provider_failure_is_not_retried(tmp_path):
    repo = make_repo(tmp_path)
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        raise ProviderError.auth("bad credentials")

    llm = FakeLLMProvider(handler)
    service = make_service(repo, llm)
    session_id = service.create_session().session_id

    outcome = service.submit_message(session_id, "a cat")

    assert calls["n"] == 1
    assert len(llm.requests) == 1
    assert any(issue.code == "provider.auth" for issue in outcome.resolution.issues)
    assert outcome.resolution.applied_deltas == []
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CLARIFICATION


def test_retryable_provider_failure_is_retried_once(tmp_path):
    repo = make_repo(tmp_path)
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError.rate_limited("slow down")
        return response(set_entry("subject.description", "a cat"))

    llm = FakeLLMProvider(handler)
    service = make_service(repo, llm)
    session_id = service.create_session().session_id

    outcome = service.submit_message(session_id, "a cat")

    assert calls["n"] == 2
    assert outcome.resolution.applied_deltas != []
    assert outcome.snapshot.current_intent_revision_id is not None


def test_parse_failure_on_the_confirmation_page_never_fakes_confirmation(tmp_path):
    service, repo, _llm, session_id, ready = _ready(
        tmp_path, extra_responses=[invalid_json_response(), invalid_json_response()]
    )
    confirmation = _confirm(service, session_id, ready)

    outcome = service.submit_message(session_id, "unparseable request")

    assert outcome.resolution.ready_for_confirmation is False
    assert outcome.confirmation_summary is None
    assert outcome.snapshot.workflow_state is WorkflowState.UNDERSTANDING
    # 没有 delta → 旧 revision 未变，但状态不允许确认（确认页不会被绕过）
    assert outcome.snapshot.current_intent_revision_id == confirmation.intent_revision_id
    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            confirmation.intent_revision_id,
            confirmation.execution_revision_id,
            confirmation.summary_hash,
        )
    assert excinfo.value.code == WORKFLOW_INVALID_STATE


# ---------------------------------------------------------------------------
# confirm_current_intent：Hard Confirmation Gate
# ---------------------------------------------------------------------------


def test_confirm_records_the_current_revisions_and_allows_generation(tmp_path):
    service, repo, _llm, session_id, outcome = _ready(tmp_path)
    summary = outcome.confirmation_summary
    assert summary is not None

    record = _confirm(service, session_id, outcome)

    assert record.confirmation_id.startswith("cnf_")
    assert record.session_id == session_id
    assert record.intent_revision_id == outcome.snapshot.current_intent_revision_id
    assert record.execution_revision_id == outcome.snapshot.current_execution_revision_id
    assert record.summary_hash == compute_summary_hash(summary)
    assert repo.get_confirmation(record.confirmation_id) == record
    assert repo.is_confirmation_valid(record.confirmation_id) is True
    # 本步不进入 GENERATING：确认只是"允许进入生成"
    snapshot = service.get_session(session_id)
    assert snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
    assert snapshot.latest_confirmation_id == record.confirmation_id


def test_confirm_is_rejected_outside_waiting_confirmation(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([empty_response()]))
    session_id = service.create_session().session_id
    outcome = service.submit_message(session_id, "")

    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(session_id, "irev_x", "erev_x", "hash")

    assert excinfo.value.code == WORKFLOW_INVALID_STATE
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CLARIFICATION


def test_confirm_with_a_stale_intent_revision_is_rejected(tmp_path):
    service, _repo, _llm, session_id, outcome = _ready(tmp_path)

    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            "irev_not_current",
            outcome.snapshot.current_execution_revision_id,
            "hash",
        )

    assert excinfo.value.code == WORKFLOW_STALE_REVISION


def test_confirm_with_a_stale_execution_revision_is_rejected(tmp_path):
    service, _repo, _llm, session_id, outcome = _ready(tmp_path)

    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            outcome.snapshot.current_intent_revision_id,
            "erev_not_current",
            "hash",
        )

    assert excinfo.value.code == WORKFLOW_STALE_REVISION


def test_confirm_with_a_wrong_summary_hash_is_rejected(tmp_path):
    service, _repo, _llm, session_id, outcome = _ready(tmp_path)

    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            outcome.snapshot.current_intent_revision_id,
            outcome.snapshot.current_execution_revision_id,
            "0" * 64,
        )

    assert excinfo.value.code == WORKFLOW_SUMMARY_HASH_MISMATCH


def test_confirm_with_a_non_string_hash_is_rejected(tmp_path):
    service, _repo, _llm, session_id, outcome = _ready(tmp_path)

    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            outcome.snapshot.current_intent_revision_id,
            outcome.snapshot.current_execution_revision_id,
            None,  # type: ignore[arg-type]
        )

    assert excinfo.value.code == WORKFLOW_SUMMARY_HASH_MISMATCH


def test_a_delayed_confirmation_cannot_be_applied_to_a_new_revision(tmp_path):
    service, repo, _llm, session_id, outcome = _ready(
        tmp_path, extra_responses=[response(set_entry("style.primary", "cinematic"))]
    )
    old = _confirm(service, session_id, outcome)

    modified = service.submit_message(session_id, "make it cinematic")

    assert repo.is_confirmation_valid(old.confirmation_id) is False
    # 旧记录仍可审计读回
    assert repo.get_confirmation(old.confirmation_id) == old
    assert modified.snapshot.current_intent_revision_id != old.intent_revision_id
    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            old.intent_revision_id,
            old.execution_revision_id,
            old.summary_hash,
        )
    assert excinfo.value.code == WORKFLOW_STALE_REVISION


def test_confirming_again_after_a_modification_produces_a_new_valid_confirmation(tmp_path):
    service, repo, _llm, session_id, outcome = _ready(
        tmp_path, extra_responses=[response(set_entry("style.primary", "cinematic"))]
    )
    old = _confirm(service, session_id, outcome)

    modified = service.submit_message(session_id, "make it cinematic")
    new = _confirm(service, session_id, modified)

    assert new.confirmation_id != old.confirmation_id
    assert repo.is_confirmation_valid(new.confirmation_id) is True
    assert repo.is_confirmation_valid(old.confirmation_id) is False


def test_changing_the_output_ratio_invalidates_the_old_confirmation(tmp_path):
    service, repo, _llm, session_id, outcome = _ready(tmp_path)
    old = _confirm(service, session_id, outcome)

    snapshot = service.get_session(session_id)
    new_execution = ExecutionRevision(
        execution_revision_id=new_id("erev"),
        session_id=session_id,
        parent_revision_id=snapshot.current_execution_revision_id,
        target_model=FAKE_IMAGE_MODEL,
        output_size="1024x1536",
        created_at=utc_now(),
    )
    repo.append_execution_revision(new_execution)

    assert repo.is_confirmation_valid(old.confirmation_id) is False
    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            old.intent_revision_id,
            old.execution_revision_id,  # 旧的 execution revision
            old.summary_hash,
        )
    assert excinfo.value.code == WORKFLOW_STALE_REVISION
    # 新的 execution revision + 旧 hash：摘要（含 output_size）已变 → hash 不匹配
    with pytest.raises(WorkflowError) as excinfo:
        service.confirm_current_intent(
            session_id,
            old.intent_revision_id,
            new_execution.execution_revision_id,
            old.summary_hash,
        )
    assert excinfo.value.code == WORKFLOW_SUMMARY_HASH_MISMATCH


# ---------------------------------------------------------------------------
# get_confirmation_summary（读取侧）
# ---------------------------------------------------------------------------


def test_get_confirmation_summary_is_unavailable_before_ready(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([empty_response()]))
    session_id = service.create_session().session_id
    service.submit_message(session_id, "")

    with pytest.raises(WorkflowError) as excinfo:
        service.get_confirmation_summary(session_id)
    assert excinfo.value.code == WORKFLOW_INVALID_STATE


def test_get_confirmation_summary_matches_the_submit_outcome(tmp_path):
    service, _repo, _llm, session_id, outcome = _ready(tmp_path)

    summary = service.get_confirmation_summary(session_id)

    assert summary == outcome.confirmation_summary
    assert compute_summary_hash(summary) == compute_summary_hash(
        outcome.confirmation_summary
    )


# ---------------------------------------------------------------------------
# Hard gate：没有绕过路径
# ---------------------------------------------------------------------------


def test_state_machine_rejects_generation_without_confirmation(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([empty_response()]))
    session_id = service.create_session().session_id

    with pytest.raises(InvalidStateTransitionError):
        repo.transition_state(session_id, WorkflowState.GENERATING)  # UNDERSTANDING

    service.submit_message(session_id, "")
    with pytest.raises(InvalidStateTransitionError):
        repo.transition_state(session_id, WorkflowState.GENERATING)  # WAITING_CLARIFICATION


def test_confirm_is_the_only_gate_and_does_not_generate():
    import inspect

    from visual_intent_agent.workflow import service as service_module

    source = inspect.getsource(service_module)
    assert "WorkflowState.GENERATING" not in source
    assert "prompt_engine" not in source
    assert "PromptEngine" not in source
    assert "ImageProvider" not in source


def test_submit_message_outcome_shape_is_frozen(tmp_path):
    from pydantic import ValidationError

    _service, _repo, _llm, _session_id, outcome = _ready(tmp_path)

    with pytest.raises(ValidationError):
        SubmitMessageOutcome(
            snapshot=outcome.snapshot,
            resolution=outcome.resolution,
            pending_question=outcome.pending_question,
            confirmation_summary=outcome.confirmation_summary,
            unexpected=True,  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        outcome.snapshot = None  # type: ignore[misc]
    assert set(SubmitMessageOutcome.model_fields) == {
        "snapshot",
        "resolution",
        "pending_question",
        "confirmation_summary",
    }
