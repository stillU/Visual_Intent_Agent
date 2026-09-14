"""Step 06 测试共享工具（唯一命名模块；禁止 `from conftest import ...`）。

提供确定性装配：`Settings`（伪造凭据，不读 .env）、临时 SQLite `Repository`、
`FakeLLMProvider`（脚本化 JSON，全离线）、`WorkflowService`，以及 LLM 输出构造器。
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from pydantic import SecretStr

from visual_intent_agent.config import Settings
from visual_intent_agent.intent_engine import IntentEngine, Interpreter
from visual_intent_agent.persistence import SQLiteRepository
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.workflow import QuestionBuilder, WorkflowService

#: 伪造 Provider 配置（不是真实凭据；任何测试都不得读取项目 .env）。
FAKE_BASE_URL = "https://provider.invalid/v1"
FAKE_API_KEY = "unit-test-key"
FAKE_IMAGE_MODEL = "qwen-image-3.0"


def make_settings(**overrides: Any) -> Settings:
    """伪造 `Settings`（显式传值，不触发 .env 读取）。"""
    values: dict[str, Any] = {
        "provider_base_url": FAKE_BASE_URL,
        "provider_api_key": SecretStr(FAKE_API_KEY),
        "image_model": FAKE_IMAGE_MODEL,
    }
    values.update(overrides)
    return Settings(**values)


def make_repo(tmp_path, name: str = "workflow.db") -> SQLiteRepository:
    """临时 SQLite Repository（`tmp_path`，绝不写 data/）。"""
    return SQLiteRepository(tmp_path / name)


def make_provider(
    responses: Iterable[str] | Any = (),
) -> FakeLLMProvider:
    """脚本化 FakeLLMProvider（队列或回调）。"""
    return FakeLLMProvider(responses)


def make_service(
    repo: SQLiteRepository,
    llm: FakeLLMProvider,
    *,
    settings: Settings | None = None,
    question_builder: QuestionBuilder | None = None,
) -> WorkflowService:
    """组装完整 WorkflowService（真实 Interpreter + IntentEngine + QuestionBuilder）。"""
    return WorkflowService(
        repo=repo,
        intent_engine=IntentEngine(Interpreter(llm)),
        question_builder=question_builder or QuestionBuilder(),
        settings=settings or make_settings(),
    )


# ---------------------------------------------------------------------------
# LLM 输出构造器（Interpreter 的冻结输入 Schema）
# ---------------------------------------------------------------------------


def set_entry(
    path: str,
    value: str | int,
    *,
    answers: bool = False,
    fragment: str | None = None,
) -> dict[str, Any]:
    """Candidate SET delta 的 LLM 输出条目。"""
    return {
        "operation": "SET",
        "path": path,
        "value": value,
        "answers_pending_question": answers,
        "evidence_fragment": fragment,
    }


def delegate_entry(
    path: str,
    *,
    answers: bool = True,
    fragment: str | None = None,
) -> dict[str, Any]:
    """Candidate resolution-only SET（user_delegated）。"""
    return {
        "operation": "SET",
        "path": path,
        "resolution": "user_delegated",
        "answers_pending_question": answers,
        "evidence_fragment": fragment,
    }


def response(
    *entries: dict[str, Any],
    unresolved_language: Iterable[str] = (),
    detected_conflicts: Iterable[dict[str, Any]] = (),
) -> str:
    """一条合法 Interpreter JSON 响应。"""
    return json.dumps(
        {
            "candidate_deltas": list(entries),
            "detected_conflicts": list(detected_conflicts),
            "unresolved_language": list(unresolved_language),
        }
    )


def empty_response() -> str:
    """合法但没有任何 Delta 的响应。"""
    return response()


def invalid_json_response() -> str:
    """非法 JSON（可重试解析失败）。"""
    return "not-json{"


def full_intent_response() -> str:
    """完整 Intent（禁止路径，可重试解析失败）。"""
    return json.dumps({"schema_version": "v1", "subject": {"description": "a cat"}})


# ---------------------------------------------------------------------------
# 多轮剧本
# ---------------------------------------------------------------------------

#: 空 Intent 时 DecisionPolicy 的确定性提问顺序（前 7 个问题解决后即 Ready）。
READY_ANSWERS: tuple[tuple[str, str], ...] = (
    ("subject.description", "a cat"),
    ("style.primary", "photorealistic"),
    ("environment.mode", "studio"),
    ("environment.location", "a wooden table"),
    ("composition.framing", "medium_shot"),
    ("subject.pose_action", "sitting"),
    ("lighting.character", "soft"),
)

#: 达到 Ready 所需的全部脚本化 LLM 响应（第 1 条为首次需求，其余为澄清回答）。
READY_RESPONSES: tuple[str, ...] = tuple(
    response(set_entry(path, value, answers=index > 0))
    for index, (path, value) in enumerate(READY_ANSWERS)
)


def ready_responses() -> list[str]:
    """返回一份新的 Ready 剧本响应列表（避免测试间共享可变对象）。"""
    return list(READY_RESPONSES)
