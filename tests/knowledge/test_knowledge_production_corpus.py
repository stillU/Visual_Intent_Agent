"""生产语料状态测试：`knowledge_base/v0.4/` 目前**全部是 draft**。

这些测试同时是"生产内容仍是 draft、没有伪造 approved、没有把测试 approved 当
生产审核证据"的离线守卫。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from visual_intent_agent.domain import INTENT_PATHS
from visual_intent_agent.knowledge import (
    KNOWLEDGE_PATHS,
    KNOWLEDGE_RETRIEVAL_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    KNOWLEDGE_TOKENIZER_VERSION,
    MAX_CONTENT_CHARS,
    ReviewStatus,
    candidate_values_for,
    compute_content_hash,
    load_corpus,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = PROJECT_ROOT / "knowledge_base" / "v0.4"


def test_production_corpus_loads_and_is_draft_only():
    corpus = load_corpus(CORPUS_DIR)
    assert corpus.corpus_version == "v0.4-draft-1"
    assert corpus.all_unit_count == 9
    assert Counter(unit.applicable_path for unit in corpus.all_units) == {
        "lighting.character": 3,
        "composition.framing": 3,
        "camera.depth_of_field": 3,
    }
    # 人工审核尚未发生：全部 draft，绝不把测试夹具的 approved 当成生产证据。
    assert {unit.review_status for unit in corpus.all_units} == {ReviewStatus.DRAFT}
    assert all(unit.reviewer is None and unit.reviewed_at is None for unit in corpus.all_units)
    assert corpus.build_units() == ()


def test_production_manifest_records_frozen_versions():
    corpus = load_corpus(CORPUS_DIR)
    manifest = corpus.manifest
    assert manifest.schema_version == KNOWLEDGE_SCHEMA_VERSION
    assert manifest.tokenizer_version == KNOWLEDGE_TOKENIZER_VERSION
    assert manifest.retrieval_version == KNOWLEDGE_RETRIEVAL_VERSION
    assert {entry.path for entry in manifest.files} == {
        "lighting.character.jsonl",
        "composition.framing.jsonl",
        "camera.depth_of_field.jsonl",
    }


def test_production_units_are_authorized_and_fully_sourced():
    corpus = load_corpus(CORPUS_DIR)
    for unit in corpus.all_units:
        assert unit.applicable_path in KNOWLEDGE_PATHS
        assert unit.candidate_value in candidate_values_for(unit.applicable_path)
        assert unit.content_hash == compute_content_hash(unit.content)
        assert 0 < len(unit.content) <= MAX_CONTENT_CHARS
        assert unit.keywords and unit.aliases
        assert unit.target_models
        for condition in unit.conditions:
            assert condition.path in INTENT_PATHS
        source = unit.source
        assert source.url is not None or source.repository_path is not None
        assert source.license is not None or source.original_declaration is not None
        assert source.source_revision is not None or source.source_date is not None
        assert source.repository_path is not None
        assert source.repository_path.startswith("knowledge_base/v0.4/")
        assert source.locator == unit.knowledge_id


def test_production_units_do_not_claim_official_model_capabilities():
    """官方 Qwen-Image `qwen-image-3.0` 映射未核实，本批全部是项目原创规则。"""
    corpus = load_corpus(CORPUS_DIR)
    assert all(unit.source.source_type.value == "project_original" for unit in corpus.all_units)
    assert all("any" in unit.target_models for unit in corpus.all_units)
    assert all(unit.source.source_date is None for unit in corpus.all_units)