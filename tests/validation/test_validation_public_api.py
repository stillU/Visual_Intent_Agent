"""公开面测试：`visual_intent_agent.validation` 的再导出与依赖边界。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import visual_intent_agent.validation as validation
from validation_helpers import ALL_PATHS, context, full_intent, set_delta
from visual_intent_agent.domain import INTENT_PATHS
from visual_intent_agent.validation import (
    ChangeSummary,
    EvidenceContext,
    ReduceResult,
    ReducerError,
    RejectedDelta,
    ValidationResult,
    reduce,
    validate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_PUBLIC_NAMES = {
    "validate",
    "reduce",
    "ValidationResult",
    "ChangeSummary",
    "ReduceResult",
    "EvidenceContext",
    "RejectedDelta",
    "ReducerError",
}

BANNED_THIRD_PARTY = {
    "httpx",
    "requests",
    "urllib3",
    "sqlite3",
    "fastapi",
    "openai",
    "PIL",
    "numpy",
    "yaml",
    "dotenv",
}


def test_package_reexports_the_frozen_public_surface() -> None:
    assert set(validation.__all__) == EXPECTED_PUBLIC_NAMES
    for name in EXPECTED_PUBLIC_NAMES:
        assert hasattr(validation, name), name


def test_public_names_are_the_expected_objects() -> None:
    assert validation.validate is validate
    assert validation.reduce is reduce
    assert validation.ValidationResult is ValidationResult
    assert validation.ChangeSummary is ChangeSummary
    assert validation.ReduceResult is ReduceResult
    assert validation.EvidenceContext is EvidenceContext
    assert validation.RejectedDelta is RejectedDelta
    assert validation.ReducerError is ReducerError


def test_all_twelve_frozen_paths_are_imported_from_domain_not_redefined() -> None:
    # Step 02 只 import Step 01 的白名单，不维护第二份清单。
    assert set(ALL_PATHS) == set(INTENT_PATHS)
    assert "INTENT_PATHS" not in validation.__all__


def test_validation_package_has_no_llm_database_or_network_dependency() -> None:
    code = (
        "import sys\n"
        "import visual_intent_agent.validation\n"
        "banned = {'httpx', 'requests', 'urllib3', 'sqlite3', 'fastapi', 'openai', 'PIL', 'numpy', 'yaml', 'dotenv'}\n"
        "third_party = sorted({m.split('.')[0] for m in sys.modules} & banned)\n"
        "internal = sorted(m for m in sys.modules if m.startswith('visual_intent_agent'))\n"
        "print(repr(third_party))\n"
        "print(repr(internal))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    third_party_line, internal_line = result.stdout.strip().splitlines()
    assert third_party_line == "[]"
    assert internal_line == repr(
        [
            "visual_intent_agent",
            "visual_intent_agent.domain",
            "visual_intent_agent.domain.constants",
            "visual_intent_agent.domain.delta",
            "visual_intent_agent.domain.identifiers",
            "visual_intent_agent.domain.intent",
            "visual_intent_agent.domain.issue",
            "visual_intent_agent.domain.paths",
            "visual_intent_agent.domain.revision",
            "visual_intent_agent.validation",
            "visual_intent_agent.validation.models",
            "visual_intent_agent.validation.reducer",
            "visual_intent_agent.validation.validator",
        ]
    )


def test_validation_does_not_import_config_or_providers_in_a_clean_interpreter() -> None:
    # 无凭据、无 Settings：validation 是纯确定性层。
    # 必须在干净子进程里判断——tests/conftest.py 会 import config，污染当前进程的
    # sys.modules。
    code = (
        "import sys\n"
        "import visual_intent_agent.validation\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('visual_intent_agent'))\n"
        "print(repr(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = eval(result.stdout.strip())  # noqa: S307 - 子进程输出，内容受控
    assert "visual_intent_agent.config" not in loaded
    assert not any(name.startswith("visual_intent_agent.providers") for name in loaded)
    assert not any(name.startswith("visual_intent_agent.policy") for name in loaded)


def test_validator_module_has_no_network_or_llm_names() -> None:
    source = (PROJECT_ROOT / "visual_intent_agent" / "validation" / "validator.py").read_text(
        encoding="utf-8"
    )
    for banned in ("httpx", "requests", "sqlite3", "openai", "Settings", "load_settings"):
        assert banned not in source, banned


def test_reducer_module_has_no_network_or_llm_names() -> None:
    source = (PROJECT_ROOT / "visual_intent_agent" / "validation" / "reducer.py").read_text(
        encoding="utf-8"
    )
    for banned in ("httpx", "requests", "sqlite3", "openai", "Settings", "load_settings"):
        assert banned not in source, banned


def test_no_module_in_the_step_02_package_contains_an_api_key() -> None:
    package_dir = PROJECT_ROOT / "visual_intent_agent" / "validation"
    for path in sorted(package_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "sk-" not in text, path
        assert "api_key" not in text.lower(), path


@pytest.mark.parametrize(
    "step_03_or_later_name",
    [
        "assess",
        "DecisionPolicy",
        "IntentResolution",
        "QuestionSpec",
        "ready_for_confirmation",
        "IntentEngine",
        "Interpreter",
    ],
)
def test_package_does_not_pre_implement_later_steps(step_03_or_later_name: str) -> None:
    assert step_03_or_later_name not in validation.__all__
    assert not hasattr(validation, step_03_or_later_name)


def test_public_functions_are_callable_and_deterministic() -> None:
    intent = full_intent()
    deltas = [set_delta("style.primary", "cinematic")]
    ctx = context("msg_1")
    first = validate(deltas, ctx, intent)
    second = validate(deltas, ctx, intent)
    assert first.model_dump_json() == second.model_dump_json()
    assert reduce(intent, first.accepted).model_dump_json() == reduce(
        intent, second.accepted
    ).model_dump_json()
