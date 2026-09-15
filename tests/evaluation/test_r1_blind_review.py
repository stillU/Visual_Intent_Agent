"""R1-B #6：盲评上下文呈现“完整用户目标 + 必须保留约束”的离线测试。"""

from __future__ import annotations

from pathlib import Path

from evaluation.image_review import (
    PATH_LABELS,
    case_background_lines,
    case_review_constraints,
    load_fixture_turns,
)
from evaluation.reporting import load_annotations

PROJECT_ROOT = Path(__file__).resolve().parents[2]
R1_ANNOTATIONS = PROJECT_ROOT / "evaluation" / "annotations" / "core_v0_3_r1.jsonl"
R1_FIXTURE = PROJECT_ROOT / "evaluation" / "fixtures" / "core_v0_3_r1.jsonl"


def test_full_relevant_user_text_is_presented() -> None:
    turns = load_fixture_turns(R1_FIXTURE)
    lines = case_background_lines(turns["s09-multiturn-001"], 3)
    assert [line["label"] for line in lines] == ["第1轮用户输入", "第2轮用户输入", "第3轮用户输入"]
    assert any("镜头拉远" in line["text"] for line in lines)


def test_review_constraints_show_preserve_and_target_change() -> None:
    annotations = {a.case_id: a for a in load_annotations(R1_ANNOTATIONS)}
    constraints = case_review_constraints(annotations["s09-multiturn-001"], "t3")
    assert constraints["must_preserve"]
    assert all(item in PATH_LABELS.values() for item in constraints["must_preserve"])
    assert constraints["target_changes"]
    assert any("SET" in item for item in constraints["target_changes"])
    # 目标改动只展示语义标签，不泄露系统身份/内部 ID。
    assert not any("system_b" in item or "baseline" in item for item in constraints["target_changes"])


def test_accept_turn_preserves_all_paths() -> None:
    annotations = {a.case_id: a for a in load_annotations(R1_ANNOTATIONS)}
    constraints = case_review_constraints(annotations["s04-conflict-001"], "t3")
    assert len(constraints["must_preserve"]) == 12
    assert constraints["target_changes"] == []
