"""公开面测试：`visual_intent_agent.policy` 的再导出与依赖边界。

policy 是纯确定性规则层：只允许 import `visual_intent_agent.domain` 与自身模块，
不得引入 LLM / 数据库 / 网络 / Prompt / 配置。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import visual_intent_agent.policy as policy
from visual_intent_agent.policy import (
    DECISION_POLICIES,
    POLICY_VERSION,
    DecisionPolicy,
    IntentResolution,
    Materiality,
    PolicyAction,
    QuestionSpec,
    UnresolvedDecision,
    assess,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_PUBLIC_NAMES = {
    "assess",
    "IntentResolution",
    "QuestionSpec",
    "DecisionPolicy",
    "Materiality",
    "PolicyAction",
    "UnresolvedDecision",
    "DECISION_POLICIES",
    "POLICY_VERSION",
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
    assert set(policy.__all__) == EXPECTED_PUBLIC_NAMES
    for name in EXPECTED_PUBLIC_NAMES:
        assert hasattr(policy, name), name


def test_public_names_are_the_expected_objects() -> None:
    assert policy.assess is assess
    assert policy.IntentResolution is IntentResolution
    assert policy.QuestionSpec is QuestionSpec
    assert policy.DecisionPolicy is DecisionPolicy
    assert policy.Materiality is Materiality
    assert policy.PolicyAction is PolicyAction
    assert policy.UnresolvedDecision is UnresolvedDecision
    assert policy.DECISION_POLICIES is DECISION_POLICIES
    assert policy.POLICY_VERSION == POLICY_VERSION


def test_policy_package_has_no_llm_database_or_network_dependency() -> None:
    code = (
        "import sys\n"
        "import visual_intent_agent.policy\n"
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
            "visual_intent_agent.policy",
            "visual_intent_agent.policy.decision_policy",
            "visual_intent_agent.policy.models",
        ]
    )


def test_policy_does_not_import_config_validation_or_providers() -> None:
    # 必须在干净子进程判断——tests/conftest.py 会 import config，污染当前 sys.modules。
    code = (
        "import sys\n"
        "import visual_intent_agent.policy\n"
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
    assert not any(name.startswith("visual_intent_agent.validation") for name in loaded)
    assert not any(name.startswith("visual_intent_agent.persistence") for name in loaded)
    assert not any(name.startswith("visual_intent_agent.prompt_engine") for name in loaded)


@pytest.mark.parametrize(
    "later_step_name",
    [
        "IntentEngine",
        "Interpreter",
        "PromptEngine",
        "PromptArtifact",
        "Repository",
        "WorkflowService",
        "FeedbackEngine",
        "GenerationPipeline",
        "QuestionBuilder",
    ],
)
def test_package_does_not_pre_implement_later_steps(later_step_name: str) -> None:
    assert later_step_name not in policy.__all__
    assert not hasattr(policy, later_step_name)


def test_policy_modules_have_no_llm_network_or_randomness_names() -> None:
    package_dir = PROJECT_ROOT / "visual_intent_agent" / "policy"
    forbidden = (
        "httpx",
        "requests",
        "sqlite3",
        "openai",
        "Settings",
        "load_settings",
        "random",
        "uuid",
        "time.time",
        "datetime.now",
        "open(",
    )
    for path in sorted(package_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path.name}: {token}"


def test_no_module_in_the_step_03_package_contains_an_api_key() -> None:
    package_dir = PROJECT_ROOT / "visual_intent_agent" / "policy"
    for path in sorted(package_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "sk-" not in text, path
        assert "api_key" not in text.lower(), path


def test_public_function_is_callable_and_deterministic() -> None:
    from visual_intent_agent.domain import VisualIntent

    intent = VisualIntent()
    first = assess(intent)
    second = assess(intent)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.applied_deltas == []
