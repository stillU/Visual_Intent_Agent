"""ID 与时间戳的最小工具（ARCHITECTURE.md 5.1 / 5.2）。

- ID 类型统一为 `Id = str`；前缀由调用方给出（冻结前缀表见 ARCHITECTURE.md 5.1）。
- 时间戳统一为 tz-aware UTC；合同模型的 `created_at` 一律用 `utc_now()` 产生。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

#: 全系统 ID 类型别名（`new_id` 生成，形态 "<prefix>_<uuid4hex>"）。
Id = str


def new_id(prefix: str) -> str:
    """生成 `f"{prefix}_{uuid4().hex}"` 形态的新 ID。

    只能在确定性代码中调用；LLM 输出中的 ID 一律忽略并由系统重填。
    """
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> datetime:
    """当前时间，tz-aware UTC（`datetime.now(timezone.utc)`）。"""
    return datetime.now(timezone.utc)


__all__ = ["Id", "new_id", "utc_now"]
