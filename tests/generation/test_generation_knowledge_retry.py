"""v0.4 Step 03 最小 Fake E2E：GenerationPipeline retry 复用原 Prompt 且零新检索。

只读复用现有 helpers；不改业务代码。证明 P2 重试路径不重新 `compile`，因此知识引擎
不会在重试时再次被调用（"知识库变更不能影响已有 Prompt 的 retry"）。
"""

from __future__ import annotations

import pytest

from generation_helpers import (
    artifact_count,
    intent_with,
    make_pipeline,
    make_repo,
    seed_confirmed_session,
)
from tests.knowledge.knowledge_helpers import approved_payload, load_test_corpus

from visual_intent_agent.knowledge import LocalKnowledgeEngine
from visual_intent_agent.prompt_engine import PromptArtifact, PromptEngine, QwenImageRenderer
from visual_intent_agent.prompt_engine.engine import (
    DELEGATED_CANDIDATES,
    select_delegated_value,
)
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_image import (
    FAKE_PNG_BYTES,
    FakeImageProvider,
)
from visual_intent_agent.providers.image import GeneratedImage, ImageGenerationResult

LIGHTING = "lighting.character"


class RecordingEngine:
    """记录检索请求的包装器（断言 retry 不增加调用数）。"""

    def __init__(self, inner):
        self.inner = inner
        self.requests = []

    def retrieve(self, request):
        self.requests.append(request)
        return self.inner.retrieve(request)


def _pipeline(tmp_path, repo, engine, provider):
    return make_pipeline(
        repo, output_dir=tmp_path / "out", provider=provider, engine=engine
    )


def test_retry_reuses_the_original_prompt_without_extra_knowledge_retrieval(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    )
    # 让知识建议值与确定性回退不同，确保 generate 阶段确实检索并被采纳。
    fallback = select_delegated_value(LIGHTING, seeded.intent_revision_id)
    chosen = next(value for value in DELEGATED_CANDIDATES[LIGHTING] if value != fallback)
    corpus = load_test_corpus(
        tmp_path,
        [
            approved_payload(
                applicable_path=LIGHTING,
                candidate_value=chosen,
                keywords=("cat",),
                aliases=(),
            )
        ],
        file_name="units.jsonl",
    )
    knowledge = RecordingEngine(LocalKnowledgeEngine(corpus=corpus))
    engine = PromptEngine(QwenImageRenderer(), repo, knowledge_engine=knowledge)

    calls = {"n": 0}

    def handler(request):  # noqa: ANN001 - 测试内联回调
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError.server("simulated first-attempt outage")
        return ImageGenerationResult(
            model="fake-image",
            provider_request_id="req_retry_ok",
            images=[GeneratedImage(content=FAKE_PNG_BYTES, mime_type="image/png")],
        )

    provider = FakeImageProvider(handler)
    pipeline = _pipeline(tmp_path, repo, engine, provider)

    # 首次 generate：compile（检索 1 次）→ Provider 失败 → FAILED，不写 GenerationArtifact。
    with pytest.raises(ProviderError):
        pipeline.generate(seeded.session_id)
    assert len(knowledge.requests) == 1
    assert calls["n"] == 1
    assert artifact_count(repo, "generation_artifacts") == 0
    assert artifact_count(repo, "prompt_artifacts") == 1
    original = PromptArtifact.model_validate_json(
        repo.get_latest_prompt_artifact(seeded.session_id).payload
    )
    assert chosen in original.prompt

    # retry：复用同一 PromptArtifact，绝不重新 compile ⇒ 知识引擎零新检索。
    retried = pipeline.retry(seeded.session_id)

    assert len(knowledge.requests) == 1
    assert artifact_count(repo, "prompt_artifacts") == 1
    assert retried.prompt_artifact_id == original.prompt_artifact_id
    assert provider.requests[-1].prompt == original.prompt
    assert artifact_count(repo, "knowledge_bundles") == 1