"""R1-B：从旧冻结集生成 `core_v0_3_r1` fixture / annotations（不读系统输出）。

本脚本**只读取**旧冻结文件 `evaluation/fixtures/core_v0_3.jsonl` 与
`evaluation/annotations/core_v0_3.jsonl`，按更改书 001 R1-A/R1-B 的语义裁定做
**逐项、可复核**的修订，写出 r1 版本。旧文件不改动。

修订依据（独立语义理由，不来自任何系统运行结果）：

1. R1-A #1 数量：中文明确数词（一只/一位/两只…）允许抽取为 `subject.count` 整数；
   未陈述数量不默认。r1 把出现过明确数词的轮次从 `paths_must_remain_unset` 移出
   `subject.count`，并把它记为 `acceptable_extra_deltas`（允许而非强制）。
2. R1-A #3/#4 冲突停用（A 工包确认）：`hard_conflict.environment_mode_location`
   （摄影棚+海滩）、`hard_conflict.lighting_environment_source`（室内+自然光）、
   `hard_conflict.style_medium_mismatch`（摄影+水彩质感）、
   `execution_conflict.framing_aspect_mismatch`（远景/全景+方图）四条启发式不是
   可证明的硬冲突，r1 全部移除；因此 s04 三例不再是冲突案例，其后续轮由
   `clarification_answer` 改为 `image_feedback` 的合法修改轮（必要对话轮次修正）。
3. R1-B #2 保留口径：为每个修改轮生成**按路径关联**的保留词组
   `preserve_path_groups`（路径 → 截至该轮的最新期望关键词组），供 r1
   Preservation 只比较“仍被要求保留”的属性；用户显式改掉的路径不再计入保留。
4. `parent_case_id` / `annotation_revision_reason` 记录来源与理由。

用法（离线）：

    .venv/bin/python evaluation/tools/build_core_v0_3_r1.py
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OLD_FIXTURE = ROOT / "evaluation" / "fixtures" / "core_v0_3.jsonl"
OLD_ANNOTATIONS = ROOT / "evaluation" / "annotations" / "core_v0_3.jsonl"
NEW_FIXTURE = ROOT / "evaluation" / "fixtures" / "core_v0_3_r1.jsonl"
NEW_ANNOTATIONS = ROOT / "evaluation" / "annotations" / "core_v0_3_r1.jsonl"

DATASET_VERSION = "core_v0_3_r1"

#: 12 条冻结意图路径（与 domain.INTENT_PATHS 一致；此脚本不 import 产品包）。
ALL_PATHS: tuple[str, ...] = (
    "subject.description",
    "color.palette",
    "camera.depth_of_field",
    "camera.angle",
    "environment.mode",
    "subject.count",
    "style.description",
    "subject.pose_action",
    "composition.framing",
    "style.primary",
    "environment.location",
    "lighting.character",
)

#: 明确数量的中文量词（只收主体可数名词量词；刻意排除“张/幅/个/双”等
#: 会误命中“一张图/双臂”的量词）。
_NUMERAL = r"(一|两|二|三|四|五|六|七|八|九|十)"
_CLASSIFIER = r"(只|位|名|条|头|匹)"
_NUMERAL_WORDS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_COUNT_RE = re.compile(_NUMERAL + _CLASSIFIER)

#: s04 三例：冲突停用后修正的必要对话轮次（t2 由澄清回答改为图片反馈修改轮）。
_S04_TURN_KIND_FIX = {"t2": "image_feedback"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    path.write_text(text, encoding="utf-8")


def explicit_subject_count(text: str) -> int | None:
    """用户文本中的明确主体数量（无 → None）。"""
    match = _COUNT_RE.search(text)
    if match is None:
        return None
    return _NUMERAL_WORDS[match.group(1)]


def explicit_subject_count_phrase(text: str) -> tuple[int, str, str] | None:
    """返回 (数量, 量词, 原短语)，如 "一只橘猫" → (1, "只", "一只")。"""
    match = _COUNT_RE.search(text)
    if match is None:
        return None
    return (_NUMERAL_WORDS[match.group(1)], match.group(2), match.group(0))


#: 英文数量表达（仅 one/two 等明确数词可写；a/an 不强制）。
_ENGLISH_COUNT_WORDS = {1: "one ", 2: "two ", 3: "three "}


def count_semantics_group(
    fixture_turns: list[dict[str, Any]],
) -> tuple[list[str], int] | None:
    """R1-A #1 的数量语义保留组：原短语 / 数字形式 / 明确英文数词。

    评测容许两种合法结构化表达（抽取 `subject.count`，或省略结构化 count），
    但**数量语义必须在可观察 Prompt 中保留**；完全丢失要由 Intent Coverage 罚。
    """
    latest: tuple[int, str, str] | None = None
    for turn in fixture_turns:
        found = explicit_subject_count_phrase(str(turn.get("user_text", "")))
        if found is not None:
            latest = found
    if latest is None:
        return None
    count, classifier, phrase = latest
    keywords = [phrase, f"{count}{classifier}"]
    english = _ENGLISH_COUNT_WORDS.get(count)
    if english:
        keywords.append(english)
    return keywords, count


def _keywords(delta: dict[str, Any]) -> list[str] | None:
    value_match = delta.get("value_match") or {}
    mode = value_match.get("mode")
    if mode == "contains_any":
        return list(value_match.get("keywords") or [])
    if mode == "exact_token":
        keyword = value_match.get("value")
        return [str(keyword)] if keyword else None
    if mode == "int_equals":
        value = value_match.get("value")
        return [str(value)] if value is not None else None
    return None


def derive_preserve_path_groups(
    turns: list[dict[str, Any]], upto_index: int
) -> dict[str, list[list[str]]]:
    """按路径关联的保留词组：截至该轮每个路径的**最新**期望关键词组。

    跨澄清轮累计：后续轮显式改掉的路径以最新期望值参与保留检查，因此
    用户合法修改不会被记成丢失；未被改动的路径沿用此前关键词。
    **CLEAR 必须从 latest 移除**：用户合法删除的值不得跨澄清轮继续被要求保留。
    """
    latest: dict[str, list[str]] = {}
    for turn in turns[: upto_index + 1]:
        for delta in turn.get("expected_deltas") or []:
            operation = delta.get("operation")
            if operation == "CLEAR":
                latest.pop(delta["path"], None)
                continue
            if operation != "SET":
                continue
            groups = _keywords(delta)
            if groups:
                latest[delta["path"]] = groups
    forbidden = turns[upto_index].get("forbidden_change_paths") or []
    return {
        path: [latest[path]]
        for path in forbidden
        if path in latest and latest[path]
    }


def _count_revision(
    turns: list[dict[str, Any]], fixture_turns: list[dict[str, Any]]
) -> None:
    """R1-A #1：明确数词允许抽取，逐轮解除 subject.count 的“必须保持未设”。

    `turns` 为标注轮；用户文本来自 fixture 轮（同序）。
    """
    seen_count: int | None = None
    for index, turn in enumerate(turns):
        user_text = ""
        kind = ""
        if index < len(fixture_turns):
            user_text = str(fixture_turns[index].get("user_text", ""))
            kind = str(fixture_turns[index].get("kind", ""))
        count = explicit_subject_count(user_text)
        if count is not None:
            seen_count = count
        if seen_count is None:
            continue
        unset = turn.get("paths_must_remain_unset")
        if unset and "subject.count" in unset:
            turn["paths_must_remain_unset"] = [p for p in unset if p != "subject.count"]
        # accept 轮不产生 Delta；不加可接受附加，避免与终局不变量混淆。
        if kind == "accept":
            continue
        # 已被显式要求改成 N（expected_deltas 里已有）时不重复加 acceptable。
        expected_paths = {(d.get("operation"), d.get("path")) for d in turn.get("expected_deltas") or []}
        if ("SET", "subject.count") in expected_paths:
            continue
        extras = turn.setdefault("acceptable_extra_deltas", [])
        if any(
            e.get("operation") == "SET" and e.get("path") == "subject.count"
            for e in extras
        ):
            continue
        extras.append(
            {
                "operation": "SET",
                "path": "subject.count",
                "value_match": {"mode": "int_equals", "value": seen_count},
                "resolution": None,
            }
        )


def _strip_conflicts(turn: dict[str, Any]) -> None:
    turn["expected_conflicts"] = []


def _add_count_semantics_prompt_group(
    annotation: dict[str, Any], fixture_turns: list[dict[str, Any]]
) -> None:
    """在 case 级 Prompt 真值中要求原用户数量语义保留（must_mention 或等价约束）。

    评测容许“抽取结构化 count”与“省略结构化 count 但在 Prompt 中明确数量”两种
    合法表达；但两种表达都不得把数量内容完全丢掉——后者由本关键词组兜底。
    """
    expectations = annotation.get("prompt_expectations")
    if not expectations:
        return
    result = count_semantics_group(fixture_turns)
    if result is None:
        return
    keywords, _count = result
    groups = expectations.setdefault("must_mention_groups", [])
    for group in groups:
        if any(keyword in group for keyword in keywords):
            return
    groups.append(keywords)

def revise_s04_annotation(annotation: dict[str, Any]) -> None:
    """s04 三例：冲突停用后的独立语义重建（t1 可直接生成，t2 为合法修改轮）。"""
    turns = annotation["turn_annotations"]
    t1, t2, t3 = turns[0], turns[1], turns[2]

    # t1：四类启发式冲突停用 → 所有阻塞决策已解决，直接进入确认与生成。
    _strip_conflicts(t1)
    t1["must_clarify"] = []
    t1["blocking_missing_paths_after_turn"] = []
    t1["must_not_clarify_paths"] = sorted(ALL_PATHS)
    t1["expected_outcome"] = "ready_and_generate"
    base_notes = (t1.get("notes") or "").strip()
    t1["notes"] = (
        "r1 修订：R1-A 停用启发式冲突规则（A 工包确认），该组合不是可证明的硬冲突；"
        "t1 全部阻塞决策已解决，直接 ready_and_generate。"
        + (f" 原文：{base_notes}" if base_notes else "")
    )

    # t2：不再是“消解冲突的澄清回答”，而是对已生成图片的合法单字段修改。
    changed_path = t2["expected_deltas"][0]["path"]
    t2["forbidden_change_paths"] = [p for p in ALL_PATHS if p != changed_path]
    t2["blocking_missing_paths_after_turn"] = []
    t2["must_clarify"] = []
    t2["expected_feedback_decision"] = "revise"
    t2["expected_outcome"] = "ready_and_generate"
    t2["notes"] = (
        f"r1 修订：冲突停用后本轮为对已生成图片的合法修改（SET {changed_path}）；"
        "其余路径逐值不变。"
    )

    # t3 accept 不变（仅确保无需改动）。
    if t3.get("expected_feedback_decision") == "accept":
        t3["expected_outcome"] = "accepted_completed"


def build() -> tuple[int, int]:
    fixtures = load_jsonl(OLD_FIXTURE)
    annotations = {a["case_id"]: a for a in load_jsonl(OLD_ANNOTATIONS)}

    new_fixtures: list[dict[str, Any]] = []
    new_annotations: list[dict[str, Any]] = []

    for case in fixtures:
        case_id = case["case_id"]
        new_case = deepcopy(case)
        new_case["dataset_version"] = DATASET_VERSION
        new_case["parent_case_id"] = case_id
        if case_id.startswith("s04-conflict"):
            for turn in new_case["turns"]:
                if turn["turn_id"] in _S04_TURN_KIND_FIX:
                    turn["kind"] = _S04_TURN_KIND_FIX[turn["turn_id"]]
                turn.pop("answer_variants", None)
        new_fixtures.append(new_case)

        annotation = deepcopy(annotations[case_id])
        annotation["dataset_version"] = DATASET_VERSION
        annotation["parent_case_id"] = case_id
        annotation["annotation_revision_reason"] = (
            "R1-B 修订：R1-A 数量语义（明确数词允许抽取）与冲突规则停用"
            "（室内自然光 / 摄影水彩 / 远景方图 / 摄影棚海滩）；仅改动有独立语义"
            "理由的答案与必要对话轮次，未运行系统输出生成期望。"
        )
        if case_id.startswith("s04-conflict"):
            revise_s04_annotation(annotation)
        else:
            for turn in annotation["turn_annotations"]:
                _strip_conflicts(turn)

        _count_revision(annotation["turn_annotations"], new_case["turns"])
        _add_count_semantics_prompt_group(annotation, new_case["turns"])

        for index, turn in enumerate(annotation["turn_annotations"]):
            groups = derive_preserve_path_groups(annotation["turn_annotations"], index)
            if groups:
                turn["preserve_path_groups"] = groups
            else:
                turn.pop("preserve_path_groups", None)

        new_annotations.append(annotation)

    dump_jsonl(NEW_FIXTURE, new_fixtures)
    dump_jsonl(NEW_ANNOTATIONS, new_annotations)
    return len(new_fixtures), len(new_annotations)


if __name__ == "__main__":
    fixture_count, annotation_count = build()
    print(f"wrote {NEW_FIXTURE} ({fixture_count} cases)")
    print(f"wrote {NEW_ANNOTATIONS} ({annotation_count} annotations)")
