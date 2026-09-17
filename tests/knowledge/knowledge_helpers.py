"""`tests/knowledge/` 共享工具（唯一命名模块，遵循 ARCHITECTURE.md 第 7 节）。

只构造**测试用**临时语料；这里的 `approved` 是测试夹具，**不是**生产审核证据，
绝不能作为真实知识库已审核的依据。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from visual_intent_agent.domain import Resolution, ResolutionRecord, VisualIntent
from visual_intent_agent.knowledge import KnowledgeCorpus, RetrievalRequest, load_corpus

#: 与生产合同一致：内容哈希必须等于 sha256(content)。
def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def source_payload(**overrides: Any) -> dict[str, Any]:
    """一个来源完整的测试来源（项目原创 + 显式原创声明）。"""
    payload: dict[str, Any] = {
        "source_type": "project_original",
        "title": "测试来源（fixture）",
        "url": None,
        "repository_path": "units.jsonl",
        "locator": "fixture",
        "source_revision": "v0.4-test",
        "source_date": None,
        "license": "internal-test",
        "original_declaration": "测试夹具原创声明；非生产审核证据。",
    }
    payload.update(overrides)
    return payload


def unit_payload(
    *,
    knowledge_id: str = "lighting.character.fixture",
    applicable_path: str = "lighting.character",
    candidate_value: str = "soft",
    content: str | None = None,
    version: str = "1",
    keywords: tuple[str, ...] = ("关键词", "keyword"),
    aliases: tuple[str, ...] = ("别名",),
    conditions: list[dict[str, Any]] | None = None,
    target_models: tuple[str, ...] = ("any",),
    source: dict[str, Any] | None = None,
    review_status: str = "draft",
    reviewer: str | None = None,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    """构造一条知识单元 payload（默认 draft，不给任何审核字段）。"""
    if content is None:
        content = f"测试规则正文：{knowledge_id}"
    return {
        "knowledge_id": knowledge_id,
        "version": version,
        "content_hash": content_hash(content),
        "content": content,
        "applicable_path": applicable_path,
        "candidate_value": candidate_value,
        "keywords": list(keywords),
        "aliases": list(aliases),
        "conditions": conditions if conditions is not None else [],
        "target_models": list(target_models),
        "source": source if source is not None else source_payload(),
        "review_status": review_status,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
    }


def approved_payload(**overrides: Any) -> dict[str, Any]:
    """测试专用 approved 单元（带伪审核字段，仅用于夹具，非生产证据）。"""
    payload = unit_payload(
        review_status="approved",
        reviewer="fixture-reviewer（测试夹具，非真实人工审核）",
        reviewed_at="2026-09-16T00:00:00+00:00",
        **overrides,
    )
    return payload


def jsonl_text(units: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(unit, ensure_ascii=False, sort_keys=True) + "\n" for unit in units)


def write_corpus(
    directory: Path,
    units: list[dict[str, Any]],
    *,
    file_name: str = "units.jsonl",
    schema_version: str = "knowledge.v1",
    corpus_version: str = "v0.4-test",
    tokenizer_version: str = "keyword.v1",
    retrieval_version: str = "lexical.v1",
    unit_count: int | None = None,
    sha256: str | None = None,
) -> Path:
    """写单文件语料 + 清单；`sha256`/`unit_count` 可覆盖以构造篡改场景。"""
    directory.mkdir(parents=True, exist_ok=True)
    text = jsonl_text(units)
    (directory / file_name).write_text(text, encoding="utf-8")
    manifest = {
        "schema_version": schema_version,
        "corpus_version": corpus_version,
        "tokenizer_version": tokenizer_version,
        "retrieval_version": retrieval_version,
        "files": [
            {
                "path": file_name,
                "sha256": sha256 if sha256 is not None else hashlib.sha256(text.encode()).hexdigest(),
                "unit_count": len(units) if unit_count is None else unit_count,
            }
        ],
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return directory


def write_manifest(directory: Path, files: list[dict[str, Any]], **overrides: Any) -> Path:
    """直接写清单（用于手工构造路径/版本/文件条目场景）。"""
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": overrides.get("schema_version", "knowledge.v1"),
        "corpus_version": overrides.get("corpus_version", "v0.4-test"),
        "tokenizer_version": overrides.get("tokenizer_version", "keyword.v1"),
        "retrieval_version": overrides.get("retrieval_version", "lexical.v1"),
        "files": files,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return directory


def write_jsonl(directory: Path, file_name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / file_name).write_text(text, encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# Step 02 追加：检索测试的 Intent / 请求 / 语料夹具（同样是测试夹具，非生产证据）
# ---------------------------------------------------------------------------


def build_intent(
    values: dict[str, object] | None = None,
    *,
    delegated: tuple[str, ...] = (),
    pinned: tuple[str, ...] = (),
) -> "VisualIntent":
    """构造一个"已确认"的 VisualIntent：给定字段值 + 显式 user_delegated 路径。

    只用于测试；这里没有任何"未确认聊天"的概念，调用方给出的即视为已确认。
    """
    facets: dict[str, dict[str, object]] = {}
    for path, value in (values or {}).items():
        facet_name, _, field_name = path.partition(".")
        facets.setdefault(facet_name, {})[field_name] = value
    resolutions = {
        path: ResolutionRecord(resolution=Resolution.USER_DELEGATED) for path in delegated
    }
    return VisualIntent(**facets, resolutions=resolutions, pinned_paths=frozenset(pinned))


def build_request(
    intent: "VisualIntent",
    *,
    target_model: str = "qwen-image-3.0",
    pending_paths: tuple[str, ...] = (),
    suffix: str = "1",
) -> "RetrievalRequest":
    """构造一个绑定四个 ID 的检索请求（ID 是测试占位值）。"""
    return RetrievalRequest(
        session_id=f"ses_test_{suffix}",
        intent_revision_id=f"irev_test_{suffix}",
        execution_revision_id=f"erev_test_{suffix}",
        confirmation_id=f"cnf_test_{suffix}",
        intent=intent,
        target_model=target_model,
        pending_paths=tuple(pending_paths),
    )


def load_test_corpus(directory: Path, units: list[dict[str, Any]], **overrides: Any) -> "KnowledgeCorpus":
    """写一个测试语料并加载为 `KnowledgeCorpus`（approved 夹具，非生产审核证据）。"""
    return load_corpus(write_corpus(directory, units, **overrides))