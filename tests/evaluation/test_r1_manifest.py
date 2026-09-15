"""R1-B：r1 冻结数据与清单合同的离线测试。

覆盖：

- `frozen_manifest_v0_3_r1.json` 的四个角色解析与哈希校验；旧清单向后兼容；
- r1 fixture/annotation 的修订语义（数量允许、冲突停用、s04 必要轮次修正、
  同一冻结用户消息、parent_case_id / annotation_revision_reason / preserve_path_groups）；
- 旧 `core_v0_3` 冻结物与旧清单保持只读可校验。

全部离线，只读仓库文件。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.manifest import (
    MANIFEST_ROLES,
    ManifestError,
    load_manifest,
    sha256_file,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
R1_MANIFEST = PROJECT_ROOT / "evaluation" / "frozen_manifest_v0_3_r1.json"
OLD_MANIFEST = PROJECT_ROOT / "evaluation" / "frozen_manifest_v0_3.json"
R1_FIXTURE = PROJECT_ROOT / "evaluation" / "fixtures" / "core_v0_3_r1.jsonl"
R1_ANNOTATIONS = PROJECT_ROOT / "evaluation" / "annotations" / "core_v0_3_r1.jsonl"
OLD_FIXTURE = PROJECT_ROOT / "evaluation" / "fixtures" / "core_v0_3.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(scope="module")
def fixtures() -> list[dict]:
    return _load_jsonl(R1_FIXTURE)


@pytest.fixture(scope="module")
def annotations() -> dict[str, dict]:
    return {row["case_id"]: row for row in _load_jsonl(R1_ANNOTATIONS)}


# ---------------------------------------------------------------------------
# 清单解析与校验
# ---------------------------------------------------------------------------


class TestManifest:
    def test_r1_manifest_roles_and_versions(self) -> None:
        manifest = load_manifest(R1_MANIFEST)
        assert manifest.manifest_version == "frozen_manifest_v0_3_r1"
        assert manifest.protocol_version == "gate_a_protocol_v0_3_r1"
        assert manifest.dataset_version == "core_v0_3_r1"
        assert manifest.config_version == "gate_a_v0_3_r1"
        assert set(manifest.roles) == set(MANIFEST_ROLES)
        assert manifest.dataset_path == R1_FIXTURE
        assert manifest.annotations_path == R1_ANNOTATIONS
        manifest.verify()

    def test_r1_manifest_hashes_match_actual_files(self) -> None:
        manifest = load_manifest(R1_MANIFEST)
        hashes = manifest.verify_hashes()
        assert hashes["manifest"] == sha256_file(R1_MANIFEST)
        assert hashes["dataset"] == sha256_file(R1_FIXTURE)
        assert hashes["annotations"] == sha256_file(R1_ANNOTATIONS)
        for role in MANIFEST_ROLES:
            assert hashes[role] == manifest.files[manifest.roles[role]]

    def test_legacy_manifest_backward_compatible(self) -> None:
        """旧清单没有 roles：按默认路径推断，旧行为不变。"""
        manifest = load_manifest(OLD_MANIFEST)
        assert manifest.manifest_version == "frozen_manifest_v0_3"
        assert manifest.dataset_path == OLD_FIXTURE
        assert manifest.protocol_path == PROJECT_ROOT / "evaluation" / "protocol.md"
        manifest.verify()

    def test_tampered_hash_is_rejected(self, tmp_path: Path) -> None:
        payload = json.loads(R1_MANIFEST.read_text(encoding="utf-8"))
        rel = payload["roles"]["dataset"]
        payload["files"][rel] = "0" * 64
        tampered = tmp_path / "manifest.json"
        tampered.write_text(json.dumps(payload), encoding="utf-8")
        manifest = load_manifest(tampered, base_dir=PROJECT_ROOT)
        with pytest.raises(ManifestError, match="verification failed"):
            manifest.verify()

    def test_missing_file_is_rejected(self, tmp_path: Path) -> None:
        payload = json.loads(R1_MANIFEST.read_text(encoding="utf-8"))
        payload["files"]["evaluation/does_not_exist.jsonl"] = "a" * 64
        tampered = tmp_path / "manifest.json"
        tampered.write_text(json.dumps(payload), encoding="utf-8")
        manifest = load_manifest(tampered, base_dir=PROJECT_ROOT)
        with pytest.raises(ManifestError, match="missing file"):
            manifest.verify()

    def test_unknown_role_is_rejected(self) -> None:
        manifest = load_manifest(R1_MANIFEST)
        with pytest.raises(ManifestError):
            manifest.resolve("unknown")

    def test_role_path_must_be_frozen(self, tmp_path: Path) -> None:
        payload = json.loads(R1_MANIFEST.read_text(encoding="utf-8"))
        # 存在的文件但不在 files 清单内：角色解析到未校验文件必须拒绝。
        payload["roles"]["dataset"] = "evaluation/reports/annotation_revision_log_v0_3_r1.md"
        tampered = tmp_path / "manifest.json"
        tampered.write_text(json.dumps(payload), encoding="utf-8")
        manifest = load_manifest(tampered, base_dir=PROJECT_ROOT)
        with pytest.raises(ManifestError, match="not listed in 'files'"):
            manifest.verify()


# ---------------------------------------------------------------------------
# r1 数据集修订语义
# ---------------------------------------------------------------------------


class TestR1Fixture:
    def test_dataset_version_and_case_ids_match_parent(self, fixtures: list[dict]) -> None:
        old_ids = [row["case_id"] for row in _load_jsonl(OLD_FIXTURE)]
        assert [row["case_id"] for row in fixtures] == old_ids
        for row in fixtures:
            assert row["dataset_version"] == "core_v0_3_r1"
            assert row["parent_case_id"] == row["case_id"]

    def test_no_answer_variants_in_r1(self, fixtures: list[dict]) -> None:
        """R1-B #5：A/B 必须使用同一份冻结用户消息。"""
        for case in fixtures:
            for turn in case["turns"]:
                assert "answer_variants" not in turn, (case["case_id"], turn["turn_id"])

    def test_s04_conflict_cases_use_feedback_turns(self, fixtures: list[dict]) -> None:
        """冲突停用后，s04 的 t2 是合法修改轮，不再是澄清回答。"""
        for case in fixtures:
            if not case["case_id"].startswith("s04-conflict"):
                continue
            kinds = {turn["turn_id"]: turn["kind"] for turn in case["turns"]}
            assert kinds["t1"] == "user_message"
            assert kinds["t2"] == "image_feedback"
            assert kinds["t3"] == "accept"


class TestR1Annotations:
    def test_revision_history_recorded(self, annotations: dict[str, dict]) -> None:
        for case_id, annotation in annotations.items():
            assert annotation["dataset_version"] == "core_v0_3_r1"
            assert annotation["parent_case_id"] == case_id
            assert annotation.get("annotation_revision_reason")

    def test_all_heuristic_conflicts_removed(self, annotations: dict[str, dict]) -> None:
        """A 已确认四条启发式冲突规则 predicate 停用；r1 不再期望检出。"""
        for case_id, annotation in annotations.items():
            for turn in annotation["turn_annotations"]:
                assert turn.get("expected_conflicts") == [], (case_id, turn["turn_id"])

    def test_explicit_numerals_allow_subject_count(self, annotations: dict[str, dict]) -> None:
        """R1-A #1：明确数词允许抽取；r1 不再把 subject.count 列为必须保持未设。"""
        t1 = annotations["s01-complete-001"]["turn_annotations"][0]
        assert "subject.count" not in t1["paths_must_remain_unset"]
        extras = t1.get("acceptable_extra_deltas") or []
        assert any(
            extra["path"] == "subject.count"
            and extra["value_match"]["mode"] == "int_equals"
            and extra["value_match"]["value"] == 1
            for extra in extras
        )

    def test_case_without_explicit_count_still_forbids_count(self, annotations: dict[str, dict]) -> None:
        """未陈述数量不默认：s02-002 t1 无主体数量，subject.count 仍必须保持未设。"""
        t1 = annotations["s02-missing-core-002"]["turn_annotations"][0]
        assert "subject.count" in t1["paths_must_remain_unset"]

    def test_s04_t1_no_longer_blocks_on_conflict(self, annotations: dict[str, dict]) -> None:
        for case_id in ("s04-conflict-001", "s04-conflict-002", "s04-conflict-003"):
            t1 = annotations[case_id]["turn_annotations"][0]
            assert t1["expected_outcome"] == "ready_and_generate"
            assert t1["must_clarify"] == []

    def test_count_semantics_must_be_preserved_in_prompt(self, annotations: dict[str, dict]) -> None:
        """允许两种结构化表达，但明确数量语义必须保留（must_mention 数量组）。"""
        groups = annotations["s01-complete-001"]["prompt_expectations"]["must_mention_groups"]
        count_groups = [group for group in groups if "一只" in group]
        assert count_groups, "明确数词案例必须要求数量语义保留"
        assert "1只" in count_groups[0]
        # 已由既有词组覆盖的案例不重复添加。
        groups_2 = annotations["s07-single-field-002"]["prompt_expectations"]["must_mention_groups"]
        assert sum(1 for group in groups_2 if "两只" in group) == 1

    def test_preserve_path_groups_are_path_linked(self, annotations: dict[str, dict]) -> None:
        """R1-B #2：修改轮带按路径关联的保留词组。"""
        t2 = annotations["s09-multiturn-001"]["turn_annotations"][2]
        groups = t2.get("preserve_path_groups") or {}
        assert groups, "s09-multiturn-001 t3 是修改轮，必须有路径关联保留词组"
        for path, path_groups in groups.items():
            assert path in t2["forbidden_change_paths"]
            assert path_groups and all(isinstance(group, list) for group in path_groups)

    def test_accept_turns_carry_no_acceptable_count_extra(self, annotations: dict[str, dict]) -> None:
        t3 = annotations["s07-single-field-002"]["turn_annotations"][2]
        assert t3["expected_outcome"] == "accepted_completed"
        assert not any(
            extra["path"] == "subject.count"
            for extra in (t3.get("acceptable_extra_deltas") or [])
        )


def test_derive_preserve_path_groups_drops_cleared_values() -> None:
    """CLEAR 必须从 latest 移除：合法删除的旧值不得跨澄清轮继续被要求保留。"""
    from evaluation.tools.build_core_v0_3_r1 import derive_preserve_path_groups

    turns = [
        {
            "expected_deltas": [
                {
                    "operation": "SET",
                    "path": "subject.description",
                    "value_match": {"mode": "contains_any", "keywords": ["猫", "cat"]},
                },
                {
                    "operation": "SET",
                    "path": "composition.framing",
                    "value_match": {"mode": "contains_any", "keywords": ["中景", "medium shot"]},
                },
            ],
            "forbidden_change_paths": [],
        },
        {
            "expected_deltas": [
                {"operation": "CLEAR", "path": "subject.description", "value_match": None}
            ],
            "forbidden_change_paths": ["subject.description", "composition.framing"],
        },
    ]
    groups = derive_preserve_path_groups(turns, 1)
    assert "subject.description" not in groups
    assert groups["composition.framing"] == [["中景", "medium shot"]]


def test_r1_protocol_and_config_exist_and_are_hashed() -> None:
    manifest = load_manifest(R1_MANIFEST)
    assert manifest.protocol_path.is_file()
    assert manifest.config_path.is_file()
    config = json.loads(manifest.config_path.read_text(encoding="utf-8"))
    assert config["config_version"] == "gate_a_v0_3_r1"
    assert config["outputs"]["real_run_root"] == "outputs/evaluation_runs"
    assert config["identity"]["formal_requires_clean_worktree"] is True
    protocol = manifest.protocol_path.read_text(encoding="utf-8")
    assert "gate_a_protocol_v0_3_r1" in protocol
    assert "forbidden_keyword_hit" in protocol
