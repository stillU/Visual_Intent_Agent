"""L3 Prompt Semantics（协议第 4 节 Layer 3；Baseline A 与 System B 同口径）。

四个指标（基于每个 PromptArtifact（B）/ 基线 Prompt 记录（A）与标注
`prompt_expectations`；全部为**文本层代理指标**，不得当作图片结果评分）：

1. **Intent Coverage**（`intent_coverage`）：`must_mention_groups` 中至少命中一个
   关键词的组占比（逐案例，**最终就绪态 Prompt**——案例内最后一次成功生成所用
   Prompt；案例未产生任何 Prompt → `missing_data`）。
2. **Unauthorized Addition**（`unauthorized_addition`）：`must_not_mention`
   关键词命中数（计数型指标，无 score；越低越好）。B 侧另记录
   `prompt.unauthorized_addition` 防护事件：`guard_triggered`（编译期防护触发
   次数）与文本层命中并存时记 `guard_bypassed`（防护被绕过）。
3. **Preservation**（`preservation`）：多轮修改轮次——前轮 Prompt 中命中的
   关键词组在本轮 Prompt 仍命中的比例（逐相邻生成对，逐案例取均值；
   修改轮次以标注 `forbidden_change_paths` 非空认定）。单轮案例或从未进入
   修改轮 → `not_applicable`；修改轮未产生 Prompt 按"保留 0"保守计（不剔分母）。
4. **Model Compatibility**（`model_compatibility`）：Prompt 参数面合法——
   size 形如 `<w>x<h>` 且为冻结值（配置 `image.size`）、无越权参数、
   目标模型名正确（`Settings.image_model`）。

关键词命中规则：不区分大小写的子串匹配（Step 01 已知限制 6：关键词真值是
"命中任一即符合"的宽松下界，本层指标不得代替 L4）。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from evaluation.reporting import MetricPayload

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: size 的合法形态（`<w>x<h>`，正整数；与 Provider 合同同口径）。
_SIZE_PATTERN = re.compile(r"^[1-9]\d*x[1-9]\d*$")


class PromptSequenceTurn(BaseModel):
    """Preservation 输入：一轮的 Prompt 产出情况（按数据集轮序）。

    - `prompt_text`：该轮成功生成所用的最终 Prompt（未产生 → None）；
    - `is_modification_turn`：标注 `forbidden_change_paths` 非空且非 accept 轮
      （accept 是终局批准、不是修改；由 Runner 判定后传入）。

    r1 新增字段（旧调用方缺省即保持 v1 口径）：

    - `expected_prompt`：标注是否**应产生** Prompt（由 `expected_outcome` 派生；
      `ready_and_generate` → True，其余 → False）。None = 未知/旧口径；
    - `preserve_path_groups`：按路径关联的保留词组（path → 同义关键词组）。
      r1 Preservation 只比较这些“仍被要求保留”的属性。
    """

    model_config = _FROZEN

    turn_id: str
    prompt_text: str | None = None
    is_modification_turn: bool = False
    expected_prompt: bool | None = None
    preserve_path_groups: dict[str, list[list[str]]] = Field(default_factory=dict)
    #: R1-B #2：本轮**合法改变**的路径（来自标注 expected_deltas 的 SET/CLEAR）。
    #: 跨无 Prompt 轮累计；下一个有效相邻 Prompt 对在比较前排除这些路径后清空，
    #: 使“用户合法改掉的旧值”不依赖“关键词恰好在前轮不命中”来碰巧跳过。
    changed_paths: list[str] = Field(default_factory=list)


class PromptRequestFace(BaseModel):
    """Model Compatibility 输入：一次生成请求的参数面（两个系统都只有
    prompt/size/model 三个字段，`extra_parameters_free` 由构造保证、照实记录）。"""

    model_config = _FROZEN

    turn_id: str
    size: str
    model: str
    extra_parameters_free: bool = True


def keyword_hit(text: str, keyword: str) -> bool:
    """关键词命中：不区分大小写的子串匹配（宽松下界，见模块 docstring）。"""
    return keyword.lower() in text.lower()


def _hit_groups(text: str, groups: list[list[str]]) -> list[bool]:
    return [any(keyword_hit(text, keyword) for keyword in group) for group in groups]


# ---------------------------------------------------------------------------
# 1. Intent Coverage
# ---------------------------------------------------------------------------


def evaluate_intent_coverage(
    must_mention_groups: list[list[str]],
    final_prompt: str | None,
) -> MetricPayload:
    """最终就绪态 Prompt 的 must_mention_groups 组命中率。"""
    if final_prompt is None:
        return MetricPayload(
            status="missing_data",
            counts={"groups_total": len(must_mention_groups)},
            details={"reason": "the case produced no prompt (no generation happened)"},
        )
    hits = _hit_groups(final_prompt, must_mention_groups)
    total = len(must_mention_groups)
    hit_count = sum(hits)
    return MetricPayload(
        status="ok",
        score=(hit_count / total) if total else None,
        counts={"groups_total": total, "groups_hit": hit_count},
        details={
            "group_hits": hits,
            "missed_groups": [
                group for group, hit in zip(must_mention_groups, hits, strict=True) if not hit
            ],
        },
    )


# ---------------------------------------------------------------------------
# 2. Unauthorized Addition
# ---------------------------------------------------------------------------


def evaluate_unauthorized_addition(
    must_not_mention: list[str],
    final_prompt: str | None,
    *,
    guard_triggered: int = 0,
) -> MetricPayload:
    """must_not_mention 命中数（计数型）；B 侧附 `prompt.unauthorized_addition` 防护面。

    `guard_triggered`：编译期防护触发次数（System B；Baseline 无此机制恒 0）。
    文本仍命中且防护零触发 → `guard_bypassed = 1`（B 侧绕过证据）。
    """
    if final_prompt is None:
        return MetricPayload(
            status="missing_data",
            counts={"guard_triggered": guard_triggered},
            details={"reason": "the case produced no prompt (no generation happened)"},
        )
    hits = [keyword for keyword in must_not_mention if keyword_hit(final_prompt, keyword)]
    return MetricPayload(
        status="ok",
        score=None,
        counts={
            "hits": len(hits),
            "guard_triggered": guard_triggered,
            "guard_bypassed": 1 if hits and guard_triggered == 0 else 0,
        },
        details={"hit_keywords": hits},
    )


def evaluate_unauthorized_addition_r1(
    must_not_mention: list[str],
    final_prompt: str | None,
    *,
    guard_triggered: int = 0,
) -> MetricPayload:
    """r1 口径：关键词违例与来源防护**分开**报告（R1-B #8 / 001 明令）。

    - `forbidden_keyword_hit`：`must_not_mention` 在最终 Prompt 中的命中**计数**
      （纯文本事实，越低越好）；
    - `source_guard_triggered`：真实可观测的 `prompt.unauthorized_addition` 编译期
      防护触发次数（来自实际编译 issue，不由关键词推导）；
    - `source_guard_bypassed`：**只有存在真实编译 source-guard 被绕过的可观测证据**
      才取值；当前记录结构下该证据不存在，故恒为 `None` 并标注 `not_observable`。
      **禁止**用关键词命中派生“防护被绕过”——关键词不是来源校验事实。
    """
    if final_prompt is None:
        return MetricPayload(
            status="missing_data",
            counts={"source_guard_triggered": guard_triggered},
            details={
                "reason": "the case produced no prompt (no generation happened)",
                "forbidden_keyword_hit": None,
                "source_guard_bypassed": None,
                "source_guard_bypassed_observability": "not_observable",
                "metric_version": "v0_3_r1",
            },
        )
    hits = [keyword for keyword in must_not_mention if keyword_hit(final_prompt, keyword)]
    return MetricPayload(
        status="ok",
        score=None,
        counts={
            "forbidden_keyword_hit": len(hits),
            "source_guard_triggered": guard_triggered,
        },
        details={
            "hit_keywords": hits,
            # 关键词命中与来源校验是两件事：不得由关键词推导 guard 被绕过。
            "source_guard_bypassed": None,
            "source_guard_bypassed_observability": "not_observable",
            "metric_version": "v0_3_r1",
        },
    )


# ---------------------------------------------------------------------------
# 3. Preservation
# ---------------------------------------------------------------------------


def evaluate_preservation(
    must_mention_groups: list[list[str]],
    prompt_sequence: list[PromptSequenceTurn],
) -> MetricPayload:
    """多轮修改轮次的关键词组保留率（逐相邻生成对，逐案例均值）。

    配对规则（冻结口径）：`prompt_sequence` 按数据集轮序携带**所有可能产出
    Prompt 的轮次**；每个 `is_modification_turn` 且此前存在过 Prompt 的轮次与其
    最近一个有 Prompt 的前轮配对。前轮命中而本轮未命中的组计为丢失；修改轮
    未产生 Prompt 按保留 0 计（保守，不剔分母）；前轮无任何组命中的对不提供
    信息，跳过并计数。
    """
    pairs: list[tuple[PromptSequenceTurn, PromptSequenceTurn]] = []
    previous_with_prompt: PromptSequenceTurn | None = None
    for turn in prompt_sequence:
        if turn.is_modification_turn and previous_with_prompt is not None:
            pairs.append((previous_with_prompt, turn))
        if turn.prompt_text is not None:
            previous_with_prompt = turn

    if not pairs:
        return MetricPayload(
            status="not_applicable",
            counts={"prompt_turns": sum(1 for t in prompt_sequence if t.prompt_text)},
            details={
                "reason": (
                    "no modification turn follows an earlier prompt "
                    "(single-turn case or no generation happened)"
                )
            },
        )

    pair_scores: list[float] = []
    pair_details: list[dict[str, object]] = []
    skipped_no_prev_hits = 0
    groups_kept = 0
    groups_lost = 0
    for previous, current in pairs:
        assert previous.prompt_text is not None  # 配对构造保证
        prev_hits = _hit_groups(previous.prompt_text, must_mention_groups)
        if not any(prev_hits):
            skipped_no_prev_hits += 1
            continue
        if current.prompt_text is None:
            kept = 0
            lost_groups = [
                group for group, hit in zip(must_mention_groups, prev_hits, strict=True) if hit
            ]
        else:
            current_hits = _hit_groups(current.prompt_text, must_mention_groups)
            kept = sum(1 for before, after in zip(prev_hits, current_hits, strict=True) if before and after)
            lost_groups = [
                group
                for group, before, after in zip(
                    must_mention_groups, prev_hits, current_hits, strict=True
                )
                if before and not after
            ]
        prev_hit_count = sum(prev_hits)
        lost = prev_hit_count - kept
        groups_kept += kept
        groups_lost += lost
        pair_score = kept / prev_hit_count
        pair_scores.append(pair_score)
        pair_details.append(
            {
                "previous_turn_id": previous.turn_id,
                "turn_id": current.turn_id,
                "kept": kept,
                "lost": lost,
                "score": pair_score,
                "lost_groups": lost_groups,
                "current_prompt_missing": current.prompt_text is None,
            }
        )

    if not pair_scores:
        return MetricPayload(
            status="not_applicable",
            counts={"pairs": len(pairs), "pairs_skipped_no_prev_hits": skipped_no_prev_hits},
            details={"reason": "no pair carried any hit group in its previous prompt"},
        )
    return MetricPayload(
        status="ok",
        score=sum(pair_scores) / len(pair_scores),
        counts={
            "pairs": len(pairs),
            "pairs_scored": len(pair_scores),
            "pairs_skipped_no_prev_hits": skipped_no_prev_hits,
            "groups_kept": groups_kept,
            "groups_lost": groups_lost,
        },
        details={"pair_details": pair_details},
    )


# ---------------------------------------------------------------------------
# 3b. Preservation（r1：路径关联 + 不误罚澄清轮）
# ---------------------------------------------------------------------------


def _flatten_groups(preserve_path_groups: dict[str, list[list[str]]]) -> list[list[str]]:
    groups: list[list[str]] = []
    for path in sorted(preserve_path_groups):
        groups.extend(preserve_path_groups[path])
    return groups


def evaluate_preservation_r1(prompt_sequence: list[PromptSequenceTurn]) -> MetricPayload:
    """r1 Preservation：只比较实际产生的相邻有效 Prompt 中仍被要求保留的属性。

    与 v1 的差异（R1-B #2/#3）：

    - 保留单元按**路径关联**（`preserve_path_groups`），用户显式改掉的路径不参与；
    - **跨无 Prompt 轮累计合法改变路径**（`changed_paths`，来自 SET/CLEAR）：下一个
      有效相邻 Prompt 对在比较前排除这些路径，随后清空；不依赖“最新关键词恰好在前轮
      不命中”来碰巧跳过；
    - **正常澄清/拒绝轮无 Prompt → `not_applicable`**，无论是否带保留标签都计数，
      不按“保留 0”保守计；
    - 标注 `expected_prompt=True` 却未产生 Prompt → 记 `failed_generation` 计数
      （另由 `generation_completion` 指标报完成率），本身不伪造 0 分；
    - 逐相邻有效 Prompt 对评估，前轮无任何保留组命中的对不提供信息，跳过并计数。
    """
    pairs: list[
        tuple[PromptSequenceTurn, PromptSequenceTurn, list[list[str]], list[str]]
    ] = []
    previous_with_prompt: PromptSequenceTurn | None = None
    no_prompt_not_applicable = 0
    failed_generation = 0
    pending_changed: set[str] = set()

    for turn in prompt_sequence:
        pending_changed.update(turn.changed_paths)
        groups = _flatten_groups(
            {
                path: path_groups
                for path, path_groups in turn.preserve_path_groups.items()
                if path not in pending_changed
            }
        )
        if turn.prompt_text is None:
            if turn.expected_prompt is True:
                failed_generation += 1
            else:
                # 结构化上不产生 Prompt（如正常澄清/拒绝/终局接受）→ 不适用。
                # 计数与是否携带保留标签无关（R1-B #3）。
                no_prompt_not_applicable += 1
            continue
        if groups and previous_with_prompt is not None:
            pairs.append(
                (previous_with_prompt, turn, groups, sorted(pending_changed))
            )
            pending_changed.clear()
        previous_with_prompt = turn

    if not pairs:
        return MetricPayload(
            status="not_applicable",
            counts={
                "pairs": 0,
                "no_prompt_not_applicable": no_prompt_not_applicable,
                "failed_generation": failed_generation,
            },
            details={
                "reason": (
                    "no evaluable adjacent prompt pair carried a still-required "
                    "preserve group (single-turn case, clarification-only, or no generation)"
                ),
                "metric_version": "v0_3_r1",
            },
        )

    pair_scores: list[float] = []
    pair_details: list[dict[str, object]] = []
    skipped_no_prev_hits = 0
    groups_kept = 0
    groups_lost = 0
    for previous, current, groups, excluded_paths in pairs:
        assert previous.prompt_text is not None  # 配对构造保证
        assert current.prompt_text is not None
        prev_hits = _hit_groups(previous.prompt_text, groups)
        if not any(prev_hits):
            skipped_no_prev_hits += 1
            continue
        current_hits = _hit_groups(current.prompt_text, groups)
        kept = sum(
            1 for before, after in zip(prev_hits, current_hits, strict=True) if before and after
        )
        lost_groups = [
            group
            for group, before, after in zip(groups, prev_hits, current_hits, strict=True)
            if before and not after
        ]
        prev_hit_count = sum(prev_hits)
        lost = prev_hit_count - kept
        groups_kept += kept
        groups_lost += lost
        pair_scores.append(kept / prev_hit_count)
        pair_details.append(
            {
                "previous_turn_id": previous.turn_id,
                "turn_id": current.turn_id,
                "kept": kept,
                "lost": lost,
                "score": kept / prev_hit_count,
                "lost_groups": lost_groups,
                "excluded_changed_paths": excluded_paths,
            }
        )

    if not pair_scores:
        return MetricPayload(
            status="not_applicable",
            counts={
                "pairs": len(pairs),
                "pairs_skipped_no_prev_hits": skipped_no_prev_hits,
                "no_prompt_not_applicable": no_prompt_not_applicable,
                "failed_generation": failed_generation,
            },
            details={
                "reason": "no pair carried any still-required preserve group in its previous prompt",
                "metric_version": "v0_3_r1",
            },
        )
    return MetricPayload(
        status="ok",
        score=sum(pair_scores) / len(pair_scores),
        counts={
            "pairs": len(pairs),
            "pairs_scored": len(pair_scores),
            "pairs_skipped_no_prev_hits": skipped_no_prev_hits,
            "no_prompt_not_applicable": no_prompt_not_applicable,
            "failed_generation": failed_generation,
            "groups_kept": groups_kept,
            "groups_lost": groups_lost,
        },
        details={"pair_details": pair_details, "metric_version": "v0_3_r1"},
    )


def evaluate_generation_completion(
    prompt_sequence: list[PromptSequenceTurn],
) -> MetricPayload:
    """r1 完成率：标注应生成（`expected_prompt=True`）的轮次中实际产生 Prompt 的比例。

    失败轮单独计数（`failed`），不从分母剔除（R1-B #3）。
    """
    expected = [t for t in prompt_sequence if t.expected_prompt is True]
    if not expected:
        return MetricPayload(
            status="not_applicable",
            details={
                "reason": "no turn in this case declares expected_prompt=True",
                "metric_version": "v0_3_r1",
            },
        )
    produced = sum(1 for t in expected if t.prompt_text is not None)
    failed = len(expected) - produced
    return MetricPayload(
        status="ok",
        score=produced / len(expected),
        counts={"expected_prompt_turns": len(expected), "produced": produced, "failed": failed},
        details={
            "failed_turn_ids": [t.turn_id for t in expected if t.prompt_text is None],
            "metric_version": "v0_3_r1",
        },
    )


# ---------------------------------------------------------------------------
# 4. Model Compatibility
# ---------------------------------------------------------------------------


def evaluate_model_compatibility(
    requests: list[PromptRequestFace],
    *,
    expected_size: str,
    expected_model: str,
) -> MetricPayload:
    """参数面合法率：size 形态 + 冻结值 + 目标模型名 + 无越权参数。"""
    if not requests:
        return MetricPayload(
            status="missing_data",
            details={"reason": "the case produced no generation request"},
        )
    violations: list[dict[str, str]] = []
    compatible = 0
    for request in requests:
        problems: list[str] = []
        if not _SIZE_PATTERN.match(request.size):
            problems.append("size_format_invalid")
        elif request.size != expected_size:
            problems.append("size_not_frozen_value")
        if request.model != expected_model:
            problems.append("target_model_mismatch")
        if not request.extra_parameters_free:
            problems.append("extra_parameters_present")
        if problems:
            violations.append(
                {"turn_id": request.turn_id, "problems": ",".join(problems)}
            )
        else:
            compatible += 1
    total = len(requests)
    return MetricPayload(
        status="ok",
        score=compatible / total,
        counts={"requests": total, "compatible": compatible, "incompatible": total - compatible},
        details={"violations": violations},
    )


__all__ = [
    "PromptSequenceTurn",
    "PromptRequestFace",
    "keyword_hit",
    "evaluate_intent_coverage",
    "evaluate_unauthorized_addition",
    "evaluate_unauthorized_addition_r1",
    "evaluate_preservation",
    "evaluate_preservation_r1",
    "evaluate_generation_completion",
    "evaluate_model_compatibility",
]
