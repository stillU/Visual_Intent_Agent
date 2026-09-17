#!/usr/bin/env python3
"""逐 case 执行 v0.5 后置收尾 F2 的当前验收清单，并生成机器可读结果。

清单是 `tests/fixtures/knowledge/v0_5_acceptance_cases.json`：绑定最终发布语料
`v0.5-approved-1`（`knowledge_base/v0.5/manifest.json` 的 SHA-256 指纹），
三路径 × 十类共 30 个 case，每个 case 映射一个**精确 pytest node id / 参数 id**。

本脚本在一个进程内：

1. 校验清单结构：30 个 case、唯一 `case_id`、唯一 `test_node_id`（禁止通用节点复用）、
   三路径 × 十类笛卡尔积完整、`derived_from_dataset` 显式、指纹与 manifest 实际字节一致；
2. 用 `pytest --collect-only` 收集清单涉及的两个模块，确认 30 个 node id 全部真实存在；
3. **逐个** node 单独执行（每个 node 一个 pytest 进程），因此每个 case 都有独立结果，
   未执行的 case 绝不标 pass；
4. 输出逐 case 结果 JSON + Markdown，并打印摘要表；任一 case 未通过则退出码 1。

不修改运行时代码 / 生产语料 / 审核归档；不联网；不调用 Provider；不写 handoff。

用法：
    uv run python tools/run_v0_5_acceptance_cases.py                  # 运行并写结果
    uv run python tools/run_v0_5_acceptance_cases.py --check          # 只校验清单与收集，不执行
    uv run python tools/run_v0_5_acceptance_cases.py --case camera.depth_of_field::pinned
    uv run python tools/run_v0_5_acceptance_cases.py --json-out /tmp/results.json --no-md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: 项目原创工程用例清单（非官方 benchmark，不引用数据集原文）。
DEFAULT_CASES = ROOT / "tests" / "fixtures" / "knowledge" / "v0_5_acceptance_cases.json"
DEFAULT_JSON_OUT = ROOT / "outputs" / "acceptance" / "v0_5_acceptance_results.json"
DEFAULT_MD_OUT = ROOT / "outputs" / "acceptance" / "v0_5_acceptance_results.md"

EXPECTED_SCHEMA = "v0.5-acceptance-cases.v1"
EXPECTED_CORPUS_VERSION = "v0.5-approved-1"
EXPECTED_PATHS = ("camera.depth_of_field", "composition.framing", "lighting.character")
EXPECTED_CATEGORIES = (
    "delegated",
    "explicit_value",
    "pinned",
    "unconfirmed_condition",
    "condition_mismatch",
    "model_mismatch",
    "unapproved_knowledge",
    "conflict",
    "missing_knowledge",
    "successful_adoption",
)
EXPECTED_CASE_COUNT = 30
ALLOWED_OUTCOMES = {"adopted", "not_adopted", "rejected", "not_overwritten"}

_NODE_LINE = re.compile(r"^(?P<node>tests/[^\s:]+\.py::[^\s]+)$")
_COLLECTED_COUNT = re.compile(r"^(?P<count>\d+) tests? collected")
_DESELECTED_COUNT = re.compile(r"(?P<count>\d+) deselected")
_SKIPPED_COUNT = re.compile(r"(?P<count>\d+) skipped")
_SHORT_SUMMARY = re.compile(r"^=+\s*(?P<body>.+?)\s*=+$")


# ---------------------------------------------------------------------------
# 清单结构校验
# ---------------------------------------------------------------------------


def load_cases(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_cases(doc: dict[str, Any], *, project_root: Path = ROOT) -> list[str]:
    """返回问题列表（空 = 清单结构、绑定与唯一映射全部通过）。"""
    problems: list[str] = []

    if doc.get("schema") != EXPECTED_SCHEMA:
        problems.append(f"schema must be {EXPECTED_SCHEMA!r}, got {doc.get('schema')!r}")
    if doc.get("corpus_version") != EXPECTED_CORPUS_VERSION:
        problems.append(
            f"corpus_version must be {EXPECTED_CORPUS_VERSION!r}, got {doc.get('corpus_version')!r}"
        )
    if tuple(doc.get("paths", ())) != EXPECTED_PATHS:
        problems.append(f"paths must be exactly {list(EXPECTED_PATHS)}, got {doc.get('paths')!r}")
    if tuple(doc.get("categories", ())) != EXPECTED_CATEGORIES:
        problems.append(
            f"categories must be exactly {list(EXPECTED_CATEGORIES)}, got {doc.get('categories')!r}"
        )
    derivation = str(doc.get("dataset_derivation", "")).lower()
    if "none" not in derivation:
        problems.append("dataset_derivation must state that no case is dataset-derived")

    # 清单声明的执行入口必须真实存在（防止"填了节点/工具名但文件不在"）。
    for key in ("test_module", "reused_test_module", "run_tool"):
        rel = str(doc.get(key, ""))
        if not rel:
            problems.append(f"declared {key!r} is missing")
        elif not (project_root / rel).is_file():
            problems.append(f"declared {key} {rel!r} does not exist on disk")

    manifest_rel = str(doc.get("corpus_manifest_path", ""))
    manifest = project_root / manifest_rel
    if not manifest.is_file():
        problems.append(f"corpus manifest {manifest_rel!r} does not exist")
    else:
        actual = sha256_file(manifest)
        declared = str(doc.get("corpus_manifest_sha256", ""))
        if declared != actual:
            problems.append(
                f"corpus_manifest_sha256 mismatch: declared {declared!r} != actual {actual!r}"
            )
        manifest_doc = json.loads(manifest.read_text(encoding="utf-8"))
        if manifest_doc.get("corpus_version") != EXPECTED_CORPUS_VERSION:
            problems.append(
                "corpus manifest corpus_version "
                f"{manifest_doc.get('corpus_version')!r} != {EXPECTED_CORPUS_VERSION!r}"
            )

    cases = doc.get("cases")
    if not isinstance(cases, list):
        return problems + ["cases must be a list"]
    if len(cases) != EXPECTED_CASE_COUNT:
        problems.append(f"expected {EXPECTED_CASE_COUNT} cases, got {len(cases)}")

    case_ids: list[str] = []
    node_ids: list[str] = []
    combos: set[tuple[str, str]] = set()
    for index, case in enumerate(cases):
        label = f"cases[{index}]"
        if not isinstance(case, dict):
            problems.append(f"{label} is not an object")
            continue
        case_id = str(case.get("case_id", ""))
        node_id = str(case.get("test_node_id", ""))
        path = str(case.get("knowledge_path", ""))
        category = str(case.get("category", ""))
        case_ids.append(case_id)
        node_ids.append(node_id)
        combos.add((path, category))

        if case_id != f"{path}::{category}":
            problems.append(f"{label} case_id {case_id!r} does not equal path::category")
        if "::" not in node_id:
            problems.append(f"{label} test_node_id {node_id!r} is not a pytest node id")
        if case.get("derived_from_dataset") is not False:
            problems.append(f"{label} must declare derived_from_dataset=false (project-original)")
        expected = case.get("expected")
        if not isinstance(expected, dict) or expected.get("outcome") not in ALLOWED_OUTCOMES:
            problems.append(f"{label} expected.outcome is missing/unsupported")
        for required in ("precondition", "fixture_type", "executor"):
            if not case.get(required):
                problems.append(f"{label} is missing {required!r}")
        if case.get("executor") not in {"matrix", "reused"}:
            problems.append(f"{label} executor {case.get('executor')!r} is not matrix/reused")

    # executor 与目标模块一致：matrix 走执行矩阵模块，reused 走既有发布采用模块。
    matrix_module = str(doc.get("test_module", ""))
    reused_module = str(doc.get("reused_test_module", ""))
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        node = str(case.get("test_node_id", ""))
        expected_module = matrix_module if case.get("executor") == "matrix" else reused_module
        if expected_module and not node.startswith(f"{expected_module}::"):
            problems.append(
                f"cases[{index}] executor={case.get('executor')!r} but node {node!r} "
                f"is not under {expected_module!r}"
            )

    duplicates = sorted({item for item in case_ids if case_ids.count(item) > 1})
    if duplicates:
        problems.append(f"duplicate case_id(s): {duplicates}")
    duplicate_nodes = sorted({item for item in node_ids if node_ids.count(item) > 1})
    if duplicate_nodes:
        problems.append(
            "multiple cases share one generic test node (forbidden): " f"{duplicate_nodes}"
        )

    expected_combos = {(path, category) for path in EXPECTED_PATHS for category in EXPECTED_CATEGORIES}
    if combos != expected_combos:
        missing = sorted(expected_combos - combos)
        extra = sorted(combos - expected_combos)
        problems.append(f"cartesian product incomplete: missing={missing} extra={extra}")

    return problems


# ---------------------------------------------------------------------------
# 收集 / 执行
# ---------------------------------------------------------------------------


def module_of(node_id: str) -> str:
    return node_id.split("::", 1)[0]


def collect_modules(
    modules: list[str], *, project_root: Path = ROOT, timeout: float = 600.0
) -> tuple[set[str], str]:
    """收集给定模块的 node id 集合；返回 (collected, combined_output)。"""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *modules,
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    collected: set[str] = set()
    for line in combined.splitlines():
        stripped = line.strip()
        match = _NODE_LINE.match(stripped)
        if match and "::" in match.group("node"):
            collected.add(match.group("node"))
        elif _COLLECTED_COUNT.match(stripped) and "error" in stripped.lower():
            collected.add("")  # 收集错误：用空串让调用方判定失败
    if proc.returncode != 0:
        collected.add("")
    return collected, combined


def run_case(node_id: str, *, project_root: Path = ROOT, timeout: float = 900.0) -> dict[str, Any]:
    """单独执行一个 node；返回结构化结果（未执行/超时绝不标 pass）。

    机器结果以 pytest 的临时 JUnit XML 为准（交付 JSON 不写临时绝对路径），
    并额外记录 deselect / skip 数量，确保"没有 skip/xfail/deselect 顶替执行"。
    """
    started = datetime.now(timezone.utc)
    fd, junit_name = tempfile.mkstemp(prefix="v05_case_", suffix=".xml")
    os.close(fd)
    junit_path = Path(junit_name)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-rA",
                "-p",
                "no:cacheprovider",
                f"--junit-xml={junit_path}",
                node_id,
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        status, detail = _classify(proc.returncode, output, junit_path)
        deselected = _count(_DESELECTED_COUNT, output)
        skipped = _count(_SKIPPED_COUNT, output)
    except subprocess.TimeoutExpired:
        return {
            "node_id": node_id,
            "status": "timeout",
            "returncode": None,
            "duration_s": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
            "deselected": 0,
            "skipped": 0,
            "detail": f"pytest exceeded {timeout:.0f}s",
        }
    finally:
        junit_path.unlink(missing_ok=True)

    if deselected:
        status = "deselected"
        detail = f"node was deselected ({deselected}); it did not actually execute"
    elif skipped and status == "pass":
        status = "skipped"
        detail = f"node reported skipped ({skipped}); a skip must not count as an executed case"

    return {
        "node_id": node_id,
        "status": status,
        "returncode": proc.returncode,
        "duration_s": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
        "deselected": deselected,
        "skipped": skipped,
        "detail": detail,
    }


def _count(pattern: re.Pattern[str], output: str) -> int:
    total = 0
    for match in pattern.finditer(output):
        total = max(total, int(match.group("count")))
    return total


def _classify(returncode: int, output: str, junit_path: Path) -> tuple[str, str]:
    """优先读 JUnit XML 判定该 node 的终态；无法判定时退回 pytest 输出文本。"""
    outcome = _junit_outcome(junit_path)
    if outcome is None:
        if returncode == 0:
            outcome = "pass"
        else:
            outcome = "error" if "ERROR" in output else "fail"

    if outcome == "pass" and returncode != 0:
        # 进程异常退出就不算通过，避免"XML 有 pass 但进程失败"被吞掉。
        return "fail", _summarize(output)
    if outcome == "pass":
        return "pass", ""
    if outcome in {"not_executed", "skipped", "xfail", "xpass"}:
        return outcome, _summarize(output)
    return outcome, _summarize(output)


def _junit_outcome(junit_path: Path) -> str | None:
    if not junit_path.is_file():
        return None
    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError:
        return None
    testcases = list(root.iter("testcase"))
    if not testcases:
        return "not_executed"
    if len(testcases) != 1:
        # 单个 node 的执行本应只有一个 testcase；多于一个说明选择了整模块。
        return "error"
    node = testcases[0]
    for child in node:
        tag = child.tag
        if tag == "failure":
            return "fail"
        if tag == "error":
            return "error"
        if tag == "skipped":
            skipped_type = (child.get("type") or "").lower()
            return "xfail" if "xfail" in skipped_type else "skipped"
    return "pass"


def _summarize(output: str) -> str:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    summary = [line for line in lines if _SHORT_SUMMARY.match(line)]
    tail = summary[-3:] if summary else lines[-6:]
    return "\n".join(tail)[-2000:]


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


def execute(
    *,
    cases_path: Path = DEFAULT_CASES,
    project_root: Path = ROOT,
    only: str | None = None,
    check_only: bool = False,
    collect_timeout: float = 600.0,
    case_timeout: float = 900.0,
) -> dict[str, Any]:
    doc = load_cases(cases_path)
    problems = validate_cases(doc, project_root=project_root)
    cases = doc.get("cases", []) if isinstance(doc.get("cases"), list) else []

    modules = sorted({module_of(str(case.get("test_node_id", ""))) for case in cases})
    collected, collect_output = collect_modules(
        modules, project_root=project_root, timeout=collect_timeout
    )
    declared_nodes = [str(case.get("test_node_id", "")) for case in cases]
    missing_nodes = sorted(node for node in declared_nodes if node not in collected)
    if missing_nodes:
        problems.append(f"declared node id(s) not collected: {missing_nodes}")

    result: dict[str, Any] = {
        "schema": "v0.5-acceptance-results.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases_path": str(cases_path.relative_to(project_root)),
        "corpus_version": doc.get("corpus_version"),
        "corpus_manifest_path": doc.get("corpus_manifest_path"),
        "corpus_manifest_sha256_declared": doc.get("corpus_manifest_sha256"),
        "corpus_manifest_sha256_actual": None,
        "modules": modules,
        "collected_nodes": len([node for node in collected if node]),
        "case_count": len(cases),
        "structure_problems": problems,
        "checked_only": check_only,
        "summary": {},
        "results": [],
    }
    manifest = project_root / str(doc.get("corpus_manifest_path", ""))
    if manifest.is_file():
        result["corpus_manifest_sha256_actual"] = sha256_file(manifest)

    if check_only:
        result["summary"] = {
            "total": len(cases),
            "executed": 0,
            "pass": 0,
            "not_pass": 0,
            "structure_ok": not problems,
        }
        return result

    selected = [case for case in cases if only is None or str(case.get("case_id")) == only]
    if only is not None and not selected:
        result["summary"] = {
            "total": len(cases),
            "executed": 0,
            "pass": 0,
            "not_pass": 0,
            "structure_ok": not problems,
        }
        return result

    executed = 0
    passed = 0
    for case in selected:
        node_id = str(case.get("test_node_id", ""))
        outcome = run_case(node_id, project_root=project_root, timeout=case_timeout)
        executed += 1
        if outcome["status"] == "pass":
            passed += 1
        result["results"].append(
            {
                "case_id": case.get("case_id"),
                "knowledge_path": case.get("knowledge_path"),
                "category": case.get("category"),
                "executor": case.get("executor"),
                "fixture_type": case.get("fixture_type"),
                "expected_outcome": (case.get("expected") or {}).get("outcome"),
                **outcome,
            }
        )

    result["summary"] = {
        "total": len(cases),
        "selected": len(selected),
        "executed": executed,
        "pass": passed,
        "not_pass": executed - passed,
        "skipped": sum(int(item.get("skipped") or 0) for item in result["results"]),
        "deselected": sum(int(item.get("deselected") or 0) for item in result["results"]),
        "structure_ok": not problems,
    }
    return result


def render_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# v0.5 F2 当前验收清单逐 case 结果")
    lines.append("")
    lines.append(f"- 生成时间（UTC）：{result['generated_at']}")
    lines.append(f"- 清单：`{result['cases_path']}`")
    lines.append(
        f"- 语料：`{result.get('corpus_version')}` / `{result.get('corpus_manifest_path')}` "
        f"sha256 `{result.get('corpus_manifest_sha256_actual')}`"
    )
    summary = result.get("summary", {})
    lines.append(
        f"- 结果：executed {summary.get('executed')}/{summary.get('total')}，"
        f"pass {summary.get('pass')}，not_pass {summary.get('not_pass')}，"
        f"structure_ok {summary.get('structure_ok')}"
    )
    lines.append("")
    if result.get("checked_only"):
        lines.append("（`--check`：只校验清单与收集，未执行 case。）")
        lines.append("")
        for problem in result.get("structure_problems", []):
            lines.append(f"- PROBLEM: {problem}")
        return "\n".join(lines).rstrip() + "\n"

    lines.append("| case_id | executor | status | node_id |")
    lines.append("|---|---|---|---|")
    for item in result.get("results", []):
        lines.append(
            f"| `{item['case_id']}` | {item['executor']} | {item['status']} | `{item['node_id']}` |"
        )
    if result.get("structure_problems"):
        lines.append("")
        lines.append("## 结构问题")
        for problem in result["structure_problems"]:
            lines.append(f"- {problem}")
    failures = [item for item in result.get("results", []) if item["status"] != "pass"]
    if failures:
        lines.append("")
        lines.append("## 未通过明细")
        for item in failures:
            lines.append(f"### {item['case_id']} → {item['status']}")
            lines.append("")
            lines.append("```")
            lines.append(item.get("detail", ""))
            lines.append("```")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--md-out", type=Path, default=DEFAULT_MD_OUT)
    parser.add_argument("--no-md", action="store_true", help="不写 Markdown 报告")
    parser.add_argument("--check", action="store_true", help="只校验清单与收集，不执行 case")
    parser.add_argument("--case", default=None, help="只执行一个 case_id（调试用）")
    parser.add_argument("--timeout", type=float, default=900.0, help="单个 case 的 pytest 超时秒数")
    args = parser.parse_args(argv)

    result = execute(
        cases_path=args.cases,
        only=args.case,
        check_only=args.check,
        case_timeout=args.timeout,
    )

    print(f"cases: {result['case_count']}  modules: {', '.join(result['modules'])}")
    print(
        "corpus: "
        f"{result.get('corpus_version')} manifest_sha256="
        f"{result.get('corpus_manifest_sha256_actual')}"
    )
    if result["structure_problems"]:
        print("STRUCTURE: FAIL")
        for problem in result["structure_problems"]:
            print(f"  - {problem}")
    else:
        print("STRUCTURE: OK")

    if not result.get("checked_only"):
        for item in result["results"]:
            print(f"  [{item['status']:<12}] {item['case_id']:<48} {item['node_id']}")
        summary = result["summary"]
        print(
            f"RESULT: executed {summary['executed']}/{summary['total']} "
            f"pass={summary['pass']} not_pass={summary['not_pass']} "
            f"skipped={summary['skipped']} deselected={summary['deselected']}"
        )

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"json: {args.json_out}")
    if not args.no_md:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(render_markdown(result), encoding="utf-8")
        print(f"md:   {args.md_out}")

    ok = not result["structure_problems"]
    if not result.get("checked_only"):
        summary = result["summary"]
        ok = (
            ok
            and summary["executed"] == summary["total"]
            and summary["not_pass"] == 0
            and summary["skipped"] == 0
            and summary["deselected"] == 0
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
