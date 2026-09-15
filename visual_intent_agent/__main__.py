"""模块入口：`python -m visual_intent_agent`（MVP v0.3 工程预览版）。

只转发到 `visual_intent_agent.cli.main`；所有交互与装配逻辑都在 `cli.py`。
默认真实模式，`--demo` 为无凭据离线演示模式。
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
