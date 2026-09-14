"""IntentEngine 必测场景（任务书「必测场景」8 条）+ 关键不变量。

全部使用 FakeLLMProvider：确定性、零网络、零数据库。
"""

from __future__ import annotations

import ie_helpers as h

from visual_intent_agent.domain import VisualIntent
from visual_intent_agent.intent_engine.models import (
    INTERPRETER_FULL_INTENT,
    INTERPRETER_UNRESOLVED_LANGUAGE,
)
from visual_intent_agent.validation.validator import (
    EVIDENCE_PENDING_QUESTION_PATH_MISMATCH,
    PATH_NOT_WHITELISTED,
    UNAUTHORIZED_RESOLUTION_CLAIM,
)

# ---------------------------------------------------------------------------
# 场景 1：「镜头拉远」只产生 SET composition.framing
# ---------------------------------------------------------------------------


def test_scenario_1_zoom_out_only_sets_composition_framing() -> None:
    before = VisualIntent()
    text = h.script(
        [
            h.delta(
                "SET",
                "composition.framing",
                value="wide shot",
                evidence_fragment="镜头拉远",
            )
        ]
    )
    resolution, _ = h.run(h.make_request(before, message_text="镜头拉远"), text)

    after = resolution.intent
    assert h.changed_paths(before, after) == {"composition.framing"}
    assert after.composition.framing == "wide shot"
    assert h.delta_paths(resolution.applied_deltas) == ["composition.framing"]
    assert resolution.applied_deltas[0].operation.value == "SET"


# ---------------------------------------------------------------------------
# 场景 2：「人物不变」产生 subject 范围 PIN，且不改变 subject 值
# ---------------------------------------------------------------------------


def test_scenario_2_keep_the_person_pins_subject_without_changing_values() -> None:
    before = VisualIntent(subject={"description": "a woman in a red coat"})  # type: ignore[arg-type]
    text = h.script(
        [
            h.delta(
                "PIN",
                "subject.description",
                evidence_fragment="人物不变",
            )
        ]
    )
    resolution, _ = h.run(h.make_request(before, message_text="人物不变"), text)

    after = resolution.intent
    assert after.pinned_paths == frozenset({"subject.description"})
    assert after.subject.description == "a woman in a red coat"
    assert h.changed_paths(before, after) == set()
    assert [delta.operation.value for delta in resolution.applied_deltas] == ["PIN"]
    assert resolution.applied_deltas[0].path.startswith("subject")


# ---------------------------------------------------------------------------
# 场景 3：「光线你决定」只委托 lighting 路径
# ---------------------------------------------------------------------------


def test_scenario_3_you_decide_lighting_only_delegates_lighting() -> None:
    pending = h.pending_question(
        "lighting.character",
        allow_delegate=True,
        suggested_values=("warm side light", "cool blue hour"),
    )
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    text = h.script(
        [
            h.delta(
                "SET",
                "lighting.character",
                resolution="user_delegated",
                answers_pending_question=True,
                evidence_fragment="光线你决定",
            )
        ]
    )
    resolution, _ = h.run(
        h.make_request(before, message_text="光线你决定", pending_question=pending), text
    )

    after = resolution.intent
    assert set(after.resolutions) == {"lighting.character"}
    assert h.resolution_map(after) == {"lighting.character": "user_delegated"}
    assert h.changed_paths(before, after) == set()
    assert "policy.delegation_not_allowed" not in h.issue_codes(resolution)
    assert h.delta_paths(resolution.applied_deltas) == ["lighting.character"]


def test_scenario_3b_delegation_on_a_non_delegatable_path_is_flagged() -> None:
    pending = h.pending_question("subject.description", allow_delegate=False)
    text = h.script(
        [
            h.delta(
                "SET",
                "subject.description",
                resolution="user_delegated",
                answers_pending_question=True,
                evidence_fragment="你决定",
            )
        ]
    )
    resolution, _ = h.run(
        h.make_request(message_text="你决定", pending_question=pending), text
    )
    assert "policy.delegation_not_allowed" in h.issue_codes(resolution)


# ---------------------------------------------------------------------------
# 场景 4：「背景不要海边」无法映射为明确新 location → 需澄清，不猜值
# ---------------------------------------------------------------------------


def test_scenario_4_negative_background_without_a_target_asks_for_clarification() -> None:
    before = VisualIntent(environment={"mode": "outdoor"})  # type: ignore[arg-type]
    text = h.script(
        unresolved_language=["背景不要海边（用户没有说明想要的地点）"],
    )
    resolution, _ = h.run(h.make_request(before, message_text="背景不要海边"), text)

    after = resolution.intent
    assert after.environment.location is None
    assert h.changed_paths(before, after) == set()
    assert resolution.applied_deltas == []
    assert INTERPRETER_UNRESOLVED_LANGUAGE in h.issue_codes(resolution)
    issue = next(
        issue for issue in resolution.issues if issue.code == INTERPRETER_UNRESOLVED_LANGUAGE
    )
    assert issue.severity.value == "warning"
    # 不猜城市/森林：没有任何 location 值被写入。
    assert "forest" not in after.model_dump_json()
    assert "city" not in after.model_dump_json()


# ---------------------------------------------------------------------------
# 场景 5：LLM 输出非法路径 → Validator 拒绝
# ---------------------------------------------------------------------------


def test_scenario_5_illegal_path_is_rejected_by_the_validator() -> None:
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    text = h.script(
        [h.delta("SET", "subject.hair_color", value="red", evidence_fragment="头发改成红色")]
    )
    resolution, _ = h.run(h.make_request(before, message_text="头发改成红色"), text)

    assert resolution.intent.model_dump_json() == before.model_dump_json()
    assert resolution.applied_deltas == []
    assert PATH_NOT_WHITELISTED in h.issue_codes(resolution)


# ---------------------------------------------------------------------------
# 场景 6：LLM 输出完整 Intent → 解析失败（可恢复，不猜测修复）
# ---------------------------------------------------------------------------


def test_scenario_6_full_intent_output_fails_to_parse() -> None:
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    text = VisualIntent(subject={"description": "a cat"}).model_dump_json()  # type: ignore[arg-type]
    resolution, _ = h.run(h.make_request(before, message_text="换成一只猫"), text)

    assert resolution.intent.model_dump_json() == before.model_dump_json()
    assert resolution.applied_deltas == []
    assert resolution.ready_for_confirmation is False
    assert INTERPRETER_FULL_INTENT in h.issue_codes(resolution)


# ---------------------------------------------------------------------------
# 场景 7：Pending Question 为 lighting 时，「第二个」只作用于 lighting
# ---------------------------------------------------------------------------


def test_scenario_7_second_option_only_touches_the_pending_lighting_path() -> None:
    pending = h.pending_question(
        "lighting.character",
        suggested_values=("warm side light", "cool blue hour"),
    )
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    text = h.script(
        [
            h.delta(
                "SET",
                "lighting.character",
                value="cool blue hour",
                answers_pending_question=True,
                evidence_fragment="第二个",
            )
        ]
    )
    resolution, provider = h.run(
        h.make_request(before, message_text="第二个", pending_question=pending), text
    )

    after = resolution.intent
    assert after.lighting.character == "cool blue hour"
    assert h.changed_paths(before, after) == {"lighting.character"}
    assert set(after.resolutions) == {"lighting.character"}

    # 局部上下文确实注入了 pending path 与候选项（LLM 才可能只在该范围解释）。
    user_prompt = provider.requests[0].messages[1].content
    assert "- target_path: lighting.character" in user_prompt
    assert "cool blue hour" in user_prompt
    assert "第二个" in user_prompt


def test_scenario_7b_scope_expansion_on_a_pending_answer_is_rejected() -> None:
    pending = h.pending_question("lighting.character")
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    text = h.script(
        [
            h.delta(
                "SET",
                "color.palette",
                value="teal",
                answers_pending_question=True,
                evidence_fragment="第二个",
            )
        ]
    )
    resolution, _ = h.run(
        h.make_request(before, message_text="第二个", pending_question=pending), text
    )

    assert resolution.intent.model_dump_json() == before.model_dump_json()
    assert resolution.applied_deltas == []
    assert EVIDENCE_PENDING_QUESTION_PATH_MISMATCH in h.issue_codes(resolution)


# ---------------------------------------------------------------------------
# 场景 8：相同输入经 Fake Provider 得到稳定 IntentResolution
# ---------------------------------------------------------------------------


def test_scenario_8_same_input_yields_a_stable_intent_resolution() -> None:
    pending = h.pending_question(
        "lighting.character", suggested_values=("warm side light", "cool blue hour")
    )
    request = h.make_request(
        VisualIntent(subject={"description": "a woman"}),  # type: ignore[arg-type]
        message_text="第二个",
        pending_question=pending,
    )
    text = h.script(
        [
            h.delta(
                "SET",
                "lighting.character",
                value="cool blue hour",
                answers_pending_question=True,
                evidence_fragment="第二个",
            )
        ]
    )
    first, _ = h.run(request, text)
    second, _ = h.run(request, text)
    assert first.model_dump_json() == second.model_dump_json()


# ---------------------------------------------------------------------------
# 不变量
# ---------------------------------------------------------------------------


def test_rejected_deltas_are_never_silently_swallowed() -> None:
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    text = h.script(
        [
            h.delta("SET", "composition.framing", value="wide", evidence_fragment="镜头拉远"),
            h.delta("SET", "subject.hair_color", value="red", evidence_fragment="头发红"),
        ]
    )
    resolution, _ = h.run(h.make_request(before, message_text="镜头拉远，头发红"), text)

    # 合法 Delta 生效，非法 Delta 留下显式 issue。
    assert h.delta_paths(resolution.applied_deltas) == ["composition.framing"]
    assert resolution.intent.composition.framing == "wide"
    assert PATH_NOT_WHITELISTED in h.issue_codes(resolution)


def test_every_applied_delta_traces_back_to_the_user_message() -> None:
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    text = h.script(
        [
            h.delta("SET", "composition.framing", value="wide", evidence_fragment="镜头拉远"),
            h.delta("PIN", "subject.description", evidence_fragment="人物不变"),
        ]
    )
    resolution, _ = h.run(
        h.make_request(before, message_text="镜头拉远，人物不变"), text
    )

    assert len(resolution.applied_deltas) == 2
    for delta in resolution.applied_deltas:
        assert delta.evidence_refs
        for ref in delta.evidence_refs:
            assert ref.message_id == h.MESSAGE_ID


def test_silence_is_never_interpreted_as_delegation() -> None:
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    text = h.script(
        [
            h.delta(
                "SET",
                "style.primary",
                resolution="user_delegated",
                evidence_fragment="人物不变",
            )
        ]
    )
    resolution, _ = h.run(h.make_request(before, message_text="人物不变"), text)

    assert resolution.intent.model_dump_json() == before.model_dump_json()
    assert resolution.applied_deltas == []
    assert UNAUTHORIZED_RESOLUTION_CLAIM in h.issue_codes(resolution)


def test_a_new_message_may_still_change_other_paths_while_a_question_is_pending() -> None:
    """待答问题只约束"回答它的证据"；普通消息仍可提出其它路径的新修改。"""
    pending = h.pending_question("lighting.character")
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    text = h.script(
        [
            h.delta(
                "SET",
                "composition.framing",
                value="close up",
                answers_pending_question=False,
                evidence_fragment="顺便改成特写",
            )
        ]
    )
    resolution, _ = h.run(
        h.make_request(before, message_text="顺便改成特写", pending_question=pending), text
    )
    assert resolution.intent.composition.framing == "close up"
    assert h.delta_paths(resolution.applied_deltas) == ["composition.framing"]


def test_policy_issues_and_validation_issues_are_merged() -> None:
    before = VisualIntent(subject={"description": "a woman"})  # type: ignore[arg-type]
    text = h.script(
        [
            h.delta("SET", "subject.hair_color", value="red", evidence_fragment="头发红"),
            h.delta(
                "SET",
                "style.primary",
                value="not applicable here",
                resolution="not_applicable",
                evidence_fragment="风格不适用",
            ),
        ]
    )
    resolution, _ = h.run(h.make_request(before, message_text="头发红，风格不适用"), text)
    codes = h.issue_codes(resolution)
    assert PATH_NOT_WHITELISTED in codes  # validation.*
    assert "policy.not_applicable_not_allowed" in codes  # policy.*
    # 被接受但违反 policy 的 Delta 仍会进入 Reducer（policy 只报告、不静默回滚）。
    assert h.delta_paths(resolution.applied_deltas) == ["style.primary"]
