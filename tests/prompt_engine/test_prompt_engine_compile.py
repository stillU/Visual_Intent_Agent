"""Step 07 编译流程边界测试：不支持的要求、覆盖检查、保存前复核、分步写库。

覆盖任务书「实现步骤」的 6（unauthorized addition 检查）、7（保存前再次验证
Confirmation 有效性）、8（保存 PromptArtifact 与新 Realization）。
"""

from __future__ import annotations

import pytest

from prompt_engine_helpers import (
    append_intent,
    artifact_count,
    artifact_counts,
    intent_with,
    make_engine,
    make_repo,
    save_confirmation_for_current,
    seed_confirmed_session,
)

from visual_intent_agent.domain import new_id
from visual_intent_agent.persistence import WorkflowState
from visual_intent_agent.prompt_engine import (
    PROMPT_MISSING_SOURCE_BINDING,
    PROMPT_NO_VALID_CONFIRMATION,
    PROMPT_UNSUPPORTED_REQUIREMENT,
    PromptArtifact,
    PromptCompilationError,
    PromptCompileRequest,
    PromptEngine,
)
from visual_intent_agent.prompt_engine.engine import read_intent_path
from visual_intent_agent.realization.models import RealizationState

LIGHTING = "lighting.character"
COLOR = "color.palette"


def _request(seeded, confirmation_id=None):
    return PromptCompileRequest(
        session_id=seeded.session_id,
        confirmation_id=confirmation_id or seeded.confirmation_id,
    )


def test_compile_rejects_a_non_supported_target_model(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo,
        intent_with({"subject.description": "a cat"}),
        target_model="some-other-image-model",
    )

    with pytest.raises(PromptCompilationError) as excinfo:
        make_engine(repo).compile(_request(seeded))

    assert excinfo.value.code == PROMPT_UNSUPPORTED_REQUIREMENT
    assert artifact_counts(repo) == {
        "prompt_artifacts": 0,
        "generation_artifacts": 0,
        "feedback_results": 0,
        "realization_states": 0,
    }


@pytest.mark.parametrize("size", ["1024", "1024*1024", "0x1024", "1024x0", ""])
def test_compile_rejects_an_unrenderable_output_size(tmp_path, size):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}), output_size=size
    )

    with pytest.raises(PromptCompilationError) as excinfo:
        make_engine(repo).compile(_request(seeded))

    assert excinfo.value.code == PROMPT_UNSUPPORTED_REQUIREMENT
    assert artifact_count(repo, "prompt_artifacts") == 0


def test_compile_rejects_an_intent_with_no_renderable_clause(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(repo, intent_with())

    with pytest.raises(PromptCompilationError) as excinfo:
        make_engine(repo).compile(_request(seeded))

    assert excinfo.value.code == PROMPT_UNSUPPORTED_REQUIREMENT
    assert artifact_count(repo, "prompt_artifacts") == 0


def test_compile_rejects_a_non_request_argument(tmp_path):
    repo = make_repo(tmp_path)
    seed_confirmed_session(repo, intent_with({"subject.description": "a cat"}))
    with pytest.raises(TypeError):
        make_engine(repo).compile("ses_1")  # type: ignore[arg-type]


def test_compile_revalidates_the_confirmation_before_saving(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(repo, intent_with({"subject.description": "a cat"}))
    engine = make_engine(repo)
    original = type(repo).is_confirmation_valid
    calls = {"count": 0}

    def flaky(self, confirmation_id):
        calls["count"] += 1
        if calls["count"] >= 2:  # 保存前的那次复核失败
            return False
        return original(self, confirmation_id)

    monkeypatch.setattr(type(repo), "is_confirmation_valid", flaky)

    with pytest.raises(PromptCompilationError) as excinfo:
        engine.compile(_request(seeded))

    assert excinfo.value.code == PROMPT_NO_VALID_CONFIRMATION
    assert calls["count"] >= 2
    assert artifact_counts(repo) == {
        "prompt_artifacts": 0,
        "generation_artifacts": 0,
        "feedback_results": 0,
        "realization_states": 0,
    }


def test_compile_can_run_after_the_session_transitions_to_generating(tmp_path):
    """Step 08 的调用顺序：WAITING_CONFIRMATION + 有效确认 → GENERATING → compile。"""
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(repo, intent_with({"subject.description": "a cat"}))
    repo.transition_state(seeded.session_id, WorkflowState.GENERATING)

    artifact = make_engine(repo).compile(_request(seeded))

    assert artifact.prompt == "subject: a cat"
    assert repo.get_current_session_snapshot(seeded.session_id).workflow_state is (
        WorkflowState.GENERATING
    )


def test_artifact_is_saved_with_the_frozen_refs_and_reads_back(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(repo, intent_with({"subject.description": "a cat"}))

    artifact = make_engine(repo).compile(_request(seeded))
    stored = repo.get_prompt_artifact(artifact.prompt_artifact_id)

    assert stored.session_id == seeded.session_id
    assert stored.refs == {
        "intent_revision_id": seeded.intent_revision_id,
        "confirmation_id": seeded.confirmation_id,
    }
    assert PromptArtifact.model_validate_json(stored.payload) == artifact
    assert repo.is_confirmation_valid(seeded.confirmation_id) is True


def test_new_realization_state_carries_active_values_and_adds_new_selections(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    )
    first = make_engine(repo).compile(_request(seeded))
    first_state = RealizationState.model_validate_json(
        repo.get_current_realization_state(seeded.session_id).payload
    )

    changed = intent_with(
        {"subject.description": "a cat"}, delegated=[LIGHTING, COLOR]
    )
    revision = append_intent(repo, seeded.session_id, changed)
    record = save_confirmation_for_current(repo, seeded.session_id)
    second = make_engine(repo).compile(
        PromptCompileRequest(
            session_id=seeded.session_id, confirmation_id=record.confirmation_id
        )
    )

    second_state = RealizationState.model_validate_json(
        repo.get_current_realization_state(seeded.session_id).payload
    )
    assert second_state.realization_id != first_state.realization_id
    assert second_state.based_on_intent_revision_id == revision.intent_revision_id
    by_path = {value.path: value for value in second_state.values}
    assert set(by_path) == {LIGHTING, COLOR}
    assert by_path[LIGHTING].value == first_state.values[0].value  # carry 原值
    assert by_path[LIGHTING].first_prompt_artifact_id == first.prompt_artifact_id
    assert by_path[COLOR].first_prompt_artifact_id == second.prompt_artifact_id
    assert second.realization_refs == [second_state.realization_id]
    assert artifact_count(repo, "realization_states") == 2
    assert first_state.realization_id != second_state.realization_id


def test_dropping_a_resolved_clause_fails_the_coverage_check(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo,
        intent_with({"subject.description": "a cat", "style.primary": "photorealistic"}),
    )
    engine = make_engine(repo)
    original = PromptEngine._build_compilation_spec

    def tampered(self, **kwargs):
        spec = original(self, **kwargs)
        return spec.model_copy(update={"clauses": spec.clauses[:-1]})

    monkeypatch.setattr(PromptEngine, "_build_compilation_spec", tampered)

    with pytest.raises(PromptCompilationError) as excinfo:
        engine.compile(_request(seeded))

    assert excinfo.value.code == PROMPT_MISSING_SOURCE_BINDING
    assert artifact_count(repo, "prompt_artifacts") == 0


def test_realization_state_refs_use_the_frozen_required_key(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    )

    artifact = make_engine(repo).compile(_request(seeded))
    stored = repo.get_current_realization_state(seeded.session_id)

    assert stored.artifact_id == artifact.realization_refs[0]
    assert stored.refs == {"based_on_intent_revision_id": seeded.intent_revision_id}


def test_compiling_twice_does_not_duplicate_or_mutate_the_delegated_value(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    )
    engine = make_engine(repo)

    first = engine.compile(_request(seeded))
    second = engine.compile(_request(seeded))

    first_by_path = {b.intent_path: b for b in first.source_bindings}
    second_by_path = {b.intent_path: b for b in second.source_bindings}
    assert first.prompt == second.prompt
    assert first_by_path.keys() == second_by_path.keys()
    for path, first_binding in first_by_path.items():
        second_binding = second_by_path[path]
        assert first_binding.text == second_binding.text
        assert first_binding.realization_id == second_binding.realization_id
    # 首次编译是 delegation，第二次是复用已有值的 realization（同一 realization state）。
    assert first_by_path[LIGHTING].source_kind == "delegation"
    assert second_by_path[LIGHTING].source_kind == "realization"
    assert first.realization_refs == second.realization_refs
    assert artifact_count(repo, "realization_states") == 1
    assert artifact_count(repo, "prompt_artifacts") == 2


def test_runtime_and_delegation_bindings_keep_distinct_realization_lists(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    )
    artifact = make_engine(repo).compile(_request(seeded))

    (realization_id,) = artifact.realization_refs
    assert realization_id.startswith("rlz_")
    assert artifact.prompt_artifact_id.startswith("pra_")
    assert all(
        binding.clause_id.startswith("clause_") for binding in artifact.source_bindings
    )
    assert isinstance(make_engine(repo), PromptEngine)


def test_a_delegated_path_without_a_candidate_table_is_unsupported(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    )
    from visual_intent_agent.prompt_engine import engine as engine_module

    monkeypatch.setattr(engine_module, "DELEGATED_CANDIDATES", {})
    with pytest.raises(PromptCompilationError) as excinfo:
        make_engine(repo).compile(_request(seeded))
    assert excinfo.value.code == PROMPT_UNSUPPORTED_REQUIREMENT
    assert artifact_count(repo, "prompt_artifacts") == 0


def test_new_ids_use_the_frozen_prefixes():
    from visual_intent_agent.prompt_engine.engine import (
        PROMPT_ARTIFACT_ID_PREFIX,
        REALIZATION_ID_PREFIX,
    )

    assert PROMPT_ARTIFACT_ID_PREFIX == "pra"
    assert REALIZATION_ID_PREFIX == "rlz"
    assert new_id(PROMPT_ARTIFACT_ID_PREFIX).startswith("pra_")


def test_artifact_is_fully_traceable_to_intent_confirmation_and_realization(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo,
        intent_with(
            {"subject.description": "a cat", "style.primary": "photorealistic"},
            delegated=[LIGHTING],
        ),
    )

    artifact = make_engine(repo).compile(_request(seeded))

    # Intent + Confirmation：ID 精确绑定且可从 Repository 读回。
    assert (
        repo.get_intent_revision(artifact.based_on_intent_revision_id).session_id
        == seeded.session_id
    )
    confirmation = repo.get_confirmation(artifact.based_on_confirmation_id)
    assert confirmation.confirmation_id == seeded.confirmation_id
    assert confirmation.intent_revision_id == artifact.based_on_intent_revision_id
    assert repo.is_confirmation_valid(artifact.based_on_confirmation_id) is True

    # 每个 binding 都指向真实存在的 Intent 值或 RealizationState。
    intent = repo.get_intent_revision(artifact.based_on_intent_revision_id).intent
    current_state = repo.get_current_realization_state(seeded.session_id)
    for binding in artifact.source_bindings:
        assert binding.intent_path is not None
        if binding.source_kind == "intent":
            assert read_intent_path(intent, binding.intent_path) is not None
            assert binding.realization_id is None
        else:
            assert binding.source_kind in {"delegation", "realization"}
            assert binding.realization_id == current_state.artifact_id
    assert artifact.realization_refs == [current_state.artifact_id]
