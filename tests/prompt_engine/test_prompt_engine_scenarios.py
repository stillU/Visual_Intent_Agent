"""Step 07 任务书「必测场景」8 条（逐条显式映射）。

    1. 无有效确认时拒绝编译；
    2. 已确认 Intent 的每个重要 clause 有来源；
    3. unspecified + omit 字段不会被具体化；
    4. delegated lighting 可被实现，但不能顺带决定 color；
    5. 已有 Realization 被稳定复用；
    6. Intent revision 变化后旧 PromptArtifact 不被覆盖；
    7. Renderer 只处理一个目标模型（见 test_prompt_renderer.py 与
       test_prompt_engine_compile.py::test_compile_rejects_a_non_supported_target_model）；
    8. 人为插入无 source clause 时编译失败。
"""

from __future__ import annotations

import pytest

from prompt_engine_helpers import (
    artifact_count,
    artifact_counts,
    append_intent,
    intent_with,
    make_engine,
    make_repo,
    save_confirmation_for_current,
    seed_confirmed_session,
)

from visual_intent_agent.domain import INTENT_PATHS, new_id
from visual_intent_agent.prompt_engine import (
    PROMPT_NO_VALID_CONFIRMATION,
    PROMPT_UNAUTHORIZED_ADDITION,
    PromptArtifact,
    PromptCompilationError,
    PromptCompileRequest,
    PromptEngine,
)
from visual_intent_agent.prompt_engine.engine import (
    CLAUSE_ORDER,
    DELEGATED_CANDIDATES,
    bind_clause,
    check_source_bindings,
    read_intent_path,
    resolved_important_paths,
    select_delegated_value,
)
from visual_intent_agent.prompt_engine.spec import CompilationClause, CompilationSpec
from visual_intent_agent.realization.models import RealizationState, RealizationValue

LIGHTING = "lighting.character"


def _compile(repo, session_id: str, confirmation_id: str) -> PromptArtifact:
    return make_engine(repo).compile(
        PromptCompileRequest(session_id=session_id, confirmation_id=confirmation_id)
    )


# ---------------------------------------------------------------------------
# 场景 1：无有效确认时拒绝编译
# ---------------------------------------------------------------------------


def test_scenario_01_compile_without_any_confirmation_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}), confirmed=False
    )

    with pytest.raises(PromptCompilationError) as excinfo:
        _compile(repo, seeded.session_id, "cnf_does_not_exist")

    assert excinfo.value.code == PROMPT_NO_VALID_CONFIRMATION
    assert artifact_counts(repo) == {
        "prompt_artifacts": 0,
        "generation_artifacts": 0,
        "feedback_results": 0,
        "realization_states": 0,
    }


def test_scenario_01_empty_confirmation_id_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(repo, intent_with({"subject.description": "a cat"}))

    with pytest.raises(PromptCompilationError) as excinfo:
        _compile(repo, seeded.session_id, "   ")

    assert excinfo.value.code == PROMPT_NO_VALID_CONFIRMATION
    assert artifact_count(repo, "prompt_artifacts") == 0


def test_scenario_01_confirmation_of_another_session_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    first = seed_confirmed_session(repo, intent_with({"subject.description": "a cat"}))
    second = seed_confirmed_session(repo, intent_with({"subject.description": "a dog"}))

    with pytest.raises(PromptCompilationError) as excinfo:
        _compile(repo, second.session_id, first.confirmation_id)

    assert excinfo.value.code == PROMPT_NO_VALID_CONFIRMATION
    assert artifact_count(repo, "prompt_artifacts") == 0


def test_scenario_01_invalidated_confirmation_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo, intent_with({"subject.description": "a cat"}), confirmed=True
    )
    append_intent(repo, seeded.session_id, intent_with({"subject.description": "a dog"}))
    assert repo.is_confirmation_valid(seeded.confirmation_id) is False

    with pytest.raises(PromptCompilationError) as excinfo:
        _compile(repo, seeded.session_id, seeded.confirmation_id)

    assert excinfo.value.code == PROMPT_NO_VALID_CONFIRMATION
    assert artifact_count(repo, "prompt_artifacts") == 0


# ---------------------------------------------------------------------------
# 场景 2：已确认 Intent 的每个重要 clause 有来源（覆盖率 100%）
# ---------------------------------------------------------------------------


def test_scenario_02_every_important_clause_has_a_source_binding(tmp_path):
    repo = make_repo(tmp_path)
    intent = intent_with(
        {
            "subject.description": "a cat",
            "subject.count": 2,
            "composition.framing": "medium_shot",
            "environment.mode": "studio",
            "environment.location": "a wooden table",
            "style.primary": "photorealistic",
            "style.description": "shot on film",
            "camera.angle": "eye_level",
            "camera.depth_of_field": "shallow",
            "color.palette": "warm",
        },
        delegated=[LIGHTING],
    )
    seeded = seed_confirmed_session(repo, intent)

    artifact = _compile(repo, seeded.session_id, seeded.confirmation_id)
    expected_paths = resolved_important_paths(intent)

    assert len(expected_paths) == 11
    assert [binding.intent_path for binding in artifact.source_bindings] == expected_paths
    for binding in artifact.source_bindings:
        assert binding.intent_path or binding.rule_id or binding.realization_id
        assert binding.text in artifact.prompt
    covered = {binding.intent_path for binding in artifact.source_bindings}
    assert covered == set(expected_paths)
    assert len(covered) / len(expected_paths) == 1.0


def test_scenario_02_bindings_record_the_right_source_kind(tmp_path):
    repo = make_repo(tmp_path)
    intent = intent_with(
        {"subject.description": "a cat", "style.primary": "photorealistic"},
        delegated=[LIGHTING],
    )
    seeded = seed_confirmed_session(repo, intent)

    artifact = _compile(repo, seeded.session_id, seeded.confirmation_id)
    kinds = {binding.intent_path: binding.source_kind for binding in artifact.source_bindings}

    assert kinds["subject.description"] == "intent"
    assert kinds["style.primary"] == "intent"
    assert kinds[LIGHTING] == "delegation"
    assert artifact.realization_refs == [
        next(b.realization_id for b in artifact.source_bindings if b.intent_path == LIGHTING)
    ]


# ---------------------------------------------------------------------------
# 场景 3：unspecified + omit 字段不会被具体化
# ---------------------------------------------------------------------------


def test_scenario_03_unspecified_and_omitted_fields_are_never_concretized(tmp_path):
    repo = make_repo(tmp_path)
    intent = intent_with(
        {
            "subject.description": "a cat",
            "style.primary": "photorealistic",
            "environment.mode": "studio",
            "environment.location": "a wooden table",
        },
        not_applicable=["color.palette"],
    )
    seeded = seed_confirmed_session(repo, intent)

    artifact = _compile(repo, seeded.session_id, seeded.confirmation_id)

    assert artifact.prompt == (
        "subject: a cat, environment: studio, location: a wooden table, "
        "style: photorealistic"
    )
    assert {binding.intent_path for binding in artifact.source_bindings} == {
        "subject.description",
        "environment.mode",
        "environment.location",
        "style.primary",
    }
    for untouched in (
        "color",
        "lighting",
        "camera",
        "framing",
        "pose",
        "subject count",
        "style detail",
    ):
        assert untouched not in artifact.prompt
    # unspecified ≠ delegated：没有任何路径被误当成委托，也没有产生 Realization。
    assert artifact.realization_refs == []
    assert artifact_count(repo, "realization_states") == 0
    assert all(binding.source_kind != "delegation" for binding in artifact.source_bindings)


def test_scenario_03_not_applicable_is_not_a_source(tmp_path):
    repo = make_repo(tmp_path)
    intent = intent_with(
        {"subject.description": "a cat"}, not_applicable=["color.palette", "camera.angle"]
    )
    seeded = seed_confirmed_session(repo, intent)

    artifact = _compile(repo, seeded.session_id, seeded.confirmation_id)

    assert "color.palette" not in {b.intent_path for b in artifact.source_bindings}
    assert "camera.angle" not in {b.intent_path for b in artifact.source_bindings}
    assert artifact.realization_refs == []


# ---------------------------------------------------------------------------
# 场景 4：delegated lighting 可被实现，但不能顺带决定 color
# ---------------------------------------------------------------------------


def test_scenario_04_delegated_lighting_is_implemented_but_color_is_not_decided(tmp_path):
    repo = make_repo(tmp_path)
    intent = intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    seeded = seed_confirmed_session(repo, intent)

    artifact = _compile(repo, seeded.session_id, seeded.confirmation_id)
    lighting = next(b for b in artifact.source_bindings if b.intent_path == LIGHTING)

    assert lighting.source_kind == "delegation"
    assert lighting.realization_id is not None
    assert lighting.text in artifact.prompt
    assert "color" not in artifact.prompt
    assert "color.palette" not in {b.intent_path for b in artifact.source_bindings}

    state = repo.get_current_realization_state(seeded.session_id)
    assert state is not None
    parsed = RealizationState.model_validate_json(state.payload)
    assert [value.path for value in parsed.values] == [LIGHTING]


def test_scenario_04_only_the_delegated_path_is_decided(tmp_path):
    repo = make_repo(tmp_path)
    intent = intent_with(
        {"subject.description": "a cat"}, delegated=[LIGHTING, "color.palette"]
    )
    seeded = seed_confirmed_session(repo, intent)

    artifact = _compile(repo, seeded.session_id, seeded.confirmation_id)
    paths = {b.intent_path for b in artifact.source_bindings}

    assert paths == {"subject.description", LIGHTING, "color.palette"}
    assert all(
        binding.source_kind == "delegation"
        for binding in artifact.source_bindings
        if binding.intent_path != "subject.description"
    )


def test_delegated_choice_is_deterministic_and_within_the_candidate_table():
    seed = "irev_fixed"
    for path, candidates in DELEGATED_CANDIDATES.items():
        first = select_delegated_value(path, seed)
        assert first == select_delegated_value(path, seed)
        assert first in candidates


# ---------------------------------------------------------------------------
# 场景 5：已有 Realization 被稳定复用
# ---------------------------------------------------------------------------


def test_scenario_05_recompiling_reuses_the_same_realization(tmp_path):
    repo = make_repo(tmp_path)
    intent = intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    seeded = seed_confirmed_session(repo, intent)

    first = _compile(repo, seeded.session_id, seeded.confirmation_id)
    second = _compile(repo, seeded.session_id, seeded.confirmation_id)

    assert first.prompt_artifact_id != second.prompt_artifact_id  # append-only
    assert first.prompt == second.prompt
    assert first.realization_refs == second.realization_refs
    assert len(first.realization_refs) == 1
    assert artifact_count(repo, "realization_states") == 1

    stored = repo.get_current_realization_state(seeded.session_id)
    parsed = RealizationState.model_validate_json(stored.payload)
    (value,) = parsed.values
    assert value.value == select_delegated_value(LIGHTING, seeded.intent_revision_id)
    assert value.first_prompt_artifact_id == first.prompt_artifact_id  # 首次产生者不变


def test_scenario_05_existing_active_realization_wins_over_reselection(tmp_path):
    repo = make_repo(tmp_path)
    intent = intent_with({"subject.description": "a cat"}, delegated=[LIGHTING])
    seeded = seed_confirmed_session(repo, intent)

    default_choice = select_delegated_value(LIGHTING, seeded.intent_revision_id)
    alternate = next(
        candidate
        for candidate in DELEGATED_CANDIDATES[LIGHTING]
        if candidate != default_choice
    )
    manual = RealizationState(
        realization_id=new_id("rlz"),
        session_id=seeded.session_id,
        based_on_intent_revision_id=seeded.intent_revision_id,
        values=[
            RealizationValue(
                path=LIGHTING,
                value=alternate,
                source="user_delegated",
                first_prompt_artifact_id="pra_manual",
            )
        ],
    )
    repo.append_realization_state(
        manual.realization_id,
        seeded.session_id,
        {"based_on_intent_revision_id": seeded.intent_revision_id},
        manual.model_dump_json(),
    )

    artifact = _compile(repo, seeded.session_id, seeded.confirmation_id)
    lighting = next(b for b in artifact.source_bindings if b.intent_path == LIGHTING)

    assert f"lighting: {alternate}" in artifact.prompt
    assert f"lighting: {default_choice}" not in artifact.prompt
    assert lighting.source_kind == "realization"
    assert lighting.realization_id == manual.realization_id
    assert artifact.realization_refs == [manual.realization_id]
    assert artifact_count(repo, "realization_states") == 1  # 纯复用不写新 state


# ---------------------------------------------------------------------------
# 场景 6：Intent revision 变化后旧 PromptArtifact 不被覆盖
# ---------------------------------------------------------------------------


def test_scenario_06_old_prompt_artifact_is_not_overwritten_after_revision_change(tmp_path):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(
        repo,
        intent_with(
            {"subject.description": "a cat", "style.primary": "photorealistic"},
            delegated=[LIGHTING],
        ),
    )
    first = _compile(repo, seeded.session_id, seeded.confirmation_id)

    changed = intent_with(
        {"subject.description": "a cat", "style.primary": "cinematic"}, delegated=[LIGHTING]
    )
    revision = append_intent(repo, seeded.session_id, changed)
    record = save_confirmation_for_current(repo, seeded.session_id)

    assert repo.is_confirmation_valid(seeded.confirmation_id) is False
    second = _compile(repo, seeded.session_id, record.confirmation_id)

    assert second.prompt_artifact_id != first.prompt_artifact_id
    assert first.based_on_intent_revision_id == seeded.intent_revision_id
    assert second.based_on_intent_revision_id == revision.intent_revision_id
    assert "style: photorealistic" in first.prompt
    assert "style: cinematic" in second.prompt
    assert artifact_count(repo, "prompt_artifacts") == 2

    stored_first = repo.get_prompt_artifact(first.prompt_artifact_id)
    assert stored_first.payload == first.model_dump_json()
    assert PromptArtifact.model_validate_json(stored_first.payload) == first
    stored_second = repo.get_prompt_artifact(second.prompt_artifact_id)
    assert PromptArtifact.model_validate_json(stored_second.payload) == second


# ---------------------------------------------------------------------------
# 场景 8：人为插入无 source clause 时编译失败
# ---------------------------------------------------------------------------


def test_scenario_08_a_clause_without_any_source_is_rejected():
    spec = CompilationSpec(
        session_id="ses_1",
        target_model="qwen-image-3.0",
        clauses=[
            CompilationClause(
                clause_id="clause_bogus",
                text="masterpiece, best quality, 8k",
                source_kind=None,
            )
        ],
    )

    with pytest.raises(PromptCompilationError) as excinfo:
        check_source_bindings(spec)

    assert excinfo.value.code == PROMPT_UNAUTHORIZED_ADDITION


def test_scenario_08_injected_clause_fails_compilation_before_any_write(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    seeded = seed_confirmed_session(repo, intent_with({"subject.description": "a cat"}))
    engine = make_engine(repo)
    original = PromptEngine._build_compilation_spec

    def tampered(self, **kwargs):
        spec = original(self, **kwargs)
        return spec.model_copy(
            update={
                "clauses": [
                    *spec.clauses,
                    CompilationClause(
                        clause_id="clause_bogus",
                        text="masterpiece, best quality",
                        source_kind=None,
                    ),
                ]
            }
        )

    monkeypatch.setattr(PromptEngine, "_build_compilation_spec", tampered)

    with pytest.raises(PromptCompilationError) as excinfo:
        engine.compile(
            PromptCompileRequest(
                session_id=seeded.session_id, confirmation_id=seeded.confirmation_id
            )
        )

    assert excinfo.value.code == PROMPT_UNAUTHORIZED_ADDITION
    assert artifact_counts(repo) == {
        "prompt_artifacts": 0,
        "generation_artifacts": 0,
        "feedback_results": 0,
        "realization_states": 0,
    }


def test_scenario_08_malformed_binding_is_reported_as_missing_source_binding():
    bad_intent_clause = CompilationClause(
        clause_id="clause_bad", text="color palette: warm", source_kind="intent"
    )
    with pytest.raises(PromptCompilationError) as excinfo:
        bind_clause(bad_intent_clause)
    assert excinfo.value.code == "prompt.missing_source_binding"

    unknown_kind = CompilationClause(
        clause_id="clause_bad",
        text="color palette: warm",
        source_kind="model_best_practice",
        intent_path="color.palette",
    )
    with pytest.raises(PromptCompilationError) as excinfo:
        bind_clause(unknown_kind)
    assert excinfo.value.code == PROMPT_UNAUTHORIZED_ADDITION


def test_runtime_clause_is_bound_by_rule_id_only():
    binding = bind_clause(
        CompilationClause(
            clause_id="clause_runtime",
            text="aspect: square",
            source_kind="runtime",
            rule_id="render.aspect_ratio",
        )
    )
    assert binding.rule_id == "render.aspect_ratio"
    assert binding.intent_path is None
    assert binding.realization_id is None


# ---------------------------------------------------------------------------
# 公共不变量
# ---------------------------------------------------------------------------


def test_clause_order_is_exactly_the_frozen_whitelist():
    assert set(CLAUSE_ORDER) == INTENT_PATHS
    assert len(CLAUSE_ORDER) == len(INTENT_PATHS)


def test_read_intent_path_reads_values_and_missing_as_none():
    intent = intent_with({"subject.description": "a cat", "subject.count": 3})
    assert read_intent_path(intent, "subject.description") == "a cat"
    assert read_intent_path(intent, "subject.count") == 3
    assert read_intent_path(intent, "color.palette") is None
