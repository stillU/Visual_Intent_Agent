"""P2 端到端（Step 08）：P1 剧本 → 确认 → compile → FakeImageProvider → WAITING_REVIEW → retry。

默认全离线（FakeLLMProvider + FakeImageProvider + tmp_path SQLite/输出目录）。
"""

from __future__ import annotations

import pytest

from visual_intent_agent.generation import (
    GenerationArtifact,
)
from visual_intent_agent.persistence import InvalidStateTransitionError, WorkflowState
from visual_intent_agent.prompt_engine import PromptArtifact
from visual_intent_agent.providers.errors import PROVIDER_SERVER_ERROR, ProviderError
from visual_intent_agent.providers.fake_image import FAKE_PNG_BYTES, FakeImageProvider
from visual_intent_agent.providers.image import GeneratedImage, ImageGenerationResult

from generation_helpers import (
    fail_once_on_review_transition,
    make_pipeline,
    output_file,
    return_to_confirmation,
    run_p1_to_confirmation,
)


def _state(repo, session_id: str) -> WorkflowState:
    return repo.get_current_session_snapshot(session_id).workflow_state


def test_p2_p1_script_reaches_waiting_review_with_a_traceable_artifact(tmp_path) -> None:
    run = run_p1_to_confirmation(tmp_path)
    pipeline = make_pipeline(run.repo, output_dir=tmp_path / "out")

    artifact = pipeline.generate(run.session_id)

    assert _state(run.repo, run.session_id) is WorkflowState.WAITING_REVIEW
    prompt_artifact = PromptArtifact.model_validate_json(
        run.repo.get_prompt_artifact(artifact.prompt_artifact_id).payload
    )
    # 完整可追溯：Generation → Prompt → Confirmation → Intent revision。
    assert prompt_artifact.based_on_confirmation_id == run.confirmation.confirmation_id
    assert prompt_artifact.based_on_intent_revision_id == run.confirmation.intent_revision_id
    assert artifact.target_model == "qwen-image-3.0"
    assert artifact.parameters == prompt_artifact.parameters
    assert output_file(artifact.output_refs[0].path).read_bytes() == FAKE_PNG_BYTES
    assert len(run.repo.list_generation_artifacts(run.session_id)) == 1


def test_p2_retry_reuses_the_original_prompt_artifact_and_keeps_history(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_p1_to_confirmation(tmp_path)
    provider = FakeImageProvider()
    pipeline = make_pipeline(run.repo, output_dir=tmp_path / "out", provider=provider)

    first = pipeline.generate(run.session_id)
    first_payload = run.repo.get_generation_artifact(first.generation_id).payload

    # 第二次生成：Artifact 落库但复核迁移失败一次 → FAILED + 原 Artifact。
    return_to_confirmation(run.repo, run.session_id)
    fail_once_on_review_transition(monkeypatch, run.repo)
    with pytest.raises(InvalidStateTransitionError):
        pipeline.generate(run.session_id)
    assert _state(run.repo, run.session_id) is WorkflowState.FAILED

    failed_stored = run.repo.list_generation_artifacts(run.session_id)[-1]
    failed_artifact = GenerationArtifact.model_validate_json(failed_stored.payload)

    retried = pipeline.retry(run.session_id, failed_artifact.generation_id)

    assert _state(run.repo, run.session_id) is WorkflowState.WAITING_REVIEW
    assert retried.generation_id != failed_artifact.generation_id
    assert retried.prompt_artifact_id == failed_artifact.prompt_artifact_id
    stored = run.repo.list_generation_artifacts(run.session_id)
    assert [item.artifact_id for item in stored] == [
        first.generation_id,
        failed_artifact.generation_id,
        retried.generation_id,
    ]
    # 历史不覆盖：第一次的 Artifact 与文件都保持原样。
    assert run.repo.get_generation_artifact(first.generation_id).payload == first_payload
    assert output_file(first.output_refs[0].path).read_bytes() == FAKE_PNG_BYTES


def test_p2_provider_failure_is_observable_and_writes_no_artifact(tmp_path) -> None:
    run = run_p1_to_confirmation(tmp_path)
    calls = {"n": 0}

    def handler(request):  # noqa: ANN001 - 测试内联回调
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError.server("simulated outage")
        return ImageGenerationResult(
            model="fake-image",
            provider_request_id="req_p2_retry",
            images=[GeneratedImage(content=FAKE_PNG_BYTES, mime_type="image/png")],
        )

    pipeline = make_pipeline(
        run.repo, output_dir=tmp_path / "out", provider=FakeImageProvider(handler)
    )

    with pytest.raises(ProviderError) as excinfo:
        pipeline.generate(run.session_id)

    assert excinfo.value.code == PROVIDER_SERVER_ERROR
    assert _state(run.repo, run.session_id) is WorkflowState.FAILED
    assert run.repo.list_generation_artifacts(run.session_id) == []
    # 纯 Provider 失败不写 Artifact，但该次 generate 编译落库的 PromptArtifact 就是原 Prompt
    # （Rev.2 裁定 1）：retry 回退复用它并成功，不重新 compile。
    latest_prompt = run.repo.get_latest_prompt_artifact(run.session_id)
    assert latest_prompt is not None
    prompt_artifacts_before = run.repo.connection.execute(
        "SELECT COUNT(*) FROM prompt_artifacts"
    ).fetchone()[0]

    retried = pipeline.retry(run.session_id, "gen_from_the_failed_attempt")

    assert _state(run.repo, run.session_id) is WorkflowState.WAITING_REVIEW
    assert retried.prompt_artifact_id == latest_prompt.artifact_id
    assert run.repo.connection.execute(
        "SELECT COUNT(*) FROM prompt_artifacts"
    ).fetchone()[0] == prompt_artifacts_before
    assert [item.artifact_id for item in run.repo.list_generation_artifacts(run.session_id)] == [
        retried.generation_id
    ]
