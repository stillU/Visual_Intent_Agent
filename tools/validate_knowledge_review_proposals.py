#!/usr/bin/env python3
"""离线校验 v0.5 Step 02 的 draft 提案（审核依据材料，不进入生产语料）。

默认校验**随项目交付的审核归档** `docs/knowledge_reviews/v0.5-approved-1/proposals/`
（原字节副本，即使 `data/` 暂存缺失也可运行）；用显式 `--dir` 可校验待审草稿目录
（例如历史暂存 `data/staging/.../draft_proposals/`）。

用途：在**不修改运行代码、不加载生产语料、不调用任何 Provider** 的前提下，确认每份提案：

1. 可按冻结的 `KnowledgeUnit` 合同解析（`extra="forbid"`、来源完整性、审核自洽等）；
2. `review_status == "draft"` 且 `reviewer` / `reviewed_at` 均为 `None`（不得伪造审核）；
3. `applicable_path` 属于三条知识白名单路径，`candidate_value` 属于唯一权威候选表；
4. 条件只使用封闭算子 `equals` / `in`，路径在白名单内，取值在系统可确认词汇内
   （`subject.count` 为严格十进制整数，其余取 `DELEGATED_CANDIDATES`）→ 条件可达；
5. `content_hash == sha256(content)`（UTF-8 小写十六进制）；
6. 提案之间无重复 `knowledge_id`；
7. 若同目录提供归档清单（默认归档与显式 `--manifest`），逐条核对 `archived_path` 与
   归档文件的实际 SHA-256 一致，且无缺失/多余条目；`archived_path` 解析后必须位于
   归档根目录内（拒绝绝对路径与 `..` 逃逸到 `data/` 等目录）。
8. 五个获批 `knowledge_id` 的文件 SHA-256 由本工具内置常量锚定**既有审核记录**：
   即便同时改写提案字节与清单里的 `original_sha256` / `archived_sha256`，也必须拒绝，
   不能让"文件和新清单一起改"变成合法。

本脚本只读；不写任何文件、不触网。退出码 0 = 全部通过，1 = 存在失败。

用法：
    uv run python tools/validate_knowledge_review_proposals.py
    uv run python tools/validate_knowledge_review_proposals.py --dir <proposals_dir>
    uv run python tools/validate_knowledge_review_proposals.py --manifest <archive_manifest.json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from visual_intent_agent.domain.paths import INTENT_PATHS  # noqa: E402
from visual_intent_agent.knowledge import (  # noqa: E402
    KNOWLEDGE_PATHS,
    ConditionOperator,
    KnowledgeUnit,
    ReviewStatus,
    candidate_values_for,
    compute_content_hash,
)
from visual_intent_agent.prompt_engine.engine import DELEGATED_CANDIDATES  # noqa: E402

#: 随项目交付的审核归档（原字节提案副本）。
DEFAULT_DIR = ROOT / "docs" / "knowledge_reviews" / "v0.5-approved-1" / "proposals"
DEFAULT_MANIFEST = DEFAULT_DIR.parent / "archive_manifest.json"

#: 审核记录绑定的 5 份获批 v2 提案文件完整 SHA-256（`knowledge_id` → 文件哈希）。
#: 这些常量来自既有权威审核记录（`docs/handoffs/v0_5_knowledge_review.md` 与
#: `knowledge_base/v0.5/APPROVAL.md`），是工具**独立于归档清单**的锚点：只信任清单里
#: 的哈希会让"同时改提案与清单"通过，因此锚点必须是代码内固定字面量。
APPROVED_ARCHIVE_SHA256: dict[str, str] = {
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

_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$")


def _condition_reachable(path: str, values: tuple[str, ...]) -> str | None:
    """返回 None 表示可达；否则返回失败原因。"""
    if path not in INTENT_PATHS:
        return f"condition path {path!r} is not a whitelisted intent path"
    if path == "subject.count":
        for value in values:
            if not _DECIMAL.match(value):
                return f"subject.count value {value!r} is not a strict decimal integer"
        return None
    allowed = DELEGATED_CANDIDATES.get(path)
    if not allowed:
        return f"condition path {path!r} has no confirmable value vocabulary"
    for value in values:
        if value not in allowed:
            return f"condition value {value!r} is unreachable for {path!r} (allowed: {list(allowed)})"
    return None


def check_unit(payload: dict, *, label: str) -> list[str]:
    errors: list[str] = []
    try:
        unit = KnowledgeUnit.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError
        return [f"{label}: failed KnowledgeUnit contract validation: {exc}"]

    if unit.review_status is not ReviewStatus.DRAFT:
        errors.append(f"{label}: review_status must be draft, got {unit.review_status.value!r}")
    if unit.reviewer is not None or unit.reviewed_at is not None:
        errors.append(f"{label}: reviewer/reviewed_at must be null (no forged human review)")
    if unit.applicable_path not in KNOWLEDGE_PATHS:
        errors.append(f"{label}: applicable_path {unit.applicable_path!r} outside knowledge whitelist")
    if unit.candidate_value not in candidate_values_for(unit.applicable_path):
        errors.append(
            f"{label}: candidate_value {unit.candidate_value!r} is not authorized for "
            f"{unit.applicable_path!r}"
        )
    if unit.content_hash != compute_content_hash(unit.content):
        errors.append(f"{label}: content_hash does not match sha256(content)")
    for condition in unit.conditions:
        if condition.operator not in (ConditionOperator.EQUALS, ConditionOperator.IN):
            errors.append(f"{label}: unsupported condition operator {condition.operator!r}")
        reason = _condition_reachable(condition.path, condition.values)
        if reason:
            errors.append(f"{label}: {reason}")
    return errors


def _resolve_archived_path(
    archive_root: Path, archived_rel: str, knowledge_id: str
) -> tuple[Path | None, str | None]:
    """把清单里的 `archived_path` 解析到**归档根目录内**；返回 (path, error)。

    拒绝绝对路径与任何解析后逃出 `archive_root` 的相对路径（例如
    `../../data/staging/...`），确保审计不会读取归档之外的暂存/运行数据。
    """
    if not archived_rel:
        return None, f"{knowledge_id}: archived_path is missing"
    candidate = Path(archived_rel)
    if candidate.is_absolute():
        return None, (
            f"{knowledge_id}: archived_path {archived_rel!r} must be relative to the archive root"
        )
    resolved = (archive_root / candidate).resolve()
    if not resolved.is_relative_to(archive_root):
        return None, (
            f"{knowledge_id}: archived_path {archived_rel!r} escapes the archive root {archive_root}"
        )
    return resolved, None


def check_manifest(proposals_dir: Path, manifest_path: Path) -> list[str]:
    """核对归档清单的映射与字节哈希；返回问题列表（空 = 通过）。

    哈希锚点 `APPROVED_ARCHIVE_SHA256` 来自既有审核记录而非本清单，因此"改写提案并同步
    改写清单哈希"依然失败；`archived_path` 必须解析到归档根内。
    """
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"archive manifest {manifest_path} is unreadable: {exc}"]
    entries = manifest.get("proposals")
    if not isinstance(entries, list):
        return [f"archive manifest {manifest_path} has no 'proposals' list"]

    archive_root = proposals_dir.parent.resolve()
    listed: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append(f"archive manifest entry is not an object: {entry!r}")
            continue
        knowledge_id = str(entry.get("knowledge_id", "<missing>"))
        if knowledge_id in listed:
            errors.append(f"archive manifest lists knowledge_id {knowledge_id!r} twice")
        listed[knowledge_id] = entry
        if entry.get("review_decision") != "approved" or entry.get("released") is not True:
            errors.append(f"{knowledge_id}: archive entry is not an approved/released proposal")
        original = str(entry.get("original_sha256", ""))
        anchor = APPROVED_ARCHIVE_SHA256.get(knowledge_id)
        if anchor is not None and original != anchor:
            errors.append(
                f"{knowledge_id}: original_sha256 {original} is not the hash bound by the human "
                f"review ({anchor}); editing the proposal and the manifest together cannot "
                f"authorize a release"
            )
        if entry.get("archived_sha256") not in (None, original):
            errors.append(f"{knowledge_id}: archived_sha256 disagrees with original_sha256")
        if anchor is not None and entry.get("archived_sha256") not in (None, anchor):
            errors.append(
                f"{knowledge_id}: archived_sha256 disagrees with the human-review hash {anchor}"
            )

        archived_rel = str(entry.get("archived_path", ""))
        archived, path_error = _resolve_archived_path(archive_root, archived_rel, knowledge_id)
        if path_error is not None:
            errors.append(path_error)
            continue
        assert archived is not None  # mypy: path_error None implies a resolved path
        if not archived.is_file():
            errors.append(f"{knowledge_id}: archived_path {archived_rel!r} does not exist")
            continue
        actual = hashlib.sha256(archived.read_bytes()).hexdigest()
        if actual != original:
            errors.append(
                f"{knowledge_id}: archived bytes {actual} do not match original_sha256 {original}"
            )
        if anchor is not None and actual != anchor:
            errors.append(
                f"{knowledge_id}: archived bytes {actual} do not match the hash bound by the "
                f"human review ({anchor})"
            )
        historical = str(entry.get("historical_staging_path", ""))
        staging_root = str(manifest.get("historical_staging_root", ""))
        if not staging_root or not historical.startswith(f"{staging_root}/"):
            errors.append(f"{knowledge_id}: historical_staging_path {historical!r} is unmapped")

    if manifest.get("archive_id") == "v0.5-approved-1" and set(listed) != set(
        APPROVED_ARCHIVE_SHA256
    ):
        errors.append(
            "v0.5-approved-1 archive must contain exactly the five human-reviewed proposals: "
            f"manifest={sorted(listed)}"
        )

    # 目录必须与清单**逐文件**一致：缺失、改名、任何多余的 *.json（含非 .v2.json）都失败。
    expected_names = {f"{knowledge_id}.v2.json" for knowledge_id in listed}
    actual_names = {path.name for path in proposals_dir.glob("*.json")}
    if expected_names != actual_names:
        errors.append(
            f"archive manifest/dir mismatch: manifest={sorted(expected_names)} "
            f"dir={sorted(actual_names)}"
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_DIR,
        help="proposal directory (default: the deliverable v0.5-approved-1 archive)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="archive manifest to cross-check (default: sibling archive_manifest.json)",
    )
    args = parser.parse_args()

    proposals_dir: Path = args.dir
    if not proposals_dir.is_dir():
        print(f"FAIL: proposal directory does not exist: {proposals_dir}")
        return 1

    files = sorted(proposals_dir.glob("*.json"))
    if not files:
        print(f"FAIL: no *.json proposals found in {proposals_dir}")
        return 1

    print(f"validating {len(files)} proposal(s) in {proposals_dir}")
    print("-" * 100)
    print(f"{'knowledge_id':<58} {'ver':<4} {'status':<6} file_sha256")
    print("-" * 100)

    errors: list[str] = []
    seen_ids: dict[str, str] = {}
    for path in files:
        raw = path.read_bytes()
        file_sha = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: not valid UTF-8 JSON: {exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{path.name}: proposal root must be a JSON object")
            continue

        label = path.name
        local_errors = check_unit(payload, label=label)
        errors.extend(local_errors)

        knowledge_id = str(payload.get("knowledge_id", "<missing>"))
        version = str(payload.get("version", "?"))
        status = str(payload.get("review_status", "?"))
        previous = seen_ids.get(knowledge_id)
        if previous is not None:
            errors.append(f"{label}: duplicate knowledge_id {knowledge_id!r} also in {previous}")
        else:
            seen_ids[knowledge_id] = label

        verdict = "OK" if not local_errors else "FAIL"
        print(f"{knowledge_id:<58} {version:<4} {status:<6} {file_sha[:16]}…  {verdict}")

    manifest_path: Path | None = args.manifest
    if manifest_path is None and (
        args.dir == DEFAULT_DIR or (proposals_dir.parent / "archive_manifest.json").is_file()
    ):
        candidate = proposals_dir.parent / "archive_manifest.json"
        if candidate.is_file():
            manifest_path = candidate
    if manifest_path is None and args.dir == DEFAULT_DIR:
        # 默认归档必须带清单；缺失清单时不能只按提案自洽性判定通过。
        errors.append(
            f"the deliverable archive manifest is missing: {DEFAULT_MANIFEST}; an unverifiable "
            f"archive must fail instead of passing"
        )
    if manifest_path is not None:
        manifest_errors = check_manifest(proposals_dir, manifest_path)
        errors.extend(manifest_errors)
        verdict = "OK" if not manifest_errors else "FAIL"
        print(f"archive manifest {manifest_path}: {verdict}")

    print("-" * 100)
    if errors:
        print(f"RESULT: FAIL ({len(errors)} problem(s))")
        for item in errors:
            print(f"  - {item}")
        return 1
    print(f"RESULT: OK — {len(files)} draft proposal(s) parse, are draft/null/null, reachable, hash-consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
