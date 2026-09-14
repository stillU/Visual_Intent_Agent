"""Step 08 真实图像 Provider smoke（`@pytest.mark.smoke`；默认运行被 pyproject 排除）。

规则（ARCHITECTURE.md 6.5）：

- 只在显式 `uv run pytest -m smoke` 时运行；无凭据时自动 skip；
- **恰好 1 次真实图像生成**：会话、Intent、Confirmation 用确定性代码直接装配（不调用
  真实 LLM），唯一真实网络调用是 `POST /images/generations` + 其响应内的一次下载；
- 断言图片字节非空且为 PNG magic、GenerationArtifact 落库、文件存在于 `tmp_path` 输出目录；
- 断言与日志绝不出现明文 key 与完整临时 URL；
- smoke 不是任何 Step 的验收条件；失败时如实记录错误分类，不伪造通过。

凭据来自 `.env`（经 `load_settings()`）；本文件不出现任何真实 key。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from visual_intent_agent.config import has_provider_credentials, load_settings
from visual_intent_agent.domain import (
    EnvironmentFacet,
    ExecutionRevision,
    IntentRevision,
    StyleFacet,
    SubjectFacet,
    VisualIntent,
    new_id,
    utc_now,
)
from visual_intent_agent.generation import GenerationArtifact, GenerationPipeline
from visual_intent_agent.persistence import (
    ConfirmationRecord,
    SQLiteRepository,
    WorkflowState,
)
from visual_intent_agent.prompt_engine import PromptEngine, QwenImageRenderer
from visual_intent_agent.providers.openai_image import OpenAIImageProvider
from visual_intent_agent.workflow import build_confirmation_summary, compute_summary_hash

pytestmark = pytest.mark.smoke

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

SMOKE_OUTPUT_SIZE = "1024x1024"


@pytest.fixture
def settings():
    if not has_provider_credentials():
        pytest.skip("smoke credentials not configured (.env / env vars missing)")
    return load_settings()


def _seed_confirmed_session(repo: SQLiteRepository, settings) -> str:
    """确定性装配"已确认会话"（不调用 LLM；Intent/Execution/Confirmation 全离线）。"""
    session_id = new_id("ses")
    repo.create_session(session_id)
    execution_revision = ExecutionRevision(
        execution_revision_id=new_id("erev"),
        session_id=session_id,
        parent_revision_id=None,
        target_model=settings.image_model,
        output_size=SMOKE_OUTPUT_SIZE,
    )
    repo.append_execution_revision(execution_revision)
    intent_revision = IntentRevision(
        intent_revision_id=new_id("irev"),
        session_id=session_id,
        parent_revision_id=None,
        intent=VisualIntent(
            subject=SubjectFacet(description="a red ceramic teapot on a wooden table"),
            style=StyleFacet(primary="photorealistic"),
            environment=EnvironmentFacet(mode="studio"),
        ),
    )
    repo.append_intent_revision(intent_revision)
    repo.transition_state(session_id, WorkflowState.WAITING_CONFIRMATION)
    summary = build_confirmation_summary(intent_revision, execution_revision)
    repo.save_confirmation(
        ConfirmationRecord(
            confirmation_id=new_id("cnf"),
            session_id=session_id,
            intent_revision_id=intent_revision.intent_revision_id,
            execution_revision_id=execution_revision.execution_revision_id,
            summary_hash=compute_summary_hash(summary),
            confirmed_at=utc_now(),
        )
    )
    return session_id


def test_real_image_generation_produces_exactly_one_png(tmp_path, settings, caplog) -> None:
    """真实 `/images/generations` + 下载：恰好 1 张 PNG，落库且可重新定位。"""
    repo = SQLiteRepository(tmp_path / "smoke.db")
    provider = OpenAIImageProvider(settings)
    try:
        session_id = _seed_confirmed_session(repo, settings)
        output_dir = tmp_path / "generations"
        pipeline = GenerationPipeline(
            repo=repo,
            prompt_engine=PromptEngine(QwenImageRenderer(), repo),
            image_provider=provider,
            output_dir=output_dir,
        )
        # 只捕获本项目包的日志（第三方 HTTP 库的 DEBUG 不在本步职责内）。
        with caplog.at_level(logging.DEBUG, logger="visual_intent_agent"):
            artifact = pipeline.generate(session_id)

        # 落库且状态进入复核。
        assert (
            repo.get_current_session_snapshot(session_id).workflow_state
            is WorkflowState.WAITING_REVIEW
        )
        stored = repo.get_generation_artifact(artifact.generation_id)
        assert stored.refs == {"prompt_artifact_id": artifact.prompt_artifact_id}
        assert GenerationArtifact.model_validate_json(stored.payload) == artifact

        # 恰好 1 张图片，字节非空且为 PNG magic。
        assert len(artifact.output_refs) == 1
        ref = artifact.output_refs[0]
        written = (PROJECT_ROOT / ref.path).resolve()
        assert written.is_file()
        content = written.read_bytes()
        assert content
        assert content.startswith(PNG_MAGIC)
        assert ref.byte_size == len(content)
        assert written.parent == output_dir.resolve() / artifact.generation_id

        # seed 不伪造（Provider 不返回就是 None）；模型与参数可追溯。
        assert artifact.seed is None or isinstance(artifact.seed, int)
        assert artifact.target_model == settings.image_model
        assert artifact.parameters.size == SMOKE_OUTPUT_SIZE

        # 不存临时 URL：Artifact payload 里没有 http(s) / 签名参数。
        payload = stored.payload
        assert "http://" not in payload
        assert "https://" not in payload
        assert "Expires=" not in payload
        assert "Signature=" not in payload

        # 断言与日志中绝不出现明文 key。
        secret = settings.provider_api_key.get_secret_value()
        assert secret not in payload
        assert secret not in repr(artifact)
        for record in caplog.records:
            assert secret not in record.getMessage()
            assert secret not in str(record.__dict__)
    finally:
        provider.close()
        repo.close()
