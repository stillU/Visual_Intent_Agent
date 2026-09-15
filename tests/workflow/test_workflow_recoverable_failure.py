"""回归测试（MVP v0.3 更改书 002 · 工包 B / 更改书 001 · R1-C）：可恢复失败。

钉住"重试耗尽后可恢复失败"的完整合同（全部离线，`FakeLLMProvider` + `tmp_path` SQLite）：

1. 连续 `provider.timeout` → `recoverable_failure=True`、底层 code 显式可见；
2. 不能用"空理解"结果伪造新的澄清问题（首轮失败留在 `UNDERSTANDING`，不落问题）；
3. 有既存 Pending Question 时保留**同一个**问题与 `WAITING_CLARIFICATION`；
4. 用户消息保留、原 Intent / 既有 PIN / revision 不变；
5. PIN 请求失败后恢复：成功一轮只应用一次 Delta（恰好一条新 revision）；
6. 过期请求（解析期间会话被推进）整体作废，绝不应用到新 revision；
7. 不新增第三层重试：调用次数等于既有 `MAX_INTERPRETATION_ATTEMPTS`。
"""

from __future__ import annotations

from visual_intent_agent.domain import (
    EnvironmentFacet,
    IntentRevision,
    VisualIntent,
    new_id,
)
from visual_intent_agent.persistence import WorkflowState
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.workflow import MAX_INTERPRETATION_ATTEMPTS

from workflow_helpers import make_provider, make_repo, make_service, response, set_entry

PINNED_PATH = "environment.mode"
CHANGED_PATH = "composition.framing"
STALE_REVISION = "workflow.stale_revision"


def _count(repo, table: str) -> int:
    return int(repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _seed_pinned_intent(repo, session_id: str) -> IntentRevision:
    """直接落一条带 PIN 的当前 IntentRevision（不消耗 LLM 脚本）。"""
    intent = VisualIntent(
        environment=EnvironmentFacet(mode="studio"),
        pinned_paths=frozenset({PINNED_PATH}),
    )
    revision = IntentRevision(
        intent_revision_id=new_id("irev"),
        session_id=session_id,
        parent_revision_id=None,
        intent=intent,
    )
    repo.append_intent_revision(revision)
    return revision


def test_consecutive_timeouts_report_a_recoverable_failure_without_a_new_question(
    tmp_path,
) -> None:
    repo = make_repo(tmp_path)
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        raise ProviderError.timeout("chat completion timed out after 60.0s")

    service = make_service(repo, FakeLLMProvider(handler))
    session_id = service.create_session().session_id

    outcome = service.submit_message(session_id, "画一只猫")

    # 不新增第三层重试：恰好是既有的有界预算（初次 + 至多一次）。
    assert calls["n"] == MAX_INTERPRETATION_ATTEMPTS == 2
    assert outcome.recoverable_failure is True
    assert outcome.failure_codes == ("provider.timeout",)
    # 空理解不得被当成新的澄清需求推进会话。
    assert outcome.snapshot.workflow_state is WorkflowState.UNDERSTANDING
    assert outcome.pending_question is None
    assert outcome.confirmation_summary is None
    assert outcome.snapshot.current_intent_revision_id is None
    assert outcome.resolution.applied_deltas == []
    assert outcome.resolution.ready_for_confirmation is False
    # 用户消息保留：可显式重试，输入不丢。
    assert len(outcome.snapshot.message_ids) == 1
    assert repo.get_current_session_snapshot(session_id).message_ids == (
        outcome.snapshot.message_ids
    )


def test_retry_exhausted_answer_keeps_the_same_pending_question(tmp_path) -> None:
    repo = make_repo(tmp_path)
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] == 1:
            return response(set_entry("subject.description", "a cat"))
        raise ProviderError.timeout("chat completion timed out after 60.0s")

    service = make_service(repo, FakeLLMProvider(handler))
    session_id = service.create_session().session_id

    first = service.submit_message(session_id, "a cat")
    assert first.snapshot.workflow_state is WorkflowState.WAITING_CLARIFICATION
    question = first.pending_question
    assert question is not None
    revision_id = first.snapshot.current_intent_revision_id

    second = service.submit_message(session_id, "photorealistic")

    assert second.recoverable_failure is True
    assert second.failure_codes == ("provider.timeout",)
    # 保留原等待状态与**同一个**问题（不重新生成 question_id / 文案）。
    assert second.snapshot.workflow_state is WorkflowState.WAITING_CLARIFICATION
    assert second.pending_question is not None
    assert second.pending_question.question_id == question.question_id
    assert second.pending_question.model_dump_json() == question.model_dump_json()
    # 原 Intent revision 不变；用户消息保留（两条）。
    assert second.snapshot.current_intent_revision_id == revision_id
    assert second.resolution.applied_deltas == []
    assert len(second.snapshot.message_ids) == 2


def test_pin_survives_retry_exhaustion_and_success_applies_exactly_once(tmp_path) -> None:
    repo = make_repo(tmp_path)
    state = {"n": 0}

    def handler(_request):
        state["n"] += 1
        if state["n"] <= MAX_INTERPRETATION_ATTEMPTS:
            raise ProviderError.timeout("chat completion timed out after 60.0s")
        return response(set_entry(CHANGED_PATH, "close_up"))

    service = make_service(repo, FakeLLMProvider(handler))
    session_id = service.create_session().session_id
    revision = _seed_pinned_intent(repo, session_id)

    # 第一轮：重试耗尽 → 可恢复失败；PIN 与原 revision 不变。
    first = service.submit_message(session_id, "改成特写")
    assert first.recoverable_failure is True
    after_failure = repo.get_current_session_snapshot(session_id)
    assert after_failure.current_intent_revision_id == revision.intent_revision_id
    assert after_failure.workflow_state is WorkflowState.UNDERSTANDING
    preserved = repo.get_intent_revision(revision.intent_revision_id).intent
    assert PINNED_PATH in preserved.pinned_paths
    assert _count(repo, "intent_revisions") == 1

    # 第二轮（显式重试）：成功一次 → 只应用一次，PIN 仍在。
    second = service.submit_message(session_id, "改成特写")
    assert second.recoverable_failure is False
    assert second.failure_codes == ()
    new_revision_id = second.snapshot.current_intent_revision_id
    assert new_revision_id is not None and new_revision_id != revision.intent_revision_id
    new_revision = repo.get_intent_revision(new_revision_id)
    assert new_revision.parent_revision_id == revision.intent_revision_id
    assert new_revision.intent.pinned_paths == frozenset({PINNED_PATH})
    assert PINNED_PATH in new_revision.intent.pinned_paths
    assert new_revision.intent.composition.framing == "close_up"
    assert _count(repo, "intent_revisions") == 2  # 恰好一次


def test_stale_request_is_discarded_and_never_applied_to_the_new_revision(
    tmp_path, monkeypatch
) -> None:
    repo = make_repo(tmp_path)
    service = make_service(
        repo, make_provider([response(set_entry("subject.description", "a cat"))])
    )
    session_id = service.create_session().session_id
    engine = service._intent_engine
    real_resolve = engine.resolve
    concurrent: dict[str, str] = {}

    def resolve_then_advance(request):
        resolution = real_resolve(request)
        # 模拟并发轮次：解析期间另一个轮次推进了会话头。
        current = repo.get_current_session_snapshot(session_id)
        revision = IntentRevision(
            intent_revision_id=new_id("irev"),
            session_id=session_id,
            parent_revision_id=current.current_intent_revision_id,
            intent=request.current_intent,
        )
        repo.append_intent_revision(revision)
        concurrent["revision_id"] = revision.intent_revision_id
        return resolution

    monkeypatch.setattr(engine, "resolve", resolve_then_advance)

    outcome = service.submit_message(session_id, "a cat")

    assert outcome.recoverable_failure is True
    assert STALE_REVISION in outcome.failure_codes
    assert outcome.resolution.applied_deltas == []
    assert outcome.resolution.ready_for_confirmation is False
    assert outcome.snapshot.current_intent_revision_id == concurrent["revision_id"]
    # 只有并发那一轮落了库；本轮的 delta 从未被应用。
    assert _count(repo, "intent_revisions") == 1
