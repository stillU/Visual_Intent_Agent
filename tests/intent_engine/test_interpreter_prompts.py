"""Interpreter prompt / schema 版本记录测试（交付物「Interpreter prompt / schema 版本记录」）。"""

from __future__ import annotations

import json

import ie_helpers as h

from visual_intent_agent.domain import INTENT_PATHS
from visual_intent_agent.intent_engine.prompts import (
    INTERPRETER_OUTPUT_SCHEMA,
    INTERPRETER_PROMPT_VERSION,
    INTERPRETER_SYSTEM_PROMPT_V1,
    build_interpreter_user_prompt,
)


def test_prompt_version_is_recorded() -> None:
    # Step 06 · P1/P4：系统提示词新增边界约束（不变量/数量/处所）→ interpreter.v2；
    # Step 06 patch 001：收窄 v2 措辞的过度泛化（明示地点必须提取、最小≠更少）→ v3；
    # Step 06 patch 002（P6 超时缓解）：保语义精简（关键句逐字保留）→ v4；
    # MVP v0.3 更改书 001 · R1-A：修正"一只猫也不得输出数量"的错误口径（明确数词必须
    # 提取、未说数量才留空）并明确自由处所不推断房间 → v5
    # （修订理由见 REVISION_001_SHORTEST_PATH.md §R1-A 与 v0_3_r2_A_handoff.md）。
    assert INTERPRETER_PROMPT_VERSION == "interpreter.v5"


def test_system_prompt_states_every_boundary_the_task_requires() -> None:
    prompt = INTERPRETER_SYSTEM_PROMPT_V1
    # 只能输出候选 Delta，不能输出完整 Intent。
    assert "candidate_deltas" in prompt
    assert "Never return a full Intent" in prompt
    # 证据要求。
    assert "evidence_fragment" in prompt
    # 沉默不是委托。
    assert "Silence" in prompt and "NEVER delegation" in prompt
    # Pending Question 局部范围（含短回答例子）。
    assert "第二个" in prompt and "就按你推荐的" in prompt and "你决定" in prompt
    # 不猜值。
    assert "unresolved_language" in prompt
    # 不输出系统字段。
    assert "Never output IDs" in prompt
    # prompt injection 防护。
    assert "untrusted data" in prompt


def test_output_schema_top_level_contract() -> None:
    schema = INTERPRETER_OUTPUT_SCHEMA
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["candidate_deltas"]
    assert set(schema["properties"]) == {
        "candidate_deltas",
        "detected_conflicts",
        "unresolved_language",
    }


def test_output_schema_delta_contract() -> None:
    item = INTERPRETER_OUTPUT_SCHEMA["properties"]["candidate_deltas"]["items"]
    assert item["additionalProperties"] is False
    assert item["required"] == ["operation", "path"]
    assert set(item["properties"]) == {
        "operation",
        "path",
        "value",
        "resolution",
        "answers_pending_question",
        "evidence_fragment",
    }
    assert item["properties"]["operation"]["enum"] == ["SET", "CLEAR", "PIN", "UNPIN"]
    # path 故意不限定 enum：非法路径必须由确定性 Validator 拒绝，而不是 Provider 层吞掉。
    assert item["properties"]["path"] == {"type": "string"}
    assert set(item["properties"]["resolution"]["enum"]) == {
        "user_specified",
        "user_confirmed_proposal",
        "user_delegated",
        "not_applicable",
        None,
    }


def test_output_schema_is_json_serializable() -> None:
    text = json.dumps(INTERPRETER_OUTPUT_SCHEMA, ensure_ascii=False)
    assert "candidate_deltas" in text


def test_user_prompt_injects_intent_message_and_whitelist() -> None:
    request = h.make_request(message_text="镜头拉远")
    prompt = build_interpreter_user_prompt(request)
    assert request.current_intent.model_dump_json() in prompt
    assert "镜头拉远" in prompt
    assert request.message_id in prompt
    for path in INTENT_PATHS:
        assert f"- {path}" in prompt
    assert "PENDING QUESTION:\n- none" in prompt


def test_user_prompt_injects_pending_question_scope_and_options() -> None:
    pending = h.pending_question(
        "lighting.character",
        allow_delegate=False,
        suggested_values=("warm side light", "cool blue hour"),
        reason="lighting must be decided",
        question_text="想要哪种光线？",
    )
    request = h.make_request(message_text="第二个", pending_question=pending)
    prompt = build_interpreter_user_prompt(request)

    assert "- question_id: qst_0001" in prompt
    assert "- target_path: lighting.character" in prompt
    assert "- allow_delegate: false" in prompt
    assert '- suggested_values: ["warm side light", "cool blue hour"]' in prompt
    assert "- reason: lighting must be decided" in prompt
    assert "- question_text: 想要哪种光线？" in prompt


def test_user_prompt_is_deterministic() -> None:
    request = h.make_request(message_text="镜头拉远")
    assert build_interpreter_user_prompt(request) == build_interpreter_user_prompt(
        request
    )
