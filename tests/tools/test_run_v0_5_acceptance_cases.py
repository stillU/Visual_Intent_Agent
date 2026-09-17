"""v0.5 后置收尾 F2 验收执行器（``tools/run_v0_5_acceptance_cases.py``）的离线测试。

覆盖：

- 清单结构校验：30 case、唯一 ``case_id`` / 唯一 ``test_node_id``、三路径 × 十类笛卡尔积、
  显式 ``derived_from_dataset``、manifest 指纹与最终语料实际字节一致；
- 篡改检测：缺 case、重复 node、跨路径少一类、指纹不符都必须被拒绝（不改真实文件）；
- 机器结果：单个 node 的 JUnit XML 终态映射（pass / fail / skipped / not_executed），
  ``deselected`` / ``skipped`` 不会被当作已执行通过，交付 JSON 不写临时绝对路径；
- Markdown 渲染包含逐 case 状态与失败明细。

全部离线、只读真实清单与生产语料；用临时副本做负例，不改真实文件。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from tools.run_v0_5_acceptance_cases import (
    DEFAULT_CASES,
    EXPECTED_CASE_COUNT,
    EXPECTED_CATEGORIES,
    EXPECTED_PATHS,
    ROOT,
    collect_modules,
    execute,
    load_cases,
    render_markdown,
    run_case,
    sha256_file,
    validate_cases,
)

RELEASE_MANIFEST = ROOT / "knowledge_base" / "v0.5" / "manifest.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _real_doc() -> dict:
    return load_cases(DEFAULT_CASES)


def _assert_only_problem_contains(problems: list[str], needle: str) -> None:
    assert problems, "expected at least one problem"
    assert any(needle in problem for problem in problems), problems


# ---------------------------------------------------------------------------
# structural validation
# ---------------------------------------------------------------------------


def test_real_acceptance_manifest_is_structurally_valid_and_bound() -> None:
    doc = _real_doc()
    assert validate_cases(doc) == []
    assert len(doc["cases"]) == EXPECTED_CASE_COUNT == 30
    assert tuple(doc["paths"]) == EXPECTED_PATHS
    assert tuple(doc["categories"]) == EXPECTED_CATEGORIES
    assert doc["corpus_version"] == "v0.5-approved-1"
    # 指纹绑定最终生产快照的实际字节。
    assert doc["corpus_manifest_sha256"] == sha256_file(RELEASE_MANIFEST)
    # 每条都是项目原创，非数据集派生。
    assert all(case["derived_from_dataset"] is False for case in doc["cases"])


def test_manifest_covers_cartesian_product_of_paths_and_categories() -> None:
    doc = _real_doc()
    combos = {(case["knowledge_path"], case["category"]) for case in doc["cases"]}
    assert combos == {(path, category) for path in EXPECTED_PATHS for category in EXPECTED_CATEGORIES}
    assert len(combos) == 30


def test_manifest_node_ids_and_case_ids_are_unique() -> None:
    doc = _real_doc()
    case_ids = [case["case_id"] for case in doc["cases"]]
    node_ids = [case["test_node_id"] for case in doc["cases"]]
    assert len(set(case_ids)) == 30
    # 30 个 case 必须映射 30 个不同节点：禁止同一通用节点复用凑数。
    assert len(set(node_ids)) == 30


def test_validate_rejects_duplicate_node_id() -> None:
    doc = copy.deepcopy(_real_doc())
    doc["cases"][1]["test_node_id"] = doc["cases"][0]["test_node_id"]
    problems = validate_cases(doc)
    _assert_only_problem_contains(problems, "generic test node")


def test_validate_rejects_duplicate_case_id() -> None:
    doc = copy.deepcopy(_real_doc())
    doc["cases"][1]["case_id"] = doc["cases"][0]["case_id"]
    problems = validate_cases(doc)
    _assert_only_problem_contains(problems, "duplicate case_id")


def test_validate_rejects_missing_and_extra_cases() -> None:
    doc = copy.deepcopy(_real_doc())
    dropped = doc["cases"].pop()
    problems = validate_cases(doc)
    _assert_only_problem_contains(problems, "expected 30 cases")
    assert any("cartesian product incomplete" in problem for problem in problems)
    assert any(
        dropped["knowledge_path"] in problem and dropped["category"] in problem
        for problem in problems
    )


def test_validate_rejects_wrong_manifest_fingerprint() -> None:
    doc = copy.deepcopy(_real_doc())
    doc["corpus_manifest_sha256"] = "0" * 64
    problems = validate_cases(doc)
    _assert_only_problem_contains(problems, "corpus_manifest_sha256 mismatch")


def test_validate_rejects_dataset_derived_case_and_bad_outcome() -> None:
    doc = copy.deepcopy(_real_doc())
    doc["cases"][0]["derived_from_dataset"] = True
    doc["cases"][0]["expected"]["outcome"] = "should_succeed"
    problems = validate_cases(doc)
    _assert_only_problem_contains(problems, "derived_from_dataset=false")
    _assert_only_problem_contains(problems, "expected.outcome")


def test_validate_rejects_case_id_not_matching_path_and_category() -> None:
    doc = copy.deepcopy(_real_doc())
    doc["cases"][2]["case_id"] = "not-a-path::not-a-category"
    problems = validate_cases(doc)
    _assert_only_problem_contains(problems, "does not equal path::category")


def test_validate_rejects_missing_run_tool_and_executor_module_drift() -> None:
    doc = copy.deepcopy(_real_doc())
    doc["run_tool"] = "tools/does_not_exist.py"
    problems = validate_cases(doc)
    _assert_only_problem_contains(problems, "run_tool 'tools/does_not_exist.py' does not exist")

    drifted = copy.deepcopy(_real_doc())
    drifted["cases"][0]["executor"] = "reused"  # matrix case 却指向矩阵模块
    problems = validate_cases(drifted)
    _assert_only_problem_contains(problems, "is not under")


def test_validate_rejects_missing_matrix_module_declaration() -> None:
    doc = copy.deepcopy(_real_doc())
    doc.pop("test_module")
    problems = validate_cases(doc)
    _assert_only_problem_contains(problems, "declared 'test_module' is missing")


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------


def test_collect_modules_finds_declared_nodes_without_temp_files() -> None:
    doc = _real_doc()
    modules = sorted({case["test_node_id"].split("::", 1)[0] for case in doc["cases"]})
    collected, output = collect_modules(modules)
    assert "" not in collected, output[-2000:]
    missing = [case["test_node_id"] for case in doc["cases"] if case["test_node_id"] not in collected]
    assert missing == []


# ---------------------------------------------------------------------------
# per-node machine results
# ---------------------------------------------------------------------------


def test_run_case_records_pass_for_a_real_node() -> None:
    node = (
        "tests/generation/test_generation_v0_5_acceptance_matrix.py"
        "::test_acceptance_case[camera.depth_of_field::pinned]"
    )
    outcome = run_case(node)
    assert outcome["status"] == "pass"
    assert outcome["returncode"] == 0
    assert outcome["deselected"] == 0
    assert outcome["skipped"] == 0
    assert "/tmp" not in json.dumps(outcome)


def test_run_case_never_marks_unexecuted_node_as_pass() -> None:
    outcome = run_case("tests/generation/test_generation_v0_5_acceptance_matrix.py::test_does_not_exist")
    assert outcome["status"] != "pass"


def test_execute_runs_every_declared_case_once_without_skips() -> None:
    result = execute()
    summary = result["summary"]
    assert result["structure_problems"] == []
    assert summary["total"] == 30
    assert summary["executed"] == 30
    assert summary["pass"] == 30
    assert summary["not_pass"] == 0
    assert summary["skipped"] == 0
    assert summary["deselected"] == 0
    assert len(result["results"]) == 30
    assert len({item["case_id"] for item in result["results"]}) == 30
    assert len({item["node_id"] for item in result["results"]}) == 30
    # 交付结果里不出现临时 JUnit 绝对路径。
    assert "v05_case_" not in json.dumps(result)
    assert "/tmp" not in json.dumps(result)


def test_execute_check_only_does_not_execute_cases() -> None:
    result = execute(check_only=True)
    assert result["checked_only"] is True
    assert result["summary"]["executed"] == 0
    assert result["results"] == []
    assert result["structure_problems"] == []


def test_execute_single_case_selects_exactly_one() -> None:
    result = execute(only="lighting.character::conflict")
    assert result["summary"]["selected"] == 1
    assert result["summary"]["executed"] == 1
    assert result["summary"]["pass"] == 1
    assert [item["case_id"] for item in result["results"]] == ["lighting.character::conflict"]


# ---------------------------------------------------------------------------
# markdown report
# ---------------------------------------------------------------------------


def test_render_markdown_lists_every_case_and_marks_failures() -> None:
    result = execute(check_only=True)
    checked = render_markdown(result)
    assert "--check" in checked
    result["checked_only"] = False
    result["results"] = [
        {
            "case_id": "camera.depth_of_field::delegated",
            "node_id": "tests/x.py::test[a]",
            "executor": "matrix",
            "status": "pass",
            "detail": "",
        },
        {
            "case_id": "camera.depth_of_field::conflict",
            "node_id": "tests/x.py::test[b]",
            "executor": "matrix",
            "status": "fail",
            "detail": "E   assert 1 == 2",
        },
    ]
    result["summary"] = {"executed": 2, "total": 2, "pass": 1, "not_pass": 1, "structure_ok": True}
    report = render_markdown(result)
    assert "`camera.depth_of_field::delegated`" in report
    assert "`camera.depth_of_field::conflict`" in report
    assert "未通过明细" in report
    assert "assert 1 == 2" in report


def test_cases_path_default_exists() -> None:
    assert Path(DEFAULT_CASES).is_file()
