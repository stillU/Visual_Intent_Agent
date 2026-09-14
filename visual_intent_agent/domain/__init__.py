"""Step 01 领域合同公开面（后续步骤统一从此处 import）。

    from visual_intent_agent.domain import (
        SCHEMA_VERSION, INTENT_PATHS, SYSTEM_FIELD_NAMES,
        Id, new_id, utc_now,
        Severity, EvidenceRef, Issue,
        Resolution, ResolutionRecord, VisualIntent,
        SubjectFacet, CompositionFacet, EnvironmentFacet, StyleFacet,
        LightingFacet, CameraFacet, ColorFacet,
        DeltaOperation, IntentDelta,
        IntentRevision, ExecutionRevision,
    )

本包只含纯数据合同：不含 Validator / Reducer / DecisionPolicy / LLM /
持久化 / HTTP API 任何逻辑。全部模型 `frozen=True, extra="forbid"`。
"""

from __future__ import annotations

from .constants import SCHEMA_VERSION
from .delta import DeltaOperation, IntentDelta
from .identifiers import Id, new_id, utc_now
from .intent import (
    CameraFacet,
    ColorFacet,
    CompositionFacet,
    EnvironmentFacet,
    LightingFacet,
    Resolution,
    ResolutionRecord,
    StyleFacet,
    SubjectFacet,
    VisualIntent,
)
from .issue import EvidenceRef, Issue, Severity
from .paths import INTENT_PATHS, SYSTEM_FIELD_NAMES
from .revision import ExecutionRevision, IntentRevision

__all__ = [
    # 版本与路径白名单
    "SCHEMA_VERSION",
    "INTENT_PATHS",
    "SYSTEM_FIELD_NAMES",
    # ID 与时间
    "Id",
    "new_id",
    "utc_now",
    # Issue 与证据
    "Severity",
    "EvidenceRef",
    "Issue",
    # Intent
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
    # Delta
    "DeltaOperation",
    "IntentDelta",
    # Revision
    "IntentRevision",
    "ExecutionRevision",
]
