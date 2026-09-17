"""v0.6 Step 01：B/C 预注册候选集与冻结清单的离线校验测试。

被测对象是 Step 01 交付物（`evaluation/v0_6/**`）与只读校验工具
`tools/validate_v0_6_cases.py`。全部离线、只读真实冻结物；负例只在临时副本上进行，
不改真实文件、不触网、不调用 Provider。

覆盖：

- 30 个独立上下文的 18/6/6 分层、三路径分配与五条规则覆盖；
- 候选集与开发池（v0.5 验收清单 / 既有 evaluation 冻结数据 / 生成测试）的逐字分离；
- `real_run_authorized=false`、预算与阈值待确认、模型身份未核实、无明文凭据；
- 冻结清单四角色哈希与知识语料哈希一致，篡改与缺失必须被拒绝；
- 逐 case 的检索期望与冻结语料 `LocalKnowledgeEngine` 复算一致；
- L1 的确定性回退与知识候选值可分离（合法 revision seed 存在）。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evaluation.manifest import ManifestError, load_manifest
from tools.validate_v0_6_cases import (
    ANNOTATIONS_REL,
    CASES_REL,
    CONFIG_REL,
    MANIFEST_REL,
    PROTOCOL_REL,
    _development_pool_texts,
    check_annotations_structure,
    check_cases_structure,
    check_dev_pool_separation,
    check_manifest,
    check_retrieval,
    check_run_config,
    check_seed_policy_is_candidate_agnostic,
    load_json,
    load_jsonl,
    validate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def report() -> dict:
    return validate(PROJECT_ROOT)


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return load_jsonl(PROJECT_ROOT / CASES_REL)


@pytest.fixture(scope="module")
def annotations() -> list[dict]:
    return load_jsonl(PROJECT_ROOT / ANNOTATIONS_REL)


@pytest.fixture(scope="module")
def config() -> dict:
    return load_json(PROJECT_ROOT / CONFIG_REL)


# ---------------------------------------------------------------------------
# 交付物存在与整体校验
# ---------------------------------------------------------------------------


class TestArtifacts:
    def test_all_step_01_files_exist(self) -> None:
        for rel in (PROTOCOL_REL, CONFIG_REL, CASES_REL, ANNOTATIONS_REL, MANIFEST_REL):
            assert (PROJECT_ROOT / rel).is_file(), rel

    def test_full_validation_passes(self, report: dict) -> None:
        assert report["problems"] == []
        assert report["ok"] is True
        assert report["cases"] == 30
        assert report["annotations"] == 30


class TestStructure:
    def test_layers_are_18_6_6(self, cases: list[dict]) -> None:
        counts = {
            layer: sum(1 for c in cases if c["layer"] == layer)
            for layer in ("L1_applicable", "L2_explicit_pin_control", "L3_no_hit_fallback")
        }
        assert counts == {
            "L1_applicable": 18,
            "L2_explicit_pin_control": 6,
            "L3_no_hit_fallback": 6,
        }

    def test_three_paths_each_have_six_plus_two_plus_two(self, cases: list[dict]) -> None:
        for path in ("camera.depth_of_field", "composition.framing", "lighting.character"):
            by_layer = {
                layer: sum(1 for c in cases if c["primary_path"] == path and c["layer"] == layer)
                for layer in ("L1_applicable", "L2_explicit_pin_control", "L3_no_hit_fallback")
            }
            assert by_layer == {
                "L1_applicable": 6,
                "L2_explicit_pin_control": 2,
                "L3_no_hit_fallback": 2,
            }, path
            categories = {
                category: sum(
                    1 for c in cases if c["primary_path"] == path and c["category"] == category
                )
                for category in (
                    "applicable_adoption",
                    "explicit_value_control",
                    "pinned_control",
                    "no_hit_fallback",
                )
            }
            assert categories == {
                "applicable_adoption": 6,
                "explicit_value_control": 1,
                "pinned_control": 1,
                "no_hit_fallback": 2,
            }, path

    def test_all_five_approved_rules_are_covered(self, cases: list[dict]) -> None:
        rules = {
            c["rule_under_test"]
            for c in cases
            if c["category"] == "applicable_adoption"
        }
        assert rules == {
            "camera.depth_of_field.shallow_for_close_up",
            "camera.depth_of_field.deep_for_wide_shot",
            "composition.framing.medium_shot_for_standing_pose",
            "lighting.character.soft_for_tight_framing",
            "lighting.character.dramatic_for_angled_framing",
        }

    def test_annotation_mirrors_case_one_to_one(self, cases, annotations) -> None:
        assert [a["case_id"] for a in annotations] == [c["case_id"] for c in cases]

    def test_project_original_and_no_external_derivation(self, cases: list[dict]) -> None:
        for case in cases:
            assert case["source"]["source_type"] == "project_original"
            assert case["source"]["license"] == "internal-project-original"
            assert case["source"]["external_ids"] == []
            assert case["derived_from_dataset"] is False
            assert case["parent_case_id"] is None

    def test_structural_tamper_is_rejected(self, cases: list[dict], config: dict) -> None:
        broken = copy.deepcopy(cases)[:-1]
        assert check_cases_structure(broken, config)
        wrong_layer = copy.deepcopy(cases)
        wrong_layer[0]["layer"] = "L3_no_hit_fallback"
        assert check_cases_structure(wrong_layer, config)


class TestDevelopmentPoolSeparation:
    def test_no_verbatim_overlap_with_development_pool(self, cases: list[dict]) -> None:
        assert check_dev_pool_separation(cases, PROJECT_ROOT) == []

    def test_dev_pool_check_detects_copied_user_text(self, cases: list[dict]) -> None:
        dev_text = (PROJECT_ROOT / "tests/generation/generation_helpers.py").read_text(
            encoding="utf-8"
        )
        assert dev_text  # 非空开发池
        tampered = copy.deepcopy(cases)
        # 用一个必然出现在开发池文件里的片段模拟“复制开发用例”。
        first_line = dev_text.splitlines()[0]
        tampered[0]["user_text"] = first_line
        problems = check_dev_pool_separation(tampered, PROJECT_ROOT)
        assert any("appears verbatim" in item for item in problems)

    def test_dev_pool_is_scanned_by_directory(self) -> None:
        texts, problems = _development_pool_texts(PROJECT_ROOT)
        assert problems == []
        scanned = {rel for rel, _ in texts}
        assert any(rel.startswith("tests/") for rel in scanned)
        assert any(rel.startswith("evaluation/fixtures/") for rel in scanned)
        assert any(rel.startswith("evaluation/annotations/") for rel in scanned)
        # v0.5 验收清单与 v0.3 冻结数据必须落在扫描范围内。
        assert "tests/fixtures/knowledge/v0_5_acceptance_cases.json" in scanned
        assert "evaluation/fixtures/core_v0_3_r1.jsonl" in scanned
        assert "evaluation/annotations/core_v0_3_r1.jsonl" in scanned
        # 保留池自身不得被当作开发池。
        assert not any(rel.startswith("evaluation/v0_6/") for rel in scanned)
        assert not any("__pycache__" in rel for rel in scanned)


class TestControlLegality:
    """F1/F2/F4：PIN 合法性、L2 越权可见性、top_score_positive 语义。"""

    def test_pin_controls_carry_an_existing_value(self, cases, annotations) -> None:
        pins = [c for c in cases if c["category"] == "pinned_control"]
        assert len(pins) == 3
        for case in pins:
            path = case["primary_path"]
            assert path in case["pinned_paths"]
            assert path in case["explicit_paths"]
            assert path not in case["delegated_paths"]
            assert case["confirmed_intent"].get(path) is not None, case["case_id"]
            ann = next(a for a in annotations if a["case_id"] == case["case_id"])
            assert ann["expected_constraints"]["pin_preserved"] is True
            assert ann["expected_constraints"]["explicit_value_preserved"] is True
            assert ann["expected_retrieval"]["reason_code"] == "pinned_existing_value_not_queried"

    def test_pinned_control_without_value_is_rejected(self, cases, config) -> None:
        tampered = copy.deepcopy(cases)
        for case in tampered:
            if case["category"] == "pinned_control":
                path = case["primary_path"]
                case["confirmed_intent"].pop(path, None)
                break
        assert any("pin_requires_existing_value" in p for p in check_cases_structure(tampered, config))

    def test_delegated_pinned_control_is_rejected(self, cases, config) -> None:
        tampered = copy.deepcopy(cases)
        for case in tampered:
            if case["category"] == "pinned_control":
                case["delegated_paths"] = [case["primary_path"]]
                break
        assert check_cases_structure(tampered, config)

    def test_l2_overreach_is_visible(self, cases, annotations) -> None:
        l2 = [a for a in annotations if a["layer"] == "L2_explicit_pin_control"]
        assert len(l2) == 6
        by_id = {c["case_id"]: c for c in cases}
        for ann in l2:
            over = ann["overreach_check"]
            assert over["differs"] is True
            value = by_id[ann["case_id"]]["confirmed_intent"][ann["primary_path"]]
            assert over["explicit_value"] == value
            assert over["rule_candidate_value"] != value
            assert over["satisfied_rule_id"]

    def test_l2_explicit_value_equal_to_candidate_is_rejected(self, annotations, cases) -> None:
        tampered = copy.deepcopy(annotations)
        for ann in tampered:
            if ann["layer"] == "L2_explicit_pin_control":
                ann["overreach_check"]["explicit_value"] = ann["overreach_check"][
                    "rule_candidate_value"
                ]
                break
        assert check_annotations_structure(cases, tampered)

    def test_top_score_positive_semantics(self, annotations) -> None:
        for ann in annotations:
            value = ann["expected_retrieval"]["top_score_positive"]
            if ann["expected_retrieval"]["outcome"] == "adopted":
                assert value is True, ann["case_id"]
            else:
                assert value is None, ann["case_id"]

    def test_top_score_positive_false_is_rejected(self, cases, annotations) -> None:
        tampered = copy.deepcopy(annotations)
        for ann in tampered:
            if ann["layer"] == "L3_no_hit_fallback":
                ann["expected_retrieval"]["top_score_positive"] = False
                break
        assert check_annotations_structure(cases, tampered)


class TestAuthorizationAndConfig:
    def test_real_run_is_not_authorized(self, config: dict) -> None:
        assert config["real_run_authorized"] is False
        assert config["budget"]["status"] == "draft_pending_user_confirmation"
        for field in ("max_total_amount", "max_requests", "max_attempts"):
            assert config["budget"][field] is None
        assert config["thresholds"]["status"] == "pending_user_confirmation_before_results"

    def test_model_identity_is_not_claimed(self, config: dict) -> None:
        assert config["providers"]["image"]["model_identity_verified"] is False
        assert config["providers"]["llm"]["model_identity_verified"] is False

    def test_config_checks_pass_and_detect_authorization_flip(self, config: dict) -> None:
        assert check_run_config(config, PROJECT_ROOT) == []
        tampered = copy.deepcopy(config)
        tampered["real_run_authorized"] = True
        assert any("real_run_authorized" in item for item in check_run_config(tampered, PROJECT_ROOT))

    def test_config_checks_detect_literal_credential(self, config: dict) -> None:
        tampered = copy.deepcopy(config)
        tampered["providers"]["image"]["api_key"] = "sk-1234567890abcdef"
        assert any("credential-like" in item for item in check_run_config(tampered, PROJECT_ROOT))


class TestFrozenManifest:
    def test_roles_and_hashes_verify(self) -> None:
        manifest = load_manifest(PROJECT_ROOT / MANIFEST_REL, base_dir=PROJECT_ROOT)
        manifest.verify()
        assert manifest.manifest_version == "frozen_manifest_v0_6"
        assert manifest.roles["protocol"] == PROTOCOL_REL
        assert manifest.roles["dataset"] == CASES_REL

    def test_manifest_check_passes(self, config: dict) -> None:
        assert check_manifest(PROJECT_ROOT, config) == []

    def test_tampered_hash_is_rejected(self, tmp_path: Path) -> None:
        payload = json.loads((PROJECT_ROOT / MANIFEST_REL).read_text(encoding="utf-8"))
        payload["files"][CASES_REL] = "0" * 64
        tampered = tmp_path / "manifest.json"
        tampered.write_text(json.dumps(payload), encoding="utf-8")
        manifest = load_manifest(tampered, base_dir=PROJECT_ROOT)
        with pytest.raises(ManifestError, match="verification failed"):
            manifest.verify()

    def test_missing_file_is_rejected(self, tmp_path: Path) -> None:
        payload = json.loads((PROJECT_ROOT / MANIFEST_REL).read_text(encoding="utf-8"))
        payload["files"]["evaluation/v0_6/does_not_exist.jsonl"] = "a" * 64
        tampered = tmp_path / "manifest.json"
        tampered.write_text(json.dumps(payload), encoding="utf-8")
        manifest = load_manifest(tampered, base_dir=PROJECT_ROOT)
        with pytest.raises(ManifestError, match="missing file"):
            manifest.verify()


class TestRetrievalReachability:
    def test_retrieval_expectations_match_frozen_corpus(
        self, cases, annotations, config
    ) -> None:
        assert check_retrieval(cases, annotations, config, PROJECT_ROOT) == []

    def test_wrong_annotation_is_detected(self, cases, annotations, config) -> None:
        tampered = copy.deepcopy(annotations)
        for ann in tampered:
            if ann["expected_retrieval"]["outcome"] == "adopted":
                ann["expected_retrieval"]["candidate_value"] = "not_a_candidate"
                break
        else:  # pragma: no cover - 数据中必然存在 adopted 期望
            raise AssertionError("no adopted annotation to tamper with")
        assert check_retrieval(cases, tampered, config, PROJECT_ROOT)

    def test_annotation_structure_tamper_is_detected(self, cases, annotations) -> None:
        tampered = copy.deepcopy(annotations)
        tampered[0]["layer"] = "L9_unknown"
        assert check_annotations_structure(cases, tampered)


class TestSeedPolicyCompliance:
    """Step 02 禁止搜索“让 C 必赢”的 revision ID；本层预注册不得强制数值差异。"""

    def test_no_forced_value_separation_anywhere(self, cases, annotations) -> None:
        blob = json.dumps(cases, ensure_ascii=False) + json.dumps(annotations, ensure_ascii=False)
        for banned in ("must_differ", "必须与候选不同", "必须产生差异", "可观察差异"):
            assert banned not in blob, banned
        for ann in annotations:
            assert ann["fallback_constraint"] == "none", ann["case_id"]

    def test_applicable_layer_allows_value_coincidence(self, annotations) -> None:
        applicable = [a for a in annotations if a["layer"] == "L1_applicable"]
        assert len(applicable) == 18
        for ann in applicable:
            assert (
                ann["expected_bc_relation"]
                == "c_may_adopt_in_authorized_path_only_bc_values_may_coincide"
            )
            delta = ann["expected_prompt_delta"]
            assert delta["prompt_may_be_identical_when_candidate_equals_fallback"] is True
            assert delta["other_paths_identical"] is True

    def test_seed_policy_is_candidate_agnostic(self, cases, config) -> None:
        assert check_seed_policy_is_candidate_agnostic(cases, config) == []
        control = config["revision_seed_control"]
        assert control["candidate_agnostic"] is True
        assert control["reads_expected_candidate_or_knowledge_result"] is False
        assert control["value_separation_required"] is False
        assert "offline_feasibility_verified" not in control

    def test_forced_difference_annotation_is_detected(self, cases, annotations) -> None:
        tampered = copy.deepcopy(annotations)
        tampered[0]["fallback_constraint"] = "b_fallback_must_differ_from_candidate"
        assert check_annotations_structure(cases, tampered)

    def test_candidate_aware_seed_policy_is_detected(self, cases, config) -> None:
        tampered = copy.deepcopy(config)
        tampered["revision_seed_control"]["reads_expected_candidate_or_knowledge_result"] = True
        assert check_seed_policy_is_candidate_agnostic(cases, tampered)
        tampered2 = copy.deepcopy(config)
        tampered2["revision_seed_control"]["value_separation_required"] = True
        assert check_seed_policy_is_candidate_agnostic(cases, tampered2)
