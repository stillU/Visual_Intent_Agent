"""Step 09 FeedbackEngine 测试：结构化解释、preserve → PIN、clarify、可恢复失败。

全部离线：`FakeLLMProvider` 脚本 + `tmp_path` SQLite + `FakeImageProvider`。
"""

from __future__ import annotations

import json

import pytest

from visual_intent_agent.domain import (
    DeltaOperation,
    IntentDelta,
    Resolution,
    VisualIntent,
)
from visual_intent_agent.feedback import (
    FEEDBACK_CLARIFICATION_REQUIRED,
    FEEDBACK_CLARIFICATION_TARGET_MISSING,
    FEEDBACK_CONTEXT_MISMATCH,
    FEEDBACK_EMPTY_OUTPUT,
    FEEDBACK_FULL_INTENT,
    FEEDBACK_INCONSISTENT_DECISION,
    FEEDBACK_INVALID_CLARIFY_PATH,
    FEEDBACK_INVALID_DELTA,
    FEEDBACK_INVALID_JSON,
    FEEDBACK_PROMPT_VERSION,
    FEEDBACK_SCHEMA_VIOLATION,
    FEEDBACK_UNKNOWN_PRESERVE_PATH,
    FeedbackDecision,
    FeedbackEngine,
    FeedbackError,
    FeedbackRequest,
    build_feedback_user_prompt,
    clarify_target_path,
    default_compile_feedback,
    expand_preserve_paths,
    is_unparseable,
    match_preserve_pattern,
    preserve_pin_deltas,
    read_intent_value,
)
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_llm import FakeLLMProvider

from feedback_helpers import (
    feedback_response,
    load_feedback_request,
    make_p3_session,
    p3_intent,
    revise_entry,
)


# ---------------------------------------------------------------------------
# 正向：accept / revise / clarify
# ---------------------------------------------------------------------------


def test_revise_maps_the_camera_request_and_preserves_subject_paths(tmp_path):
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[
                    revise_entry(
                        "composition.framing",
                        value="wide_shot",
                        evidence_fragment="pull the camera back",
                    )
                ],
                preserve_paths=["subject.*"],
            )
        ],
    )
    engine = FeedbackEngine(session.llm.provider)
    request = load_feedback_request(session, feedback_text="keep the person, pull back")
    result = engine.analyze(request)

    assert result.decision is FeedbackDecision.REVISE
    assert result.session_id == session.session_id
    assert result.generation_id == session.generation.generation_id
    # 反馈绑定的 Intent revision 就是该次生成所依据的 revision（确定性填写）
    assert result.intent_revision_id == request.prompt_artifact.based_on_intent_revision_id
    assert result.preserve_paths == ["subject.*"]

    by_operation = [(delta.operation, delta.path) for delta in result.candidate_deltas]
    assert (DeltaOperation.SET, "composition.framing") in by_operation
    # preserve 模式转成合法 PIN（只对有值的 subject 路径；count 无值 → 只留 preserve 记录）
    assert (DeltaOperation.PIN, "subject.description") in by_operation
    assert (DeltaOperation.PIN, "subject.pose_action") in by_operation
    assert all(path != "subject.count" for _, path in by_operation)

    framing = next(d for d in result.candidate_deltas if d.path == "composition.framing")
    assert framing.value == "wide_shot"
    # 证据的 message_id 由系统重填；fragment 来自 LLM
    assert all(
        ref.message_id == "msg_probe" for delta in result.candidate_deltas for ref in delta.evidence_refs
    )
    assert framing.evidence_refs[0].fragment == "pull the camera back"
    assert all(
        ref.pending_question_id is None
        for delta in result.candidate_deltas
        for ref in delta.evidence_refs
    )
    assert result.compile_feedback
    assert result.issues == []


def test_accept_has_no_deltas_and_no_issues(tmp_path):
    session = make_p3_session(
        tmp_path, feedback_responses=[feedback_response("accept")]
    )
    engine = FeedbackEngine(session.llm.provider)
    result = engine.analyze(load_feedback_request(session, feedback_text="perfect, done"))

    assert result.decision is FeedbackDecision.ACCEPT
    assert result.candidate_deltas == []
    assert result.preserve_paths == []
    assert result.compile_feedback is None
    assert result.issues == []
    assert result.evidence_refs[0].message_id == "msg_probe"


def test_vague_background_feedback_becomes_clarify_without_guessing(tmp_path):
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "clarify",
                clarify_path="environment.mode",
                clarify_reason="the user is unhappy with the background but did not say what it should be",
            )
        ],
    )
    engine = FeedbackEngine(session.llm.provider)
    result = engine.analyze(load_feedback_request(session, feedback_text="the background is not good"))

    assert result.decision is FeedbackDecision.CLARIFY
    assert result.candidate_deltas == []
    assert result.preserve_paths == []
    assert result.compile_feedback is None
    # 绝不猜"换成海边"：没有任何 SET location 的候选
    assert all(delta.path != "environment.location" for delta in result.candidate_deltas)
    assert clarify_target_path(result) == "environment.mode"
    carrier = [i for i in result.issues if i.code == FEEDBACK_CLARIFICATION_REQUIRED]
    assert len(carrier) == 1
    assert "did not say" in carrier[0].message


def test_clarify_without_a_locatable_path_is_not_actionable(tmp_path):
    session = make_p3_session(
        tmp_path, feedback_responses=[feedback_response("clarify")]
    )
    engine = FeedbackEngine(session.llm.provider)
    result = engine.analyze(load_feedback_request(session, feedback_text="I do not like it"))

    assert result.decision is FeedbackDecision.CLARIFY
    assert clarify_target_path(result) is None
    assert [issue.code for issue in result.issues] == [FEEDBACK_CLARIFICATION_TARGET_MISSING]
    assert result.issues[0].severity.value == "error"


# ---------------------------------------------------------------------------
# 负向：解析失败 → 可恢复 issue，不抛异常
# ---------------------------------------------------------------------------


def _engine_with(responses: list[str]):
    provider = FakeLLMProvider(responses)
    return FeedbackEngine(provider), provider


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ("", FEEDBACK_EMPTY_OUTPUT),
        ("not json at all", FEEDBACK_INVALID_JSON),
        (json.dumps({"unexpected": True}), FEEDBACK_SCHEMA_VIOLATION),
        (json.dumps({"decision": "maybe"}), FEEDBACK_SCHEMA_VIOLATION),
        (json.dumps({"schema_version": "v1", "subject": {"description": "cat"}}), FEEDBACK_FULL_INTENT),
        (json.dumps({"decision": "revise"}), FEEDBACK_INCONSISTENT_DECISION),
        (
            json.dumps({"decision": "accept", "candidate_deltas": [revise_entry("composition.framing", value="x")]}),
            FEEDBACK_INCONSISTENT_DECISION,
        ),
        (
            json.dumps({"decision": "clarify", "candidate_deltas": [revise_entry("composition.framing", value="x")]}),
            FEEDBACK_INCONSISTENT_DECISION,
        ),
        (
            json.dumps({"decision": "clarify", "clarify_path": "subject.clothing"}),
            FEEDBACK_INVALID_CLARIFY_PATH,
        ),
        (
            json.dumps(
                {
                    "decision": "revise",
                    "candidate_deltas": [
                        {"operation": "CLEAR", "path": "composition.framing", "value": "nope"}
                    ],
                }
            ),
            FEEDBACK_INVALID_DELTA,
        ),
    ],
)
def test_unparseable_output_becomes_a_recoverable_issue(tmp_path, payload, expected_code):
    session = make_p3_session(tmp_path, feedback_responses=[payload])
    engine = FeedbackEngine(session.llm.provider)
    result = engine.analyze(load_feedback_request(session))

    assert result.decision is FeedbackDecision.CLARIFY
    assert result.candidate_deltas == []
    assert result.preserve_paths == []
    assert [issue.code for issue in result.issues] == [expected_code]
    assert is_unparseable(result) is True
    assert clarify_target_path(result) is None


def test_provider_failure_becomes_a_recoverable_issue(tmp_path):
    session = make_p3_session(tmp_path)

    def boom(request):
        raise ProviderError.timeout("upstream timed out")

    engine = FeedbackEngine(FakeLLMProvider(boom))
    result = engine.analyze(load_feedback_request(session))

    assert result.decision is FeedbackDecision.CLARIFY
    assert result.candidate_deltas == []
    assert result.issues[0].code == "provider.timeout"
    assert "retryable=True" in result.issues[0].message
    assert is_unparseable(result) is False


def test_empty_feedback_text_is_reported_without_calling_the_llm(tmp_path):
    session = make_p3_session(tmp_path)
    engine, provider = _engine_with([feedback_response("accept")])
    result = engine.analyze(load_feedback_request(session, feedback_text="   "))

    assert [issue.code for issue in result.issues] == [FEEDBACK_EMPTY_OUTPUT]
    assert provider.requests == []


def test_unknown_preserve_pattern_is_reported_and_ignored(tmp_path):
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[revise_entry("composition.framing", value="wide_shot")],
                preserve_paths=["subject.*", "clothing.*", "*"],
            )
        ],
    )
    engine = FeedbackEngine(session.llm.provider)
    result = engine.analyze(load_feedback_request(session))

    assert result.preserve_paths == ["subject.*", "clothing.*", "*"]
    unknown = [i for i in result.issues if i.code == FEEDBACK_UNKNOWN_PRESERVE_PATH]
    assert len(unknown) == 1
    assert "clothing.*" in unknown[0].message and "*" in unknown[0].message
    # 只有白名单命中路径被 PIN
    assert {d.path for d in result.candidate_deltas if d.operation is DeltaOperation.PIN} == {
        "subject.description",
        "subject.pose_action",
    }


# ---------------------------------------------------------------------------
# 上下文（程序级失败）
# ---------------------------------------------------------------------------


def test_generation_and_prompt_must_belong_together(tmp_path):
    session = make_p3_session(tmp_path)
    engine, _ = _engine_with([feedback_response("accept")])
    request = load_feedback_request(session)
    broken = request.model_copy(
        update={
            "generation": request.generation.model_copy(
                update={"prompt_artifact_id": "pra_other"}
            )
        }
    )
    with pytest.raises(FeedbackError) as excinfo:
        engine.analyze(broken)
    assert excinfo.value.code == FEEDBACK_CONTEXT_MISMATCH


def test_cross_session_artifacts_are_rejected(tmp_path):
    session = make_p3_session(tmp_path)
    engine, _ = _engine_with([feedback_response("accept")])
    request = load_feedback_request(session)
    broken = request.model_copy(
        update={"generation": request.generation.model_copy(update={"session_id": "ses_other"})}
    )
    with pytest.raises(FeedbackError):
        engine.analyze(broken)


# ---------------------------------------------------------------------------
# 请求上下文与提示词
# ---------------------------------------------------------------------------


def test_engine_never_modifies_the_request_intent(tmp_path):
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[revise_entry("composition.framing", value="wide_shot")],
                preserve_paths=["subject.*"],
            )
        ],
    )
    engine = FeedbackEngine(session.llm.provider)
    request = load_feedback_request(session)
    before = request.current_intent.model_dump_json()
    engine.analyze(request)
    assert request.current_intent.model_dump_json() == before


def test_user_prompt_contains_the_generation_binding_and_the_feedback_text(tmp_path):
    session = make_p3_session(tmp_path)
    request = load_feedback_request(session, feedback_text="make the framing wider")
    prompt = build_feedback_user_prompt(request)

    assert FEEDBACK_PROMPT_VERSION == "feedback.v1"
    assert f"generation_id: {session.generation.generation_id}" in prompt
    assert f"prompt_artifact_id: {session.generation.prompt_artifact_id}" in prompt
    assert "ALLOWED PATHS" in prompt
    assert "make the framing wider" in prompt
    assert "CURRENT INTENT" in prompt
    assert "REALIZATION" in prompt


def test_engine_uses_json_object_response_format(tmp_path):
    session = make_p3_session(tmp_path, feedback_responses=[feedback_response("accept")])
    engine = FeedbackEngine(session.llm.provider)
    engine.analyze(load_feedback_request(session))
    request = session.llm.requests[-1]
    assert request.response_format == {"type": "json_object"}
    assert request.messages[0].role == "system"
    assert "Feedback Interpreter" in request.messages[0].content
    assert "untrusted data" in request.messages[1].content


def test_fenced_json_is_normalized(tmp_path):
    payload = "```json\n" + feedback_response("accept") + "\n```"
    session = make_p3_session(tmp_path, feedback_responses=[payload])
    engine = FeedbackEngine(session.llm.provider)
    result = engine.analyze(load_feedback_request(session))
    assert result.decision is FeedbackDecision.ACCEPT


# ---------------------------------------------------------------------------
# 确定性纯函数
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("composition.framing", ("composition.framing",)),
        ("subject.*", ("subject.count", "subject.description", "subject.pose_action")),
        ("*", None),
        ("clothing.*", None),
        ("subject.clothing", None),
        ("", None),
    ],
)
def test_match_preserve_pattern(pattern, expected):
    assert match_preserve_pattern(pattern) == expected


def test_expand_preserve_paths_is_deterministic_and_reports_unknown():
    paths, unknown = expand_preserve_paths(["subject.*", "nope.*", "subject.*", " "])
    assert paths == ("subject.count", "subject.description", "subject.pose_action")
    assert unknown == ("nope.*",)


def test_preserve_pin_deltas_only_pins_paths_with_a_current_value():
    intent = p3_intent()
    deltas = preserve_pin_deltas(
        ("subject.count", "subject.description"),
        intent,
        [],
    )
    assert [(delta.operation, delta.path) for delta in deltas] == [
        (DeltaOperation.PIN, "subject.description")
    ]
    assert read_intent_value(intent, "subject.count") is None
    assert read_intent_value(intent, "subject.description") == "a cat"


def test_default_compile_feedback_renders_deltas_and_preserve_paths():
    delta = IntentDelta(
        operation=DeltaOperation.SET, path="composition.framing", value="wide_shot"
    )
    text = default_compile_feedback([delta], ["subject.*"])
    assert "SET composition.framing=wide_shot" in text
    assert "preserve subject.*" in text


def test_delegated_style_value_is_never_treated_as_a_user_fact():
    intent = p3_intent()
    assert intent.resolutions["lighting.character"].resolution is Resolution.USER_DELEGATED
    assert read_intent_value(intent, "lighting.character") is None
    # 无值 → 不会被 PIN（preserve 只是记录，不是新值）
    assert preserve_pin_deltas(("lighting.character",), intent, []) == []


def test_request_rejects_unknown_fields_for_programmers():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        FeedbackRequest(
            session_id="ses_1",
            message_id="msg_1",
            feedback_text="x",
            generation=None,
            prompt_artifact=None,
            current_intent=VisualIntent(),
            bogus=True,
        )
