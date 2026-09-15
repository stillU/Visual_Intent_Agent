"""任务书「必测场景」逐条映射（Step 03 的验收主线）。"""

from __future__ import annotations

from policy_helpers import (
    empty_intent,
    intent_from,
    ready_intent,
    with_resolution,
)
from visual_intent_agent.policy import Materiality, PolicyAction, assess


def _actions(result) -> dict[str, str]:
    return {decision.path: decision.action.value for decision in result.unresolved_decisions}


# 1. 空 Intent 不可 Ready。
def test_scenario_1_empty_intent_is_never_ready() -> None:
    result = assess(empty_intent())
    assert result.ready_for_confirmation is False
    assert result.question is not None
    assert result.question.target_path == "subject.description"
    assert result.unresolved_decisions, "空 Intent 必须暴露全部未解决决策"
    assert any(decision.action is PolicyAction.BLOCK for decision in result.unresolved_decisions)


# 2. 只有 subject 但核心必需项缺失时不可 Ready。
def test_scenario_2_subject_alone_leaves_core_decisions_missing() -> None:
    result = assess(intent_from({"subject.description": "a lone astronaut"}))
    assert result.ready_for_confirmation is False
    actions = _actions(result)
    assert actions["style.primary"] == "block"
    assert actions["environment.mode"] == "block"
    # 唯一问题按 core 优先级选：subject 之后的第一个 core 缺失项。
    assert result.question is not None
    assert result.question.target_path == "style.primary"


# 3. 用户明确 delegated 且 path 可委托时视为已解决。
def test_scenario_3_delegation_on_a_delegatable_path_resolves() -> None:
    intent = with_resolution(ready_intent(), "camera.angle", "user_delegated")
    result = assess(intent)
    assert result.issues == []
    assert "camera.angle" not in _actions(result)
    assert result.ready_for_confirmation is True


# 4. 不可委托字段被标记 delegated 时返回 issue。
def test_scenario_4_delegation_on_a_non_delegatable_path_returns_an_issue() -> None:
    result = assess(with_resolution(empty_intent(), "subject.description", "user_delegated"))
    assert [issue.code for issue in result.issues] == ["policy.delegation_not_allowed"]
    assert result.ready_for_confirmation is False


# 5. not_applicable 不满足规则条件时返回 issue。
def test_scenario_5_not_applicable_without_a_matching_rule_condition_returns_an_issue() -> None:
    result = assess(with_resolution(empty_intent(), "style.primary", "not_applicable"))
    assert "policy.not_applicable_not_allowed" in [issue.code for issue in result.issues]
    assert result.ready_for_confirmation is False


# 6. omit 的字段不阻塞，但仍保持 unspecified。
def test_scenario_6_omitted_fields_do_not_block_and_stay_unspecified() -> None:
    intent = ready_intent()
    result = assess(intent)
    assert result.ready_for_confirmation is True
    omitted = {
        decision.path
        for decision in result.unresolved_decisions
        if decision.action is PolicyAction.OMIT
    }
    assert omitted == {"camera.angle", "camera.depth_of_field", "color.palette"}
    # 不具体化：Intent 原样返回，omit 路径仍为 None。
    assert result.intent is intent
    assert result.intent.camera.angle is None
    assert result.intent.camera.depth_of_field is None
    assert result.intent.color.palette is None


# 7. R1-A 停用"摄影棚 × 户外地点"硬冲突后，该组合不再是冲突；缺失决策照常阻塞。
def test_scenario_7_retired_conflict_combination_does_not_override_missing_decisions() -> None:
    intent = intent_from(
        {
            "subject.description": "a lone astronaut",
            "environment.mode": "studio",
            "environment.location": "an outdoor street",
        }
    )
    result = assess(intent)
    assert result.conflicts == [], "该组合在 policy.v2 中不再判定为冲突"
    assert result.ready_for_confirmation is False
    assert result.question is not None
    # 冲突停用后，问题回到第一个缺失的 core 决策（style.primary）。
    assert result.question.target_path == "style.primary"
    assert _actions(result)["style.primary"] == "block"


# 8. 所有 block 项解决且无冲突时 Ready。
def test_scenario_8_ready_when_every_block_is_resolved_and_no_conflict() -> None:
    result = assess(ready_intent())
    assert result.ready_for_confirmation is True
    assert result.question is None
    assert result.issues == []
    assert result.conflicts == []
    assert not any(
        decision.action is PolicyAction.BLOCK for decision in result.unresolved_decisions
    )
    assert all(
        decision.materiality in {Materiality.CORE, Materiality.PERCEPTUAL}
        for decision in result.unresolved_decisions
    )


# 9. Policy 结果不依赖 LLM 或随机数。
def test_scenario_9_results_do_not_depend_on_llm_or_randomness() -> None:
    intent = intent_from(
        {"subject.description": "a lone astronaut", "environment.mode": "studio"}
    )
    first = assess(intent)
    second = assess(intent)
    third = assess(intent, None)
    assert first.model_dump_json() == second.model_dump_json() == third.model_dump_json()
    # 输入对象在三次调用后仍逐值不变。
    assert intent == intent_from(
        {"subject.description": "a lone astronaut", "environment.mode": "studio"}
    )


def test_scenario_9_dict_insertion_order_does_not_change_the_result() -> None:
    ordered = with_resolution(
        with_resolution(empty_intent(), "style.primary", "user_delegated"),
        "color.palette",
        "not_applicable",
    )
    reversed_records = dict(reversed(list(ordered.resolutions.items())))
    shuffled = ordered.model_copy(update={"resolutions": reversed_records})

    first = assess(ordered)
    second = assess(shuffled)
    # 派生结果与 resolutions 的插入顺序无关（issue 按路径排序输出）。
    assert [issue.model_dump() for issue in first.issues] == [
        issue.model_dump() for issue in second.issues
    ]
    assert [decision.model_dump() for decision in first.unresolved_decisions] == [
        decision.model_dump() for decision in second.unresolved_decisions
    ]
    assert first.question == second.question
    assert first.ready_for_confirmation == second.ready_for_confirmation
    # 同一个输入重复调用逐字节稳定。
    assert assess(ordered).model_dump_json() == first.model_dump_json()


# 验收条件 2：每个 block 都能说明 path 和原因。
def test_every_block_decision_explains_its_path_and_rule() -> None:
    for intent in (
        empty_intent(),
        intent_from({"subject.description": "a lone astronaut"}),
        intent_from({"subject.description": "a lone astronaut", "environment.mode": "studio"}),
    ):
        result = assess(intent)
        for decision in result.unresolved_decisions:
            if decision.action is not PolicyAction.BLOCK:
                continue
            assert decision.path in decision.reason
            assert "required_if=" in decision.reason
            assert "unresolved" in decision.reason


# 验收条件 4：ready_for_confirmation 完全由 block 项与 Hard Conflict 决定。
def test_ready_flag_is_derived_only_from_block_decisions_and_hard_conflicts() -> None:
    from visual_intent_agent.policy.decision_policy import is_hard_conflict

    candidates = (
        empty_intent(),
        ready_intent(),
        intent_from({"subject.description": "a lone astronaut"}),
        intent_from({"environment.mode": "studio", "environment.location": "an outdoor street"}),
        with_resolution(ready_intent(), "camera.angle", "user_delegated"),
    )
    for intent in candidates:
        result = assess(intent)
        blocking = any(
            decision.action is PolicyAction.BLOCK for decision in result.unresolved_decisions
        )
        hard = any(is_hard_conflict(issue) for issue in result.conflicts)
        assert result.ready_for_confirmation is (not blocking and not hard)
        assert (result.question is None) is result.ready_for_confirmation
