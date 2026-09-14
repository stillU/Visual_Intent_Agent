"""P1 端到端测试共享工具（唯一命名模块；禁止 `from conftest import ...`）。

与 `tests/workflow/workflow_helpers.py` 内容等价的**目录内**副本：pytest 只把每个
测试目录自身加入 `sys.path`，因此 e2e 目录不跨目录 import，避免 Rev.1 记录的
`conftest` 同名缓存问题。全离线：伪造 `Settings` + `FakeLLMProvider` + `tmp_path` SQLite。
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

FAKE_BASE_URL = "https://provider.invalid/v1"
FAKE_API_KEY = "unit-test-key"
FAKE_IMAGE_MODEL = "qwen-image-3.0"

#: 用户消息文本（脚本化 LLM 不解析文本，但保持剧本可读）。
USER_TURNS: tuple[str, ...] = (
    "a photo of a cat",
    "photorealistic style",
    "in a studio",
    "on a wooden table",
    "medium shot",
    "sitting",
    "soft lighting",
)

#: 空 Intent 时 DecisionPolicy 的确定性提问顺序；全部解决后即 Ready。
READY_ANSWERS: tuple[tuple[str, str], ...] = (
    ("subject.description", "a cat"),
    ("style.primary", "photorealistic"),
    ("environment.mode", "studio"),
    ("environment.location", "a wooden table"),
    ("composition.framing", "medium_shot"),
    ("subject.pose_action", "sitting"),
    ("lighting.character", "soft"),
)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "provider_base_url": FAKE_BASE_URL,
        "provider_api_key": SecretStr(FAKE_API_KEY),
        "image_model": FAKE_IMAGE_MODEL,
    }
    values.update(overrides)
    return Settings(**values)


def make_repo(tmp_path, name: str = "e2e.db") -> SQLiteRepository:
    return SQLiteRepository(tmp_path / name)


def make_provider(responses: Iterable[str] | Any = ()) -> FakeLLMProvider:
    return FakeLLMProvider(responses)


def make_service(
    repo: SQLiteRepository,
    llm: FakeLLMProvider,
    *,
    settings: Settings | None = None,
    question_builder: QuestionBuilder | None = None,
) -> WorkflowService:
    return WorkflowService(
        repo=repo,
        intent_engine=IntentEngine(Interpreter(llm)),
        question_builder=question_builder or QuestionBuilder(),
        settings=settings or make_settings(),
    )


def set_entry(
    path: str,
    value: str | int,
    *,
    answers: bool = False,
    fragment: str | None = None,
) -> dict[str, Any]:
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
    return json.dumps(
        {
            "candidate_deltas": list(entries),
            "detected_conflicts": list(detected_conflicts),
            "unresolved_language": list(unresolved_language),
        }
    )


def empty_response() -> str:
    return response()


def ready_responses() -> list[str]:
    """需求先行的剧本（第 1 条是需求，其余是回答）。"""
    return [
        response(set_entry(path, value, answers=index > 0))
        for index, (path, value) in enumerate(READY_ANSWERS)
    ]


def clarification_responses() -> list[str]:
    """模糊需求先行的剧本（每一条都是对当前问题的回答）。"""
    return [response(set_entry(path, value, answers=True)) for path, value in READY_ANSWERS]
