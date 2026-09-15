"""MVP v0.3 Step 01：Gate A Core Dataset 合同测试（全离线）。

校验对象（冻结物，内容哈希固化于本文件与 evaluation/frozen_manifest_v0_3.json）：

- evaluation/protocol.md                    —— 评测协议
- evaluation/fixtures/core_v0_3.jsonl       —— Core Dataset 输入
- evaluation/annotations/core_v0_3.jsonl    —— 人工标注答案
- evaluation/configs/gate_a_v0_3.json       —— 冻结实验配置

覆盖：ID 唯一与格式、字段完整、标注与案例一一对应、路径全部命中 INTENT_PATHS
（只 import `visual_intent_agent.domain.paths`，不维护第二份清单）、操作与
Resolution 使用冻结枚举、多轮引用闭合（澄清回答跟随澄清提问、反馈/接受必须有
在先图片）、场景全覆盖、配置与 ARCHITECTURE.md 第 3 节命名表一致、无明文凭据。

本目录遵循 v0.2 测试惯例：无 conftest.py、不跨测试目录 import、全离线。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from visual_intent_agent.domain.delta import DeltaOperation
from visual_intent_agent.domain.intent import Resolution
from visual_intent_agent.domain.paths import INTENT_PATHS

# ---------------------------------------------------------------------------
# 冻结常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_DIR = PROJECT_ROOT / "evaluation"
FIXTURES_PATH = EVALUATION_DIR / "fixtures" / "core_v0_3.jsonl"
ANNOTATIONS_PATH = EVALUATION_DIR / "annotations" / "core_v0_3.jsonl"
CONFIG_PATH = EVALUATION_DIR / "configs" / "gate_a_v0_3.json"
PROTOCOL_PATH = EVALUATION_DIR / "protocol.md"
MANIFEST_PATH = EVALUATION_DIR / "frozen_manifest_v0_3.json"

DATASET_VERSION = "core_v0_3"

#: 冻结内容哈希（与 evaluation/frozen_manifest_v0_3.json 一致；修改冻结物必须
#: 生成新版本文件，本常量与清单同步换新，旧版本只读保留）。
FROZEN_SHA256: dict[str, str] = {
    "evaluation/protocol.md": "70cc0b3c4bde970a91172191b398cf3869df539fa0971ae85cfd849c52c26d93",
    "evaluation/fixtures/core_v0_3.jsonl": "0519534702225949bede2782516cd1543c936d2fd298834ad36dd253fc63a64b",
    "evaluation/annotations/core_v0_3.jsonl": "d1712aab45c42251d920d22f6a5a6a1fe5bef964f577e2b45aead851dc6a6b57",
    "evaluation/configs/gate_a_v0_3.json": "3b6159f8bc620f6c31fc85fb40a937a7ced1a71296a8c6ab9d0514c0f329905b",
}

CASE_ID_PATTERN = re.compile(r"^s(0[1-9]|10)-[a-z][a-z0-9-]*-\d{3}$")
TURN_ID_PATTERN = re.compile(r"^t\d+$")

SCENARIOS: frozenset[str] = frozenset(
    {
        "single_turn_complete",
        "missing_core_decision",
        "missing_perceptual_decision",
        "conflicting_requirements",
        "explicit_delegation",
        "vague_delegation",
        "single_field_modification",
        "pin_unpin",
        "consecutive_modifications",
        "vague_image_feedback",
    }
)

TURN_KINDS: frozenset[str] = frozenset(
    {"user_message", "clarification_answer", "image_feedback", "accept"}
)

EXPECTED_OUTCOMES: frozenset[str] = frozenset(
    {
        "ready_and_generate",
        "clarification_expected",
        "accepted_completed",
        "no_state_change",
    }
)

VALUE_MATCH_MODES: frozenset[str] = frozenset(
    {"exact_token", "contains_any", "int_equals", "any_non_empty"}
)

FEEDBACK_DECISIONS: frozenset[str] = frozenset({"accept", "revise", "clarify"})

L4_DIMENSIONS: frozenset[str] = frozenset(
    {"intent_alignment", "attribute_preservation", "user_preference", "edit_success"}
)

#: expected_rejections 允许引用的 issue code 命名空间（ARCHITECTURE.md 5.5）。
ISSUE_CODE_NAMESPACES: frozenset[str] = frozenset(
    {
        "validation",
        "policy",
        "persistence",
        "provider",
        "interpreter",
        "workflow",
        "prompt",
        "generation",
        "feedback",
        "config",
    }
)

CONFLICT_RULE_IDS: frozenset[str] = frozenset(
    {
        "hard_conflict.environment_mode_location",
        "hard_conflict.lighting_environment_source",
        "hard_conflict.style_medium_mismatch",
        "execution_conflict.framing_aspect_mismatch",
    }
)

_OPERATIONS = {op.value for op in DeltaOperation}
_RESOLUTIONS = {r.value for r in Resolution}


# ---------------------------------------------------------------------------
# 加载工具
# ---------------------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict]:
    cases: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            assert stripped, f"{path.name}:{line_no} 存在空行"
            obj = json.loads(stripped)
            assert isinstance(obj, dict), f"{path.name}:{line_no} 不是 JSON 对象"
            cases.append(obj)
    return cases


@pytest.fixture(scope="module")
def fixtures() -> list[dict]:
    return _load_jsonl(FIXTURES_PATH)


@pytest.fixture(scope="module")
def annotations() -> list[dict]:
    return _load_jsonl(ANNOTATIONS_PATH)


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _all_delta_like(turn_annotation: dict) -> list[dict]:
    return (
        turn_annotation["expected_deltas"]
        + turn_annotation["acceptable_extra_deltas"]
        + turn_annotation["expected_rejections"]
    )


def _case_produces_image(annotation: dict) -> bool:
    return any(
        ta["expected_outcome"] == "ready_and_generate"
        for ta in annotation["turn_annotations"]
    )


# ---------------------------------------------------------------------------
# 冻结哈希
# ---------------------------------------------------------------------------


class TestFrozenHashes:
    def test_frozen_files_exist(self):
        for rel_path in FROZEN_SHA256:
            assert (PROJECT_ROOT / rel_path).is_file(), f"冻结文件缺失: {rel_path}"
        assert MANIFEST_PATH.is_file()

    @pytest.mark.parametrize("rel_path", sorted(FROZEN_SHA256))
    def test_content_hash_matches_frozen_value(self, rel_path: str):
        digest = hashlib.sha256(
            (PROJECT_ROOT / rel_path).read_bytes()
        ).hexdigest()
        assert digest == FROZEN_SHA256[rel_path], (
            f"{rel_path} 内容已变化（冻结物以哈希标识；修改必须生成新版本文件）"
        )

    def test_manifest_sidecar_is_consistent(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        assert manifest["algorithm"] == "sha256"
        assert manifest["dataset_version"] == DATASET_VERSION
        assert manifest["files"] == FROZEN_SHA256


# ---------------------------------------------------------------------------
# Fixture 结构
# ---------------------------------------------------------------------------


class TestFixtureStructure:
    def test_case_ids_unique_and_well_formed(self, fixtures):
        ids = [c["case_id"] for c in fixtures]
        assert len(ids) == len(set(ids)), "case_id 重复"
        for case_id in ids:
            assert CASE_ID_PATTERN.match(case_id), f"非法 case_id: {case_id}"

    def test_required_fields_and_dataset_version(self, fixtures):
        required = {
            "case_id", "dataset_version", "scenario", "turn_type",
            "description", "tags", "turns",
        }
        for case in fixtures:
            assert required <= set(case), f"{case.get('case_id')} 缺字段"
            assert case["dataset_version"] == DATASET_VERSION
            assert case["scenario"] in SCENARIOS
            assert case["turn_type"] in {"single", "multi"}
            assert case["description"].strip()
            assert isinstance(case["tags"], list)

    def test_turn_structure(self, fixtures):
        for case in fixtures:
            turns = case["turns"]
            expected_ids = [f"t{i}" for i in range(1, len(turns) + 1)]
            assert [t["turn_id"] for t in turns] == expected_ids, case["case_id"]
            for turn in turns:
                assert turn["kind"] in TURN_KINDS, (case["case_id"], turn)
                assert turn["user_text"].strip(), (case["case_id"], turn["turn_id"])
                assert TURN_ID_PATTERN.match(turn["turn_id"])
            assert turns[0]["kind"] == "user_message", (
                f"{case['case_id']} 首轮必须是 user_message"
            )

    def test_turn_type_matches_turn_count(self, fixtures):
        for case in fixtures:
            count = len(case["turns"])
            if case["turn_type"] == "single":
                assert count == 1, case["case_id"]
                assert case["turns"][0]["kind"] == "user_message"
            else:
                assert 3 <= count <= 5, (case["case_id"], count)

    def test_answer_variants_reference_whitelist_paths(self, fixtures):
        for case in fixtures:
            for turn in case["turns"]:
                variants = turn.get("answer_variants")
                if variants is None:
                    continue
                assert turn["kind"] == "clarification_answer"
                for path in variants:
                    assert path in INTENT_PATHS, (case["case_id"], path)


# ---------------------------------------------------------------------------
# 标注与案例一一对应
# ---------------------------------------------------------------------------


class TestAnnotationAlignment:
    def test_same_case_ids_same_order(self, fixtures, annotations):
        assert [c["case_id"] for c in fixtures] == [a["case_id"] for a in annotations]
        assert [c["scenario"] for c in fixtures] == [a["scenario"] for a in annotations]

    def test_turn_annotations_align_with_turns(self, fixtures, annotations):
        for case, annotation in zip(fixtures, annotations):
            fixture_turn_ids = [t["turn_id"] for t in case["turns"]]
            annotation_turn_ids = [ta["turn_id"] for ta in annotation["turn_annotations"]]
            assert annotation_turn_ids == fixture_turn_ids, annotation["case_id"]

    def test_annotation_required_fields(self, annotations):
        required = {
            "turn_id", "expected_deltas", "acceptable_extra_deltas",
            "expected_rejections", "forbidden_change_paths",
            "paths_must_remain_unset", "expected_resolution_records",
            "blocking_missing_paths_after_turn", "must_clarify",
            "must_not_clarify_paths", "acceptable_clarify_paths",
            "expected_conflicts", "expected_feedback_decision",
            "expected_carry", "expected_outcome", "notes",
        }
        for annotation in annotations:
            for ta in annotation["turn_annotations"]:
                assert required <= set(ta), (annotation["case_id"], ta["turn_id"])
                assert ta["expected_outcome"] in EXPECTED_OUTCOMES


# ---------------------------------------------------------------------------
# 冻结词汇：路径 / 操作 / Resolution / value_match
# ---------------------------------------------------------------------------


class TestFrozenVocabulary:
    def test_all_paths_in_whitelist(self, annotations):
        for annotation in annotations:
            case_id = annotation["case_id"]
            for ta in annotation["turn_annotations"]:
                for delta in _all_delta_like(ta):
                    assert delta["path"] in INTENT_PATHS, (case_id, delta)
                for field in (
                    "forbidden_change_paths",
                    "paths_must_remain_unset",
                    "blocking_missing_paths_after_turn",
                    "must_not_clarify_paths",
                    "acceptable_clarify_paths",
                ):
                    for path in ta[field]:
                        assert path in INTENT_PATHS, (case_id, field, path)
                for path in ta["expected_resolution_records"]:
                    assert path in INTENT_PATHS, (case_id, path)
                for entry in ta["must_clarify"]:
                    assert entry["target_path"] in INTENT_PATHS, (case_id, entry)
                carry = ta["expected_carry"]
                if carry is not None:
                    for path in carry["carried"] + carry["invalidated"]:
                        assert path in INTENT_PATHS, (case_id, path)

    def test_operations_and_resolutions_are_frozen_enum_values(self, annotations):
        for annotation in annotations:
            for ta in annotation["turn_annotations"]:
                for delta in _all_delta_like(ta):
                    assert delta["operation"] in _OPERATIONS, delta
                    resolution = delta.get("resolution")
                    assert resolution is None or resolution in _RESOLUTIONS, delta
                for value in ta["expected_resolution_records"].values():
                    assert value in _RESOLUTIONS, value

    def test_delta_shape_matches_operation(self, annotations):
        """镜像 domain.IntentDelta 冻结形状：SET 至少携带 value/resolution 之一。"""
        for annotation in annotations:
            for ta in annotation["turn_annotations"]:
                for delta in ta["expected_deltas"] + ta["acceptable_extra_deltas"]:
                    if delta["operation"] == "SET":
                        assert (
                            delta["value_match"] is not None
                            or delta.get("resolution") is not None
                        ), delta
                    else:
                        assert delta["value_match"] is None, delta
                        assert delta.get("resolution") is None, delta

    def test_value_match_modes(self, annotations):
        for annotation in annotations:
            for ta in annotation["turn_annotations"]:
                for delta in ta["expected_deltas"] + ta["acceptable_extra_deltas"]:
                    vm = delta["value_match"]
                    if vm is None:
                        continue
                    assert vm["mode"] in VALUE_MATCH_MODES, vm
                    if vm["mode"] == "exact_token":
                        assert isinstance(vm["value"], str) and vm["value"]
                    elif vm["mode"] == "contains_any":
                        keywords = vm["keywords"]
                        assert isinstance(keywords, list) and keywords
                        assert all(isinstance(k, str) and k for k in keywords)
                    elif vm["mode"] == "int_equals":
                        assert isinstance(vm["value"], int) and not isinstance(
                            vm["value"], bool
                        )
                        assert delta["path"] == "subject.count", (
                            "int 值只允许出现在 subject.count"
                        )

    def test_expected_rejections_reference_known_issue_namespaces(self, annotations):
        for annotation in annotations:
            for ta in annotation["turn_annotations"]:
                for rejection in ta["expected_rejections"]:
                    codes = rejection["expected_issue_codes"]
                    assert isinstance(codes, list) and codes
                    for code in codes:
                        namespace, _, name = code.partition(".")
                        assert namespace in ISSUE_CODE_NAMESPACES and name, code
                    assert rejection["only_if_emitted"] is True

    def test_expected_conflicts_use_frozen_rule_ids(self, annotations):
        for annotation in annotations:
            for ta in annotation["turn_annotations"]:
                for conflict in ta["expected_conflicts"]:
                    assert conflict["rule_id"] in CONFLICT_RULE_IDS, conflict
                    assert conflict["kind"] in {"hard", "execution"}
                    assert isinstance(conflict["blocking"], bool)


# ---------------------------------------------------------------------------
# 多轮引用闭合与轮次一致性
# ---------------------------------------------------------------------------


class TestMultiTurnClosure:
    def test_clarification_answer_follows_clarification(self, fixtures, annotations):
        for case, annotation in zip(fixtures, annotations):
            outcomes = [ta["expected_outcome"] for ta in annotation["turn_annotations"]]
            for index, turn in enumerate(case["turns"]):
                if turn["kind"] != "clarification_answer":
                    continue
                assert index > 0, (case["case_id"], turn["turn_id"])
                assert outcomes[index - 1] == "clarification_expected", (
                    f"{case['case_id']}:{turn['turn_id']} 回答前一轮必须是澄清提问"
                )

    def test_feedback_and_accept_require_prior_image(self, fixtures, annotations):
        for case, annotation in zip(fixtures, annotations):
            outcomes = [ta["expected_outcome"] for ta in annotation["turn_annotations"]]
            for index, turn in enumerate(case["turns"]):
                if turn["kind"] not in {"image_feedback", "accept"}:
                    continue
                assert "ready_and_generate" in outcomes[:index], (
                    f"{case['case_id']}:{turn['turn_id']} 反馈/接受之前必须已生成图片"
                )

    def test_accept_turns_are_terminal_and_expect_accept_decision(
        self, fixtures, annotations
    ):
        for case, annotation in zip(fixtures, annotations):
            for index, turn in enumerate(case["turns"]):
                if turn["kind"] != "accept":
                    continue
                ta = annotation["turn_annotations"][index]
                assert index == len(case["turns"]) - 1, case["case_id"]
                assert ta["expected_feedback_decision"] == "accept"
                assert ta["expected_outcome"] == "accepted_completed"

    def test_feedback_decision_only_on_feedback_kinds(self, fixtures, annotations):
        for case, annotation in zip(fixtures, annotations):
            for turn, ta in zip(case["turns"], annotation["turn_annotations"]):
                decision = ta["expected_feedback_decision"]
                if turn["kind"] in {"image_feedback", "accept"}:
                    assert decision in FEEDBACK_DECISIONS, (case["case_id"], turn)
                else:
                    assert decision is None, (case["case_id"], turn["turn_id"])

    def test_outcome_consistency_with_clarify_expectations(self, annotations):
        for annotation in annotations:
            for ta in annotation["turn_annotations"]:
                if ta["expected_outcome"] == "clarification_expected":
                    assert ta["must_clarify"], (
                        f"{annotation['case_id']}:{ta['turn_id']} 期望澄清但无目标"
                    )
                if ta["expected_outcome"] == "ready_and_generate":
                    assert not ta["must_clarify"], (
                        f"{annotation['case_id']}:{ta['turn_id']} 就绪轮不得再提问"
                    )

    def test_forbidden_paths_exclude_expected_value_changes(self, annotations):
        for annotation in annotations:
            for ta in annotation["turn_annotations"]:
                value_changing = {
                    d["path"]
                    for d in (
                        ta["expected_deltas"] + ta["acceptable_extra_deltas"]
                    )
                    if d["operation"] in {"SET", "CLEAR"}
                }
                overlap = set(ta["forbidden_change_paths"]) & value_changing
                assert not overlap, (
                    f"{annotation['case_id']}:{ta['turn_id']} "
                    f"forbidden 与期望变更重叠: {sorted(overlap)}"
                )


# ---------------------------------------------------------------------------
# 场景覆盖
# ---------------------------------------------------------------------------


class TestScenarioCoverage:
    def test_all_ten_scenarios_covered(self, fixtures):
        covered = {c["scenario"] for c in fixtures}
        assert covered == SCENARIOS, f"缺失场景: {sorted(SCENARIOS - covered)}"

    def test_each_scenario_has_at_least_two_cases(self, fixtures):
        for scenario in sorted(SCENARIOS):
            count = sum(1 for c in fixtures if c["scenario"] == scenario)
            assert count >= 2, f"{scenario} 只有 {count} 例"

    def test_dataset_size_within_frozen_band(self, fixtures):
        assert 20 <= len(fixtures) <= 30

    def test_single_turn_scenarios_are_single_turn(self, fixtures):
        for case in fixtures:
            if case["scenario"] == "single_turn_complete":
                assert case["turn_type"] == "single"

    def test_consecutive_modifications_have_three_to_five_turns(self, fixtures):
        for case in fixtures:
            if case["scenario"] == "consecutive_modifications":
                assert 3 <= len(case["turns"]) <= 5, case["case_id"]


# ---------------------------------------------------------------------------
# 图片评价维度与 Prompt 期望
# ---------------------------------------------------------------------------


class TestImageDimensionsAndPromptExpectations:
    def test_l4_dimensions_are_frozen_enum(self, annotations):
        for annotation in annotations:
            for dim in annotation["image_evaluation_dimensions"]:
                assert dim["dimension"] in L4_DIMENSIONS, dim
                assert dim["focus"].strip()

    def test_applies_to_turns_reference_existing_turns(self, fixtures, annotations):
        for case, annotation in zip(fixtures, annotations):
            turn_ids = {t["turn_id"] for t in case["turns"]}
            for dim in annotation["image_evaluation_dimensions"]:
                for turn_id in dim.get("applies_to_turns", []):
                    assert turn_id in turn_ids, (case["case_id"], turn_id)

    def test_dimensions_empty_iff_case_produces_no_image(self, annotations):
        for annotation in annotations:
            if _case_produces_image(annotation):
                assert annotation["image_evaluation_dimensions"], annotation["case_id"]
                assert annotation["prompt_expectations"] is not None
            else:
                assert annotation["image_evaluation_dimensions"] == [], (
                    annotation["case_id"]
                )
                assert annotation["prompt_expectations"] is None, annotation["case_id"]

    def test_prompt_expectations_shape(self, annotations):
        for annotation in annotations:
            expectations = annotation["prompt_expectations"]
            if expectations is None:
                continue
            groups = expectations["must_mention_groups"]
            assert isinstance(groups, list) and groups
            for group in groups:
                assert isinstance(group, list) and group
                assert all(isinstance(k, str) and k for k in group)
            assert isinstance(expectations["must_not_mention"], list)


# ---------------------------------------------------------------------------
# 实验配置
# ---------------------------------------------------------------------------


class TestGateAConfig:
    def test_config_version(self, config):
        assert config["config_version"] == "gate_a_v0_3"

    def test_env_var_names_match_architecture_naming_table(self, config):
        """环境变量名必须与 ARCHITECTURE.md 第 3 节命名表逐字一致。"""
        assert config["credentials"]["base_url_env"] == "VIA_PROVIDER_BASE_URL"
        assert config["credentials"]["api_key_env"] == "VIA_PROVIDER_API_KEY"
        assert config["llm"]["model_env"] == "VIA_LLM_MODEL"
        assert config["llm"]["timeout_seconds_env"] == "VIA_LLM_TIMEOUT_SECONDS"
        assert config["image"]["model_env"] == "VIA_IMAGE_MODEL"
        assert config["image"]["timeout_seconds_env"] == "VIA_IMAGE_TIMEOUT_SECONDS"
        assert config["http"]["max_retries_env"] == "VIA_HTTP_MAX_RETRIES"

    def test_frozen_defaults_match_architecture(self, config):
        assert config["llm"]["model_default"] == "qwen3.8-max"
        assert config["image"]["model_default"] == "qwen-image-3.0"
        assert config["llm"]["timeout_seconds_default"] == 60.0
        assert config["image"]["timeout_seconds_default"] == 120.0
        assert config["http"]["max_retries_default"] == 2
        assert config["image"]["size"] == "1024x1024"
        assert config["image"]["images_per_generation"] == 1

    def test_llm_sampling_parameters_are_not_set(self, config):
        """与 System B 冻结调用行为一致：不显式设置 temperature/max_tokens。"""
        assert config["llm"]["temperature"] is None
        assert config["llm"]["max_tokens"] is None

    def test_failure_status_enum(self, config):
        assert config["failure_handling"]["case_result_status_enum"] == [
            "completed",
            "flow_deviation",
            "failed",
        ]

    def test_repetition_policy_frozen(self, config):
        assert config["repetitions"]["llm_layer_runs_per_case"] == 2
        assert config["repetitions"]["image_runs_per_case"] == 1


# ---------------------------------------------------------------------------
# 凭据卫生
# ---------------------------------------------------------------------------


class TestCredentialHygiene:
    """冻结物与测试文件不得包含明文 key（凭据只引用环境变量名）。"""

    _KEY_PATTERNS = (
        re.compile(r"sk-[A-Za-z0-9]{8,}"),
        re.compile(r"Bearer\s+[A-Za-z0-9._-]{10,}"),
    )

    def _files_under_evaluation(self) -> list[Path]:
        return [p for p in EVALUATION_DIR.rglob("*") if p.is_file()]

    def test_no_plaintext_keys_in_evaluation_tree(self):
        for path in self._files_under_evaluation() + [Path(__file__)]:
            text = path.read_text(encoding="utf-8")
            for pattern in self._KEY_PATTERNS:
                assert not pattern.search(text), f"{path} 疑似包含明文凭据"
