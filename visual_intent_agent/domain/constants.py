"""Schema 版本常量（全系统唯一定义）。

冻结约定（ARCHITECTURE.md 5.3）：所有可持久化合同都带 `schema_version` 字段，
默认值取本常量；不支持版本的输入必须显式拒绝，禁止静默丢字段。
"""

from __future__ import annotations

from typing import Literal

#: 当前 Schema 版本，全系统唯一来源；值固定为 "v1"。
SCHEMA_VERSION: Literal["v1"] = "v1"

__all__ = ["SCHEMA_VERSION"]
