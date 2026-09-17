"""v0.4 知识合同（严格冻结，`extra="forbid"`）。

本模块定义知识单元的**数据合同**与清单/语料容器。它不实现检索、不实现
KnowledgeBundle、不接 PromptEngine、不写 Repository、不接 CLI —— 这些属
Step 02～04。

合同要点（任务书 `01_knowledge_contract_corpus.md`）：

- `KnowledgeUnit` 是一条可独立人工审核的规则；`content_hash` 必须等于
  `sha256(content)`（内容被篡改或哈希过期即拒绝）。
- `applicable_path` 只能是本版三条白名单路径之一；`candidate_value` 必须属于
  `prompt_engine.engine.DELEGATED_CANDIDATES` 对应路径的候选集合（复用，不复制）。
- `conditions` 只允许**精确值 / 显式枚举集合**两种封闭算子；模型里没有表达式、
  正则或 LLM 判定字段，恶意正文也不参与判定。
- `source` 必须完整：来源类型、标题、入口（URL 或仓库相对路径）、定位、
  revision 或日期、许可或内部原创声明；`target_models` 不接受通配模式
  （除显式通用标识 `any`）。
- 审核字段严格自洽：`approved` 必须带真实 `reviewer`/`reviewed_at`；
  `draft`/`rejected` **禁止**携带这两个字段（agent 不得虚构人工审核）。

不可信字符串边界：`content`、`keywords`、`aliases`、`conditions` 的值/路径、
`target_models` 与来源元数据都视为不可信输入；每个单项有长度上界，单行字符串
拒绝 C0 控制字符（`content` 允许制表符与换行），错误回显有统一长度截断。

**conditions 消费语义（冻结，Step 02 实现，不得放宽）**：

1. 同一单元的多个 `conditions` 是 **AND**：全部满足才视为命中；
2. `conditions` 为空元组时**恒满足**（无附加适用条件）；
3. 条件引用的路径在当前已确认 Intent 中**缺失**（无值且无解析记录）→ 视为
   **不满足**，绝不宽松通过；
4. 条件只读取已确认的 Intent 字段，不读取 `content` 正文、不接受未确认字段；
5. 比较是**类型精确**的：`subject.count`（int）把十进制字符串按整数比较（拒绝
   前导零/正号/空串），其余路径按字符串逐字比较；不做 bool/None 强制转换、
   不做子串匹配、不做大小写折叠。字符串条件值是字面量，不是正则。

内容（`content`）是不可信数据：仅用于说明与审计，绝不执行、绝不作为系统指令、
绝不拼入 Interpreter/Feedback 消息。消费端只读验证过的 path/value/conditions。
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from visual_intent_agent.domain.paths import INTENT_PATHS

from .candidates import KNOWLEDGE_PATHS, candidate_values_for

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# ---------------------------------------------------------------------------
# 版本与限制常量
# ---------------------------------------------------------------------------

#: 知识语料 Schema 版本（本版唯一支持值）。
KNOWLEDGE_SCHEMA_VERSION: Literal["knowledge.v1"] = "knowledge.v1"

#: 加载器接受的知识 Schema 版本集合（升级须显式扩集合并评审）。
SUPPORTED_KNOWLEDGE_SCHEMA_VERSIONS: frozenset[str] = frozenset({KNOWLEDGE_SCHEMA_VERSION})

#: 清单里记录的**冻结**分词/检索版本；清单值必须精确等于其一。
KNOWLEDGE_TOKENIZER_VERSION: str = "keyword.v1"
KNOWLEDGE_RETRIEVAL_VERSION: str = "lexical.v1"

#: 单条知识单元正文的硬上界（任务书建议 ≤800 中文字符，本版按拒绝处理）。
MAX_CONTENT_CHARS: int = 800

#: 不可信字符串的单项/单项数上界（防御无界回显与内存膨胀）。
MAX_TERM_CHARS: int = 64
MAX_TERMS: int = 64
MAX_CONDITION_VALUES: int = 16
MAX_CONDITIONS_PER_UNIT: int = 16
MAX_TARGET_MODEL_CHARS: int = 128
MAX_METADATA_CHARS: int = 256
MAX_URL_CHARS: int = 2048
MAX_REPOSITORY_PATH_CHARS: int = 256

#: 错误消息回显上界（不回显无限长度的不可信输入）。
MAX_ERROR_MESSAGE_CHARS: int = 512

#: 通用模型标识：知识不猜测模型兼容性，只接受精确别名或这个显式通用值。
GENERIC_TARGET_MODEL: str = "any"

#: 语料清单文件名。
MANIFEST_FILENAME: str = "manifest.json"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def compute_content_hash(content: str) -> str:
    """`sha256(content)` 的规范算法（UTF-8，小写十六进制）。

    注意：`content_hash` **只绑定 `content` 字符串**，不覆盖 path/candidate/
    conditions/source 等授权字段；这些字段的完整性由清单记录的**文件 sha256**
    与 `corpus_version`/`schema_version` 一起绑定（见 ADR-005）。
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _non_blank(value: str, *, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty string")
    return value


def _reject_control_characters(value: str, *, what: str, allow_line_breaks: bool = False) -> str:
    allowed = {"\t", "\n"} if allow_line_breaks else frozenset()
    for char in value:
        code = ord(char)
        if (code < 0x20 and char not in allowed) or code == 0x7F:
            raise ValueError(f"{what} must not contain control characters")
    return value


def _bounded(value: str, *, what: str, max_chars: int) -> str:
    if len(value) > max_chars:
        raise ValueError(f"{what} must be at most {max_chars} characters (got {len(value)})")
    return value


def _single_line_string(value: str, *, what: str, max_chars: int) -> str:
    _non_blank(value, what=what)
    _reject_control_characters(value, what=what)
    return _bounded(value, what=what, max_chars=max_chars)


def ensure_review_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("reviewed_at must be timezone-aware UTC; naive datetimes are rejected")
    return value.astimezone(timezone.utc)


def clean_target_models(values: tuple[str, ...]) -> tuple[str, ...]:
    """`target_models` 的唯一校验实现（KnowledgeUnit 与适用性快照共用）。

    非空、去重、长度有界；只接受**精确模型标识**或显式通用标识
    `GENERIC_TARGET_MODEL`，拒绝通配/首尾空白（不猜测模型兼容性）。
    """
    cleaned = _clean_terms(values, what="target_models", require_non_empty=True)
    for model in cleaned:
        _bounded(model, what="target_models", max_chars=MAX_TARGET_MODEL_CHARS)
        if model == GENERIC_TARGET_MODEL:
            continue
        if any(ch in model for ch in "*?[]") or model.strip() != model:
            raise ValueError(
                "target_models must be exact model identifiers or the explicit "
                f"generic marker {GENERIC_TARGET_MODEL!r}; wildcard guessing is rejected"
            )
    return cleaned


def check_review_integrity(
    review_status: ReviewStatus,
    reviewer: str | None,
    reviewed_at: datetime | None,
) -> None:
    """审核字段自洽的唯一判定（KnowledgeUnit 与适用性快照共用）。

    `approved` 必须同时带真实 `reviewer` 与 `reviewed_at`；`draft`/`rejected`
    禁止携带这两个字段。程序拒绝"无审核的 approved"与"draft 预填审核字段"，
    但不代替人类审核。
    """
    if review_status is ReviewStatus.APPROVED:
        if reviewer is None or reviewed_at is None:
            raise ValueError(
                "approved units require both reviewer and reviewed_at; approval without a "
                "real human review is rejected"
            )
    else:
        if reviewer is not None or reviewed_at is not None:
            raise ValueError(
                "draft/rejected units must not carry reviewer or reviewed_at; "
                "forged review fields are rejected"
            )


def _clean_terms(
    values: tuple[str, ...],
    *,
    what: str,
    require_non_empty: bool,
    max_items: int = MAX_TERMS,
) -> tuple[str, ...]:
    if len(values) > max_items:
        raise ValueError(f"{what} must contain at most {max_items} terms (got {len(values)})")
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{what} must not contain blank terms")
        term = raw.strip()
        _reject_control_characters(term, what=what)
        _bounded(term, what=what, max_chars=MAX_TERM_CHARS)
        key = term.casefold()
        if key in seen:
            raise ValueError(f"{what} contains a duplicate term {term!r}")
        seen.add(key)
        cleaned.append(term)
    if require_non_empty and not cleaned:
        raise ValueError(f"{what} must contain at least one term")
    return tuple(cleaned)


# ---------------------------------------------------------------------------
# 封闭枚举
# ---------------------------------------------------------------------------


class ReviewStatus(str, Enum):
    """审核状态；只有 `approved` 才允许进入构建消费。"""

    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


class SourceType(str, Enum):
    """来源类型（封闭枚举，无自由文本类型）。"""

    OFFICIAL_DOCUMENTATION = "official_documentation"
    VENDOR_REPOSITORY = "vendor_repository"
    PROJECT_ORIGINAL = "project_original"


class ConditionOperator(str, Enum):
    """条件算子（封闭枚举）：精确值或显式枚举集合；禁止表达式/正则/LLM 判定。"""

    EQUALS = "equals"
    IN = "in"


# ---------------------------------------------------------------------------
# 条件与来源
# ---------------------------------------------------------------------------


class KnowledgeCondition(BaseModel):
    """基于**已确认字段**的条件：`path` 精确等于某值，或属于显式枚举集合。

    消费语义（冻结，Step 02 实现）：多条件 AND；空条件恒满足；路径在已确认
    Intent 中缺失 → 不满足；只读已确认 Intent 字段；类型精确比较（`subject.count`
    按十进制整数，其余按字符串逐字比较，无子串/大小写/正则/强制转换）。
    """

    model_config = _FROZEN

    path: str
    operator: ConditionOperator
    values: tuple[str, ...]

    @field_validator("path")
    @classmethod
    def _path_whitelist(cls, value: str) -> str:
        _single_line_string(value, what="condition path", max_chars=MAX_METADATA_CHARS)
        if value not in INTENT_PATHS:
            raise ValueError(f"condition path {value!r} is not a whitelisted intent path")
        return value

    @field_validator("values")
    @classmethod
    def _values_shape(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_terms(
            value,
            what="condition values",
            require_non_empty=True,
            max_items=MAX_CONDITION_VALUES,
        )

    @model_validator(mode="after")
    def _operator_arity(self) -> "KnowledgeCondition":
        if self.operator is ConditionOperator.EQUALS and len(self.values) != 1:
            raise ValueError("operator 'equals' requires exactly one value")
        return self


class KnowledgeSource(BaseModel):
    """来源完整性合同：类型 / 标题 / 入口 / 定位 / revision 或日期 / 许可或原创声明。"""

    model_config = _FROZEN

    source_type: SourceType
    title: str
    url: str | None = None
    repository_path: str | None = None
    locator: str
    source_revision: str | None = None
    source_date: date | None = None
    license: str | None = None
    original_declaration: str | None = None

    @field_validator("title", "locator")
    @classmethod
    def _text_shape(cls, value: str) -> str:
        return _single_line_string(value, what="source title/locator", max_chars=MAX_METADATA_CHARS)

    @field_validator("url")
    @classmethod
    def _url_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _single_line_string(value, what="source url", max_chars=MAX_URL_CHARS)
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("source url must be an absolute http(s) URL")
        return value

    @field_validator("repository_path")
    @classmethod
    def _repo_path_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _single_line_string(
            value, what="source repository_path", max_chars=MAX_REPOSITORY_PATH_CHARS
        )
        if value.startswith("/") or "\\" in value or ".." in value.split("/"):
            raise ValueError("source repository_path must be a safe repository-relative path")
        return value

    @field_validator("source_revision", "license")
    @classmethod
    def _optional_metadata_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _single_line_string(value, what="source metadata", max_chars=MAX_METADATA_CHARS)

    @field_validator("original_declaration")
    @classmethod
    def _declaration_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _non_blank(value, what="source original_declaration")
        _reject_control_characters(
            value, what="source original_declaration", allow_line_breaks=True
        )
        return _bounded(value, what="source original_declaration", max_chars=MAX_METADATA_CHARS)

    @model_validator(mode="after")
    def _completeness(self) -> "KnowledgeSource":
        if self.url is None and self.repository_path is None:
            raise ValueError("source must provide a url or a repository_path as its entry point")
        if self.license is None and self.original_declaration is None:
            raise ValueError("source must declare a license or an original-work declaration")
        if self.source_revision is None and self.source_date is None:
            raise ValueError(
                "source must record at least one of source_revision / source_date; "
                "undated sources are not auditable"
            )
        if self.source_type is SourceType.PROJECT_ORIGINAL:
            if self.original_declaration is None:
                raise ValueError(
                    "project_original sources must carry an explicit original_declaration"
                )
        return self


# ---------------------------------------------------------------------------
# 知识单元
# ---------------------------------------------------------------------------


class KnowledgeUnit(BaseModel):
    """一条可独立审核的规则（严格冻结；见模块 docstring）。"""

    model_config = _FROZEN

    knowledge_id: str
    version: str
    content_hash: str
    content: str
    applicable_path: str
    candidate_value: str
    keywords: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    conditions: tuple[KnowledgeCondition, ...] = ()
    target_models: tuple[str, ...]
    source: KnowledgeSource
    review_status: ReviewStatus = ReviewStatus.DRAFT
    reviewer: str | None = None
    reviewed_at: datetime | None = None

    # -- 单字段形态 --------------------------------------------------------

    @field_validator("knowledge_id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        _non_blank(value, what="knowledge_id")
        _reject_control_characters(value, what="knowledge_id")
        if not _ID_PATTERN.match(value):
            raise ValueError(
                "knowledge_id must be lowercase [a-z0-9._-], start alphanumeric, length 3..128"
            )
        return value

    @field_validator("version")
    @classmethod
    def _version_shape(cls, value: str) -> str:
        _non_blank(value, what="version")
        if not _VERSION_PATTERN.match(value):
            raise ValueError("version must match [A-Za-z0-9][A-Za-z0-9._-]{0,31}")
        return value

    @field_validator("content_hash")
    @classmethod
    def _hash_shape(cls, value: str) -> str:
        if not isinstance(value, str) or not _HEX64.match(value):
            raise ValueError("content_hash must be a 64-character lowercase sha256 hex digest")
        return value

    @field_validator("content")
    @classmethod
    def _content_shape(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("content must be a non-empty string")
        _reject_control_characters(value, what="content", allow_line_breaks=True)
        if len(value) > MAX_CONTENT_CHARS:
            raise ValueError(
                f"content must be at most {MAX_CONTENT_CHARS} characters "
                f"(got {len(value)}); split oversized documents into atomic reviewable units"
            )
        return value

    @field_validator("applicable_path")
    @classmethod
    def _path_shape(cls, value: str) -> str:
        _single_line_string(value, what="applicable_path", max_chars=MAX_METADATA_CHARS)
        if value not in KNOWLEDGE_PATHS:
            raise ValueError(
                f"applicable_path {value!r} is outside the frozen v0.4 knowledge whitelist "
                f"{sorted(KNOWLEDGE_PATHS)}"
            )
        return value

    @field_validator("candidate_value")
    @classmethod
    def _candidate_shape(cls, value: str) -> str:
        return _single_line_string(value, what="candidate_value", max_chars=MAX_TERM_CHARS)

    @field_validator("keywords")
    @classmethod
    def _keywords_shape(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_terms(value, what="keywords", require_non_empty=True)

    @field_validator("aliases")
    @classmethod
    def _aliases_shape(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_terms(value, what="aliases", require_non_empty=False)

    @field_validator("conditions")
    @classmethod
    def _conditions_count(cls, value: tuple[KnowledgeCondition, ...]) -> tuple[KnowledgeCondition, ...]:
        if len(value) > MAX_CONDITIONS_PER_UNIT:
            raise ValueError(
                f"conditions must contain at most {MAX_CONDITIONS_PER_UNIT} entries "
                f"(got {len(value)})"
            )
        return value

    @field_validator("target_models")
    @classmethod
    def _target_models_shape(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return clean_target_models(value)

    @field_validator("reviewer")
    @classmethod
    def _reviewer_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _single_line_string(value, what="reviewer", max_chars=MAX_METADATA_CHARS)

    @field_validator("reviewed_at")
    @classmethod
    def _reviewed_at_shape(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return ensure_review_utc(value)

    # -- 跨字段不变量 ------------------------------------------------------

    @model_validator(mode="after")
    def _content_hash_binding(self) -> "KnowledgeUnit":
        expected = compute_content_hash(self.content)
        if self.content_hash != expected:
            raise ValueError(
                "content_hash does not match sha256(content); tampered or stale unit is rejected"
            )
        return self

    @model_validator(mode="after")
    def _candidate_authorization(self) -> "KnowledgeUnit":
        allowed = candidate_values_for(self.applicable_path)
        if not allowed:
            raise ValueError(
                f"no authorized candidate table exists for path {self.applicable_path!r}"
            )
        if self.candidate_value not in allowed:
            raise ValueError(
                f"candidate_value {self.candidate_value!r} is not an authorized candidate for "
                f"{self.applicable_path!r} (authorized: {list(allowed)})"
            )
        return self

    @model_validator(mode="after")
    def _review_integrity(self) -> "KnowledgeUnit":
        check_review_integrity(self.review_status, self.reviewer, self.reviewed_at)
        return self


# ---------------------------------------------------------------------------
# 清单与语料容器
# ---------------------------------------------------------------------------


class KnowledgeCorpusFile(BaseModel):
    """清单中的单个 JSONL 文件条目。"""

    model_config = _FROZEN

    path: str
    sha256: str
    unit_count: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def _path_shape(cls, value: str) -> str:
        _single_line_string(value, what="manifest file path", max_chars=MAX_REPOSITORY_PATH_CHARS)
        if value.startswith("/") or "\\" in value or ".." in value.split("/"):
            raise ValueError("manifest file path must be a safe corpus-relative path")
        if not value.endswith(".jsonl"):
            raise ValueError("manifest file path must end with .jsonl")
        return value

    @field_validator("sha256")
    @classmethod
    def _hash_shape(cls, value: str) -> str:
        if not isinstance(value, str) or not _HEX64.match(value):
            raise ValueError("manifest sha256 must be a 64-character lowercase hex digest")
        return value


class KnowledgeManifest(BaseModel):
    """语料清单：语料版本 + 每文件哈希 + Schema/分词/检索版本。"""

    model_config = _FROZEN

    schema_version: str
    corpus_version: str
    tokenizer_version: str
    retrieval_version: str
    files: tuple[KnowledgeCorpusFile, ...]

    @field_validator("schema_version", "corpus_version")
    @classmethod
    def _version_text_shape(cls, value: str) -> str:
        return _single_line_string(value, what="manifest version field", max_chars=MAX_METADATA_CHARS)

    @field_validator("tokenizer_version", "retrieval_version")
    @classmethod
    def _slug_shape(cls, value: str) -> str:
        _single_line_string(value, what="tokenizer/retrieval version", max_chars=MAX_METADATA_CHARS)
        if not _SLUG_PATTERN.match(value):
            raise ValueError(
                "tokenizer/retrieval version must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}"
            )
        return value

    @field_validator("files")
    @classmethod
    def _files_non_empty(cls, value: tuple[KnowledgeCorpusFile, ...]) -> tuple[KnowledgeCorpusFile, ...]:
        if not value:
            raise ValueError("manifest must list at least one JSONL file")
        return value


class KnowledgeCorpus(BaseModel):
    """已校验的语料：`all_units` 供审计；`build_units()` 只返回 approved。"""

    model_config = _FROZEN

    manifest: KnowledgeManifest
    all_units: tuple[KnowledgeUnit, ...]

    @property
    def corpus_version(self) -> str:
        return self.manifest.corpus_version

    @property
    def all_unit_count(self) -> int:
        return len(self.all_units)

    def build_units(self) -> tuple[KnowledgeUnit, ...]:
        """构建消费集合：**只允许 `approved`**（draft/rejected 一律排除）。"""
        return tuple(unit for unit in self.all_units if unit.review_status is ReviewStatus.APPROVED)


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------


class KnowledgeError(Exception):
    """知识语料/合同错误；`.code` 使用 `knowledge.*` 命名空间。

    消息统一截断到 `MAX_ERROR_MESSAGE_CHARS`，避免把不可信输入原样无限回显。
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        text = message if len(message) <= MAX_ERROR_MESSAGE_CHARS else message[: MAX_ERROR_MESSAGE_CHARS - 3] + "..."
        super().__init__(f"[{code}] {text}")


KNOWLEDGE_MANIFEST_MISSING = "knowledge.manifest_missing"
KNOWLEDGE_MANIFEST_INVALID = "knowledge.manifest_invalid"
KNOWLEDGE_SCHEMA_VERSION_UNSUPPORTED = "knowledge.schema_version_unsupported"
KNOWLEDGE_VERSION_MISMATCH = "knowledge.version_mismatch"
KNOWLEDGE_UNSAFE_PATH = "knowledge.unsafe_path"
KNOWLEDGE_FILE_MISSING = "knowledge.file_missing"
KNOWLEDGE_FILE_UNLISTED = "knowledge.file_unlisted"
KNOWLEDGE_HASH_MISMATCH = "knowledge.hash_mismatch"
KNOWLEDGE_INVALID_ENCODING = "knowledge.invalid_encoding"
KNOWLEDGE_INVALID_UNIT = "knowledge.invalid_unit"
KNOWLEDGE_DUPLICATE_ID = "knowledge.duplicate_id"
KNOWLEDGE_UNIT_COUNT_MISMATCH = "knowledge.unit_count_mismatch"
KNOWLEDGE_EMPTY_CORPUS = "knowledge.empty_corpus"


__all__ = [
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
    "clean_target_models",
    "check_review_integrity",
    "ensure_review_utc",
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
]