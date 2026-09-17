"""JSONL 权威语料的加载与完整性校验（v0.4 Step 01）。

加载流程（确定性、离线、无网络）：

1. 读取 `manifest.json`（缺失/畸形 → `knowledge.manifest_missing` /
   `knowledge.manifest_invalid`）；
2. 校验 Schema 版本受支持（`knowledge.schema_version_unsupported`）；
3. 逐个清单文件：解析安全相对路径、确认存在、比对 `sha256` 文件哈希
   （`knowledge.unsafe_path` / `knowledge.file_missing` / `knowledge.hash_mismatch`）；
4. 逐行解析 JSONL，构造严格 `KnowledgeUnit`（任何非法路径/候选/来源/审核字段
   → `knowledge.invalid_unit`）；同一语料内重复 `knowledge_id` →
   `knowledge.duplicate_id`（同 ID 不允许静默覆盖，更新须新版本）；
5. 拒绝清单之外的多余 `.jsonl` 文件（`knowledge.file_unlisted`）；
6. 返回 `KnowledgeCorpus`：`all_units` 供审计，`build_units()` 只给 approved。

安全边界：`content` 始终是不可信数据，只被 JSON 解码为字符串；本模块不执行、
不解释、不把正文当指令，也不访问网络。检索属 Step 02，本模块只提供加载接口。
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from .models import (
    KNOWLEDGE_DUPLICATE_ID,
    KNOWLEDGE_EMPTY_CORPUS,
    KNOWLEDGE_FILE_MISSING,
    KNOWLEDGE_FILE_UNLISTED,
    KNOWLEDGE_HASH_MISMATCH,
    KNOWLEDGE_INVALID_ENCODING,
    KNOWLEDGE_INVALID_UNIT,
    KNOWLEDGE_MANIFEST_INVALID,
    KNOWLEDGE_MANIFEST_MISSING,
    KNOWLEDGE_RETRIEVAL_VERSION,
    KNOWLEDGE_SCHEMA_VERSION_UNSUPPORTED,
    KNOWLEDGE_TOKENIZER_VERSION,
    KNOWLEDGE_UNIT_COUNT_MISMATCH,
    KNOWLEDGE_UNSAFE_PATH,
    KNOWLEDGE_VERSION_MISMATCH,
    MANIFEST_FILENAME,
    SUPPORTED_KNOWLEDGE_SCHEMA_VERSIONS,
    KnowledgeCorpus,
    KnowledgeError,
    KnowledgeManifest,
    KnowledgeUnit,
)


def _safe_relative(raw: str, *, what: str) -> PurePosixPath:
    """把清单/来源里的相对路径规范成安全 `PurePosixPath`（拒绝逃逸与绝对路径）。"""
    if not isinstance(raw, str) or not raw.strip():
        raise KnowledgeError(KNOWLEDGE_UNSAFE_PATH, f"{what} must be a non-empty relative path")
    if "\\" in raw or "\x00" in raw:
        raise KnowledgeError(KNOWLEDGE_UNSAFE_PATH, f"{what} {raw!r} contains an unsafe character")
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or any(part in {"..", ""} for part in candidate.parts):
        raise KnowledgeError(
            KNOWLEDGE_UNSAFE_PATH,
            f"{what} {raw!r} must stay inside the corpus directory",
        )
    return candidate


def _resolve_inside(base: Path, relative: PurePosixPath, *, what: str) -> Path:
    base_resolved = base.resolve()
    resolved = (base_resolved / Path(*relative.parts)).resolve()
    if resolved != base_resolved and base_resolved not in resolved.parents:
        raise KnowledgeError(
            KNOWLEDGE_UNSAFE_PATH,
            f"{what} {relative.as_posix()!r} escapes the corpus directory",
        )
    return resolved


def _load_manifest(base: Path) -> KnowledgeManifest:
    manifest_path = base / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise KnowledgeError(
            KNOWLEDGE_MANIFEST_MISSING,
            f"corpus manifest {manifest_path} does not exist; JSONL is authoritative but the "
            "manifest is required to verify it",
        )
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeError(
            KNOWLEDGE_MANIFEST_INVALID, f"manifest {manifest_path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise KnowledgeError(KNOWLEDGE_MANIFEST_INVALID, "manifest root must be a JSON object")
    try:
        manifest = KnowledgeManifest.model_validate(raw)
    except ValidationError as exc:
        raise KnowledgeError(
            KNOWLEDGE_MANIFEST_INVALID, f"manifest {manifest_path} failed contract validation: {exc}"
        ) from exc
    if manifest.schema_version not in SUPPORTED_KNOWLEDGE_SCHEMA_VERSIONS:
        raise KnowledgeError(
            KNOWLEDGE_SCHEMA_VERSION_UNSUPPORTED,
            f"manifest schema_version {manifest.schema_version!r} is not supported "
            f"(supported: {sorted(SUPPORTED_KNOWLEDGE_SCHEMA_VERSIONS)})",
        )
    if manifest.tokenizer_version != KNOWLEDGE_TOKENIZER_VERSION:
        raise KnowledgeError(
            KNOWLEDGE_VERSION_MISMATCH,
            f"manifest tokenizer_version {manifest.tokenizer_version!r} does not match the "
            f"frozen v0.4 tokenizer version {KNOWLEDGE_TOKENIZER_VERSION!r}",
        )
    if manifest.retrieval_version != KNOWLEDGE_RETRIEVAL_VERSION:
        raise KnowledgeError(
            KNOWLEDGE_VERSION_MISMATCH,
            f"manifest retrieval_version {manifest.retrieval_version!r} does not match the "
            f"frozen v0.4 retrieval version {KNOWLEDGE_RETRIEVAL_VERSION!r}",
        )
    return manifest


def _parse_units(text: str, *, rel_path: str) -> list[KnowledgeUnit]:
    units: list[KnowledgeUnit] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise KnowledgeError(
                KNOWLEDGE_INVALID_UNIT, f"{rel_path}:{line_number} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise KnowledgeError(
                KNOWLEDGE_INVALID_UNIT,
                f"{rel_path}:{line_number} must be a JSON object (one knowledge unit per line)",
            )
        try:
            units.append(KnowledgeUnit.model_validate(payload))
        except ValidationError as exc:
            raise KnowledgeError(
                KNOWLEDGE_INVALID_UNIT,
                f"{rel_path}:{line_number} failed KnowledgeUnit contract validation: {exc}",
            ) from exc
    return units


def load_corpus(corpus_dir: str | Path) -> KnowledgeCorpus:
    """加载并**完整校验**一个 JSONL 权威语料目录。

    只读、确定性、离线；不执行正文、不触网、不写任何文件。
    """
    base = Path(corpus_dir)
    if not base.is_dir():
        raise KnowledgeError(
            KNOWLEDGE_MANIFEST_MISSING, f"corpus directory {base} does not exist"
        )
    manifest = _load_manifest(base)

    listed: set[str] = set()
    seen_ids: dict[str, str] = {}
    all_units: list[KnowledgeUnit] = []

    for entry in manifest.files:
        relative = _safe_relative(entry.path, what="manifest file path")
        rel_posix = relative.as_posix()
        if rel_posix in listed:
            raise KnowledgeError(
                KNOWLEDGE_MANIFEST_INVALID, f"manifest lists {rel_posix!r} more than once"
            )
        listed.add(rel_posix)

        file_path = _resolve_inside(base, relative, what="manifest file path")
        if not file_path.is_file():
            raise KnowledgeError(
                KNOWLEDGE_FILE_MISSING, f"manifest lists {rel_posix!r} but the file is missing"
            )
        data = file_path.read_bytes()
        digest = sha256(data).hexdigest()
        if digest != entry.sha256:
            raise KnowledgeError(
                KNOWLEDGE_HASH_MISMATCH,
                f"{rel_posix!r} sha256 {digest} does not match the manifest {entry.sha256}; "
                "tampered corpus files are rejected",
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise KnowledgeError(
                KNOWLEDGE_INVALID_ENCODING, f"{rel_posix!r} is not valid UTF-8: {exc}"
            ) from exc

        units = _parse_units(text, rel_path=rel_posix)
        if len(units) != entry.unit_count:
            raise KnowledgeError(
                KNOWLEDGE_UNIT_COUNT_MISMATCH,
                f"{rel_posix!r} declares unit_count={entry.unit_count} but contains "
                f"{len(units)} units",
            )
        for unit in units:
            previous = seen_ids.get(unit.knowledge_id)
            if previous is not None:
                raise KnowledgeError(
                    KNOWLEDGE_DUPLICATE_ID,
                    f"knowledge_id {unit.knowledge_id!r} appears in both {previous!r} and "
                    f"{rel_posix!r}; duplicate IDs are rejected and updates require a new version",
                )
            seen_ids[unit.knowledge_id] = rel_posix
        all_units.extend(units)

    base_resolved = base.resolve()
    actual_files: set[str] = set()
    for found in sorted(base_resolved.rglob("*.jsonl")):
        if found.is_file():
            actual_files.add(found.relative_to(base_resolved).as_posix())
    unlisted = sorted(actual_files - listed)
    if unlisted:
        raise KnowledgeError(
            KNOWLEDGE_FILE_UNLISTED,
            f"corpus contains unlisted JSONL file(s) {unlisted}; every authoritative unit must "
            "be covered by the manifest hashes",
        )

    if not all_units:
        raise KnowledgeError(KNOWLEDGE_EMPTY_CORPUS, "corpus contains no knowledge units")

    return KnowledgeCorpus(manifest=manifest, all_units=tuple(all_units))


__all__ = ["load_corpus"]