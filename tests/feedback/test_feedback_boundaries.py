"""回归测试（MVP v0.3 Step 06 · P1 / P5）：FeedbackEngine 的 CLEAR / preserve 边界。

失败证据（`evaluation/reports/failure_catalog.jsonl`）：

- **P1 越权 CLEAR（L1 阻断，最高优先）**（`FC-P1-l1-clear-over-expansion`）：
  s08-pin-unpin-001 rep1 t5（`eturn_24807db8aadfa204`）与 rep2 t3
  （`eturn_12a7525d937d679a`）都是 `image_feedback` 轮 —— 用户原文"现在把猫去掉。/
  把猫从画面里去掉。"由 **FeedbackEngine** 解释成
  `CLEAR subject.description` + `CLEAR subject.count` + `CLEAR subject.pose_action`；
  后两条不在任何 `applied` 语义内（标注 `forbidden_change_paths`），
  `unauthorized_fields_unchanged` 42/44、8 次违例、`gate_a_blocked=true`。
- **P5 PIN 范围过宽**（`FC-P5-pin-scope-overreach`）：
  s07-single-field-002 t2（`emet_78618550965e1762`/`emet_8d04c6d11fdeb3e0`）
  用户"改成两只边牧，一黑一白，其他都不变"被展开成 5~6 条 PIN
  （`composition.framing`、`environment.mode`、`environment.location`、
  `lighting.character`、`style.primary`、`subject.pose_action`），13 个 out_of_scope；
  同形态还出现在 s07-single-field-001 t2 与 s09-multiturn-001 t3。
  s09-multiturn-001 t3"狗保持不变"只允许 PIN subject 三路径。

本文件断言：

1. `FEEDBACK_PROMPT_VERSION` 已提升，且"删除按路径 / preserve 按声明范围"两条边界以
   稳定 token 写入系统提示词，并真实进入 Provider 请求；
2. preserve 展开沿用具名范围时行为不变（`subject.*` 只覆盖 subject 有值路径），
   未声明的 facet（environment / lighting / composition）不得被凭空并入。
"""

from __future__ import annotations

from visual_intent_agent.domain import DeltaOperation
from visual_intent_agent.feedback import (
    FEEDBACK_PROMPT_VERSION,
    FEEDBACK_SYSTEM_PROMPT_V1,
    FeedbackEngine,
)

from feedback_helpers import (
    feedback_response,
    load_feedback_request,
    make_p3_session,
    revise_entry,
)

CLEAR_MARKER = "CLEAR IS PER-PATH"
PRESERVE_MARKER = "PRESERVE SCOPE"
COUNTING_MARKER = "COUNTING"
LOCATIVE_MARKER = "LOCATIVE"


def test_feedback_prompt_version_is_bumped_for_the_boundary_revision() -> None:
    # patch 002（P6 超时缓解）：保语义精简 → feedback.v3（两条边界关键句逐字保留）；
    # 更改书 002 工包 B：补 COUNTING/LOCATIVE 语义边界 → feedback.v4。
    assert FEEDBACK_PROMPT_VERSION == "feedback.v4"


def test_feedback_prompt_states_the_clear_is_per_path_boundary() -> None:
    prompt = FEEDBACK_SYSTEM_PROMPT_V1
    assert CLEAR_MARKER in prompt
    # 反例：删主体不得连带 CLEAR count / pose_action。
    assert "subject.count" in prompt and "subject.pose_action" in prompt


def test_feedback_prompt_states_the_preserve_scope_boundary() -> None:
    prompt = FEEDBACK_SYSTEM_PROMPT_V1
    assert PRESERVE_MARKER in prompt
    # 反例：笼统的"其他都不变"只覆盖用户点名的范围，不得覆盖全部有值路径。
    assert "其他都不变" in prompt or "everything else" in prompt


def test_feedback_prompt_states_the_counting_boundary() -> None:
    """更改书 001 · R1-A 数量规则：显式数词才写 count，冠词/单数名词不得默认 1。"""
    prompt = FEEDBACK_SYSTEM_PROMPT_V1
    assert COUNTING_MARKER in prompt
    assert "subject.count" in prompt
    assert "never means 1" in prompt
    assert "两只" in prompt


def test_feedback_prompt_states_the_locative_free_text_boundary() -> None:
    """更改书 001 · R1-A 地点规则：自由处所不推导房间，明确地点必须提取。"""
    prompt = FEEDBACK_SYSTEM_PROMPT_V1
    assert LOCATIVE_MARKER in prompt
    assert "environment.location" in prompt
    assert "在沙发上" in prompt
    assert "地点是客厅" in prompt


def test_the_boundary_prompt_is_the_one_actually_sent_to_the_provider(tmp_path) -> None:
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[
                    revise_entry(
                        "composition.framing",
                        value="close_up",
                        evidence_fragment="改成特写",
                    )
                ],
            )
        ],
    )
    engine = FeedbackEngine(session.llm.provider)
    engine.analyze(load_feedback_request(session, feedback_text="改成特写"))

    system_message = session.llm.provider.requests[0].messages[0]
    assert system_message.role == "system"
    assert CLEAR_MARKER in system_message.content
    assert PRESERVE_MARKER in system_message.content


def test_feedback_prompt_is_semantically_compressed_not_weakened() -> None:
    """patch 002：保语义精简让提示词明显变短（v2 4485 → v3 3657）。

    只钉住"明显下降"的方向（< 4000），不钉死具体字符数；更新 `feedback.v4` 只补
    COUNTING / LOCATIVE 两条语义边界（3993 字符），四条保护关键句逐字继续成立。
    """
    assert len(FEEDBACK_SYSTEM_PROMPT_V1) < 4000
    assert CLEAR_MARKER in FEEDBACK_SYSTEM_PROMPT_V1
    assert PRESERVE_MARKER in FEEDBACK_SYSTEM_PROMPT_V1
    assert COUNTING_MARKER in FEEDBACK_SYSTEM_PROMPT_V1
    assert LOCATIVE_MARKER in FEEDBACK_SYSTEM_PROMPT_V1


def test_named_preserve_scope_expands_only_inside_the_declared_scope(tmp_path) -> None:
    """`preserve_paths` 的具名 facet 范围不得外溢到其它 facet。

    对照 s07-single-field-002 t2 / s09-multiturn-001 t3：用户只点名"其他都不变 /
    狗保持不变"，正确范围是被点名 facet 的有值路径；实现不得自行补 PIN。
    """
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[
                    revise_entry("subject.count", value=2, evidence_fragment="改成两只")
                ],
                preserve_paths=["composition.*", "style.*"],
            )
        ],
    )
    engine = FeedbackEngine(session.llm.provider)
    result = engine.analyze(
        load_feedback_request(session, feedback_text="改成两只，其他都不变")
    )

    pins = {
        delta.path
        for delta in result.candidate_deltas
        if delta.operation is DeltaOperation.PIN
    }
    assert pins <= {"composition.framing", "style.primary", "style.description"}
    assert "composition.framing" in pins
    # 用户没有点名 subject / environment / lighting，实现不得替它们 PIN。
    assert not {
        "subject.description",
        "subject.pose_action",
        "environment.mode",
        "environment.location",
        "lighting.character",
    } & pins
    assert result.preserve_paths == ["composition.*", "style.*"]


def test_clear_removal_maps_exactly_the_declared_path(tmp_path) -> None:
    """P1 对照：用户只要求删主体时，候选里只出现被点名的 CLEAR 路径。

    这里脚本化的是**修复后应有的候选形态**：确定性层（Validator/Reducer/Policy）
    对多出来的 `CLEAR subject.count` / `CLEAR subject.pose_action` 没有冻结规则可以
    拦截（它们当前既未 pinned、也确有值），因此修复必须落在候选生成边界。
    """
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[
                    revise_entry(
                        "subject.description",
                        operation="CLEAR",
                        evidence_fragment="现在把猫去掉。",
                    )
                ],
            )
        ],
    )
    engine = FeedbackEngine(session.llm.provider)
    result = engine.analyze(load_feedback_request(session, feedback_text="现在把猫去掉。"))

    clear_paths = [
        delta.path
        for delta in result.candidate_deltas
        if delta.operation is DeltaOperation.CLEAR
    ]
    assert clear_paths == ["subject.description"]


def test_named_facet_preserve_still_expands_to_that_facet_only(tmp_path) -> None:
    """对照标注 `acceptable_extra_deltas`：显式点名 facet 的 preserve 仍被接受。

    s07-single-field-001 t2（"猫和背景都别动"）的标注把
    `PIN subject.description`、`PIN subject.pose_action`、`PIN environment.mode`、
    `PIN environment.location` 列为 acceptable。收紧"其他都不变"的展开不得误伤
    这类**用户已点名 facet**的合法 preserve 形态。
    """
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[
                    revise_entry(
                        "composition.framing",
                        value="close_up",
                        evidence_fragment="改成特写",
                    )
                ],
                preserve_paths=["subject.*", "environment.*"],
            )
        ],
    )
    engine = FeedbackEngine(session.llm.provider)
    result = engine.analyze(
        load_feedback_request(session, feedback_text="猫和背景都别动，改成特写")
    )

    pins = {
        delta.path
        for delta in result.candidate_deltas
        if delta.operation is DeltaOperation.PIN
    }
    assert {
        "subject.description",
        "subject.pose_action",
        "environment.mode",
    } <= pins
    # `environment.location` 在该 Intent 里只有 delegated 记录、没有值，
    # preserve_pin_deltas 不会为无值路径发 PIN（不是本步收紧造成的）。
    assert "environment.location" not in pins
    assert "lighting.character" not in pins
    assert "camera.angle" not in pins
