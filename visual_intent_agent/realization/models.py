"""Step 07 Realization 数据模型：`RealizationValue` 与 `RealizationState`。

冻结形状见 `docs/ARCHITECTURE.md` 第 4 节「Step 07 — prompt_engine + realization/models」：

    RealizationValue  path / value / source / first_prompt_artifact_id /
                      carry_policy / status / invalidated_reason / invalidated_at /
                      knowledge_bundle_id / knowledge_unit_id / knowledge_unit_version
    RealizationState  schema_version / realization_id / session_id /
                      based_on_intent_revision_id / values / created_at

v0.4 Step 03 兼容扩展：`RealizationValue` 新增三个**可选**知识追溯字段
（`knowledge_bundle_id` / `knowledge_unit_id` / `knowledge_unit_version`）。旧 payload
缺省为 None 仍可读；三字段必须同时为非空字符串或全为 None。知识追溯只说明
"为何选这个具体实现"，**不改变**授权 `source`（仍只能是 `user_delegated`）。

本步骤**只建立模型与读写字段**。Realization 的 carry（跨 revision 继承）与失效评估
（`evaluate_carry` / `CarryEvaluation`）属于 Step 09 的 `realization/carry.py`，本步不实现、
不预留接口；`RealizationValue` 上的 `carry_policy` / `status` / `invalidated_*` 字段由
Step 09 写入，Step 07 只读取 `status == "active"` 的值并原样复用（preserve 语义）。

历史不可覆盖：失效产生**新** `RealizationState`（新 `realization_id`），旧 state 仍保留在
`realization_states` 表中可审计；本模型不提供任何原地修改路径（`frozen=True`）。

包布局（ARCHITECTURE.md 第 1 节）：`realization/` 是**多 Step 拥有**的包，其
`__init__.py` 必须保持空白，因此本模块不可经 `visual_intent_agent.realization` 再导出，
下游一律 `from visual_intent_agent.realization.models import RealizationState`。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from visual_intent_agent.domain.constants import SCHEMA_VERSION
from visual_intent_agent.domain.identifiers import utc_now

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: `RealizationValue.source` 的唯一合法值：只允许用户显式委托产生。
REALIZATION_SOURCE_USER_DELEGATED = "user_delegated"

#: `RealizationValue.carry_policy` 的唯一合法值（Step 09 的评估口径，本步只承载）。
REALIZATION_CARRY_PRESERVE_UNTIL_INVALIDATED = "preserve_until_invalidated"

#: `RealizationValue.status` 的合法值。
REALIZATION_STATUS_ACTIVE = "active"
REALIZATION_STATUS_INVALIDATED = "invalidated"


def _ensure_utc(value: datetime) -> datetime:
    """拒绝 naive datetime；把 tz-aware 值归一到 UTC（保持同一时刻）。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("invalidated_at must be timezone-aware UTC; naive datetimes are rejected")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    """JSON 序列化形态：ISO 8601 带 `+00:00`（ARCHITECTURE.md 5.2）。"""
    return value.astimezone(timezone.utc).isoformat()


def _ensure_utc_optional(value: datetime | None) -> datetime | None:
    """可选时间戳：None 原样保留，非 None 时与 `_ensure_utc` 同规则。"""
    if value is None:
        return None
    return _ensure_utc(value)


def _iso_utc_optional(value: datetime | None) -> str | None:
    """可选时间戳的 JSON 形态；None 序列化为 null。"""
    if value is None:
        return None
    return _iso_utc(value)


#: tz-aware UTC datetime：校验 + JSON 序列化形态在此一次冻结。
_UtcDatetime = Annotated[
    datetime,
    AfterValidator(_ensure_utc),
    PlainSerializer(_iso_utc, return_type=str, when_used="json"),
]

#: 可空 tz-aware UTC datetime（`invalidated_at` 未失效时为 None）。
_OptionalUtcDatetime = Annotated[
    datetime | None,
    AfterValidator(_ensure_utc_optional),
    PlainSerializer(_iso_utc_optional, return_type=str | None, when_used="json"),
]


class RealizationValue(BaseModel):
    """一个已实现的委托决策（一条路径一个值）。

    原有字段面 8 个（ARCHITECTURE.md 4「Step 07」）：

    - `path`：被委托的 Intent 路径（`domain.INTENT_PATHS` 之一）；
    - `value`：系统在**该路径局部范围内**确定的具体实现（不是新的 Intent 值）；
    - `source`：唯一合法值 `user_delegated`（只有用户显式委托才允许系统决定）；
    - `first_prompt_artifact_id`：首次产生该值的 PromptArtifact（稳定复用：再次编译
      **不得**改写它）；
    - `carry_policy`：默认 `preserve_until_invalidated`（Step 09 的评估口径）；
    - `status`：`active` / `invalidated`；只有 `active` 值会被 Step 07 复用；
    - `invalidated_reason` / `invalidated_at`：仅当 Step 09 置失效时填写。

    v0.4 Step 03 追加 3 个**可选**知识追溯字段（兼容扩展，默认 None）：

    - `knowledge_bundle_id` / `knowledge_unit_id` / `knowledge_unit_version`：记录
      该具体实现由哪条已审核知识单元建议（经由哪个 `KnowledgeBundle`）。三字段
      **必须同时为非空字符串或全为 None**，禁止半填写；旧 payload 缺省为 None。
      这些字段只说明"为何选这个具体实现"，**不**构成用户授权，也不改变 `source`。
    """

    model_config = _FROZEN

    path: str
    value: str
    source: Literal["user_delegated"]
    first_prompt_artifact_id: str
    carry_policy: Literal["preserve_until_invalidated"] = (
        REALIZATION_CARRY_PRESERVE_UNTIL_INVALIDATED
    )
    status: Literal["active", "invalidated"] = REALIZATION_STATUS_ACTIVE
    invalidated_reason: str | None = None
    invalidated_at: _OptionalUtcDatetime = None
    knowledge_bundle_id: str | None = None
    knowledge_unit_id: str | None = None
    knowledge_unit_version: str | None = None

    @field_validator(
        "knowledge_bundle_id", "knowledge_unit_id", "knowledge_unit_version"
    )
    @classmethod
    def _knowledge_ref_shape(cls, value: str | None) -> str | None:
        """单个知识追溯字段：None 或缺省保留；否则必须是非空字符串。"""
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "knowledge provenance fields must be non-empty strings when present"
            )
        return value

    @model_validator(mode="after")
    def _knowledge_refs_triplet(self) -> "RealizationValue":
        """知识三字段必须同时有效或全空（禁止半填写造成断链）。"""
        present = (
            self.knowledge_bundle_id is not None,
            self.knowledge_unit_id is not None,
            self.knowledge_unit_version is not None,
        )
        if len(set(present)) != 1:
            raise ValueError(
                "knowledge_bundle_id, knowledge_unit_id and knowledge_unit_version must "
                "be provided together or all be None"
            )
        return self


class RealizationState(BaseModel):
    """会话级 Realization 快照（append-only；失效产生新 state，历史不覆盖）。

    `based_on_intent_revision_id` 必须指向同会话已存在的 `IntentRevision`
    （Repository 的 `realization_states` 外键冻结键 `based_on_intent_revision_id`）。
    """

    model_config = _FROZEN

    schema_version: Literal["v1"] = SCHEMA_VERSION
    realization_id: str
    session_id: str
    based_on_intent_revision_id: str
    values: list[RealizationValue] = Field(default_factory=list)
    created_at: _UtcDatetime = Field(default_factory=utc_now)

    def active_values(self) -> list[RealizationValue]:
        """当前仍可复用的值（`status == "active"`，按声明顺序）。"""
        return [value for value in self.values if value.status == REALIZATION_STATUS_ACTIVE]

    def active_value_for(self, path: str) -> RealizationValue | None:
        """按路径取 active 值；同一路径出现多个 active 值时取**第一个**（确定性）。"""
        for value in self.values:
            if value.path == path and value.status == REALIZATION_STATUS_ACTIVE:
                return value
        return None


__all__ = [
    "RealizationValue",
    "RealizationState",
    "REALIZATION_SOURCE_USER_DELEGATED",
    "REALIZATION_CARRY_PRESERVE_UNTIL_INVALIDATED",
    "REALIZATION_STATUS_ACTIVE",
    "REALIZATION_STATUS_INVALIDATED",
]
