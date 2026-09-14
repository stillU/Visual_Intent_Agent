"""Step 07 集成测试：用 FakeLLMProvider + tmp_path SQLite 从 P1 流程走到确认再 compile。

全离线（零网络、零真实凭据）：LLM 由脚本化 `FakeLLMProvider` 提供，Repository 是临时
SQLite，PromptEngine 只用唯一的目标模型 Renderer；不构造任何图像 Provider。
"""

from __future__ import annotations

import json
import subprocess
import sys

from prompt_engine_helpers import (
    FAKE_IMAGE_MODEL,
    artifact_counts,
    make_engine,
    response,
    run_p1_to_confirmation,
    run_p1_to_ready,
    set_entry,
)

from visual_intent_agent.domain import new_id
from visual_intent_agent.prompt_engine import (
    PROMPT_NO_VALID_CONFIRMATION,
    PromptArtifact,
    PromptCompilationError,
    PromptCompileRequest,
)
from visual_intent_agent.prompt_engine.engine import resolved_important_paths
from visual_intent_agent.realization.models import RealizationState
from visual_intent_agent.workflow import compute_summary_hash

LIGHTING = "lighting.character"


def _compile(run):
    return make_engine(run.repo).compile(
        PromptCompileRequest(
            session_id=run.session_id, confirmation_id=run.confirmation.confirmation_id
        )
    )


def test_p1_confirmed_prompt_artifact_is_saved_and_traceable(tmp_path):
    run = run_p1_to_confirmation(tmp_path)

    artifact = _compile(run)

    assert artifact.session_id == run.session_id
    assert artifact.based_on_intent_revision_id == run.confirmation.intent_revision_id
    assert artifact.based_on_confirmation_id == run.confirmation.confirmation_id
    assert artifact.target_model == FAKE_IMAGE_MODEL
    assert artifact.parameters.size == "1024x1024"
    assert artifact.prompt.startswith("subject: a cat")
    assert "lighting:" in artifact.prompt
    assert "color" not in artifact.prompt

    intent = run.repo.get_intent_revision(run.confirmation.intent_revision_id).intent
    assert {b.intent_path for b in artifact.source_bindings} == set(
        resolved_important_paths(intent)
    )
    assert all(
        binding.intent_path or binding.rule_id or binding.realization_id
        for binding in artifact.source_bindings
    )

    stored = run.repo.get_prompt_artifact(artifact.prompt_artifact_id)
    assert stored.refs == {
        "intent_revision_id": run.confirmation.intent_revision_id,
        "confirmation_id": run.confirmation.confirmation_id,
    }
    assert PromptArtifact.model_validate_json(stored.payload) == artifact
    assert run.repo.is_confirmation_valid(run.confirmation.confirmation_id) is True
    assert artifact_counts(run.repo) == {
        "prompt_artifacts": 1,
        "generation_artifacts": 0,
        "feedback_results": 0,
        "realization_states": 1,
    }


def test_p1_delegated_lighting_is_realized_and_color_stays_unspecified(tmp_path):
    run = run_p1_to_confirmation(tmp_path)

    artifact = _compile(run)
    stored = run.repo.get_current_realization_state(run.session_id)
    state = RealizationState.model_validate_json(stored.payload)

    assert [value.path for value in state.values] == [LIGHTING]
    assert state.based_on_intent_revision_id == run.confirmation.intent_revision_id
    (value,) = state.values
    assert value.source == "user_delegated"
    assert value.status == "active"
    assert value.first_prompt_artifact_id == artifact.prompt_artifact_id
    assert f"lighting: {value.value}" in artifact.prompt
    assert artifact.realization_refs == [state.realization_id]


def test_p1_reconfirmation_keeps_old_artifact_and_reuses_realization(tmp_path):
    run = run_p1_to_confirmation(
        tmp_path,
        extra_responses=[response(set_entry("style.primary", "cinematic"))],
    )
    first = _compile(run)
    first_refs = first.realization_refs

    modified = run.service.submit_message(run.session_id, "make it cinematic")
    assert modified.confirmation_summary is not None
    second_confirmation = run.service.confirm_current_intent(
        run.session_id,
        modified.snapshot.current_intent_revision_id,
        modified.snapshot.current_execution_revision_id,
        compute_summary_hash(modified.confirmation_summary),
    )
    second = make_engine(run.repo).compile(
        PromptCompileRequest(
            session_id=run.session_id, confirmation_id=second_confirmation.confirmation_id
        )
    )

    assert second.prompt_artifact_id != first.prompt_artifact_id
    assert "style: cinematic" in second.prompt
    assert run.repo.get_prompt_artifact(first.prompt_artifact_id).payload == (
        first.model_dump_json()
    )
    assert artifact_counts(run.repo) == {
        "prompt_artifacts": 2,
        "generation_artifacts": 0,
        "feedback_results": 0,
        "realization_states": 1,  # 复用既有 Realization，不重新选择
    }
    assert second.realization_refs == first_refs
    assert run.repo.is_confirmation_valid(first.based_on_confirmation_id) is False


def test_p1_compile_before_confirmation_is_rejected(tmp_path):
    _service, repo, _llm, session_id, outcome = run_p1_to_ready(tmp_path)
    assert outcome.snapshot.latest_confirmation_id is None

    try:
        make_engine(repo).compile(
            PromptCompileRequest(session_id=session_id, confirmation_id=new_id("cnf"))
        )
    except PromptCompilationError as error:
        assert error.code == PROMPT_NO_VALID_CONFIRMATION
    else:  # pragma: no cover - 失败即测试失败
        raise AssertionError("compile must reject a session without a valid confirmation")

    assert artifact_counts(repo) == {
        "prompt_artifacts": 0,
        "generation_artifacts": 0,
        "feedback_results": 0,
        "realization_states": 0,
    }


def test_p1_prompt_layer_never_touches_image_generation(tmp_path):
    run = run_p1_to_confirmation(tmp_path)
    artifact = _compile(run)

    assert artifact.prompt  # Prompt 是文本，不是生成结果
    assert artifact_counts(run.repo)["generation_artifacts"] == 0
    assert artifact_counts(run.repo)["feedback_results"] == 0


def test_prompt_engine_imports_no_http_or_image_provider():
    """干净子进程：导入 prompt_engine 不拉入 httpx / providers / generation / feedback。"""
    code = (
        "import json, sys;"
        "import visual_intent_agent.prompt_engine;"
        "print(json.dumps({"
        "'modules': sorted(m for m in sys.modules if m.startswith('visual_intent_agent')),"
        "'httpx': 'httpx' in sys.modules,"
        "'sqlite3': 'sqlite3' in sys.modules}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    modules = set(report["modules"])
    assert report["httpx"] is False
    assert "visual_intent_agent.providers" not in modules
    assert "visual_intent_agent.generation" not in modules
    assert "visual_intent_agent.feedback" not in modules
    assert "visual_intent_agent.prompt_engine" in modules
