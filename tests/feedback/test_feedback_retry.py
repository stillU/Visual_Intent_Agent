"""回归测试（MVP v0.3 Step 06 patch 002 · P6 超时缓解）：FeedbackEngine 引擎级重试。

失败证据（诊断性真实复跑 `outputs/evaluation_runs/`）：

- 诊断一 `erun_e7413108dc8a6104` / `s08-pin-unpin-001` t2
  （`eturn_c86c180e3ebc5094`，`feedback_id=fbk_f9cee845098946359af5947813da0bba`，
  `llm_call=ellm_981ba576cfcd9065`）：FeedbackEngine 调用 `provider.timeout`
  （`latency_ms=181 947`，adapter 内 2 次退避重试共 3 次 HTTP 尝试全超时）
  → 引擎降级为 recoverable clarify、零候选 → PIN 未落库 → t3 的合法 CLEAR 撞标注
  `forbidden_change_paths`（L1 违例）。
- 诊断二 `erun_f78691dffb0f7494` / `s07-single-field-002` t2
  （`eturn_fbb635a1d116d9b7`，`feedback_id=fbk_27b7f4b4a6e04b4687764f0e9c0d8e60`，
  `llm_call=ellm_59fdeb3f627e4de5`）：同一形态——SET `subject.count` 随超时丢失，
  t3 被合理判为 revise 落库 → 撞 forbidden。

本文件钉住 [Rev.3]（`docs/handoffs/architecture_decision_004.md`）：

1. **红→绿**：无引擎级重试时，第一次 `provider.timeout` 即降级并丢失 delta；有重试时，
   第二次成功则 delta 保留并正常 `_to_result`；
2. 只有可重试 Provider 错误（`RETRYABLE_CODES`）重试，最多 2 次尝试；
3. 不可重试 Provider 错误与解析失败（被测系统行为）**不重试**；
4. 重试用尽后的降级与 v0.2 逐字一致：recoverable clarify、零候选、状态不变。

全部离线：`FakeLLMProvider` 脚本 + `tmp_path` SQLite + `FakeImageProvider`。
"""

from __future__ import annotations

import pytest

from visual_intent_agent.domain import DeltaOperation, IntentRevision, new_id
from visual_intent_agent.feedback import (
    FEEDBACK_INVALID_JSON,
    MAX_FEEDBACK_ATTEMPTS,
    FeedbackDecision,
    FeedbackEngine,
)
from visual_intent_agent.persistence import WorkflowState
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.workflow import QuestionBuilder
from visual_intent_agent.workflow.review import ReviewService
from visual_intent_agent.workflow.service import MAX_INTERPRETATION_ATTEMPTS

from feedback_helpers import (
    artifact_counts,
    feedback_response,
    load_feedback_request,
    make_p3_session,
    revise_entry,
)


def test_retry_budget_matches_the_interpreter_precedent() -> None:
    """引擎级重试预算必须严格对齐 Interpreter 的 `MAX_INTERPRETATION_ATTEMPTS=2`。"""
    assert MAX_FEEDBACK_ATTEMPTS == 2
    assert MAX_FEEDBACK_ATTEMPTS == MAX_INTERPRETATION_ATTEMPTS


def test_retryable_timeout_retries_and_applies_the_second_attempt_delta(tmp_path) -> None:
    """红→绿核心：第一次 timeout、第二次成功 → delta 保留（无重试时会丢失）。"""
    session = make_p3_session(tmp_path)
    attempts: list[int] = []

    def handler(request):
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise ProviderError.timeout("chat completion timed out after 60.0s")
        return feedback_response(
            "revise",
            candidate_deltas=[
                revise_entry("subject.count", value=2, evidence_fragment="改成两只")
            ],
        )

    provider = FakeLLMProvider(handler)
    engine = FeedbackEngine(provider)
    result = engine.analyze(
        load_feedback_request(session, feedback_text="改成两只边牧，一黑一白，其他都不变。")
    )

    # 重试确实发生：同一请求被调用两次，第二次结果被真正应用。
    assert len(provider.requests) == 2
    assert (
        provider.requests[0].messages[1].content
        == provider.requests[1].messages[1].content
    )
    assert result.decision is FeedbackDecision.REVISE
    assert result.issues == []
    set_deltas = [
        (delta.operation, delta.path, delta.value)
        for delta in result.candidate_deltas
        if delta.operation is DeltaOperation.SET
    ]
    assert (DeltaOperation.SET, "subject.count", 2) in set_deltas


def test_retry_exhausted_degrades_to_recoverable_clarify_without_state_change(
    tmp_path,
) -> None:
    """两次都 timeout → 与 v0.2 逐字一致的降级（clarify、零候选、状态不变）。"""
    session = make_p3_session(tmp_path)

    def handler(request):
        raise ProviderError.timeout("chat completion timed out after 60.0s")

    provider = FakeLLMProvider(handler)
    engine = FeedbackEngine(provider)
    request = load_feedback_request(session, feedback_text="现在把猫去掉。")
    before = request.current_intent.model_dump_json()
    result = engine.analyze(request)

    assert len(provider.requests) == MAX_FEEDBACK_ATTEMPTS == 2
    assert result.decision is FeedbackDecision.CLARIFY
    assert result.candidate_deltas == []
    assert result.preserve_paths == []
    assert result.compile_feedback is None
    assert [issue.code for issue in result.issues] == ["provider.timeout"]
    assert "retryable=True" in result.issues[0].message
    # 状态不变：请求携带的 Intent 逐字节不变（引擎不修改任何状态）。
    assert request.current_intent.model_dump_json() == before


@pytest.mark.parametrize("error", ["auth", "invalid_request"])
def test_non_retryable_provider_error_is_not_retried(tmp_path, error) -> None:
    """不可重试 Provider 错误（auth / invalid_request）立即降级，只调用一次。"""
    session = make_p3_session(tmp_path)

    def handler(request):
        raise getattr(ProviderError, error)("provider rejected the request")

    provider = FakeLLMProvider(handler)
    engine = FeedbackEngine(provider)
    result = engine.analyze(load_feedback_request(session, feedback_text="改成特写。"))

    assert len(provider.requests) == 1
    assert result.decision is FeedbackDecision.CLARIFY
    assert [issue.code for issue in result.issues] == [f"provider.{error}"]
    assert "retryable=False" in result.issues[0].message


def test_parse_failure_is_not_retried(tmp_path) -> None:
    """解析失败属被测系统行为：不重试、按冻结语义立即降级为可恢复 issue。"""
    session = make_p3_session(tmp_path)

    def handler(request):
        return "not json at all"

    provider = FakeLLMProvider(handler)
    engine = FeedbackEngine(provider)
    result = engine.analyze(load_feedback_request(session, feedback_text="改成特写。"))

    assert len(provider.requests) == 1
    assert result.decision is FeedbackDecision.CLARIFY
    assert [issue.code for issue in result.issues] == [FEEDBACK_INVALID_JSON]


# ---------------------------------------------------------------------------
# 更改书 002 · 工单 B：ReviewService 层的可恢复失败（显式、不推进状态）
# ---------------------------------------------------------------------------


def _review_with(session, engine) -> ReviewService:
    return ReviewService(
        repo=session.repo,
        feedback_engine=engine,
        question_builder=QuestionBuilder(),
    )


def _advancing_engine(session, engine):
    """包装 FeedbackEngine：`analyze` 返回后模拟并发轮次推进一条新 IntentRevision。"""
    concurrent: dict[str, str] = {}

    class _AdvancingEngine:
        def analyze(self, request):
            result = engine.analyze(request)
            current = session.repo.get_current_session_snapshot(session.session_id)
            revision = IntentRevision(
                intent_revision_id=new_id("irev"),
                session_id=session.session_id,
                parent_revision_id=current.current_intent_revision_id,
                intent=request.current_intent,
            )
            session.repo.append_intent_revision(revision)
            concurrent["revision_id"] = revision.intent_revision_id
            return result

    return _AdvancingEngine(), concurrent


def test_review_provider_timeout_is_an_explicit_recoverable_failure(tmp_path) -> None:
    """重试耗尽后 `FeedbackOutcome` 显式报告失败；状态/Intent/PIN 不变、可显式重试。"""
    session = make_p3_session(tmp_path)
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        raise ProviderError.timeout("chat completion timed out after 60.0s")

    review = _review_with(session, FeedbackEngine(FakeLLMProvider(handler)))
    before = artifact_counts(session.repo)

    outcome = review.submit_feedback(
        session.session_id, session.generation.generation_id, "make it pop"
    )

    assert calls["n"] == MAX_FEEDBACK_ATTEMPTS == 2  # 不新增第三层重试
    assert outcome.recoverable_failure is True
    assert outcome.failure_codes == ("provider.timeout",)
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_REVIEW
    assert outcome.pending_question is None
    assert outcome.feedback.decision is FeedbackDecision.CLARIFY
    after = artifact_counts(session.repo)
    assert after["intent_revisions"] == before["intent_revisions"]
    assert after["realization_states"] == before["realization_states"]


def test_stale_revise_is_not_applied_to_the_new_revision(tmp_path) -> None:
    """过期 revise：解析期间会话已推进 → 旧 Delta 绝不套到新 revision。"""
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[
                    revise_entry(
                        "composition.framing",
                        value="close_up",
                        evidence_fragment="closer",
                    )
                ],
            )
        ],
    )
    engine, concurrent = _advancing_engine(session, FeedbackEngine(session.llm.provider))
    review = _review_with(session, engine)
    before = artifact_counts(session.repo)

    outcome = review.submit_feedback(
        session.session_id, session.generation.generation_id, "closer"
    )

    assert outcome.recoverable_failure is True
    assert "workflow.stale_revision" in outcome.failure_codes
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_REVIEW
    assert outcome.snapshot.current_intent_revision_id == concurrent["revision_id"]
    assert outcome.resolution is not None
    assert outcome.resolution.applied_deltas == []
    assert outcome.resolution.ready_for_confirmation is False
    # 反馈日志保留可追溯；只有并发那一条 revision，本轮候选从未被应用。
    after = artifact_counts(session.repo)
    assert after["feedback_results"] == before["feedback_results"] + 1
    assert after["intent_revisions"] == before["intent_revisions"] + 1


def test_stale_accept_never_completes_the_session(tmp_path) -> None:
    """过期 accept：解析期间会话已推进 → 不得把会话推进到 COMPLETED。"""
    session = make_p3_session(tmp_path, feedback_responses=[feedback_response("accept")])
    engine, concurrent = _advancing_engine(session, FeedbackEngine(session.llm.provider))
    review = _review_with(session, engine)
    before = artifact_counts(session.repo)

    outcome = review.submit_feedback(
        session.session_id, session.generation.generation_id, "looks good"
    )

    assert outcome.feedback.decision is FeedbackDecision.ACCEPT
    assert outcome.recoverable_failure is True
    assert "workflow.stale_revision" in outcome.failure_codes
    # 状态仍是等待复核（未 COMPLETED），当前 revision 是并发那一条。
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_REVIEW
    assert outcome.snapshot.current_intent_revision_id == concurrent["revision_id"]
    after = artifact_counts(session.repo)
    assert after["feedback_results"] == before["feedback_results"] + 1
    assert after["intent_revisions"] == before["intent_revisions"] + 1


def test_stale_clarify_never_opens_a_new_question(tmp_path) -> None:
    """过期 clarify：解析期间会话已推进 → 不得落新 Pending Question / 切到澄清态。"""
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "clarify",
                clarify_path="environment.mode",
                clarify_reason="the user did not say what the background should be",
            )
        ],
    )
    engine, concurrent = _advancing_engine(session, FeedbackEngine(session.llm.provider))
    review = _review_with(session, engine)
    before = artifact_counts(session.repo)

    outcome = review.submit_feedback(
        session.session_id, session.generation.generation_id, "the background is not good"
    )

    assert outcome.recoverable_failure is True
    assert "workflow.stale_revision" in outcome.failure_codes
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_REVIEW
    assert outcome.pending_question is None
    assert outcome.snapshot.pending_question_id is None
    assert outcome.snapshot.current_intent_revision_id == concurrent["revision_id"]
    after = artifact_counts(session.repo)
    assert after["feedback_results"] == before["feedback_results"] + 1
    assert after["intent_revisions"] == before["intent_revisions"] + 1
