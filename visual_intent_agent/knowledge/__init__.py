"""v0.4 知识库公开面（Step 01：合同 + 加载；Step 02：检索 + KnowledgeBundle）。

    from visual_intent_agent.knowledge import (
        KNOWLEDGE_PATHS, load_corpus, KnowledgeCorpus, KnowledgeUnit,
        KnowledgeSource, KnowledgeCondition, ReviewStatus,
        RetrievalRequest, LocalKnowledgeEngine, KnowledgeBundle, tokenize,
    )

边界：

- **有**：严格冻结的知识合同、来源/条件/审核字段、候选值授权（复用
  `prompt_engine.engine.DELEGATED_CANDIDATES`）、JSONL 权威语料加载与
  清单/哈希/版本校验、approved-only 构建消费（Step 01）；确定性本地词法检索
  （`keyword.v1`/`lexical.v1`）、条件判定、`KnowledgeBundle` 与检索 Protocol
  （Step 02）。
- **没有**（属后续步骤）：`PromptEngine` 接入（Step 03）、Repository 持久化
  （Step 03）、CLI 与开关（Step 04）。Step 02 的检索结果**不改变**任何既有生成
  行为，且不提供开关：关闭 RAG 由调用方不调用检索实现。

依赖边界：本包**不在模块顶层** import `prompt_engine`。候选值只在函数调用时
惰性读取 `prompt_engine.engine.DELEGATED_CANDIDATES`，因此 Step 03 让
`prompt_engine` 依赖 `knowledge` 时不会形成循环导入，也不存在第二份候选表。
"""

from __future__ import annotations

from .bundle import (
    ELIGIBILITY_SNAPSHOT_VERSION,
    KNOWLEDGE_BUNDLE_ID_PREFIX,
    MAX_RETRIEVAL_PATHS,
    SUPPORTED_ELIGIBILITY_SNAPSHOT_VERSIONS,
    TOP_K,
    AdoptionOutcome,
    AdoptionRejectionReason,
    BundleStatus,
    KnowledgeAdoptionDecision,
    KnowledgeBundle,
    KnowledgeEligibilitySnapshot,
    KnowledgeQuery,
    KnowledgeRecommendation,
    KnowledgeRejection,
    KnowledgeUnitHit,
    PathRetrievalResult,
    RejectionReason,
    RetrievalOutcome,
    compute_corpus_fingerprint,
)
from .candidates import (
    KNOWLEDGE_PATHS,
    candidate_values_for,
    delegated_candidates,
    is_authorized_candidate,
)
from .loader import load_corpus
from .models import (
    GENERIC_TARGET_MODEL,
    KNOWLEDGE_EMPTY_CORPUS,
    KNOWLEDGE_DUPLICATE_ID,
    KNOWLEDGE_FILE_MISSING,
    KNOWLEDGE_FILE_UNLISTED,
    KNOWLEDGE_HASH_MISMATCH,
    KNOWLEDGE_INVALID_ENCODING,
    KNOWLEDGE_INVALID_UNIT,
    KNOWLEDGE_MANIFEST_INVALID,
    KNOWLEDGE_MANIFEST_MISSING,
    KNOWLEDGE_RETRIEVAL_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    KNOWLEDGE_SCHEMA_VERSION_UNSUPPORTED,
    KNOWLEDGE_TOKENIZER_VERSION,
    KNOWLEDGE_UNIT_COUNT_MISMATCH,
    KNOWLEDGE_UNSAFE_PATH,
    KNOWLEDGE_VERSION_MISMATCH,
    MANIFEST_FILENAME,
    MAX_CONDITIONS_PER_UNIT,
    MAX_CONDITION_VALUES,
    MAX_CONTENT_CHARS,
    MAX_ERROR_MESSAGE_CHARS,
    MAX_METADATA_CHARS,
    MAX_REPOSITORY_PATH_CHARS,
    MAX_TARGET_MODEL_CHARS,
    MAX_TERM_CHARS,
    MAX_TERMS,
    MAX_URL_CHARS,
    SUPPORTED_KNOWLEDGE_SCHEMA_VERSIONS,
    ConditionOperator,
    KnowledgeCondition,
    KnowledgeCorpus,
    KnowledgeCorpusFile,
    KnowledgeError,
    KnowledgeManifest,
    KnowledgeSource,
    KnowledgeUnit,
    ReviewStatus,
    SourceType,
    compute_content_hash,
)
from .retrieval import (
    SUBJECT_COUNT_PATH,
    KnowledgeEngine,
    LocalKnowledgeEngine,
    QueryBuilder,
    RetrievalRequest,
    build_query_text,
    evaluate_conditions,
    normalize_text,
    read_confirmed_intent_value,
    score_tokens,
    target_model_applies,
    tokenize,
    unit_retrieval_tokens,
)

__all__ = [
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
    # Step 02 追加：确定性检索与编译级 KnowledgeBundle
    # （只追加新名字，不改动 Step 01 已冻结的合同名/语义）
    "KNOWLEDGE_BUNDLE_ID_PREFIX",
    "TOP_K",
    "MAX_RETRIEVAL_PATHS",
    "SUBJECT_COUNT_PATH",
    "ELIGIBILITY_SNAPSHOT_VERSION",
    "SUPPORTED_ELIGIBILITY_SNAPSHOT_VERSIONS",
    "BundleStatus",
    "RetrievalOutcome",
    "RejectionReason",
    "AdoptionRejectionReason",
    "AdoptionOutcome",
    "KnowledgeQuery",
    "KnowledgeEligibilitySnapshot",
    "KnowledgeUnitHit",
    "KnowledgeRejection",
    "PathRetrievalResult",
    "KnowledgeRecommendation",
    "KnowledgeAdoptionDecision",
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
    "target_model_applies",
    "build_query_text",
]