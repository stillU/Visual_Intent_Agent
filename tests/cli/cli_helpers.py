"""CLI 端到端测试共享工具（唯一命名模块；禁止 `from conftest import ...`、禁止跨目录 import）。

自包含、全离线：

- `ScriptedConsole`：把脚本化输入喂给 `run_repl` 并记录全部输出；
- `ScriptedLLM`：按 system prompt 分派"反馈 / 解释器"两条独立脚本队列；队列元素可以是
  响应文本，也可以是 `ProviderError` 实例（用于构造可恢复失败 / timeout 场景）；
- `make_cli_app`：用真实 `WorkflowService` / `ReviewService` / `GenerationPipeline` +
  `FakeImageProvider` 装配 `SessionApp`，默认不接触任何真实 Provider 或凭据。
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Iterable, Sequence

from pydantic import SecretStr

from visual_intent_agent.cli import SessionApp
from visual_intent_agent.config import Settings
from visual_intent_agent.feedback import FeedbackEngine
from visual_intent_agent.generation import GenerationPipeline
from visual_intent_agent.intent_engine import IntentEngine, Interpreter
from visual_intent_agent.persistence import SQLiteRepository, WorkflowState
from visual_intent_agent.prompt_engine import (
    QWEN_IMAGE_MODEL,
    PromptArtifact,
    PromptEngine,
    QwenImageRenderer,
)
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_image import FakeImageProvider
from visual_intent_agent.providers.llm import LLMRequest, LLMResponse
from visual_intent_agent.workflow import QuestionBuilder, WorkflowService
from visual_intent_agent.workflow.review import ReviewService

#: 伪造 Provider 配置（不是真实凭据；测试绝不读取项目 .env）。
FAKE_BASE_URL = "https://provider.invalid/v1"
FAKE_API_KEY = "unit-test-key"
FAKE_MODEL = QWEN_IMAGE_MODEL
FAKE_LLM_MODEL = "fake-llm"


class ScriptedConsole:
    """脚本化控制台：按顺序回答 `prompt`，记录 `print` 内容。"""

    def __init__(
        self,
        answers: Sequence[str],
        hook: Callable[[str], None] | None = None,
    ) -> None:
        self._answers = list(answers)
        self._hook = hook
        self.output: list[str] = []

    def print(self, text: str = "") -> None:
        self.output.append(str(text))

    def prompt(self, text: str) -> str:
        if self._hook is not None:
            self._hook(text)
        self.output.append(str(text))
        if not self._answers:
            raise AssertionError("ScriptedConsole ran out of scripted answers")
        answer = self._answers.pop(0)
        self.output.append(f"> {answer}")
        return answer

    @property
    def text(self) -> str:
        return "\n".join(self.output)


class ScriptedLLM:
    """按 system prompt 分派的两队列脚本 Provider（条目可为文本或异常实例）。"""

    def __init__(
        self,
        interpreter_responses: Iterable[Any] = (),
        feedback_responses: Iterable[Any] = (),
    ) -> None:
        self.interpreter_responses: deque[Any] = deque(interpreter_responses)
        self.feedback_responses: deque[Any] = deque(feedback_responses)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not isinstance(request, LLMRequest):
            raise ProviderError.invalid_request("ScriptedLLM.complete expects an LLMRequest")
        self.requests.append(request)
        system = request.messages[0].content if request.messages else ""
        queue = (
            self.feedback_responses
            if "Feedback Interpreter" in system
            else self.interpreter_responses
        )
        if not queue:
            raise ProviderError.invalid_request(
                "script is exhausted for this LLM role (no guessing is attempted)"
            )
        entry = queue.popleft()
        if isinstance(entry, BaseException):
            raise entry
        return LLMResponse(content=str(entry), model=FAKE_LLM_MODEL)


class FlakyImageProvider:
    """在指定调用序号（1-based）抛 `provider.timeout`，其余委托 `FakeImageProvider`。"""

    def __init__(self, fail_on_calls: Iterable[int] = (1,)) -> None:
        self._fail_on = set(fail_on_calls)
        self._calls = 0
        self.requests: list[Any] = []
        self._delegate = FakeImageProvider()

    def generate(self, request: Any) -> Any:
        self._calls += 1
        self.requests.append(request)
        if self._calls in self._fail_on:
            raise ProviderError.timeout("simulated image provider timeout")
        return self._delegate.generate(request)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "provider_base_url": FAKE_BASE_URL,
        "provider_api_key": SecretStr(FAKE_API_KEY),
        "image_model": FAKE_MODEL,
    }
    values.update(overrides)
    return Settings(**values)


def make_cli_app(
    tmp_path: Path,
    *,
    interpreter_responses: Iterable[Any] = (),
    feedback_responses: Iterable[Any] = (),
    image_provider: Any = None,
    settings: Settings | None = None,
    name: str = "cli.db",
) -> tuple[SessionApp, SQLiteRepository, ScriptedLLM, Path]:
    """装配真实服务 + Fake Provider 的 `SessionApp`（全离线）。"""
    repo = SQLiteRepository(tmp_path / name)
    llm = ScriptedLLM(interpreter_responses, feedback_responses)
    settings = settings if settings is not None else make_settings()
    output_dir = tmp_path / "outputs"
    workflow = WorkflowService(
        repo=repo,
        intent_engine=IntentEngine(Interpreter(llm)),
        question_builder=QuestionBuilder(),
        settings=settings,
    )
    review = ReviewService(
        repo=repo,
        feedback_engine=FeedbackEngine(llm),
        question_builder=QuestionBuilder(),
    )
    pipeline = GenerationPipeline(
        repo=repo,
        prompt_engine=PromptEngine(QwenImageRenderer(), repo),
        image_provider=image_provider if image_provider is not None else FakeImageProvider(),
        output_dir=output_dir,
    )
    app = SessionApp(
        repo=repo,
        workflow=workflow,
        review=review,
        pipeline=pipeline,
        settings=settings,
    )
    return app, repo, llm, output_dir


# ---------------------------------------------------------------------------
# 脚本构造（与 tests/generation、tests/feedback 的同名字段保持逐字一致）
# ---------------------------------------------------------------------------


def set_entry(
    path: str, value: str | int, *, answers: bool = False, fragment: str | None = None
) -> dict[str, Any]:
    return {
        "operation": "SET",
        "path": path,
        "value": value,
        "answers_pending_question": answers,
        "evidence_fragment": fragment,
    }


def delegate_entry(path: str, *, answers: bool = True) -> dict[str, Any]:
    return {
        "operation": "SET",
        "path": path,
        "resolution": "user_delegated",
        "answers_pending_question": answers,
        "evidence_fragment": None,
    }


def response(*entries: dict[str, Any]) -> str:
    return json.dumps(
        {
            "candidate_deltas": list(entries),
            "detected_conflicts": [],
            "unresolved_language": [],
        }
    )


#: P1 达到 Ready 的确定性 7 轮剧本（与 DecisionPolicy 的提问顺序一致）。
READY_RESPONSES: tuple[str, ...] = (
    response(set_entry("subject.description", "a cat")),
    response(set_entry("style.primary", "photorealistic", answers=True)),
    response(set_entry("environment.mode", "studio", answers=True)),
    response(set_entry("environment.location", "a wooden table", answers=True)),
    response(set_entry("composition.framing", "medium_shot", answers=True)),
    response(set_entry("subject.pose_action", "sitting", answers=True)),
    response(delegate_entry("lighting.character", answers=True)),
)

#: 七条用户回答（与 READY_RESPONSES 一一对应）。
READY_USER_TURNS: tuple[str, ...] = (
    "a photo of a cat",
    "photorealistic",
    "studio",
    "a wooden table",
    "medium shot",
    "sitting",
    "you decide",
)


def feedback_response(
    decision: str,
    *,
    candidate_deltas: Iterable[dict[str, Any]] = (),
    preserve_paths: Iterable[str] = (),
    clarify_path: str | None = None,
    clarify_reason: str | None = None,
) -> str:
    payload: dict[str, Any] = {"decision": decision}
    if candidate_deltas:
        payload["candidate_deltas"] = list(candidate_deltas)
    if preserve_paths:
        payload["preserve_paths"] = list(preserve_paths)
    if clarify_path is not None:
        payload["clarify_path"] = clarify_path
    if clarify_reason is not None:
        payload["clarify_reason"] = clarify_reason
    return json.dumps(payload)


def revise_entry(
    path: str, value: str, *, evidence_fragment: str | None = None
) -> dict[str, Any]:
    return {
        "operation": "SET",
        "path": path,
        "value": value,
        "evidence_fragment": evidence_fragment,
    }


# ---------------------------------------------------------------------------
# 断言工具
# ---------------------------------------------------------------------------


def current_state(repo: SQLiteRepository, session_id: str) -> WorkflowState:
    return repo.get_current_session_snapshot(session_id).workflow_state


def artifact_count(repo: SQLiteRepository, table: str) -> int:
    return int(repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def prompt_artifacts(repo: SQLiteRepository) -> list[PromptArtifact]:
    """按落库顺序返回全部 `PromptArtifact`（只读；集成断言用）。"""
    rows = repo.connection.execute(
        "SELECT prompt_artifact_id FROM prompt_artifacts ORDER BY rowid"
    ).fetchall()
    return [
        PromptArtifact.model_validate_json(
            repo.get_prompt_artifact(str(row[0])).payload
        )
        for row in rows
    ]


def current_intent_value(repo: SQLiteRepository, session_id: str, path: str) -> Any:
    snapshot = repo.get_current_session_snapshot(session_id)
    assert snapshot.current_intent_revision_id is not None
    intent = repo.get_intent_revision(snapshot.current_intent_revision_id).intent
    facet_name, _, field_name = path.partition(".")
    facet = getattr(intent, facet_name)
    return getattr(facet, field_name)


def single_session_id(repo: SQLiteRepository) -> str:
    row = repo.connection.execute("SELECT session_id FROM sessions").fetchone()
    assert row is not None
    return str(row[0])


__all__ = [
    "FAKE_BASE_URL",
    "FAKE_API_KEY",
    "FAKE_MODEL",
    "FAKE_LLM_MODEL",
    "ScriptedConsole",
    "ScriptedLLM",
    "FlakyImageProvider",
    "make_settings",
    "make_cli_app",
    "set_entry",
    "delegate_entry",
    "response",
    "READY_RESPONSES",
    "READY_USER_TURNS",
    "feedback_response",
    "revise_entry",
    "current_state",
    "artifact_count",
    "prompt_artifacts",
    "current_intent_value",
    "single_session_id",
]
