"""回归测试（MVP v0.3 更改书 001 · R1-A）：中文明确数量的提取与不脑补。

已确认的错误语义（`REVISION_001_SHORTEST_PATH.md` 第 2 节「一只猫被提示词强制禁止
解析数量 1」）：prompt v2～v4 的 rule 12 把「一只猫 / 一位老人」整体列为"不得输出
`subject.count`"的反例，使 Interpreter 对**明确中文数词**也不敢提取数量。

R1-A 修正后的语义：

    明确数词（一只/两只/三位/两个，one/two）  -> 必须 `SET subject.count` 对应整数；
    未说数量（"猫在睡觉"）/ 英文不定冠词 a/an -> `subject.count` 保持未设，绝不补 1。

本文件用 FakeLLM 钉住**确定性链路**（模型是否遵守由 R2 真实探针验证，离线测试不能
证明真实语言理解）：

1. 明确数量 + 其它明示路径必须全部落库；
2. 未说数量时 `subject.count` 必须保持 None（不脑补）；
3. rule 12 的"只约束 `subject.count`"不得变成少提取其它路径的理由。
"""

from __future__ import annotations

import ie_helpers as h

from visual_intent_agent.domain import VisualIntent
from visual_intent_agent.intent_engine.prompts import (
    INTERPRETER_PROMPT_VERSION,
    INTERPRETER_SYSTEM_PROMPT_V1,
)


def test_prompt_requires_explicit_numbers_and_never_defaults_one() -> None:
    prompt = INTERPRETER_SYSTEM_PROMPT_V1
    flat = " ".join(prompt.split())
    assert INTERPRETER_PROMPT_VERSION == "interpreter.v5"
    # 明确数词是正例（必须提取），未说数量/不定冠词是反例（不得提取）。
    assert "一只猫" in prompt and "两只狗" in prompt and "猫在睡觉" in prompt
    assert "a cat" in prompt
    assert "never default it to 1" in prompt
    # v4 的错误反例措辞必须消失：不能再把"一只猫"整体当作禁止提取的反例。
    assert "A singular noun phrase, a classifier or an indefinite article is NOT a quantity" not in flat


def test_explicit_chinese_number_is_extracted_with_every_other_stated_path() -> None:
    """「两只狗」+ 明示路径：数量必须落库，且不得因 rule 12 少提取其它路径。"""
    text = "两只狗在草地上奔跑，中景构图。"
    request = h.make_request(VisualIntent(), message_text=text)
    resolution, _ = h.run(
        request,
        h.script(
            [
                h.delta("SET", "subject.count", value=2, evidence_fragment="两只"),
                h.delta("SET", "subject.description", value="狗", evidence_fragment="两只狗"),
                h.delta(
                    "SET",
                    "subject.pose_action",
                    value="在草地上奔跑",
                    evidence_fragment="在草地上奔跑",
                ),
                h.delta("SET", "composition.framing", value="中景", evidence_fragment="中景构图"),
            ]
        ),
    )

    assert {delta.path for delta in resolution.applied_deltas} == {
        "subject.count",
        "subject.description",
        "subject.pose_action",
        "composition.framing",
    }
    assert resolution.intent.subject.count == 2
    assert resolution.intent.subject.description == "狗"
    assert resolution.intent.composition.framing == "中景"
    # rule 12 不得造成任何越权 issue。
    assert h.issue_codes(resolution) == []


def test_explicit_english_number_is_extracted() -> None:
    text = "two dogs running on a beach"
    request = h.make_request(VisualIntent(), message_text=text)
    resolution, _ = h.run(
        request,
        h.script(
            [
                h.delta("SET", "subject.count", value=2, evidence_fragment="two"),
                h.delta("SET", "subject.description", value="dogs", evidence_fragment="dogs"),
            ]
        ),
    )
    assert resolution.intent.subject.count == 2
    assert {delta.path for delta in resolution.applied_deltas} == {
        "subject.count",
        "subject.description",
    }


def test_no_stated_amount_never_becomes_a_count_delta() -> None:
    """「猫在睡觉」/「a cat」：未说数量 → 保持未设，确定性层也不补 1。"""
    text = "猫在睡觉"
    request = h.make_request(VisualIntent(), message_text=text)
    resolution, _ = h.run(
        request,
        h.script(
            [h.delta("SET", "subject.description", value="猫", evidence_fragment="猫在睡觉")]
        ),
    )
    assert resolution.intent.subject.count is None
    assert "subject.count" not in {delta.path for delta in resolution.applied_deltas}

    english = h.make_request(VisualIntent(), message_text="a cat")
    english_resolution, _ = h.run(
        english,
        h.script(
            [h.delta("SET", "subject.description", value="cat", evidence_fragment="a cat")]
        ),
    )
    assert english_resolution.intent.subject.count is None


def test_explicit_count_does_not_drop_the_subject_description_delta() -> None:
    """rule 12 只约束 `subject.count`：数量与主体描述同时给出时两条都必须生效。"""
    text = "一只黑猫坐在窗台上"
    request = h.make_request(VisualIntent(), message_text=text)
    resolution, _ = h.run(
        request,
        h.script(
            [
                h.delta("SET", "subject.count", value=1, evidence_fragment="一只"),
                h.delta("SET", "subject.description", value="黑猫", evidence_fragment="一只黑猫"),
                h.delta(
                    "SET",
                    "subject.pose_action",
                    value="坐在窗台上",
                    evidence_fragment="坐在窗台上",
                ),
            ]
        ),
    )
    applied = {delta.path for delta in resolution.applied_deltas}
    assert applied == {"subject.count", "subject.description", "subject.pose_action"}
    assert resolution.intent.subject.count == 1
    assert resolution.intent.subject.description == "黑猫"
    assert resolution.intent.subject.pose_action == "坐在窗台上"
