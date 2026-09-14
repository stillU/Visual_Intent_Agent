"""IntentDelta：LLM 提议与确定性状态之间唯一的状态载体（README 不变量 2）。

冻结形状（ARCHITECTURE.md 4）：

- `SET` 必须至少携带 `value` 或 `resolution` 之一
  （resolution-only SET = 授权变更，例如 user_delegated）；
- `CLEAR` / `PIN` / `UNPIN` 必须两者都不携带。

本步骤**不**校验 `path` 是否命中 `INTENT_PATHS`：IntentDelta 是 LLM 输出的解析
目标，白名单与证据校验属 Step 02 Validator。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from .intent import Resolution
from .issue import EvidenceRef

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class DeltaOperation(str, Enum):
    """合法操作仅四个。"""

    SET = "SET"
    CLEAR = "CLEAR"
    PIN = "PIN"
    UNPIN = "UNPIN"


class IntentDelta(BaseModel):
    """候选 Delta（frozen + extra="forbid"）；LLM 只能提出，不能直接修改 Intent。"""

    model_config = _FROZEN

    operation: DeltaOperation
    path: str
    value: str | int | None = None
    resolution: Resolution | None = None
    evidence_refs: list[EvidenceRef] = []

    @model_validator(mode="after")
    def _shape_must_match_operation(self) -> "IntentDelta":
        carries_payload = self.value is not None or self.resolution is not None
        if self.operation is DeltaOperation.SET:
            if not carries_payload:
                raise ValueError(
                    "SET delta must carry at least one of value or resolution"
                )
        elif carries_payload:
            raise ValueError(
                f"{self.operation.value} delta must not carry value or resolution"
            )
        return self


__all__ = ["DeltaOperation", "IntentDelta"]
