"""v0.5 Step 03：用**最终发布语料**做三路径真实采用与追溯集成（全离线 Fake）。

任务书 `03_corpus_release.md`「必须使用最终语料做离线集成」要求：不用 `--demo --rag`
内置夹具冒充生产语料；复用现有依赖注入接口，加载 `knowledge_base/v0.5/`，连接
**临时真实 SQLite Repository** 与 **Fake Image Provider**。

本文件覆盖：

- 5 条获批规则各自的**可达正例**（条件满足 → 检索命中 → 消费端 adopted → 实际
  Realization / PromptArtifact / Bundle 可读回）；
- 每条规则的**条件不符反例**（不做保护边界放宽，回退确定性基线）；
- **PIN 保护边界**（被 PIN 的委托路径根本不检索、不被覆盖）；
- **关闭 RAG 的确定性基线差异**：差异只在被授权路径，且不宣称更优；
- **active Realization 复用 / 原 Prompt retry 不重新检索 / 快照切换不改写历史 / 旧 Bundle
  兼容**；
- 采用过程**无额外 LLM / 网络调用**（知识引擎是本地 JSONL + 内存词法检索）。

所有 `approved` 都来自发布快照本身（真实审核决定），不是本文件的测试夹具。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from generation_helpers import (
    FAKE_IMAGE_MODEL,
    FAKE_OUTPUT_SIZE,
    artifact_count,
    intent_with,
    make_pipeline,
    make_repo,
    return_to_confirmation,
    run_p1_to_confirmation,
    save_confirmation_for_current,
)

from visual_intent_agent.domain import ExecutionRevision, IntentRevision, new_id
from visual_intent_agent.knowledge import (
    ELIGIBILITY_SNAPSHOT_VERSION,
    AdoptionOutcome,
    AdoptionRejectionReason,
    BundleStatus,
    KnowledgeBundle,
    LocalKnowledgeEngine,
    RetrievalOutcome,
    ReviewStatus,
    compute_content_hash,
    compute_corpus_fingerprint,
    load_corpus,
)
from visual_intent_agent.persistence import WorkflowState
from visual_intent_agent.prompt_engine import PromptArtifact, PromptEngine, QwenImageRenderer
from visual_intent_agent.prompt_engine.engine import (
    RENDER_LABELS,
    evaluate_recommendation,
    select_delegated_value,
)
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_image import FAKE_PNG_BYTES, FakeImageProvider
from visual_intent_agent.providers.image import GeneratedImage, ImageGenerationResult
from visual_intent_agent.realization.models import (
    REALIZATION_STATUS_ACTIVE,
    RealizationState,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = PROJECT_ROOT / "knowledge_base" / "v0.5"
LEGACY_DIR = PROJECT_ROOT / "knowledge_base" / "v0.4"

CORPUS_VERSION = "v0.5-approved-1"
MODEL = FAKE_IMAGE_MODEL
#: 发布快照里 5 条规则的**真实**审核时间（见 knowledge_base/v0.5/APPROVAL.md）。
RELEASE_REVIEWED_AT = datetime(2026, 9, 16, 10, 25, 59, tzinfo=timezone.utc)
LIGHTING = "lighting.character"
CAMERA = "camera.depth_of_field"
FRAMING = "composition.framing"


# ---------------------------------------------------------------------------
# 三条知识路径的获批规则：可达正例上下文 + 条件不符反例上下文
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleCase:
    knowledge_id: str
    path: str
    candidate: str
    positive: dict[str, object]
    negative: dict[str, object]


RULE_CASES: tuple[RuleCase, ...] = (
    RuleCase(
        knowledge_id="camera.depth_of_field.shallow_for_close_up",
        path=CAMERA,
        candidate="shallow",
        positive={
            "composition.framing": "close_up",
            "style.primary": "cinematic",
            "subject.description": "a cat",
        },
        # 只改一条条件：style 不再是 cinematic ⇒ A1 条件不符（A2 也因 framing 不满足）。
        negative={
            "composition.framing": "close_up",
            "style.primary": "photorealistic",
            "subject.description": "a cat",
        },
    ),
    RuleCase(
        knowledge_id="camera.depth_of_field.deep_for_wide_shot",
        path=CAMERA,
        candidate="deep",
        positive={
            "composition.framing": "wide_shot",
            "environment.mode": "outdoor",
            "subject.description": "a cat",
        },
        # environment 不再是 outdoor ⇒ A2 条件不符。
        negative={
            "composition.framing": "wide_shot",
            "environment.mode": "studio",
            "subject.description": "a cat",
        },
    ),
    RuleCase(
        knowledge_id="composition.framing.medium_shot_for_standing_pose",
        path=FRAMING,
        candidate="medium_shot",
        positive={"subject.pose_action": "standing", "environment.mode": "studio"},
        # environment 不是 studio ⇒ B3 条件不符（framing 路径只有这一条）。
        negative={"subject.pose_action": "standing", "environment.mode": "indoor"},
    ),
    RuleCase(
        knowledge_id="lighting.character.soft_for_tight_framing",
        path=LIGHTING,
        candidate="soft",
        positive={
            "composition.framing": "close_up",
            "environment.mode": "studio",
            "subject.description": "a cat",
        },
        # environment 不是 studio/indoor ⇒ C1 条件不符；C2 因缺 camera.angle 也不满足。
        negative={
            "composition.framing": "close_up",
            "environment.mode": "outdoor",
            "subject.description": "a cat",
        },
    ),
    RuleCase(
        knowledge_id="lighting.character.dramatic_for_angled_framing",
        path=LIGHTING,
        candidate="dramatic",
        positive={
            "camera.angle": "low_angle",
            "style.primary": "cinematic",
            "composition.framing": "wide_shot",
        },
        # angle 不再是 low_angle ⇒ C2 条件不符；framing=wide_shot 同时排除 C1。
        negative={
            "camera.angle": "high_angle",
            "style.primary": "cinematic",
            "composition.framing": "wide_shot",
        },
    ),
)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


class RecordingEngine:
    """记录检索请求次数（断言复用 / retry 不产生新检索）。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.requests: list[object] = []

    def retrieve(self, request):  # noqa: ANN001 - 测试包装器
        self.requests.append(request)
        return self.inner.retrieve(request)


def release_corpus():
    return load_corpus(RELEASE_DIR)


def _revision_id_avoiding(path: str, candidate: str) -> str:
    """选一个 intent_revision_id，使确定性回退**不等于**知识候选值。

    这样"RAG 选值与关闭 RAG 基线不同"是被授权路径的合法工程上下文，而不是伪造差异。
    """
    for index in range(1000):
        revision_id = f"irev_v05_{index:032x}"
        if select_delegated_value(path, revision_id) != candidate:
            return revision_id
    raise AssertionError("no revision id produced a fallback different from the candidate")


def _seed_confirmed(repo, intent, revision_id: str):
    """写入一条已确认会话，并**控制** intent_revision_id（决定确定性回退值）。"""
    session_id = new_id("ses")
    repo.create_session(session_id)
    execution = ExecutionRevision(
        execution_revision_id=new_id("erev"),
        session_id=session_id,
        parent_revision_id=None,
        target_model=MODEL,
        output_size=FAKE_OUTPUT_SIZE,
    )
    repo.append_execution_revision(execution)
    revision = IntentRevision(
        intent_revision_id=revision_id,
        session_id=session_id,
        parent_revision_id=None,
        intent=intent,
    )
    repo.append_intent_revision(revision)
    repo.transition_state(session_id, WorkflowState.WAITING_CONFIRMATION)
    confirmation = save_confirmation_for_current(repo, session_id)
    return session_id, confirmation


def _pipeline(tmp_path, repo, engine, provider, name: str = "out"):
    return make_pipeline(repo, output_dir=tmp_path / name, provider=provider, engine=engine)


def _active_value(repo, session_id: str, path: str):
    state = RealizationState.model_validate_json(
        repo.get_current_realization_state(session_id).payload
    )
    return next(
        value
        for value in state.values
        if value.path == path and value.status == REALIZATION_STATUS_ACTIVE
    )


def _latest_prompt(repo, session_id: str) -> PromptArtifact:
    return PromptArtifact.model_validate_json(repo.get_latest_prompt_artifact(session_id).payload)


def _release_engine(repo, corpus):
    return PromptEngine(
        QwenImageRenderer(), repo, knowledge_engine=LocalKnowledgeEngine(corpus=corpus)
    )


# ---------------------------------------------------------------------------
# 正例：每条获批规则都可达并被实际采用 + 可追溯读回
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", RULE_CASES, ids=[case.knowledge_id for case in RULE_CASES])
def test_each_approved_rule_is_reachable_and_actually_adopted(tmp_path, case: RuleCase):
    corpus = release_corpus()
    unit = next(u for u in corpus.build_units() if u.knowledge_id == case.knowledge_id)
    repo = make_repo(tmp_path)
    revision_id = _revision_id_avoiding(case.path, case.candidate)
    session_id, _ = _seed_confirmed(
        repo, intent_with(case.positive, delegated={case.path}), revision_id
    )
    provider = FakeImageProvider()
    pipeline = _pipeline(tmp_path, repo, _release_engine(repo, corpus), provider)

    pipeline.generate(session_id)

    # 采用过程无额外 Provider 调用（知识检索是本地 JSONL + 内存词法）。
    assert len(provider.requests) == 1

    # 实际 Prompt 使用了知识候选值（不是确定性回退）。
    prompt_artifact = _latest_prompt(repo, session_id)
    assert f"{RENDER_LABELS[case.path]}: {case.candidate}" in prompt_artifact.prompt
    assert unit.source.repository_path  # 来源完整，随单元一起进入快照

    # 实际 Realization 记录了完整知识追溯三元组。
    value = _active_value(repo, session_id, case.path)
    assert value.value == case.candidate
    assert value.knowledge_unit_id == case.knowledge_id
    assert value.knowledge_unit_version == "2"
    assert value.knowledge_bundle_id is not None
    assert value.knowledge_bundle_id in prompt_artifact.knowledge_bundle_refs

    # Bundle 从数据库读回：语料版本/指纹、命中单元、adopted 裁定、适用性快照。
    stored = repo.list_knowledge_bundles(session_id)
    assert len(stored) == 1
    bundle = KnowledgeBundle.model_validate_json(stored[0].payload)
    assert bundle.bundle_id == value.knowledge_bundle_id
    assert bundle.status is BundleStatus.OK
    assert bundle.corpus_version == CORPUS_VERSION
    assert bundle.schema_version == "knowledge.v1"
    assert bundle.tokenizer_version == "keyword.v1"
    assert bundle.retrieval_version == "lexical.v1"
    assert bundle.corpus_fingerprint == compute_corpus_fingerprint(corpus.manifest)
    assert bundle.target_model == MODEL

    result = bundle.result_for(case.path)
    assert result is not None and result.outcome is RetrievalOutcome.ADOPTED
    hit = result.hits[0]
    assert hit.knowledge_id == case.knowledge_id
    assert hit.version == unit.version
    assert hit.content_hash == unit.content_hash
    assert hit.content == unit.content
    snapshot = hit.eligibility_snapshot
    assert snapshot is not None
    assert snapshot.snapshot_version == ELIGIBILITY_SNAPSHOT_VERSION
    assert snapshot.conditions == unit.conditions
    assert snapshot.target_models == unit.target_models
    assert snapshot.review_status is ReviewStatus.APPROVED
    assert snapshot.reviewer == "change"
    assert snapshot.reviewed_at is not None

    recommendation = bundle.recommendation_for(case.path)
    assert recommendation is not None and recommendation.knowledge_id == case.knowledge_id
    decision = bundle.decision_for(case.path)
    assert decision is not None and decision.outcome is AdoptionOutcome.ADOPTED
    assert decision.reason_code is None


# ---------------------------------------------------------------------------
# 反例：条件不符 ⇒ 明确回退确定性基线，不采用
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", RULE_CASES, ids=[case.knowledge_id for case in RULE_CASES])
def test_conditions_that_do_not_hold_fall_back_to_the_deterministic_baseline(
    tmp_path, case: RuleCase
):
    corpus = release_corpus()
    repo = make_repo(tmp_path)
    revision_id = _revision_id_avoiding(case.path, case.candidate)
    fallback = select_delegated_value(case.path, revision_id)
    session_id, _ = _seed_confirmed(
        repo, intent_with(case.negative, delegated={case.path}), revision_id
    )
    pipeline = _pipeline(tmp_path, repo, _release_engine(repo, corpus), FakeImageProvider())

    pipeline.generate(session_id)

    value = _active_value(repo, session_id, case.path)
    assert value.value == fallback
    assert value.value != case.candidate
    assert value.knowledge_bundle_id is None
    assert value.knowledge_unit_id is None
    assert value.knowledge_unit_version is None

    stored = repo.list_knowledge_bundles(session_id)
    assert len(stored) == 1
    bundle = KnowledgeBundle.model_validate_json(stored[0].payload)
    assert bundle.status is BundleStatus.OK  # 语料可用，只是该单元条件不符
    assert bundle.recommendation_for(case.path) is None
    assert bundle.decision_for(case.path) is None
    result = bundle.result_for(case.path)
    assert result is not None and result.outcome is RetrievalOutcome.NO_HIT
    rejected = {item.knowledge_id: item.reason_code for item in result.rejections}
    assert case.knowledge_id in rejected
    assert rejected[case.knowledge_id].value == "conditions_not_satisfied"


# ---------------------------------------------------------------------------
# 保护边界：PIN 的委托路径不被检索、不被覆盖
# ---------------------------------------------------------------------------


def test_pinned_delegated_path_is_never_retrieved_or_overridden(tmp_path):
    corpus = release_corpus()
    repo = make_repo(tmp_path)
    revision_id = _revision_id_avoiding(LIGHTING, "soft")
    fallback = select_delegated_value(LIGHTING, revision_id)
    intent = intent_with(
        {"composition.framing": "close_up", "environment.mode": "studio", "subject.description": "a cat"},
        delegated={LIGHTING},
        pinned={LIGHTING},
    )
    session_id, _ = _seed_confirmed(repo, intent, revision_id)
    knowledge = RecordingEngine(LocalKnowledgeEngine(corpus=corpus))
    engine = PromptEngine(QwenImageRenderer(), repo, knowledge_engine=knowledge)
    pipeline = _pipeline(tmp_path, repo, engine, FakeImageProvider())

    pipeline.generate(session_id)

    # 被 PIN 的委托路径根本不进入检索，也不产生 Bundle。
    assert knowledge.requests == []
    assert repo.list_knowledge_bundles(session_id) == []
    value = _active_value(repo, session_id, LIGHTING)
    assert value.value == fallback
    assert value.knowledge_bundle_id is None


# ---------------------------------------------------------------------------
# 与关闭 RAG 的确定性基线差异（差异只在授权范围，不宣称更优）
# ---------------------------------------------------------------------------


def test_rag_selection_differs_from_disabled_rag_baseline_only_in_authorized_path(tmp_path):
    corpus = release_corpus()
    values = {
        "composition.framing": "close_up",
        "environment.mode": "studio",
        "subject.description": "a cat",
    }
    revision_id = _revision_id_avoiding(LIGHTING, "soft")
    fallback = select_delegated_value(LIGHTING, revision_id)
    assert fallback != "soft"

    rag_repo = make_repo(tmp_path, "rag.db")
    rag_session, _ = _seed_confirmed(
        rag_repo, intent_with(values, delegated={LIGHTING}), revision_id
    )
    _pipeline(tmp_path, rag_repo, _release_engine(rag_repo, corpus), FakeImageProvider(), "rag_out").generate(
        rag_session
    )
    rag_prompt = _latest_prompt(rag_repo, rag_session).prompt

    # 同一 revision / 同一确认上下文，但关闭 RAG（不注入 knowledge_engine）。
    base_repo = make_repo(tmp_path, "baseline.db")
    base_session, base_confirmation = _seed_confirmed(
        base_repo, intent_with(values, delegated={LIGHTING}), revision_id
    )
    base_engine = PromptEngine(QwenImageRenderer(), base_repo)
    _pipeline(tmp_path, base_repo, base_engine, FakeImageProvider(), "base_out").generate(base_session)
    base_prompt = _latest_prompt(base_repo, base_session).prompt

    assert "lighting: soft" in rag_prompt
    assert f"lighting: {fallback}" in base_prompt
    assert rag_prompt != base_prompt

    # 差异只允许出现在被授权路径：其余 clause 完全一致。
    rag_other = sorted(c for c in rag_prompt.split(", ") if not c.startswith("lighting:"))
    base_other = sorted(c for c in base_prompt.split(", ") if not c.startswith("lighting:"))
    assert rag_other == base_other

    # 关闭 RAG 的会话没有 Bundle / 知识追溯；RAG 会话确实有。
    assert base_repo.list_knowledge_bundles(base_session) == []
    assert base_repo.get_current_realization_state(base_session) is not None
    assert base_confirmation is not None
    assert len(rag_repo.list_knowledge_bundles(rag_session)) == 1


# ---------------------------------------------------------------------------
# active 复用 / retry 不重新检索 / 快照切换不改写历史 / 旧 Bundle 兼容
# ---------------------------------------------------------------------------


def test_active_realization_reuse_and_snapshot_switch_do_not_rewrite_history(tmp_path):
    corpus = release_corpus()
    repo = make_repo(tmp_path)
    revision_id = _revision_id_avoiding(LIGHTING, "soft")
    intent = intent_with(
        {"composition.framing": "close_up", "environment.mode": "studio", "subject.description": "a cat"},
        delegated={LIGHTING},
    )
    session_id, _ = _seed_confirmed(repo, intent, revision_id)
    knowledge = RecordingEngine(LocalKnowledgeEngine(corpus=corpus))
    provider = FakeImageProvider()
    pipeline = _pipeline(
        tmp_path, repo, PromptEngine(QwenImageRenderer(), repo, knowledge_engine=knowledge), provider
    )

    pipeline.generate(session_id)
    assert len(knowledge.requests) == 1
    assert artifact_count(repo, "knowledge_bundles") == 1
    first_bundle = repo.list_knowledge_bundles(session_id)[0]
    first_value = _active_value(repo, session_id, LIGHTING)
    assert first_value.knowledge_unit_id == "lighting.character.soft_for_tight_framing"

    # 已有 active Realization：再次 compile/generate 直接复用，绝不重新检索。
    return_to_confirmation(repo, session_id)
    pipeline.generate(session_id)
    assert len(knowledge.requests) == 1
    assert artifact_count(repo, "knowledge_bundles") == 1
    reused_value = _active_value(repo, session_id, LIGHTING)
    assert reused_value == first_value
    latest = _latest_prompt(repo, session_id)
    assert "lighting: soft" in latest.prompt
    assert first_bundle.artifact_id in latest.knowledge_bundle_refs

    # 知识快照切换（换成 build_units() 为空的 v0.4）：旧生成依据与历史 Bundle 不被改写。
    switched_engine = PromptEngine(
        QwenImageRenderer(), repo, knowledge_engine=LocalKnowledgeEngine(corpus_dir=str(LEGACY_DIR))
    )
    switched = _pipeline(tmp_path, repo, switched_engine, FakeImageProvider(), "switched_out")
    return_to_confirmation(repo, session_id)
    switched.generate(session_id)
    assert len(knowledge.requests) == 1
    assert artifact_count(repo, "knowledge_bundles") == 1
    after = repo.get_knowledge_bundle(first_bundle.artifact_id)
    assert after.payload == first_bundle.payload
    assert _active_value(repo, session_id, LIGHTING) == first_value
    assert "lighting: soft" in _latest_prompt(repo, session_id).prompt


def test_legacy_bundle_without_snapshot_is_readable_but_never_silently_adopted(tmp_path):
    corpus = release_corpus()
    repo = make_repo(tmp_path)
    revision_id = _revision_id_avoiding(LIGHTING, "soft")
    intent = intent_with(
        {"composition.framing": "close_up", "environment.mode": "studio", "subject.description": "a cat"},
        delegated={LIGHTING},
    )
    session_id, _ = _seed_confirmed(repo, intent, revision_id)
    pipeline = _pipeline(tmp_path, repo, _release_engine(repo, corpus), FakeImageProvider())
    pipeline.generate(session_id)
    current = KnowledgeBundle.model_validate_json(
        repo.list_knowledge_bundles(session_id)[0].payload
    )

    # 模拟旧记录：去掉消费端裁定与适用性快照（兼容扩展之前的 payload）。
    legacy_payload = json.loads(current.model_dump_json())
    legacy_payload.pop("adoption_decisions", None)
    for result in legacy_payload["path_results"]:
        for hit in result.get("hits", []):
            hit.pop("eligibility_snapshot", None)
    legacy = KnowledgeBundle.model_validate_json(json.dumps(legacy_payload))

    assert legacy.adoption_decisions == ()
    assert legacy.result_for(LIGHTING).hits[0].eligibility_snapshot is None
    recommendation = legacy.recommendation_for(LIGHTING)
    assert recommendation is not None
    # 缺条件证据的旧记录不得被当作无限适用：消费端明确拒绝。
    verdict = evaluate_recommendation(
        bundle=legacy,
        recommendation=recommendation,
        path=LIGHTING,
        intent=intent,
        target_model=MODEL,
    )
    assert not verdict.accepted
    assert verdict.reason_code is AdoptionRejectionReason.MISSING_ELIGIBILITY_SNAPSHOT


def test_retry_reuses_the_original_prompt_without_new_release_retrieval(tmp_path):
    corpus = release_corpus()
    repo = make_repo(tmp_path)
    revision_id = _revision_id_avoiding(LIGHTING, "soft")
    intent = intent_with(
        {"composition.framing": "close_up", "environment.mode": "studio", "subject.description": "a cat"},
        delegated={LIGHTING},
    )
    session_id, _ = _seed_confirmed(repo, intent, revision_id)
    knowledge = RecordingEngine(LocalKnowledgeEngine(corpus=corpus))
    engine = PromptEngine(QwenImageRenderer(), repo, knowledge_engine=knowledge)

    calls = {"n": 0}

    def handler(request):  # noqa: ANN001 - 测试内联回调
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError.server("simulated first-attempt outage")
        return ImageGenerationResult(
            model="fake-image",
            provider_request_id="req_v0_5_retry",
            images=[GeneratedImage(content=FAKE_PNG_BYTES, mime_type="image/png")],
        )

    provider = FakeImageProvider(handler)
    pipeline = _pipeline(tmp_path, repo, engine, provider)

    with pytest.raises(ProviderError):
        pipeline.generate(session_id)
    assert len(knowledge.requests) == 1
    assert artifact_count(repo, "knowledge_bundles") == 1
    original = _latest_prompt(repo, session_id)
    assert "lighting: soft" in original.prompt

    retried = pipeline.retry(session_id)

    assert len(knowledge.requests) == 1
    assert artifact_count(repo, "knowledge_bundles") == 1
    assert retried.prompt_artifact_id == original.prompt_artifact_id
    assert provider.requests[-1].prompt == original.prompt


# ---------------------------------------------------------------------------
# Step 04：真实 P1 工作流端到端闭环
# FakeLLMProvider → Interpreter / IntentEngine → 确认门禁 → v0.5 LocalKnowledgeEngine
# → FakeImageProvider → 临时真实 SQLite；断言 C1（soft）实际采用且 Bundle/适用性可追溯。
# ---------------------------------------------------------------------------


def test_p1_workflow_closes_the_loop_with_release_corpus_c1_adopted(tmp_path):
    """复用 `run_p1_to_confirmation` 的**真实** P1 工作流，验证 C1 被实际采用并可追溯。

    本文件其余用例直接向临时库写入已确认会话（精确控制 revision 与上下文），只覆盖
    "已确认 → 采用"半程；本用例补的是从用户消息开始的完整闭环：Intent 由
    `FakeLLMProvider` → `Interpreter`/`IntentEngine` 生成、经确认门禁确认后才检索
    `knowledge_base/v0.5/`，并在真实 SQLite 中留下可读回的 Bundle 与适用性快照。

    `run_p1_to_confirmation` 的 Ready 意图含 `composition.framing=medium_shot`、
    `environment.mode=studio` 且委托 `lighting.character`，正好满足 C1
    （`lighting.character.soft_for_tight_framing`）的条件。
    """
    run = run_p1_to_confirmation(tmp_path, name="v05_loop.db")
    repo = run.repo
    session_id = run.session_id
    corpus = release_corpus()
    provider = FakeImageProvider()
    pipeline = _pipeline(tmp_path, repo, _release_engine(repo, corpus), provider)

    pipeline.generate(session_id)

    # 一次生成只请求一次图片 Provider（知识检索是本地 JSONL + 内存词法，无额外 LLM/网络）。
    assert len(provider.requests) == 1
    assert (
        repo.get_current_session_snapshot(session_id).workflow_state
        is WorkflowState.WAITING_REVIEW
    )

    # 产物绑定的是这条**真实**确认；Prompt 确实采用 C1 候选值 soft。
    prompt_artifact = _latest_prompt(repo, session_id)
    assert prompt_artifact.based_on_confirmation_id == run.confirmation.confirmation_id
    assert prompt_artifact.based_on_intent_revision_id == run.confirmation.intent_revision_id
    assert "lighting: soft" in prompt_artifact.prompt

    value = _active_value(repo, session_id, LIGHTING)
    assert value.value == "soft"
    # 确定性回退从不写知识三元组；这里非空即证明这是知识采用（即便回退值恰好也是 soft）。
    assert value.knowledge_unit_id == "lighting.character.soft_for_tight_framing"
    assert value.knowledge_unit_version == "2"
    assert value.knowledge_bundle_id is not None

    # Bundle 可从真实 SQLite 读回，并携带语料版本/指纹与采用裁定。
    stored = repo.list_knowledge_bundles(session_id)
    assert len(stored) == 1
    bundle = KnowledgeBundle.model_validate_json(stored[0].payload)
    assert bundle.bundle_id == value.knowledge_bundle_id
    assert bundle.bundle_id in prompt_artifact.knowledge_bundle_refs
    assert bundle.status is BundleStatus.OK
    assert bundle.corpus_version == CORPUS_VERSION
    assert bundle.corpus_fingerprint == compute_corpus_fingerprint(corpus.manifest)
    assert bundle.target_model == MODEL

    result = bundle.result_for(LIGHTING)
    assert result is not None and result.outcome is RetrievalOutcome.ADOPTED
    hit = result.hits[0]
    assert hit.knowledge_id == "lighting.character.soft_for_tight_framing"
    assert hit.content_hash == compute_content_hash(hit.content)

    # 适用性快照必须可追溯到真实人工审核，而不是测试夹具。
    snapshot = hit.eligibility_snapshot
    assert snapshot is not None
    assert snapshot.snapshot_version == ELIGIBILITY_SNAPSHOT_VERSION
    assert snapshot.conditions == next(
        unit.conditions
        for unit in corpus.build_units()
        if unit.knowledge_id == "lighting.character.soft_for_tight_framing"
    )
    assert snapshot.review_status is ReviewStatus.APPROVED
    assert snapshot.reviewer == "change"
    assert snapshot.reviewed_at == RELEASE_REVIEWED_AT

    decision = bundle.decision_for(LIGHTING)
    assert decision is not None and decision.outcome is AdoptionOutcome.ADOPTED
    assert decision.reason_code is None
