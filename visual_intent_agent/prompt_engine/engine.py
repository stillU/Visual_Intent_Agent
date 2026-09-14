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

from visual_intent_agent.domain import (
    INTENT_PATHS,
    IntentRevision,
    Resolution,
    VisualIntent,
    new_id,
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
class _RealizationPlan:
    """一次编译的委托实现结果（内部结构）。

    - `choices`：delegated_path → `(value, source_kind, realization_id)`；
    - `new_state`：需要在返回前 `append_realization_state` 的新 state（无新选择时为 None）；
    - `refs`：写入 PromptArtifact 的 `realization_refs`。
    """

    choices: Mapping[str, tuple[str, str, str]] = field(default_factory=dict)
    new_state: RealizationState | None = None
    refs: tuple[str, ...] = ()

    def choice(self, path: str) -> tuple[str, str, str] | None:
        return self.choices.get(path)


class PromptEngine:
    """受限 Prompt 编译器（确定性代码；唯一外部输入是 Repository 与 Renderer）。"""

    def __init__(self, renderer: ModelRenderer, repo: Repository) -> None:
        self._renderer = renderer
        self._repo = repo

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
        plan = self._plan_realizations(session_id, intent_revision, prompt_artifact_id)
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
        )

        # 保存前再次验证：确认必须仍然有效且仍绑定当前两类 revision。
        self._require_valid_confirmation(
            session_id,
            confirmation_id,
            self._repo.get_current_session_snapshot(session_id),
        )

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

    def _plan_realizations(
        self,
        session_id: str,
        intent_revision: IntentRevision,
        prompt_artifact_id: str,
    ) -> _RealizationPlan:
        """为全部 delegated 路径决定实现值：优先复用 active Realization，否则确定性新选。

        - 已有 active 值：**原样复用**（不重新选择，不改写 `first_prompt_artifact_id`）；
        - 尚无值的 delegated 路径：用固定候选表 + 稳定摘要规则选择，并组装进一个**新**
          `RealizationState`（新 `rlz` id；carry active 值，历史不覆盖）；
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
            chosen = select_delegated_value(path, intent_revision.intent_revision_id)
            selected[path] = chosen
            carried.append(
                RealizationValue(
                    path=path,
                    value=chosen,
                    source="user_delegated",
                    first_prompt_artifact_id=prompt_artifact_id,
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
        return _RealizationPlan(choices=choices, new_state=new_state, refs=(new_state_id,))

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
]
