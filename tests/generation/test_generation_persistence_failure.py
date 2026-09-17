"""F1 定向回归：compile 边界内的持久化失败必须进入受控 FAILED 路径。

修补前证据（README §2）：`GenerationPipeline.generate` 只捕获
`PromptCompilationError`；`PromptEngine` 落库时抛出的 `RepositoryError` 会穿透
`generate`，会话停留在 GENERATING，且 CLI 无法给出正确的 retry 提示。

本文件只做离线故障注入：真实 `GenerationPipeline` + `PromptEngine`（含可选
`LocalKnowledgeEngine`）+ `FakeImageProvider` + `tmp_path` SQLite。逐项覆盖
README §2「必须新增的离线回归」1、3、4、6；Provider 失败重试与 RAG 关闭回归
沿用既有测试（`test_generation_knowledge_retry.py`、`test_prompt_engine_knowledge.py`）。

夹具说明：`tests/knowledge` 的 approved 单元只是测试夹具，不是生产审核证据。
"""

from __future__ import annotations

import logging

import pytest

from generation_helpers import (
    append_intent,
    artifact_count,
    intent_with,
    make_pipeline,
    make_repo,
    return_to_confirmation,
    save_confirmation_for_current,
    seed_confirmed_session,
)
from tests.knowledge.knowledge_helpers import approved_payload, load_test_corpus

from visual_intent_agent.generation import GENERATION_NO_VALID_CONFIRMATION, GenerationError
from visual_intent_agent.knowledge import LocalKnowledgeEngine
from visual_intent_agent.persistence import RepositoryError, WorkflowState
from visual_intent_agent.prompt_engine import PromptEngine, QwenImageRenderer
from visual_intent_agent.prompt_engine.engine import (
    DELEGATED_CANDIDATES,
    select_delegated_value,
)
from visual_intent_agent.providers.fake_image import FakeImageProvider

LIGHTING = "lighting.character"
PIPELINE_LOGGER = "visual_intent_agent.generation.pipeline"
DATABASE_ERROR = "persistence.database_error"


def _state(repo, session_id: str) -> WorkflowState:
    return repo.get_current_session_snapshot(session_id).workflow_state


def _corpus(tmp_path, intent_revision_id: str):
    """一个会把 `lighting.character` 建议改成非回退值的 approved 夹具语料。"""
    fallback = select_delegated_value(LIGHTING, intent_revision_id)
    chosen = next(value for value in DELEGATED_CANDIDATES[LIGHTING] if value != fallback)
    return load_test_corpus(
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


def _knowledge_pipeline(tmp_path, repo, seeded, provider=None):
    engine = PromptEngine(
        QwenImageRenderer(),
        repo,
        knowledge_engine=LocalKnowledgeEngine(corpus=_corpus(tmp_path, seeded.intent_revision_id)),
    )
    return make_pipeline(
        repo,
        output_dir=tmp_path / "out",
        provider=provider if provider is not None else FakeImageProvider(),
        engine=engine,
    )


def _boom(method: str):
    def boom(*args, **kwargs):  # noqa: ANN001 - 测试内联回调
        raise RepositoryError(DATABASE_ERROR, f"forced {method} write failure")

    return boom


# ---------------------------------------------------------------------------
# 1. Bundle 写入失败：FAILED / 0 图片调用 / 无 Prompt / 无悬空引用
# ---------------------------------------------------------------------------


def test_bundle_write_failure_marks_failed_without_provider_call_or_prompt(
    tmp_path, monkeypatch
) -> None:
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    )
    provider = FakeImageProvider()
    pipeline = _knowledge_pipeline(tmp_path, repo, seeded, provider=provider)
    monkeypatch.setattr(repo, "append_knowledge_bundle", _boom("knowledge bundle"))

    with pytest.raises(RepositoryError) as excinfo:
        pipeline.generate(seeded.session_id)

    # 原错误 code 与异常对象保留（不把写库失败伪装成知识无命中或编译失败）。
    assert excinfo.value.code == DATABASE_ERROR
    assert _state(repo, seeded.session_id) is WorkflowState.FAILED
    assert provider.requests == []
    assert artifact_count(repo, "generation_artifacts") == 0
    assert artifact_count(repo, "prompt_artifacts") == 0
    assert artifact_count(repo, "realization_states") == 0
    assert artifact_count(repo, "knowledge_bundles") == 0
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------------------
# 4. 同一 compile 边界的其它写入失败：Realization / Prompt 表
# ---------------------------------------------------------------------------


def test_realization_write_failure_marks_failed_and_leaves_no_prompt(tmp_path, monkeypatch) -> None:
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    )
    provider = FakeImageProvider()
    pipeline = _knowledge_pipeline(tmp_path, repo, seeded, provider=provider)
    monkeypatch.setattr(repo, "append_realization_state", _boom("realization"))

    with pytest.raises(RepositoryError) as excinfo:
        pipeline.generate(seeded.session_id)

    assert excinfo.value.code == DATABASE_ERROR
    assert _state(repo, seeded.session_id) is WorkflowState.FAILED
    assert provider.requests == []
    # Bundle 先落库，允许作为审计记录保留；但绝无引用它的 Prompt。
    assert artifact_count(repo, "knowledge_bundles") == 1
    assert artifact_count(repo, "realization_states") == 0
    assert artifact_count(repo, "prompt_artifacts") == 0
    assert artifact_count(repo, "generation_artifacts") == 0


def test_prompt_write_failure_marks_failed_and_leaves_no_prompt(tmp_path, monkeypatch) -> None:
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    )
    provider = FakeImageProvider()
    pipeline = _knowledge_pipeline(tmp_path, repo, seeded, provider=provider)
    monkeypatch.setattr(repo, "append_prompt_artifact", _boom("prompt"))

    with pytest.raises(RepositoryError) as excinfo:
        pipeline.generate(seeded.session_id)

    assert excinfo.value.code == DATABASE_ERROR
    assert _state(repo, seeded.session_id) is WorkflowState.FAILED
    assert provider.requests == []
    assert artifact_count(repo, "knowledge_bundles") == 1
    assert artifact_count(repo, "realization_states") == 1
    assert artifact_count(repo, "prompt_artifacts") == 0
    assert artifact_count(repo, "generation_artifacts") == 0


# ---------------------------------------------------------------------------
# 3. 第二轮写 Bundle 失败：旧图/旧 Prompt 保留，但不能用旧确认 retry
# ---------------------------------------------------------------------------


def test_second_round_bundle_failure_preserves_history_and_blocks_stale_retry(
    tmp_path, monkeypatch
) -> None:
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    )
    provider = FakeImageProvider()
    pipeline = _knowledge_pipeline(tmp_path, repo, seeded, provider=provider)

    first = pipeline.generate(seeded.session_id)
    first_generation_payload = repo.get_generation_artifact(first.generation_id).payload
    first_prompt_payload = repo.get_prompt_artifact(first.prompt_artifact_id).payload
    provider_calls_before = len(provider.requests)

    # 第二轮：修改 Intent 并新增一个尚未实现的知识路径（否则 active Realization
    # 会被纯复用，不触发新检索/新 Bundle），旧确认天然失效 → 重新确认。
    return_to_confirmation(repo, seeded.session_id)
    append_intent(
        repo,
        seeded.session_id,
        intent_with(
            {"subject.description": "a dog"},
            delegated=[LIGHTING, "camera.depth_of_field"],
        ),
    )
    save_confirmation_for_current(repo, seeded.session_id)

    monkeypatch.setattr(repo, "append_knowledge_bundle", _boom("knowledge bundle"))
    with pytest.raises(RepositoryError):
        pipeline.generate(seeded.session_id)

    assert _state(repo, seeded.session_id) is WorkflowState.FAILED
    # 历史不被覆盖：旧图旧 Prompt 逐字节保留。
    assert repo.get_generation_artifact(first.generation_id).payload == first_generation_payload
    assert repo.get_prompt_artifact(first.prompt_artifact_id).payload == first_prompt_payload
    assert artifact_count(repo, "generation_artifacts") == 1
    assert artifact_count(repo, "prompt_artifacts") == 1

    # 旧 Prompt 绑定的确认已失效 → retry 必须拒绝，且不调用 Provider。
    with pytest.raises(GenerationError) as excinfo:
        pipeline.retry(seeded.session_id)
    assert excinfo.value.code == GENERATION_NO_VALID_CONFIRMATION
    assert _state(repo, seeded.session_id) is WorkflowState.FAILED
    assert len(provider.requests) == provider_calls_before
    assert artifact_count(repo, "generation_artifacts") == 1


# ---------------------------------------------------------------------------
# 6. 失败处理自身不可写：保留原始错误 + 迁移失败诊断，不伪称恢复 / 不重试
# ---------------------------------------------------------------------------


def test_unwritable_database_keeps_original_error_and_reports_migration_failure(
    tmp_path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    )
    provider = FakeImageProvider()
    pipeline = make_pipeline(repo, output_dir=tmp_path / "out", provider=provider)
    monkeypatch.setattr(repo, "append_prompt_artifact", _boom("prompt"))

    transition_calls: list[WorkflowState] = []
    original_transition = repo.transition_state

    def failing_failed_transition(session_id: str, to_state: WorkflowState) -> None:
        transition_calls.append(to_state)
        if to_state is WorkflowState.FAILED:
            raise RepositoryError(DATABASE_ERROR, "database is not writable")
        return original_transition(session_id, to_state)

    monkeypatch.setattr(repo, "transition_state", failing_failed_transition)

    with caplog.at_level(logging.ERROR, logger=PIPELINE_LOGGER):
        with pytest.raises(RepositoryError) as excinfo:
            pipeline.generate(seeded.session_id)

    # 原始错误原样上抛，不伪称已完成 FAILED 迁移。
    assert excinfo.value.code == DATABASE_ERROR
    assert _state(repo, seeded.session_id) is WorkflowState.GENERATING
    assert artifact_count(repo, "prompt_artifacts") == 0
    assert provider.requests == []
    # 迁移只尝试一次（无无限重试循环），且失败诊断可观察。
    assert transition_calls.count(WorkflowState.FAILED) == 1
    assert any(
        "could not record the FAILED workflow state" in record.getMessage()
        for record in caplog.records
    )
