"""Step 09 修改 Workflow：`ReviewService.submit_feedback`（新增文件，不改 Step 06 既有文件）。

冻结流程（ARCHITECTURE.md 4「Step 09」；任务书「P3 完整链路」）：

    ReviewService.submit_feedback(session_id, generation_id, text) -> FeedbackOutcome
        # 仅 WAITING_REVIEW 可用
        # 1. 存反馈消息（msg）；加载反馈精确关联的 GenerationArtifact / PromptArtifact /
        #    当前 Intent / 当前 RealizationState
        # 2. FeedbackEngine.analyze → FeedbackResult（落库，refs={"generation_id": ...}）
        # 3. accept  → transition COMPLETED
        #    revise  → validate → reduce → assess → 新 IntentRevision 落库
        #              → evaluate_carry（必要时落新 RealizationState）
        #              → WAITING_CONFIRMATION（等待**重新确认**，绝不跳过）
        #              → 确认后由既有 GenerationPipeline.generate 再生成
        #    clarify → PendingQuestion（qst）落库 → WAITING_CLARIFICATION
        #              （WAITING_REVIEW → UNDERSTANDING → WAITING_CLARIFICATION 两步走）

关键不变量：

- **不跳过重新确认**：revise 只把会话推进到 `WAITING_CONFIRMATION`；正式 Prompt 与图片
  仍必须经 `WorkflowService.confirm_current_intent` + `GenerationPipeline.generate`
  （README 不变量 5/6）。`submit_feedback` **不**调用 Provider、不编译 Prompt、不生成图片；
- **不猜具体设计**：`clarify` 只针对 FeedbackEngine 显式指定的白名单路径提问，
  并复用 Step 06 的 `QuestionBuilder`（问题不带预设值 → 不会把"背景不好"变成"海边"）；
  无法定位目标时不改变状态、不伪造问题；
- **反馈精确关联**：`generation_id` 必须是该会话**当前待复核**的那次生成
  （否则 `workflow.generation_not_found` / `workflow.generation_mismatch`）；
  `FeedbackResult` 落库 refs 严格为 `{"generation_id"}`；
- **历史不可覆盖**：IntentRevision / RealizationState / FeedbackResult 一律只增不改；
  失效产生**新** RealizationState（旧值显式 invalidated），旧确认由 revision 变化天然失效；
- **【Rev.5 · 工单 B】可恢复失败不推进**：引擎层失败（重试耗尽 / 解析失败 / Provider
  失败）、Delta 全被拒、以及**过期反馈**（`analyze` 期间会话已推进到别的 revision /
  状态）一律不产生 revision、不改变状态、不伪造问题；过期检查在**决策分支之前**统一
  生效，因此过期 `accept` 也不会把会话推到 `COMPLETED`、过期 `clarify` 也不会落新问题。
  反馈结果本身仍落库可追溯（过期不阻止日志写入）。用户消息与既有 Intent/PIN 保留，
  客户端据 `FeedbackOutcome.recoverable_failure` + `failure_codes` 显式重试；过期反馈的
  `failure_codes` 含既有 `workflow.stale_revision`。

本模块**不修改** Step 06 既有文件（`questions.py` / `confirmation.py` / `service.py` /
`__init__.py`），因此 `ReviewService` / `FeedbackOutcome` 从
`visual_intent_agent.workflow.review` 导入（不经 `workflow/__init__.py` 再导出）。

依赖边界（遵守 Step 06 冻结的 `tests/workflow/test_workflow_public_api.py`）：
`workflow/` 包内文件不得 import `prompt_engine` / `generation` / 图像 adapter。因此
本模块**不**直接 import `GenerationArtifact` / `PromptArtifact`，而是把 Repository 信封里
的 payload 解析为 JSON 字典，再用 `FeedbackRequest.model_validate({...})` 让 pydantic
把嵌套字典校验成正确的合同对象（对象契约仍由 Step 07/08 持有，本模块不复制其字段面）。
`os` / `httpx` / `sqlite3` 同样不 import（持久化只经 `Repository`）。
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from visual_intent_agent.domain import (
    INTENT_PATHS,
    ExecutionRevision,
    IntentRevision,
    Issue,
    Severity,
    VisualIntent,
    new_id,
    utc_now,
)
from visual_intent_agent.feedback import (
    FEEDBACK_CLARIFICATION_REQUIRED,
    FeedbackDecision,
    FeedbackEngine,
    FeedbackRequest,
    FeedbackResult,
    clarify_target_path,
)
from visual_intent_agent.persistence import (
    Repository,
    RepositoryError,
    SessionSnapshot,
    WorkflowState,
)
from visual_intent_agent.policy import (
    DECISION_POLICIES,
    IntentResolution,
    QuestionSpec,
    assess,
)
from visual_intent_agent.realization.carry import (
    CarryEvaluation,
    build_carry_state,
    evaluate_carry,
)
from visual_intent_agent.realization.models import RealizationState
from visual_intent_agent.validation import EvidenceContext, reduce, validate

from .confirmation import (
    WORKFLOW_INVALID_STATE,
    WORKFLOW_STALE_REVISION,
    ConfirmationSummary,
    WorkflowError,
    build_confirmation_summary,
)
from .models import (
    engine_failure_codes,
    turn_failure_codes,
)
from .questions import PendingQuestion, QuestionBuilder

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: 消息 / IntentRevision / 问题 ID 前缀（ARCHITECTURE.md 5.1 冻结前缀表）。
MESSAGE_ID_PREFIX = "msg"
INTENT_REVISION_ID_PREFIX = "irev"
QUESTION_ID_PREFIX = "qst"

# ---------------------------------------------------------------------------
# 本步新增 code（命名空间 `workflow.*`；ARCHITECTURE.md 5.5：新增 code 必须写入 handoff）
# ---------------------------------------------------------------------------

#: `generation_id` 缺失 / 该会话不存在该 GenerationArtifact（或其 PromptArtifact 缺失）。
REVIEW_GENERATION_NOT_FOUND = "workflow.generation_not_found"
#: `generation_id` 属于另一个会话，或不是本会话当前待复核的那次生成。
REVIEW_GENERATION_MISMATCH = "workflow.generation_mismatch"

REVIEW_ERROR_CODES: frozenset[str] = frozenset(
    {REVIEW_GENERATION_NOT_FOUND, REVIEW_GENERATION_MISMATCH}
)


class ReviewError(Exception):
    """修改 Workflow 的硬门禁失败（带 `.code`，本步新增两个 `workflow.*` code）。

    与 Step 06 的 `WorkflowError` 分开：`WorkflowError.__init__` 只接受其冻结的三个
    code（不改既有文件），因此本步新增的 code 由本类承载；"状态不允许"等既有语义
    仍复用 `WorkflowError(WORKFLOW_INVALID_STATE, ...)`。
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in REVIEW_ERROR_CODES:
            raise ValueError(f"unknown review error code: {code!r}")
        self.code = code
        super().__init__(f"[{code}] {message}")


class FeedbackOutcome(BaseModel):
    """`submit_feedback` 的返回形状（Step 09 冻结，写入 handoff）。

    - `snapshot`：处理完成后的会话快照（accept → `COMPLETED`；revise →
      `WAITING_CONFIRMATION`（或 `WAITING_CLARIFICATION`）；clarify →
      `WAITING_CLARIFICATION`；可恢复失败 → 原状态 `WAITING_REVIEW`）；
    - `feedback`：反馈解释结果（已落库；`generation_id` / `intent_revision_id` 可追溯）；
    - `resolution`：revise 路径的 `IntentResolution`（含 applied deltas 与全部 issues）；
    - `pending_question`：clarify 路径（或 revise 后不可确认）落地的问题；
    - `confirmation_summary`：进入 `WAITING_CONFIRMATION` 时的确认摘要（客户端展示并把
      `compute_summary_hash(...)` 交给 `WorkflowService.confirm_current_intent`）；
    - `carry`：revise 路径的 Realization 继承/失效评估；
    - `realization_state_id`：本轮是否落了新的 RealizationState（无失效时为 None）；
    - `recoverable_failure`：可恢复失败（解析失败 / Provider 失败 / Delta 全部被拒 /
      过期请求）——状态未改变、无新 revision、无新 Artifact；
    - `failure_codes`（Rev.5 向后兼容新增，默认空元组）：失败原因 code（ERROR 级 issue，
      按出现顺序去重）。过期请求含既有 `workflow.stale_revision`。
    """

    model_config = _FROZEN

    session_id: str
    message_id: str
    generation_id: str
    feedback: FeedbackResult
    snapshot: SessionSnapshot
    resolution: IntentResolution | None = None
    pending_question: PendingQuestion | None = None
    confirmation_summary: ConfirmationSummary | None = None
    carry: CarryEvaluation | None = None
    realization_state_id: str | None = None
    recoverable_failure: bool = False
    failure_codes: tuple[str, ...] = ()


class ReviewService:
    """P3 修改闭环用例（唯一入口；确定性代码，唯一 LLM 调用经 FeedbackEngine）。

    构造依赖（最小化设计，写入 handoff）：

    - `repo`：唯一持久化面（读取 Artifact / 写入 message、revision、feedback、
      realization、问题与状态迁移）；
    - `feedback_engine`：反馈 → Candidate Delta 的唯一 LLM 边界；
    - `question_builder`：`QuestionSpec` → `PendingQuestion`（clarify 与 revise 后不可确认时复用）。

    不注入 `IntentEngine`（反馈已是结构化 Delta，无需再理解自然语言）、不注入
    `GenerationPipeline`（重新确认是外部硬门禁：`submit_feedback` 只推进到
    `WAITING_CONFIRMATION`，生成由调用方在确认后调用 `GenerationPipeline.generate`）。
    """

    def __init__(
        self,
        repo: Repository,
        feedback_engine: FeedbackEngine,
        question_builder: QuestionBuilder,
    ) -> None:
        self._repo = repo
        self._feedback_engine = feedback_engine
        self._question_builder = question_builder

    # -- 用例 --------------------------------------------------------------

    def submit_feedback(
        self, session_id: str, generation_id: str, text: str
    ) -> FeedbackOutcome:
        """处理一条针对指定 `GenerationArtifact` 的反馈。"""
        if not isinstance(text, str):
            raise TypeError("text must be a str; the workflow never guesses user input")

        snapshot = self._repo.get_current_session_snapshot(session_id)
        if snapshot.workflow_state is not WorkflowState.WAITING_REVIEW:
            raise WorkflowError(
                WORKFLOW_INVALID_STATE,
                f"session {session_id!r} is in {snapshot.workflow_state.value}; feedback "
                "is only accepted in WAITING_REVIEW",
            )

        generation_payload, prompt_artifact_payload = self._load_artifacts(
            session_id, generation_id
        )
        current_intent = self._current_intent(snapshot)
        realization_state = self._load_realization_state(session_id)

        message_id = new_id(MESSAGE_ID_PREFIX)
        self._repo.append_message(session_id, message_id, "user", text, utc_now())
        # 本轮请求的"应有会话头"：入口快照 + 本条新增消息（与 service.py 同口径）。
        expected = self._repo.get_current_session_snapshot(session_id)

        result = self._feedback_engine.analyze(
            _request(
                session_id=session_id,
                message_id=message_id,
                text=text,
                generation_payload=generation_payload,
                prompt_artifact_payload=prompt_artifact_payload,
                current_intent=current_intent,
                realization_state=realization_state,
            )
        )
        self._repo.append_feedback_result(
            result.feedback_id,
            session_id,
            {"generation_id": result.generation_id},
            result.model_dump_json(),
        )

        # 【Rev.5 · 工单 B】统一过期检查（**在一切决策分支之前**）：analyze 期间会话被
        # 别的轮次推进（状态 / intent+execution revision / pending question / 消息集）
        # → accept / clarify / revise 一律不推进新状态，返回显式 stale 可恢复失败。
        # 反馈结果已在上方落库（保留可追溯），过期不阻止日志写入。
        stale = self._stale_snapshot(session_id, expected)
        if stale is not None:
            return self._stale_outcome(
                session_id=session_id,
                message_id=message_id,
                result=result,
                current_intent=current_intent,
                stale=stale,
            )

        # 【Rev.5 · 工单 B】引擎层可恢复失败（重试已经用尽 / 解析失败）：本轮结果落库
        # 可追溯，但**绝不**应用其候选、不改变状态、不伪造问题；用户消息与既有
        # Intent/PIN 全部保留，客户端据 `recoverable_failure` 显式重试。
        if engine_failure_codes(result.issues):
            return self._outcome(
                session_id=session_id,
                message_id=message_id,
                result=result,
                recoverable_failure=True,
                failure_codes=turn_failure_codes(result.issues),
            )

        if result.decision is FeedbackDecision.ACCEPT:
            return self._accept(session_id, message_id, result)

        if result.decision is FeedbackDecision.CLARIFY:
            return self._clarify(session_id, message_id, result)

        return self._revise(
            session_id=session_id,
            message_id=message_id,
            result=result,
            snapshot=snapshot,
            current_intent=current_intent,
            realization_state=realization_state,
        )

    # -- 分支：accept -------------------------------------------------------

    def _accept(
        self, session_id: str, message_id: str, result: FeedbackResult
    ) -> FeedbackOutcome:
        """用户明确接受 → `COMPLETED`（`WAITING_REVIEW → COMPLETED` 为冻结迁移）。"""
        self._repo.transition_state(session_id, WorkflowState.COMPLETED)
        return self._outcome(
            session_id=session_id,
            message_id=message_id,
            result=result,
        )

    # -- 分支：clarify ------------------------------------------------------

    def _clarify(
        self,
        session_id: str,
        message_id: str,
        result: FeedbackResult,
    ) -> FeedbackOutcome:
        """模糊反馈 → 针对显式白名单路径提问；无法定位时不改变状态。"""
        target_path = clarify_target_path(result)
        if target_path is None:
            # 解析失败 / Provider 失败 / 无法定位目标：不猜问题、不改状态。
            return self._outcome(
                session_id=session_id,
                message_id=message_id,
                result=result,
                recoverable_failure=True,
                failure_codes=turn_failure_codes(result.issues),
            )

        # WAITING_REVIEW → UNDERSTANDING → WAITING_CLARIFICATION（既有迁移两步走）。
        self._repo.transition_state(session_id, WorkflowState.UNDERSTANDING)
        pending = self._question_builder.build(
            _clarification_spec(target_path, result), session_id
        )
        self._repo.save_pending_question(
            session_id, pending.question_id, pending.model_dump_json()
        )
        self._repo.transition_state(session_id, WorkflowState.WAITING_CLARIFICATION)
        return self._outcome(
            session_id=session_id,
            message_id=message_id,
            result=result,
        )

    # -- 分支：revise -------------------------------------------------------

    def _revise(
        self,
        *,
        session_id: str,
        message_id: str,
        result: FeedbackResult,
        snapshot: SessionSnapshot,
        current_intent: VisualIntent,
        realization_state: RealizationState | None,
    ) -> FeedbackOutcome:
        """明确修改 → 过 Validator/Reducer/Policy → 新 revision → carry → 重新确认。"""
        validation = validate(
            result.candidate_deltas,
            _evidence_context(self._repo, session_id, snapshot),
            current_intent,
        )
        if not validation.accepted:
            # 没有任何 Delta 通过验证：不产生 revision、不改变状态、不猜测修复。
            failure_resolution = _failure_resolution(
                current_intent,
                validation.issues,
                _current_execution_revision(self._repo, snapshot),
            )
            return self._outcome(
                session_id=session_id,
                message_id=message_id,
                result=result,
                resolution=failure_resolution,
                recoverable_failure=True,
                failure_codes=turn_failure_codes(failure_resolution.issues),
            )

        reduced = reduce(current_intent, validation.accepted)
        # 【Step 06 · P3】评估同样携带会话当前 ExecutionRevision（证据
        # FC-P3-execution-context-never-passed：此前 assess 恒用 None）。
        policy_resolution = assess(
            reduced.intent, _current_execution_revision(self._repo, snapshot)
        )
        resolution = policy_resolution.model_copy(
            update={
                "applied_deltas": list(validation.accepted),
                "issues": [*validation.issues, *policy_resolution.issues],
            }
        )

        revision = IntentRevision(
            intent_revision_id=new_id(INTENT_REVISION_ID_PREFIX),
            session_id=session_id,
            parent_revision_id=snapshot.current_intent_revision_id,
            intent=reduced.intent,
            applied_deltas=list(validation.accepted),
            created_at=utc_now(),
        )
        self._repo.append_intent_revision(revision)  # 旧 Confirmation 由此天然失效

        carry = evaluate_carry(realization_state, reduced.change_summary)
        realization_state_id: str | None = None
        if carry.has_invalidations() and realization_state is not None:
            new_state = build_carry_state(
                carry,
                realization_state,
                session_id=session_id,
                based_on_intent_revision_id=revision.intent_revision_id,
            )
            self._repo.append_realization_state(
                new_state.realization_id,
                session_id,
                {"based_on_intent_revision_id": revision.intent_revision_id},
                new_state.model_dump_json(),
            )
            realization_state_id = new_state.realization_id

        self._repo.transition_state(session_id, WorkflowState.UNDERSTANDING)
        if resolution.ready_for_confirmation:
            self._repo.clear_pending_question(session_id)
            self._repo.transition_state(session_id, WorkflowState.WAITING_CONFIRMATION)
        elif resolution.question is not None:
            pending = self._question_builder.build(resolution.question, session_id)
            self._repo.save_pending_question(
                session_id, pending.question_id, pending.model_dump_json()
            )
            self._repo.transition_state(session_id, WorkflowState.WAITING_CLARIFICATION)
        # 否则：assess 在 not ready 时必然给出 question（Step 03 冻结）；防御性地留在
        # UNDERSTANDING，等待下一条消息，不伪造确认。

        return self._outcome(
            session_id=session_id,
            message_id=message_id,
            result=result,
            resolution=resolution,
            carry=carry,
            realization_state_id=realization_state_id,
        )

    # -- 读取（全部经 Repository；禁止直读 SQLite）--------------------------

    def _load_artifacts(
        self, session_id: str, generation_id: str
    ) -> tuple[dict, dict]:
        """加载**当前待复核**的 GenerationArtifact 与其 PromptArtifact 的 payload 字典。

        返回 `(generation_payload, prompt_artifact_payload)`：本模块不 import Step 07/08
        的模型（依赖边界见模块 docstring），由 `FeedbackRequest.model_validate` 把 payload
        校验成正确的合同对象。
        """
        if not isinstance(generation_id, str) or not generation_id.strip():
            raise ReviewError(
                REVIEW_GENERATION_NOT_FOUND,
                "submit_feedback requires the generation_id of the generation under review",
            )
        try:
            stored = self._repo.get_generation_artifact(generation_id)
        except RepositoryError as exc:
            raise ReviewError(
                REVIEW_GENERATION_NOT_FOUND,
                f"no GenerationArtifact exists for generation_id={generation_id!r} "
                f"({exc.code})",
            ) from exc
        if stored.session_id != session_id:
            raise ReviewError(
                REVIEW_GENERATION_MISMATCH,
                f"generation {generation_id!r} belongs to another session",
            )
        latest = self._repo.list_generation_artifacts(session_id)
        if not latest or latest[-1].artifact_id != generation_id:
            raise ReviewError(
                REVIEW_GENERATION_MISMATCH,
                f"generation {generation_id!r} is not the generation currently under "
                "review for this session; feedback must bind the latest generation",
            )
        generation_payload = json.loads(stored.payload)
        prompt_artifact_id = stored.refs.get("prompt_artifact_id") or generation_payload.get(
            "prompt_artifact_id"
        )
        if not prompt_artifact_id:
            raise ReviewError(
                REVIEW_GENERATION_NOT_FOUND,
                f"generation {generation_id!r} carries no prompt_artifact_id reference",
            )
        try:
            prompt_stored = self._repo.get_prompt_artifact(str(prompt_artifact_id))
        except RepositoryError as exc:
            raise ReviewError(
                REVIEW_GENERATION_NOT_FOUND,
                f"generation {generation_id!r} references a missing PromptArtifact "
                f"{prompt_artifact_id!r} ({exc.code})",
            ) from exc
        if prompt_stored.session_id != session_id:
            raise ReviewError(
                REVIEW_GENERATION_MISMATCH,
                f"prompt artifact {prompt_artifact_id!r} belongs to another session",
            )
        return generation_payload, json.loads(prompt_stored.payload)

    def _current_intent(self, snapshot: SessionSnapshot) -> VisualIntent:
        """当前 Draft Intent（反馈针对的 Intent 快照；无 revision 视为硬门禁失败）。"""
        if snapshot.current_intent_revision_id is None:
            raise WorkflowError(
                WORKFLOW_INVALID_STATE,
                f"session {snapshot.session_id!r} has no current intent revision to review",
            )
        return self._repo.get_intent_revision(snapshot.current_intent_revision_id).intent

    def _load_realization_state(self, session_id: str) -> RealizationState | None:
        """读取会话当前 RealizationState（无则 None；payload 反序列化归本步）。"""
        stored = self._repo.get_current_realization_state(session_id)
        if stored is None:
            return None
        return RealizationState.model_validate_json(stored.payload)

    # -- 结果组装 ----------------------------------------------------------

    def _stale_snapshot(
        self, session_id: str, expected: SessionSnapshot
    ) -> SessionSnapshot | None:
        """本轮会话头被推进时返回当前快照，否则 None（统一过期检查，Rev.5 · 工单 B）。

        与 `service.py::_request_is_current` 同口径：状态必须仍是本轮入口的
        `WAITING_REVIEW`，且 intent/execution revision、pending question、消息集均未变化
        （`expected` = 入口快照 + 本条反馈消息）。任一变化 → accept / clarify / revise
        一律不推进新状态。
        """
        current = self._repo.get_current_session_snapshot(session_id)
        if (
            current.workflow_state is not WorkflowState.WAITING_REVIEW
            or current.current_intent_revision_id
            != expected.current_intent_revision_id
            or current.current_execution_revision_id
            != expected.current_execution_revision_id
            or current.pending_question_id != expected.pending_question_id
            or current.message_ids != expected.message_ids
        ):
            return current
        return None

    def _stale_outcome(
        self,
        *,
        session_id: str,
        message_id: str,
        result: FeedbackResult,
        current_intent: VisualIntent,
        stale: SessionSnapshot,
    ) -> FeedbackOutcome:
        """过期反馈的显式可恢复失败（反馈日志已落库；状态与 Intent 均不推进）。"""
        stale_issue = Issue(
            code=WORKFLOW_STALE_REVISION,
            message=(
                "the session advanced while this feedback was being interpreted; "
                "this turn was discarded and no delta was applied to the new revision"
            ),
            severity=Severity.ERROR,
        )
        return self._outcome(
            session_id=session_id,
            message_id=message_id,
            result=result,
            resolution=_failure_resolution(
                current_intent,
                [stale_issue],
                _current_execution_revision(self._repo, stale),
            ),
            recoverable_failure=True,
            failure_codes=turn_failure_codes([stale_issue]),
        )

    def _outcome(
        self,
        *,
        session_id: str,
        message_id: str,
        result: FeedbackResult,
        resolution: IntentResolution | None = None,
        carry: CarryEvaluation | None = None,
        realization_state_id: str | None = None,
        recoverable_failure: bool = False,
        failure_codes: tuple[str, ...] = (),
    ) -> FeedbackOutcome:
        snapshot = self._repo.get_current_session_snapshot(session_id)
        summary = (
            self._build_summary_for(snapshot)
            if snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
            else None
        )
        return FeedbackOutcome(
            session_id=session_id,
            message_id=message_id,
            generation_id=result.generation_id,
            feedback=result,
            snapshot=snapshot,
            resolution=resolution,
            pending_question=_decode_pending_question(snapshot),
            confirmation_summary=summary,
            carry=carry,
            realization_state_id=realization_state_id,
            recoverable_failure=recoverable_failure,
            failure_codes=tuple(failure_codes),
        )

    def _build_summary_for(self, snapshot: SessionSnapshot) -> ConfirmationSummary:
        if snapshot.current_intent_revision_id is None:
            raise WorkflowError(
                WORKFLOW_INVALID_STATE,
                f"session {snapshot.session_id!r} has no current intent revision to summarize",
            )
        if snapshot.current_execution_revision_id is None:
            raise WorkflowError(
                WORKFLOW_INVALID_STATE,
                f"session {snapshot.session_id!r} has no current execution revision to summarize",
            )
        return build_confirmation_summary(
            self._repo.get_intent_revision(snapshot.current_intent_revision_id),
            self._repo.get_execution_revision(snapshot.current_execution_revision_id),
        )


# ---------------------------------------------------------------------------
# 模块级纯函数
# ---------------------------------------------------------------------------


def _request(
    *,
    session_id: str,
    message_id: str,
    text: str,
    generation_payload: dict,
    prompt_artifact_payload: dict,
    current_intent: VisualIntent,
    realization_state: RealizationState | None,
) -> FeedbackRequest:
    """构造 FeedbackEngine 的请求（对象全部只读）。

    Generation / Prompt 用它们**已落库的 payload 字典**传入，由 pydantic 校验成 Step 07/08
    的合同对象；本模块因此不需要 import `prompt_engine` / `generation`（依赖边界）。
    """
    return FeedbackRequest.model_validate(
        {
            "session_id": session_id,
            "message_id": message_id,
            "feedback_text": text,
            "generation": generation_payload,
            "prompt_artifact": prompt_artifact_payload,
            "current_intent": current_intent,
            "realization_state": realization_state,
        }
    )


def _evidence_context(
    repo: Repository, session_id: str, snapshot: SessionSnapshot
) -> EvidenceContext:
    """反馈证据边界：本轮消息已落库；反馈不是 Pending Question 的回答。"""
    current = repo.get_current_session_snapshot(session_id)
    return EvidenceContext(
        available_message_ids=frozenset(current.message_ids),
        pending_question_id=snapshot.pending_question_id,
        pending_question_path=_pending_question_path(snapshot),
    )


def _pending_question_path(snapshot: SessionSnapshot) -> str | None:
    """Pending Question 的目标路径（无问题则为 None；payload 反序列化归 Step 06）。"""
    if snapshot.pending_question_id is None or snapshot.pending_question_payload is None:
        return None
    return PendingQuestion.model_validate_json(
        snapshot.pending_question_payload
    ).target_path


def _current_execution_revision(
    repo: Repository, snapshot: SessionSnapshot
) -> ExecutionRevision | None:
    """当前 ExecutionRevision（Step 06 · P3）。

    与 `service._current_execution_revision` 同口径的受控重复（不抽共享 helper）；
    指针缺失时返回 None，绝不伪造输出尺寸。
    """
    if snapshot.current_execution_revision_id is None:
        return None
    return repo.get_execution_revision(snapshot.current_execution_revision_id)


def _failure_resolution(
    current_intent: VisualIntent,
    validation_issues: list[Issue],
    execution_context: ExecutionRevision | None = None,
) -> IntentResolution:
    """全部 Delta 被拒时的可观察 resolution（intent 原样、无 Delta、不可确认）。"""
    base = assess(current_intent, execution_context)
    return base.model_copy(
        update={
            "applied_deltas": [],
            "issues": [*validation_issues, *base.issues],
            "ready_for_confirmation": False,
        }
    )


def _clarification_spec(target_path: str, result: FeedbackResult) -> QuestionSpec:
    """由 clarify 结果构造 `QuestionSpec`（目标路径来自 issue；不猜预设值）。

    `suggested_values` 故意为空：Step 03 的建议选项表不是公开面，本步不复制第二份表，
    也因此**不会**把"背景不好"猜成任何具体设计；用户可自由输入或委托（策略允许时）。
    """
    policy = _owner_policy(target_path)
    reason = next(
        (
            issue.message
            for issue in result.issues
            if issue.code == FEEDBACK_CLARIFICATION_REQUIRED and issue.path == target_path
        ),
        None,
    )
    return QuestionSpec(
        target_path=target_path,
        reason=reason or f"the feedback does not specify a concrete expectation for {target_path!r}",
        allow_delegate=bool(policy is not None and policy.delegatable),
        allow_custom=True,
        suggested_values=(),
    )


def _owner_policy(target_path: str):
    """路径所属 Decision（成员路径包括 `dependencies`）；未命中返回 None。"""
    if target_path not in INTENT_PATHS:
        return None
    for policy in DECISION_POLICIES:
        if target_path == policy.path or target_path in policy.dependencies:
            return policy
    return None


def _decode_pending_question(snapshot: SessionSnapshot) -> PendingQuestion | None:
    """从会话 payload 反序列化待答问题（payload 形态由 Step 06 冻结为 JSON）。"""
    if snapshot.pending_question_id is None or snapshot.pending_question_payload is None:
        return None
    return PendingQuestion.model_validate_json(snapshot.pending_question_payload)


__all__ = [
    "ReviewService",
    "FeedbackOutcome",
    "ReviewError",
    "REVIEW_GENERATION_NOT_FOUND",
    "REVIEW_GENERATION_MISMATCH",
    "REVIEW_ERROR_CODES",
    "MESSAGE_ID_PREFIX",
    "INTENT_REVISION_ID_PREFIX",
    "QUESTION_ID_PREFIX",
]
