"""Step 01 公开面测试：再导出、冻结边界、无顶层 prompt_engine 依赖。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

import visual_intent_agent.knowledge as knowledge
from visual_intent_agent.domain import INTENT_PATHS

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_PUBLIC_NAMES = {
    # 路径与候选
    "KNOWLEDGE_PATHS",
    "delegated_candidates",
    "candidate_values_for",
    "is_authorized_candidate",
    # 加载
    "load_corpus",
    # 常量
    "KNOWLEDGE_SCHEMA_VERSION",
    "SUPPORTED_KNOWLEDGE_SCHEMA_VERSIONS",
    "KNOWLEDGE_TOKENIZER_VERSION",
    "KNOWLEDGE_RETRIEVAL_VERSION",
    "MAX_CONTENT_CHARS",
    "MAX_TERM_CHARS",
    "MAX_TERMS",
    "MAX_CONDITION_VALUES",
    "MAX_CONDITIONS_PER_UNIT",
    "MAX_TARGET_MODEL_CHARS",
    "MAX_METADATA_CHARS",
    "MAX_URL_CHARS",
    "MAX_REPOSITORY_PATH_CHARS",
    "MAX_ERROR_MESSAGE_CHARS",
    "GENERIC_TARGET_MODEL",
    "MANIFEST_FILENAME",
    "compute_content_hash",
    # 枚举
    "ReviewStatus",
    "SourceType",
    "ConditionOperator",
    # 合同模型
    "KnowledgeCondition",
    "KnowledgeSource",
    "KnowledgeUnit",
    "KnowledgeCorpusFile",
    "KnowledgeManifest",
    "KnowledgeCorpus",
    # 错误
    "KnowledgeError",
    "KNOWLEDGE_MANIFEST_MISSING",
    "KNOWLEDGE_MANIFEST_INVALID",
    "KNOWLEDGE_SCHEMA_VERSION_UNSUPPORTED",
    "KNOWLEDGE_VERSION_MISMATCH",
    "KNOWLEDGE_UNSAFE_PATH",
    "KNOWLEDGE_FILE_MISSING",
    "KNOWLEDGE_FILE_UNLISTED",
    "KNOWLEDGE_HASH_MISMATCH",
    "KNOWLEDGE_INVALID_ENCODING",
    "KNOWLEDGE_INVALID_UNIT",
    "KNOWLEDGE_DUPLICATE_ID",
    "KNOWLEDGE_UNIT_COUNT_MISMATCH",
    "KNOWLEDGE_EMPTY_CORPUS",
    # Step 02 追加：检索与 KnowledgeBundle（不改变 Step 01 已冻结的合同名/语义）
    "KNOWLEDGE_BUNDLE_ID_PREFIX",
    "TOP_K",
    "MAX_RETRIEVAL_PATHS",
    "SUBJECT_COUNT_PATH",
    # v0.4 F2 追加：适用性快照 / 消费端裁定（只追加，不改旧语义）
    "ELIGIBILITY_SNAPSHOT_VERSION",
    "SUPPORTED_ELIGIBILITY_SNAPSHOT_VERSIONS",
    "AdoptionRejectionReason",
    "AdoptionOutcome",
    "KnowledgeEligibilitySnapshot",
    "KnowledgeAdoptionDecision",
    "target_model_applies",
    "BundleStatus",
    "RetrievalOutcome",
    "RejectionReason",
    "KnowledgeQuery",
    "KnowledgeUnitHit",
    "KnowledgeRejection",
    "PathRetrievalResult",
    "KnowledgeRecommendation",
    "KnowledgeBundle",
    "compute_corpus_fingerprint",
    "KnowledgeEngine",
    "LocalKnowledgeEngine",
    "QueryBuilder",
    "RetrievalRequest",
    "normalize_text",
    "tokenize",
    "unit_retrieval_tokens",
    "score_tokens",
    "read_confirmed_intent_value",
    "evaluate_conditions",
    "build_query_text",
}


def test_public_surface_matches_the_frozen_names():
    assert set(knowledge.__all__) == EXPECTED_PUBLIC_NAMES
    for name in EXPECTED_PUBLIC_NAMES:
        assert hasattr(knowledge, name)


def test_knowledge_paths_are_a_subset_of_the_intent_whitelist():
    assert knowledge.KNOWLEDGE_PATHS <= INTENT_PATHS


def test_contract_models_are_frozen_and_forbid_extra():
    config = knowledge.KnowledgeUnit.model_config
    assert config["frozen"] is True
    assert config["extra"] == "forbid"


def test_candidate_lookup_returns_the_prompt_engine_mapping():
    from visual_intent_agent.prompt_engine.engine import DELEGATED_CANDIDATES

    assert knowledge.delegated_candidates() is DELEGATED_CANDIDATES
    assert (
        knowledge.candidate_values_for("lighting.character")
        == DELEGATED_CANDIDATES["lighting.character"]
    )


def test_candidate_lookup_for_known_and_unknown_paths():
    # 已授权路径返回候选集合；没有候选表的路径返回空元组，绝不猜测。
    assert knowledge.candidate_values_for("subject.description") == ()
    assert knowledge.is_authorized_candidate("lighting.character", "soft") is True
    assert knowledge.is_authorized_candidate("lighting.character", "harsh") is False
    assert knowledge.is_authorized_candidate("subject.description", "x") is False


def test_importing_knowledge_does_not_import_prompt_engine():
    """在干净解释器中 import knowledge 不得连带 import prompt_engine（防 Step 03 成环）。"""
    code = (
        "import sys; import visual_intent_agent.knowledge; "
        "print('visual_intent_agent.prompt_engine.engine' in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == "False"


def test_frozen_model_rejects_mutation():
    unit = knowledge.KnowledgeUnit.model_validate(
        {
            "knowledge_id": "lighting.character.api",
            "version": "1",
            "content_hash": knowledge.compute_content_hash("x"),
            "content": "x",
            "applicable_path": "lighting.character",
            "candidate_value": "soft",
            "keywords": ["k"],
            "target_models": ["any"],
            "source": {
                "source_type": "project_original",
                "title": "t",
                "repository_path": "knowledge_base/v0.4/lighting.character.jsonl",
                "locator": "l",
                "source_revision": "v1",
                "original_declaration": "own",
            },
        }
    )
    try:
        unit.candidate_value = "dramatic"  # type: ignore[misc]
    except ValidationError:
        return
    raise AssertionError("frozen KnowledgeUnit must reject attribute mutation")