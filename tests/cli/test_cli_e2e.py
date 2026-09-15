"""CLI 端到端测试（全 Fake、全离线、tmp_path SQLite；默认真实 Provider 零调用）。

覆盖任务书 08 的最小用户流程与关键不变量：

- 正常闭环：需求 → 澄清 → 摘要 → 人工确认 → 生成（显示路径）→ 接受 → 退出；
- 局部反馈：修改指定路径 → **必须重新确认** → 生成新 Artifact（历史不覆盖）；
- timeout 恢复：Provider 超时按可恢复失败处理，状态不变，需用户显式 `retry`；
- 不自动确认：确认页输入修改消息时，旧确认不产生、也不被套用；
- 配置缺失：真实模式给出可读提示并返回退出码 2；
- `--demo` 无凭据可操作；`python -m visual_intent_agent --help` 入口可用。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from visual_intent_agent.cli import main, run_repl
from visual_intent_agent.config import PROJECT_ROOT
from visual_intent_agent.domain import CompositionFacet, IntentRevision, new_id
from visual_intent_agent.generation import GenerationArtifact
from visual_intent_agent.persistence import WorkflowState
from visual_intent_agent.providers.errors import ProviderError

from cli_helpers import (
    READY_RESPONSES,
    READY_USER_TURNS,
    FlakyImageProvider,
    ScriptedConsole,
    artifact_count,
    current_intent_value,
    current_state,
    feedback_response,
    make_cli_app,
    make_settings,
    prompt_artifacts,
    response,
    revise_entry,
    set_entry,
    single_session_id,
)


def _artifacts(repo, session_id: str) -> list[GenerationArtifact]:
    return [
        GenerationArtifact.model_validate_json(stored.payload)
        for stored in repo.list_generation_artifacts(session_id)
    ]


def _output_file(output_dir: Path, artifact: GenerationArtifact) -> Path:
    return output_dir / artifact.generation_id / "image_1.png"


def _append_revision_with_framing(repo, session_id: str, framing: str) -> IntentRevision:
    """直接落一条新 IntentRevision（模拟"展示摘要后 revision 被外部改变"）。"""
    snapshot = repo.get_current_session_snapshot(session_id)
    assert snapshot.current_intent_revision_id is not None
    current = repo.get_intent_revision(snapshot.current_intent_revision_id)
    revised = current.intent.model_copy(
        update={"composition": CompositionFacet(framing=framing)}
    )
    revision = IntentRevision(
        intent_revision_id=new_id("irev"),
        session_id=session_id,
        parent_revision_id=snapshot.current_intent_revision_id,
        intent=revised,
    )
    repo.append_intent_revision(revision)
    return revision


# ---------------------------------------------------------------------------
# 正常闭环
# ---------------------------------------------------------------------------


def test_cli_normal_flow_confirm_generate_and_accept(tmp_path):
    app, repo, _llm, output_dir = make_cli_app(
        tmp_path,
        interpreter_responses=READY_RESPONSES,
        feedback_responses=[feedback_response("accept")],
    )
    answers = [*READY_USER_TURNS, "y", "accept"]
    console = ScriptedConsole(answers)

    assert run_repl(app, console) == 0

    session_id = single_session_id(repo)
    assert current_state(repo, session_id) is WorkflowState.COMPLETED
    assert artifact_count(repo, "confirmations") == 1
    assert artifact_count(repo, "generation_artifacts") == 1
    assert artifact_count(repo, "prompt_artifacts") == 1

    artifact = _artifacts(repo, session_id)[0]
    assert _output_file(output_dir, artifact).is_file()
    # 生成文件路径与 Artifact ID 必须展示给用户。
    assert str(artifact.generation_id) in console.text
    assert str(_output_file(output_dir, artifact).resolve()) in console.text
    # 不得泄露任何凭据 / Provider 配置。
    assert "unit-test-key" not in console.text
    assert "provider.invalid" not in console.text


# ---------------------------------------------------------------------------
# 局部反馈 → 重新确认 → 新 Artifact
# ---------------------------------------------------------------------------


def test_cli_partial_feedback_requires_reconfirmation_and_new_artifact(tmp_path):
    app, repo, _llm, output_dir = make_cli_app(
        tmp_path,
        interpreter_responses=READY_RESPONSES,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[
                    revise_entry(
                        "composition.framing", "wide_shot", evidence_fragment="宽"
                    )
                ],
            ),
            feedback_response("accept"),
        ],
    )
    answers = [
        *READY_USER_TURNS,
        "y",  # 第一次确认 → 生成 #1
        "把构图改宽一点",  # 局部反馈 → revise → 重新等待确认
        "y",  # 必须再次确认 → 生成 #2
        "accept",
    ]
    console = ScriptedConsole(answers)

    assert run_repl(app, console) == 0

    session_id = single_session_id(repo)
    assert current_state(repo, session_id) is WorkflowState.COMPLETED
    assert artifact_count(repo, "generation_artifacts") == 2
    assert artifact_count(repo, "confirmations") == 2
    assert current_intent_value(repo, session_id, "composition.framing") == "wide_shot"

    artifacts = _artifacts(repo, session_id)
    first, second = artifacts
    assert first.generation_id != second.generation_id
    assert first.prompt_artifact_id != second.prompt_artifact_id
    assert _output_file(output_dir, first).is_file()
    assert _output_file(output_dir, second).is_file()
    # 历史 Artifact 仍可审计（不被覆盖）。
    assert repo.get_generation_artifact(first.generation_id) is not None


# ---------------------------------------------------------------------------
# timeout 恢复：可恢复失败 + 显式 retry
# ---------------------------------------------------------------------------


def test_cli_provider_timeout_is_recoverable_only_by_explicit_retry(tmp_path):
    timeout = ProviderError.timeout("simulated provider timeout")
    app, repo, _llm, output_dir = make_cli_app(
        tmp_path,
        # 第 1 轮成功；第 2 轮（回答 style.primary 时）连续两次超时。
        interpreter_responses=[
            READY_RESPONSES[0],
            timeout,
            timeout,
            *READY_RESPONSES[1:],
        ],
        feedback_responses=[feedback_response("accept")],
    )
    answers = [
        "a photo of a cat",
        "photorealistic",
        "retry",
        *READY_USER_TURNS[2:],
        "y",
        "accept",
    ]
    console = ScriptedConsole(answers)

    assert run_repl(app, console) == 0

    session_id = single_session_id(repo)
    # 超时后状态不前进、同一个问题被保留、也没有偷偷生成；必须显式 retry 才继续。
    assert "provider.timeout" in console.text
    assert "本轮未处理成功" in console.text
    assert "retry=重发上一条" in console.text
    assert console.text.count("style.primary") >= 2  # 失败前后都是同一个待答问题
    assert current_state(repo, session_id) is WorkflowState.COMPLETED
    assert artifact_count(repo, "generation_artifacts") == 1
    assert artifact_count(repo, "confirmations") == 1
    artifact = _artifacts(repo, session_id)[0]
    assert _output_file(output_dir, artifact).is_file()
    # 失败的那条回答被显式重发（同一条文本）。
    assert console.text.count("photorealistic") >= 2


# ---------------------------------------------------------------------------
# FAILED 生成：复用同一 PromptArtifact 显式重试（不伪造 generation_id）
# ---------------------------------------------------------------------------


def test_cli_failed_generation_retry_reuses_prompt_without_fabricated_id(tmp_path):
    flaky = FlakyImageProvider(fail_on_calls=(1,))
    app, repo, _llm, output_dir = make_cli_app(
        tmp_path,
        interpreter_responses=READY_RESPONSES,
        feedback_responses=[feedback_response("accept")],
        image_provider=flaky,
    )
    answers = [*READY_USER_TURNS, "y", "retry", "accept"]
    console = ScriptedConsole(answers)

    assert run_repl(app, console) == 0

    session_id = single_session_id(repo)
    # 首次生成 Provider 失败 → FAILED、无 GenerationArtifact；retry 复用同一 PromptArtifact。
    assert "provider.timeout" in console.text
    assert current_state(repo, session_id) is WorkflowState.COMPLETED
    assert artifact_count(repo, "prompt_artifacts") == 1  # 绝不重新 compile
    assert artifact_count(repo, "generation_artifacts") == 1
    assert len(flaky.requests) == 2
    artifact = _artifacts(repo, session_id)[0]
    assert _output_file(output_dir, artifact).is_file()


def test_cli_retry_after_second_generation_failure_uses_second_prompt(tmp_path):
    """第一版成功 → 修改再确认 → 第二次生成失败 → retry 必须复用**第二版** Prompt。"""
    flaky = FlakyImageProvider(fail_on_calls=(2,))
    app, repo, _llm, output_dir = make_cli_app(
        tmp_path,
        interpreter_responses=READY_RESPONSES,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[
                    revise_entry(
                        "composition.framing", "wide_shot", evidence_fragment="宽"
                    )
                ],
            ),
            feedback_response("accept"),
        ],
        image_provider=flaky,
    )
    answers = [
        *READY_USER_TURNS,
        "y",  # 第一次确认 → 生成 #1 成功
        "把构图改宽一点",  # 修改 → 重新确认
        "y",  # 第二次确认 → 生成 #2 Provider 失败 → FAILED
        "retry",  # 必须复用第二次编译的 Prompt（不能挑第一版的旧 Artifact）
        "accept",
    ]
    console = ScriptedConsole(answers)

    assert run_repl(app, console) == 0

    session_id = single_session_id(repo)
    assert "provider.timeout" in console.text
    assert current_state(repo, session_id) is WorkflowState.COMPLETED
    assert len(flaky.requests) == 3  # 成功 / 失败 / retry 成功
    prompts = prompt_artifacts(repo)
    assert len(prompts) == 2
    first_prompt, second_prompt = prompts
    assert first_prompt.prompt_artifact_id != second_prompt.prompt_artifact_id

    artifacts = _artifacts(repo, session_id)
    assert len(artifacts) == 2
    # 第二次生成（无论失败前还是 retry 后）必须绑定第二版 Prompt。
    assert artifacts[0].prompt_artifact_id == first_prompt.prompt_artifact_id
    retried = artifacts[-1]
    assert retried.prompt_artifact_id == second_prompt.prompt_artifact_id
    assert _output_file(output_dir, retried).is_file()
    # 第二版 Prompt 依据的是包含修改（wide_shot）的当前 revision。
    snapshot = repo.get_current_session_snapshot(session_id)
    assert second_prompt.based_on_intent_revision_id == snapshot.current_intent_revision_id
    assert current_intent_value(repo, session_id, "composition.framing") == "wide_shot"


# ---------------------------------------------------------------------------
# 确认绑定：展示之后 revision 变化必须被拒绝（不得静默确认没看到的版本）
# ---------------------------------------------------------------------------


def test_cli_confirm_rejects_revision_changed_after_summary_display(tmp_path):
    app, repo, _llm, output_dir = make_cli_app(
        tmp_path,
        interpreter_responses=READY_RESPONSES,
        feedback_responses=[feedback_response("accept")],
    )
    mutated = {"done": False}

    def hook(prompt_text: str) -> None:
        # 在用户看到摘要之后、提交确认之前，外部改变当前 revision。
        if "确认并生成" in prompt_text and not mutated["done"]:
            mutated["done"] = True
            _append_revision_with_framing(repo, single_session_id(repo), "close_up")

    answers = [*READY_USER_TURNS, "y", "y", "accept"]
    console = ScriptedConsole(answers, hook=hook)

    assert run_repl(app, console) == 0

    session_id = single_session_id(repo)
    # 第一次确认被拒（旧 revision），只发生一次真正确认与一次生成。
    assert (
        "workflow.stale_revision" in console.text
        or "workflow.summary_hash_mismatch" in console.text
    )
    assert console.text.count("已确认并生成。") == 1
    assert artifact_count(repo, "confirmations") == 1
    assert artifact_count(repo, "generation_artifacts") == 1
    # 生成依据的是用户第二次实际看到并确认的 revision。
    assert current_intent_value(repo, session_id, "composition.framing") == "close_up"
    artifact = _artifacts(repo, session_id)[0]
    assert _output_file(output_dir, artifact).is_file()


# ---------------------------------------------------------------------------
# Prompt 编译失败：必须是可捕获的领域错误；无 Prompt 时明确只能退出 / 新会话
# ---------------------------------------------------------------------------


def test_cli_prompt_compilation_failure_is_handled_and_cannot_retry(tmp_path):
    app, repo, _llm, _output_dir = make_cli_app(
        tmp_path,
        interpreter_responses=READY_RESPONSES,
        feedback_responses=[],
        settings=make_settings(image_model="unsupported-model"),
    )
    answers = [*READY_USER_TURNS, "y", "exit"]
    console = ScriptedConsole(answers)

    # 编译失败（prompt.unsupported_requirement）不得让 CLI 崩溃。
    assert run_repl(app, console) == 0

    session_id = single_session_id(repo)
    assert "prompt.unsupported_requirement" in console.text
    assert current_state(repo, session_id) is WorkflowState.FAILED
    assert artifact_count(repo, "prompt_artifacts") == 0
    assert artifact_count(repo, "generation_artifacts") == 0
    # 没有可复用 Prompt：明确只能退出 / 新建会话，不提示可直接重新确认。
    assert "无法 retry，也不能直接重新确认" in console.text


# ---------------------------------------------------------------------------
# 不自动确认
# ---------------------------------------------------------------------------

def test_cli_never_confirms_without_the_explicit_confirmation_token(tmp_path):
    app, repo, _llm, output_dir = make_cli_app(
        tmp_path,
        interpreter_responses=[
            *READY_RESPONSES,
            # 用户在确认页输入的是修改（不是确认）：服务应落新 revision 并再次要求确认。
            response(set_entry("composition.framing", "wide_shot")),
        ],
        feedback_responses=[feedback_response("accept")],
    )
    answers = [
        *READY_USER_TURNS,
        "make the framing wider",  # 确认页输入修改 → 不得确认
        "y",  # 显式确认
        "accept",
    ]
    console = ScriptedConsole(answers)

    assert run_repl(app, console) == 0

    session_id = single_session_id(repo)
    assert current_state(repo, session_id) is WorkflowState.COMPLETED
    # 只有一次显式确认 / 一次生成；修改消息没有触发任何自动确认。
    assert artifact_count(repo, "confirmations") == 1
    assert artifact_count(repo, "generation_artifacts") == 1
    assert current_intent_value(repo, session_id, "composition.framing") == "wide_shot"
    artifact = _artifacts(repo, session_id)[0]
    assert _output_file(output_dir, artifact).is_file()


# ---------------------------------------------------------------------------
# 配置缺失 / --demo / 模块入口
# ---------------------------------------------------------------------------


def test_real_mode_missing_credentials_returns_clear_error():
    console = ScriptedConsole([])
    code = main([], console=console, env={}, env_file=None)
    assert code == 2
    assert "配置错误" in console.text
    assert "VIA_PROVIDER_BASE_URL" in console.text
    assert "--demo" in console.text


def test_demo_mode_runs_offline_without_credentials(tmp_path):
    console = ScriptedConsole(
        ["一只猫", "photorealistic", "studio", "木桌", "medium_shot", "sitting", "you decide", "y", "accept"]
    )
    code = main(
        [
            "--demo",
            "--db",
            str(tmp_path / "demo.db"),
            "--output-dir",
            str(tmp_path / "outputs"),
        ],
        console=console,
        env={},
        env_file=None,
    )
    assert code == 0
    assert "离线演示模式" in console.text
    assert "已确认并生成" in console.text
    assert "会话已完成" in console.text


def test_module_entrypoint_help_works_without_credentials():
    env = {key: value for key, value in os.environ.items() if not key.startswith("VIA_")}
    result = subprocess.run(
        [sys.executable, "-m", "visual_intent_agent", "--help"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0
    assert "--demo" in result.stdout
    assert "--db" in result.stdout
