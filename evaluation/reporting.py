"""MVP v0.3 Step 03：统一评测结果合同 + 逐案例 JSONL 落盘 + 聚合摘要。

本模块是 Gate A 评测记录的**唯一合同**（协议第 5/7/8 节落地）：

- 输入合同：冻结标注文件（`evaluation/annotations/core_v0_3.jsonl`）的只读模型
  （`CaseAnnotation` 等），供 Runner 与 L1~L3 指标共享；只读载入，绝不改写标注；
- 输出合同：`EvalRunRecord` / `EvalCaseRecord` / `EvalTurnRecord` / `MetricRecord` /
  `EvalFailureRecord` / `ArtifactRef`（A/B 两系统同一形状）；
- 缺失数据三态 `not_applicable` / `missing_data` / `failed` 逐案例逐指标显式记录，
  **不从分母静默剔除**（协议第 5 节）；
- 逐案例 JSONL（`cases/<case_id>.jsonl`，每行一个带 `record_type` 的记录）+
  `run.json` + `summary.json`；聚合结果只引用（不复制）Baseline 记录 ID 与
  Artifact 路径，可追溯到原始 Turn/Artifact；
- L1 任一核心不变量失败 → 摘要显式 `gate_a_blocked = true`（协议第 4 节 Layer 1）。

随机性记录口径（协议第 7 节）：System B 的每次 LLM/图像调用由 Runner 的
记录包装器落 `LLMCallObservation` / `ImageCallObservation`；Baseline A 的同等信息
已在 Step 02 的 `BaselineTurnRecord` 内，本合同以 ID **引用**而非复制
（`EvalTurnRecord.baseline_turn_record_id`）。`retry_count` 只表达"评测层未重试"
（恒 0）——adapter 内部冻结重试不通过 Provider Protocol 暴露，保持诚实不虚构
（与 Step 02 已知限制 1 同口径）。

全部模型 `frozen=True, extra="forbid"`；`created_at` 一律 tz-aware UTC。
任何凭据（base_url / api_key 的值）**绝不进入任何字段**——只记录环境变量名。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Iterable, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from evaluation.direct_baseline import digest

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: 本模块记录信封的版本。
EVAL_RECORDS_SCHEMA_VERSION = "eval_records_v1"
EvalRecordsSchemaVersion = Literal["eval_records_v1"]

#: 评测框架实现版本（实现变更时递增；写入 Run 记录）。
EVALUATION_HARNESS_VERSION = "evaluation_harness_v1"

#: 参评系统标识（协议第 1 节）。
SystemId = Literal["baseline_a", "system_b"]
SYSTEM_IDS: tuple[SystemId, SystemId] = ("baseline_a", "system_b")

#: 指标层（L4 图片盲评属 Step 04，本层集合刻意不含 L4）。
LayerId = Literal["L1", "L2", "L3"]

#: 案例结果状态（冻结配置 `failure_handling.case_result_status_enum` 三值）。
EvalCaseStatus = Literal["completed", "flow_deviation", "failed"]

#: 逐轮执行状态（`skipped` 只用于 Baseline 的 accept 轮；`blocked` 为 R1-B
#: 级联口径：首次根因之后无法执行的脚本轮，不重复记独立根因）。
EvalTurnStatus = Literal["completed", "skipped", "failed", "blocked"]

#: 指标三态 + 正常（协议第 5 节；三态不剔分母）。
MetricStatus = Literal["ok", "not_applicable", "missing_data", "failed"]

#: 数据集轮次形态（单轮/多轮分开聚合的维度键）。
TurnType = Literal["single", "multi"]

#: 聚合范围：`single` / `multi` 分开保留，`all` 为二者合并且可继续向上聚合。
TurnScope = Literal["single", "multi", "all"]

#: 轮次种类（与冻结数据集 `turns[].kind` 一致）。
EvalTurnKind = Literal["user_message", "clarification_answer", "image_feedback", "accept"]

#: 期望结局四值枚举（协议第 2.2 节）。
ExpectedOutcome = Literal[
    "ready_and_generate",
    "clarification_expected",
    "accepted_completed",
    "no_state_change",
]

#: 记录 ID 前缀（`eval_ids_sha256_v1` 方案：全部 ID = `sha256(parts 以 "|" 连接)[:16]`，
#: 与 Step 02 `baseline_ids_sha256_v1` 同族；不含随机数与时钟，重放产生相同 ID）。
EVAL_ID_SCHEME_VERSION = "eval_ids_sha256_v1"
RUN_ID_PREFIX = "erun"
CASE_RECORD_ID_PREFIX = "ecase"
TURN_RECORD_ID_PREFIX = "eturn"
METRIC_RECORD_ID_PREFIX = "emet"
ARTIFACT_REF_ID_PREFIX = "eart"
LLM_OBSERVATION_ID_PREFIX = "ellm"
IMAGE_OBSERVATION_ID_PREFIX = "eimg"

#: L1 核心不变量标识（协议第 4 节 Layer 1 四条；任一失败 → Gate A 阻断）。
L1_INVARIANT_UNAUTHORIZED = "unauthorized_fields_unchanged"
L1_INVARIANT_CONFIRMATION = "no_stale_confirmation"
L1_INVARIANT_PINNED = "pinned_not_overwritten"
L1_INVARIANT_HISTORY = "history_append_only"
L1_INVARIANTS: tuple[str, str, str, str] = (
    L1_INVARIANT_UNAUTHORIZED,
    L1_INVARIANT_CONFIRMATION,
    L1_INVARIANT_PINNED,
    L1_INVARIANT_HISTORY,
)

#: L2 指标名（仅 System B；协议第 4 节 Layer 2）。
L2_METRICS: tuple[str, str, str, str, str] = (
    "delta_accuracy",
    "missing_decision_recall",
    "clarification_precision",
    "conflict_detection",
    "delegation_scope_accuracy",
)

#: L3 指标名（A/B 同口径；协议第 4 节 Layer 3）。
L3_METRICS: tuple[str, str, str, str] = (
    "intent_coverage",
    "unauthorized_addition",
    "preservation",
    "model_compatibility",
)

#: r1 L3 指标集：在 v1 基础上新增 `generation_completion`（应生成而失败的完成率，
#: R1-B #3：失败单独记，不通过删除分母美化结果）。
L3_R1_METRICS: tuple[str, str, str, str, str] = (*L3_METRICS, "generation_completion")

#: r1 指标版本标识（新旧指标分别标识；见 protocol_v0_3_r1.md §4.3）。
METRIC_VERSION_V1 = "v1"
METRIC_VERSION_R1 = "v0_3_r1"


def _ensure_utc(value: datetime) -> datetime:
    """拒绝 naive datetime；把 tz-aware 值归一到 UTC。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware UTC; naive datetimes are rejected")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


_UtcDatetime = Annotated[datetime, AfterValidator(_ensure_utc), PlainSerializer(_iso_utc)]


def make_eval_record_id(prefix: str, *parts: str) -> str:
    """评测记录稳定 ID：`<prefix>_<digest[:16]>`（方案 `eval_ids_sha256_v1`）。"""
    return f"{prefix}_{digest(*parts)[:16]}"


# ---------------------------------------------------------------------------
# 输入合同：冻结标注（只读；结构与 evaluation/annotations/core_v0_3.jsonl 对齐）
# ---------------------------------------------------------------------------


class ValueMatchSpec(BaseModel):
    """`value_match` 真值（协议第 2.3 节四种模式 + `null`）。"""

    model_config = _FROZEN

    mode: Literal["exact_token", "contains_any", "int_equals", "any_non_empty", "null"]
    keywords: list[str] = Field(default_factory=list)
    value: int | None = None


class ExpectedDeltaSpec(BaseModel):
    """金标准 Delta：`operation` + `path` + `value_match` + `resolution`（可选约束）。"""

    model_config = _FROZEN

    operation: Literal["SET", "CLEAR", "PIN", "UNPIN"]
    path: str
    value_match: ValueMatchSpec | None = None
    resolution: str | None = None


class ExpectedRejectionSpec(BaseModel):
    """条件性拒绝：若系统提出该 Delta 则必须被拒（命中任一 issue code 即符合）。"""

    model_config = _FROZEN

    operation: Literal["SET", "CLEAR", "PIN", "UNPIN"]
    path: str
    expected_issue_codes: list[str] = Field(default_factory=list)
    only_if_emitted: bool = True
    resolution: str | None = None


class MustClarifySpec(BaseModel):
    """必须澄清的目标（`allow_delegate` 为标注期望，可选）。"""

    model_config = _FROZEN

    target_path: str
    reason: str | None = None
    allow_delegate: bool | None = None


class ExpectedConflictSpec(BaseModel):
    """期望检出的冲突（同 rule_id 匹配；`blocking=True` 未检出且进入 ready 记严重失败）。"""

    model_config = _FROZEN

    rule_id: str
    kind: str | None = None
    blocking: bool = False


class ExpectedCarrySpec(BaseModel):
    """期望的 Realization 继承/失效路径集合（仅作证据记录，不单独计分）。"""

    model_config = _FROZEN

    carried: list[str] = Field(default_factory=list)
    invalidated: list[str] = Field(default_factory=list)


class TurnAnnotation(BaseModel):
    """一轮的标注真值（字段语义以协议第 2.2 节为准）。"""

    model_config = _FROZEN

    turn_id: str
    expected_deltas: list[ExpectedDeltaSpec] = Field(default_factory=list)
    acceptable_extra_deltas: list[ExpectedDeltaSpec] = Field(default_factory=list)
    expected_rejections: list[ExpectedRejectionSpec] = Field(default_factory=list)
    forbidden_change_paths: list[str] = Field(default_factory=list)
    paths_must_remain_unset: list[str] = Field(default_factory=list)
    expected_resolution_records: dict[str, str] = Field(default_factory=dict)
    blocking_missing_paths_after_turn: list[str] = Field(default_factory=list)
    must_clarify: list[MustClarifySpec] = Field(default_factory=list)
    must_not_clarify_paths: list[str] = Field(default_factory=list)
    acceptable_clarify_paths: list[str] = Field(default_factory=list)
    expected_conflicts: list[ExpectedConflictSpec] = Field(default_factory=list)
    expected_feedback_decision: str | None = None
    expected_carry: ExpectedCarrySpec | None = None
    expected_outcome: str | None = None
    notes: str | None = None
    #: R1-B #2：按路径关联的保留词组（path → 同义关键词组列表）。r1 Preservation
    #: 只比较这些“仍被要求保留”的属性；旧标注缺省为空（回退到 v1 口径）。
    preserve_path_groups: dict[str, list[list[str]]] = Field(default_factory=dict)


class ImageEvalDimension(BaseModel):
    """L4 维度与评审焦点（Step 04 消费；本步只忠实载入）。"""

    model_config = _FROZEN

    dimension: str
    focus: str
    applies_to_turns: list[str] | None = None


class PromptExpectations(BaseModel):
    """L3 关键词真值：同义关键词组 + 禁止出现关键词。"""

    model_config = _FROZEN

    must_mention_groups: list[list[str]] = Field(default_factory=list)
    must_not_mention: list[str] = Field(default_factory=list)


class CaseAnnotation(BaseModel):
    """一个案例的完整标注（与 fixture 同 ID 同序）。"""

    model_config = _FROZEN

    case_id: str
    dataset_version: str
    scenario: str
    #: R1-B #1：r1 标注记录来源案例 ID 与修订理由（旧标注缺省 None）。
    parent_case_id: str | None = None
    annotation_revision_reason: str | None = None
    annotation_basis: str | None = None
    turn_annotations: list[TurnAnnotation] = Field(default_factory=list)
    image_evaluation_dimensions: list[ImageEvalDimension] = Field(default_factory=list)
    #: 关键词真值可为 None（如 `s06-delegate-vague-002`：无就绪态 Prompt 可评估的
    #: 案例）——此时 L3 文本层指标记 `missing_data` 并注明原因，不得伪造评分。
    prompt_expectations: PromptExpectations | None = None
    notes: str | None = None

    def turn_annotation(self, turn_id: str) -> TurnAnnotation | None:
        """按 turn_id 取标注（无则 None，调用方决定如何记录）。"""
        for annotation in self.turn_annotations:
            if annotation.turn_id == turn_id:
                return annotation
        return None


def load_annotations(path: Path) -> list[CaseAnnotation]:
    """读入冻结标注 JSONL；同一文件内 case_id 必须唯一。"""
    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read annotations {source}: {exc}") from exc
    annotations: list[CaseAnnotation] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            annotation = CaseAnnotation.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{source}:{lineno}: invalid annotation: {exc}") from exc
        if annotation.case_id in seen:
            raise ValueError(f"{source}:{lineno}: duplicate case_id {annotation.case_id!r}")
        seen.add(annotation.case_id)
        annotations.append(annotation)
    if not annotations:
        raise ValueError(f"annotations {source} contains no cases")
    return annotations


# ---------------------------------------------------------------------------
# 输出合同：Failure / Artifact 引用 / 调用观察
# ---------------------------------------------------------------------------


class EvalFailureRecord(BaseModel):
    """一次失败的结构化记录（协议第 7 节：失败保留在分母，携带错误 code）。

    `code` 沿用来源命名空间：`provider.*`（Provider 边界）、`workflow.*` /
    `generation.*` / `prompt.*`（System B 硬门禁）、`baseline.*`（Baseline 执行器
    自身判定，如 `baseline.empty_llm_output`）、`evaluation.*`（评测层自身判定）。

    `stage` 与 Step 02 `BaselineErrorStage`（`llm` / `image` / `baseline`）兼容：
    Baseline 轮失败时原样透传，不做有损映射。
    """

    model_config = _FROZEN

    code: str
    message: str
    stage: Literal["llm", "image", "baseline", "workflow", "generation", "prompt", "runner"]
    retryable: bool | None = None
    status_code: int | None = None
    provider_request_id: str | None = None

    @field_validator("code")
    @classmethod
    def _code_must_be_namespaced(cls, value: str) -> str:
        if "." not in value or value != value.lower():
            raise ValueError(f"error code must be a lowercase '<area>.<name>' string, got {value!r}")
        return value


class ArtifactRef(BaseModel):
    """对原始 Artifact 的**引用**（不复制内容；聚合可追溯的最小单元）。

    - `ref_id`：被引用方的原始 ID（如 Baseline `bart_*` / `bprompt_*`，System B 的
      `gen_*` / `pra_*` / SQLite 文件引用）；
    - `path`：相对 **Run 根目录** 的路径（`None` 表示仅 ID 引用，如库内 Artifact）；
    - `designated_for_l4`：该图片是否进入 L4 盲评（冻结口径：仅第 1 次重复的
      图片成对进入 L4，见 runner 模块 docstring）。
    """

    model_config = _FROZEN

    artifact_ref_id: str
    kind: Literal[
        "baseline_case_record",
        "baseline_turn_record",
        "baseline_prompt",
        "baseline_image",
        "prompt_artifact",
        "generation_artifact",
        "image_file",
        "sqlite_db",
        "case_jsonl",
    ]
    ref_id: str
    path: str | None = None
    sha256: str | None = None
    designated_for_l4: bool = False
    note: str | None = None


class LLMCallObservation(BaseModel):
    """System B 一次 LLM 调用的随机性记录（协议第 7 节字段逐项对应）。

    `temperature_sent` / `max_tokens_sent` 恒为 `None`（冻结条件：System B 三处
    调用点不显式设置采样参数）；`response_format_sent` 照实登记
    （Interpreter / FeedbackEngine 为 `{"type": "json_object"}`）。
    """

    model_config = _FROZEN

    schema_version: EvalRecordsSchemaVersion = EVAL_RECORDS_SCHEMA_VERSION
    call_id: str
    role: Literal["interpreter", "feedback_engine"]
    model_requested: str | None = None
    model_returned: str | None = None
    temperature_sent: None = None
    max_tokens_sent: None = None
    response_format_sent: dict[str, Any] | None = None
    provider_request_id: str | None = None
    latency_ms: float = Field(ge=0)
    retry_count: int = Field(ge=0, default=0)
    status: Literal["ok", "failed"]
    error: EvalFailureRecord | None = None


class ImageCallObservation(BaseModel):
    """System B 一次图像调用的随机性记录（seed 只记录 Provider 返回值，禁止伪造）。"""

    model_config = _FROZEN

    schema_version: EvalRecordsSchemaVersion = EVAL_RECORDS_SCHEMA_VERSION
    call_id: str
    prompt_sha256: str
    size: str
    model_requested: str | None = None
    model_returned: str | None = None
    seed: int | None = None
    provider_request_id: str | None = None
    latency_ms: float = Field(ge=0)
    retry_count: int = Field(ge=0, default=0)
    status: Literal["ok", "failed"]
    error: EvalFailureRecord | None = None
    image_count: int = Field(ge=0, default=0)


# ---------------------------------------------------------------------------
# 输出合同：System B 逐轮观察
# ---------------------------------------------------------------------------


class ObservedDelta(BaseModel):
    """System B 一条被接受 Delta 的扁平观察（值与 Resolution 均为纯数据）。"""

    model_config = _FROZEN

    operation: str
    path: str
    value: str | int | None = None
    resolution: str | None = None


class ObservedIssue(BaseModel):
    """System B 一条可观察 issue（validation / policy / interpreter / feedback）。"""

    model_config = _FROZEN

    code: str
    path: str | None = None
    severity: str | None = None


class ObservedQuestion(BaseModel):
    """System B 本轮落地（或仍生效）的待答问题。"""

    model_config = _FROZEN

    question_id: str
    target_path: str
    allow_delegate: bool
    allow_custom: bool = True
    suggested_values: list[str] = Field(default_factory=list)


class ObservedConfirmation(BaseModel):
    """Runner 代表用户完成的一次自动确认（协议第 3 节：重算 summary_hash 后确认）。"""

    model_config = _FROZEN

    confirmation_id: str
    intent_revision_id: str
    execution_revision_id: str
    summary_hash: str
    #: 本轮变更是否使旧确认失效（来自确认摘要的 change_summary；无法取得为 None）。
    confirmation_invalidated: bool | None = None


class ObservedGeneration(BaseModel):
    """一次成功生成及其确认绑定证据（L1 不变量 2 的观测面）。"""

    model_config = _FROZEN

    generation_id: str
    prompt_artifact_id: str
    #: 该 PromptArtifact 绑定的确认 / Intent revision（L1 复核点）。
    based_on_confirmation_id: str
    based_on_intent_revision_id: str
    #: 生成时刻会话快照的当前 revision 指针。
    current_intent_revision_id: str | None = None
    current_execution_revision_id: str | None = None
    prompt_text: str
    prompt_sha256: str
    target_model: str
    size: str
    seed: int | None = None
    image_paths: list[str] = Field(default_factory=list)


class SystemBTurnObservation(BaseModel):
    """System B 一轮的完整观察（驱动结果 + L1/L2/L3 所需的全部只读状态）。

    所有产品侧 ID（`ses_*` / `irev_*` / `gen_*` 等）原样保留以便追溯；它们含
    uuid4，跨重放不稳定——重放一致性以结构投影（掩码动态 ID）断言，见测试。
    """

    model_config = _FROZEN

    session_id: str
    state_before: str
    state_after: str
    intent_revision_before: str | None = None
    intent_revision_after: str | None = None
    applied_deltas: list[ObservedDelta] = Field(default_factory=list)
    issues: list[ObservedIssue] = Field(default_factory=list)
    #: `unresolved_decisions`（action=block）的路径，与标注
    #: `blocking_missing_paths_after_turn` 同口径（每 Decision 第一个未解决成员）。
    blocking_unresolved_paths: list[str] = Field(default_factory=list)
    unresolved_decisions: list[dict[str, str]] = Field(default_factory=list)
    #: 归一化后的冲突 rule_id 列表（剥离 `policy.` 前缀；含 Interpreter 报告项）。
    conflict_rule_ids: list[str] = Field(default_factory=list)
    question: ObservedQuestion | None = None
    ready_for_confirmation: bool | None = None
    auto_confirmed: bool = False
    confirmation: ObservedConfirmation | None = None
    generations: list[ObservedGeneration] = Field(default_factory=list)
    feedback_decision: str | None = None
    feedback_id: str | None = None
    feedback_recoverable_failure: bool | None = None
    #: B 工包可恢复失败接口（SubmitMessageOutcome / FeedbackOutcome 的
    #: `recoverable_failure` 与 `failure_codes`）：Runner 用它显式识别首次根因，
    #: 不凭“有无 pending_question”推断成功。旧记录缺省 None / 空。
    recoverable_failure: bool | None = None
    failure_codes: list[str] = Field(default_factory=list)
    carry_carried: list[str] = Field(default_factory=list)
    carry_invalidated: list[str] = Field(default_factory=list)
    realization_state_id: str | None = None
    #: 该轮结束后的 `user_delegated` 路径集合（L2 Delegation Scope）。
    delegated_paths_after: list[str] = Field(default_factory=list)
    #: 12 条白名单路径的值 / Resolution / PIN 逐轮快照（L1 不变量 1/3 的观测面）。
    intent_values_before: dict[str, str | int | None] = Field(default_factory=dict)
    intent_values_after: dict[str, str | int | None] = Field(default_factory=dict)
    resolutions_before: dict[str, str] = Field(default_factory=dict)
    resolutions_after: dict[str, str] = Field(default_factory=dict)
    pinned_before: list[str] = Field(default_factory=list)
    pinned_after: list[str] = Field(default_factory=list)
    #: L1 不变量 2 的探针结果：`rejected`（预期）/ `accepted`（违规）/ `not_applicable`。
    stale_revision_probe: Literal["rejected", "accepted", "not_applicable"] = "not_applicable"
    bad_hash_probe: Literal["rejected", "accepted", "not_applicable"] = "not_applicable"
    #: 逐表行数 before → after（append-only 不变量的计数面；payload 哈希由 Runner
    #: 直接交给 L1 指标函数，不进本记录，SQLite 文件本身是原始证据）。
    table_counts_before: dict[str, int] = Field(default_factory=dict)
    table_counts_after: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 输出合同：Turn / Case / Metric / Run
# ---------------------------------------------------------------------------


class EvalTurnRecord(BaseModel):
    """一轮的统一记录（A/B 同形状；Baseline 的调用细节以 ID 引用 Step 02 记录）。"""

    model_config = _FROZEN

    schema_version: EvalRecordsSchemaVersion = EVAL_RECORDS_SCHEMA_VERSION
    record_id: str
    run_id: str
    case_id: str
    system: SystemId
    repetition: int = Field(ge=1)
    turn_id: str
    turn_index: int = Field(ge=1)
    turn_kind: EvalTurnKind
    #: 实际提交给系统的文本（System B 可能是按待答问题选中的 answer_variant）。
    input_text: str | None = None
    input_source: Literal["user_text", "answer_variant", "not_executed"]
    status: EvalTurnStatus
    #: 驱动级脚本偏离（协议第 3 节第 3 条；不静默跳过、不替用户改写）。
    flow_deviation: bool = False
    flow_deviation_reason: str | None = None
    #: R1-B #4：首次根因之后无法执行的脚本轮。`blocked=True` 时 `blocked_by_turn_id`
    #: 指向同案例同重复中首个失败轮；不重复记独立根因、不自动改送反馈入口。
    blocked_by_prior_failure: bool = False
    blocked_by_turn_id: str | None = None
    expected_outcome: str | None = None
    actual_outcome: str | None = None
    outcome_matches: bool | None = None
    error: EvalFailureRecord | None = None
    #: Baseline A：Step 02 逐轮记录 ID（引用，不复制）。
    baseline_turn_record_id: str | None = None
    #: 最终 Prompt 文本（A：BaselinePromptRecord.text；B：PromptArtifact.prompt）。
    #: Prompt 文本本身是 L3 的测量对象，故直接携带（附 sha256 可校验）。
    prompt_text: str | None = None
    prompt_sha256: str | None = None
    #: System B 的逐次调用随机性记录；Baseline 恒空（同等信息见被引用的 Step 02 记录）。
    llm_calls: list[LLMCallObservation] = Field(default_factory=list)
    image_calls: list[ImageCallObservation] = Field(default_factory=list)
    system_b: SystemBTurnObservation | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    latency_ms: float = Field(ge=0, default=0.0)
    created_at: _UtcDatetime

    @model_validator(mode="after")
    def _status_consistency(self) -> "EvalTurnRecord":
        if self.status == "failed" and self.error is None:
            raise ValueError("a failed turn must carry an error record")
        if self.status != "failed" and self.error is not None:
            raise ValueError("only a failed turn may carry an error record")
        if self.flow_deviation and not self.flow_deviation_reason:
            raise ValueError("flow_deviation requires flow_deviation_reason")
        if self.status == "blocked" and not self.blocked_by_prior_failure:
            raise ValueError("a blocked turn must set blocked_by_prior_failure=True")
        if self.blocked_by_prior_failure and self.status != "blocked":
            raise ValueError("blocked_by_prior_failure is only valid for status='blocked'")
        if self.blocked_by_prior_failure and not self.blocked_by_turn_id:
            raise ValueError("a blocked turn must reference blocked_by_turn_id")
        if self.status == "blocked" and self.error is not None:
            raise ValueError("a blocked turn must not carry its own error (root cause is elsewhere)")
        if self.system == "baseline_a" and self.system_b is not None:
            raise ValueError("baseline turns must not carry a system_b observation")
        return self


class EvalCaseRecord(BaseModel):
    """一个 (案例, 系统, 重复序次) 的聚合记录（逐轮记录是独立 JSONL 行，按 ID 引用）。"""

    model_config = _FROZEN

    schema_version: EvalRecordsSchemaVersion = EVAL_RECORDS_SCHEMA_VERSION
    record_id: str
    run_id: str
    case_id: str
    system: SystemId
    repetition: int = Field(ge=1)
    scenario: str
    turn_type: TurnType
    status: EvalCaseStatus
    #: 仅 System B：L1 四不变量任一失败即 True（摘要据此标 Gate A 阻断）。
    l1_failed: bool | None = None
    turn_record_ids: list[str] = Field(default_factory=list)
    flow_deviation_turns: list[str] = Field(default_factory=list)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    error: EvalFailureRecord | None = None
    created_at: _UtcDatetime

    @model_validator(mode="after")
    def _l1_flag_only_for_system_b(self) -> "EvalCaseRecord":
        if self.system == "baseline_a" and self.l1_failed is not None:
            raise ValueError("l1_failed is only meaningful for system_b (baseline is not_applicable)")
        if self.status != "failed" and self.error is not None:
            raise ValueError("only a failed case may carry an error record")
        return self


class MetricRecord(BaseModel):
    """一个 (案例, 系统, 重复, 指标) 的统一记录。

    - `status`：三态 + `ok`；三态**不从分母剔除**（协议第 5 节）；
    - `score`：比率型主数值（recall / 覆盖率等；计数型指标为 None）；
    - `passed`：过/败型指标（L1 四不变量）的布尔结论；
    - `counts`：分子/分母等原始计数（聚合时**求和**而非再平均，保证可继续聚合）；
    - `details`：轻量证据（逐轮命中/遗漏/违例列表），可追溯到 `evidence_refs`。
    """

    model_config = _FROZEN

    schema_version: EvalRecordsSchemaVersion = EVAL_RECORDS_SCHEMA_VERSION
    record_id: str
    run_id: str
    case_id: str
    system: SystemId
    repetition: int = Field(ge=1)
    turn_type: TurnType
    layer: LayerId
    metric: str
    #: 指标口径版本（R1-B #8：新旧指标分别标识，禁止直接拼成同口径提升）。
    metric_version: str = METRIC_VERSION_V1
    status: MetricStatus
    score: float | None = None
    passed: bool | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    failure: EvalFailureRecord | None = None
    created_at: _UtcDatetime

    @field_validator("score")
    @classmethod
    def _score_in_unit_interval(cls, value: float | None) -> float | None:
        if value is not None and not (0.0 <= value <= 1.0):
            raise ValueError(f"score must be within [0.0, 1.0], got {value!r}")
        return value

    @model_validator(mode="after")
    def _status_consistency(self) -> "MetricRecord":
        if self.status == "ok":
            if self.failure is not None:
                raise ValueError("an ok metric must not carry a failure record")
        elif self.score is not None or self.passed is not None:
            raise ValueError(
                "not_applicable / missing_data / failed metrics must not carry score/passed "
                "(no fabricated zero or perfect scores)"
            )
        if self.status == "failed" and self.failure is None:
            raise ValueError("a failed metric must carry a failure record")
        return self


class EvalProviderSnapshot(BaseModel):
    """Run 级 Provider 配置快照：只记非密信息与环境变量**名**（严禁凭据值）。

    `system_b_response_format` 与 `baseline_response_format` 的结构差异是系统结构
    本身，按协议第 1 节照实登记；`temperature_sent` / `max_tokens_sent` 恒 None
    （两系统都不显式设置采样参数）。
    """

    model_config = _FROZEN

    llm_model: str
    image_model: str
    llm_timeout_seconds: float = Field(gt=0)
    image_timeout_seconds: float = Field(gt=0)
    http_max_retries: int = Field(ge=0)
    image_size: str
    images_per_generation: int = Field(ge=1)
    base_url_env: str
    api_key_env: str
    system_b_response_format: dict[str, Any] | None = None
    baseline_response_format: None = None
    temperature_sent: None = None
    max_tokens_sent: None = None


class EvalRunRecord(BaseModel):
    """Run 级元数据（落盘 `<run_dir>/run.json`；A/B 与全部重复共用）。"""

    model_config = _FROZEN

    schema_version: EvalRecordsSchemaVersion = EVAL_RECORDS_SCHEMA_VERSION
    run_id: str
    harness_version: str
    id_scheme_version: str
    code_version: str
    #: R1-B #10：正式 Run 记录实际 Git commit 与工作区 dirty 状态；诊断 Run 显式标记。
    code_commit: str | None = None
    code_dirty: bool | None = None
    identity_mode: Literal["unspecified", "formal", "diagnostic"] = "unspecified"
    metric_version: str = METRIC_VERSION_V1
    run_nonce: str = ""
    protocol_version: str
    dataset_version: str
    config_version: str
    manifest_version: str
    dataset_sha256: str
    annotations_sha256: str
    config_sha256: str
    protocol_sha256: str
    manifest_sha256: str
    provider: EvalProviderSnapshot
    repetitions: int = Field(ge=1)
    #: 进入 L4 盲评的重复序次（冻结口径：图片每次重复都生成以驱动多轮闭环，
    #: 仅该次重复的图片成对进入 L4；A/B 对称）。
    l4_repetition: int = Field(ge=1)
    systems: list[SystemId] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    case_count: int = Field(ge=0)
    created_at: _UtcDatetime

    @model_validator(mode="after")
    def _case_count_matches_ids(self) -> "EvalRunRecord":
        if self.case_count != len(self.case_ids):
            raise ValueError("case_count must equal len(case_ids)")
        return self


# ---------------------------------------------------------------------------
# 指标函数的统一返回（evaluation.metrics.* 纯函数的输出；Runner 包装为 MetricRecord）
# ---------------------------------------------------------------------------


class MetricPayload(BaseModel):
    """指标纯函数的返回载荷。

    - `status`：建议状态，只取 `ok` / `not_applicable` / `missing_data`
      （`failed` 由 Runner 在执行失败时判定，指标函数不产生）；
    - `score` / `passed` 在非 `ok` 时必须为 None（不伪装零分/满分）；
    - `counts` 为原始计数（分子/分母），聚合时求和。
    """

    model_config = _FROZEN

    status: Literal["ok", "not_applicable", "missing_data"] = "ok"
    score: float | None = None
    passed: bool | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _non_ok_carries_no_score(self) -> "MetricPayload":
        if self.status != "ok" and (self.score is not None or self.passed is not None):
            raise ValueError("non-ok metric payloads must not carry score/passed")
        return self


# ---------------------------------------------------------------------------
# 聚合摘要
# ---------------------------------------------------------------------------


class MetricAggregate(BaseModel):
    """一个 (layer, metric, system, turn_scope) 的聚合。

    `counts_sum` 逐键**求和**（可继续向上聚合：跨 Run 汇总时直接相加）；
    `score_mean` 只在 `status=ok 且 score 非 None` 的记录上取平均。
    """

    model_config = _FROZEN

    layer: LayerId
    metric: str
    system: SystemId
    turn_scope: TurnScope
    #: 指标口径版本（R1-B #8：聚合层同样标识，禁止跨版本直接比较）。
    metric_version: str = METRIC_VERSION_V1
    n_cases: int = Field(ge=0)
    n_ok: int = Field(ge=0)
    n_not_applicable: int = Field(ge=0)
    n_missing_data: int = Field(ge=0)
    n_failed: int = Field(ge=0)
    n_passed: int = Field(ge=0)
    n_failed_checks: int = Field(ge=0)
    score_mean: float | None = None
    counts_sum: dict[str, int] = Field(default_factory=dict)
    #: 追溯到原始记录（每个样本案例一条 MetricRecord ID）。
    metric_record_ids: list[str] = Field(default_factory=list)


class L1BlockingEntry(BaseModel):
    """一条 Gate A 阻断证据（L1 任一不变量失败的 案例×重复 与失败不变量清单）。"""

    model_config = _FROZEN

    case_id: str
    repetition: int = Field(ge=1)
    failed_invariants: list[str] = Field(default_factory=list)
    metric_record_ids: list[str] = Field(default_factory=list)


class RunSummary(BaseModel):
    """Run 级聚合摘要（落盘 `<run_dir>/summary.json`）。

    - 单轮与多轮**分开**保留（`turn_scope = single / multi`），并给出 `all` 合并视图；
    - 每指标给出样本量与三态计数；三态不剔分母；
    - `gate_a_blocked`：任一 System B 案例的任一 L1 不变量失败（或 L1 指标
      无法评估 = `failed`）即 True（协议第 4 节 Layer 1：任一失败直接阻断）。
    """

    model_config = _FROZEN

    schema_version: EvalRecordsSchemaVersion = EVAL_RECORDS_SCHEMA_VERSION
    run_id: str
    case_status_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    metrics: list[MetricAggregate] = Field(default_factory=list)
    gate_a_blocked: bool = False
    l1_blocking: list[L1BlockingEntry] = Field(default_factory=list)
    created_at: _UtcDatetime


class EvalRunResult(BaseModel):
    """`EvaluationRunner.run()` 的内存返回（全部记录同时已落盘）。"""

    model_config = _FROZEN

    run: EvalRunRecord
    cases: list[EvalCaseRecord] = Field(default_factory=list)
    turns: list[EvalTurnRecord] = Field(default_factory=list)
    metrics: list[MetricRecord] = Field(default_factory=list)
    summary: RunSummary


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


def aggregate_run(
    run: EvalRunRecord,
    cases: Iterable[EvalCaseRecord],
    metrics: Iterable[MetricRecord],
    *,
    created_at: datetime,
) -> RunSummary:
    """把逐案例 MetricRecord 聚合为 RunSummary（纯函数，可重复调用/跨 Run 复用）。

    - 按 (layer, metric, system) × (single, multi, all) 分组；`all` 视图由两个
      分组合并而来（样本量为二者之和），保证"单轮/多轮分开且可继续聚合"；
    - 三态计数照常进入 `n_cases` 分母；`score_mean` 只在 ok 且带 score 的记录上计算；
    - `counts_sum` 只在 ok 记录上求和（失败/不适用记录不产生可信计数）。
    """
    case_list = list(cases)
    metric_list = list(metrics)

    status_counts: dict[str, dict[str, int]] = {}
    for case in case_list:
        bucket = status_counts.setdefault(case.system, {})
        bucket[case.status] = bucket.get(case.status, 0) + 1

    groups: dict[tuple[str, str, str, str], list[MetricRecord]] = {}
    for record in metric_list:
        for scope in (record.turn_type, "all"):
            key = (record.layer, record.metric, record.system, scope)
            groups.setdefault(key, []).append(record)

    aggregates: list[MetricAggregate] = []
    for (layer, metric, system, scope) in sorted(groups):
        records = groups[(layer, metric, system, scope)]
        ok_records = [r for r in records if r.status == "ok"]
        scores = [r.score for r in ok_records if r.score is not None]
        counts_sum: dict[str, int] = {}
        for record in ok_records:
            for key_name, value in record.counts.items():
                counts_sum[key_name] = counts_sum.get(key_name, 0) + value
        aggregates.append(
            MetricAggregate(
                layer=layer,  # type: ignore[arg-type]
                metric=metric,
                system=system,  # type: ignore[arg-type]
                turn_scope=scope,  # type: ignore[arg-type]
                metric_version=(
                    records[0].metric_version if records else METRIC_VERSION_V1
                ),
                n_cases=len(records),
                n_ok=len(ok_records),
                n_not_applicable=sum(1 for r in records if r.status == "not_applicable"),
                n_missing_data=sum(1 for r in records if r.status == "missing_data"),
                n_failed=sum(1 for r in records if r.status == "failed"),
                n_passed=sum(1 for r in ok_records if r.passed is True),
                n_failed_checks=sum(1 for r in ok_records if r.passed is False),
                score_mean=(sum(scores) / len(scores)) if scores else None,
                counts_sum=counts_sum,
                metric_record_ids=[r.record_id for r in records],
            )
        )

    blocking: list[L1BlockingEntry] = []
    by_case: dict[tuple[str, int], list[MetricRecord]] = {}
    for record in metric_list:
        if record.system == "system_b" and record.layer == "L1":
            by_case.setdefault((record.case_id, record.repetition), []).append(record)
    for (case_id, repetition), records in sorted(by_case.items()):
        # L1 的 metric 名即不变量名（见 L1_INVARIANTS）。
        failed_invariants = sorted(
            r.metric
            for r in records
            if (r.passed is False and r.status == "ok") or r.status == "failed"
        )
        if failed_invariants:
            blocking.append(
                L1BlockingEntry(
                    case_id=case_id,
                    repetition=repetition,
                    failed_invariants=failed_invariants,
                    metric_record_ids=[r.record_id for r in records],
                )
            )

    return RunSummary(
        run_id=run.run_id,
        case_status_counts=status_counts,
        metrics=aggregates,
        gate_a_blocked=bool(blocking),
        l1_blocking=blocking,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# 落盘（确定性 JSON：sort_keys + 固定缩进；JSONL 逐行一个记录）
# ---------------------------------------------------------------------------


def write_json(path: Path, payload: BaseModel) -> None:
    """确定性 JSON 落盘（与 Step 02 同约定：同输入逐字节一致）。"""
    data = payload.model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


def write_case_jsonl(
    path: Path,
    case_records: Iterable[EvalCaseRecord],
    turn_records: Iterable[EvalTurnRecord],
    metric_records: Iterable[MetricRecord],
) -> None:
    """逐案例 JSONL：每行 `{"record_type": ..., "record": {...}}`，键排序保证确定性。

    行序：case 记录（按 system/repetition）→ turn 记录（按 system/repetition/
    turn_index）→ metric 记录（按 system/repetition/layer/metric）。
    """
    lines: list[str] = []
    ordered_cases = sorted(case_records, key=lambda r: (r.system, r.repetition))
    ordered_turns = sorted(turn_records, key=lambda r: (r.system, r.repetition, r.turn_index))
    ordered_metrics = sorted(
        metric_records, key=lambda r: (r.system, r.repetition, r.layer, r.metric)
    )
    for record_type, records in (
        ("case", ordered_cases),
        ("turn", ordered_turns),
        ("metric", ordered_metrics),
    ):
        for record in records:
            envelope = {
                "record_type": record_type,
                "record": record.model_dump(mode="json"),
            }
            lines.append(json.dumps(envelope, ensure_ascii=False, sort_keys=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_case_jsonl(path: Path) -> list[dict[str, Any]]:
    """读回逐案例 JSONL（聚合/审计用；返回原始 dict 列表）。"""
    entries: list[dict[str, Any]] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line:
            entries.append(json.loads(line))
    return entries


__all__ = [
    "EVAL_RECORDS_SCHEMA_VERSION",
    "EVALUATION_HARNESS_VERSION",
    "EVAL_ID_SCHEME_VERSION",
    "SYSTEM_IDS",
    "L1_INVARIANTS",
    "L1_INVARIANT_UNAUTHORIZED",
    "L1_INVARIANT_CONFIRMATION",
    "L1_INVARIANT_PINNED",
    "L1_INVARIANT_HISTORY",
    "L2_METRICS",
    "L3_METRICS",
    "SystemId",
    "LayerId",
    "EvalCaseStatus",
    "EvalTurnStatus",
    "MetricStatus",
    "TurnType",
    "TurnScope",
    "EvalTurnKind",
    "ExpectedOutcome",
    "ValueMatchSpec",
    "ExpectedDeltaSpec",
    "ExpectedRejectionSpec",
    "MustClarifySpec",
    "ExpectedConflictSpec",
    "ExpectedCarrySpec",
    "TurnAnnotation",
    "ImageEvalDimension",
    "PromptExpectations",
    "CaseAnnotation",
    "load_annotations",
    "EvalFailureRecord",
    "ArtifactRef",
    "LLMCallObservation",
    "ImageCallObservation",
    "ObservedDelta",
    "ObservedIssue",
    "ObservedQuestion",
    "ObservedConfirmation",
    "ObservedGeneration",
    "SystemBTurnObservation",
    "EvalTurnRecord",
    "EvalCaseRecord",
    "MetricRecord",
    "EvalProviderSnapshot",
    "EvalRunRecord",
    "MetricPayload",
    "MetricAggregate",
    "L1BlockingEntry",
    "RunSummary",
    "EvalRunResult",
    "make_eval_record_id",
    "aggregate_run",
    "write_json",
    "write_case_jsonl",
    "read_case_jsonl",
]
