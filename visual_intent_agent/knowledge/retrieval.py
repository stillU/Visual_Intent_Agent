"""v0.4 Step 02：确定性本地检索（keyword.v1 / lexical.v1）。

本模块实现"已确认 Intent + 目标模型 + 当前待实现路径 → 结构化检索结果"的纯本地、
离线、确定性算法。它不接 PromptEngine、不写 Repository、不接 CLI —— 这些属 Step 03/04。

检索路径（任务书 `02_retrieval_bundle.md`）：

1. `QueryBuilder` **只**取已确认 Intent、目标模型与调用方给出的当前待实现路径；
   不读未确认聊天、任意文件或秘密配置。只接受仍是 `user_delegated`、尚无确认值、
   未被 PIN 的知识路径（确认门禁在调用方 PromptEngine，本类做纵深防御）。
2. 先过滤：approved（`KnowledgeCorpus.build_units()`）→ `target_models` 精确标识
   或显式 `any` → 路径 → conditions；**再**做词法排序。
3. 规范化（`keyword.v1`，规则与版本冻结）：Unicode **NFKC** → 英文小写字母数字词
   → 中文连续字符的**二元组**（单个中文字符保留为单 token）。不下载分词模型、
   不触网、不新增依赖。
4. 评分（`lexical.v1`）：`|query token 集 ∩ unit token 集| / |query token 集|`；
   空查询得 0。按 `-score, knowledge_id, version` 稳定排序，每路径 Top-3。
   **零分不命中**；文件行序不影响选择。
5. 只有最高分单元给出**唯一 candidate** 才可采用；最高分并列且 candidate 不同 →
   `ambiguous` 回退，绝不强行采用。同分同 candidate 可采用（稳定取排序首位）并
   保留全部 Top-3 诊断。

conditions 语义严格按 ADR-005 第 5 节（冻结，不得放宽）：

- 多条件 **AND**；空条件恒满足；条件路径在已确认 Intent 中缺失 → **不满足**；
- 只读已确认 Intent 字段（`INTENT_PATHS`），不读正文、不读未确认字段；
- `subject.count` 把条件是**严格十进制字符串**转 int 比较（拒绝前导零/正号/空串），
  其余路径按字符串逐字比较；无子串/大小写折叠/正则/强制转换；算子只有
  `equals` / `in`。

回退与错误边界：`LocalKnowledgeEngine.retrieve` 只捕获明确的 `KnowledgeError`
（语料缺失/哈希/版本/合同校验失败）并产出可观测的**诊断 Bundle**；绝不
`except Exception` 吞掉编程错误。禁用 RAG 由调用方**不调用**本模块实现（本步不接
开关）。`knowledge` 包不在模块顶层 import `prompt_engine`（防 Step 03 成环）。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, field_validator

from visual_intent_agent.domain import INTENT_PATHS, Resolution, VisualIntent, new_id

from .bundle import (
    KNOWLEDGE_BUNDLE_ID_PREFIX,
    MAX_RETRIEVAL_PATHS,
    TOP_K,
    BundleStatus,
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
from .candidates import KNOWLEDGE_PATHS
from .loader import load_corpus
from .models import (
    GENERIC_TARGET_MODEL,
    KNOWLEDGE_RETRIEVAL_VERSION,
    KNOWLEDGE_TOKENIZER_VERSION,
    ConditionOperator,
    KnowledgeCondition,
    KnowledgeCorpus,
    KnowledgeError,
    KnowledgeUnit,
)

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: 本模块实现必须与清单冻结版本一致；版本升级须显式改代码，绝不静默沿用旧语义。
if KNOWLEDGE_TOKENIZER_VERSION != "keyword.v1" or KNOWLEDGE_RETRIEVAL_VERSION != "lexical.v1":
    raise ValueError(
        "retrieval.py implements the frozen keyword.v1 / lexical.v1 semantics; "
        "a version bump requires an explicit retrieval change"
    )

#: 需要按整数比较的条件路径（ADR-005 第 5 节）。
SUBJECT_COUNT_PATH: str = "subject.count"

#: 严格十进制整数（拒绝前导零/正号/空串/负号）。
_STRICT_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)$")

#: CJK 连续字符范围（中文二元组的基础；不含标点与拉丁字母）。
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
)


def _is_cjk(character: str) -> bool:
    code = ord(character)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def normalize_text(text: str) -> str:
    """`keyword.v1` 规范化第一步：Unicode **NFKC**（全角/兼容字符折叠为规范形）。"""
    if not isinstance(text, str):
        raise TypeError("normalize_text expects a string")
    return unicodedata.normalize("NFKC", text)


def tokenize(text: str) -> tuple[str, ...]:
    """`keyword.v1` 分词：英文小写字母数字词 + 中文连续字符二元组（单字保留）。

    去重保序，返回元组（deterministic）。标点与非 ASCII 非中文字符一律作为分隔符。
    """
    normalized = normalize_text(text).lower()
    tokens: list[str] = []
    cjk_run: list[str] = []
    latin_run: list[str] = []

    def flush_cjk() -> None:
        if not cjk_run:
            return
        if len(cjk_run) == 1:
            tokens.append(cjk_run[0])
        else:
            tokens.extend(
                cjk_run[index] + cjk_run[index + 1] for index in range(len(cjk_run) - 1)
            )
        cjk_run.clear()

    def flush_latin() -> None:
        if latin_run:
            tokens.append("".join(latin_run))
            latin_run.clear()

    for character in normalized:
        if _is_cjk(character):
            flush_latin()
            cjk_run.append(character)
        elif ("a" <= character <= "z") or ("0" <= character <= "9"):
            flush_cjk()
            latin_run.append(character)
        else:
            flush_cjk()
            flush_latin()
    flush_cjk()
    flush_latin()

    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return tuple(unique)


def unit_retrieval_tokens(unit: KnowledgeUnit) -> tuple[str, ...]:
    """单元检索 token 集：正文 + keywords + aliases，使用同一 `keyword.v1` 规范化。"""
    tokens: list[str] = []
    seen: set[str] = set()
    for text in (unit.content, *unit.keywords, *unit.aliases):
        for token in tokenize(text):
            if token not in seen:
                seen.add(token)
                tokens.append(token)
    return tuple(tokens)


def score_tokens(query_tokens: Iterable[str], unit_tokens: Iterable[str]) -> float:
    """`|query ∩ unit| / |query|`；空查询得 0（`lexical.v1` 冻结公式）。"""
    query = set(query_tokens)
    if not query:
        return 0.0
    return len(query & set(unit_tokens)) / len(query)


def read_confirmed_intent_value(intent: VisualIntent, path: str) -> object | None:
    """只读读取已确认 Intent 的白名单路径值（不改状态、不猜测）。

    与 `prompt_engine.read_intent_path` 语义相同，但本包不在模块顶层 import
    `prompt_engine`（防 Step 03 循环依赖），因此这里做等价的最小读取。
    """
    if path not in INTENT_PATHS:
        raise ValueError(f"path {path!r} is not a whitelisted intent path")
    facet_name, _, field_name = path.partition(".")
    facet = getattr(intent, facet_name, None)
    if facet is None:
        return None
    return getattr(facet, field_name, None)


def _strict_decimal(raw: str) -> int | None:
    if not isinstance(raw, str) or not _STRICT_DECIMAL.match(raw):
        return None
    return int(raw)


def _condition_satisfied(condition: KnowledgeCondition, intent: VisualIntent) -> bool:
    actual = read_confirmed_intent_value(intent, condition.path)
    if actual is None:
        # 缺字段（无值且无解析记录）→ 不满足；missing ≠ user_delegated。
        return False
    if condition.path == SUBJECT_COUNT_PATH:
        if isinstance(actual, bool) or not isinstance(actual, int):
            return False
        parsed: list[int] = []
        for raw in condition.values:
            number = _strict_decimal(raw)
            if number is None:
                return False
            parsed.append(number)
        return actual in parsed
    if not isinstance(actual, str):
        return False
    if condition.operator is ConditionOperator.EQUALS:
        return actual == condition.values[0]
    return actual in condition.values


def evaluate_conditions(
    conditions: Iterable[KnowledgeCondition], intent: VisualIntent
) -> bool:
    """ADR-005 条件语义：多条件 AND、空条件恒满足、缺字段不满足、类型精确比较。"""
    return all(_condition_satisfied(condition, intent) for condition in conditions)


def target_model_applies(unit_models: Iterable[str], target_model: str) -> bool:
    """单元是否适用于目标模型：精确标识或显式通用标识 `any`（唯一实现）。

    检索过滤与 PromptEngine 消费端二次校验**共用本函数**，避免出现第二套模型
    适用性解释器；不做通配/前缀/大小写猜测。
    """
    models = tuple(unit_models)
    return GENERIC_TARGET_MODEL in models or target_model in models


def build_query_text(intent: VisualIntent) -> str:
    """查询文本 = 全部已确认 Intent 字段值（按路径字典序，确定性）。

    不含未确认聊天/文件/配置；不含路径名与目标模型（它们用于过滤与身份绑定），
    因此当已确认上下文为空时查询为空 → 得分 0 → 明确无命中，而不是猜测。
    """
    parts: list[str] = []
    for path in sorted(INTENT_PATHS):
        value = read_confirmed_intent_value(intent, path)
        if value is not None:
            parts.append(str(value))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 查询构建
# ---------------------------------------------------------------------------


class QueryBuilder:
    """把已确认 Intent / 目标模型 / 待实现路径转成每路径的确定性查询。"""

    def build(
        self,
        *,
        intent: VisualIntent,
        target_model: str,
        pending_paths: Iterable[str],
    ) -> tuple[KnowledgeQuery, ...]:
        if not isinstance(intent, VisualIntent):
            raise TypeError("QueryBuilder.build expects a VisualIntent")
        if not isinstance(target_model, str) or not target_model.strip():
            raise ValueError("target_model must be a non-empty string")
        if isinstance(pending_paths, str):
            raise TypeError("pending_paths must be an iterable of paths, not a string")

        raw_paths = tuple(pending_paths)
        for path in raw_paths:
            if not isinstance(path, str) or path not in INTENT_PATHS:
                raise ValueError(f"pending path {path!r} is not a whitelisted intent path")

        selected = sorted({path for path in raw_paths if path in KNOWLEDGE_PATHS})
        for path in selected:
            record = intent.resolutions.get(path)
            if record is None or record.resolution is not Resolution.USER_DELEGATED:
                raise ValueError(
                    f"pending path {path!r} is not marked user_delegated in the confirmed intent"
                )
            if read_confirmed_intent_value(intent, path) is not None:
                raise ValueError(
                    f"pending path {path!r} already has a confirmed value; RAG may not override it"
                )
            if path in intent.pinned_paths:
                raise ValueError(f"pending path {path!r} is pinned; RAG never overrides a PIN")

        text = build_query_text(intent)
        tokens = tokenize(text)
        return tuple(
            KnowledgeQuery(path=path, target_model=target_model, text=text, tokens=tokens)
            for path in selected[:MAX_RETRIEVAL_PATHS]
        )


# ---------------------------------------------------------------------------
# 请求与引擎接口
# ---------------------------------------------------------------------------


class RetrievalRequest(BaseModel):
    """一次编译级检索请求：确认身份 + 已确认 Intent + 目标模型 + 待实现路径。"""

    model_config = _FROZEN

    session_id: str
    intent_revision_id: str
    execution_revision_id: str
    confirmation_id: str
    intent: VisualIntent
    target_model: str
    pending_paths: tuple[str, ...] = ()

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

    @field_validator("pending_paths")
    @classmethod
    def _pending_paths_shape(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unique: list[str] = []
        for path in value:
            if not isinstance(path, str) or path not in INTENT_PATHS:
                raise ValueError(f"pending path {path!r} is not a whitelisted intent path")
            if path not in unique:
                unique.append(path)
        return tuple(unique)


@runtime_checkable
class KnowledgeEngine(Protocol):
    """检索接口合同：真实本地实现与测试 Fake 必须满足同一签名。"""

    def retrieve(self, request: RetrievalRequest) -> KnowledgeBundle:
        """返回一个编译级 `KnowledgeBundle`（诊断回退也走同一合同）。"""
        ...


def _rejection(unit: KnowledgeUnit, reason_code: RejectionReason, reason: str) -> KnowledgeRejection:
    return KnowledgeRejection(
        knowledge_id=unit.knowledge_id,
        version=unit.version,
        candidate_value=unit.candidate_value,
        reason_code=reason_code,
        reason=reason,
    )


def _sorted_rejections(rejections: list[KnowledgeRejection]) -> tuple[KnowledgeRejection, ...]:
    return tuple(
        sorted(rejections, key=lambda item: (item.reason_code.value, item.knowledge_id, item.version))
    )


class LocalKnowledgeEngine:
    """真实本地实现：JSONL 权威语料 + `keyword.v1` 词法索引（内存、只读、离线）。

    语料在首次 `retrieve` 时加载并缓存（`启动时构建一次并固定语料哈希；不热更新`）；
    要加载新版本必须显式新建引擎（或传入新 `KnowledgeCorpus`）。只有明确的
    `KnowledgeError` 被捕获并转成诊断 Bundle；其他异常（编程错误）照常抛出。
    """

    def __init__(
        self,
        corpus: KnowledgeCorpus | None = None,
        *,
        corpus_dir: str | Path | None = None,
        query_builder: QueryBuilder | None = None,
    ) -> None:
        if (corpus is None) == (corpus_dir is None):
            raise ValueError("provide exactly one of corpus or corpus_dir")
        if corpus is not None and not isinstance(corpus, KnowledgeCorpus):
            raise TypeError("corpus must be a KnowledgeCorpus")
        self._corpus = corpus
        self._corpus_dir = Path(corpus_dir) if corpus_dir is not None else None
        self._loaded: KnowledgeCorpus | None = None
        self._builder = query_builder if query_builder is not None else QueryBuilder()

    def retrieve(self, request: RetrievalRequest) -> KnowledgeBundle:
        if not isinstance(request, RetrievalRequest):
            raise TypeError("retrieve expects a RetrievalRequest")
        queries = self._builder.build(
            intent=request.intent,
            target_model=request.target_model,
            pending_paths=request.pending_paths,
        )
        try:
            corpus = self._get_corpus()
        except KnowledgeError as exc:
            return self._build_bundle(
                request,
                status=BundleStatus.CORPUS_ERROR,
                queries=queries,
                results=(),
                recommendations=(),
                reason_code=exc.code,
                reason=str(exc),
                corpus=None,
            )
        if not queries:
            return self._build_bundle(
                request,
                status=BundleStatus.NO_PENDING_PATHS,
                queries=(),
                results=(),
                recommendations=(),
                reason_code="no_pending_paths",
                reason="no pending knowledge path was supplied; nothing was retrieved",
                corpus=corpus,
            )
        units = corpus.build_units()
        if not units:
            return self._build_bundle(
                request,
                status=BundleStatus.NO_APPROVED_UNITS,
                queries=queries,
                results=(),
                recommendations=(),
                reason_code="no_approved_units",
                reason="the corpus has no approved unit; fall back to the fixed candidate table",
                corpus=corpus,
            )
        results = tuple(self._retrieve_path(query, units, request.intent) for query in queries)
        recommendations = tuple(
            self._recommendation(result)
            for result in results
            if result.outcome is RetrievalOutcome.ADOPTED
        )
        return self._build_bundle(
            request,
            status=BundleStatus.OK,
            queries=queries,
            results=results,
            recommendations=recommendations,
            reason_code=None,
            reason=None,
            corpus=corpus,
        )

    # -- 内部 --------------------------------------------------------------

    def _get_corpus(self) -> KnowledgeCorpus:
        if self._corpus is not None:
            return self._corpus
        if self._loaded is None:
            self._loaded = load_corpus(self._corpus_dir)
        return self._loaded

    def _retrieve_path(
        self, query: KnowledgeQuery, units: tuple[KnowledgeUnit, ...], intent: VisualIntent
    ) -> PathRetrievalResult:
        if not query.tokens:
            return PathRetrievalResult(
                path=query.path,
                outcome=RetrievalOutcome.EMPTY_QUERY,
                reason_code="empty_query",
                reason="the confirmed intent produced no query tokens; score 0 cannot hit",
            )

        query_tokens = set(query.tokens)
        scored: list[tuple[float, KnowledgeUnit, tuple[str, ...]]] = []
        rejections: list[KnowledgeRejection] = []

        for unit in units:
            if unit.applicable_path != query.path:
                continue
            if not target_model_applies(unit.target_models, query.target_model):
                rejections.append(
                    _rejection(
                        unit,
                        RejectionReason.MODEL_MISMATCH,
                        f"target_models {unit.target_models!r} are neither the exact model "
                        f"{query.target_model!r} nor the generic marker {GENERIC_TARGET_MODEL!r}",
                    )
                )
                continue
            if not evaluate_conditions(unit.conditions, intent):
                rejections.append(
                    _rejection(
                        unit,
                        RejectionReason.CONDITIONS_NOT_SATISFIED,
                        "conditions are not satisfied by the confirmed intent "
                        "(missing fields are not satisfied)",
                    )
                )
                continue
            unit_tokens = set(unit_retrieval_tokens(unit))
            score = score_tokens(query_tokens, unit_tokens)
            if score <= 0.0:
                rejections.append(
                    _rejection(
                        unit,
                        RejectionReason.ZERO_SCORE,
                        "no shared retrieval token with the query; zero score never hits",
                    )
                )
                continue
            scored.append((score, unit, tuple(sorted(query_tokens & unit_tokens))))

        scored.sort(key=lambda item: (-item[0], item[1].knowledge_id, item[1].version))
        ranked = scored[:TOP_K]
        for _, unit, _ in scored[TOP_K:]:
            rejections.append(
                _rejection(unit, RejectionReason.BELOW_TOP_K, f"ranked below Top-{TOP_K}")
            )

        hits = tuple(
            KnowledgeUnitHit(
                knowledge_id=unit.knowledge_id,
                version=unit.version,
                content_hash=unit.content_hash,
                applicable_path=unit.applicable_path,
                candidate_value=unit.candidate_value,
                source=unit.source,
                content=unit.content,
                score=score,
                rank=rank,
                matched_tokens=matched,
                # 适用性快照：从**已校验的权威 KnowledgeUnit 原样复制**，
                # 不从 query 猜、不从正文重新解析。消费端据此做第二道复核。
                eligibility_snapshot=KnowledgeEligibilitySnapshot(
                    conditions=unit.conditions,
                    target_models=unit.target_models,
                    review_status=unit.review_status,
                    reviewer=unit.reviewer,
                    reviewed_at=unit.reviewed_at,
                ),
            )
            for rank, (score, unit, matched) in enumerate(ranked, start=1)
        )

        if not ranked:
            return PathRetrievalResult(
                path=query.path,
                outcome=RetrievalOutcome.NO_HIT,
                reason_code="no_hit",
                reason="no approved unit passed model/path/conditions filters with a positive score",
                rejections=_sorted_rejections(rejections),
            )

        # 并列判定必须覆盖**全部**正分单元，而不只是保留的 Top-3：否则"前 3 个同分同
        # 候选 + 第 4 个同分不同候选"会被错误地采用，违反"最高分并列且候选不同 →
        # ambiguous"。
        top_score = scored[0][0]
        leaders = [item for item in scored if item[0] == top_score]
        if len({item[1].candidate_value for item in leaders}) > 1:
            for _, unit, _ in leaders:
                rejections.append(
                    _rejection(
                        unit,
                        RejectionReason.TIE_AMBIGUOUS,
                        "highest score is tied across different candidate values; "
                        "ambiguous is not adopted",
                    )
                )
            return PathRetrievalResult(
                path=query.path,
                outcome=RetrievalOutcome.AMBIGUOUS,
                reason_code="ambiguous",
                reason="top score is tied across different candidate values",
                hits=hits,
                rejections=_sorted_rejections(rejections),
            )

        for _, unit, _ in leaders[1:]:
            rejections.append(
                _rejection(
                    unit,
                    RejectionReason.TIE_NOT_ADOPTED,
                    "same top score and same candidate; the stable sort leader was adopted",
                )
            )
        return PathRetrievalResult(
            path=query.path,
            outcome=RetrievalOutcome.ADOPTED,
            reason_code="unique_top_candidate",
            reason=f"unique top candidate {ranked[0][1].candidate_value!r}",
            hits=hits,
            rejections=_sorted_rejections(rejections),
        )

    @staticmethod
    def _recommendation(result: PathRetrievalResult) -> KnowledgeRecommendation:
        leader = result.hits[0]
        return KnowledgeRecommendation(
            path=result.path,
            knowledge_id=leader.knowledge_id,
            version=leader.version,
            content_hash=leader.content_hash,
            candidate_value=leader.candidate_value,
            score=leader.score,
            reason_code="unique_top_candidate",
            reason=f"unique top candidate {leader.candidate_value!r} from {leader.knowledge_id!r}",
        )

    @staticmethod
    def _build_bundle(
        request: RetrievalRequest,
        *,
        status: BundleStatus,
        queries: tuple[KnowledgeQuery, ...],
        results: tuple[PathRetrievalResult, ...],
        recommendations: tuple[KnowledgeRecommendation, ...],
        reason_code: str | None,
        reason: str | None,
        corpus: KnowledgeCorpus | None,
    ) -> KnowledgeBundle:
        fingerprint = None if corpus is None else compute_corpus_fingerprint(corpus.manifest)
        manifest = None if corpus is None else corpus.manifest
        return KnowledgeBundle(
            bundle_id=new_id(KNOWLEDGE_BUNDLE_ID_PREFIX),
            session_id=request.session_id,
            intent_revision_id=request.intent_revision_id,
            execution_revision_id=request.execution_revision_id,
            confirmation_id=request.confirmation_id,
            target_model=request.target_model,
            status=status,
            schema_version=None if manifest is None else manifest.schema_version,
            corpus_version=None if manifest is None else manifest.corpus_version,
            tokenizer_version=None if manifest is None else manifest.tokenizer_version,
            retrieval_version=None if manifest is None else manifest.retrieval_version,
            corpus_fingerprint=fingerprint,
            queries=queries,
            path_results=results,
            recommendations=recommendations,
            reason_code=reason_code,
            reason=reason,
        )


__all__ = [
    "SUBJECT_COUNT_PATH",
    "normalize_text",
    "tokenize",
    "unit_retrieval_tokens",
    "score_tokens",
    "read_confirmed_intent_value",
    "evaluate_conditions",
    "target_model_applies",
    "build_query_text",
    "QueryBuilder",
    "RetrievalRequest",
    "KnowledgeEngine",
    "LocalKnowledgeEngine",
]