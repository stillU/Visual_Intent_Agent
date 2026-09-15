"""回归测试（MVP v0.3 Step 06 · P1 / P4）：Interpreter 的三条边界约束。

失败证据（`evaluation/reports/failure_catalog.jsonl`）：

- **P4 无依据具体化**（`FC-P4-baseless-concretization`）：
  s02-missing-core-001 t1（`emet_084e77929579b45c`）把"趴在壁炉边"具体化为
  `environment.location="壁炉边"`；s09-multiturn-002 r2 t1（`emet_6bb1e99d2e0cb4ab`）
  把"趴在沙发上"具体化为 `environment.location="沙发"`；19 个案例运行的
  `subject.count` 被凭空 SET 为 1（56 个 out_of_scope 中的 30 个、
  58 个 must_remain_unset 违例中的 50 个）。
- **P1 越权 CLEAR 的 Interpreter 侧**（`FC-P1-l1-clear-over-expansion`）：
  "删主体"只允许 CLEAR 用户点名的那条路径。s08-pin-unpin-001 t5
  （`eturn_24807db8aadfa204`）与 r2 t3（`eturn_12a7525d937d679a`）的越权 CLEAR 由
  **FeedbackEngine** 提出（见 `tests/feedback/test_feedback_boundaries.py`）；同一条
  "CLEAR IS PER-PATH" 边界同时约束 Interpreter，避免同类语义从用户消息侧进入。

修复落在 Interpreter 系统提示词的边界约束上；本文件断言：

1. 修复后的 prompt 版本号已提升，且约束以稳定 token 写入系统提示词；
2. 约束真的被送到 Provider（`Interpreter.interpret` 的 system message）；
3. 约束只描述"不得凭空产生值/不得越界"，不新增任何业务默认值或规则表。

R1-A（v5）修订：旧口径把"一只猫"也列为不得产生数量的反例，导致**明确数词**被误拒；
现改为"明确数词必须提取（一只→1、两只→2），只有未说数量 / 英文不定冠词才留空"。
"""

from __future__ import annotations

import ie_helpers as h
import pytest

from visual_intent_agent.domain import VisualIntent
from visual_intent_agent.intent_engine.prompts import (
    INTERPRETER_PROMPT_VERSION,
    INTERPRETER_SYSTEM_PROMPT_V1,
)

#: 约束的稳定 token（新增语义必须同时出现在系统提示词里）。
COUNTING_MARKER = "COUNTING"
LOCATIVE_MARKER = "FREE LOCATIVE PHRASES"
CLEAR_MARKER = "CLEAR IS PER-PATH"


def test_prompt_version_is_bumped_for_the_boundary_revision() -> None:
    """修改系统提示词必须提升版本常量（Step 06 / patch 001 / patch 002 / R1-A 见 R1-A 交接）。"""
    assert INTERPRETER_PROMPT_VERSION == "interpreter.v5"


def test_system_prompt_states_the_counting_boundary() -> None:
    """R1-A：明确数词必须提取；未说数量不得补值（旧口径把"一只猫"也当反例，已纠正）。"""
    prompt = INTERPRETER_SYSTEM_PROMPT_V1
    assert COUNTING_MARKER in prompt
    # 明确中文数词是**正例**：必须输出对应整数。
    assert "一只猫" in prompt and "两只狗" in prompt
    # 未说数量 / 英文不定冠词是**反例**：不得产生 count delta。
    assert "猫在睡觉" in prompt and "a cat" in prompt
    assert "subject.count" in prompt


def test_system_prompt_states_the_locative_boundary() -> None:
    prompt = INTERPRETER_SYSTEM_PROMPT_V1
    assert LOCATIVE_MARKER in prompt
    # 反例必须显式出现，避免"趴在……"被吞进 location。
    assert "壁炉边" in prompt or "沙发" in prompt
    assert "environment.location" in prompt
    # R1-A：自由处所不得被用来推断用户没说的房间。
    assert "never infer a room" in " ".join(prompt.split())


def test_system_prompt_states_the_clear_is_per_path_boundary() -> None:
    prompt = INTERPRETER_SYSTEM_PROMPT_V1
    assert CLEAR_MARKER in prompt
    assert "subject.count" in prompt and "subject.pose_action" in prompt


def test_the_boundary_prompt_is_the_one_actually_sent_to_the_provider() -> None:
    """约束必须进入真实 LLM 请求的 system message（不是只写在常量里）。"""
    request = h.make_request(VisualIntent(), message_text="现在把猫去掉。")
    _, provider = h.run(request, h.script())
    system_message = provider.requests[0].messages[0]
    assert system_message.role == "system"
    for marker in (COUNTING_MARKER, LOCATIVE_MARKER, CLEAR_MARKER):
        assert marker in system_message.content


@pytest.mark.parametrize(
    "fragment",
    ["一只银灰色英国短毛猫端坐在钢琴上", "趴在壁炉边"],
)
def test_boundary_prompt_keeps_the_user_message_and_evidence_contract(fragment: str) -> None:
    """约束不改变输出合同：仍然只允许 candidate_deltas + 证据片段。"""
    request = h.make_request(VisualIntent(), message_text=fragment)
    _, provider = h.run(request, h.script())
    user_prompt = provider.requests[0].messages[1].content
    assert fragment in user_prompt
    assert "candidate_deltas" in user_prompt
    assert "evidence_fragment" in INTERPRETER_SYSTEM_PROMPT_V1


# ---------------------------------------------------------------------------
# P3 引擎侧接线：resolve 必须把 ExecutionRevision 交给 assess
# ---------------------------------------------------------------------------


def test_engine_threads_the_execution_context_into_assess() -> None:
    """`IntentEngine.resolve` 必须把请求携带的 ExecutionRevision 交给 `assess`。

    调用点证据：`intent_engine/engine.py`（正常路径与可恢复失败路径）都必须传
    `execution_context`。R1-A（policy.v2）停用了"远景构图 × 方形输出"执行冲突，因此
    这里不能用冲突 code 作为"已接线"的证据；改为在调用链上观察：给 `assess` 打一个
    记录入参的探针，断言收到的正是请求里的 ExecutionRevision（未传时为 None）。
    """
    import visual_intent_agent.intent_engine.engine as engine_module
    from visual_intent_agent.policy import assess as real_assess

    seen: list[str | None] = []

    def recording_assess(intent, execution_context=None):
        seen.append(
            None if execution_context is None else execution_context.output_size
        )
        return real_assess(intent, execution_context)

    original = engine_module.assess
    engine_module.assess = recording_assess
    try:
        request = h.make_request(VisualIntent(), message_text="远景构图")
        request = request.model_copy(update={"execution_context": h.execution("1024x1024")})
        h.run(
            request,
            h.script(
                [
                    h.delta(
                        "SET",
                        "composition.framing",
                        value="全景",
                        evidence_fragment="远景构图",
                    )
                ]
            ),
        )
    finally:
        engine_module.assess = original

    assert seen == ["1024x1024"]


def test_retired_framing_rule_reports_no_execution_conflict_even_with_context() -> None:
    """停用后：即使带了执行上下文，远景/全景也不再报执行冲突（R1-A 裁定）。"""
    request = h.make_request(VisualIntent(), message_text="远景构图")
    request = request.model_copy(update={"execution_context": h.execution("1024x1024")})
    resolution, _ = h.run(
        request,
        h.script(
            [
                h.delta(
                    "SET",
                    "composition.framing",
                    value="全景",
                    evidence_fragment="远景构图",
                )
            ]
        ),
    )
    assert resolution.conflicts == []
    assert resolution.intent.composition.framing == "全景"
