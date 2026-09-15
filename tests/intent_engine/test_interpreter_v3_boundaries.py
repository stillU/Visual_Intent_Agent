"""回归测试（MVP v0.3 Step 06 patch 001）：Interpreter v3 的**收窄**边界。

诊断证据（`evaluation/reports/fix_traceability.md` patch 001 章节）：

- 诊断性真实复跑 `erun_e7413108dc8a6104` 的 s04-conflict-001 t1 出现"零 delta → 对
  `subject.description` 发起标注不该问的澄清"。编排方首轮怀疑是 v2 的 rule 12~15
  让模型对明示信息也不敢提取；
- patch 001 的定向真实探针（`/tmp/via_probe.py`，记录见交接文档）证明该轮的真实
  根因是 **Provider 超时级联**（t1 = 两次 resolve、每次三个 HTTP 尝试全部 60s 超时，
  latency 363.9s ≈ 2×181.9s），不是提取退化；同一条消息在 provider 正常返回时
  v2 也能产出完整 7 条 delta；
- 但 v2 的措辞确有可收窄之处：rule 12 与 rule 13 之间丢失换行（"…default it to 1.13.
  LOCATIVE-FREE TEXT…"），rule 12 未声明"只约束 `subject.count`"，rule 13 未声明
  "明示地点标记必须提取"，rule 15 未声明"最小 ≠ 少于用户明示内容"。

本文件断言 v3 的收窄点写入系统提示词并真实送达 Provider，且**不放松** P4 的保护意图
（无依据不赋值）。行为层的"模型一定遵守"无法用离线测试证明（Step 06 已声明的限制），
这里额外用 FakeLLM 钉住确定性链路：明示 7 条 delta（含显式地点）必须全部落库，而
`subject.count` 保持未设。

R1-A（v5 / policy.v2）修订：数量正反例按"明确数词必须提取、未说数量才留空"改写；
rule 13 标题改为 `FREE LOCATIVE PHRASES`；"摄影棚 × 海滩"关键词 hard conflict 停用，
因此下方端到端断言改为"全部路径生效、冲突为空、字段原样保留"。
"""

from __future__ import annotations

import ie_helpers as h

from visual_intent_agent.domain import VisualIntent
from visual_intent_agent.intent_engine.prompts import (
    INTERPRETER_PROMPT_VERSION,
    INTERPRETER_SYSTEM_PROMPT_V1,
    build_interpreter_user_prompt,
)

def flat(text: str) -> str:
    """把提示词空白折叠成单空格：措辞断行不应成为断言失败原因。"""
    return " ".join(text.split())


#: v3 收窄点的稳定 token（新增语义必须同时出现在系统提示词里）。
COUNT_MARKER = "COUNTING"
LOCATIVE_MARKER = "FREE LOCATIVE PHRASES"
EXPLICIT_PLACE_MARKER = "An explicit place marker always yields its own delta"
COUNT_SCOPE_MARKER = "This rule constrains `subject.count` ONLY"
MINIMAL_MARKER = "MINIMAL never means fewer"
CONFLICT_MARKER = "a reason to emit fewer deltas"


def test_v3_prompt_version_is_recorded() -> None:
    """patch 001 修订系统提示词措辞 → v3；patch 002 保语义精简 → v4；
    R1-A 修正数量/自由处所语义 → v5。

    v3 的收窄点（COUNT_SCOPE / EXPLICIT_PLACE / MINIMAL / CONFLICT）在 v5 中继续逐字
    成立，构成"后续修订不丢 v3 语义"的回归证据；数量正反例本身按 R1-A 改写（见
    `test_v3_prompt_scopes_the_counting_rule_to_subject_count_only`）。
    """
    assert INTERPRETER_PROMPT_VERSION == "interpreter.v5"


def test_v3_prompt_repairs_the_glued_rule_numbering() -> None:
    """v2 的 rule 12/13 被拼成 "…never default it to 1.13. LOCATIVE…"。

    v3 恢复两条独立规则；R1-A（v5）把 rule 13 的标题从带连字符的
    `LOCATIVE-FREE TEXT` 改为 `FREE LOCATIVE PHRASES`（语义不变，措辞更准确）。
    断言"12./13. 两条独立编号 + 正文不再出现粘连编号"。
    """
    prompt = INTERPRETER_SYSTEM_PROMPT_V1
    assert "12. COUNTING" in prompt
    assert "13. FREE LOCATIVE PHRASES" in prompt
    assert "1.13." not in prompt


def test_v3_prompt_scopes_the_counting_rule_to_subject_count_only() -> None:
    """数量规则只作用于 `subject.count`，不得成为少提取其它路径的理由。

    R1-A（v5）修正正反例：明确数词是**正例**（"一只猫"→count=1），只有未说数量
    （"猫在睡觉"）或英文不定冠词（"a cat"）才是不得产生 count delta 的反例。
    """
    prompt = INTERPRETER_SYSTEM_PROMPT_V1
    assert COUNT_MARKER in prompt
    assert COUNT_SCOPE_MARKER in prompt
    assert "一只猫" in prompt and "猫在睡觉" in prompt and "a cat" in prompt
    assert "must NOT produce a `subject.count` delta" in prompt
    assert "never default it to 1" in prompt


def test_v3_prompt_requires_extraction_of_explicit_place_markers() -> None:
    """rule 13 的收窄：只有"自由处所短语"里的地点词禁止吞并；明示地点必须提取。"""
    prompt = INTERPRETER_SYSTEM_PROMPT_V1
    assert LOCATIVE_MARKER in prompt
    assert "you MUST emit `SET environment.location`" in flat(prompt)
    assert EXPLICIT_PLACE_MARKER in flat(prompt)
    # 明示地点示例必须出现（中文 + 英文 marker）。
    assert "地点是海边沙滩" in prompt
    assert "地点是客厅" in prompt
    assert "location: the beach" in prompt


def test_v3_prompt_keeps_the_locative_free_text_protection() -> None:
    """P4 保护意图不得被收窄掉：处所短语仍不得被吞成 `environment.location`。"""
    prompt = INTERPRETER_SYSTEM_PROMPT_V1
    assert "壁炉边" in prompt and "沙发" in prompt
    assert "never extract it as one" in flat(prompt)
    assert "A place word that only occurs inside such a pose/description phrase" in flat(prompt)


def test_v3_prompt_states_minimal_is_never_less_than_stated() -> None:
    """rule 15 的收窄："最小 delta 集" ≠ 少于用户明示的路径。"""
    prompt = INTERPRETER_SYSTEM_PROMPT_V1
    assert MINIMAL_MARKER in prompt
    assert (
        'Extra paths that seem "implied" by the phrasing are out of scope' in flat(prompt)
    )


def test_v3_prompt_states_a_conflict_is_not_a_reason_to_drop_deltas() -> None:
    """rule 10 的收窄：冲突时保留双方 delta，绝不因此少提取。"""
    prompt = INTERPRETER_SYSTEM_PROMPT_V1
    assert CONFLICT_MARKER in prompt
    assert "keep both deltas" in prompt


def test_v3_boundary_prompt_is_the_one_sent_to_the_provider() -> None:
    """收窄点必须进入真实 LLM 请求的 system message（不是只写在常量里）。"""
    request = h.make_request(
        VisualIntent(),
        message_text="一只黑猫坐在窗台上，写实摄影风格，摄影棚环境，地点是海边沙滩，中景，柔和光线。",
    )
    _, provider = h.run(request, h.script())
    system_message = provider.requests[0].messages[0]
    assert system_message.role == "system"
    sent = flat(system_message.content)
    for marker in (
        COUNT_MARKER,
        LOCATIVE_MARKER,
        COUNT_SCOPE_MARKER,
        EXPLICIT_PLACE_MARKER,
        MINIMAL_MARKER,
        CONFLICT_MARKER,
    ):
        assert marker in sent
    # rule 13 的收窄点必须是完整句子（含 MUST 强制提取与明示 marker 示例）。
    assert "you MUST emit `SET environment.location`" in sent
    assert "地点是海边沙滩" in sent


def test_v5_prompt_is_semantically_compressed_not_weakened() -> None:
    """patch 002 的保语义精简（v3 5660 → v4 ≈4590）在 v5 保持；v5 修正数量/处所语义
    时只做了小幅增补，仍明显低于 v3 的 5660。

    只钉住"明显低于 v3 精简前"的方向（< 5200），不钉死具体字符数（后续修订可继续精简）；
    其余关键句断言在本文件中逐字继续成立，构成"精简不丢语义"的回归证据。
    """
    assert len(INTERPRETER_SYSTEM_PROMPT_V1) < 5200
    # 被断言的全部 6 个稳定 marker 必须仍在（与上方测试同源，防止精简误删）。
    # 用折叠空白的口径断言：换行位置可随措辞精简变化，语义句子本身不得消失。
    flattened = flat(INTERPRETER_SYSTEM_PROMPT_V1)
    for marker in (
        COUNT_MARKER,
        LOCATIVE_MARKER,
        COUNT_SCOPE_MARKER,
        EXPLICIT_PLACE_MARKER,
        MINIMAL_MARKER,
        CONFLICT_MARKER,
    ):
        assert marker in flattened


def test_v3_user_prompt_contract_is_unchanged() -> None:
    """措辞修订不动输出合同：用户消息、白名单与 Schema 注入逐字保持。"""
    text = "一只黑猫坐在窗台上，地点是海边沙滩，中景。"
    request = h.make_request(VisualIntent(), message_text=text)
    prompt = build_interpreter_user_prompt(request)
    assert text in prompt
    assert request.message_id in prompt
    assert "OUTPUT JSON CONTRACT" in prompt
    assert '"candidate_deltas"' in prompt


# ---------------------------------------------------------------------------
# 确定性链路：明示地点 + 明示 7 路径必须全部生效并触发冻结冲突
# ---------------------------------------------------------------------------


def test_explicit_seven_path_extraction_is_applied_without_keyword_conflict() -> None:
    """s04 t1 形态：7 条明示 delta 全部落库，`subject.count` 不被凭空赋值。

    这是 patch 001 要保护的端到端行为：模型按提示词正常提取（明示地点入库、明示路径
    一条不少）。R1-A（policy.v2）停用了"摄影棚 × 海滩"关键词冲突，因此这里断言：
    全部路径生效、冲突为空、字段原样保留、`ready` 只由缺失决策决定。
    """
    text = "一只黑猫坐在窗台上，写实摄影风格，摄影棚环境，地点是海边沙滩，中景，柔和光线。"
    request = h.make_request(VisualIntent(), message_text=text)
    scripted = [
        h.delta("SET", "subject.description", value="黑猫", evidence_fragment="一只黑猫"),
        h.delta("SET", "subject.pose_action", value="坐在窗台上", evidence_fragment="坐在窗台上"),
        h.delta("SET", "style.primary", value="写实摄影", evidence_fragment="写实摄影风格"),
        h.delta("SET", "environment.mode", value="摄影棚环境", evidence_fragment="摄影棚环境"),
        h.delta(
            "SET",
            "environment.location",
            value="海边沙滩",
            evidence_fragment="地点是海边沙滩",
        ),
        h.delta("SET", "composition.framing", value="中景", evidence_fragment="中景"),
        h.delta("SET", "lighting.character", value="柔和光线", evidence_fragment="柔和光线"),
    ]
    resolution, _ = h.run(request, h.script(scripted))

    applied = {delta.path for delta in resolution.applied_deltas}
    assert applied == {
        "subject.description",
        "subject.pose_action",
        "style.primary",
        "environment.mode",
        "environment.location",
        "composition.framing",
        "lighting.character",
    }
    # P4 保护：模型没给 count，确定性层也不得凭空补 1。
    assert resolution.intent.subject.count is None
    # R1-A：摄影棚 + 海滩是布景组合，不再报关键词冲突；两个值都原样保留。
    assert resolution.conflicts == []
    assert resolution.intent.environment.mode == "摄影棚环境"
    assert resolution.intent.environment.location == "海边沙滩"


def test_explicit_place_only_message_does_not_invent_count() -> None:
    """只给显式地点与构图时，只应落这两条；`subject.count` 保持未设。"""
    request = h.make_request(
        VisualIntent(), message_text="一只黑猫坐在窗台上，地点是海边沙滩，中景。"
    )
    resolution, _ = h.run(
        request,
        h.script(
            [
                h.delta(
                    "SET",
                    "environment.location",
                    value="海边沙滩",
                    evidence_fragment="地点是海边沙滩",
                ),
                h.delta("SET", "composition.framing", value="中景", evidence_fragment="中景"),
            ]
        ),
    )
    assert {delta.path for delta in resolution.applied_deltas} == {
        "environment.location",
        "composition.framing",
    }
    assert resolution.intent.subject.count is None
    assert resolution.intent.environment.location == "海边沙滩"
