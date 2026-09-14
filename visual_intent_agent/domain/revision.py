"""Revision 合同：IntentRevision 与 ExecutionRevision。

冻结（ARCHITECTURE.md 4 / 5.2）：

- revision 只增不改（历史不可覆盖）；
- `created_at` 一律 tz-aware UTC：naive 输入被显式拒绝，非 UTC 的 tz-aware 输入
  被归一到 UTC（保持同一时刻），JSON 序列化固定为带 `+00:00` 的 ISO 8601；
- `ExecutionRevision` 只表达"当前目标模型 + 输出比例"的最小形态，
  本步骤不扩展完整模型参数系统。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer

from .constants import SCHEMA_VERSION
from .delta import IntentDelta
from .identifiers import utc_now
from .intent import VisualIntent

_FROZEN = ConfigDict(frozen=True, extra="forbid")


def _ensure_utc(value: datetime) -> datetime:
    """拒绝 naive datetime；把 tz-aware 值归一到 UTC（同一时刻）。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware UTC; naive datetimes are rejected")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    """JSON 序列化形态：ISO 8601 带 +00:00（ARCHITECTURE.md 5.2）。"""
    return value.astimezone(timezone.utc).isoformat()


#: tz-aware UTC datetime：校验 + JSON 序列化形态在此一次冻结。
_UtcDatetime = Annotated[
    datetime,
    AfterValidator(_ensure_utc),
    PlainSerializer(_iso_utc, return_type=str, when_used="json"),
]


class IntentRevision(BaseModel):
    """Intent 的一次完整快照 + 本轮 applied delta（frozen）。"""

    model_config = _FROZEN

    schema_version: Literal["v1"] = SCHEMA_VERSION
    intent_revision_id: str
    session_id: str
    parent_revision_id: str | None
    intent: VisualIntent
    applied_deltas: list[IntentDelta] = []
    created_at: _UtcDatetime = Field(default_factory=utc_now)


class ExecutionRevision(BaseModel):
    """执行侧最小 revision：目标模型 + 输出尺寸（比例由尺寸派生）。"""

    model_config = _FROZEN

    schema_version: Literal["v1"] = SCHEMA_VERSION
    execution_revision_id: str
    session_id: str
    parent_revision_id: str | None
    target_model: str
    output_size: str = "1024x1024"
    created_at: _UtcDatetime = Field(default_factory=utc_now)


__all__ = ["IntentRevision", "ExecutionRevision"]
