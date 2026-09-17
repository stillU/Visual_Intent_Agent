"""F1 CLI 定向回归：知识 Bundle 写库失败必须给出安全提示且不诱导 retry。

修补前：`RepositoryError` 穿透 `GenerationPipeline.generate`，会话停留 GENERATING，
CLI 只能在"瞬时状态"分支打印无法继续的提示。修补后必须走 FAILED 分支：显示稳定
code + 可读建议；没有可复用 Prompt 时明确"无法 retry"，不提示 retry。

全离线：Fake LLM / Fake Image Provider / tmp_path SQLite / 临时语料夹具。
"""

from __future__ import annotations

import pytest

from cli_helpers import (
    READY_RESPONSES,
    READY_USER_TURNS,
    ScriptedConsole,
    artifact_count,
    current_state,
    make_cli_app,
    single_session_id,
)
from tests.knowledge.knowledge_helpers import approved_payload, load_test_corpus

from visual_intent_agent.cli import run_repl
from visual_intent_agent.knowledge import LocalKnowledgeEngine
from visual_intent_agent.persistence import RepositoryError, WorkflowState
from visual_intent_agent.providers.fake_image import FakeImageProvider

LIGHTING = "lighting.character"
DATABASE_ERROR = "persistence.database_error"


def _corpus(tmp_path):
    return load_test_corpus(
        tmp_path,
        [
            approved_payload(
                applicable_path=LIGHTING,
                candidate_value="soft",
                keywords=("cat",),
                aliases=(),
            )
        ],
        file_name="units.jsonl",
    )


def test_cli_reports_bundle_write_failure_safely_and_does_not_offer_retry(
    tmp_path, monkeypatch
) -> None:
    provider = FakeImageProvider()
    app, repo, _llm, _output_dir = make_cli_app(
        tmp_path,
        interpreter_responses=READY_RESPONSES,
        image_provider=provider,
        knowledge_engine=LocalKnowledgeEngine(corpus=_corpus(tmp_path)),
    )

    def boom(*args, **kwargs):  # noqa: ANN001 - 测试内联回调
        raise RepositoryError(DATABASE_ERROR, "forced bundle write failure")

    monkeypatch.setattr(repo, "append_knowledge_bundle", boom)

    answers = [*READY_USER_TURNS, "y", "exit"]
    console = ScriptedConsole(answers)

    # 写库失败不得让 CLI 崩溃，也不得停留在 GENERATING。
    assert run_repl(app, console) == 0

    session_id = single_session_id(repo)
    assert current_state(repo, session_id) is WorkflowState.FAILED
    assert DATABASE_ERROR in console.text
    assert "持久化失败" in console.text
    # 没有可复用 Prompt：明确无法 retry，且 UI 不再提示 retry（不诱导）。
    assert "无法 retry，也不能直接重新确认" in console.text
    assert "输入 retry 重新生成" not in console.text
    assert "输入 exit 退出" in console.text
    assert "会话停留在" not in console.text
    assert artifact_count(repo, "prompt_artifacts") == 0
    assert artifact_count(repo, "generation_artifacts") == 0
    assert artifact_count(repo, "knowledge_bundles") == 0
    assert provider.requests == []
