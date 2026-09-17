"""工具级回归：真实子进程调用 ``tools/validate_knowledge_review_proposals.py``。

这些用例不导入工具内部函数，而是像审计员一样运行脚本并断言**退出码与输出**，覆盖
任务书 F1 的加固要求：

- 默认校验随项目交付的审核归档（不依赖被忽略的 ``data/``）→ 退出码 0；
- 显式 ``--dir``（可带 ``--manifest``；无显式清单时自动发现同级清单）→ 退出码 0；
- 缺失、字节篡改、多余文件 → 退出码 1；
- ``archived_path`` 绝对路径 / ``..`` 逃逸到归档根之外（即使目标字节真实）→ 退出码 1；
- **清单哈希同步伪造**（同时改写提案字节与清单里的 ``original_sha256`` /
  ``archived_sha256``）→ 退出码 1，因为工具用内置审核锚点独立判断；
- 重新序列化与额外字段篡改 → 退出码 1。

破坏性操作一律只作用于 ``tmp_path`` 的副本，绝不改写真实归档。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL = PROJECT_ROOT / "tools" / "validate_knowledge_review_proposals.py"

ARCHIVE_DIR = PROJECT_ROOT / "docs" / "knowledge_reviews" / "v0.5-approved-1"
ARCHIVE_PROPOSALS_DIR = ARCHIVE_DIR / "proposals"
ARCHIVE_MANIFEST_PATH = ARCHIVE_DIR / "archive_manifest.json"

#: 用于注入篡改的获批提案（真实归档中的原字节副本）。
VICTIM_ID = "lighting.character.soft_for_tight_framing"
VICTIM_NAME = f"{VICTIM_ID}.v2.json"


def _run_tool(*args: object) -> subprocess.CompletedProcess[str]:
    """以当前解释器在项目根运行工具，收集退出码与输出。"""
    return subprocess.run(
        [sys.executable, str(TOOL), *(str(arg) for arg in args)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _copy_archive(tmp_path: Path) -> Path:
    """把可交付归档整目录复制到 tmp_path，返回副本内的归档根目录。"""
    target = tmp_path / "archive"
    shutil.copytree(ARCHIVE_DIR, target)
    return target


def _run_copy(archive_root: Path) -> subprocess.CompletedProcess[str]:
    return _run_tool(
        "--dir",
        archive_root / "proposals",
        "--manifest",
        archive_root / "archive_manifest.json",
    )


def _write_manifest(archive_root: Path, manifest: dict) -> None:
    (archive_root / "archive_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _load_manifest(archive_root: Path) -> dict:
    return json.loads((archive_root / "archive_manifest.json").read_text(encoding="utf-8"))


def _entry(manifest: dict, knowledge_id: str = VICTIM_ID) -> dict:
    matches = [item for item in manifest["proposals"] if item["knowledge_id"] == knowledge_id]
    assert len(matches) == 1
    return matches[0]


# ---------------------------------------------------------------------------
# 默认归档 / 显式 --dir
# ---------------------------------------------------------------------------


def test_default_invocation_passes_on_the_deliverable_archive():
    result = _run_tool()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: OK" in result.stdout
    assert "archive manifest" in result.stdout


def test_explicit_dir_and_manifest_validate_a_verified_copy(tmp_path):
    archive_root = _copy_archive(tmp_path)

    result = _run_copy(archive_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: OK" in result.stdout


def test_explicit_dir_without_manifest_auto_discovers_sibling(tmp_path):
    archive_root = _copy_archive(tmp_path)

    result = _run_tool("--dir", archive_root / "proposals")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "archive manifest" in result.stdout


# ---------------------------------------------------------------------------
# 拒绝：缺失 / 字节篡改 / 多余文件
# ---------------------------------------------------------------------------


def test_missing_archived_proposal_is_rejected(tmp_path):
    archive_root = _copy_archive(tmp_path)
    (archive_root / "proposals" / VICTIM_NAME).unlink()

    result = _run_copy(archive_root)

    assert result.returncode == 1
    assert "RESULT: FAIL" in result.stdout
    assert "does not exist" in result.stdout or "mismatch" in result.stdout


def test_byte_tampered_archived_proposal_is_rejected(tmp_path):
    archive_root = _copy_archive(tmp_path)
    victim = archive_root / "proposals" / VICTIM_NAME
    victim.write_bytes(victim.read_bytes() + b" ")

    result = _run_copy(archive_root)

    assert result.returncode == 1
    assert "do not match original_sha256" in result.stdout
    assert "human review" in result.stdout


@pytest.mark.parametrize("extra_name", ["rogue.extra.v2.json", "rogue.json"])
def test_extra_proposal_file_is_rejected(tmp_path, extra_name):
    archive_root = _copy_archive(tmp_path)
    shutil.copyfile(
        ARCHIVE_PROPOSALS_DIR / VICTIM_NAME,
        archive_root / "proposals" / extra_name,
    )

    result = _run_copy(archive_root)

    assert result.returncode == 1
    assert "archive manifest/dir mismatch" in result.stdout


# ---------------------------------------------------------------------------
# 拒绝：清单哈希同步伪造（工具内置审核锚点）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mutation", ["content", "extra_field", "reserialized"])
def test_manifest_hash_synchronized_forgery_is_still_rejected(tmp_path, mutation):
    """同时改写提案字节与清单哈希仍必须失败——锚点来自既有审核记录而非清单。"""
    archive_root = _copy_archive(tmp_path)
    victim = archive_root / "proposals" / VICTIM_NAME
    payload = json.loads(victim.read_text(encoding="utf-8"))

    if mutation == "content":
        payload["content"] = payload["content"] + "（伪造追加）"
        payload["content_hash"] = hashlib.sha256(payload["content"].encode("utf-8")).hexdigest()
    elif mutation == "extra_field":
        payload["rogue_note"] = "tampered"
    elif mutation != "reserialized":  # pragma: no cover - guarded by parametrize
        raise AssertionError(f"unknown mutation {mutation!r}")

    # 重新序列化：即使语义不变，字节也已改变，清单必须能独立识破。
    forged = (json.dumps(payload, ensure_ascii=False, indent=4) + "\n").encode("utf-8")
    victim.write_bytes(forged)
    forged_sha = hashlib.sha256(forged).hexdigest()

    manifest = _load_manifest(archive_root)
    entry = _entry(manifest)
    entry["original_sha256"] = forged_sha
    entry["archived_sha256"] = forged_sha
    _write_manifest(archive_root, manifest)

    result = _run_copy(archive_root)

    assert result.returncode == 1
    assert "human review" in result.stdout


# ---------------------------------------------------------------------------
# 拒绝：archived_path 越界（绝对路径 / .. 逃逸）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("escape", ["parent_relative", "absolute"])
def test_archived_path_outside_archive_root_is_rejected(tmp_path, escape):
    """目标文件字节真实存在且哈希相符，唯独路径在归档根之外——仍必须拒绝。"""
    archive_root = _copy_archive(tmp_path)
    genuine = (ARCHIVE_PROPOSALS_DIR / VICTIM_NAME).read_bytes()

    outside = tmp_path / "escape"
    outside.mkdir()
    (outside / VICTIM_NAME).write_bytes(genuine)

    manifest = _load_manifest(archive_root)
    entry = _entry(manifest)
    if escape == "parent_relative":
        entry["archived_path"] = f"../escape/{VICTIM_NAME}"
    else:
        entry["archived_path"] = str((outside / VICTIM_NAME).resolve())
    _write_manifest(archive_root, manifest)

    result = _run_copy(archive_root)

    assert result.returncode == 1
    if escape == "parent_relative":
        assert "escapes the archive root" in result.stdout
    else:
        assert "must be relative to the archive root" in result.stdout


def test_traversal_to_gitignored_data_staging_is_rejected(tmp_path):
    """典型攻击面：archived_path 指向被忽略的 data/staging，工具不得读取它。"""
    archive_root = _copy_archive(tmp_path)
    genuine = (ARCHIVE_PROPOSALS_DIR / VICTIM_NAME).read_bytes()

    staging = tmp_path / "data" / "staging" / "draft_proposals"
    staging.mkdir(parents=True)
    (staging / VICTIM_NAME).write_bytes(genuine)

    manifest = _load_manifest(archive_root)
    _entry(manifest)["archived_path"] = f"../data/staging/draft_proposals/{VICTIM_NAME}"
    _write_manifest(archive_root, manifest)

    result = _run_copy(archive_root)

    assert result.returncode == 1
    assert "escapes the archive root" in result.stdout
