"""Step 01 domain 单测共享工具：加载 `tests/fixtures/schema_v1/` 下的 JSON fixture。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "schema_v1"


@pytest.fixture
def schema_fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture
def load_schema_fixture() -> Callable[[str], Any]:
    """返回 loader：`load_schema_fixture("empty_intent.json") -> dict`。"""

    def _load(name: str) -> Any:
        return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))

    return _load
