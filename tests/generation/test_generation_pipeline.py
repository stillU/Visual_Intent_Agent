"""GenerationPipeline 集成测试（Step 08）：门禁、成功落库、失败、retry、历史、外键。

默认全离线：`FakeImageProvider`（确定性 PNG 常量）+ `tmp_path` 输出目录 + `tmp_path` SQLite。
"""

from __future__ import annotations

import logging

import pytest

from visual_intent_agent.domain import new_id
from visual_intent_agent.generation import (
    GENERATION_ARTIFACT_NOT_FOUND,
    GENERATION_INVALID_STATE,
    GENERATION_NO_VALID_CONFIRMATION,
    GENERATION_SESSION_MISMATCH,
    GenerationArtifact,
    GenerationError,
)
from visual_intent_agent.persistence import (
    InvalidStateTransitionError,
    RepositoryError,
    WorkflowState,
)
from visual_intent_agent.prompt_engine import PromptArtifact, PromptCompilationError
from visual_intent_agent.providers.errors import (
    PROVIDER_SERVER_ERROR,
    ProviderError,
)
from visual_intent_agent.providers.fake_image import FAKE_PNG_BYTES, FakeImageProvider
from visual_intent_agent.providers.image import GeneratedImage, ImageGenerationResult

from generation_helpers import (
    READY_INTENT_VALUES,
    append_intent,
    artifact_count,
    fail_once_on_review_transition,
    intent_with,
    make_pipeline,
    make_repo,
    output_file,
    ready_intent,
    return_to_confirmation,
    seed_confirmed_session,
)

PIPELINE_LOGGER = "visual_intent_agent.generation.pipeline"


def _seed(repo, **kwargs):
    return seed_confirmed_session(repo, ready_intent(), **kwargs)


def _failing_provider(*, message: str = "upstream exploded"):
    def handler(request):  # noqa: ANN001 - 测试内联回调
        raise ProviderError.server(message)

    provider = FakeImageProvider(handler)
    return provider


def _state(repo, session_id: str) -> WorkflowState:
    return repo.get_current_session_snapshot(session_id).workflow_state


def _original_artifact(repo, session_id: str) -> tuple[GenerationArtifact, str]:
    stored = repo.list_generation_artifacts(session_id)[0]
    return GenerationArtifact.model_validate_json(stored.payload), stored.payload


# ---------------------------------------------------------------------------
# 必测场景 1：未确认时无法调用 Provider
# ---------------------------------------------------------------------------


def test_generate_requires_waiting_confirmation_state(tmp_path) -> None:
    repo = make_repo(tmp_path)
    session_id = new_id("ses")
    repo.create_session(session_id)  # UNDERSTANDING
    provider = FakeImageProvider()
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)

    with pytest.raises(GenerationError) as excinfo:
        pipeline.generate(session_id)

    assert excinfo.value.code == GENERATION_INVALID_STATE
    assert provider.requests == []
    assert artifact_count(repo, "generation_artifacts") == 0
    assert _state(repo, session_id) is WorkflowState.UNDERSTANDING


def test_generate_without_any_confirmation_is_rejected_before_any_provider_call(tmp_path) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo, confirmed=False)
    provider = FakeImageProvider()
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)

    with pytest.raises(GenerationError) as excinfo:
        pipeline.generate(seeded.session_id)

    assert excinfo.value.code == GENERATION_NO_VALID_CONFIRMATION
    assert provider.requests == []
    assert artifact_count(repo, "prompt_artifacts") == 0
    assert artifact_count(repo, "generation_artifacts") == 0
    assert _state(repo, seeded.session_id) is WorkflowState.WAITING_CONFIRMATION


# ---------------------------------------------------------------------------
# 必测场景 2：PromptArtifact 与当前 Confirmation 不匹配时拒绝生成
# ---------------------------------------------------------------------------


def test_generate_rejects_a_confirmation_invalidated_by_a_new_revision(tmp_path) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    provider = FakeImageProvider()
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)

    append_intent(
        repo,
        seeded.session_id,
        intent_with({**READY_INTENT_VALUES, "subject.description": "a dog"}),
    )
    assert repo.is_confirmation_valid(seeded.confirmation_id) is False

    with pytest.raises(GenerationError) as excinfo:
        pipeline.generate(seeded.session_id)

    assert excinfo.value.code == GENERATION_NO_VALID_CONFIRMATION
    assert provider.requests == []
    assert artifact_count(repo, "generation_artifacts") == 0
    assert _state(repo, seeded.session_id) is WorkflowState.WAITING_CONFIRMATION


# ---------------------------------------------------------------------------
# 必测场景 3：成功调用后 Artifact 完整且状态为 WAITING_REVIEW
# ---------------------------------------------------------------------------


def test_successful_generation_writes_bytes_and_saves_a_complete_artifact(tmp_path) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    output_dir = tmp_path / "out"
    provider = FakeImageProvider()
    pipeline = make_pipeline(repo, output_dir=output_dir, provider=provider)

    artifact = pipeline.generate(seeded.session_id)

    assert _state(repo, seeded.session_id) is WorkflowState.WAITING_REVIEW
    assert artifact.generation_id.startswith("gen_")
    assert artifact.session_id == seeded.session_id
    assert artifact.target_model == "qwen-image-3.0"
    assert artifact.model_version == "fake-image"
    assert artifact.parameters.size == "1024x1024"
    assert artifact.seed is None  # Provider 不返回 seed，禁止伪造
    assert artifact.provider_request_id == "fake-image-request-0001"
    assert len(artifact.output_refs) == 1
    ref = artifact.output_refs[0]
    assert ref.mime_type == "image/png"
    assert ref.byte_size == len(FAKE_PNG_BYTES)
    assert ref.path.endswith(f"{artifact.generation_id}/image_1.png")

    written = output_file(ref.path)
    assert written.is_file()
    assert written.read_bytes() == FAKE_PNG_BYTES
    assert written.parent == output_dir / artifact.generation_id

    stored = repo.get_generation_artifact(artifact.generation_id)
    assert stored.refs == {"prompt_artifact_id": artifact.prompt_artifact_id}
    assert stored.session_id == seeded.session_id
    assert GenerationArtifact.model_validate_json(stored.payload) == artifact
    assert len(repo.list_generation_artifacts(seeded.session_id)) == 1

    # 不存临时 URL / seed 伪造。
    assert "http" not in stored.payload
    assert "Expires" not in stored.payload
    assert "Signature" not in stored.payload


def test_provider_request_is_built_only_from_the_prompt_artifact(tmp_path) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    provider = FakeImageProvider()
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)

    artifact = pipeline.generate(seeded.session_id)
    prompt_artifact = PromptArtifact.model_validate_json(
        repo.get_prompt_artifact(artifact.prompt_artifact_id).payload
    )

    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.prompt == prompt_artifact.prompt
    assert request.size == prompt_artifact.parameters.size
    assert request.model == prompt_artifact.target_model


def test_multiple_returned_images_are_written_in_order(tmp_path) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    result = ImageGenerationResult(
        model="qwen-image-3.0",
        provider_request_id="req_multi",
        images=[
            GeneratedImage(content=b"first-image", mime_type="image/png"),
            GeneratedImage(content=b"second-image", mime_type="image/png"),
        ],
        seed=11,
    )
    provider = FakeImageProvider([result])
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)

    artifact = pipeline.generate(seeded.session_id)

    assert len(artifact.output_refs) == 2
    assert artifact.seed == 11
    for index, ref in enumerate(artifact.output_refs, start=1):
        assert ref.path.endswith(f"image_{index}.png")
        assert output_file(ref.path).read_bytes() in {b"first-image", b"second-image"}
    assert output_file(artifact.output_refs[0].path).read_bytes() == b"first-image"
    assert output_file(artifact.output_refs[1].path).read_bytes() == b"second-image"


# ---------------------------------------------------------------------------
# 必测场景 4：Provider 失败后状态为 FAILED，没有伪造 Artifact
# ---------------------------------------------------------------------------


def test_provider_failure_transitions_to_failed_without_an_artifact(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    pipeline = make_pipeline(
        repo, output_dir=tmp_path / "out", provider=_failing_provider()
    )

    with caplog.at_level(logging.ERROR, logger=PIPELINE_LOGGER):
        with pytest.raises(ProviderError) as excinfo:
            pipeline.generate(seeded.session_id)

    assert excinfo.value.code == PROVIDER_SERVER_ERROR
    assert _state(repo, seeded.session_id) is WorkflowState.FAILED
    # 没有伪造 Artifact：失败不写 generation_artifacts，也不落任何图片。
    assert artifact_count(repo, "generation_artifacts") == 0
    assert not (tmp_path / "out").exists()
    # 结构化日志可观察（只含 ID / 分类 / 状态码）。
    records = [record for record in caplog.records if record.name == PIPELINE_LOGGER]
    assert records
    failure = records[-1]
    assert failure.event == "generation.failed"
    assert failure.session_id == seeded.session_id
    assert failure.reason_code == PROVIDER_SERVER_ERROR
    assert failure.retryable is True


def test_prompt_compile_failure_transitions_to_failed_without_an_artifact(tmp_path) -> None:
    repo = make_repo(tmp_path)
    # ExecutionRevision 指向不被唯一 renderer 支持的模型 → compile 抛 unsupported_requirement。
    seeded = _seed(repo, target_model="some-other-image-model")
    provider = FakeImageProvider()
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)

    with pytest.raises(PromptCompilationError):
        pipeline.generate(seeded.session_id)

    assert _state(repo, seeded.session_id) is WorkflowState.FAILED
    assert provider.requests == []
    assert artifact_count(repo, "generation_artifacts") == 0


# ---------------------------------------------------------------------------
# 必测场景 5：Retry 使用同一 PromptArtifact
# ---------------------------------------------------------------------------


def test_retry_requires_failed_state(tmp_path) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out")
    artifact = pipeline.generate(seeded.session_id)  # WAITING_REVIEW

    with pytest.raises(GenerationError) as excinfo:
        pipeline.retry(seeded.session_id, artifact.generation_id)

    assert excinfo.value.code == GENERATION_INVALID_STATE


def test_retry_without_any_prompt_artifact_is_rejected_with_a_clear_code(tmp_path) -> None:
    # 会话从未编译过 Prompt（无任何 PromptArtifact）→ 回退也无原 Prompt 可复用。
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out")
    repo.transition_state(seeded.session_id, WorkflowState.GENERATING)
    repo.transition_state(seeded.session_id, WorkflowState.FAILED)
    assert artifact_count(repo, "prompt_artifacts") == 0

    with pytest.raises(GenerationError) as excinfo:
        pipeline.retry(seeded.session_id, "gen_does_not_exist")

    assert excinfo.value.code == GENERATION_ARTIFACT_NOT_FOUND
    assert _state(repo, seeded.session_id) is WorkflowState.FAILED


def test_retry_after_a_pure_provider_failure_reuses_the_latest_prompt_artifact(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """必测：纯 Provider 失败（未落 Artifact）后 retry 成功且复用同一 PromptArtifact。

    `generation_id` 取自失败日志事件 `generation.failed`（失败路径不写 Artifact，
    调用方只能从结构化日志拿到该 id）。断言 Provider 入参逐字段相同、PromptArtifact
    行数不变（不重新 compile）、产生新 generation_id、FAILED → GENERATING → WAITING_REVIEW。
    """
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
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
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)

    with caplog.at_level(logging.ERROR, logger=PIPELINE_LOGGER):
        with pytest.raises(ProviderError):
            pipeline.generate(seeded.session_id)

    assert _state(repo, seeded.session_id) is WorkflowState.FAILED
    assert artifact_count(repo, "generation_artifacts") == 0
    # 失败现场：该次 generate 已编译落库的 PromptArtifact 就是 retry 要复用的原 Prompt。
    assert artifact_count(repo, "prompt_artifacts") == 1
    failed_generation_id = caplog.records[-1].generation_id
    expected_prompt = PromptArtifact.model_validate_json(
        repo.get_latest_prompt_artifact(seeded.session_id).payload
    )
    assert caplog.records[-1].prompt_artifact_id == expected_prompt.prompt_artifact_id

    prompt_artifacts_before = artifact_count(repo, "prompt_artifacts")
    retried = pipeline.retry(seeded.session_id, failed_generation_id)

    assert _state(repo, seeded.session_id) is WorkflowState.WAITING_REVIEW
    assert retried.generation_id != failed_generation_id
    assert retried.prompt_artifact_id == expected_prompt.prompt_artifact_id
    assert retried.target_model == expected_prompt.target_model
    assert retried.parameters == expected_prompt.parameters
    # 不重新 compile：PromptArtifact 恰好仍是失败前那一条，Provider 入参逐字段相同。
    assert artifact_count(repo, "prompt_artifacts") == prompt_artifacts_before == 1
    assert len(provider.requests) == 2
    assert provider.requests[-1].prompt == expected_prompt.prompt
    assert provider.requests[-1].size == expected_prompt.parameters.size
    assert provider.requests[-1].model == expected_prompt.target_model
    assert provider.requests[-1] == provider.requests[0]
    # 新 Artifact 可读回，历史不覆盖（失败不写 Artifact，重试成功后恰好 1 条）。
    stored = repo.get_generation_artifact(retried.generation_id)
    assert stored.refs == {"prompt_artifact_id": expected_prompt.prompt_artifact_id}
    assert GenerationArtifact.model_validate_json(stored.payload) == retried
    assert len(repo.list_generation_artifacts(seeded.session_id)) == 1
    assert output_file(retried.output_refs[0].path).read_bytes() == FAKE_PNG_BYTES


def test_retry_fallback_rejects_a_prompt_artifact_whose_confirmation_is_stale(tmp_path) -> None:
    """必测：回退的 PromptArtifact 确认已失效 → 拒绝 retry，状态不变、Provider 零调用。"""
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    provider = _failing_provider()
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)

    with pytest.raises(ProviderError):
        pipeline.generate(seeded.session_id)
    assert _state(repo, seeded.session_id) is WorkflowState.FAILED

    append_intent(repo, seeded.session_id, intent_with(READY_INTENT_VALUES))
    assert repo.is_confirmation_valid(seeded.confirmation_id) is False
    calls_before = len(provider.requests)

    with pytest.raises(GenerationError) as excinfo:
        pipeline.retry(seeded.session_id, "gen_from_the_failed_attempt")

    assert excinfo.value.code == GENERATION_NO_VALID_CONFIRMATION
    assert _state(repo, seeded.session_id) is WorkflowState.FAILED
    assert len(provider.requests) == calls_before  # 不调用 Provider
    assert artifact_count(repo, "generation_artifacts") == 0
    assert artifact_count(repo, "prompt_artifacts") == 1  # 不重新 compile


def test_retry_reuses_the_same_prompt_artifact_without_recompiling(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    provider = FakeImageProvider()
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)

    fail_once_on_review_transition(monkeypatch, repo)
    with pytest.raises(InvalidStateTransitionError):
        pipeline.generate(seeded.session_id)
    assert _state(repo, seeded.session_id) is WorkflowState.FAILED

    original, original_payload = _original_artifact(repo, seeded.session_id)
    prompt_artifacts_before = artifact_count(repo, "prompt_artifacts")
    provider_calls_before = len(provider.requests)

    retried = pipeline.retry(seeded.session_id, original.generation_id)

    assert _state(repo, seeded.session_id) is WorkflowState.WAITING_REVIEW
    assert retried.generation_id != original.generation_id
    assert retried.prompt_artifact_id == original.prompt_artifact_id
    # 不得重新 compile：PromptArtifact 数量不变，Provider 的输入逐字段相同。
    assert artifact_count(repo, "prompt_artifacts") == prompt_artifacts_before
    assert len(provider.requests) == provider_calls_before + 1
    assert provider.requests[-1].prompt == provider.requests[0].prompt
    assert provider.requests[-1].size == provider.requests[0].size
    assert provider.requests[-1].model == provider.requests[0].model
    # 历史不覆盖：旧 Artifact 逐字节保持。
    assert repo.get_generation_artifact(original.generation_id).payload == original_payload
    assert len(repo.list_generation_artifacts(seeded.session_id)) == 2


def test_retry_rejects_a_prompt_artifact_whose_confirmation_is_stale(tmp_path, monkeypatch) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    provider = FakeImageProvider()
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)

    fail_once_on_review_transition(monkeypatch, repo)
    with pytest.raises(InvalidStateTransitionError):
        pipeline.generate(seeded.session_id)
    original, _ = _original_artifact(repo, seeded.session_id)

    append_intent(repo, seeded.session_id, intent_with(READY_INTENT_VALUES))

    with pytest.raises(GenerationError) as excinfo:
        pipeline.retry(seeded.session_id, original.generation_id)

    assert excinfo.value.code == GENERATION_NO_VALID_CONFIRMATION
    assert _state(repo, seeded.session_id) is WorkflowState.FAILED
    assert len(provider.requests) == 1


def test_retry_rejects_an_artifact_from_another_session(tmp_path, monkeypatch) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    other = _seed(repo)
    provider = FakeImageProvider()
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)

    fail_once_on_review_transition(monkeypatch, repo)
    with pytest.raises(InvalidStateTransitionError):
        pipeline.generate(seeded.session_id)
    original, _ = _original_artifact(repo, seeded.session_id)

    repo.transition_state(other.session_id, WorkflowState.GENERATING)
    repo.transition_state(other.session_id, WorkflowState.FAILED)

    with pytest.raises(GenerationError) as excinfo:
        pipeline.retry(other.session_id, original.generation_id)

    assert excinfo.value.code == GENERATION_SESSION_MISMATCH


def test_retry_provider_failure_returns_to_failed_and_keeps_history(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    calls = {"n": 0}

    def handler(request):  # noqa: ANN001 - 测试内联回调
        calls["n"] += 1
        if calls["n"] == 1:
            return ImageGenerationResult(
                model="fake-image",
                images=[GeneratedImage(content=FAKE_PNG_BYTES, mime_type="image/png")],
            )
        raise ProviderError.rate_limited("simulated retry failure")

    provider = FakeImageProvider(handler)
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)
    # 第一次成功落盘，但让 WAITING_REVIEW 迁移失败一次 → FAILED + 原 Artifact。
    fail_once_on_review_transition(monkeypatch, repo)
    with pytest.raises(InvalidStateTransitionError):
        pipeline.generate(seeded.session_id)
    original, original_payload = _original_artifact(repo, seeded.session_id)

    with pytest.raises(ProviderError) as excinfo:
        pipeline.retry(seeded.session_id, original.generation_id)

    assert excinfo.value.code == "provider.rate_limited"
    assert _state(repo, seeded.session_id) is WorkflowState.FAILED
    assert artifact_count(repo, "generation_artifacts") == 1
    assert repo.get_generation_artifact(original.generation_id).payload == original_payload


# ---------------------------------------------------------------------------
# 必测场景 6：多次生成产生不同 generation ID，不覆盖历史
# ---------------------------------------------------------------------------


def test_multiple_generations_have_distinct_ids_and_preserve_history(tmp_path) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out")

    first = pipeline.generate(seeded.session_id)
    first_payload = repo.get_generation_artifact(first.generation_id).payload
    return_to_confirmation(repo, seeded.session_id)
    second = pipeline.generate(seeded.session_id)

    assert first.generation_id != second.generation_id
    assert first.prompt_artifact_id != second.prompt_artifact_id  # 每次生成独立编译
    stored = repo.list_generation_artifacts(seeded.session_id)
    assert [item.artifact_id for item in stored] == [first.generation_id, second.generation_id]
    assert repo.get_generation_artifact(first.generation_id).payload == first_payload
    assert output_file(first.output_refs[0].path).read_bytes() == FAKE_PNG_BYTES
    assert output_file(second.output_refs[0].path).read_bytes() == FAKE_PNG_BYTES


def test_generate_never_modifies_the_intent_or_existing_prompt_artifacts(tmp_path) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out")

    intent_before = repo.get_intent_revision(seeded.intent_revision_id)
    revisions_before = artifact_count(repo, "intent_revisions")

    first = pipeline.generate(seeded.session_id)
    first_prompt_payload = repo.get_prompt_artifact(first.prompt_artifact_id).payload
    return_to_confirmation(repo, seeded.session_id)
    second = pipeline.generate(seeded.session_id)

    intent_after = repo.get_intent_revision(seeded.intent_revision_id)
    assert intent_after.model_dump_json() == intent_before.model_dump_json()
    assert artifact_count(repo, "intent_revisions") == revisions_before
    assert repo.get_prompt_artifact(first.prompt_artifact_id).payload == first_prompt_payload
    assert second.prompt_artifact_id != first.prompt_artifact_id


# ---------------------------------------------------------------------------
# 必测场景 7：Feedback 外键可精确引用某个 GenerationArtifact
# ---------------------------------------------------------------------------


def test_generation_artifact_is_referenceable_by_a_feedback_foreign_key(tmp_path) -> None:
    repo = make_repo(tmp_path)
    seeded = _seed(repo)
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out")
    artifact = pipeline.generate(seeded.session_id)

    feedback_id = new_id("fbk")
    repo.append_feedback_result(
        feedback_id, seeded.session_id, {"generation_id": artifact.generation_id}, "{}"
    )
    assert artifact_count(repo, "feedback_results") == 1

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_feedback_result(
            new_id("fbk"), seeded.session_id, {"generation_id": "gen_missing"}, "{}"
        )
    assert excinfo.value.code == "persistence.foreign_key_violation"


def test_feedback_cannot_reference_another_sessions_generation(tmp_path) -> None:
    repo = make_repo(tmp_path)
    first = _seed(repo)
    second = _seed(repo)
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out")
    artifact = pipeline.generate(first.session_id)

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_feedback_result(
            new_id("fbk"), second.session_id, {"generation_id": artifact.generation_id}, "{}"
        )
    assert excinfo.value.code == "persistence.ref_session_mismatch"
