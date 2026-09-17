"""MVP v0.6 Step 02：隔离评测上下文的合法装配（固定 revision ID + 确认门禁）。

职责（只读产品公开接口，不改运行时、不做 monkeypatch）：

1. **冻结的 revision seed 映射**：`revision_seed_for(case_id, repetition)` 用
   `case_id + repetition` 的**单次确定性哈希**给出 `intent_revision_id`，B/C 两侧写入
   **相同值**。映射不读取候选值、不循环挑 ID、不依赖生成结果——**禁止**任何
   “搜索让 C 必赢 / 让 B/C 必差异”的选种。若某样本的知识候选值恰好等于确定性回退值，
   该样本是**有效样本**，如实记录为“采用但 Prompt 无差异”，不得人为规避。
2. **合法确认绑定**：直接经 `Repository` 公开写接口落固定 ID 的
   IntentRevision / ExecutionRevision（`append_*` 原样接受调用方 ID，只要求
   `parent_revision_id == 当前 head`），再 `transition_state(WAITING_CONFIRMATION)`
   并调用 `WorkflowService.confirm_current_intent`（重算 summary_hash 的 Hard
   Confirmation Gate）。评测层代表**事先批准的场景**，不伪称真实用户交互。
3. **B/C 差异只来自知识开关**：`arm_knowledge_engine("B") is None`；
   `"C"` 注入加载 `knowledge_base/v0.5/` 的 `LocalKnowledgeEngine`。

为什么不能走 `WorkflowService.submit_message`：该入口用 `new_id()` 生成随机
`intent_revision_id`（`visual_intent_agent/workflow/service.py`），无法让两侧共享
同一 seed。改用 Repository 公开写接口并不绕过任何领域校验：parent head、确认门禁、
summary_hash、binding_hash、Realization / Prompt 编译门禁全部照常生效。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

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
)
from visual_intent_agent.intent_engine import IntentEngine, Interpreter
from visual_intent_agent.knowledge import (
    KnowledgeCorpus,
    LocalKnowledgeEngine,
    compute_corpus_fingerprint,
    load_corpus,
)
from visual_intent_agent.persistence import SQLiteRepository, WorkflowState
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.workflow import (
    QuestionBuilder,
    WorkflowService,
    compute_summary_hash,
)

from .records import AnnotationSpec, CaseSpec, make_record_id

#: revision seed 族（与 Step 01 校验工具的 `irev_v06` 约定一致）。
REVISION_SEED_FAMILY = "irev_v06"
#: 冻结的 seed 规则说明（写入 run.json）。
REVISION_SEED_RULE = (
    "intent_revision_id = irev_v06_<sha256(case_id|rep<N>)[:16]>；单次固定哈希，"
    "不读取候选值、不循环挑 ID、不依赖结果；B/C 两侧同值。"
)
#: 调用顺序规则说明（写入 run.json）。
CALL_ORDER_RULE = (
    "B_first/C_first 由 sha256(case_id|rep<N>|order) 的最低位确定；与生成结果无关，"
    "记录于每条 PairRecord.call_order，可据此复建真实调用次序。"
)
#: 冻结输出尺寸（ExecutionRevision.output_size；比例由尺寸派生）。
OUTPUT_SIZE = "1024x1024"

#: 各 facet 的模型（仅装配用；产品代码只 import 白名单）。
_FACET_MODELS: dict[str, type] = {
    "subject": SubjectFacet,
    "composition": CompositionFacet,
    "environment": EnvironmentFacet,
    "style": StyleFacet,
    "lighting": LightingFacet,
    "camera": CameraFacet,
    "color": ColorFacet,
}


class ContextError(RuntimeError):
    """评测上下文无法合法装配（例如冻结 seed 族不满足预注册约束）。"""


class ConfirmedContext(BaseModel):
    """一个隔离会话的已确认评测上下文（B/C 各一份，互不共享库）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    intent_revision_id: str
    execution_revision_id: str
    confirmation_id: str
    summary_hash: str
    confirmation_valid: bool
    target_model: str
    output_size: str


# ---------------------------------------------------------------------------
# 输入读取
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:  # pragma: no cover - 冻结文件已由 Step 01 校验
            raise ContextError(f"{path}:{line_number} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ContextError(f"{path}:{line_number} must be a JSON object")
        rows.append(payload)
    return rows


def load_cases(path: Path) -> list[CaseSpec]:
    """读取 `cases.jsonl`（顺序保留；重复 case_id 显式拒绝）。"""
    cases = [CaseSpec.model_validate(row) for row in _read_jsonl(path)]
    ids = [case.case_id for case in cases]
    if len(set(ids)) != len(ids):
        raise ContextError(f"{path} contains duplicate case_id values")
    return cases


def load_annotations(path: Path) -> dict[str, AnnotationSpec]:
    """读取 `annotations.jsonl`，返回 case_id → 标注。"""
    annotations: dict[str, AnnotationSpec] = {}
    for row in _read_jsonl(path):
        annotation = AnnotationSpec.model_validate(row)
        if annotation.case_id in annotations:
            raise ContextError(f"{path} contains duplicate annotation for {annotation.case_id!r}")
        annotations[annotation.case_id] = annotation
    return annotations


# ---------------------------------------------------------------------------
# Intent 装配
# ---------------------------------------------------------------------------


def build_intent(case: CaseSpec) -> VisualIntent:
    """从冻结 case 的 `confirmed_intent` + `delegated_paths` + `pinned_paths` 装配 Intent。

    `delegated_paths` 记为 `user_delegated`（且**不给值**，供 RAG/回退实现）；
    `pinned_paths` 进 `pinned_paths`；`explicit_paths` 的值直接来自 `confirmed_intent`。
    """
    buckets: dict[str, dict[str, object]] = {name: {} for name in _FACET_MODELS}
    for path, value in case.confirmed_intent.items():
        facet_name, _, field_name = path.partition(".")
        if facet_name not in buckets:
            raise ContextError(f"case {case.case_id!r}: unknown intent facet in path {path!r}")
        buckets[facet_name][field_name] = value
    resolutions: dict[str, ResolutionRecord] = {}
    for path in case.delegated_paths:
        resolutions[path] = ResolutionRecord(resolution=Resolution.USER_DELEGATED)
    facets = {name: model(**buckets[name]) for name, model in _FACET_MODELS.items()}
    return VisualIntent(
        **facets,
        resolutions=resolutions,
        pinned_paths=frozenset(case.pinned_paths),
    )


# ---------------------------------------------------------------------------
# 冻结 seed 映射
# ---------------------------------------------------------------------------


def _seed_candidate(case_id: str, repetition: int) -> str:
    return f"{REVISION_SEED_FAMILY}_{hashlib.sha256(f'{case_id}|rep{repetition}'.encode()).hexdigest()[:16]}"


def revision_seed_for(*, case_id: str, repetition: int) -> str:
    """返回单次固定哈希得到的 `intent_revision_id`；B/C 同值。

    纯函数：只读 `case_id + repetition`，**不读取候选值、不循环搜索、不依赖结果**。
    若某样本的采用值恰好等于确定性回退值，那是有效结果（采用但 Prompt 无差异），
    由记录如实反映，不做任何规避。
    """
    if repetition < 1:
        raise ContextError("repetition must be >= 1")
    return _seed_candidate(case_id, repetition)


def execution_revision_id_for(*, case_id: str, repetition: int) -> str:
    """确定性的 ExecutionRevision ID（不参与任何选值种子）。"""
    return f"erev_v06_{hashlib.sha256(f'{case_id}|rep{repetition}|exec'.encode()).hexdigest()[:16]}"


def session_id_for(*, case_id: str, repetition: int, arm: str, attempt: int = 1) -> str:
    """确定性的会话 ID；同一 arm 的续跑 attempt 使用不同 ID/目录，避免 session_exists。"""
    return make_record_id("sesv06", case_id, f"rep{repetition}", arm, f"attempt{attempt}")


def call_order_for(*, case_id: str, repetition: int) -> str:
    """每对固定的调用顺序（与结果无关，可复建）。"""
    lowest = hashlib.sha256(f"{case_id}|rep{repetition}|order".encode("utf-8")).digest()[0]
    return "C_first" if lowest % 2 else "B_first"


# ---------------------------------------------------------------------------
# 已确认会话装配（Hard Confirmation Gate）
# ---------------------------------------------------------------------------


def seed_confirmed_session(
    *,
    repo: SQLiteRepository,
    case: CaseSpec,
    repetition: int,
    arm: str,
    settings: Settings,
    attempt: int = 1,
) -> ConfirmedContext:
    """在一个**全新** SQLite 中写入固定 revision ID 的已确认上下文。

    - 不再调用 `submit_message`（随机 revision），直接经公开 Repository 写接口落库；
    - 确认走 `WorkflowService.confirm_current_intent`（唯一 Hard Confirmation Gate）；
    - 无 active Realization（全新库、只写两类 revision）；
    - `attempt` 只影响会话 ID（每次续跑必须使用全新库/会话，绝不复用旧 DB）。
    """
    revision_id = revision_seed_for(case_id=case.case_id, repetition=repetition)
    execution_id = execution_revision_id_for(case_id=case.case_id, repetition=repetition)
    session_id = session_id_for(
        case_id=case.case_id, repetition=repetition, arm=arm, attempt=attempt
    )

    repo.create_session(session_id)
    repo.append_execution_revision(
        ExecutionRevision(
            execution_revision_id=execution_id,
            session_id=session_id,
            parent_revision_id=None,
            target_model=settings.image_model,
            output_size=OUTPUT_SIZE,
        )
    )
    repo.append_intent_revision(
        IntentRevision(
            intent_revision_id=revision_id,
            session_id=session_id,
            parent_revision_id=None,
            intent=build_intent(case),
            applied_deltas=[],
        )
    )
    repo.transition_state(session_id, WorkflowState.WAITING_CONFIRMATION)

    workflow = WorkflowService(
        repo=repo,
        intent_engine=IntentEngine(Interpreter(FakeLLMProvider([]))),
        question_builder=QuestionBuilder(),
        settings=settings,
    )
    summary = workflow.get_confirmation_summary(session_id)
    summary_hash = compute_summary_hash(summary)
    record = workflow.confirm_current_intent(session_id, revision_id, execution_id, summary_hash)
    return ConfirmedContext(
        session_id=session_id,
        intent_revision_id=revision_id,
        execution_revision_id=execution_id,
        confirmation_id=record.confirmation_id,
        summary_hash=summary_hash,
        confirmation_valid=repo.is_confirmation_valid(record.confirmation_id),
        target_model=settings.image_model,
        output_size=OUTPUT_SIZE,
    )


# ---------------------------------------------------------------------------
# 知识语料（仅 C 臂）
# ---------------------------------------------------------------------------


def load_knowledge_corpus(corpus_dir: Path) -> KnowledgeCorpus:
    """加载并校验冻结语料（离线、只读）。"""
    return load_corpus(Path(corpus_dir))


def knowledge_fingerprint(corpus: KnowledgeCorpus) -> str:
    return compute_corpus_fingerprint(corpus.manifest)


def knowledge_manifest_sha256(corpus_dir: Path) -> str:
    from evaluation.manifest import sha256_file

    return sha256_file(Path(corpus_dir) / "manifest.json")


def arm_knowledge_engine(arm: str, corpus: KnowledgeCorpus | None):
    """B → None（关闭 RAG）；C → LocalKnowledgeEngine（显式加载冻结语料）。"""
    if arm == "B":
        return None
    if corpus is None:
        raise ContextError("arm C requires the frozen v0.5 knowledge corpus")
    return LocalKnowledgeEngine(corpus=corpus)


__all__ = [
    "REVISION_SEED_FAMILY",
    "REVISION_SEED_RULE",
    "CALL_ORDER_RULE",
    "OUTPUT_SIZE",
    "ContextError",
    "ConfirmedContext",
    "load_cases",
    "load_annotations",
    "build_intent",
    "revision_seed_for",
    "execution_revision_id_for",
    "session_id_for",
    "call_order_for",
    "seed_confirmed_session",
    "load_knowledge_corpus",
    "knowledge_fingerprint",
    "knowledge_manifest_sha256",
    "arm_knowledge_engine",
    "PROJECT_ROOT",
]
