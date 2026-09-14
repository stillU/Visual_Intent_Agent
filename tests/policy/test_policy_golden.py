"""Golden 测试：`tests/fixtures/policy_cases/*.json` 驱动完整 IntentResolution 断言。

每个 fixture 固定一个 Intent（+ 可选 ExecutionRevision）与其**完整**预期
`IntentResolution`（全字段，含 intent 回显）。fixture 为版本化 JSON，重复加载与
序列化必须稳定。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from visual_intent_agent.domain import ExecutionRevision, VisualIntent
from visual_intent_agent.policy import IntentResolution, assess

CASES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "policy_cases"
CASE_FILES = sorted(CASES_DIR.glob("*.json"))
CASE_IDS = [path.stem for path in CASE_FILES]

REQUIRED_CASE_IDS = {
    "empty_intent",
    "subject_only_core_missing",
    "delegated_delegatable_resolves",
    "delegated_not_delegatable_issue",
    "not_applicable_invalid_issue",
    "omit_not_blocking",
    "hard_conflict_over_missing",
    "ready_for_confirmation",
}


def _load(case_path: Path) -> dict:
    return json.loads(case_path.read_text(encoding="utf-8"))


def test_all_mandatory_scenario_cases_exist() -> None:
    assert REQUIRED_CASE_IDS <= set(CASE_IDS)


@pytest.mark.parametrize("case_path", CASE_FILES, ids=CASE_IDS)
def test_golden_case_matches_the_full_intent_resolution(case_path: Path) -> None:
    case = _load(case_path)
    intent = VisualIntent.model_validate(case["intent"])
    execution_context = (
        ExecutionRevision.model_validate(case["execution_context"])
        if case["execution_context"] is not None
        else None
    )
    result = assess(intent, execution_context)
    assert json.loads(result.model_dump_json()) == case["expected"]


@pytest.mark.parametrize("case_path", CASE_FILES, ids=CASE_IDS)
def test_golden_case_is_reproducible(case_path: Path) -> None:
    case = _load(case_path)
    first = assess(VisualIntent.model_validate(case["intent"]))
    second = assess(VisualIntent.model_validate(case["intent"]))
    assert first.model_dump_json() == second.model_dump_json()


@pytest.mark.parametrize("case_path", CASE_FILES, ids=CASE_IDS)
def test_expected_resolution_round_trips(case_path: Path) -> None:
    case = _load(case_path)
    expected = IntentResolution.model_validate(case["expected"])
    assert json.loads(expected.model_dump_json()) == case["expected"]
    # 回显的 Intent 与输入逐值一致（assess 不修改输入）。
    assert expected.intent == VisualIntent.model_validate(case["intent"])


@pytest.mark.parametrize("case_path", CASE_FILES, ids=CASE_IDS)
def test_golden_case_has_a_description_and_stable_id(case_path: Path) -> None:
    case = _load(case_path)
    assert case["case_id"] == case_path.stem
    assert case["description"].strip()


def test_golden_case_ids_are_unique() -> None:
    assert len(CASE_IDS) == len(set(CASE_IDS))
