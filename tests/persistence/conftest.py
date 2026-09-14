"""Step 04 persistence 集成测试 fixtures（临时 SQLite）。

工厂与场景装配工具在 `persistence_helpers.py`：放在唯一命名的模块中，避免
pytest 以多个目录参数运行时 `conftest` basename 的 sys.modules 缓存冲突
（上游 `tests/validation` 与 `tests/policy` 都使用 `from conftest import ...`，
该冲突与本步骤无关，详见 step_04_handoff「已知限制」）。
"""

from __future__ import annotations

import pytest

from visual_intent_agent.persistence import SQLiteRepository


@pytest.fixture
def db_path(tmp_path):
    """临时 SQLite 文件路径（每个测试独立）。"""
    return tmp_path / "visual_intent_step04.db"


@pytest.fixture
def repo(db_path):
    """已迁移的临时 SQLite Repository。"""
    instance = SQLiteRepository(db_path)
    try:
        yield instance
    finally:
        instance.close()
