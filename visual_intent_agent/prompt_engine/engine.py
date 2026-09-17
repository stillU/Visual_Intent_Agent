"""Step 07 PromptEngine：从已确认 Intent / ExecutionContext / Realization 编译 PromptArtifact。

冻结流程（ARCHITECTURE.md 4「Step 07」；任务书「实现步骤」1～8）：

    1. `repo.is_confirmation_valid` **复核**（无效 → `prompt.no_valid_confirmation`）；
    2. 读当前 Intent / Execution revision 与 `get_current_realization_state`；
    3. 逐 clause 生成 SourceBinding（每个重要视觉 clause 必须可回溯）；
    4. delegated 路径局部实现，且**优先复用** active Realization（不重新选择）；
    5. unauthorized addition 检查（任何重要 clause 无合法 binding → 编译失败）；
    6. 新 Realization 落库（`append_realization_state`，refs 必填键
       `based_on_intent_revision_id`）；
    7. 保存前**再次**验证 Confirmation 有效性；
    8. `append_prompt_artifact`（refs 必填键 `intent_revision_id` + `confirmation_id`）→ 返回。

授权边界（任务书）：

- **可以**：改变措辞、调整目标模型语法、组织 token、实现明确 delegated 的决策、
  复用未失效的 Realization；
- **不可以**：新增主体 / 显著颜色 / 服装 / 场景 / 视觉风格，重新解释用户偏好——
  除非对应 path 已明确 `user_delegated`。

关键语义（验收口径）：

- **重要视觉 clause 来源覆盖率 100%**：Prompt 文本由 clause 文本拼装而成，每个 clause
  都必须绑定 `intent_path` / `rule_id` / `realization_id`；已解决的每个重要路径都必须
  有 clause（不允许静默丢弃已确认要求）。
- **unspecified ≠ delegated**：既无值、又无 `user_delegated` 记录的路径（含
  `not_applicable` 与 policy `omit`）**不进入 Prompt、不产生 binding、不产生 Realization**。

确定性：本模块无 LLM、无随机数、无网络；delegated 选择是固定候选表 + 稳定摘要选择规则。

本模块**不**要求会话处于 `WAITING_CONFIRMATION`：Step 08 `GenerationPipeline.generate`
在迁移到 `GENERATING` **之后**才调用 `compile`，因此编译门禁只看
`is_confirmation_valid`（确认仍绑定当前两类 revision），不看工作流状态。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Mapping

from pydantic import ValidationError

from visual_intent_agent.domain import (
    INTENT_PATHS,
    ExecutionRevision,
    IntentRevision,
    Resolution,
    VisualIntent,
    new_id,
)
from visual_intent_agent.knowledge import (
    ELIGIBILITY_SNAPSHOT_VERSION,
    KNOWLEDGE_PATHS,
    AdoptionOutcome,
    AdoptionRejectionReason,
    KnowledgeAdoptionDecision,
    KnowledgeBundle,
    KnowledgeEngine,
    KnowledgeError,
    KnowledgeRecommendation,
    RetrievalRequest,
    RetrievalOutcome,
    ReviewStatus,
    compute_content_hash,
    evaluate_conditions,
    target_model_applies,
)
from visual_intent_agent.persistence import Repository, SessionSnapshot
from visual_intent_agent.realization.models import (
    REALIZATION_STATUS_ACTIVE,
    RealizationState,
    RealizationValue,
)

from .models import (
    PROMPT_MISSING_SOURCE_BINDING,
    PROMPT_NO_VALID_CONFIRMATION,
    PROMPT_UNAUTHORIZED_ADDITION,
    PROMPT_UNSUPPORTED_REQUIREMENT,
    SOURCE_KIND_DELEGATION,
    SOURCE_KIND_INTENT,
    SOURCE_KIND_REALIZATION,
    SOURCE_KIND_RUNTIME,
    SOURCE_KINDS,
    PromptArtifact,
    PromptCompilationError,
    PromptCompileRequest,
    PromptParameters,
    SourceBinding,
)
from .renderer import ModelRenderer
from .spec import CompilationClause, CompilationSpec

# ---------------------------------------------------------------------------
# clause 顺序、措辞与确定性委托候选表
# ---------------------------------------------------------------------------

#: clause 渲染顺序（Prompt 组织方式属 implementation 自由度，但必须确定）。
#: 这是**顺序**声明，不是第二份路径白名单：import 时断言其集合恰等于 `INTENT_PATHS`。
CLAUSE_ORDER: tuple[str, ...] = (
    "subject.description",
    "subject.count",
    "subject.pose_action",
    "composition.framing",
    "environment.mode",
    "environment.location",
    "lighting.character",
    "style.primary",
    "style.description",
    "camera.angle",
    "camera.depth_of_field",
    "color.palette",
)

if set(CLAUSE_ORDER) != INTENT_PATHS or len(CLAUSE_ORDER) != len(INTENT_PATHS):
    raise ValueError("CLAUSE_ORDER must be exactly the frozen INTENT_PATHS whitelist")

#: 各路径的 clause 标签（措辞自由度；只影响文本，不影响来源绑定）。
RENDER_LABELS: Mapping[str, str] = {
    "subject.description": "subject",
    "subject.count": "subject count",
    "subject.pose_action": "pose",
    "composition.framing": "framing",
    "environment.mode": "environment",
    "environment.location": "location",
    "lighting.character": "lighting",
    "style.primary": "style",
    "style.description": "style detail",
    "camera.angle": "camera angle",
    "camera.depth_of_field": "depth of field",
    "color.palette": "color palette",
}

#: 每个**可委托**路径的固定候选表（确定性代码，不是 LLM，也不是"最佳实践"知识库）。
#: 只覆盖 `policy.DECISION_POLICIES` 中 `delegatable=True` 的 9 个决策成员路径；
#: 委托路径没有条目时抛 `prompt.unsupported_requirement`（绝不由模型临时编造）。
DELEGATED_CANDIDATES: Mapping[str, tuple[str, ...]] = {
    "style.primary": ("cinematic", "photorealistic", "illustration"),
    "environment.mode": ("studio", "indoor", "outdoor"),
    "environment.location": (
        "a minimalist studio backdrop",
        "a cozy interior",
        "a quiet outdoor setting",
    ),
    "composition.framing": ("close_up", "medium_shot", "wide_shot"),
    "subject.pose_action": ("standing", "sitting", "walking"),
    "lighting.character": ("soft", "dramatic", "natural"),
    "camera.angle": ("eye_level", "low_angle", "high_angle"),
    "color.palette": ("warm", "cool", "monochrome"),
    "camera.depth_of_field": ("shallow", "deep"),
}

#: ID 前缀（ARCHITECTURE.md 5.1）：PromptArtifact = `pra`，RealizationState = `rlz`。
PROMPT_ARTIFACT_ID_PREFIX = "pra"
REALIZATION_ID_PREFIX = "rlz"


def select_delegated_value(path: str, seed: str) -> str:
    """为一个 delegated 路径做**确定性**局部选择。

    规则：`index = int(sha256(f"{path}\\x1f{seed}").digest()[:8]) % len(candidates)`。
    纯确定性、无随机数、无时钟、无环境依赖；`seed` 取当前 `intent_revision_id`，因此
    同一 revision 的重复调用结果逐字相同。已有 active Realization 时本函数**不会被调用**
    （复用优先，见 `PromptEngine._plan_realizations`）。
    """
    candidates = DELEGATED_CANDIDATES.get(path)
    if not candidates:
        raise PromptCompilationError(
            PROMPT_UNSUPPORTED_REQUIREMENT,
            f"no deterministic candidate table exists for delegated path {path!r}; "
            "the engine never invents a delegated implementation",
        )
    digest = hashlib.sha256(f"{path}\x1f{seed}".encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(candidates)
    return candidates[index]


def render_clause_text(path: str, value: object) -> str:
    """确定性 clause 文本：`"<label>: <value>"`（措辞自由度，仅影响表达）。"""
    return f"{RENDER_LABELS[path]}: {value}"


# ---------------------------------------------------------------------------
# 路径读取 / 已解决重要路径
# ---------------------------------------------------------------------------


def read_intent_path(intent: VisualIntent, path: str) -> object | None:
    """按白名单路径只读读取 Intent 当前值（不改状态，不猜测）。"""
    facet_name, _, field_name = path.partition(".")
    facet = getattr(intent, facet_name, None)
    if facet is None:
        return None
    return getattr(facet, field_name, None)


def _resolution_of(intent: VisualIntent, path: str) -> Resolution | None:
    record = intent.resolutions.get(path)
    return None if record is None else record.resolution


def resolved_important_paths(intent: VisualIntent) -> list[str]:
    """已解决的重要视觉路径（按 `CLAUSE_ORDER`，确定性）。

    已解决 = 该路径**有值**（user_specified / confirmed proposal）或**被显式委托**
    （user_delegated 且无值）。`not_applicable` 与完全缺失不属于已解决，也绝不会被
    具体化（unspecified ≠ delegated）。
    """
    resolved: list[str] = []
    for path in CLAUSE_ORDER:
        if read_intent_path(intent, path) is not None:
            resolved.append(path)
        elif _resolution_of(intent, path) is Resolution.USER_DELEGATED:
            resolved.append(path)
    return resolved


# ---------------------------------------------------------------------------
# Source binding 与 unauthorized addition 检查
# ---------------------------------------------------------------------------


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def bind_clause(clause: CompilationClause) -> SourceBinding:
    """把一个 clause 转成 `SourceBinding`；无合法来源时抛 `PromptCompilationError`。

    - 完全没有任何来源字段 / `source_kind` 非法 → `prompt.unauthorized_addition`
      （无来源的重大视觉内容，编译失败而不是继续生成）；
    - 声明了来源类别但缺少该类别强制字段（或字段形态不符）→
      `prompt.missing_source_binding`。
    """
    kind = clause.source_kind
    if kind is None or kind not in SOURCE_KINDS:
        raise PromptCompilationError(
            PROMPT_UNAUTHORIZED_ADDITION,
            f"clause {clause.clause_id!r} ({clause.text!r}) has no legal source: "
            f"source_kind={kind!r}, intent_path={clause.intent_path!r}, "
            f"rule_id={clause.rule_id!r}, realization_id={clause.realization_id!r}; "
            "unauthorized additions fail the compilation instead of being rendered",
        )

    if kind == SOURCE_KIND_RUNTIME:
        if _blank(clause.rule_id):
            raise PromptCompilationError(
                PROMPT_MISSING_SOURCE_BINDING,
                f"runtime clause {clause.clause_id!r} requires a non-empty rule_id",
            )
        if clause.intent_path is not None and clause.intent_path not in INTENT_PATHS:
            raise PromptCompilationError(
                PROMPT_MISSING_SOURCE_BINDING,
                f"runtime clause {clause.clause_id!r} references non-whitelisted "
                f"intent_path {clause.intent_path!r}",
            )
        if clause.realization_id is not None:
            raise PromptCompilationError(
                PROMPT_MISSING_SOURCE_BINDING,
                f"runtime clause {clause.clause_id!r} must not carry a realization_id",
            )
        return SourceBinding(
            clause_id=clause.clause_id,
            text=clause.text,
            source_kind=SOURCE_KIND_RUNTIME,
            intent_path=clause.intent_path,
            rule_id=clause.rule_id,
        )

    if clause.intent_path is None or clause.intent_path not in INTENT_PATHS:
        raise PromptCompilationError(
            PROMPT_MISSING_SOURCE_BINDING,
            f"clause {clause.clause_id!r} declares source_kind={kind!r} but its "
            f"intent_path {clause.intent_path!r} is not a whitelisted intent path",
        )
    if kind == SOURCE_KIND_INTENT:
        if _blank(clause.rule_id) is False or clause.realization_id is not None:
            raise PromptCompilationError(
                PROMPT_MISSING_SOURCE_BINDING,
                f"intent clause {clause.clause_id!r} must carry only intent_path "
                "(no rule_id / realization_id)",
            )
        return SourceBinding(
            clause_id=clause.clause_id,
            text=clause.text,
            source_kind=SOURCE_KIND_INTENT,
            intent_path=clause.intent_path,
        )

    # delegation / realization：必须引用一个 RealizationState。
    if _blank(clause.realization_id):
        raise PromptCompilationError(
            PROMPT_MISSING_SOURCE_BINDING,
            f"clause {clause.clause_id!r} declares source_kind={kind!r} but carries no "
            "realization_id; delegated implementations must be persisted before rendering",
        )
    if _blank(clause.rule_id) is False:
        raise PromptCompilationError(
            PROMPT_MISSING_SOURCE_BINDING,
            f"clause {clause.clause_id!r} of kind {kind!r} must not carry a rule_id",
        )
    return SourceBinding(
        clause_id=clause.clause_id,
        text=clause.text,
        source_kind=kind,
        intent_path=clause.intent_path,
        realization_id=clause.realization_id,
    )


def check_source_bindings(spec: CompilationSpec) -> list[SourceBinding]:
    """逐 clause 生成 SourceBinding；任一无合法来源即抛 `PromptCompilationError`。"""
    return [bind_clause(clause) for clause in spec.clauses]


def check_clause_coverage(intent: VisualIntent, bindings: list[SourceBinding]) -> None:
    """已解决的每个重要路径都必须有绑定 clause（不允许静默丢弃已确认要求）。"""
    covered = {binding.intent_path for binding in bindings if binding.intent_path is not None}
    missing = [path for path in resolved_important_paths(intent) if path not in covered]
    if missing:
        raise PromptCompilationError(
            PROMPT_MISSING_SOURCE_BINDING,
            f"resolved important path(s) {missing} produced no bound clause; a confirmed "
            "requirement must never be silently dropped",
        )


# ---------------------------------------------------------------------------
# 委托实现计划
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _KnowledgeSelection:
    """本次编译采纳的一条知识建议（只用于给新 RealizationValue 写三项追溯字段）。

    `value` 是候选值本身；`content` 绝不进入 clause、绝不改变授权 `source`。
    """

    value: str
    bundle_id: str
    unit_id: str
    unit_version: str


@dataclass(frozen=True)
class _RealizationPlan:
    """一次编译的委托实现结果（内部结构）。

    - `choices`：delegated_path → `(value, source_kind, realization_id)`；
    - `new_state`：需要在返回前 `append_realization_state` 的新 state（无新选择时为 None）；
    - `refs`：写入 PromptArtifact 的 `realization_refs`；
    - `knowledge_bundle`：本次检索产生、必须先落库的编译级 Bundle（未检索时为 None）；
    - `knowledge_bundle_refs`：写入 PromptArtifact 的 `knowledge_bundle_refs`（含本次
      诊断 Bundle 与复用 active Realization 实际使用的历史 Bundle，去重稳定）。
    """

    choices: Mapping[str, tuple[str, str, str]] = field(default_factory=dict)
    new_state: RealizationState | None = None
    refs: tuple[str, ...] = ()
    knowledge_bundle: KnowledgeBundle | None = None
    knowledge_bundle_refs: tuple[str, ...] = ()

    def choice(self, path: str) -> tuple[str, str, str] | None:
        return self.choices.get(path)


@dataclass(frozen=True)
class RecommendationVerdict:
    """消费端对单条检索推荐的裁定（编译期内存结构，随后写入 Bundle 持久化）。"""

    path: str
    knowledge_id: str
    version: str
    candidate_value: str
    accepted: bool
    reason_code: AdoptionRejectionReason | None = None
    reason: str = ""

    def to_decision(self) -> KnowledgeAdoptionDecision:
        """转成可持久化的不可变裁定记录（adopted / rejected + 封闭原因码）。"""
        return KnowledgeAdoptionDecision(
            path=self.path,
            knowledge_id=self.knowledge_id,
            version=self.version,
            candidate_value=self.candidate_value,
            outcome=AdoptionOutcome.ADOPTED if self.accepted else AdoptionOutcome.REJECTED,
            reason_code=self.reason_code,
            reason=self.reason,
        )


def _reject(
    recommendation: KnowledgeRecommendation,
    reason_code: AdoptionRejectionReason,
    reason: str,
) -> RecommendationVerdict:
    return RecommendationVerdict(
        path=recommendation.path,
        knowledge_id=recommendation.knowledge_id,
        version=recommendation.version,
        candidate_value=recommendation.candidate_value,
        accepted=False,
        reason_code=reason_code,
        reason=reason,
    )


def evaluate_recommendation(
    *,
    bundle: KnowledgeBundle,
    recommendation: KnowledgeRecommendation,
    path: str,
    intent: VisualIntent,
    target_model: str,
) -> RecommendationVerdict:
    """消费端二次校验：**绝不只相信检索接口已经过滤**（F2 纵深防御）。

    只读取 Bundle 快照与 PromptEngine 自己读到的**已确认 Intent**（`request.intent`），
    不用 Bundle 的查询文本替代事实；复用 `evaluate_conditions` / `target_model_applies`
    两个唯一实现，不复制第二套条件或模型解释器。任何一步失败都返回带封闭原因码的
    `rejected` 裁定，调用方据此回退现有固定候选表。
    """
    if read_intent_path(intent, path) is not None:
        return _reject(
            recommendation,
            AdoptionRejectionReason.PATH_ALREADY_RESOLVED,
            "the path already has a confirmed value; knowledge never overrides it",
        )
    if _resolution_of(intent, path) is not Resolution.USER_DELEGATED:
        return _reject(
            recommendation,
            AdoptionRejectionReason.PATH_NOT_DELEGATED,
            "the path is not explicitly user_delegated in the confirmed intent",
        )
    if path in intent.pinned_paths:
        return _reject(
            recommendation,
            AdoptionRejectionReason.PATH_PINNED,
            "the path is pinned; knowledge never overrides a PIN",
        )
    if recommendation.path != path:
        return _reject(
            recommendation,
            AdoptionRejectionReason.SNAPSHOT_MISMATCH,
            "the recommendation path does not match the path being decided",
        )
    if recommendation.candidate_value not in DELEGATED_CANDIDATES.get(path, ()):
        return _reject(
            recommendation,
            AdoptionRejectionReason.CANDIDATE_NOT_AUTHORIZED,
            "the recommended candidate is outside the single authoritative candidate table",
        )
    query = next((item for item in bundle.queries if item.path == path), None)
    if query is None or query.target_model != target_model:
        return _reject(
            recommendation,
            AdoptionRejectionReason.QUERY_MISSING_OR_MODEL_MISMATCH,
            "the bundle records no query for this path bound to the compile's target model",
        )
    result = bundle.result_for(path)
    if result is None or result.outcome is not RetrievalOutcome.ADOPTED or not result.hits:
        return _reject(
            recommendation,
            AdoptionRejectionReason.NO_ADOPTED_RESULT,
            "the bundle has no adopted path result with retained hits for this path",
        )
    leader = result.hits[0]
    if (
        leader.applicable_path != path
        or leader.knowledge_id != recommendation.knowledge_id
        or leader.version != recommendation.version
        or leader.content_hash != recommendation.content_hash
        or leader.candidate_value != recommendation.candidate_value
        or leader.score != recommendation.score
    ):
        return _reject(
            recommendation,
            AdoptionRejectionReason.SNAPSHOT_MISMATCH,
            "the recommendation does not mirror the retained Top-1 unit snapshot",
        )
    # 实值复核：对快照正文重新计算 content_hash，而不是只检查哈希字符串形状；
    # 同一条错误哈希同时填进推荐与命中项也无法过关。
    if compute_content_hash(leader.content) != leader.content_hash:
        return _reject(
            recommendation,
            AdoptionRejectionReason.CONTENT_HASH_MISMATCH,
            "the retained unit content does not hash to its recorded content_hash; "
            "the snapshot is not a faithful copy",
        )
    snapshot = leader.eligibility_snapshot
    if snapshot is None:
        return _reject(
            recommendation,
            AdoptionRejectionReason.MISSING_ELIGIBILITY_SNAPSHOT,
            "the retained unit carries no eligibility snapshot (old or unverified record); "
            "missing evidence is never treated as unrestricted applicability",
        )
    if snapshot.snapshot_version != ELIGIBILITY_SNAPSHOT_VERSION:
        return _reject(
            recommendation,
            AdoptionRejectionReason.SNAPSHOT_VERSION_UNSUPPORTED,
            f"eligibility snapshot version {snapshot.snapshot_version!r} is not supported "
            f"by this compiler (expected {ELIGIBILITY_SNAPSHOT_VERSION!r})",
        )
    if snapshot.review_status is not ReviewStatus.APPROVED:
        rejected_status = getattr(snapshot.review_status, "value", snapshot.review_status)
        return _reject(
            recommendation,
            AdoptionRejectionReason.REVIEW_NOT_APPROVED,
            f"the unit review status is {rejected_status!r}; only approved "
            "knowledge may be adopted",
        )
    if snapshot.reviewer is None or snapshot.reviewed_at is None:
        return _reject(
            recommendation,
            AdoptionRejectionReason.REVIEW_FIELDS_INVALID,
            "the approved snapshot carries no reviewer/reviewed_at evidence",
        )
    if not target_model_applies(snapshot.target_models, target_model):
        return _reject(
            recommendation,
            AdoptionRejectionReason.MODEL_NOT_APPLICABLE,
            f"the unit target_models {snapshot.target_models!r} are neither the compile's "
            f"target model {target_model!r} nor the generic marker",
        )
    if not evaluate_conditions(snapshot.conditions, intent):
        return _reject(
            recommendation,
            AdoptionRejectionReason.CONDITIONS_NOT_SATISFIED,
            "the unit conditions are not satisfied by this compile's confirmed intent "
            "(missing fields are not satisfied)",
        )
    return RecommendationVerdict(
        path=path,
        knowledge_id=recommendation.knowledge_id,
        version=recommendation.version,
        candidate_value=recommendation.candidate_value,
        accepted=True,
        reason_code=None,
        reason="the complete eligibility snapshot is satisfied by the confirmed intent",
    )


def _historical_knowledge_refs(current: RealizationState | None) -> tuple[str, ...]:
    """复用 active Realization 实际引用的历史 Bundle id（按 state 顺序去重稳定）。

    只取 `status == "active"` 的值：失效值不会被复用，其 Bundle 也不进本次追溯链；
    这正是"复用来源链不断链又不冒充本次检索"的边界。
    """
    refs: list[str] = []
    seen: set[str] = set()
    if current is None:
        return ()
    for value in current.values:
        if value.status != REALIZATION_STATUS_ACTIVE:
            continue
        bundle_id = value.knowledge_bundle_id
        if bundle_id is not None and bundle_id not in seen:
            seen.add(bundle_id)
            refs.append(bundle_id)
    return tuple(refs)


class PromptEngine:
    """受限 Prompt 编译器（确定性代码；唯一外部输入是 Repository、Renderer 与可选知识引擎）。

    `knowledge_engine` 是 keyword-only 可选依赖（默认 None）：None 表示**关闭 RAG**，
    不检索、不生成 Bundle，行为与接入前逐字等价。传入时只在"本版三条知识路径仍是
    user_delegated、无值、未 PIN、无 active Realization"时**一次**检索，且只采纳通过
    消费端二次校验的候选值；知识正文绝不进入 clause，也不新增授权来源类别。
    """

    def __init__(
        self,
        renderer: ModelRenderer,
        repo: Repository,
        *,
        knowledge_engine: KnowledgeEngine | None = None,
    ) -> None:
        self._renderer = renderer
        self._repo = repo
        self._knowledge_engine = knowledge_engine

    # -- 公开用例 ----------------------------------------------------------

    def compile(self, request: PromptCompileRequest) -> PromptArtifact:
        """编译并保存一份 PromptArtifact（严格按冻结流程）。"""
        if not isinstance(request, PromptCompileRequest):
            raise TypeError("compile expects a PromptCompileRequest")
        session_id = request.session_id
        confirmation_id = request.confirmation_id

        snapshot = self._repo.get_current_session_snapshot(session_id)
        self._require_valid_confirmation(session_id, confirmation_id, snapshot)
        record = self._repo.get_confirmation(confirmation_id)
        intent_revision = self._repo.get_intent_revision(record.intent_revision_id)
        execution_revision = self._repo.get_execution_revision(record.execution_revision_id)
        if intent_revision.session_id != session_id or execution_revision.session_id != session_id:
            raise PromptCompilationError(
                PROMPT_NO_VALID_CONFIRMATION,
                "the confirmation resolves to revisions of another session",
            )

        parameters = PromptParameters(size=execution_revision.output_size)
        target_model = execution_revision.target_model
        if not self._renderer.supports(target_model, parameters.size):
            raise PromptCompilationError(
                PROMPT_UNSUPPORTED_REQUIREMENT,
                f"the single supported target model/renderer does not support "
                f"target_model={target_model!r} with size={parameters.size!r}",
            )

        prompt_artifact_id = new_id(PROMPT_ARTIFACT_ID_PREFIX)
        plan = self._plan_realizations(
            session_id=session_id,
            intent_revision=intent_revision,
            execution_revision=execution_revision,
            confirmation_id=confirmation_id,
            prompt_artifact_id=prompt_artifact_id,
        )
        spec = self._build_compilation_spec(
            session_id=session_id,
            target_model=target_model,
            parameters=parameters,
            intent_revision=intent_revision,
            plan=plan,
        )
        # unauthorized addition 检查：任何重要 clause 无合法 binding → 编译失败。
        bindings = check_source_bindings(spec)
        check_clause_coverage(intent_revision.intent, bindings)

        prompt = self._renderer.render(spec)
        if not prompt.strip():
            raise PromptCompilationError(
                PROMPT_UNSUPPORTED_REQUIREMENT,
                "the confirmed intent produced no renderable important clause",
            )

        artifact = PromptArtifact(
            prompt_artifact_id=prompt_artifact_id,
            session_id=session_id,
            based_on_intent_revision_id=intent_revision.intent_revision_id,
            based_on_confirmation_id=confirmation_id,
            target_model=target_model,
            prompt=prompt,
            parameters=parameters,
            source_bindings=bindings,
            realization_refs=list(plan.refs),
            knowledge_bundle_refs=list(plan.knowledge_bundle_refs),
        )

        # 保存前再次验证：确认必须仍然有效且仍绑定当前两类 revision。
        self._require_valid_confirmation(
            session_id,
            confirmation_id,
            self._repo.get_current_session_snapshot(session_id),
        )

        # 持久化顺序：Bundle（不可变快照）→ Realization → Prompt。
        # 任一环节硬失败都不产生"指向未落库 Bundle 的已保存 Prompt"。
        if plan.knowledge_bundle is not None:
            self._append_knowledge_bundle(plan.knowledge_bundle)
        if plan.new_state is not None:
            self._repo.append_realization_state(
                plan.new_state.realization_id,
                session_id,
                {"based_on_intent_revision_id": intent_revision.intent_revision_id},
                plan.new_state.model_dump_json(),
            )
        self._repo.append_prompt_artifact(
            prompt_artifact_id,
            session_id,
            {
                "intent_revision_id": intent_revision.intent_revision_id,
                "confirmation_id": confirmation_id,
            },
            artifact.model_dump_json(),
        )
        return artifact

    # -- 内部实现 ----------------------------------------------------------

    def _require_valid_confirmation(
        self, session_id: str, confirmation_id: str, snapshot: SessionSnapshot
    ) -> None:
        """Hard Confirmation Gate 复核（无有效确认绝不编译）。"""
        if not isinstance(confirmation_id, str) or not confirmation_id.strip():
            raise PromptCompilationError(
                PROMPT_NO_VALID_CONFIRMATION,
                "a non-empty confirmation_id is required to compile a prompt",
            )
        if not self._repo.is_confirmation_valid(confirmation_id):
            raise PromptCompilationError(
                PROMPT_NO_VALID_CONFIRMATION,
                f"confirmation {confirmation_id!r} is missing, invalidated, or does not bind "
                "the current intent/execution revision; ask the user to confirm again",
            )
        record = self._repo.get_confirmation(confirmation_id)
        if record.session_id != session_id:
            raise PromptCompilationError(
                PROMPT_NO_VALID_CONFIRMATION,
                f"confirmation {confirmation_id!r} belongs to another session",
            )
        if (
            snapshot.current_intent_revision_id != record.intent_revision_id
            or snapshot.current_execution_revision_id != record.execution_revision_id
        ):
            raise PromptCompilationError(
                PROMPT_NO_VALID_CONFIRMATION,
                "the confirmation no longer binds the current intent/execution revision",
            )

    def _load_current_realization(self, session_id: str) -> RealizationState | None:
        """读取会话当前 RealizationState（无则 None；payload 反序列化归本步）。"""
        stored = self._repo.get_current_realization_state(session_id)
        if stored is None:
            return None
        return RealizationState.model_validate_json(stored.payload)

    def _append_knowledge_bundle(self, bundle: KnowledgeBundle) -> None:
        """把本次检索的不可变 Bundle 先落库（refs 精确绑定三类 revision/确认）。"""
        self._repo.append_knowledge_bundle(
            bundle.bundle_id,
            bundle.session_id,
            {
                "intent_revision_id": bundle.intent_revision_id,
                "execution_revision_id": bundle.execution_revision_id,
                "confirmation_id": bundle.confirmation_id,
            },
            bundle.model_dump_json(),
        )

    def _plan_realizations(
        self,
        *,
        session_id: str,
        intent_revision: IntentRevision,
        execution_revision: ExecutionRevision,
        confirmation_id: str,
        prompt_artifact_id: str,
    ) -> _RealizationPlan:
        """为全部 delegated 路径决定实现值：优先复用 active Realization，否则确定性新选。

        - 已有 active 值：**原样复用**（不重新选择、不重新检索、不改写
          `first_prompt_artifact_id` 与知识追溯字段）；
        - 尚无值的 delegated 路径：本版三条知识路径且仍 `user_delegated`、未 PIN 时，
          一次性构建 `RetrievalRequest` 调用知识引擎并做消费端二次校验；通过则采用知识
          候选值（附三项追溯字段），否则回退现有 `select_delegated_value`；非知识路径
          原逻辑不变；
        - 没有任何新选择时不写新 state（纯复用不产生冗余历史）。
        """
        intent = intent_revision.intent
        current = self._load_current_realization(session_id)

        active_by_path: dict[str, RealizationValue] = {}
        if current is not None:
            for value in current.values:
                if value.status == REALIZATION_STATUS_ACTIVE and value.path not in active_by_path:
                    active_by_path[value.path] = value

        delegated_paths = [
            path
            for path in CLAUSE_ORDER
            if read_intent_path(intent, path) is None
            and _resolution_of(intent, path) is Resolution.USER_DELEGATED
        ]
        missing = [path for path in delegated_paths if path not in active_by_path]
        historical_refs = _historical_knowledge_refs(current)

        if not missing:
            existing_id = current.realization_id if current is not None else None
            choices = {
                path: (active_by_path[path].value, SOURCE_KIND_REALIZATION, existing_id)
                for path in delegated_paths
            }
            return _RealizationPlan(
                choices=choices,
                new_state=None,
                refs=(existing_id,) if existing_id is not None else (),
                knowledge_bundle_refs=historical_refs,
            )

        # 只有"本版三条 + user_delegated + 无值 + 未 PIN + 无 active Realization"的
        # 路径才可能被知识建议改选；一次检索覆盖全部符合条件的路径。
        pending_knowledge = [
            path
            for path in missing
            if path in KNOWLEDGE_PATHS and path not in intent.pinned_paths
        ]
        knowledge_bundle: KnowledgeBundle | None = None
        selections: dict[str, _KnowledgeSelection] = {}
        if self._knowledge_engine is not None and pending_knowledge:
            knowledge_bundle, selections = self._retrieve_knowledge(
                session_id=session_id,
                intent_revision=intent_revision,
                execution_revision=execution_revision,
                confirmation_id=confirmation_id,
                pending_paths=tuple(pending_knowledge),
            )

        new_state_id = new_id(REALIZATION_ID_PREFIX)
        carried: list[RealizationValue] = []
        seen_paths: set[str] = set()
        if current is not None:
            for value in current.values:
                if value.status != REALIZATION_STATUS_ACTIVE or value.path in seen_paths:
                    continue
                seen_paths.add(value.path)
                carried.append(value)
        selected: dict[str, str] = {}
        for path in missing:
            selection = selections.get(path)
            chosen = (
                selection.value
                if selection is not None
                else select_delegated_value(path, intent_revision.intent_revision_id)
            )
            selected[path] = chosen
            carried.append(
                RealizationValue(
                    path=path,
                    value=chosen,
                    source="user_delegated",
                    first_prompt_artifact_id=prompt_artifact_id,
                    knowledge_bundle_id=None if selection is None else selection.bundle_id,
                    knowledge_unit_id=None if selection is None else selection.unit_id,
                    knowledge_unit_version=(
                        None if selection is None else selection.unit_version
                    ),
                )
            )
        new_state = RealizationState(
            realization_id=new_state_id,
            session_id=session_id,
            based_on_intent_revision_id=intent_revision.intent_revision_id,
            values=carried,
        )
        choices = {
            path: (
                active_by_path[path].value if path in active_by_path else selected[path],
                SOURCE_KIND_REALIZATION if path in active_by_path else SOURCE_KIND_DELEGATION,
                new_state_id,
            )
            for path in delegated_paths
        }
        refs: list[str] = []
        if knowledge_bundle is not None:
            refs.append(knowledge_bundle.bundle_id)
        refs.extend(historical_refs)
        return _RealizationPlan(
            choices=choices,
            new_state=new_state,
            refs=(new_state_id,),
            knowledge_bundle=knowledge_bundle,
            knowledge_bundle_refs=tuple(dict.fromkeys(refs)),
        )

    # -- 知识检索与消费端二次校验 ------------------------------------------

    def _retrieve_knowledge(
        self,
        *,
        session_id: str,
        intent_revision: IntentRevision,
        execution_revision: ExecutionRevision,
        confirmation_id: str,
        pending_paths: tuple[str, ...],
    ) -> tuple[KnowledgeBundle | None, dict[str, _KnowledgeSelection]]:
        """构建唯一一条 `RetrievalRequest`、调用知识引擎并二次校验其结果。

        - 身份绑定与 compile 完全一致（session/intent/execution/confirmation + 目标模型）；
        - 只捕获明确的 `KnowledgeError`（知识读取/校验失败）→ 按"无命中"回退，不生成
          Bundle；编程错误照常抛出；
        - 检索返回后立即复核确认：检索期间失效必须硬失败，绝不伪装无命中（返回的
          Bundle 也不落库，避免留下引用失效确认的审计记录）。
        """
        request = RetrievalRequest(
            session_id=session_id,
            intent_revision_id=intent_revision.intent_revision_id,
            execution_revision_id=execution_revision.execution_revision_id,
            confirmation_id=confirmation_id,
            intent=intent_revision.intent,
            target_model=execution_revision.target_model,
            pending_paths=pending_paths,
        )
        try:
            bundle = self._knowledge_engine.retrieve(request)
        except KnowledgeError:
            return None, {}
        self._require_valid_confirmation(
            session_id,
            confirmation_id,
            self._repo.get_current_session_snapshot(session_id),
        )
        selections, final_bundle = self._consume_knowledge_bundle(bundle, request)
        return final_bundle, selections

    def _consume_knowledge_bundle(
        self, bundle: object, request: RetrievalRequest
    ) -> tuple[dict[str, _KnowledgeSelection], KnowledgeBundle]:
        """消费端二次校验：绝不只相信检索接口已过滤。

        硬失败（伪造/跨会话/过期/越界 Bundle 一律拒绝，不伪装无命中）：
        - 返回值不是 `KnowledgeBundle`（伪造对象）；
        - session/intent/execution/confirmation ID 或目标模型与请求不一致；
        - 出现不在本次 pending 集合内的 query / path_result / recommendation（越界）。

        软回退（拒绝该条建议，逐路径回退固定候选表）：候选值已不在唯一权威候选表、
        path 不再是 user_delegated / 已有值 / 已 PIN、缺失/不受支持的适用性快照、
        审核或模型不适用、条件不满足、content_hash 与正文不符等。每条推荐都被记录为
        `KnowledgeAdoptionDecision`（adopted/rejected + 封闭原因码），并写入返回的
        **新** Bundle（原检索器对象不被原地修改）；该 Bundle 仍作为诊断/审计记录落库。
        """
        if not isinstance(bundle, KnowledgeBundle):
            raise PromptCompilationError(
                PROMPT_UNAUTHORIZED_ADDITION,
                "the knowledge engine returned an object that is not a KnowledgeBundle; "
                "refusing to consume unverifiable provenance",
            )
        if (
            bundle.session_id != request.session_id
            or bundle.intent_revision_id != request.intent_revision_id
            or bundle.execution_revision_id != request.execution_revision_id
            or bundle.confirmation_id != request.confirmation_id
            or bundle.target_model != request.target_model
        ):
            raise PromptCompilationError(
                PROMPT_UNAUTHORIZED_ADDITION,
                "the knowledge bundle identity does not match this compile "
                "(cross-session, stale, or forged bundle); refusing to consume it",
            )
        pending = set(request.pending_paths)
        out_of_bounds = sorted(
            {query.path for query in bundle.queries} - pending
            | {result.path for result in bundle.path_results} - pending
            | {rec.path for rec in bundle.recommendations} - pending
        )
        if out_of_bounds:
            raise PromptCompilationError(
                PROMPT_UNAUTHORIZED_ADDITION,
                f"the knowledge bundle reports paths outside this compile's pending set: "
                f"{out_of_bounds}; refusing to consume it",
            )

        selections: dict[str, _KnowledgeSelection] = {}
        decisions: list[KnowledgeAdoptionDecision] = []
        for path in request.pending_paths:
            recommendation = bundle.recommendation_for(path)
            if recommendation is None:
                continue
            verdict = evaluate_recommendation(
                bundle=bundle,
                recommendation=recommendation,
                path=path,
                intent=request.intent,
                target_model=request.target_model,
            )
            decisions.append(verdict.to_decision())
            if not verdict.accepted:
                continue
            selections[path] = _KnowledgeSelection(
                value=recommendation.candidate_value,
                bundle_id=bundle.bundle_id,
                unit_id=recommendation.knowledge_id,
                unit_version=recommendation.version,
            )
        # 返回**新** Bundle（追加消费端裁定），原检索器对象保持不变；调用方落库该对象。
        # 若引擎返回的 Bundle 无法重新通过合同校验（被 model_construct 之类绕过的伪对象），
        # 宁可硬失败，也绝不落库一份 schema 非法的审计记录。
        try:
            final_bundle = bundle.with_adoption_decisions(tuple(decisions))
        except ValidationError as exc:
            raise PromptCompilationError(
                PROMPT_UNAUTHORIZED_ADDITION,
                "the knowledge engine returned a bundle that fails contract re-validation; "
                "refusing to consume or persist it",
            ) from exc
        return selections, final_bundle

    def _build_compilation_spec(
        self,
        *,
        session_id: str,
        target_model: str,
        parameters: PromptParameters,
        intent_revision: IntentRevision,
        plan: _RealizationPlan,
    ) -> CompilationSpec:
        """把已解决的 Intent 路径逐条转换成 clause（内部轻量结构，不是 Prompt AST）。

        - 有值路径 → `intent` clause；
        - `user_delegated` 路径 → `delegation`（新选定）或 `realization`（复用）clause；
        - 缺失 / `not_applicable` / policy `omit` → **不产生 clause**（保持不具体化）。
        """
        intent = intent_revision.intent
        clauses: list[CompilationClause] = []
        for position, path in enumerate(CLAUSE_ORDER):
            clause_id = f"clause_{position:02d}"
            value = read_intent_path(intent, path)
            if value is not None:
                clauses.append(
                    CompilationClause(
                        clause_id=clause_id,
                        text=render_clause_text(path, value),
                        intent_path=path,
                        source_kind=SOURCE_KIND_INTENT,
                    )
                )
                continue
            if _resolution_of(intent, path) is Resolution.USER_DELEGATED:
                choice = plan.choice(path)
                if choice is None:
                    raise PromptCompilationError(
                        PROMPT_UNSUPPORTED_REQUIREMENT,
                        f"delegated path {path!r} has no planned implementation",
                    )
                chosen, kind, realization_id = choice
                clauses.append(
                    CompilationClause(
                        clause_id=clause_id,
                        text=render_clause_text(path, chosen),
                        intent_path=path,
                        source_kind=kind,
                        realization_id=realization_id,
                    )
                )
            # 其余情况（缺失 / not_applicable / 其它无值 resolution）一律不具体化。
        return CompilationSpec(
            session_id=session_id,
            target_model=target_model,
            parameters=parameters,
            clauses=clauses,
        )


__all__ = [
    "PromptEngine",
    "CLAUSE_ORDER",
    "RENDER_LABELS",
    "DELEGATED_CANDIDATES",
    "PROMPT_ARTIFACT_ID_PREFIX",
    "REALIZATION_ID_PREFIX",
    "select_delegated_value",
    "render_clause_text",
    "read_intent_path",
    "resolved_important_paths",
    "bind_clause",
    "check_source_bindings",
    "check_clause_coverage",
    "RecommendationVerdict",
    "evaluate_recommendation",
]
