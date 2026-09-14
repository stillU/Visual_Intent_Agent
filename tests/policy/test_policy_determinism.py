"""确定性测试：同输入多次调用、跨进程 hash seed、以及无随机/时钟依赖。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from policy_helpers import empty_intent, intent_from, ready_intent, with_resolution
from visual_intent_agent.policy import IntentResolution, assess

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "policy_cases"

_SUBPROCESS_CODE = (
    "import hashlib, json, sys\n"
    "from pathlib import Path\n"
    "from visual_intent_agent.domain import VisualIntent\n"
    "from visual_intent_agent.policy import assess\n"
    "case = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
    "intent = VisualIntent.model_validate(case['intent'])\n"
    "payload = assess(intent).model_dump_json()\n"
    "print(hashlib.sha256(payload.encode('utf-8')).hexdigest())\n"
)


def _digest(case_path: Path, hash_seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_CODE, str(case_path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    "case_id",
    ["empty_intent", "hard_conflict_over_missing", "delegated_delegatable_resolves"],
)
def test_digest_is_identical_across_hash_seeds(case_id: str) -> None:
    case_path = CASES_DIR / f"{case_id}.json"
    digests = {_digest(case_path, seed) for seed in ("0", "7", "424242")}
    assert len(digests) == 1, digests


def test_repeated_calls_are_byte_identical() -> None:
    for intent in (empty_intent(), ready_intent(), intent_from({"environment.mode": "studio"})):
        first = assess(intent).model_dump_json()
        for _ in range(5):
            assert assess(intent).model_dump_json() == first


def test_repeated_calls_do_not_mutate_the_input() -> None:
    intent = intent_from({"subject.description": "a lone astronaut"})
    before = intent.model_dump_json()
    for _ in range(3):
        assess(intent)
    assert intent.model_dump_json() == before


def test_policy_ignores_pinned_paths() -> None:
    base = with_resolution(empty_intent(), "style.primary", "user_delegated")
    first = base.model_copy(update={"pinned_paths": frozenset({"subject.description"})})
    second = base.model_copy(
        update={"pinned_paths": frozenset({"subject.description", "color.palette"})}
    )
    # policy v1 不读取 pinned_paths；它只影响确认失效（Step 02/04）。
    assert assess(first).unresolved_decisions == assess(second).unresolved_decisions


def test_applied_deltas_are_always_empty_for_assess() -> None:
    for intent in (empty_intent(), ready_intent()):
        assert assess(intent).applied_deltas == []


def test_assess_result_json_round_trips_byte_stable() -> None:
    intent = intent_from({"environment.mode": "studio", "environment.location": "the beach"})
    dumped = assess(intent).model_dump_json()
    reloaded = IntentResolution.model_validate_json(dumped)
    assert reloaded.model_dump_json() == dumped
    assert reloaded.intent == intent
