"""IntentEngine 公开面与依赖边界测试（验收条件「无数据库硬依赖，便于与 Step 04 独立测试」）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from visual_intent_agent.intent_engine import (
    IntentEngine,
    Interpreter,
    IntentResolveRequest,
    InterpreterResult,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = PROJECT_ROOT / "visual_intent_agent"

EXPECTED_ROOT_EXPORTS = [
    "IntentEngine",
    "Interpreter",
    "IntentResolveRequest",
    "InterpreterResult",
]

_FORBIDDEN_SOURCE_TOKENS = (
    "sqlite3",
    "persistence",
    "os.environ",
    "getenv",
    "load_dotenv",
    "sk-",
    "requests",
    "openai",
)


def test_package_root_exports_exactly_the_frozen_surface() -> None:
    import visual_intent_agent.intent_engine as package

    assert package.__all__ == EXPECTED_ROOT_EXPORTS
    assert IntentEngine and Interpreter and IntentResolveRequest and InterpreterResult


def test_importing_the_package_has_no_database_or_http_dependency() -> None:
    code = (
        "import sys\n"
        "import visual_intent_agent.intent_engine as ie\n"
        "assert set(ie.__all__) == set(" + repr(EXPECTED_ROOT_EXPORTS) + ")\n"
        "forbidden = {\n"
        "    'sqlite3',\n"
        "    'httpx',\n"
        "    'visual_intent_agent.persistence',\n"
        "    'visual_intent_agent.config',\n"
        "    'visual_intent_agent.providers.openai_llm',\n"
        "}\n"
        "loaded = sorted(forbidden & set(sys.modules))\n"
        "assert not loaded, loaded\n"
        "print('ok')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"


def test_intent_engine_sources_do_not_touch_persistence_or_credentials() -> None:
    for path in sorted((PACKAGE_DIR / "intent_engine").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN_SOURCE_TOKENS:
            assert token not in source, f"{path.name} must not reference {token!r}"


def test_provider_sources_do_not_hardcode_credentials() -> None:
    for name in ("errors.py", "llm.py", "openai_llm.py", "fake_llm.py"):
        source = (PACKAGE_DIR / "providers" / name).read_text(encoding="utf-8")
        assert "sk-" not in source
        assert "os.environ" not in source
        assert "load_dotenv" not in source


def test_providers_package_init_is_still_empty() -> None:
    """多 Step 共有包：`providers/__init__.py` 必须保持空白（ARCHITECTURE.md 第 1 节）。"""
    assert (PACKAGE_DIR / "providers" / "__init__.py").read_text(encoding="utf-8") == ""
