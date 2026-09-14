"""共享 pytest 基础设施（架构骨架交付）。

- smoke 测试在无凭据时自动 skip；
- 默认测试运行通过 pyproject 的 addopts `-m 'not smoke'` 完全离线；
- 业务 fixture（临时 SQLite、Fake Provider 等）由各 Step 在对应子目录添加。
"""

import pytest

from visual_intent_agent.config import has_provider_credentials


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if has_provider_credentials():
        return
    skip = pytest.mark.skip(reason="smoke credentials not configured (.env / env vars missing)")
    for item in items:
        if "smoke" in item.keywords:
            item.add_marker(skip)
