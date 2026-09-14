"""Step 09 P3 端到端：多轮局部修改闭环（5 轮：4 次修改 + accept）。

全离线：多轮 fixture（`tests/fixtures/multiturn/`）+ `FakeLLMProvider` +
`FakeImageProvider` + `tmp_path` SQLite / 输出目录；零网络、零真实凭据。

测量口径（任务书「两层 Preservation」）：

- **Intent Preservation（代码保证）**：每轮只允许 applied delta 命中的路径变化；
  其余路径逐值不变（`assert_only_authorized_value_changes`）；
- **Image Preservation（只测量）**：记录每次 GenerationArtifact 的模型/参数/输出字节
  sha256，并断言 Artifact 可追溯；**不**承诺新图片在视觉上与原图一致
  （离线 Fake 字节恒定只是测量可复现，不是视觉结论）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from visual_intent_agent.domain import DeltaOperation
from visual_intent_agent.feedback import FeedbackDecision
from visual_intent_agent.generation import (
    GENERATION_NO_VALID_CONFIRMATION,
    GenerationError,
)
from visual_intent_agent.persistence import WorkflowState
from visual_intent_agent.prompt_engine import PromptArtifact
from visual_intent_agent.workflow.confirmation import WorkflowError
from visual_intent_agent.workflow.review import (
    REVIEW_GENERATION_MISMATCH,
    REVIEW_GENERATION_NOT_FOUND,
    ReviewError,
)

from feedback_helpers import (
    artifact_count,
    artifact_counts,
    artifact_payloads,
    confirm_and_generate,
    current_intent,
    current_realization,
    feedback_response,
    generation_measurement,
    intent_values,
    intent_with,
    make_p3_session,
    revise_entry,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "multiturn"
HISTORY_TABLES = (
    "intent_revisions",
    "prompt_artifacts",
    "generation_artifacts",
    "feedback_results",
    "realization_states",
)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _intent_from(fixture: dict):
    spec = fixture["intent"]
    return intent_with(spec["values"], delegated=spec["delegated"], pinned=spec["pinned"])


def _scripts(fixture: dict) -> tuple[list[str], list[str]]:
    feedback = [
        json.dumps(round_["feedback_response"]) for round_ in fixture["rounds"]
    ]
    interpreter = [
        json.dumps(round_["answer"]["interpreter_response"])
        for round_ in fixture["rounds"]
        if "answer" in round_
    ]
    return feedback, interpreter


def _applied_paths(outcome, operation: DeltaOperation) -> list[str]:
    assert outcome.resolution is not None
    return [
        delta.path
        for delta in outcome.resolution.applied_deltas
        if delta.operation is operation
    ]


def _unauthorized_value_change_count(before, after, authorized_paths) -> int:
    """状态层测量点：未授权路径中出现值变化的条数（预期恒为 0）。"""
    return sum(
        1
        for path in before
        if path not in authorized_paths and after[path] != before[path]
    )


def _assert_only_authorized_value_changes(before, after, authorized_paths):
    """状态层不变量：未授权路径的值逐值不变（README 不变量 4）。"""
    assert set(before) == set(after)
    assert _unauthorized_value_change_count(before, after, authorized_paths) == 0
    for path in before:
        if path in authorized_paths:
            continue
        assert after[path] == before[path], f"unauthorized change on {path!r}"


def _image_layer_attribute_diff(previous: dict, current: dict) -> dict[str, tuple]:
    """图片层测量点：相邻两次 GenerationArtifact 的属性差异（只测量，不承诺视觉一致）。"""
    keys = (
        "generation_id",
        "prompt_artifact_id",
        "target_model",
        "model_version",
        "size",
        "seed",
        "outputs",
    )
    return {key: (previous[key], current[key]) for key in keys if previous[key] != current[key]}


def _prompt_of(repo, generation) -> PromptArtifact:
    return PromptArtifact.model_validate_json(
        repo.get_prompt_artifact(generation.prompt_artifact_id).payload
    )


def _binding_for(prompt_artifact: PromptArtifact, path: str):
    matches = [b for b in prompt_artifact.source_bindings if b.intent_path == path]
    assert len(matches) == 1, (path, [b.intent_path for b in prompt_artifact.source_bindings])
    return matches[0]


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["p3_multiturn_v1.json", "p3_carry_cases_v1.json"])
def test_multiturn_fixtures_are_versioned_and_round_trip_stable(name):
    path = FIXTURES / name
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["fixture_version"] == "v1"
    assert json.loads(json.dumps(data, ensure_ascii=False)) == data
    assert json.dumps(data, sort_keys=True) == json.dumps(
        json.loads(json.dumps(data, ensure_ascii=False)), sort_keys=True
    )
    # fixture 中禁止真实凭据与真实临时 URL
    assert "sk-" not in raw
    assert "http://" not in raw and "https://" not in raw
    assert "Signature=" not in raw and "Expires=" not in raw


# ---------------------------------------------------------------------------
# 5 轮完整闭环
# ---------------------------------------------------------------------------


def test_p3_five_round_loop(tmp_path):
    fixture = _load_fixture("p3_multiturn_v1.json")
    feedback_script, interpreter_script = _scripts(fixture)
    session = make_p3_session(
        tmp_path,
        intent=_intent_from(fixture),
        feedback_responses=feedback_script,
        interpreter_responses=interpreter_script,
    )
    repo = session.repo
    session_id = session.session_id

    assert session.generation is not None
    assert repo.get_current_session_snapshot(session_id).workflow_state is WorkflowState.WAITING_REVIEW

    generations = [session.generation]
    measurements = [generation_measurement(session.generation)]
    attribute_diffs: list[dict] = []
    baseline_location_realization = _prompt_of(repo, session.generation)
    location_binding_first = _binding_for(baseline_location_realization, "environment.location")

    def _measure_new_generation(artifact) -> None:
        generations.append(artifact)
        measurements.append(generation_measurement(artifact))
        attribute_diffs.append(
            _image_layer_attribute_diff(measurements[-2], measurements[-1])
        )

    for round_ in fixture["rounds"]:
        expect = round_["expect"]
        snapshot_before = repo.get_current_session_snapshot(session_id)
        values_before = intent_values(current_intent(repo, session_id))
        counts_before = artifact_counts(repo)
        history_before = {table: artifact_payloads(repo, table) for table in HISTORY_TABLES}
        reviewed_generation = generations[-1]

        outcome = session.review.submit_feedback(
            session_id, reviewed_generation.generation_id, round_["text"]
        )

        # 反馈精确绑定本轮被复核的 GenerationArtifact
        assert outcome.feedback.decision.value == expect["decision"]
        assert outcome.feedback.generation_id == reviewed_generation.generation_id
        assert outcome.generation_id == reviewed_generation.generation_id
        assert outcome.feedback.intent_revision_id == snapshot_before.current_intent_revision_id
        assert outcome.message_id in repo.get_current_session_snapshot(session_id).message_ids
        stored_feedback = artifact_payloads(repo, "feedback_results")
        assert stored_feedback[outcome.feedback.feedback_id] == outcome.feedback.model_dump_json()
        assert artifact_count(repo, "feedback_results") == counts_before["feedback_results"] + 1

        decision = outcome.feedback.decision

        if decision is FeedbackDecision.ACCEPT:
            assert outcome.snapshot.workflow_state is WorkflowState.COMPLETED
            assert outcome.recoverable_failure is False
            assert artifact_count(repo, "generation_artifacts") == counts_before[
                "generation_artifacts"
            ]
            assert outcome.feedback.candidate_deltas == []
        elif decision is FeedbackDecision.CLARIFY:
            assert expect.get("intent_unchanged") is True
            assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CLARIFICATION
            assert outcome.pending_question is not None
            assert outcome.pending_question.target_path == expect["clarify_path"]
            assert outcome.pending_question.allow_custom is True
            # 模糊反馈绝不自动 SET：Intent 与 revision 计数都没变
            assert intent_values(current_intent(repo, session_id)) == values_before
            assert artifact_counts(repo)["intent_revisions"] == counts_before["intent_revisions"]
            assert artifact_counts(repo)["generation_artifacts"] == counts_before[
                "generation_artifacts"
            ]

            answer = round_["answer"]
            realization_before_answer = current_realization(repo, session_id)
            submitted = session.workflow.submit_message(session_id, answer["text"])
            assert submitted.snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
            assert submitted.pending_question is None
            authorized = {delta.path for delta in submitted.resolution.applied_deltas}
            _assert_only_authorized_value_changes(
                values_before, intent_values(current_intent(repo, session_id)), authorized
            )
            # Rev.3 工单 D（`architecture_decision_003.md`）：clarify 回答经
            # `submit_message` 落新 IntentRevision 后同样执行 evaluate_carry —— 回答改写了
            # Environment Decision 的 dependency 路径（environment.mode），因此
            # environment.location 的 Realization 显式失效并落**新** state。
            assert realization_before_answer is not None
            answer_state = current_realization(repo, session_id)
            assert answer_state is not None
            assert answer_state.realization_id != realization_before_answer.realization_id
            assert (
                answer_state.based_on_intent_revision_id
                == submitted.snapshot.current_intent_revision_id
            )
            invalidated_location = next(
                value
                for value in answer_state.values
                if value.path == "environment.location"
            )
            assert invalidated_location.status == "invalidated"
            assert invalidated_location.invalidated_reason == "decision_dependency_changed"
            assert invalidated_location.invalidated_at is not None
            _measure_new_generation(confirm_and_generate(session))
            assert repo.get_current_session_snapshot(session_id).workflow_state is WorkflowState.WAITING_REVIEW
            # generation #4：失效后由 PromptEngine 重新实现（compile 落新 state），
            # binding 不再指向被失效的旧 Realization（`source_kind="delegation"`）。
            answer_binding = _binding_for(
                _prompt_of(repo, generations[-1]), "environment.location"
            )
            realized_state = current_realization(repo, session_id)
            assert realized_state is not None
            assert answer_binding.source_kind == "delegation"
            assert answer_binding.realization_id == realized_state.realization_id
            assert answer_binding.realization_id != location_binding_first.realization_id
            assert answer_binding.realization_id != answer_state.realization_id
            replacement = realized_state.active_value_for("environment.location")
            assert replacement is not None
            # 新实现由本次 compile 首次产生（旧值对象不被复用；文本可能取到同一候选）
            assert replacement.first_prompt_artifact_id == generations[-1].prompt_artifact_id
            assert (
                replacement.first_prompt_artifact_id
                != invalidated_location.first_prompt_artifact_id
            )
            assert replacement is not invalidated_location
        else:
            assert decision is FeedbackDecision.REVISE
            assert outcome.recoverable_failure is False
            assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
            assert outcome.confirmation_summary is not None
            assert _applied_paths(outcome, DeltaOperation.SET) == expect["changed_paths"]
            assert sorted(_applied_paths(outcome, DeltaOperation.PIN)) == sorted(
                expect.get("pinned_paths", [])
            )
            assert outcome.feedback.preserve_paths == expect.get("preserve_paths", [])
            assert outcome.carry is not None
            assert outcome.carry.carried_paths() == expect.get("carried_realizations", [])
            assert outcome.carry.invalidated_paths() == expect.get(
                "invalidated_realizations", []
            )

            authorized = {delta.path for delta in outcome.resolution.applied_deltas}
            _assert_only_authorized_value_changes(
                values_before, intent_values(current_intent(repo, session_id)), authorized
            )

            # 重要修改后旧 Confirmation 天然失效：必须先重新确认才能再生成。
            if snapshot_before.latest_confirmation_id is not None:
                assert repo.is_confirmation_valid(snapshot_before.latest_confirmation_id) is False
            with pytest.raises(GenerationError) as excinfo:
                session.pipeline.generate(session_id)
            assert excinfo.value.code == GENERATION_NO_VALID_CONFIRMATION
            assert repo.get_current_session_snapshot(session_id).workflow_state is WorkflowState.WAITING_CONFIRMATION

            _measure_new_generation(confirm_and_generate(session))
            assert repo.get_current_session_snapshot(session_id).workflow_state is WorkflowState.WAITING_REVIEW

        # 历史不可覆盖：本轮之前存在的每一行 payload 逐字节不变（append-only）
        for table, before_rows in history_before.items():
            after_rows = artifact_payloads(repo, table)
            for artifact_id, payload in before_rows.items():
                assert after_rows[artifact_id] == payload, (table, artifact_id)

    # 最终状态与轮数
    assert repo.get_current_session_snapshot(session_id).workflow_state is WorkflowState.COMPLETED
    assert len(generations) == 5
    assert len(measurements) == 5

    # ---- append-only 终局计数（Rev.3 工单 D 后实测；见 step_06_patch_001.md） ----
    # realization_states 由 4 变为 6：第 3 轮 clarify 回答 +1（carry 失效落新 state）、
    # generation #4 重新实现 +1（compile 落新 state）。
    final_counts = artifact_counts(repo)
    assert final_counts == {
        "intent_revisions": 5,
        "prompt_artifacts": 5,
        "generation_artifacts": 5,
        "feedback_results": 5,
        "realization_states": 6,
    }

    # ---- 状态层：3～5 轮后未修改路径仍保持（Intent Preservation） ----
    final_values = intent_values(current_intent(repo, session_id))
    assert final_values["subject.description"] == "a cat"
    assert final_values["style.primary"] == "photorealistic"
    assert final_values["subject.pose_action"] == "sitting"
    assert final_values["composition.framing"] == "wide_shot"
    assert final_values["lighting.character"] == "dramatic"
    assert final_values["environment.mode"] == "outdoor"
    # 第 1 轮的显式保持（PIN subject.*）跨全部后续轮次保持
    assert current_intent(repo, session_id).pinned_paths == frozenset(
        {"subject.description", "subject.pose_action"}
    )

    # ---- delegated choice：未失效时稳定继承 ----
    # 第 1 轮（只改镜头）环境地点 Realization 原样复用：binding 仍是 realization 且文本不变
    second_prompt = _prompt_of(repo, generations[1])
    assert _binding_for(second_prompt, "environment.location").text == location_binding_first.text
    assert _binding_for(second_prompt, "environment.location").source_kind == "realization"
    # 第 2 轮（只改光线）刻意不失效环境地点：仍然复用同一取值
    third_prompt = _prompt_of(repo, generations[2])
    assert _binding_for(third_prompt, "environment.location").text == location_binding_first.text
    # 第 2 轮修改 delegated lighting：旧 lighting Realization 显式失效，改为 Intent 值
    assert _binding_for(third_prompt, "lighting.character").source_kind == "intent"
    assert _binding_for(third_prompt, "lighting.character").text.endswith("dramatic")

    # ---- 图片层：只测量，不承诺视觉一致 ----
    for measurement in measurements:
        assert measurement["target_model"] == session.generation.target_model
        assert measurement["size"] == session.generation.parameters.size
        assert measurement["seed"] is None
        assert measurement["outputs"]
        assert all(len(output["sha256"]) == 64 for output in measurement["outputs"])
    assert len({m["generation_id"] for m in measurements}) == 5
    assert len({m["prompt_artifact_id"] for m in measurements}) == 5
    # 每轮都产生新的 generation / prompt artifact；模型与参数面保持不变（测量点）
    assert len(attribute_diffs) == 4
    for diff in attribute_diffs:
        assert set(diff) <= {"generation_id", "prompt_artifact_id", "outputs"}
        assert "generation_id" in diff and "prompt_artifact_id" in diff
    # FakeImageProvider 字节确定 → 输出 sha256 在离线测量中不变；
    # 这是"测量可复现"，**不是**任何"图片视觉一致"的结论（任务书两层 Preservation）。
    assert len({m["outputs"][0]["sha256"] for m in measurements}) == 1
    assert len({m["model_version"] for m in measurements}) == 1
    assert len({m["size"] for m in measurements}) == 1
    assert len({m["seed"] for m in measurements}) == 1


def test_every_modification_requires_a_fresh_confirmation_and_a_new_generation(tmp_path):
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[revise_entry("composition.framing", value="wide_shot")],
            )
        ],
    )
    first_generation = session.generation
    outcome = session.review.submit_feedback(
        session.session_id, first_generation.generation_id, "pull the camera back"
    )
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
    assert outcome.confirmation_summary is not None

    with pytest.raises(GenerationError):
        session.pipeline.generate(session.session_id)

    second = confirm_and_generate(session)
    assert second.generation_id != first_generation.generation_id
    assert second.prompt_artifact_id != first_generation.prompt_artifact_id
    assert session.repo.get_current_session_snapshot(session.session_id).workflow_state is WorkflowState.WAITING_REVIEW


# ---------------------------------------------------------------------------
# 硬门禁与可恢复失败
# ---------------------------------------------------------------------------


def test_feedback_is_only_accepted_in_waiting_review(tmp_path):
    session = make_p3_session(tmp_path, feedback_responses=[feedback_response("accept")])
    session.repo.transition_state(session.session_id, WorkflowState.COMPLETED)
    with pytest.raises(WorkflowError) as excinfo:
        session.review.submit_feedback(session.session_id, session.generation.generation_id, "done")
    assert excinfo.value.code == "workflow.invalid_state"


def test_unknown_or_empty_generation_id_is_rejected(tmp_path):
    session = make_p3_session(tmp_path)
    with pytest.raises(ReviewError) as excinfo:
        session.review.submit_feedback(session.session_id, "gen_missing", "pull back")
    assert excinfo.value.code == REVIEW_GENERATION_NOT_FOUND
    with pytest.raises(ReviewError):
        session.review.submit_feedback(session.session_id, "  ", "pull back")


def test_feedback_must_bind_the_generation_currently_under_review(tmp_path):
    fixture = _load_fixture("p3_multiturn_v1.json")
    first_round = fixture["rounds"][0]
    session = make_p3_session(
        tmp_path,
        intent=_intent_from(fixture),
        feedback_responses=[
            json.dumps(first_round["feedback_response"]),
            feedback_response("accept"),
        ],
    )
    stale_generation = session.generation.generation_id
    session.review.submit_feedback(session.session_id, stale_generation, first_round["text"])
    fresh_generation = confirm_and_generate(session)

    # 旧 generation 不再是被复核对象（又是"上一轮的图片"）
    with pytest.raises(ReviewError) as excinfo:
        session.review.submit_feedback(session.session_id, stale_generation, "change the lighting")
    assert excinfo.value.code == REVIEW_GENERATION_MISMATCH

    outcome = session.review.submit_feedback(
        session.session_id, fresh_generation.generation_id, "I am happy with this"
    )
    assert outcome.feedback.decision is FeedbackDecision.ACCEPT
    assert outcome.feedback.generation_id == fresh_generation.generation_id


def test_recoverable_failure_leaves_the_review_state_untouched(tmp_path):
    session = make_p3_session(
        tmp_path, feedback_responses=["this is not json"]
    )
    before = artifact_counts(session.repo)
    before_values = intent_values(current_intent(session.repo, session.session_id))

    outcome = session.review.submit_feedback(
        session.session_id, session.generation.generation_id, "hmm"
    )

    assert outcome.recoverable_failure is True
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_REVIEW
    assert outcome.pending_question is None
    assert outcome.resolution is None
    assert outcome.feedback.decision is FeedbackDecision.CLARIFY
    assert outcome.feedback.issues[0].code.startswith("feedback.unparseable_output")
    after = artifact_counts(session.repo)
    assert after["intent_revisions"] == before["intent_revisions"]
    assert after["prompt_artifacts"] == before["prompt_artifacts"]
    assert after["generation_artifacts"] == before["generation_artifacts"]
    assert after["realization_states"] == before["realization_states"]
    # 反馈本身仍落库（可追溯），但状态层未授权字段变化为零
    assert after["feedback_results"] == before["feedback_results"] + 1
    assert intent_values(current_intent(session.repo, session.session_id)) == before_values


def test_rejected_deltas_do_not_create_an_empty_revision(tmp_path):
    # LLM 提议一个白名单外路径 → Validator 拒绝 → 不产生 revision、不改变状态
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[revise_entry("subject.clothing", value="red coat")],
            )
        ],
    )
    before = artifact_counts(session.repo)
    outcome = session.review.submit_feedback(
        session.session_id, session.generation.generation_id, "give her a red coat"
    )

    assert outcome.recoverable_failure is True
    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_REVIEW
    assert outcome.resolution is not None
    assert outcome.resolution.applied_deltas == []
    assert any(issue.code == "validation.path_not_whitelisted" for issue in outcome.resolution.issues)
    assert artifact_counts(session.repo)["intent_revisions"] == before["intent_revisions"]


def test_clarify_never_sets_a_location_value(tmp_path):
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "clarify",
                clarify_path="environment.mode",
                clarify_reason="unspecified background",
            )
        ],
    )
    before_values = intent_values(current_intent(session.repo, session.session_id))
    outcome = session.review.submit_feedback(
        session.session_id, session.generation.generation_id, "the background is not good"
    )
    after_values = intent_values(current_intent(session.repo, session.session_id))

    assert outcome.snapshot.workflow_state is WorkflowState.WAITING_CLARIFICATION
    assert outcome.pending_question is not None
    assert outcome.pending_question.target_path == "environment.mode"
    assert after_values == before_values
    assert after_values["environment.location"] is None
    # 没有任何"换成海边"式的猜测值
    assert "beach" not in outcome.feedback.model_dump_json().lower()


# ---------------------------------------------------------------------------
# Realization 失效链路（含 PromptEngine 复用）
# ---------------------------------------------------------------------------


def test_dependency_change_invalidates_the_realization_before_recompiling(tmp_path):
    session = make_p3_session(
        tmp_path,
        feedback_responses=[
            feedback_response(
                "revise",
                candidate_deltas=[revise_entry("environment.mode", value="outdoor")],
            )
        ],
    )
    repo = session.repo
    session_id = session.session_id
    state_before = current_realization(repo, session_id)
    assert state_before is not None
    location_before = state_before.active_value_for("environment.location")
    assert location_before is not None
    first_binding = _binding_for(_prompt_of(repo, session.generation), "environment.location")
    # 首次编译没有历史 Realization → 这是一次新选择（delegation + 初始 state）
    assert first_binding.source_kind == "delegation"
    assert first_binding.realization_id == state_before.realization_id

    outcome = session.review.submit_feedback(
        session_id, session.generation.generation_id, "make the environment outdoor"
    )

    assert outcome.carry is not None
    assert outcome.carry.invalidated_paths() == ["environment.location"]
    assert outcome.carry.carried_paths() == ["lighting.character"]
    assert outcome.realization_state_id is not None
    state_after = current_realization(repo, session_id)
    assert state_after is not None
    assert state_after.realization_id == outcome.realization_state_id
    invalidated = next(
        value for value in state_after.values if value.path == "environment.location"
    )
    assert invalidated.status == "invalidated"
    assert invalidated.invalidated_reason == "decision_dependency_changed"
    assert invalidated.invalidated_at is not None
    assert invalidated.value == location_before.value
    # 历史 state 不被覆盖：旧 realization_id 仍在表里，payload 逐字节保留
    states = artifact_payloads(repo, "realization_states")
    assert state_before.realization_id in states
    assert state_after.realization_id in states

    new_generation = confirm_and_generate(session)
    new_binding = _binding_for(_prompt_of(repo, new_generation), "environment.location")
    # 失效后重新实现：新选择 + 新 state（PromptEngine 落库），不再复用旧 realization
    assert new_binding.source_kind == "delegation"
    assert new_binding.realization_id != first_binding.realization_id
    assert new_binding.realization_id != state_after.realization_id
