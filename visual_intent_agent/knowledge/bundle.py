"""v0.4 Step 02：编译级 `KnowledgeBundle` 合同（严格冻结）。

一次编译会为**每条待实现路径分别检索**，再把结果合并成**一个**编译级
`KnowledgeBundle`。Bundle 是审计记录，不是新的用户授权：它只报告"检索到了哪些
已审核单元、采用/拒绝了哪个候选值、为什么回退"，绝不写回 `VisualIntent`、绝不
冒充 `EvidenceRef`、绝不绕过确认。

必须记录（任务书 `02_retrieval_bundle.md`）：

- `bundle_id`、session/intent/execution/confirmation IDs；
- 语料 `corpus_version`、`schema_version`、`tokenizer_version`、
  `retrieval_version` 与**语料指纹**（覆盖 manifest 逐文件 sha256）；
- 每路径的查询与路径、实际 Top-3 单元**不可变快照**（id/version/content_hash/
  来源/内容/score）、采用与拒绝原因、最终推荐、时间；
- 上限：最多 `MAX_RETRIEVAL_PATHS`(3) 条路径 × `TOP_K`(3) 个单元。

诊断 Bundle：无命中、approved 为空、语料读取/校验失败、没有待实现路径时**仍然**
产生 Bundle，只是 `status`/`outcome` 非 `ok`/`adopted` 且 `recommendations` 为空，
使调用方可以明确回退并解释原因。"关闭 RAG"由调用方**不调用**检索实现——本模块
没有开关。

`bundle_id` 是**每次检索唯一的记录 ID**（`new_id("kbu")` → `kbu_<uuid4hex>`），
因为 Bundle 是 append-only 审计记录：同一 confirmation 重复 compile 不得复用主键。
可复现性由内容字段承担（`corpus_fingerprint` + `queries` + `path_results` +
`recommendations`）；需要比较两次检索是否等价时，必须排除 `bundle_id` 与
`created_at`，不得把随机 ID/时间当作语料或排序稳定性的证据。

本模块不 import `prompt_engine`（模块顶层反向 import 会与 Step 03 成环），
也不触网、不读写文件、不执行任何正文。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from visual_intent_agent.domain import utc_now

from .candidates import KNOWLEDGE_PATHS
from .models import (
    MAX_CONDITIONS_PER_UNIT,
    KnowledgeCondition,
    KnowledgeManifest,
    KnowledgeSource,
    ReviewStatus,
    check_review_integrity,
    clean_target_models,
    ensure_review_utc,
)

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: Bundle ID 前缀（本步新增；Step 01 的 ID 前缀表未包含知识 Bundle）。
#: `kbu`（knowledge bundle unique）是冻结前缀表 `ses/msg/irev/...` 的**兼容扩展**：
#: 新前缀不冲突既有前缀，且 Bundle ID 每次检索唯一（append-only 主键不复用）。
KNOWLEDGE_BUNDLE_ID_PREFIX: str = "kbu"

#: 每条路径保留的单元快照上限。
TOP_K: int = 3

#: 单次编译检索的路径上限（本版知识路径恰好 3 条）。
MAX_RETRIEVAL_PATHS: int = 3

#: 命中单元适用性快照的合同版本（v0.4 F2 兼容扩展）。
#:
#: 版本标识落在**嵌套快照**里，而不是 `KnowledgeBundle.schema_version`：后者是语料
#: manifest 的 schema 版本（如 `knowledge.v1`），被 `_status_consistency` 与
#: `compute_corpus_fingerprint` 绑定；`retrieval_version`（`lexical.v1`）由
#: `retrieval.py` 冻结断言。复用它们会污染语料指纹或词法语义。
#: 缺省为 None 的整块快照表示"旧记录未提供条件证据"，绝不被当作无限适用。
ELIGIBILITY_SNAPSHOT_VERSION: str = "eligibility.v1"

#: 当前代码能解释的快照版本集合（升级须显式扩集合与评审；镜像
#: `SUPPORTED_KNOWLEDGE_SCHEMA_VERSIONS` 的加载器模式，保证旧版本仍可读）。
SUPPORTED_ELIGIBILITY_SNAPSHOT_VERSIONS: frozenset[str] = frozenset(
    {ELIGIBILITY_SNAPSHOT_VERSION}
)

_BUNDLE_ID = re.compile(r"^kbu_[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware UTC; naive datetimes are rejected")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    """JSON 序列化固定为 ISO 8601 带 `+00:00`（跨进程/跨时区逐字节稳定）。"""
    return value.astimezone(timezone.utc).isoformat()


_UtcDatetime = Annotated[
    datetime,
    AfterValidator(_ensure_utc),
    PlainSerializer(_iso_utc, return_type=str, when_used="json"),
]


def compute_corpus_fingerprint(manifest: KnowledgeManifest) -> str:
    """语料指纹：schema/corpus/tokenizer/retrieval 版本 + manifest 逐文件 sha256。

    逐文件条目按 path 排序，`json.dumps(sort_keys=True, ensure_ascii=False)` 后取
    sha256；因此文件在清单中的顺序不影响指纹，任何文件内容或版本变化都会改变它。
    这使 Bundle 可回溯到**确定的**语料版本，不能拿旧索引冒充新语料。
    """
    payload = {
        "schema_version": manifest.schema_version,
        "corpus_version": manifest.corpus_version,
        "tokenizer_version": manifest.tokenizer_version,
        "retrieval_version": manifest.retrieval_version,
        "files": sorted(
            (
                {"path": entry.path, "sha256": entry.sha256, "unit_count": entry.unit_count}
                for entry in manifest.files
            ),
            key=lambda item: item["path"],
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class BundleStatus(str, Enum):
    """编译级状态：语料可用且存在查询时才是 `ok`；其余都是可观测回退。"""

    OK = "ok"
    NO_PENDING_PATHS = "no_pending_paths"
    NO_APPROVED_UNITS = "no_approved_units"
    CORPUS_ERROR = "corpus_error"


class RetrievalOutcome(str, Enum):
    """单路径结果：只有 `adopted` 才产生最终推荐。"""

    ADOPTED = "adopted"
    NO_HIT = "no_hit"
    AMBIGUOUS = "ambiguous"
    EMPTY_QUERY = "empty_query"


class RejectionReason(str, Enum):
    """拒绝/未采用原因（封闭枚举，便于诊断统计）。"""

    MODEL_MISMATCH = "model_mismatch"
    CONDITIONS_NOT_SATISFIED = "conditions_not_satisfied"
    ZERO_SCORE = "zero_score"
    BELOW_TOP_K = "below_top_k"
    TIE_AMBIGUOUS = "tie_ambiguous"
    TIE_NOT_ADOPTED = "tie_not_adopted"


class AdoptionRejectionReason(str, Enum):
    """**消费端**（PromptEngine）复核拒绝原因（封闭枚举，供 CLI 与测试查询）。

    与 `RejectionReason`（检索端）分开：检索器说"推荐了谁"，消费端说"编译器是否
    实际采用以及为什么没有采用"，两者不得混为一谈。
    """

    MISSING_ELIGIBILITY_SNAPSHOT = "missing_eligibility_snapshot"
    SNAPSHOT_VERSION_UNSUPPORTED = "snapshot_version_unsupported"
    REVIEW_NOT_APPROVED = "review_not_approved"
    REVIEW_FIELDS_INVALID = "review_fields_invalid"
    CONDITIONS_NOT_SATISFIED = "conditions_not_satisfied"
    MODEL_NOT_APPLICABLE = "model_not_applicable"
    CONTENT_HASH_MISMATCH = "content_hash_mismatch"
    SNAPSHOT_MISMATCH = "snapshot_mismatch"
    CANDIDATE_NOT_AUTHORIZED = "candidate_not_authorized"
    PATH_ALREADY_RESOLVED = "path_already_resolved"
    PATH_NOT_DELEGATED = "path_not_delegated"
    PATH_PINNED = "path_pinned"
    NO_ADOPTED_RESULT = "no_adopted_result"
    QUERY_MISSING_OR_MODEL_MISMATCH = "query_missing_or_model_mismatch"


class AdoptionOutcome(str, Enum):
    """消费端裁定：检索器推荐了，编译器**实际采用**还是**拒绝并回退**。"""

    ADOPTED = "adopted"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# 查询与命中快照
# ---------------------------------------------------------------------------


class KnowledgeQuery(BaseModel):
    """一条路径的确定性检索查询：路径 + 目标模型 + 规范化查询文本与 token。"""

    model_config = _FROZEN

    path: str
    target_model: str
    text: str
    tokens: tuple[str, ...]

    @field_validator("path")
    @classmethod
    def _path_whitelist(cls, value: str) -> str:
        if value not in KNOWLEDGE_PATHS:
            raise ValueError(f"query path {value!r} is outside the frozen knowledge whitelist")
        return value

    @field_validator("target_model")
    @classmethod
    def _model_shape(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("target_model must be a non-empty string")
        return value

    @field_validator("tokens")
    @classmethod
    def _tokens_shape(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("query tokens must be unique")
        if any(not token for token in value):
            raise ValueError("query tokens must be non-empty")
        return value


class KnowledgeEligibilitySnapshot(BaseModel):
    """命中单元的**适用性快照**（v0.4 F2 兼容扩展，供消费端二次校验）。

    由 `LocalKnowledgeEngine` 从**已校验的权威 `KnowledgeUnit` 原样复制**（不从 query
    猜、不从正文重新解析），随不可变 Bundle payload 一起落库。它用于复核与追溯，
    **不是**数字签名：不能证明任意恶意检索服务可信，信任边界仍是经审核、哈希校验的
    本地权威语料。

    旧记录没有本字段（整个快照为 `None`）表示"未提供条件证据"；空 `conditions=()`
    是**显式**的"无附加适用条件"。两者语义不同，前者绝不能被当成无限适用。
    """

    model_config = _FROZEN

    snapshot_version: str = ELIGIBILITY_SNAPSHOT_VERSION
    conditions: tuple[KnowledgeCondition, ...] = ()
    target_models: tuple[str, ...]
    review_status: ReviewStatus
    reviewer: str | None = None
    reviewed_at: datetime | None = None

    @field_validator("snapshot_version")
    @classmethod
    def _version_supported(cls, value: str) -> str:
        if value not in SUPPORTED_ELIGIBILITY_SNAPSHOT_VERSIONS:
            raise ValueError(
                f"eligibility snapshot version {value!r} is not supported "
                f"(supported: {sorted(SUPPORTED_ELIGIBILITY_SNAPSHOT_VERSIONS)})"
            )
        return value

    @field_validator("conditions")
    @classmethod
    def _conditions_count(
        cls, value: tuple[KnowledgeCondition, ...]
    ) -> tuple[KnowledgeCondition, ...]:
        if len(value) > MAX_CONDITIONS_PER_UNIT:
            raise ValueError(
                f"conditions must contain at most {MAX_CONDITIONS_PER_UNIT} entries "
                f"(got {len(value)})"
            )
        return value

    @field_validator("target_models")
    @classmethod
    def _target_models_shape(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # 与 KnowledgeUnit 共用唯一实现，避免出现第二套模型适用性解释器。
        return clean_target_models(value)

    @field_validator("reviewed_at")
    @classmethod
    def _reviewed_at_shape(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return ensure_review_utc(value)

    @model_validator(mode="after")
    def _review_integrity(self) -> "KnowledgeEligibilitySnapshot":
        # 与 KnowledgeUnit 共用；允许构造 draft/rejected 快照（诊断可达），
        # 但"是否可采纳"由消费端显式要求 approved。
        check_review_integrity(self.review_status, self.reviewer, self.reviewed_at)
        return self


class KnowledgeUnitHit(BaseModel):
    """实际命中的**不可变快照**：id/version/content_hash/来源/内容/score。

    v0.4 F2 兼容扩展：可选 `eligibility_snapshot`（默认 `None`）。旧 payload 缺该字段
    仍可反序列化，此时表示"旧记录未提供条件证据"，消费端**不得**采纳该推荐。
    """

    model_config = _FROZEN

    knowledge_id: str
    version: str
    content_hash: str
    applicable_path: str
    candidate_value: str
    source: KnowledgeSource
    content: str
    score: float = Field(ge=0.0, le=1.0)
    rank: int = Field(ge=1, le=TOP_K)
    matched_tokens: tuple[str, ...]
    eligibility_snapshot: KnowledgeEligibilitySnapshot | None = None

    @field_validator("content_hash")
    @classmethod
    def _hash_shape(cls, value: str) -> str:
        if not isinstance(value, str) or not _HEX64.match(value):
            raise ValueError("content_hash must be a 64-character lowercase sha256 hex digest")
        return value


class KnowledgeRejection(BaseModel):
    """一个未采用单元的原因（诊断，不是授权）。"""

    model_config = _FROZEN

    knowledge_id: str
    version: str
    candidate_value: str
    reason_code: RejectionReason
    reason: str


class PathRetrievalResult(BaseModel):
    """一条路径的独立检索结果（最多 Top-3 快照），最终推荐单独记录。"""

    model_config = _FROZEN

    path: str
    outcome: RetrievalOutcome
    reason_code: str
    reason: str
    hits: tuple[KnowledgeUnitHit, ...] = ()
    rejections: tuple[KnowledgeRejection, ...] = ()

    @field_validator("path")
    @classmethod
    def _path_whitelist(cls, value: str) -> str:
        if value not in KNOWLEDGE_PATHS:
            raise ValueError(f"result path {value!r} is outside the frozen knowledge whitelist")
        return value

    @field_validator("hits")
    @classmethod
    def _hits_bound(cls, value: tuple[KnowledgeUnitHit, ...]) -> tuple[KnowledgeUnitHit, ...]:
        if len(value) > TOP_K:
            raise ValueError(f"at most {TOP_K} unit snapshots may be retained per path")
        if [hit.rank for hit in value] != list(range(1, len(value) + 1)):
            raise ValueError("hit ranks must be 1..n in order")
        return value

    @model_validator(mode="after")
    def _outcome_hits_consistency(self) -> "PathRetrievalResult":
        if any(hit.applicable_path != self.path for hit in self.hits):
            raise ValueError("all hits of a path result must share the result path")
        if self.outcome is RetrievalOutcome.ADOPTED and not self.hits:
            raise ValueError("an adopted outcome requires at least one retained hit")
        if self.outcome is not RetrievalOutcome.ADOPTED and self.reason_code in {"", "adopted"}:
            raise ValueError("non-adopted outcomes must carry a non-adopted reason_code")
        return self


class KnowledgeRecommendation(BaseModel):
    """最终推荐：一条路径唯一被采用的候选值及其知识来源与分数。"""

    model_config = _FROZEN

    path: str
    knowledge_id: str
    version: str
    content_hash: str
    candidate_value: str
    score: float = Field(ge=0.0, le=1.0)
    reason_code: str
    reason: str

    @field_validator("path")
    @classmethod
    def _path_whitelist(cls, value: str) -> str:
        if value not in KNOWLEDGE_PATHS:
            raise ValueError(
                f"recommendation path {value!r} is outside the frozen knowledge whitelist"
            )
        return value

    @field_validator("content_hash")
    @classmethod
    def _hash_shape(cls, value: str) -> str:
        if not isinstance(value, str) or not _HEX64.match(value):
            raise ValueError("content_hash must be a 64-character lowercase sha256 hex digest")
        return value


# ---------------------------------------------------------------------------
# 编译级 Bundle
# ---------------------------------------------------------------------------


class KnowledgeAdoptionDecision(BaseModel):
    """**消费端**（PromptEngine）对一条检索推荐的最终裁定（持久化可查询）。

    - `KnowledgeRecommendation` 只表达"检索器推荐了什么"；
    - 本模型表达"编译器是否实际采用该推荐"：`adopted` 或 `rejected` + 封闭原因码。

    它由 PromptEngine 在落库前追加到 Bundle（`KnowledgeBundle.with_adoption_decisions`
    返回**新**对象，绝不原地修改检索器返回的 Bundle），旧 Bundle 缺该字段仍可读。
    本记录是诊断/追溯，不是授权：被拒推荐照常回退固定候选表。
    """

    model_config = _FROZEN

    path: str
    knowledge_id: str
    version: str
    candidate_value: str
    outcome: AdoptionOutcome
    reason_code: AdoptionRejectionReason | None = None
    reason: str = ""

    @field_validator("path")
    @classmethod
    def _path_whitelist(cls, value: str) -> str:
        if value not in KNOWLEDGE_PATHS:
            raise ValueError(f"adoption decision path {value!r} is outside the knowledge whitelist")
        return value

    @model_validator(mode="after")
    def _outcome_reason_consistency(self) -> "KnowledgeAdoptionDecision":
        if self.outcome is AdoptionOutcome.ADOPTED:
            if self.reason_code is not None:
                raise ValueError("an adopted decision must not carry a rejection reason_code")
        else:
            if self.reason_code is None:
                raise ValueError("a rejected decision must carry a rejection reason_code")
            if not self.reason.strip():
                raise ValueError("a rejected decision must carry a non-empty reason")
        return self


class KnowledgeBundle(BaseModel):
    """一次编译的知识检索审计记录（frozen + extra=forbid）。"""

    model_config = _FROZEN

    bundle_id: str
    session_id: str
    intent_revision_id: str
    execution_revision_id: str
    confirmation_id: str
    target_model: str
    status: BundleStatus

    schema_version: str | None = None
    corpus_version: str | None = None
    tokenizer_version: str | None = None
    retrieval_version: str | None = None
    corpus_fingerprint: str | None = None

    queries: tuple[KnowledgeQuery, ...] = ()
    path_results: tuple[PathRetrievalResult, ...] = ()
    recommendations: tuple[KnowledgeRecommendation, ...] = ()
    #: 消费端裁定（v0.4 F2 兼容追加，默认空）；旧 payload 缺该字段仍可读。
    adoption_decisions: tuple[KnowledgeAdoptionDecision, ...] = ()

    reason_code: str | None = None
    reason: str | None = None
    created_at: _UtcDatetime = Field(default_factory=utc_now)

    # -- 形态 --------------------------------------------------------------

    @field_validator("bundle_id")
    @classmethod
    def _bundle_id_shape(cls, value: str) -> str:
        if not isinstance(value, str) or not _BUNDLE_ID.match(value):
            raise ValueError("bundle_id must match kbu_<32 lowercase hex>")
        return value

    @field_validator(
        "session_id",
        "intent_revision_id",
        "execution_revision_id",
        "confirmation_id",
        "target_model",
    )
    @classmethod
    def _identity_shape(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("identity fields must be non-empty strings")
        return value

    @field_validator("corpus_fingerprint")
    @classmethod
    def _fingerprint_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _HEX64.match(value):
            raise ValueError("corpus_fingerprint must be a 64-character lowercase sha256 digest")
        return value

    # -- 上限与一致性 ------------------------------------------------------

    @field_validator("queries")
    @classmethod
    def _queries_bound(cls, value: tuple[KnowledgeQuery, ...]) -> tuple[KnowledgeQuery, ...]:
        if len(value) > MAX_RETRIEVAL_PATHS:
            raise ValueError(f"at most {MAX_RETRIEVAL_PATHS} queries may be recorded")
        paths = [query.path for query in value]
        if len(set(paths)) != len(paths):
            raise ValueError("queries must not repeat a path")
        return value

    @field_validator("path_results")
    @classmethod
    def _results_bound(cls, value: tuple[PathRetrievalResult, ...]) -> tuple[PathRetrievalResult, ...]:
        if len(value) > MAX_RETRIEVAL_PATHS:
            raise ValueError(f"at most {MAX_RETRIEVAL_PATHS} path results may be recorded")
        paths = [result.path for result in value]
        if len(set(paths)) != len(paths):
            raise ValueError("path_results must not repeat a path")
        return value

    @field_validator("recommendations")
    @classmethod
    def _recommendations_bound(
        cls, value: tuple[KnowledgeRecommendation, ...]
    ) -> tuple[KnowledgeRecommendation, ...]:
        paths = [recommendation.path for recommendation in value]
        if len(set(paths)) != len(paths):
            raise ValueError("at most one recommendation per path")
        return value

    @field_validator("adoption_decisions")
    @classmethod
    def _decisions_bound(
        cls, value: tuple[KnowledgeAdoptionDecision, ...]
    ) -> tuple[KnowledgeAdoptionDecision, ...]:
        paths = [decision.path for decision in value]
        if len(set(paths)) != len(paths):
            raise ValueError("at most one adoption decision per path")
        return value

    @model_validator(mode="after")
    def _status_consistency(self) -> "KnowledgeBundle":
        corpus_known = self.status is not BundleStatus.CORPUS_ERROR
        versions = (
            self.schema_version,
            self.corpus_version,
            self.tokenizer_version,
            self.retrieval_version,
            self.corpus_fingerprint,
        )
        if corpus_known and any(version is None for version in versions):
            raise ValueError(
                "non-corpus_error bundles must record schema/corpus/tokenizer/retrieval "
                "versions and the corpus fingerprint"
            )
        if not corpus_known and any(version is not None for version in versions):
            raise ValueError("corpus_error bundles must not claim corpus versions or a fingerprint")
        if self.status is not BundleStatus.OK and self.recommendations:
            raise ValueError("only an ok bundle may carry recommendations")
        adopted = {
            result.path for result in self.path_results if result.outcome is RetrievalOutcome.ADOPTED
        }
        for recommendation in self.recommendations:
            if recommendation.path not in adopted:
                raise ValueError(
                    f"recommendation for {recommendation.path!r} lacks an adopted path result"
                )
            result = next(
                item for item in self.path_results if item.path == recommendation.path
            )
            if recommendation.knowledge_id not in {hit.knowledge_id for hit in result.hits}:
                raise ValueError(
                    "recommendation knowledge_id must be one of the retained Top-3 snapshots"
                )
        return self

    @model_validator(mode="after")
    def _adoption_decisions_consistency(self) -> "KnowledgeBundle":
        """消费端裁定只能针对本 Bundle 实际存在的推荐，且字段必须逐一对齐。"""
        by_path = {recommendation.path: recommendation for recommendation in self.recommendations}
        for decision in self.adoption_decisions:
            recommendation = by_path.get(decision.path)
            if recommendation is None:
                raise ValueError(
                    f"adoption decision for {decision.path!r} lacks a recommendation in this bundle"
                )
            if (
                decision.knowledge_id != recommendation.knowledge_id
                or decision.version != recommendation.version
                or decision.candidate_value != recommendation.candidate_value
            ):
                raise ValueError(
                    "adoption decision must mirror the recommendation it decides on"
                )
        return self

    # -- 消费端裁定（返回新对象，绝不原地修改） ----------------------------

    def with_adoption_decisions(
        self, decisions: tuple[KnowledgeAdoptionDecision, ...]
    ) -> "KnowledgeBundle":
        """返回追加消费端裁定的**新** Bundle；原检索器对象保持不变。

        经完整重校验后再构造，因此跨字段不变量（裁定必须镜像推荐）仍然生效；
        返回对象的 `bundle_id` 不变，调用方可放心落库为最终审计记录。
        """
        payload = self.model_dump()
        payload["adoption_decisions"] = [decision.model_dump() for decision in decisions]
        return KnowledgeBundle.model_validate(payload)

    def decision_for(self, path: str) -> KnowledgeAdoptionDecision | None:
        for decision in self.adoption_decisions:
            if decision.path == path:
                return decision
        return None

    # -- 派生视图（只读，不落额外状态） ------------------------------------

    @property
    def is_fallback(self) -> bool:
        """是否有任一待检索路径没有产生推荐（调用方必须走固定候选回退）。"""
        if self.status is not BundleStatus.OK:
            return True
        return len(self.recommendations) != len(self.path_results)

    def recommendation_for(self, path: str) -> KnowledgeRecommendation | None:
        for recommendation in self.recommendations:
            if recommendation.path == path:
                return recommendation
        return None

    def result_for(self, path: str) -> PathRetrievalResult | None:
        for result in self.path_results:
            if result.path == path:
                return result
        return None


__all__ = [
    "KNOWLEDGE_BUNDLE_ID_PREFIX",
    "TOP_K",
    "MAX_RETRIEVAL_PATHS",
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
]