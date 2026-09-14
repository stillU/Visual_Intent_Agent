"""Step 08 测试共享工具（唯一命名模块；禁止 `from conftest import ...`）。

自包含、全离线：伪造 `Settings`（不读 .env）、`tmp_path` SQLite、`FakeLLMProvider`
（P1 剧本）、`FakeImageProvider`（确定性 PNG 常量），以及两条装配路径：

1. `run_p1_to_confirmation`：真实 P1 工作流（多轮澄清 → Ready → 确认）；
2. `seed_confirmed_session`：直接把确定性构造的 Intent/Execution revision 与
   Confirmation 写入临时 Repository（便于精确覆盖单个场景）。

另含 `fail_once_on_review_transition`：模拟"GenerationArtifact 已保存但没能进入
WAITING_REVIEW"，这是**唯一**能通过系统路径得到 "FAILED + 原 GenerationArtifact" 的
状态（Provider 失败不写 Artifact），retry 用例据此构造真实失败现场。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pydantic import SecretStr

from visual_intent_agent.config import PROJECT_ROOT, Settings
from visual_intent_agent.domain import (
    CameraFacet,
    ColorFacet,
    CompositionFacet,
    EnvironmentFacet,
    ExecutionRevision,
    IntentRevision,
    LightingFacet,
    Resolution,
    ResolutionRecord,
    StyleFacet,
    SubjectFacet,
    VisualIntent,
    new_id,
    utc_now,
)
from visual_intent_agent.generation import GenerationPipeline
from visual_intent_agent.intent_engine import IntentEngine, Interpreter
from visual_intent_agent.persistence import (
    ConfirmationRecord,
    InvalidStateTransitionError,
    SQLiteRepository,
    WorkflowState,
)
from visual_intent_agent.prompt_engine import PromptEngine, QwenImageRenderer
from visual_intent_agent.providers.fake_image import FakeImageProvider
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.workflow import (
    QuestionBuilder,
    WorkflowService,
    build_confirmation_summary,
    compute_summary_hash,
)

#: 伪造 Provider 配置（不是真实凭据；任何测试都不得读取项目 .env）。
FAKE_BASE_URL = "https://provider.invalid/v1"
FAKE_API_KEY = "unit-test-key"
FAKE_IMAGE_MODEL = "qwen-image-3.0"
FAKE_OUTPUT_SIZE = "1024x1024"

#: 真实的 12 条白名单路径 → Facet 模型（仅测试装配用；产品代码只 import 白名单）。
_FACET_MODELS: dict[str, type] = {
    "subject": SubjectFacet,
    "composition": CompositionFacet,
    "environment": EnvironmentFacet,
    "style": StyleFacet,
    "lighting": LightingFacet,
    "camera": CameraFacet,
    "color": ColorFacet,
}

#: P1 剧本的用户消息（脚本化 LLM 不解析文本，仅保持剧本可读）。
USER_TURNS: tuple[str, ...] = (
    "a photo of a cat",
    "photorealistic style",
    "in a studio",
    "on a wooden table",
    "medium shot",
    "sitting",
    "you decide the lighting",
)

#: 一个"可编译"的已确认视觉意图：六个有值路径 + 一个显式委托路径。
READY_INTENT_VALUES: dict[str, Any] = {
    "subject.description": "a cat",
    "style.primary": "photorealistic",
    "environment.mode": "studio",
    "environment.location": "a wooden table",
    "composition.framing": "medium_shot",
    "subject.pose_action": "sitting",
}


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "provider_base_url": FAKE_BASE_URL,
        "provider_api_key": SecretStr(FAKE_API_KEY),
        "image_model": FAKE_IMAGE_MODEL,
    }
    values.update(overrides)
    return Settings(**values)


def make_repo(tmp_path, name: str = "generation.db") -> SQLiteRepository:
    return SQLiteRepository(tmp_path / name)


def make_engine(repo: SQLiteRepository) -> PromptEngine:
    return PromptEngine(QwenImageRenderer(), repo)


def make_fake_image(**kwargs: Any) -> FakeImageProvider:
    return FakeImageProvider(**kwargs)


def make_pipeline(
    repo: SQLiteRepository,
    *,
    output_dir: Path,
    provider: Any = None,
    engine: PromptEngine | None = None,
) -> GenerationPipeline:
    """默认全离线：FakeImageProvider + `tmp_path` 输出目录。"""
    return GenerationPipeline(
        repo=repo,
        prompt_engine=engine if engine is not None else make_engine(repo),
        image_provider=provider if provider is not None else FakeImageProvider(),
        output_dir=output_dir,
    )


# ---------------------------------------------------------------------------
# Intent 构造
# ---------------------------------------------------------------------------


def intent_with(
    values: dict[str, Any] | None = None,
    *,
    delegated: Iterable[str] = (),
    not_applicable: Iterable[str] = (),
    pinned: Iterable[str] = (),
) -> VisualIntent:
    buckets: dict[str, dict[str, Any]] = {name: {} for name in _FACET_MODELS}
    for path, value in (values or {}).items():
        facet_name, _, field_name = path.partition(".")
        if facet_name not in buckets:
            raise ValueError(f"unknown facet in path {path!r}")
        buckets[facet_name][field_name] = value
    resolutions: dict[str, ResolutionRecord] = {}
    for path in delegated:
        resolutions[path] = ResolutionRecord(resolution=Resolution.USER_DELEGATED)
    for path in not_applicable:
        resolutions[path] = ResolutionRecord(resolution=Resolution.NOT_APPLICABLE)
    facets = {name: model(**buckets[name]) for name, model in _FACET_MODELS.items()}
    return VisualIntent(
        **facets,
        resolutions=resolutions,
        pinned_paths=frozenset(pinned),
    )


def ready_intent() -> VisualIntent:
    """六个有值路径 + `lighting.character` 显式委托（可编译、含 Realization）。"""
    return intent_with(READY_INTENT_VALUES, delegated={"lighting.character"})


# ---------------------------------------------------------------------------
# 直接落库的"已确认会话"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeededSession:
    session_id: str
    intent_revision_id: str
    execution_revision_id: str
    confirmation_id: str | None


def save_confirmation_for_current(
    repo: SQLiteRepository, session_id: str
) -> ConfirmationRecord:
    snapshot = repo.get_current_session_snapshot(session_id)
    assert snapshot.current_intent_revision_id is not None
    assert snapshot.current_execution_revision_id is not None
    intent_revision = repo.get_intent_revision(snapshot.current_intent_revision_id)
    execution_revision = repo.get_execution_revision(snapshot.current_execution_revision_id)
    summary = build_confirmation_summary(intent_revision, execution_revision)
    record = ConfirmationRecord(
        confirmation_id=new_id("cnf"),
        session_id=session_id,
        intent_revision_id=intent_revision.intent_revision_id,
        execution_revision_id=execution_revision.execution_revision_id,
        summary_hash=compute_summary_hash(summary),
        confirmed_at=utc_now(),
    )
    repo.save_confirmation(record)
    return record


def seed_confirmed_session(
    repo: SQLiteRepository,
    intent: VisualIntent,
    *,
    session_id: str | None = None,
    output_size: str = FAKE_OUTPUT_SIZE,
    target_model: str = FAKE_IMAGE_MODEL,
    confirmed: bool = True,
) -> SeededSession:
    session_id = session_id or new_id("ses")
    repo.create_session(session_id)
    execution = ExecutionRevision(
        execution_revision_id=new_id("erev"),
        session_id=session_id,
        parent_revision_id=None,
        target_model=target_model,
        output_size=output_size,
    )
    repo.append_execution_revision(execution)
    intent_revision = IntentRevision(
        intent_revision_id=new_id("irev"),
        session_id=session_id,
        parent_revision_id=None,
        intent=intent,
    )
    repo.append_intent_revision(intent_revision)
    repo.transition_state(session_id, WorkflowState.WAITING_CONFIRMATION)
    record = save_confirmation_for_current(repo, session_id) if confirmed else None
    return SeededSession(
        session_id=session_id,
        intent_revision_id=intent_revision.intent_revision_id,
        execution_revision_id=execution.execution_revision_id,
        confirmation_id=None if record is None else record.confirmation_id,
    )


def append_intent(
    repo: SQLiteRepository, session_id: str, intent: VisualIntent
) -> IntentRevision:
    """在当前 head 之后追加一条 IntentRevision（使旧 Confirmation 失效）。"""
    snapshot = repo.get_current_session_snapshot(session_id)
    revision = IntentRevision(
        intent_revision_id=new_id("irev"),
        session_id=session_id,
        parent_revision_id=snapshot.current_intent_revision_id,
        intent=intent,
    )
    repo.append_intent_revision(revision)
    return revision


def return_to_confirmation(repo: SQLiteRepository, session_id: str) -> None:
    """WAITING_REVIEW → UNDERSTANDING → WAITING_CONFIRMATION（revision 未变，确认仍有效）。"""
    repo.transition_state(session_id, WorkflowState.UNDERSTANDING)
    repo.transition_state(session_id, WorkflowState.WAITING_CONFIRMATION)


def fail_once_on_review_transition(monkeypatch, repo: SQLiteRepository) -> dict[str, bool]:
    """让下一次 `WAITING_REVIEW` 迁移失败一次（Artifact 已保存 → FAILED + 原 Artifact）。"""
    state = {"triggered": False}
    original = repo.transition_state

    def flaky(session_id: str, to_state) -> None:
        if to_state is WorkflowState.WAITING_REVIEW and not state["triggered"]:
            state["triggered"] = True
            raise InvalidStateTransitionError(
                session_id,
                WorkflowState.GENERATING,
                to_state,
                reason="simulated review transition failure",
            )
        return original(session_id, to_state)

    monkeypatch.setattr(repo, "transition_state", flaky)
    return state


# ---------------------------------------------------------------------------
# P1 真实工作流（全 Fake、全离线）
# ---------------------------------------------------------------------------


def set_entry(path: str, value: str | int, *, answers: bool = False) -> dict[str, Any]:
    return {
        "operation": "SET",
        "path": path,
        "value": value,
        "answers_pending_question": answers,
        "evidence_fragment": None,
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


def ready_responses() -> list[str]:
    """达到 Ready 的 7 轮剧本；第 7 轮把 `lighting.character` 委托给系统。"""
    entries = [
        set_entry("subject.description", "a cat"),
        set_entry("style.primary", "photorealistic", answers=True),
        set_entry("environment.mode", "studio", answers=True),
        set_entry("environment.location", "a wooden table", answers=True),
        set_entry("composition.framing", "medium_shot", answers=True),
        set_entry("subject.pose_action", "sitting", answers=True),
        delegate_entry("lighting.character", answers=True),
    ]
    return [response(entry) for entry in entries]


def make_service(repo: SQLiteRepository, llm: FakeLLMProvider) -> WorkflowService:
    return WorkflowService(
        repo=repo,
        intent_engine=IntentEngine(Interpreter(llm)),
        question_builder=QuestionBuilder(),
        settings=make_settings(),
    )


@dataclass(frozen=True)
class P1Run:
    service: WorkflowService
    repo: SQLiteRepository
    session_id: str
    confirmation: ConfirmationRecord


def run_p1_to_confirmation(tmp_path, *, name: str = "p2.db") -> P1Run:
    """多轮澄清 → Ready → WAITING_CONFIRMATION → 确认（全 Fake、全离线）。"""
    repo = make_repo(tmp_path, name)
    llm = FakeLLMProvider(ready_responses())
    service = make_service(repo, llm)
    session_id = service.create_session().session_id
    outcome = None
    for text in USER_TURNS:
        outcome = service.submit_message(session_id, text)
    assert outcome is not None
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
    assert outcome.confirmation_summary is not None
    confirmation = service.confirm_current_intent(
        session_id,
        outcome.snapshot.current_intent_revision_id,
        outcome.snapshot.current_execution_revision_id,
        compute_summary_hash(outcome.confirmation_summary),
    )
    return P1Run(service=service, repo=repo, session_id=session_id, confirmation=confirmation)


# ---------------------------------------------------------------------------
# 账本 / 文件断言工具
# ---------------------------------------------------------------------------


def artifact_count(repo: SQLiteRepository, table: str) -> int:
    return int(repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def artifact_counts(repo: SQLiteRepository) -> dict[str, int]:
    return {
        table: artifact_count(repo, table)
        for table in (
            "intent_revisions",
            "prompt_artifacts",
            "generation_artifacts",
            "feedback_results",
            "realization_states",
        )
    }


def output_file(ref_path: str) -> Path:
    """把 OutputRef.path（相对项目根）还原成真实文件路径。"""
    return (Path(PROJECT_ROOT) / ref_path).resolve()


def output_files_exist(pipeline_output_dir: Path, artifact) -> bool:
    return all(
        (pipeline_output_dir / artifact.generation_id / f"image_{index}.png").is_file()
        for index in range(1, len(artifact.output_refs) + 1)
    )
