"""Issue 与证据引用（供 Validator / DecisionPolicy / Workflow 统一使用）。

证据（`EvidenceRef`）只记录来源，本步骤不判断其语义是否正确；语义校验属 Step 02。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class Severity(str, Enum):
    """Issue 严重级别。默认 ERROR。"""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class EvidenceRef(BaseModel):
    """证据引用：一条已存消息（可含原文片段与所回答的 Pending Question）。

    `message_id` 必填——每条用户输入都是已存消息；本步骤不校验该消息是否存在
    （存在性校验属 Step 02 Validator）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str
    fragment: str | None = None
    pending_question_id: str | None = None


class Issue(BaseModel):
    """结构化问题：code 命名空间见 ARCHITECTURE.md 5.5（`<area>.<name>`）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    path: str | None = None
    severity: Severity = Severity.ERROR


__all__ = ["Severity", "EvidenceRef", "Issue"]
