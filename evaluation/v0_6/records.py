"""MVP v0.6 Step 02：B/C 配对执行器的数据合同（输入解析 + 输出记录）。

本模块只定义模型与确定性 ID 工具，不触网、不读环境变量、不 import `tests/`：

- **输入合同**（Step 01 冻结物）：
  - `CaseSpec`      ← `evaluation/v0_6/cases.jsonl`（每行一个独立上下文）；
  - `AnnotationSpec`← `evaluation/v0_6/annotations.jsonl`（预注册期望，逐 case 一一对应）。
  两者用 `extra="ignore"` 解析：冻结文件里存在少量诊断性字段（如
  `expected_retrieval.mechanism`、`pair.revision_seed_rule`），执行器只读取自己需要的
  子集，绝不因为 Step 01 追加只读说明字段而拒绝加载。
- **输出合同**（本步骤落盘）：`PairRecord`（run/case/repetition/arm 主键）与
  `ProviderCallRecord`（逐次图片调用），以及 Run 级 `RunRecordV06` 与 `RunSummaryV06`。
  输出模型一律 `frozen=True, extra="forbid"`，字段缺失即显式失败。

ID 方案 `bc_ids_sha256_v1`：`<prefix>_<sha256(parts 以 "|" 连接)[:16]>`，不含随机数
与时钟；同配置重放得到相同评测层 ID（产品侧 uuid4 ID 原样保留在记录里以便追溯）。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer

#: 输出记录信封版本（实现变更时递增）。
BC_RECORDS_SCHEMA_VERSION = "bc_records_v0_6"
#: 配对执行器实现版本（写入 Run 记录；与旧 A/B harness 分属不同标识）。
BC_HARNESS_VERSION = "bc_paired_harness_v1"
#: 评测层 ID 方案。
BC_ID_SCHEME_VERSION = "bc_ids_sha256_v1"

RUN_ID_PREFIX = "bcrun"
PAIR_RECORD_ID_PREFIX = "bcpair"
CALL_RECORD_ID_PREFIX = "bccall"
DECISION_ID_PREFIX = "bcdecision"

#: 两个实验臂（B = 关闭 RAG，C = 开启 RAG 并加载 v0.5-approved-1）。
Arm = Literal["B", "C"]
ARMS: tuple[Arm, Arm] = ("B", "C")

#: 一次调用/一个配对的结果状态。
#: - `ok`：成功产出图片；
#: - `failed`：确定的、未计费的失败（编译失败 / 认证 / 非法请求）；
#: - `unknown`：超时/网络/服务端错误或进程中断，结果与是否计费未知，绝不自动重发；
#: - `skipped`：续跑时按幂等策略跳过（已有成功结果或未知结果）。
PairStatus = Literal["ok", "failed", "unknown", "skipped"]

#: 调用顺序（每对固定规则的随机化；可据此复建真实调用次序）。
CallOrder = Literal["B_first", "C_first"]


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware UTC; naive datetimes are rejected")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


_UtcDatetime = Annotated[
    datetime,
    AfterValidator(_ensure_utc),
    PlainSerializer(_iso_utc, return_type=str, when_used="json"),
]


def digest(*parts: str) -> str:
    """确定性摘要：`sha256("|".join(parts))` 的十六进制（`bc_ids_sha256_v1`）。"""
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_record_id(prefix: str, *parts: str) -> str:
    """评测层稳定 ID：`<prefix>_<digest[:16]>`。"""
    return f"{prefix}_{digest(*parts)[:16]}"


# ---------------------------------------------------------------------------
# 输入合同：Step 01 冻结的 cases.jsonl / annotations.jsonl
# ---------------------------------------------------------------------------

_INPUT = ConfigDict(frozen=True, extra="ignore")


class PairSpec(BaseModel):
    """case 内的配对参数（arms / repetitions）。"""

    model_config = _INPUT

    arms: list[Arm] = Field(default_factory=lambda: ["B", "C"])
    repetitions: int = Field(default=2, ge=1)


class CaseSource(BaseModel):
    """样本来源声明（只读；本版必须全部项目原创）。"""

    model_config = _INPUT

    source_type: str
    license: str
    external_ids: list[str] = Field(default_factory=list)
    source_revision: str | None = None
    declaration: str | None = None


class CaseSpec(BaseModel):
    """一个独立上下文（`cases.jsonl` 一行）。

    `confirmed_intent` 是白名单路径 → 已确认值的映射；`delegated_paths` 是
    `user_delegated` 且**无值**的路径；`pinned_paths` 是 PIN；`explicit_paths` 是
    由用户明确给出值的路径。
    """

    model_config = _INPUT

    case_id: str
    dataset_version: str
    layer: str
    category: str
    primary_path: str
    user_text: str
    confirmed_intent: dict[str, str] = Field(default_factory=dict)
    delegated_paths: list[str] = Field(default_factory=list)
    pinned_paths: list[str] = Field(default_factory=list)
    explicit_paths: list[str] = Field(default_factory=list)
    rule_under_test: str | None = None
    pair: PairSpec = Field(default_factory=PairSpec)
    group_id: str
    parent_case_id: str | None = None
    derived_from_dataset: bool
    source: CaseSource
    notes: str = ""

    def effective_repetitions(self) -> int:
        return self.pair.repetitions


class RetrievalExpectation(BaseModel):
    model_config = _INPUT

    queried: bool
    outcome: str
    reason_code: str | None = None
    knowledge_id: str | None = None
    candidate_value: str | None = None
    top_score_positive: bool | None = None
    mechanism: str | None = None


class AdoptionExpectation(BaseModel):
    model_config = _INPUT

    outcome: str
    reason_code: str | None = None
    adoption_rejection_reason: str | None = None


class ConstraintsExpectation(BaseModel):
    model_config = _INPUT

    explicit_value_preserved: bool | None = None
    pin_preserved: bool | None = None
    confirmation_binding_preserved: bool | None = None
    immutable_history_violations: int = 0
    unauthorized_path_changes: int = 0


class PromptDeltaExpectation(BaseModel):
    model_config = _INPUT

    path: str
    b_value_source: str | None = None
    c_value_source: str | None = None
    other_paths_identical: bool = True


class Stratification(BaseModel):
    model_config = _INPUT

    layer: str
    category: str
    primary_path: str
    reported_separately: bool = True
    independent_context_unit: str


class AnnotationSpec(BaseModel):
    """一个上下文的预注册期望（`annotations.jsonl` 一行）。"""

    model_config = _INPUT

    case_id: str
    dataset_version: str
    layer: str
    primary_path: str
    rule_under_test: str | None = None
    expected_retrieval: RetrievalExpectation
    expected_adoption: AdoptionExpectation
    expected_constraints: ConstraintsExpectation
    expected_bc_relation: str
    expected_prompt_delta: PromptDeltaExpectation
    fallback_constraint: str
    stratification: Stratification
    metric_roles: list[str] = Field(default_factory=list)
    annotation_basis: str | None = None
    annotation_status: str | None = None
    annotator: str | None = None
    independent_reviewer: str | None = None
    independent_reviewed_at: str | None = None
    #: 越权可见性预注册（Step 01 对 L2 明确值/PIN 提供）：满足规则候选 vs 现存值是否不同。
    overreach_check: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# 输出合同
# ---------------------------------------------------------------------------

_OUTPUT = ConfigDict(frozen=True, extra="forbid")


class ErrorRecord(BaseModel):
    """一次失败的结构化记录（code 原样保留产品/Provider 命名空间）。"""

    model_config = _OUTPUT

    code: str
    message: str
    stage: Literal["image", "workflow", "generation", "prompt", "persistence", "runner"]
    retryable: bool | None = None
    status_code: int | None = None
    provider_request_id: str | None = None


class ProviderCallRecord(BaseModel):
    """一次图片 Provider 调用的随机性与记账记录。

    - `seed_requested`：请求面是否携带 seed。当前 `ImageGenerationRequest` 没有 seed
      字段，因此恒为 `None`——**不得**把 revision seed 当图片 seed。
    - `seed_returned`：只记录 Provider 返回值；未返回即 `None`（禁止伪造）。
    - `internal_attempts_observed`：适配器内部重试不可观察，恒为 `None`；
      `worst_case_attempts` 按 `1 + Settings.http_max_retries` 预留。
    """

    model_config = _OUTPUT

    call_id: str
    attempt_index: int = Field(ge=1)
    role: Literal["image"] = "image"
    prompt_sha256: str
    size: str
    model_requested: str | None = None
    model_returned: str | None = None
    seed_requested: None = None
    seed_returned: int | None = None
    provider_request_id: str | None = None
    endpoint_redacted: str | None = None
    latency_ms: float = Field(ge=0)
    internal_attempts_observed: None = None
    worst_case_attempts: int = Field(ge=1)
    status: Literal["ok", "failed", "unknown"]
    cost_minor: int = Field(ge=0, default=0)
    cost_source: str
    currency: str | None = None
    error: ErrorRecord | None = None


class RetrievalTrace(BaseModel):
    """一个配对在目标路径上的检索/采用观察（B 臂为“未检索”）。"""

    model_config = _OUTPUT

    queried: bool
    outcome: str
    reason_code: str | None = None
    corpus_version: str | None = None
    corpus_fingerprint: str | None = None
    manifest_sha256: str | None = None
    bundle_ids: list[str] = Field(default_factory=list)
    knowledge_id: str | None = None
    candidate_value: str | None = None
    adoption_outcome: str | None = None
    adoption_rejection_reason: str | None = None
    fallback_value: str | None = None
    actual_value: str | None = None
    actual_value_source: str | None = None
    #: C 臂：采用值是否恰好等于确定性回退值（True = 采用但 Prompt 无可观察差异）。
    #: 这是有效样本，如实记录，不做人为规避；B 臂为 None。
    adopted_without_prompt_delta: bool | None = None
    #: 预注册越权可见性检查（L2 明确值/PIN 可追溯；其它层为 None）。
    overreach_check: dict[str, Any] | None = None


class PairRecord(BaseModel):
    """一个 `case × repetition × arm` 的完整记录（append-only 主键单元）。"""

    model_config = _OUTPUT

    schema_version: str = BC_RECORDS_SCHEMA_VERSION
    record_id: str
    run_id: str
    case_id: str
    repetition: int = Field(ge=1)
    arm: Arm
    #: 该 (case, repetition, arm) 的第几次 arm 运行（续跑重试时 > 1；每次全新库/会话）。
    attempt: int = Field(ge=1, default=1)
    layer: str
    category: str
    primary_path: str
    call_order: CallOrder
    status: PairStatus
    session_id: str
    db_rel_path: str
    intent_revision_id: str
    execution_revision_id: str
    confirmation_id: str
    summary_hash: str
    confirmation_valid: bool
    prompt_artifact_id: str | None = None
    prompt_sha256: str | None = None
    generation_id: str | None = None
    realization_id: str | None = None
    generation_seed_returned: int | None = None
    image_rel_paths: list[str] = Field(default_factory=list)
    retrieval: RetrievalTrace
    provider_calls: list[ProviderCallRecord] = Field(default_factory=list)
    error: ErrorRecord | None = None
    latency_ms: float = Field(ge=0, default=0.0)
    resumed: bool = False
    #: True = 由预算账本中未结算的预留重建的“进程中断/结果未知”记录（未再次调用 Provider）。
    interrupted: bool = False
    created_at: _UtcDatetime


class RunKnowledgeSnapshot(BaseModel):
    model_config = _OUTPUT

    corpus_dir: str
    corpus_version: str | None = None
    corpus_fingerprint: str | None = None
    manifest_sha256: str | None = None
    approved_unit_count: int | None = None


class RunProviderSnapshot(BaseModel):
    """Run 级图像 Provider 配置快照：只记非密信息与环境变量**名**。"""

    model_config = _OUTPUT

    image_model: str
    model_identity_verified: bool
    image_size: str
    images_per_generation: int = Field(ge=1)
    http_max_retries: int = Field(ge=0)
    image_timeout_seconds: float | None = None
    base_url_env: str | None = None
    api_key_env: str | None = None


class RunRecordV06(BaseModel):
    """Run 级元数据（`<run_dir>/run.json`）。"""

    model_config = _OUTPUT

    schema_version: str = BC_RECORDS_SCHEMA_VERSION
    run_id: str
    harness_version: str = BC_HARNESS_VERSION
    id_scheme_version: str = BC_ID_SCHEME_VERSION
    code_version: str
    code_commit: str | None = None
    code_dirty: bool | None = None
    identity_mode: Literal["unspecified", "formal", "diagnostic"]
    protocol_version: str
    dataset_version: str
    config_version: str
    manifest_version: str
    protocol_sha256: str
    config_sha256: str
    dataset_sha256: str
    annotations_sha256: str
    manifest_sha256: str
    knowledge: RunKnowledgeSnapshot
    provider: RunProviderSnapshot
    repetitions: int = Field(ge=1)
    case_ids: list[str] = Field(default_factory=list)
    case_count: int = Field(ge=0)
    arms: list[Arm] = Field(default_factory=lambda: ["B", "C"])
    call_order_rule: str
    revision_seed_rule: str
    image_seed_rule: str
    dry_run: bool
    real_run_authorized: bool
    created_at: _UtcDatetime


class BudgetLedgerEntry(BaseModel):
    """预算账本一行（append-only；`reserve` 先于请求落盘，`settle` 事后落盘）。"""

    model_config = _OUTPUT

    schema_version: str = BC_RECORDS_SCHEMA_VERSION
    event: Literal["reserve", "settle"]
    reservation_id: str
    pair_key: str
    attempt_index: int = Field(ge=1)
    created_at: _UtcDatetime
    worst_case_attempts: int | None = Field(default=None, ge=1)
    worst_case_cost_minor: int | None = Field(default=None, ge=0)
    charged_cost_minor: int | None = Field(default=None, ge=0)
    cost_source: str | None = None
    currency: str | None = None
    status: Literal["ok", "failed", "unknown"] | None = None


class DecisionRequired(BaseModel):
    """需要人工决定的事项（unknown 请求绝不自动重发）。"""

    model_config = _OUTPUT

    decision_id: str
    run_id: str
    case_id: str
    repetition: int
    arm: Arm
    reason: str
    pair_key: str
    provider_request_id: str | None = None
    created_at: _UtcDatetime


class LayerSummary(BaseModel):
    """按预注册分层报告的分母（失败/未知不剔除）。

    同时保留两种分母，禁止混用：

    - `planned_comparison_pairs` = 该层 `case × repetition`（真正的 B/C 比较对数）；
    - `planned_arm_runs`        = 该层 `case × repetition × arm`（基础 arm 运行/请求数）。
    """

    model_config = _OUTPUT

    layer: str
    planned_comparison_pairs: int = Field(ge=0)
    planned_arm_runs: int = Field(ge=0)
    ok: int = Field(ge=0)
    failed: int = Field(ge=0)
    unknown: int = Field(ge=0)
    skipped: int = Field(ge=0)
    not_attempted: int = Field(ge=0)
    both_ok: int = Field(ge=0)
    b_failed: int = Field(ge=0)
    c_failed: int = Field(ge=0)
    both_failed: int = Field(ge=0)
    pair_unknown: int = Field(ge=0)
    not_run: int = Field(ge=0)


#: 一个比较对（case × repetition）的结局分类；`sum(pair_outcomes) == planned_comparison_pairs`。
PAIR_OUTCOMES: tuple[str, ...] = (
    "both_ok",
    "b_failed",
    "c_failed",
    "both_failed",
    "unknown",
    "not_run",
)


def classify_pair_outcome(b_status: str | None, c_status: str | None) -> str:
    """把一个比较对的 B/C 两臂状态映射到封闭结局词表。

    `None` 表示该臂没有记录（未开始/预算停止）。任一臂 `unknown` 优先归类为
    `unknown`（结果/计费未知，绝不当作普通失败或成功）。
    """
    if b_status is None or c_status is None:
        return "not_run"
    statuses = {b_status, c_status}
    if "unknown" in statuses:
        return "unknown"
    if b_status == "ok" and c_status == "ok":
        return "both_ok"
    if b_status == "ok" and c_status == "failed":
        return "c_failed"
    if c_status == "ok" and b_status == "failed":
        return "b_failed"
    if b_status == "failed" and c_status == "failed":
        return "both_failed"
    return "not_run"


class RunSummaryV06(BaseModel):
    """Run 级汇总：两种分母清晰区分、失败/未知显式保留。

    - `planned_comparison_pairs = case_count × repetitions`（净胜率等配对指标的分母）；
    - `planned_arm_runs = case_count × repetitions × 2`（基础 arm 运行 / 图片请求数）；
    - `pair_outcomes` 按 `both_ok / b_failed / c_failed / both_failed / unknown / not_run`
      分类，之和恒等于 `planned_comparison_pairs`。**不得**用 arm 运行数（120）作为
      配对指标分母。
    """

    model_config = _OUTPUT

    schema_version: str = BC_RECORDS_SCHEMA_VERSION
    run_id: str
    planned_comparison_pairs: int = Field(ge=0)
    planned_arm_runs: int = Field(ge=0)
    records_total: int = Field(ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    pair_outcomes: dict[str, int] = Field(default_factory=dict)
    layers: list[LayerSummary] = Field(default_factory=list)
    retrieval_counts: dict[str, int] = Field(default_factory=dict)
    adoption_counts: dict[str, int] = Field(default_factory=dict)
    fallback_reasons: dict[str, int] = Field(default_factory=dict)
    provider_calls_total: int = Field(ge=0)
    provider_calls_ok: int = Field(ge=0)
    unknown_calls: int = Field(ge=0)
    #: 已结算的**已知**成本（成功调用按最坏上界、确定性拒绝记 0）。
    known_cost_minor: int = Field(ge=0)
    #: 结果未知/未结算预留的**成本上界**（绝不并入 known）。
    unknown_cost_upper_bound_minor: int = Field(ge=0, default=0)
    currency: str | None = None
    cost_basis: str
    decisions_required: int = Field(ge=0)
    resumed_ok_skipped: int = Field(ge=0)
    failed_pair_keys: list[str] = Field(default_factory=list)
    unknown_pair_keys: list[str] = Field(default_factory=list)
    #: 因预算/尝试上限未开始的计划 arm 运行（仍计入分母，绝不静默丢弃）。
    unattempted_arm_runs: int = Field(ge=0, default=0)
    unattempted_pair_keys: list[str] = Field(default_factory=list)
    safety_violations: list[str] = Field(default_factory=list)
    budget_exhausted: bool
    created_at: _UtcDatetime


class PairedRunResult(BaseModel):
    """`PairedRunner.run()` 的内存返回（全部记录已落盘）。"""

    model_config = _OUTPUT

    run: RunRecordV06
    records: list[PairRecord] = Field(default_factory=list)
    summary: RunSummaryV06


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def error_record(exc: BaseException, *, stage: str) -> ErrorRecord:
    """把产品/Provider 异常转成结构化失败记录（code 原样保留）。"""
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or "." not in code:
        code = "evaluation.unexpected_error"
    return ErrorRecord(
        code=code,
        message=str(exc),
        stage=stage,  # type: ignore[arg-type]
        retryable=getattr(exc, "retryable", None),
        status_code=getattr(exc, "status_code", None),
        provider_request_id=getattr(exc, "provider_request_id", None),
    )


def dump_json(value: BaseModel | Any) -> Any:
    """把模型转成可 JSON 序列化的纯数据（frozen 记录落盘统一入口）。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


__all__ = [
    "BC_RECORDS_SCHEMA_VERSION",
    "BC_HARNESS_VERSION",
    "BC_ID_SCHEME_VERSION",
    "RUN_ID_PREFIX",
    "PAIR_RECORD_ID_PREFIX",
    "CALL_RECORD_ID_PREFIX",
    "DECISION_ID_PREFIX",
    "Arm",
    "ARMS",
    "PairStatus",
    "CallOrder",
    "PairSpec",
    "CaseSource",
    "CaseSpec",
    "RetrievalExpectation",
    "AdoptionExpectation",
    "ConstraintsExpectation",
    "PromptDeltaExpectation",
    "Stratification",
    "AnnotationSpec",
    "ErrorRecord",
    "ProviderCallRecord",
    "RetrievalTrace",
    "PairRecord",
    "RunKnowledgeSnapshot",
    "RunProviderSnapshot",
    "RunRecordV06",
    "BudgetLedgerEntry",
    "DecisionRequired",
    "LayerSummary",
    "PAIR_OUTCOMES",
    "classify_pair_outcome",
    "RunSummaryV06",
    "PairedRunResult",
    "digest",
    "sha256_text",
    "make_record_id",
    "utc_now",
    "error_record",
    "dump_json",
]
