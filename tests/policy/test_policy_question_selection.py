"""下一待询问目标测试：唯一问题、冻结优先级、QuestionSpec 形状。"""

from __future__ import annotations

import pytest

from policy_helpers import (
    READY_VALUES,
    empty_intent,
    execution,
    intent_from,
    ready_intent,
    with_resolution,
)
from visual_intent_agent.policy import QuestionSpec, assess


def test_every_assessment_produces_at_most_one_question() -> None:
    for intent in (empty_intent(), ready_intent(), intent_from({"subject.description": "x"})):
        result = assess(intent)
        assert result.question is None or isinstance(result.question, QuestionSpec)


def test_ready_resolution_has_no_question() -> None:
    assert assess(ready_intent()).question is None


def test_empty_intent_asks_for_the_first_core_decision() -> None:
    result = assess(empty_intent())
    assert result.question is not None
    assert result.question.target_path == "subject.description"


def test_core_missing_outranks_perceptual_missing() -> None:
    intent = intent_from(
        {
            "subject.description": "a lone astronaut",
            "environment.mode": "studio",
            "environment.location": "seamless grey backdrop",
        }
    )
    result = assess(intent)
    # style.primary（core）在 framing / pose_action（perceptual）之前。
    assert result.question is not None
    assert result.question.target_path == "style.primary"


def test_perceptual_missing_outranks_execution_conflict() -> None:
    values = dict(READY_VALUES)
    del values["subject.pose_action"]  # perceptual 缺失
    values["composition.framing"] = "wide shot"  # 与 1024x1024 冲突
    result = assess(intent_from(values), execution("1024x1024"))
    assert [issue.code for issue in result.conflicts] == [
        "policy.execution_conflict.framing_aspect_mismatch"
    ]
    assert result.question is not None
    assert result.question.target_path == "subject.pose_action"


def test_hard_conflict_outranks_core_missing_for_the_question() -> None:
    intent = intent_from(
        {"environment.mode": "studio", "environment.location": "an outdoor street"}
    )
    result = assess(intent)
    assert result.question is not None
    assert result.question.target_path == "environment.mode"
    assert result.question.reason.startswith("environment.mode declares")


def test_question_allow_delegate_follows_the_decision_policy() -> None:
    # subject.description 是唯一 delegatable=False 的决策。
    empty_question = assess(empty_intent()).question
    assert empty_question is not None
    assert empty_question.allow_delegate is False

    subject_only = intent_from({"subject.description": "a lone astronaut"})
    next_question = assess(subject_only).question
    assert next_question is not None
    assert next_question.target_path == "style.primary"
    assert next_question.allow_delegate is True


def test_question_for_a_delegated_missing_path_still_uses_its_policy() -> None:
    # environment.location 是 Environment 的成员路径：allow_delegate 继承自该决策。
    intent = intent_from(
        {
            "subject.description": "a lone astronaut",
            "style.primary": "cinematic realism",
            "environment.mode": "studio",
        }
    )
    result = assess(intent)
    assert result.question is not None
    assert result.question.target_path == "environment.location"
    assert result.question.allow_delegate is True


def test_question_spec_is_left_for_step_06_to_render() -> None:
    result = assess(empty_intent())
    assert result.question is not None
    assert result.question.question_text is None
    assert result.question.question_id is None
    assert result.question.allow_custom is True


def test_suggested_values_come_from_the_policy_table() -> None:
    subject_only = intent_from({"subject.description": "a lone astronaut"})
    question = assess(subject_only).question
    assert question is not None
    assert question.suggested_values == ("photorealistic", "cinematic", "illustration")


def test_open_ended_decisions_have_no_suggested_values() -> None:
    question = assess(empty_intent()).question
    assert question is not None
    assert question.suggested_values == ()


def test_conflict_questions_have_no_suggested_values() -> None:
    intent = intent_from(
        {"environment.mode": "studio", "environment.location": "an outdoor street"}
    )
    question = assess(intent).question
    assert question is not None
    assert question.suggested_values == ()
    assert question.target_path == "environment.mode"


def test_question_reason_is_deterministic_and_names_the_rule() -> None:
    question = assess(empty_intent()).question
    assert question is not None
    assert "required_if='always'" in question.reason
    assert "subject.description" in question.reason


@pytest.mark.parametrize("path", ["subject.count", "style.description"])
def test_supplementary_paths_never_become_questions(path: str) -> None:
    # 补充路径不是 Decision：即使有非法 Resolution 也永远不是待询问目标。
    intent = with_resolution(empty_intent(), path, "user_delegated")
    result = assess(intent)
    assert result.question is not None
    assert result.question.target_path != path


def test_question_for_each_block_decision_is_its_own_path() -> None:
    result = assess(empty_intent())
    blocking = {
        decision.path
        for decision in result.unresolved_decisions
        if decision.action.value == "block"
    }
    assert result.question is not None
    assert result.question.target_path in blocking
