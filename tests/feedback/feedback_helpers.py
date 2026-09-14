"""Step 09 测试共享工具（唯一命名模块；禁止 `from conftest import ...`，禁止跨目录 import）。

自包含、全离线：伪造 `Settings`（不读 .env）、`tmp_path` SQLite、`FakeImageProvider`
（确定性 PNG 常量）、`FakeLLMProvider`（按 system prompt 分派的脚本），以及 P3 装配：

1. `seed_confirmed_session`：把确定性构造的 Intent/Execution revision 与 Confirmation
   直接写入临时 Repository（便于精确覆盖单个场景）；
2. `make_p3_session`：seed → 跑一次真实 `GenerationPipeline.generate` → `WAITING_REVIEW`，
   返回 `ReviewService` / `WorkflowService` / `GenerationPipeline` 的可驱动 bundle；
3. 反馈反馈脚本构造器：`feedback_response(...)` / `revise_entry(...)` / `clarify_response(...)`；
4. 历史与测量的只读工具：`artifact_counts`、`artifact_payloads`、`intent_values`、
   `generation_measurement`。

反馈 LLM 与 Interpreter LLM 共用同一个 `FakeLLMProvider`，按 system prompt 文本分派
（`"Feedback Interpreter"` → 反馈脚本；否则 → Interpreter 脚本），因此多轮剧本不会因
调用顺序变化而错位。
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

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
from visual_intent_agent.feedback import FeedbackEngine
from visual_intent_agent.generation import GenerationArtifact, GenerationPipeline
from visual_intent_agent.intent_engine import IntentEngine, Interpreter
from visual_intent_agent.persistence import (
    ConfirmationRecord,
    SQLiteRepository,
    WorkflowState,
)
from visual_intent_agent.prompt_engine import PromptEngine, QwenImageRenderer
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_image import FakeImageProvider
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.providers.llm import LLMRequest, LLMResponse
from visual_intent_agent.workflow import (
    QuestionBuilder,
    WorkflowService,
    build_confirmation_summary,
    compute_summary_hash,
)
from visual_intent_agent.workflow.review import ReviewService

#: 伪造 Provider 配置（不是真实凭据；任何测试都不得读取项目 .env）。
FAKE_BASE_URL = "https://provider.invalid/v1"
FAKE_API_KEY = "unit-test-key"
FAKE_IMAGE_MODEL = "qwen-image-3.0"
FAKE_OUTPUT_SIZE = "1024x1024"

#: FakeLLMProvider 响应模型名（确定性）。
FAKE_LLM_MODEL = "fake-llm"

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

#: P3 基础意图：五个有值路径 + 两个显式委托路径（lighting / location 各产生 Realization）。
P3_INTENT_VALUES: dict[str, Any] = {
    "subject.description": "a cat",
    "style.primary": "photorealistic",
    "environment.mode": "studio",
    "composition.framing": "medium_shot",
    "subject.pose_action": "sitting",
}
P3_DELEGATED: tuple[str, ...] = ("lighting.character", "environment.location")


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


def make_repo(tmp_path, name: str = "p3.db") -> SQLiteRepository:
    return SQLiteRepository(tmp_path / name)


def make_prompt_engine(repo: SQLiteRepository) -> PromptEngine:
    return PromptEngine(QwenImageRenderer(), repo)


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
        prompt_engine=engine if engine is not None else make_prompt_engine(repo),
        image_provider=provider if provider is not None else FakeImageProvider(),
        output_dir=output_dir,
    )


def intent_with(
    values: Mapping[str, Any] | None = None,
    *,
    delegated: Iterable[str] = (),
    not_applicable: Iterable[str] = (),
    pinned: Iterable[str] = (),
) -> VisualIntent:
    """构造一个 VisualIntent（仅测试装配用）。"""
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


def p3_intent() -> VisualIntent:
    """P3 基础意图（可编译、含两个 delegated 路径）。"""
    return intent_with(P3_INTENT_VALUES, delegated=P3_DELEGATED)


# ---------------------------------------------------------------------------
# 已确认会话 + 一次生成
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


def confirm_current(service: WorkflowService, session_id: str) -> ConfirmationRecord:
    """在当前 `WAITING_CONFIRMATION` 上做一次真实确认（重算摘要 → 提交 hash）。"""
    summary = service.get_confirmation_summary(session_id)
    snapshot = service.get_session(session_id)
    return service.confirm_current_intent(
        session_id,
        snapshot.current_intent_revision_id,
        snapshot.current_execution_revision_id,
        compute_summary_hash(summary),
    )


def confirm_and_generate(bundle: "P3Session") -> GenerationArtifact:
    """重新确认 + 既有 `GenerationPipeline.generate`（P3 的"再生成"唯一路径）。"""
    confirm_current(bundle.workflow, bundle.session_id)
    return bundle.pipeline.generate(bundle.session_id)


# ---------------------------------------------------------------------------
# 按 system prompt 分派的 Fake LLM
# ---------------------------------------------------------------------------


class _DispatchLLM:
    """把 `FakeLLMProvider` 包装成"反馈 / 解释器"两条独立脚本队列。"""

    def __init__(
        self,
        feedback_responses: Iterable[str] = (),
        interpreter_responses: Iterable[str] = (),
    ) -> None:
        self.feedback_responses: deque[str] = deque(feedback_responses)
        self.interpreter_responses: deque[str] = deque(interpreter_responses)
        self.provider = FakeLLMProvider(self._handle)

    def _handle(self, request: LLMRequest) -> LLMResponse:
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
        return LLMResponse(content=queue.popleft(), model=FAKE_LLM_MODEL)

    @property
    def requests(self) -> list[LLMRequest]:
        return self.provider.requests


# ---------------------------------------------------------------------------
# P3 session bundle
# ---------------------------------------------------------------------------


@dataclass
class P3Session:
    repo: SQLiteRepository
    session_id: str
    output_dir: Path
    pipeline: GenerationPipeline
    review: ReviewService
    workflow: WorkflowService
    llm: _DispatchLLM
    confirmation: ConfirmationRecord
    generation: GenerationArtifact
    intent: VisualIntent


def make_p3_session(
    tmp_path,
    *,
    intent: VisualIntent | None = None,
    feedback_responses: Iterable[str] = (),
    interpreter_responses: Iterable[str] = (),
    name: str = "p3.db",
    provider: Any = None,
) -> P3Session:
    """seed 一个已确认会话 → 真实 generate → WAITING_REVIEW，并返回可驱动 bundle。"""
    repo = make_repo(tmp_path, name)
    output_dir = tmp_path / "outputs"
    llm = _DispatchLLM(feedback_responses, interpreter_responses)
    seeded_confirmation = seed_confirmed_session(repo, intent or p3_intent())
    pipeline = make_pipeline(repo, output_dir=output_dir, provider=provider)
    generation = pipeline.generate(seeded_confirmation.session_id)

    review = ReviewService(
        repo=repo,
        feedback_engine=FeedbackEngine(llm.provider),
        question_builder=QuestionBuilder(),
    )
    workflow = WorkflowService(
        repo=repo,
        intent_engine=IntentEngine(Interpreter(llm.provider)),
        question_builder=QuestionBuilder(),
        settings=make_settings(),
    )
    confirmation = repo.get_confirmation(seeded_confirmation.confirmation_id)
    return P3Session(
        repo=repo,
        session_id=seeded_confirmation.session_id,
        output_dir=output_dir,
        pipeline=pipeline,
        review=review,
        workflow=workflow,
        llm=llm,
        confirmation=confirmation,
        generation=generation,
        intent=intent or p3_intent(),
    )


# ---------------------------------------------------------------------------
# 反馈 / 解释器脚本构造
# ---------------------------------------------------------------------------


def revise_entry(
    path: str,
    *,
    value: str | int | None = None,
    resolution: str | None = None,
    operation: str = "SET",
    evidence_fragment: str | None = None,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "path": path,
        "value": value,
        "resolution": resolution,
        "evidence_fragment": evidence_fragment,
    }


def feedback_response(
    decision: str,
    *,
    candidate_deltas: Iterable[dict[str, Any]] = (),
    preserve_paths: Iterable[str] = (),
    compile_feedback: str | None = None,
    clarify_path: str | None = None,
    clarify_reason: str | None = None,
    **extra: Any,
) -> str:
    """序列化一条 FeedbackEngine 脚本响应（`**extra` 用于注入非法字段做负例）。"""
    payload: dict[str, Any] = {"decision": decision}
    if candidate_deltas:
        payload["candidate_deltas"] = list(candidate_deltas)
    if preserve_paths:
        payload["preserve_paths"] = list(preserve_paths)
    if compile_feedback is not None:
        payload["compile_feedback"] = compile_feedback
    if clarify_path is not None:
        payload["clarify_path"] = clarify_path
    if clarify_reason is not None:
        payload["clarify_reason"] = clarify_reason
    payload.update(extra)
    return json.dumps(payload)


def interpreter_response(
    *entries: dict[str, Any],
    unresolved_language: Iterable[str] = (),
) -> str:
    """序列化一条 Interpreter 脚本响应（与 `INTERPRETER_OUTPUT_SCHEMA` 对应）。"""
    return json.dumps(
        {
            "candidate_deltas": list(entries),
            "detected_conflicts": [],
            "unresolved_language": list(unresolved_language),
        }
    )


def set_entry(
    path: str, value: str | int, *, answers: bool = False
) -> dict[str, Any]:
    return {
        "operation": "SET",
        "path": path,
        "value": value,
        "answers_pending_question": answers,
        "evidence_fragment": None,
    }


# ---------------------------------------------------------------------------
# 只读测量工具
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


def artifact_payloads(repo: SQLiteRepository, table: str) -> dict[str, str]:
    """`id -> payload` 快照（用于断言历史 Artifact 逐字节不被覆盖）。"""
    pk, column = {
        "intent_revisions": ("intent_revision_id", "revision_json"),
        "prompt_artifacts": ("prompt_artifact_id", "payload"),
        "generation_artifacts": ("generation_id", "payload"),
        "feedback_results": ("feedback_id", "payload"),
        "realization_states": ("realization_id", "payload"),
    }[table]
    rows = repo.connection.execute(f"SELECT {pk}, {column} FROM {table}").fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def intent_values(intent: VisualIntent) -> dict[str, Any]:
    """路径 → 值（12 条白名单；None 表示缺失）。"""
    from visual_intent_agent.domain import INTENT_PATHS

    return {path: read_path(intent, path) for path in sorted(INTENT_PATHS)}


def read_path(intent: VisualIntent, path: str) -> Any:
    facet_name, _, field_name = path.partition(".")
    facet = getattr(intent, facet_name, None)
    return None if facet is None else getattr(facet, field_name, None)


def current_intent(repo: SQLiteRepository, session_id: str) -> VisualIntent:
    snapshot = repo.get_current_session_snapshot(session_id)
    assert snapshot.current_intent_revision_id is not None
    return repo.get_intent_revision(snapshot.current_intent_revision_id).intent


def current_realization(repo: SQLiteRepository, session_id: str):
    from visual_intent_agent.realization.models import RealizationState

    stored = repo.get_current_realization_state(session_id)
    return None if stored is None else RealizationState.model_validate_json(stored.payload)


def load_feedback_request(
    bundle: "P3Session",
    *,
    feedback_text: str = "pull the camera back",
    message_id: str = "msg_probe",
):
    """由已生成的 P3 session 装配一个只读 `FeedbackRequest`（引擎级测试用）。"""
    from visual_intent_agent.feedback import FeedbackRequest
    from visual_intent_agent.prompt_engine import PromptArtifact

    repository = bundle.repo
    generation = bundle.generation
    prompt_artifact = PromptArtifact.model_validate_json(
        repository.get_prompt_artifact(generation.prompt_artifact_id).payload
    )
    return FeedbackRequest(
        session_id=bundle.session_id,
        message_id=message_id,
        feedback_text=feedback_text,
        generation=generation,
        prompt_artifact=prompt_artifact,
        current_intent=current_intent(repository, bundle.session_id),
        realization_state=current_realization(repository, bundle.session_id),
    )


def active_realization_value(repo: SQLiteRepository, session_id: str, path: str):
    state = current_realization(repo, session_id)
    return None if state is None else state.active_value_for(path)


def output_bytes_sha256(ref_path: str) -> str:
    return hashlib.sha256((Path(PROJECT_ROOT) / ref_path).read_bytes()).hexdigest()


def generation_measurement(artifact: GenerationArtifact) -> dict[str, Any]:
    """图片层测量点（只测量，不承诺视觉一致）。

    记录：generation_id / prompt_artifact_id / target_model / model_version /
    parameters / seed / 输出文件（路径、MIME、字节数、sha256）。FakeImageProvider
    返回确定性字节，因此本层测量在离线测试中可复现；**这不构成任何"视觉不变"承诺**
    （README 不变量 / 任务书「两层 Preservation」：Image Preservation 只测量）。
    """
    return {
        "generation_id": artifact.generation_id,
        "prompt_artifact_id": artifact.prompt_artifact_id,
        "target_model": artifact.target_model,
        "model_version": artifact.model_version,
        "size": artifact.parameters.size,
        "seed": artifact.seed,
        "outputs": [
            {
                "path": ref.path,
                "mime_type": ref.mime_type,
                "byte_size": ref.byte_size,
                "sha256": output_bytes_sha256(ref.path),
            }
            for ref in artifact.output_refs
        ],
    }


__all__ = [
    "FAKE_BASE_URL",
    "FAKE_API_KEY",
    "FAKE_IMAGE_MODEL",
    "FAKE_OUTPUT_SIZE",
    "FAKE_LLM_MODEL",
    "P3_INTENT_VALUES",
    "P3_DELEGATED",
    "SeededSession",
    "P3Session",
    "make_settings",
    "make_repo",
    "make_prompt_engine",
    "make_pipeline",
    "make_p3_session",
    "intent_with",
    "p3_intent",
    "save_confirmation_for_current",
    "seed_confirmed_session",
    "confirm_current",
    "confirm_and_generate",
    "feedback_response",
    "interpreter_response",
    "revise_entry",
    "set_entry",
    "artifact_count",
    "artifact_counts",
    "artifact_payloads",
    "intent_values",
    "read_path",
    "current_intent",
    "current_realization",
    "load_feedback_request",
    "active_realization_value",
    "output_bytes_sha256",
    "generation_measurement",
]
