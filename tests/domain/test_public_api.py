"""公开面测试：`visual_intent_agent.domain` 的再导出、模型不可变性、无外部依赖。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

import visual_intent_agent.domain as domain
from visual_intent_agent.domain import (
    CameraFacet,
    ColorFacet,
    CompositionFacet,
    DeltaOperation,
    EnvironmentFacet,
    EvidenceRef,
    ExecutionRevision,
    IntentDelta,
    IntentRevision,
    Issue,
    LightingFacet,
    Resolution,
    ResolutionRecord,
    Severity,
    StyleFacet,
    SubjectFacet,
    VisualIntent,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_PUBLIC_NAMES = {
    "SCHEMA_VERSION",
    "INTENT_PATHS",
    "SYSTEM_FIELD_NAMES",
    "Id",
    "new_id",
    "utc_now",
    "Severity",
    "EvidenceRef",
    "Issue",
    "Resolution",
    "ResolutionRecord",
    "VisualIntent",
    "SubjectFacet",
    "CompositionFacet",
    "EnvironmentFacet",
    "StyleFacet",
    "LightingFacet",
    "CameraFacet",
    "ColorFacet",
    "DeltaOperation",
    "IntentDelta",
    "IntentRevision",
    "ExecutionRevision",
}

CONTRACT_MODELS = [
    VisualIntent,
    SubjectFacet,
    CompositionFacet,
    EnvironmentFacet,
    StyleFacet,
    LightingFacet,
    CameraFacet,
    ColorFacet,
    ResolutionRecord,
    EvidenceRef,
    Issue,
    IntentDelta,
    IntentRevision,
    ExecutionRevision,
]


def test_domain_package_reexports_the_frozen_public_surface() -> None:
    assert set(domain.__all__) == EXPECTED_PUBLIC_NAMES
    for name in EXPECTED_PUBLIC_NAMES:
        assert hasattr(domain, name), name


@pytest.mark.parametrize("model", CONTRACT_MODELS, ids=lambda m: m.__name__)
def test_contract_models_are_frozen_and_forbid_extra_fields(model: type[BaseModel]) -> None:
    assert model.model_config.get("frozen") is True
    assert model.model_config.get("extra") == "forbid"


@pytest.mark.parametrize("enum_type", [Resolution, DeltaOperation, Severity])
def test_enums_are_string_enums(enum_type: type) -> None:
    assert issubclass(enum_type, str)
    for member in enum_type:
        assert isinstance(member.value, str)


def test_domain_package_has_no_provider_database_or_web_dependency() -> None:
    code = (
        "import sys\n"
        "import visual_intent_agent.domain\n"
        "banned = {'httpx', 'requests', 'sqlite3', 'fastapi', 'openai', 'PIL', 'numpy'}\n"
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
    assert "visual_intent_agent.config" not in internal_line
    assert "visual_intent_agent.providers" not in internal_line
