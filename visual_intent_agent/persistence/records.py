"""Step 04 存储记录模型（ARCHITECTURE.md 4「Step 04」）。

三个公开记录形状：

- `ConfirmationRecord`：Confirmation 的严格 revision 绑定快照（append-only）；
- `SessionSnapshot`：会话当前状态的完整读取视图（revision 指针 / 确认 / 待答问题 / 消息）；
- `StoredArtifact`：Artifact 存储"信封"——`refs` 外键 + `payload` JSON 字符串，
  使 Step 04 无需预知 Step 07/08/09 的业务模型。

全部 `frozen=True, extra="forbid"`；`created_at` / `confirmed_at` 一律 tz-aware UTC
（naive 输入显式拒绝，非 UTC 的 tz-aware 输入归一到 UTC），JSON 序列化固定
`+00:00`（与 domain.revision 的约定一致，ARCHITECTURE.md 5.2）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer

from visual_intent_agent.domain.constants import SCHEMA_VERSION
from visual_intent_agent.domain.identifiers import utc_now

from .state_machine import WorkflowState

_FROZEN = ConfigDict(frozen=True, extra="forbid")


def _ensure_utc(value: datetime) -> datetime:
    """拒绝 naive datetime；把 tz-aware 值归一到 UTC（保持同一时刻）。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware UTC; naive datetimes are rejected")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    """JSON 序列化形态：ISO 8601 带 `+00:00`。"""
    return value.astimezone(timezone.utc).isoformat()


#: tz-aware UTC datetime：校验 + JSON 序列化形态。
_UtcDatetime = Annotated[
    datetime,
    AfterValidator(_ensure_utc),
    PlainSerializer(_iso_utc, return_type=str, when_used="json"),
]


class ConfirmationRecord(BaseModel):
    """一次 Confirmation：绑定 intent_revision + execution_revision + summary_hash。

    记录本身不可修改（append-only）。当前 revision 改变后旧记录保留，但
    `Repository.is_confirmation_valid` 会返回 False。
    """

    model_config = _FROZEN

    schema_version: Literal["v1"] = SCHEMA_VERSION
    confirmation_id: str
    session_id: str
    intent_revision_id: str
    execution_revision_id: str
    summary_hash: str
    confirmed_at: _UtcDatetime = Field(default_factory=utc_now)


class SessionSnapshot(BaseModel):
    """会话当前状态视图（唯一读取入口，Step 06 的 `get_session` 直接返回它）。"""

    model_config = _FROZEN

    session_id: str
    workflow_state: WorkflowState
    current_intent_revision_id: str | None = None
    current_execution_revision_id: str | None = None
    latest_confirmation_id: str | None = None
    pending_question_id: str | None = None
    pending_question_payload: str | None = None
    message_ids: tuple[str, ...] = ()


class StoredArtifact(BaseModel):
    """Artifact 存储信封：`refs` 是外键引用，`payload` 是各 Step 的业务 JSON。"""

    model_config = _FROZEN

    artifact_id: str
    session_id: str
    refs: dict[str, str]
    payload: str
    created_at: _UtcDatetime = Field(default_factory=utc_now)


__all__ = ["ConfirmationRecord", "SessionSnapshot", "StoredArtifact"]
