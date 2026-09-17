"""v0.5 Step 03：`knowledge_base/v0.5/` 发布快照只读守卫。

本文件的 `approved` 是**真实人工审核决定**（reviewer=`change`，
reviewed_at=`2026-09-16T10:25:59Z`），不是测试夹具；夹具 approved 仍只在
`tests/knowledge/knowledge_helpers.py` 与 `--demo --rag` 内置语料里。

覆盖任务书 03「验收」中可离线判定的部分：最终快照可加载、获批数准确、真实批准证据可追溯、
不在同一语料内重复 ID、manifest 文件哈希/条数一致、v0.4 与用户数据库不变。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from visual_intent_agent.domain import INTENT_PATHS
from visual_intent_agent.knowledge import (
    KNOWLEDGE_PATHS,
    KNOWLEDGE_RETRIEVAL_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    KNOWLEDGE_TOKENIZER_VERSION,
    ReviewStatus,
    SourceType,
    candidate_values_for,
    compute_content_hash,
    load_corpus,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = PROJECT_ROOT / "knowledge_base" / "v0.5"
LEGACY_DIR = PROJECT_ROOT / "knowledge_base" / "v0.4"

#: 审核绑定的提案**随项目交付的归档副本**（在 `docs/` 下，不入被忽略的 `data/`）。
#: 缺失时守卫显式失败，不静默跳过；历史暂存位置见同目录 `archive_manifest.json` 的映射。
REVIEW_ARCHIVE_DIR = PROJECT_ROOT / "docs" / "knowledge_reviews" / "v0.5-approved-1"
ARCHIVED_PROPOSALS_DIR = REVIEW_ARCHIVE_DIR / "proposals"
ARCHIVE_MANIFEST_PATH = REVIEW_ARCHIVE_DIR / "archive_manifest.json"
ARCHIVE_README_PATH = REVIEW_ARCHIVE_DIR / "README.md"

#: 历史暂存根：仅用于核对归档清单里的历史路径映射，不作为读取来源。
HISTORICAL_STAGING_ROOT = (
    "data/staging/rag_resources_20260916/knowledge_review/draft_proposals"
)

#: 发布快照对应的随包批准摘要（审核入口之一）。
APPROVAL_SUMMARY_PATH = PROJECT_ROOT / "knowledge_base" / "v0.5" / "APPROVAL.md"

#: v0.4/v0.5 全部 JSONL 与 manifest 的冻结字节哈希（F1 加固：修复前后必须逐字节一致）。
#: 这些是发布快照的固定断言，不是从当前文件反推的期望值；任一被改写都必须失败。
FROZEN_CORPUS_SHA256: dict[str, str] = {
    "knowledge_base/v0.4/camera.depth_of_field.jsonl": (
        "8d1a0f64856ad4bd229d746a90f8eb102c22f45057a395a178e4fc82c5ffb002"
    ),
    "knowledge_base/v0.4/composition.framing.jsonl": (
        "0f4d3025768d15e7362cfea369978ae3178afb15d25ecd87364ec7975f20efb8"
    ),
    "knowledge_base/v0.4/lighting.character.jsonl": (
        "1924393f84417dfc27bf6e737d8d4882a05a9ef3b75c883cfaa4a555d342c8af"
    ),
    "knowledge_base/v0.4/manifest.json": (
        "ec013105e452c4fd47733bb0ca7efafa562519c198dc5ef40eea1b615304b9e2"
    ),
    "knowledge_base/v0.5/camera.depth_of_field.jsonl": (
        "9a32c511b0361e181a24f926fbc679dc23be28aef0303d9e95b83eebc5ee62d3"
    ),
    "knowledge_base/v0.5/composition.framing.jsonl": (
        "a07af1ac94634362df14079a803c4aef50439e717f4a7ece49635b316b190609"
    ),
    "knowledge_base/v0.5/lighting.character.jsonl": (
        "0b159a8fd7455ae9c3bdbc2e6499014f729fe88c5ca35db52253aae2d008b8c5"
    ),
    "knowledge_base/v0.5/manifest.json": (
        "0bbeea9bbf7438bd8480e4cc56b1797368e2aa6b47c1601b0f276b44697b37d7"
    ),
}

#: v0.4 语料清单的冻结字节哈希（Step 04 复核：v0.4 未被发布流程触碰）。
V0_4_MANIFEST_SHA256 = FROZEN_CORPUS_SHA256["knowledge_base/v0.4/manifest.json"]

#: 机械录入唯一允许变化的三个字段；其余任何差异都表示发布越权。
REVIEW_ONLY_FIELDS = frozenset({"review_status", "reviewer", "reviewed_at"})

CORPUS_VERSION = "v0.5-approved-1"
REVIEWER = "change"
REVIEWED_AT = datetime(2026, 9, 16, 10, 25, 59, tzinfo=timezone.utc)

#: 本轮获批的 5 条精确提案（ID → 审核绑定的提案文件完整 SHA-256）。
APPROVED_PROPOSALS: dict[str, str] = {
    "camera.depth_of_field.shallow_for_close_up": (
        "dd12f624ac4d39ab8c65faa23d4fd62293945285912792112fa811bd768dab54"
    ),
    "camera.depth_of_field.deep_for_wide_shot": (
        "5de514666b6ab898bcb2af1ceb78d172395b58aac98918fe99a2ac5458580ad3"
    ),
    "composition.framing.medium_shot_for_standing_pose": (
        "dadec4245210947f3bf45b12292084e947a8ba7a4fa2ff24f0ed33fa70c73449"
    ),
    "lighting.character.soft_for_tight_framing": (
        "1dae6d59421f7e90aec4305cf7c843831e704c930f80d47737e7af167b88d8dc"
    ),
    "lighting.character.dramatic_for_angled_framing": (
        "4539e45ccc94e42e9d936336eac09e356bc472d11359ea2011444b4743fc605f"
    ),
}

#: 获批提案绑定的 content_hash（正文未改，故与发布快照一致）。
APPROVED_CONTENT_HASHES: dict[str, str] = {
    "camera.depth_of_field.shallow_for_close_up": (
        "8124fdd97f82cded4e9dcfbb02e3a4c73c963aa48d19e7c2ba6db70b67d8a6e5"
    ),
    "camera.depth_of_field.deep_for_wide_shot": (
        "b32de75822d16f0dc21786543f4422aa8eab24a14869eee07e44636eb9cd1db9"
    ),
    "composition.framing.medium_shot_for_standing_pose": (
        "94afb6e376cddb41130f9fe2b74b38c3a3f9509a7bf58deaa88c87fdca138669"
    ),
    "lighting.character.soft_for_tight_framing": (
        "fc81f7f5077813e0daf4c5e784c020b97e19b92e0a191c1ae3bae46d5e90fbf2"
    ),
    "lighting.character.dramatic_for_angled_framing": (
        "e367bd3046bb44909bfeae10cf5dbd7e6c90f2c07c61a2664fe0cdc75ba62697"
    ),
}


def test_release_corpus_loads_with_exactly_the_five_approved_units():
    corpus = load_corpus(RELEASE_DIR)

    assert corpus.corpus_version == CORPUS_VERSION
    assert corpus.all_unit_count == 5
    assert {unit.knowledge_id for unit in corpus.all_units} == set(APPROVED_PROPOSALS)
    assert {unit.knowledge_id for unit in corpus.build_units()} == set(APPROVED_PROPOSALS)
    # 发布目录只收录本次获批单元：没有 draft / rejected 混入生产 JSONL。
    assert {unit.review_status for unit in corpus.all_units} == {ReviewStatus.APPROVED}
    assert Counter(unit.applicable_path for unit in corpus.all_units) == {
        "camera.depth_of_field": 2,
        "composition.framing": 1,
        "lighting.character": 2,
    }
    # 三路径各至少一条（任务书最低完成标准）。
    assert {unit.applicable_path for unit in corpus.all_units} == set(KNOWLEDGE_PATHS)


def test_release_units_carry_the_real_review_decision_and_hashes():
    corpus = load_corpus(RELEASE_DIR)

    for unit in corpus.build_units():
        assert unit.version == "2"
        assert unit.review_status is ReviewStatus.APPROVED
        assert unit.reviewer == REVIEWER
        assert unit.reviewed_at == REVIEWED_AT
        # 机械录入：正文逐字未改，content_hash 与获批提案一致。
        assert unit.content_hash == APPROVED_CONTENT_HASHES[unit.knowledge_id]
        assert unit.content_hash == compute_content_hash(unit.content)


def test_release_manifest_records_frozen_versions_and_verified_file_hashes():
    corpus = load_corpus(RELEASE_DIR)
    manifest = corpus.manifest

    assert manifest.corpus_version == CORPUS_VERSION
    assert manifest.schema_version == KNOWLEDGE_SCHEMA_VERSION
    assert manifest.tokenizer_version == KNOWLEDGE_TOKENIZER_VERSION
    assert manifest.retrieval_version == KNOWLEDGE_RETRIEVAL_VERSION
    assert {entry.path for entry in manifest.files} == {
        "camera.depth_of_field.jsonl",
        "composition.framing.jsonl",
        "lighting.character.jsonl",
    }
    assert sum(entry.unit_count for entry in manifest.files) == 5
    # load_corpus 已逐文件核验 sha256 与 unit_count；这里再确认清单无重复路径。
    assert len({entry.path for entry in manifest.files}) == len(manifest.files)


def test_release_units_are_authorized_fully_sourced_and_project_original():
    corpus = load_corpus(RELEASE_DIR)

    for unit in corpus.all_units:
        assert unit.applicable_path in KNOWLEDGE_PATHS
        assert unit.candidate_value in candidate_values_for(unit.applicable_path)
        assert unit.keywords and unit.target_models
        for condition in unit.conditions:
            assert condition.path in INTENT_PATHS
        source = unit.source
        assert source.source_type is SourceType.PROJECT_ORIGINAL
        assert source.original_declaration
        assert source.repository_path is not None
        assert source.source_revision is not None or source.source_date is not None
        assert source.locator == unit.knowledge_id
        # 未借用 COCO 许可 / 未使用外部事实来源。
        assert source.license == "internal-project-original"


def test_release_does_not_touch_legacy_v0_4_which_stays_all_draft():
    legacy = load_corpus(LEGACY_DIR)

    assert legacy.corpus_version == "v0.4-draft-1"
    assert legacy.all_unit_count == 9
    assert {unit.review_status for unit in legacy.all_units} == {ReviewStatus.DRAFT}
    assert all(unit.reviewer is None and unit.reviewed_at is None for unit in legacy.all_units)
    assert legacy.build_units() == ()


def _released_units_as_payloads() -> dict[str, dict]:
    corpus = load_corpus(RELEASE_DIR)
    return {unit.knowledge_id: json.loads(unit.model_dump_json()) for unit in corpus.all_units}


def _assert_release_matches_archived_proposals(proposals_dir: Path) -> None:
    """机械录入守卫：发布单元相对获批提案只允许改审核三字段。

    审核绑定的是归档提案文件的完整 SHA-256（见 `APPROVED_PROPOSALS`，与
    `docs/handoffs/v0_5_knowledge_review.md` / `knowledge_base/v0.5/APPROVAL.md`
    的既有批准记录一致）。函数按参数目录读取，既校验字节未被改动，又逐字段比对发布
    快照；正文、条件、候选、关键词、别名、来源、版本等任何字段被改动都必须失败。
    提案文件缺失时**显式失败**——无法核验的发布会当作通过是不可接受的。
    """
    released = _released_units_as_payloads()

    for knowledge_id, expected_file_sha256 in APPROVED_PROPOSALS.items():
        proposal_path = proposals_dir / f"{knowledge_id}.v2.json"
        assert proposal_path.is_file(), (
            f"approved proposal {proposal_path} is missing; the release guard refuses to skip "
            f"{knowledge_id!r} and fails instead of passing an unverifiable release"
        )
        raw = proposal_path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected_file_sha256
        proposal = json.loads(raw.decode("utf-8"))

        # 提案必须仍是未审核材料；发布单元必须是真实 approved。
        assert proposal["review_status"] == "draft"
        assert proposal["reviewer"] is None and proposal["reviewed_at"] is None
        unit = released[knowledge_id]
        assert unit["review_status"] == "approved"
        assert unit["reviewer"] == REVIEWER
        assert unit["reviewed_at"] == "2026-09-16T10:25:59Z"

        changed = {
            key for key in set(proposal) | set(unit) if proposal.get(key) != unit.get(key)
        }
        assert changed == REVIEW_ONLY_FIELDS, (
            f"{knowledge_id}: release must differ from its approved proposal only in "
            f"{sorted(REVIEW_ONLY_FIELDS)}; got {sorted(changed)}"
        )


def test_release_units_differ_from_archived_approved_proposals_only_in_review_fields():
    """发布守卫改读可交付归档：不含 `data/` 也能核验发布。"""
    _assert_release_matches_archived_proposals(ARCHIVED_PROPOSALS_DIR)


def test_release_guard_reads_the_deliverable_archive_not_gitignored_staging():
    """归档必须处于可交付目录（`docs/`），而不是被 `.gitignore` 排除的 `data/`。"""
    relative = ARCHIVED_PROPOSALS_DIR.relative_to(PROJECT_ROOT)
    assert relative.parts[0] == "docs"
    assert "data" not in relative.parts
    assert ARCHIVED_PROPOSALS_DIR.is_dir()
    assert ARCHIVE_MANIFEST_PATH.is_file()
    assert ARCHIVE_README_PATH.is_file()
    assert APPROVAL_SUMMARY_PATH.is_file()
    for knowledge_id in APPROVED_PROPOSALS:
        assert (ARCHIVED_PROPOSALS_DIR / f"{knowledge_id}.v2.json").is_file()


def test_archived_manifest_maps_history_and_matches_the_approved_hashes():
    """归档清单：哈希锚定既有批准记录，并给出历史暂存路径 → 归档副本的明确映射。"""
    manifest = json.loads(ARCHIVE_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["archive_id"] == "v0.5-approved-1"
    assert manifest["corpus_version"] == CORPUS_VERSION
    assert manifest["reviewer"] == REVIEWER
    assert manifest["reviewed_at"] == "2026-09-16T10:25:59Z"
    assert manifest["historical_staging_root"] == HISTORICAL_STAGING_ROOT
    assert manifest["authoritative_review_record"] == "docs/handoffs/v0_5_knowledge_review.md"

    entries = {entry["knowledge_id"]: entry for entry in manifest["proposals"]}
    assert set(entries) == set(APPROVED_PROPOSALS)
    released_ids = {unit.knowledge_id for unit in load_corpus(RELEASE_DIR).all_units}

    for knowledge_id, expected_file_sha256 in APPROVED_PROPOSALS.items():
        entry = entries[knowledge_id]
        # 清单里的"原始哈希"必须等于既有批准记录绑定的哈希（不单独信任清单）。
        assert entry["original_sha256"] == expected_file_sha256
        assert entry["archived_sha256"] == expected_file_sha256
        assert entry["review_decision"] == "approved" and entry["released"] is True
        assert entry["release_knowledge_id"] == knowledge_id
        assert entry["release_jsonl"].startswith("knowledge_base/v0.5/")
        assert entry["release_jsonl"].endswith(f"{entry['applicable_path']}.jsonl")
        assert knowledge_id in released_ids

        archived = REVIEW_ARCHIVE_DIR / entry["archived_path"]
        assert archived.is_file()
        # F1 加固：archived_path 必须解析后仍位于归档根内，禁止 `..`/绝对路径穿越到 data/。
        assert not Path(entry["archived_path"]).is_absolute()
        resolved = archived.resolve()
        assert resolved.is_relative_to(REVIEW_ARCHIVE_DIR.resolve()), (
            f"{knowledge_id}: archived_path {entry['archived_path']!r} escapes the archive root"
        )
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == expected_file_sha256

        historical = entry["historical_staging_path"]
        assert historical.startswith(f"{HISTORICAL_STAGING_ROOT}/")
        assert historical.endswith(f"{knowledge_id}.v2.json")
        assert REVIEW_ARCHIVE_DIR.joinpath(entry["archived_path"]).name == f"{knowledge_id}.v2.json"


def _copy_archive(tmp_path: Path) -> Path:
    target = tmp_path / "archived_proposals"
    shutil.copytree(ARCHIVED_PROPOSALS_DIR, target)
    return target


def test_untampered_archive_copy_still_passes_the_guard(tmp_path):
    """对照组：未改动的临时副本必须通过，证明篡改用例不是因为副本本身而失败。"""
    _assert_release_matches_archived_proposals(_copy_archive(tmp_path))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_file",
        "appended_byte",
        "forged_review_status",
        "invalid_json",
        "reserialized",
        "extra_field",
    ],
)
def test_release_guard_rejects_missing_or_byte_tampered_archive(tmp_path, mutation):
    """对临时归档副本注入缺失/字节篡改/伪造审核/重序列化/额外字段，守卫必须拒绝。"""
    target = _copy_archive(tmp_path)
    victim = target / "lighting.character.soft_for_tight_framing.v2.json"

    if mutation == "missing_file":
        victim.unlink()
    elif mutation == "appended_byte":
        victim.write_bytes(victim.read_bytes() + b" ")
    elif mutation == "forged_review_status":
        payload = json.loads(victim.read_text(encoding="utf-8"))
        payload["review_status"] = "approved"
        payload["reviewer"] = REVIEWER
        payload["reviewed_at"] = "2026-09-16T10:25:59Z"
        victim.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif mutation == "invalid_json":
        victim.write_text("{not json", encoding="utf-8")
    elif mutation == "reserialized":
        # 语义不变、仅重新格式化也改变字节；原字节归档不允许任何重序列化。
        payload = json.loads(victim.read_text(encoding="utf-8"))
        victim.write_text(
            json.dumps(payload, ensure_ascii=False, indent=4) + "\n", encoding="utf-8"
        )
    elif mutation == "extra_field":
        payload = json.loads(victim.read_text(encoding="utf-8"))
        payload["rogue_note"] = "tampered"
        victim.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:  # pragma: no cover - guarded by the parametrize list
        raise AssertionError(f"unknown mutation {mutation!r}")

    with pytest.raises(AssertionError):
        _assert_release_matches_archived_proposals(target)


@pytest.mark.parametrize("field", ["conditions", "candidate_value"])
def test_mechanical_entry_guard_rejects_condition_or_candidate_change(
    tmp_path, monkeypatch, field
):
    """即使字节哈希被重新算出，条件/候选任何变化仍必须被机械录入守卫拒绝。

    真实发布受"原始哈希锚定既有批准记录"保护；这里把期望哈希改为篡改后的值以**单独**
    检验字段比对分支，证明守卫不会只看哈希就放过条件/候选变更。
    """
    target = _copy_archive(tmp_path)
    knowledge_id = "lighting.character.soft_for_tight_framing"
    victim = target / f"{knowledge_id}.v2.json"
    payload = json.loads(victim.read_text(encoding="utf-8"))
    if field == "conditions":
        payload["conditions"] = [
            {"path": "environment.mode", "operator": "equals", "values": ["outdoor"]}
        ]
    else:
        payload["candidate_value"] = "dramatic"
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    victim.write_bytes(raw)
    monkeypatch.setitem(APPROVED_PROPOSALS, knowledge_id, hashlib.sha256(raw).hexdigest())

    with pytest.raises(AssertionError, match="only in"):
        _assert_release_matches_archived_proposals(target)


def test_mechanical_entry_guard_rejects_extra_field_even_with_resynced_hash(
    tmp_path, monkeypatch
):
    """额外字段即使被重新锚定哈希，也必须被字段比对拒绝：发布只能差审核三字段。

    与条件/候选变更用例同理：把期望哈希改成篡改后的值以单独检验字段比对分支，
    证明"多加一个字段"不会因为字节哈希对得上而被当成合法机械录入。
    """
    target = _copy_archive(tmp_path)
    knowledge_id = "lighting.character.soft_for_tight_framing"
    victim = target / f"{knowledge_id}.v2.json"
    payload = json.loads(victim.read_text(encoding="utf-8"))
    payload["rogue_note"] = "tampered"
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    victim.write_bytes(raw)
    monkeypatch.setitem(APPROVED_PROPOSALS, knowledge_id, hashlib.sha256(raw).hexdigest())

    with pytest.raises(AssertionError, match="only in"):
        _assert_release_matches_archived_proposals(target)


@pytest.mark.parametrize(
    ("relative_path", "expected_sha256"),
    sorted(FROZEN_CORPUS_SHA256.items()),
)
def test_frozen_corpus_jsonl_and_manifest_bytes(relative_path, expected_sha256):
    """v0.4/v0.5 所有 JSONL 与 manifest 的字节哈希固定：修复前后逐字节一致，无需迁移。"""
    path = PROJECT_ROOT / relative_path

    assert path.is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256


def _git_check_ignore(paths: list[str]) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available; cannot verify .gitignore coverage")
    inside = subprocess.run(
        [git, "-C", str(PROJECT_ROOT), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        pytest.skip("project root is not a git work tree; cannot verify .gitignore coverage")
    return subprocess.run(
        [git, "-C", str(PROJECT_ROOT), "check-ignore", "--", *paths],
        capture_output=True,
        text=True,
        check=False,
    )


def test_deliverable_archive_and_knowledge_fixtures_are_not_gitignored():
    """归档与工程 fixture 必须可随项目交付：不得被 `.gitignore` 排除。

    自动覆盖归档目录内的全部现存文件，以及 `tests/fixtures/knowledge/` 下**已存在**的
    文件（未来新增 fixture 自动纳入；目录不存在时不制造空假 fixture）。
    """
    candidates = sorted(str(path) for path in REVIEW_ARCHIVE_DIR.rglob("*") if path.is_file())
    fixture_root = PROJECT_ROOT / "tests" / "fixtures" / "knowledge"
    if fixture_root.is_dir():
        candidates.extend(sorted(str(path) for path in fixture_root.rglob("*") if path.is_file()))
    assert candidates, "expected deliverable archive files to exist"

    result = _git_check_ignore(candidates)

    # `git check-ignore` 退出码：0 = 至少一个被忽略，1 = 全部未被忽略。
    assert result.returncode == 1, (
        "deliverable paths must not be gitignored; `git check-ignore` reported:\n"
        f"{result.stdout}{result.stderr}"
    )


def test_legacy_v0_4_manifest_is_byte_frozen():
    """v0.4 清单字节冻结：发布 v0.5 不得改写旧语料（含 hash/条数/版本）。"""
    digest = hashlib.sha256((LEGACY_DIR / "manifest.json").read_bytes()).hexdigest()

    assert digest == V0_4_MANIFEST_SHA256
