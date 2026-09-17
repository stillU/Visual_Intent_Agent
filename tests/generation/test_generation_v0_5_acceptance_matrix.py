"""v0.5 后置收尾 F2：当前可执行验收矩阵（三路径 × 十类，全部离线）。

本模块是 `tests/fixtures/knowledge/v0_5_acceptance_cases.json` 的执行端：每个 case
按 **knowledge_path × scenario** 精确参数化，节点 ID 即
`test_acceptance_case[<path>::<category>]`，不存在"一个通用节点复用凑数"。

覆盖分工：

- `condition_mismatch` 与 `successful_adoption` 复用既有
  `tests/generation/test_generation_v0_5_release_adoption.py` 的**逐规则参数化**节点
  （生产发布快照 + 临时真实 SQLite），本模块不重复实现。
- 其余 8 类（委托 / 明确值 / PIN / 未确认条件 / 模型不符 / 未审核知识 / 冲突 / 缺少知识）
  在本模块逐路径执行。

边界（与任务书 F2 一致）：

- 正例（委托、成功采用）加载**最终生产快照** `knowledge_base/v0.5/`；负例隔离被测原因：
  模型不符用 approved 单元、条件满足；冲突用两个可消费的 approved 单元；未确认 / PIN /
  明确值零检索零 Bundle；未审核知识用 draft 单元并对照 approved 孪生单元。
- 不复制 COCO-CN / GenEval / T2I-CompBench 原文或 ID；全部为项目原创工程用例。
- 无网络、无真实 Provider、无正式评测。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from generation_helpers import (
    FAKE_IMAGE_MODEL,
    intent_with,
    make_repo,
    seed_confirmed_session,
)
from tests.knowledge.knowledge_helpers import load_test_corpus, unit_payload

from visual_intent_agent.knowledge import (
    AdoptionOutcome,
    BundleStatus,
    KnowledgeBundle,
    LocalKnowledgeEngine,
    RetrievalOutcome,
    load_corpus,
)
from visual_intent_agent.prompt_engine import (
    PromptCompileRequest,
    PromptEngine,
    QwenImageRenderer,
)
from visual_intent_agent.prompt_engine.engine import RENDER_LABELS, select_delegated_value
from visual_intent_agent.realization.models import (
    REALIZATION_STATUS_ACTIVE,
    RealizationState,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = PROJECT_ROOT / "tests" / "fixtures" / "knowledge" / "v0_5_acceptance_cases.json"
RELEASE_DIR = PROJECT_ROOT / "knowledge_base" / "v0.5"
RELEASE_MANIFEST = RELEASE_DIR / "manifest.json"

MODEL = FAKE_IMAGE_MODEL
CORPUS_VERSION = "v0.5-approved-1"
REUSED_TEST_MODULE = "tests/generation/test_generation_v0_5_release_adoption.py"
REUSED_POSITIVE = "test_each_approved_rule_is_reachable_and_actually_adopted"
REUSED_NEGATIVE = "test_conditions_that_do_not_hold_fall_back_to_the_deterministic_baseline"

MATRIX_CATEGORIES: tuple[str, ...] = (
    "delegated",
    "explicit_value",
    "pinned",
    "unconfirmed_condition",
    "model_mismatch",
    "unapproved_knowledge",
    "conflict",
    "missing_knowledge",
)
REUSED_CATEGORIES: tuple[str, ...] = ("condition_mismatch", "successful_adoption")
ALL_CATEGORIES: tuple[str, ...] = (
    "delegated",
    "explicit_value",
    "pinned",
    "unconfirmed_condition",
    "condition_mismatch",
    "model_mismatch",
    "unapproved_knowledge",
    "conflict",
    "missing_knowledge",
    "successful_adoption",
)

#: 三路径的正例上下文、候选、生产单元 ID 与合成夹具参数（项目原创，非数据集派生）。
PATH_SCENARIOS: dict[str, dict[str, object]] = {
    "camera.depth_of_field": {
        "positive_intent": {
            "composition.framing": "close_up",
            "style.primary": "cinematic",
            "subject.description": "a cat",
        },
        "candidate": "shallow",
        "unit_id": "camera.depth_of_field.shallow_for_close_up",
        "explicit_value": "deep",
        "conflict_candidates": ("shallow", "deep"),
        "fixture_keywords": ("close_up", "cinematic", "cat", "depth of field"),
        "positive_conditions": [
            {"path": "composition.framing", "operator": "equals", "values": ["close_up"]},
            {"path": "style.primary", "operator": "equals", "values": ["cinematic"]},
        ],
        "reused_param": "camera.depth_of_field.shallow_for_close_up",
    },
    "composition.framing": {
        "positive_intent": {
            "subject.pose_action": "standing",
            "environment.mode": "studio",
            "subject.description": "a cat",
        },
        "candidate": "medium_shot",
        "unit_id": "composition.framing.medium_shot_for_standing_pose",
        "explicit_value": "close_up",
        "conflict_candidates": ("medium_shot", "close_up"),
        "fixture_keywords": ("standing", "studio", "medium shot", "cat"),
        "positive_conditions": [
            {"path": "subject.pose_action", "operator": "in", "values": ["standing", "walking"]},
            {"path": "environment.mode", "operator": "equals", "values": ["studio"]},
        ],
        "reused_param": "composition.framing.medium_shot_for_standing_pose",
    },
    "lighting.character": {
        "positive_intent": {
            "composition.framing": "close_up",
            "environment.mode": "studio",
            "subject.description": "a cat",
        },
        "candidate": "soft",
        "unit_id": "lighting.character.soft_for_tight_framing",
        "explicit_value": "dramatic",
        "conflict_candidates": ("soft", "dramatic"),
        "fixture_keywords": ("soft light", "close_up", "studio", "cat"),
        "positive_conditions": [
            {
                "path": "composition.framing",
                "operator": "in",
                "values": ["close_up", "medium_shot"],
            },
            {"path": "environment.mode", "operator": "in", "values": ["studio", "indoor"]},
        ],
        "reused_param": "lighting.character.soft_for_tight_framing",
    },
}
PATHS: tuple[str, ...] = tuple(PATH_SCENARIOS)


def _positive_intent(path: str) -> dict[str, object]:
    return dict(PATH_SCENARIOS[path]["positive_intent"])  # type: ignore[arg-type]


def _matrix_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for path in PATHS:
        positive = _positive_intent(path)
        explicit = dict(positive)
        explicit[path] = PATH_SCENARIOS[path]["explicit_value"]
        unconfirmed = {"subject.description": positive["subject.description"]}
        cases.extend(
            [
                {
                    "case_id": f"{path}::delegated",
                    "knowledge_path": path,
                    "category": "delegated",
                    "scenario": {"intent_values": positive, "delegated": [path], "pinned": []},
                },
                {
                    "case_id": f"{path}::explicit_value",
                    "knowledge_path": path,
                    "category": "explicit_value",
                    "scenario": {
                        "intent_values": explicit,
                        "delegated": [],
                        "pinned": [],
                        "explicit_value": PATH_SCENARIOS[path]["explicit_value"],
                    },
                },
                {
                    "case_id": f"{path}::pinned",
                    "knowledge_path": path,
                    "category": "pinned",
                    "scenario": {
                        "intent_values": {"subject.description": "a cat"},
                        "delegated": [path],
                        "pinned": [path],
                    },
                },
                {
                    "case_id": f"{path}::unconfirmed_condition",
                    "knowledge_path": path,
                    "category": "unconfirmed_condition",
                    "scenario": {
                        "intent_values": unconfirmed,
                        "delegated": [],
                        "pinned": [],
                    },
                },
                {
                    "case_id": f"{path}::model_mismatch",
                    "knowledge_path": path,
                    "category": "model_mismatch",
                    "scenario": {
                        "intent_values": positive,
                        "delegated": [path],
                        "pinned": [],
                        "unit_target_models": ["qwen-image-2.0"],
                    },
                },
                {
                    "case_id": f"{path}::unapproved_knowledge",
                    "knowledge_path": path,
                    "category": "unapproved_knowledge",
                    "scenario": {"intent_values": positive, "delegated": [path], "pinned": []},
                },
                {
                    "case_id": f"{path}::conflict",
                    "knowledge_path": path,
                    "category": "conflict",
                    "scenario": {"intent_values": positive, "delegated": [path], "pinned": []},
                },
                {
                    "case_id": f"{path}::missing_knowledge",
                    "knowledge_path": path,
                    "category": "missing_knowledge",
                    "scenario": {"intent_values": positive, "delegated": [path], "pinned": []},
                },
            ]
        )
    return cases


MATRIX_CASES: list[dict[str, object]] = _matrix_cases()


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


class _Recording:
    """记录检索请求（证明明确值 / PIN / 未确认零检索）。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.requests: list[object] = []

    def retrieve(self, request):  # noqa: ANN001 - 测试包装器
        self.requests.append(request)
        return self.inner.retrieve(request)


def _intent(case: dict) -> object:
    scenario = case["scenario"]
    return intent_with(
        scenario.get("intent_values", {}),
        delegated=scenario.get("delegated", []),
        pinned=scenario.get("pinned", []),
    )


def _compile(tmp_path, case: dict, engine, name: str):
    repo = make_repo(tmp_path, name)
    seeded = seed_confirmed_session(repo, _intent(case))
    prompt_engine = PromptEngine(QwenImageRenderer(), repo, knowledge_engine=engine)
    artifact = prompt_engine.compile(
        PromptCompileRequest(session_id=seeded.session_id, confirmation_id=seeded.confirmation_id)
    )
    return repo, seeded, artifact


def _active_value(repo, session_id: str, path: str):
    record = repo.get_current_realization_state(session_id)
    if record is None:
        return None
    state = RealizationState.model_validate_json(record.payload)
    return next(
        (
            value
            for value in state.values
            if value.path == path and value.status == REALIZATION_STATUS_ACTIVE
        ),
        None,
    )


def _only_bundle(repo, session_id: str) -> KnowledgeBundle:
    stored = repo.list_knowledge_bundles(session_id)
    assert len(stored) == 1
    return KnowledgeBundle.model_validate_json(stored[0].payload)


def _fallback(path: str, seeded) -> str:
    return select_delegated_value(path, seeded.intent_revision_id)


def _fixture_unit(
    path: str,
    candidate: str,
    *,
    knowledge_id: str | None = None,
    review_status: str = "approved",
    target_models: tuple[str, ...] = ("any",),
) -> dict:
    approved = review_status == "approved"
    return unit_payload(
        knowledge_id=knowledge_id or f"{path}.acceptance-fixture",
        applicable_path=path,
        candidate_value=candidate,
        content="acceptance engineering fixture " + " ".join(PATH_SCENARIOS[path]["fixture_keywords"]),
        version="1",
        keywords=tuple(PATH_SCENARIOS[path]["fixture_keywords"]),
        aliases=(),
        conditions=PATH_SCENARIOS[path]["positive_conditions"],
        target_models=target_models,
        review_status=review_status,
        reviewer="fixture-reviewer（测试夹具，非真实人工审核）" if approved else None,
        reviewed_at="2026-09-16T00:00:00+00:00" if approved else None,
    )


def _rejection_reasons(result) -> dict[str, str]:
    return {item.knowledge_id: item.reason_code.value for item in result.rejections}


# ---------------------------------------------------------------------------
# 逐类执行器
# ---------------------------------------------------------------------------


def _run_delegated(tmp_path, case: dict) -> None:
    path = case["knowledge_path"]
    recording = _Recording(LocalKnowledgeEngine(corpus=load_corpus(RELEASE_DIR)))
    repo, seeded, _ = _compile(tmp_path, case, recording, "delegated.db")

    assert len(recording.requests) == 1
    value = _active_value(repo, seeded.session_id, path)
    assert value is not None
    assert value.value == PATH_SCENARIOS[path]["candidate"]
    assert value.knowledge_unit_id == PATH_SCENARIOS[path]["unit_id"]
    assert value.knowledge_unit_version == "2"
    assert value.knowledge_bundle_id is not None

    bundle = _only_bundle(repo, seeded.session_id)
    assert bundle.status is BundleStatus.OK
    assert bundle.corpus_version == CORPUS_VERSION
    result = bundle.result_for(path)
    assert result is not None and result.outcome is RetrievalOutcome.ADOPTED
    decision = bundle.decision_for(path)
    assert decision is not None and decision.outcome is AdoptionOutcome.ADOPTED


def _run_zero_retrieval(tmp_path, case: dict, name: str) -> tuple:
    path = case["knowledge_path"]
    recording = _Recording(LocalKnowledgeEngine(corpus=load_corpus(RELEASE_DIR)))
    repo, seeded, artifact = _compile(tmp_path, case, recording, name)
    assert recording.requests == []
    assert repo.list_knowledge_bundles(seeded.session_id) == []
    assert artifact.knowledge_bundle_refs == []
    return path, repo, seeded, artifact


def _run_explicit_value(tmp_path, case: dict) -> None:
    path, repo, seeded, artifact = _run_zero_retrieval(tmp_path, case, "explicit.db")
    expected = case["scenario"]["explicit_value"]
    # 显式值进入编译产物；知识既不改写它，也不为它建 Bundle / 追踪三元组。
    assert expected in artifact.prompt
    value = _active_value(repo, seeded.session_id, path)
    if value is not None:
        assert value.value == expected
        assert value.knowledge_bundle_id is None
        assert value.knowledge_unit_id is None


def _run_pinned(tmp_path, case: dict) -> None:
    path, repo, seeded, _ = _run_zero_retrieval(tmp_path, case, "pinned.db")
    value = _active_value(repo, seeded.session_id, path)
    assert value is not None
    assert value.value == _fallback(path, seeded)
    assert value.knowledge_bundle_id is None
    assert value.knowledge_unit_id is None


def _run_unconfirmed_condition(tmp_path, case: dict) -> None:
    path, repo, seeded, artifact = _run_zero_retrieval(tmp_path, case, "unconfirmed.db")
    assert _active_value(repo, seeded.session_id, path) is None
    assert f"{RENDER_LABELS[path]}:" not in artifact.prompt


def _run_model_mismatch(tmp_path, case: dict) -> None:
    path = case["knowledge_path"]
    target_models = tuple(case["scenario"]["unit_target_models"])
    unit = _fixture_unit(path, PATH_SCENARIOS[path]["candidate"], target_models=target_models)
    corpus = load_test_corpus(tmp_path / "mm_corpus", [unit])
    recording = _Recording(LocalKnowledgeEngine(corpus=corpus))
    repo, seeded, _ = _compile(tmp_path, case, recording, "mm.db")

    value = _active_value(repo, seeded.session_id, path)
    assert value is not None and value.value == _fallback(path, seeded)
    assert value.knowledge_bundle_id is None

    bundle = _only_bundle(repo, seeded.session_id)
    result = bundle.result_for(path)
    assert result is not None and result.outcome is RetrievalOutcome.NO_HIT
    assert _rejection_reasons(result).get(f"{path}.acceptance-fixture") == "model_mismatch"

    # 隔离：同一 approved 单元、条件满足且可检索，只把目标模型改为 any 即被采用。
    twin_unit = _fixture_unit(
        path,
        PATH_SCENARIOS[path]["candidate"],
        knowledge_id=f"{path}.acceptance-fixture-twin",
    )
    twin_corpus = load_test_corpus(tmp_path / "mm_twin_corpus", [twin_unit])
    twin_repo, twin_seeded, _ = _compile(
        tmp_path, case, _Recording(LocalKnowledgeEngine(corpus=twin_corpus)), "mm_twin.db"
    )
    twin_value = _active_value(twin_repo, twin_seeded.session_id, path)
    assert twin_value is not None
    assert twin_value.value == PATH_SCENARIOS[path]["candidate"]
    assert twin_value.knowledge_unit_id == f"{path}.acceptance-fixture-twin"


def _run_unapproved_knowledge(tmp_path, case: dict) -> None:
    path = case["knowledge_path"]
    fixture_id = f"{path}.acceptance-fixture"
    draft = _fixture_unit(
        path, PATH_SCENARIOS[path]["candidate"], review_status="draft"
    )
    corpus = load_test_corpus(tmp_path / "draft_corpus", [draft])
    # 夹具确实包含这条单元，但它只是 draft：语料构建阶段就把它挡在候选之外。
    assert [unit.knowledge_id for unit in corpus.all_units] == [fixture_id]
    assert corpus.build_units() == ()

    recording = _Recording(LocalKnowledgeEngine(corpus=corpus))
    repo, seeded, _ = _compile(tmp_path, case, recording, "draft.db")

    # 检索路径确实被查询过：不是"没查"，而是"查了但没有可消费的 approved 单元"。
    assert len(recording.requests) == 1
    value = _active_value(repo, seeded.session_id, path)
    assert value is not None and value.value == _fallback(path, seeded)
    assert value.knowledge_bundle_id is None
    assert value.knowledge_unit_id is None

    # 真实合同：draft 因未审核在语料门被过滤（`build_units()` 为空），检索得到语料级
    # 回退，没有 path 级 result / 推荐 / 采用裁定，也绝不被采用。
    bundle = _only_bundle(repo, seeded.session_id)
    assert bundle.status is BundleStatus.NO_APPROVED_UNITS
    assert bundle.result_for(path) is None
    assert bundle.recommendation_for(path) is None
    assert bundle.decision_for(path) is None
    assert bundle.recommendations == ()

    # 隔离：同字段 approved 孪生单元被采用，证明唯一差别是审核状态。
    twin = _fixture_unit(
        path,
        PATH_SCENARIOS[path]["candidate"],
        knowledge_id=f"{path}.acceptance-fixture-twin",
        review_status="approved",
    )
    twin_corpus = load_test_corpus(tmp_path / "approved_twin_corpus", [twin])
    twin_repo, twin_seeded, _ = _compile(
        tmp_path, case, _Recording(LocalKnowledgeEngine(corpus=twin_corpus)), "twin.db"
    )
    twin_value = _active_value(twin_repo, twin_seeded.session_id, path)
    assert twin_value is not None and twin_value.value == PATH_SCENARIOS[path]["candidate"]


def _run_conflict(tmp_path, case: dict) -> None:
    path = case["knowledge_path"]
    first, second = PATH_SCENARIOS[path]["conflict_candidates"]
    units = [
        _fixture_unit(path, first, knowledge_id=f"{path}.conflict.a"),
        _fixture_unit(path, second, knowledge_id=f"{path}.conflict.b"),
    ]
    corpus = load_test_corpus(tmp_path / "conflict_corpus", units)
    recording = _Recording(LocalKnowledgeEngine(corpus=corpus))
    repo, seeded, _ = _compile(tmp_path, case, recording, "conflict.db")

    value = _active_value(repo, seeded.session_id, path)
    assert value is not None and value.value == _fallback(path, seeded)
    assert value.knowledge_bundle_id is None

    bundle = _only_bundle(repo, seeded.session_id)
    result = bundle.result_for(path)
    assert result is not None and result.outcome is RetrievalOutcome.AMBIGUOUS
    # 两个 approved 可消费单元同分不同候选：并列回退，绝不强行采用。
    assert bundle.recommendations == ()
    reasons = _rejection_reasons(result)
    assert reasons.get(f"{path}.conflict.a") == "tie_ambiguous"
    assert reasons.get(f"{path}.conflict.b") == "tie_ambiguous"


def _run_missing_knowledge(tmp_path, case: dict) -> None:
    path = case["knowledge_path"]
    # 语料本身可构建：放一条**属于其他 target path** 的 approved 单元。因此
    # `build_units()` 非空、Bundle 可用（status=ok），只有本次委托的目标 path 没有任何
    # 适用单元 —— 这才是"缺少知识"，与"全语料无 approved"（unapproved_knowledge）不同。
    other_path = next(candidate for candidate in PATHS if candidate != path)
    other_unit = _fixture_unit(other_path, PATH_SCENARIOS[other_path]["candidate"])
    corpus = load_test_corpus(tmp_path / "missing_corpus", [other_unit])
    assert [unit.knowledge_id for unit in corpus.build_units()] == [other_unit["knowledge_id"]]
    assert not any(unit.applicable_path == path for unit in corpus.build_units())

    recording = _Recording(LocalKnowledgeEngine(corpus=corpus))
    repo, seeded, _ = _compile(tmp_path, case, recording, "missing.db")
    assert len(recording.requests) == 1  # 目标 path 确实被检索过

    value = _active_value(repo, seeded.session_id, path)
    assert value is not None and value.value == _fallback(path, seeded)
    assert value.knowledge_bundle_id is None
    assert value.knowledge_unit_id is None

    bundle = _only_bundle(repo, seeded.session_id)
    assert bundle.status is BundleStatus.OK  # 语料可用；缺的是目标 path 的单元
    assert bundle.is_fallback is True
    assert bundle.recommendations == ()
    result = bundle.result_for(path)
    assert result is not None
    assert result.outcome is RetrievalOutcome.NO_HIT
    assert result.reason_code == "no_hit"
    assert result.hits == ()
    assert result.rejections == ()
    assert bundle.recommendation_for(path) is None
    assert bundle.decision_for(path) is None

    # 对照：同一目标 path 加上一条 approved 可消费单元后即被采用，证明缺失是唯一原因。
    control_unit = _fixture_unit(
        path,
        PATH_SCENARIOS[path]["candidate"],
        knowledge_id=f"{path}.acceptance-fixture-control",
    )
    control_corpus = load_test_corpus(tmp_path / "control_corpus", [control_unit])
    control_repo, control_seeded, _ = _compile(
        tmp_path, case, _Recording(LocalKnowledgeEngine(corpus=control_corpus)), "control.db"
    )
    control_value = _active_value(control_repo, control_seeded.session_id, path)
    assert control_value is not None
    assert control_value.value == PATH_SCENARIOS[path]["candidate"]
    assert control_value.knowledge_unit_id == f"{path}.acceptance-fixture-control"
    control_bundle = _only_bundle(control_repo, control_seeded.session_id)
    assert control_bundle.status is BundleStatus.OK
    control_result = control_bundle.result_for(path)
    assert control_result is not None and control_result.outcome is RetrievalOutcome.ADOPTED
    assert control_bundle.recommendation_for(path) is not None
    assert control_bundle.decision_for(path) is not None


_RUNNERS = {
    "delegated": _run_delegated,
    "explicit_value": _run_explicit_value,
    "pinned": _run_pinned,
    "unconfirmed_condition": _run_unconfirmed_condition,
    "model_mismatch": _run_model_mismatch,
    "unapproved_knowledge": _run_unapproved_knowledge,
    "conflict": _run_conflict,
    "missing_knowledge": _run_missing_knowledge,
}


@pytest.mark.parametrize("case", MATRIX_CASES, ids=[str(c["case_id"]) for c in MATRIX_CASES])
def test_acceptance_case(tmp_path, case: dict):
    _RUNNERS[case["category"]](tmp_path, case)


# ---------------------------------------------------------------------------
# 清单校验：三路径 × 十类完整、绑定最终语料、映射节点真实存在
# ---------------------------------------------------------------------------


def _load_cases_doc() -> dict:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _reused_cases(doc: dict) -> list[dict]:
    return [case for case in doc["cases"] if case["executor"] == "reused"]


def test_acceptance_manifest_binds_the_final_release_and_covers_3x10():
    doc = _load_cases_doc()
    assert doc["schema"] == "v0.5-acceptance-cases.v1"
    assert doc["corpus_version"] == CORPUS_VERSION
    assert doc["corpus_dir"] == "knowledge_base/v0.5"
    assert tuple(doc["paths"]) == PATHS
    assert tuple(doc["categories"]) == ALL_CATEGORIES
    assert "none" in doc["dataset_derivation"].lower()
    assert doc["corpus_manifest_sha256"] == _sha256(RELEASE_MANIFEST)

    cases = doc["cases"]
    assert len(cases) == 30
    combos = {(case["knowledge_path"], case["category"]) for case in cases}
    assert combos == {(path, category) for path in PATHS for category in ALL_CATEGORIES}
    assert len({case["case_id"] for case in cases}) == 30
    # 每个 case 映射到**唯一**节点，禁止多 case 共用同一通用节点。
    assert len({case["test_node_id"] for case in cases}) == 30
    for case in cases:
        assert case["derived_from_dataset"] is False
        assert case["expected"]["outcome"] in {
            "adopted",
            "not_adopted",
            "rejected",
            "not_overwritten",
        }
        assert case["precondition"]
        assert case["fixture_type"]

    # 矩阵执行端与清单一一对应（无漂移）。
    assert {case["case_id"] for case in cases if case["executor"] == "matrix"} == {
        str(case["case_id"]) for case in MATRIX_CASES
    }
    assert {case["category"] for case in cases if case["executor"] == "matrix"} == set(
        MATRIX_CATEGORIES
    )
    assert {case["category"] for case in _reused_cases(doc)} == set(REUSED_CATEGORIES)


def test_acceptance_manifest_expectations_match_the_release_corpus():
    doc = _load_cases_doc()
    corpus = load_corpus(RELEASE_DIR)
    units = {unit.knowledge_id: unit for unit in corpus.build_units()}

    for case in doc["cases"]:
        expected = case["expected"]
        unit_id = expected.get("knowledge_unit_id")
        if case["category"] in {"delegated", "successful_adoption"}:
            unit = units[unit_id]
            assert unit.applicable_path == case["knowledge_path"]
            assert unit.candidate_value == expected["candidate_value"]
            assert unit.version == "2"
        elif case["category"] == "condition_mismatch":
            assert expected["reason_code"] == "conditions_not_satisfied"
        elif case["category"] == "model_mismatch":
            assert expected["reason_code"] == "model_mismatch"
        elif case["category"] == "unapproved_knowledge":
            # draft 在语料构建阶段被过滤 ⇒ 语料级 no_approved_units 回退
            # （不是 path 级 review_not_approved 拒绝；后者只存在于消费端伪造快照合同）。
            assert expected["reason_code"] == "no_approved_units"
        elif case["category"] == "conflict":
            assert expected["reason_code"] == "tie_ambiguous"
        elif case["category"] == "missing_knowledge":
            # 语料可构建（status=ok），但目标委托 path 无适用单元 ⇒ path 级 no_hit 回退。
            assert expected["reason_code"] == "no_hit"


def test_acceptance_manifest_isolates_each_negative_reason_code():
    """每个负例类别必须给出**各自**的真实原因码，不能两类共用同一结果掩盖隔离。"""
    doc = _load_cases_doc()
    by_category = {
        case["category"]: case["expected"]["reason_code"] for case in doc["cases"]
    }
    expected = {
        "delegated": None,
        "explicit_value": "explicit_value_preserved",
        "pinned": "path_pinned",
        "unconfirmed_condition": "path_not_confirmed",
        "condition_mismatch": "conditions_not_satisfied",
        "model_mismatch": "model_mismatch",
        # 全语料无 approved（corpus 级回退）vs 语料健康但目标 path 无单元（path 级回退）
        # 是两个不同的真实原因码，必须分别出现在 unapproved_knowledge 与 missing_knowledge。
        "unapproved_knowledge": "no_approved_units",
        "missing_knowledge": "no_hit",
        "conflict": "tie_ambiguous",
        "successful_adoption": None,
    }
    assert by_category == expected
    # 负例原因码两两不同：不存在"换个名字但结果与另一类完全相同"的伪隔离。
    negative = {
        category: code
        for category, code in by_category.items()
        if category not in {"delegated", "successful_adoption"}
    }
    assert len(set(negative.values())) == len(negative)
    # fixture 类型也必须能区分这两类同语料级/路径级回退。
    fixture_types = {
        case["category"]: case["fixture_type"]
        for case in doc["cases"]
        if case["category"] in {"unapproved_knowledge", "missing_knowledge"}
    }
    assert fixture_types == {
        "unapproved_knowledge": "synthetic_draft_and_approved_fixture",
        "missing_knowledge": "synthetic_other_path_approved_fixture",
    }


def test_acceptance_manifest_node_ids_actually_exist():
    """映射目标必须真实存在（收集两个模块的节点 ID 并逐条核对）。"""
    doc = _load_cases_doc()
    modules = sorted({case["test_node_id"].split("::", 1)[0] for case in doc["cases"]})
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *modules],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    collected = {
        line.strip()
        for line in proc.stdout.splitlines()
        if "::" in line and line.strip().startswith("tests/")
    }
    missing = sorted(case["test_node_id"] for case in doc["cases"] if case["test_node_id"] not in collected)
    assert not missing, f"acceptance case node ids not collected: {missing}"


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
