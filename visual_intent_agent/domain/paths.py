"""路径白名单（全系统唯一定义，ARCHITECTURE.md 5.4）。

后续所有步骤（Validator、Reducer、DecisionPolicy、PromptEngine、
FeedbackEngine、测试）只允许 import 本模块，不得维护第二份清单。
"""

from __future__ import annotations

#: Intent 路径白名单：恰好 12 条，不多不少（与七类 Facet 的字段一一对应）。
INTENT_PATHS: frozenset[str] = frozenset(
    {
        "subject.description",
        "subject.count",
        "subject.pose_action",
        "composition.framing",
        "environment.mode",
        "environment.location",
        "style.primary",
        "style.description",
        "lighting.character",
        "camera.angle",
        "camera.depth_of_field",
        "color.palette",
    }
)

#: Validator 系统字段保护用的字段名集合（防御纵深；Delta 本就只能命中白名单）。
SYSTEM_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "schema_version",
        "intent_id",
        "session_id",
        "created_at",
        "intent_revision_id",
        "execution_revision_id",
        "parent_revision_id",
        "workflow_state",
        "confirmation_id",
        "summary_hash",
    }
)

__all__ = ["INTENT_PATHS", "SYSTEM_FIELD_NAMES"]
