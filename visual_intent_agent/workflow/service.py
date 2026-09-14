"""Step 06 应用层用例：`WorkflowService`（ARCHITECTURE.md 4「Step 06 — workflow」）。

    create_session()            -> SessionSnapshot
    submit_message(sid, text)   -> SubmitMessageOutcome
    get_session(sid)            -> SessionSnapshot
    confirm_current_intent(sid, intent_revision_id, execution_revision_id, summary_hash)
                                -> ConfirmationRecord

`submit_message` 冻结流程（任务书「Workflow 编排 / 用户消息」）：

    1. 保存 message（role=user，ID 前缀 `msg`）；
    2. 状态进入 UNDERSTANDING（同状态为幂等无操作；确认页消息走 Rev.1 第 11 条迁移）；
    3. 调用 IntentEngine.resolve（Pending Question 从会话 payload 反序列化并 to_spec()）；
    4. 有新 delta 则组装 IntentRevision（parent=当前）、落库；无 delta 不产生新 revision；
       【Rev.3 增补】落新 revision 后接入 Realization carry（依据
       `docs/handoffs/architecture_decision_003.md` 工单 D）：读取当前 RealizationState →
       由既有 `derive_change_summary(applied_deltas)` 派生 ChangeSummary →
       `evaluate_carry(state, change_summary)`；**仅当** state 存在且本轮确有失效时以
       `build_carry_state` 落**新** RealizationState（refs 精确
       `{"based_on_intent_revision_id": 新 revision}`）。无 state 或无失效 → 零写入、
       零异常；carry 不进返回形状（`SubmitMessageOutcome` 与公开面零变化）；
    5. 有 question：QuestionBuilder 构造 PendingQuestion（ID 前缀 `qst`）落库 +
       WAITING_CLARIFICATION；
    6. ready_for_confirmation：清空 Pending Question + WAITING_CONFIRMATION。

`confirm_current_intent` 是 **Hard Confirmation Gate** 的唯一入口（任务书「用户确认」）：

    1. 仅 WAITING_CONFIRMATION（否则 workflow.invalid_state）；
    2. 请求的 intent/execution revision 必须等于当前快照（否则 workflow.stale_revision）；
    3. 按当前快照重算 `compute_summary_hash` 并与请求比对（否则
       workflow.summary_hash_mismatch）；
    4. 成功则保存 ConfirmationRecord（ID 前缀 `cnf`）。

本步到此为止：**不生成 Prompt、不调用图像 Provider、不迁移到 GENERATING**。确认后
状态保持 WAITING_CONFIRMATION，`Repository.is_confirmation_valid` 即"允许进入生成"
的唯一判定（Step 07/08 消费）。

可恢复失败的"最多修复一次"（设计书：不合法输出最多修复一次；Step 05 把该决策交给
Step 06）：`IntentEngine.resolve` 返回的 issue 命中可重试类别时，用同一请求再解析一次
（总计最多 2 次调用），仍失败则按可恢复 issue 返回、不猜测修复、不静默吞。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from visual_intent_agent.config import Settings
from visual_intent_agent.domain import (
    ExecutionRevision,
    IntentRevision,
    VisualIntent,
    new_id,
    utc_now,
)
from visual_intent_agent.intent_engine import IntentEngine, IntentResolveRequest
from visual_intent_agent.intent_engine.models import INTERPRETER_UNPARSEABLE_OUTPUT
from visual_intent_agent.persistence import (
    ConfirmationRecord,
    Repository,
    SessionSnapshot,
    WorkflowState,
)
from visual_intent_agent.policy import IntentResolution
from visual_intent_agent.providers.errors import RETRYABLE_CODES
from visual_intent_agent.realization.carry import build_carry_state, evaluate_carry
from visual_intent_agent.realization.models import RealizationState

from .confirmation import (
    WORKFLOW_INVALID_STATE,
    WORKFLOW_STALE_REVISION,
    WORKFLOW_SUMMARY_HASH_MISMATCH,
    ConfirmationSummary,
    WorkflowError,
    build_confirmation_summary,
    compute_summary_hash,
    derive_change_summary,
)
from .questions import PendingQuestion, QuestionBuilder

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: 首条 ExecutionRevision 的默认输出尺寸（比例由此派生；Step 06 不新增执行设置用例）。
DEFAULT_OUTPUT_SIZE = "1024x1024"

#: 会话 / 消息 / revision / 确认 ID 前缀（ARCHITECTURE.md 5.1 冻结前缀表）。
SESSION_ID_PREFIX = "ses"
MESSAGE_ID_PREFIX = "msg"
INTENT_REVISION_ID_PREFIX = "irev"
EXECUTION_REVISION_ID_PREFIX = "erev"
CONFIRMATION_ID_PREFIX = "cnf"

#: 一次 resolve 的最多调用次数：初次 + 最多一次修复（设计书"不合法输出最多修复一次"）。
MAX_INTERPRETATION_ATTEMPTS = 2

#: 允许 `submit_message` 的当前状态（其余状态拒绝，避免非法迁移并保护执行态）。
_SUBMITTABLE_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.UNDERSTANDING,
        WorkflowState.WAITING_CLARIFICATION,
        WorkflowState.WAITING_CONFIRMATION,
    }
)


class SubmitMessageOutcome(BaseModel):
    """`submit_message` 的返回形状（Step 06 冻结，写入 handoff）。

    - `snapshot`：处理完成后的会话快照（状态、当前 revision、pending question 指针）；
    - `resolution`：本次 `IntentEngine.resolve` 的结果（含 applied deltas 与全部 issues）；
    - `pending_question`：当前生效的待答问题（有则非 None；ready 时为 None）；
    - `confirmation_summary`：仅当进入 `WAITING_CONFIRMATION` 时给出，客户端展示它并把
      `compute_summary_hash(...)` 交给 `confirm_current_intent`。
    """

    model_config = _FROZEN

    snapshot: SessionSnapshot
    resolution: IntentResolution
    pending_question: PendingQuestion | None = None
    confirmation_summary: ConfirmationSummary | None = None


class WorkflowService:
    """P1 工作流用例（唯一业务入口；确定性代码，唯一 LLM 调用经 IntentEngine）。"""

    def __init__(
        self,
        repo: Repository,
        intent_engine: IntentEngine,
        question_builder: QuestionBuilder,
        settings: Settings,
    ) -> None:
        self._repo = repo
        self._intent_engine = intent_engine
        self._question_builder = question_builder
        self._settings = settings

    # -- 用例 ---------------------------------------------------------------

    def create_session(self) -> SessionSnapshot:
        """新建会话：ID `ses`、初始 ExecutionRevision、状态 UNDERSTANDING。"""
        session_id = new_id(SESSION_ID_PREFIX)
        self._repo.create_session(session_id)
        self._repo.append_execution_revision(
            ExecutionRevision(
                execution_revision_id=new_id(EXECUTION_REVISION_ID_PREFIX),
                session_id=session_id,
                parent_revision_id=None,
                target_model=self._settings.image_model,
                output_size=DEFAULT_OUTPUT_SIZE,
            )
        )
        return self._repo.get_current_session_snapshot(session_id)

    def submit_message(self, session_id: str, text: str) -> SubmitMessageOutcome:
        """处理一条用户消息（新需求或澄清回答），返回最新快照与解析结果。"""
        if not isinstance(text, str):
            raise TypeError("text must be a str; the workflow never guesses user input")

        snapshot = self._repo.get_current_session_snapshot(session_id)
        if snapshot.workflow_state not in _SUBMITTABLE_STATES:
            raise WorkflowError(
                WORKFLOW_INVALID_STATE,
                f"session {session_id!r} is in {snapshot.workflow_state.value}; "
                "new messages are only accepted while the user is in an understanding "
                "or waiting-for-clarification/confirmation state",
            )

        previous_question = _decode_pending_question(snapshot)
        message_id = new_id(MESSAGE_ID_PREFIX)
        self._repo.append_message(session_id, message_id, "user", text, utc_now())
        self._repo.transition_state(session_id, WorkflowState.UNDERSTANDING)

        snapshot = self._repo.get_current_session_snapshot(session_id)
        request = IntentResolveRequest(
            current_intent=_current_intent(self._repo, snapshot),
            message_id=message_id,
            message_text=text,
            pending_question=(
                previous_question.to_spec() if previous_question is not None else None
            ),
            available_message_ids=frozenset(snapshot.message_ids),
        )
        resolution = self._resolve_with_single_repair(request)

        if resolution.applied_deltas:
            revision = IntentRevision(
                intent_revision_id=new_id(INTENT_REVISION_ID_PREFIX),
                session_id=session_id,
                parent_revision_id=snapshot.current_intent_revision_id,
                intent=resolution.intent,
                applied_deltas=list(resolution.applied_deltas),
                created_at=utc_now(),
            )
            self._repo.append_intent_revision(revision)

            # 【Rev.3 工单 D】新 revision 落库后接入 Realization carry：与
            # `review.py::_revise` 对应段落逐字同口径的受控重复（不抽共享 helper）。
            # 先读 state、再派生 ChangeSummary、再评估；无 state / 无失效 → 零写入。
            realization_state = self._load_realization_state(session_id)
            change_summary = derive_change_summary(list(resolution.applied_deltas))
            carry = evaluate_carry(realization_state, change_summary)
            if realization_state is not None and carry.has_invalidations():
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

        if resolution.ready_for_confirmation:
            self._repo.clear_pending_question(session_id)
            self._repo.transition_state(session_id, WorkflowState.WAITING_CONFIRMATION)
        elif resolution.question is not None:
            pending = self._question_builder.build(resolution.question, session_id)
            self._repo.save_pending_question(
                session_id, pending.question_id, pending.model_dump_json()
            )
            self._repo.transition_state(session_id, WorkflowState.WAITING_CLARIFICATION)
        # 否则：可恢复失败且当前 Intent 无待决问题（例如确认页消息解析失败）：
        # 不猜测修复、不伪造问题、不进入确认；保持 UNDERSTANDING，issue 在 resolution 中可见。

        final = self._repo.get_current_session_snapshot(session_id)
        confirmation_summary = (
            self._build_summary_for(final)
            if final.workflow_state is WorkflowState.WAITING_CONFIRMATION
            else None
        )
        return SubmitMessageOutcome(
            snapshot=final,
            resolution=resolution,
            pending_question=_decode_pending_question(final),
            confirmation_summary=confirmation_summary,
        )

    def get_session(self, session_id: str) -> SessionSnapshot:
        """读取会话当前快照（状态、revision 指针、pending question、消息 ID）。"""
        return self._repo.get_current_session_snapshot(session_id)

    def get_confirmation_summary(self, session_id: str) -> ConfirmationSummary:
        """预览当前确认摘要（读取侧；用于 GET 会话时重建确认视图）。

        摘要按当前两类 revision 重算，因此客户端可随时得到与 `confirm_current_intent`
        将要核对的**同一个** hash；仅 `WAITING_CONFIRMATION` 可用。
        """
        snapshot = self._repo.get_current_session_snapshot(session_id)
        if snapshot.workflow_state is not WorkflowState.WAITING_CONFIRMATION:
            raise WorkflowError(
                WORKFLOW_INVALID_STATE,
                f"session {session_id!r} is in {snapshot.workflow_state.value}; a confirmation "
                "summary only exists in WAITING_CONFIRMATION",
            )
        return self._build_summary_for(snapshot)

    def confirm_current_intent(
        self,
        session_id: str,
        intent_revision_id: str,
        execution_revision_id: str,
        summary_hash: str,
    ) -> ConfirmationRecord:
        """确认当前展示的 revision（Hard Confirmation Gate 的唯一入口）。"""
        snapshot = self._repo.get_current_session_snapshot(session_id)
        if snapshot.workflow_state is not WorkflowState.WAITING_CONFIRMATION:
            raise WorkflowError(
                WORKFLOW_INVALID_STATE,
                f"session {session_id!r} is in {snapshot.workflow_state.value}; "
                "confirmation is only accepted in WAITING_CONFIRMATION",
            )
        if (
            intent_revision_id != snapshot.current_intent_revision_id
            or execution_revision_id != snapshot.current_execution_revision_id
        ):
            raise WorkflowError(
                WORKFLOW_STALE_REVISION,
                "the confirmation does not bind the current intent/execution revision "
                f"(requested intent={intent_revision_id!r}, "
                f"execution={execution_revision_id!r}; current intent="
                f"{snapshot.current_intent_revision_id!r}, current execution="
                f"{snapshot.current_execution_revision_id!r}); refresh the summary and "
                "ask the user to confirm again",
            )

        expected_hash = compute_summary_hash(self._build_summary_for(snapshot))
        if not isinstance(summary_hash, str) or summary_hash != expected_hash:
            raise WorkflowError(
                WORKFLOW_SUMMARY_HASH_MISMATCH,
                "the requested summary_hash does not match the summary recomputed from the "
                "current revisions; the user confirmed a different (e.g. stale) summary",
            )

        record = ConfirmationRecord(
            confirmation_id=new_id(CONFIRMATION_ID_PREFIX),
            session_id=session_id,
            intent_revision_id=intent_revision_id,
            execution_revision_id=execution_revision_id,
            summary_hash=expected_hash,
            confirmed_at=utc_now(),
        )
        self._repo.save_confirmation(record)
        return record

    # -- 内部实现 ----------------------------------------------------------

    def _resolve_with_single_repair(self, request: IntentResolveRequest) -> IntentResolution:
        """初次解析 + 至多一次可重试失败的重试（不猜测修复，不吞 issue）。"""
        resolution = self._intent_engine.resolve(request)
        attempts = 1
        while attempts < MAX_INTERPRETATION_ATTEMPTS and _has_retryable_failure(resolution):
            resolution = self._intent_engine.resolve(request)
            attempts += 1
        return resolution

    def _build_summary_for(self, snapshot: SessionSnapshot) -> ConfirmationSummary:
        if (
            snapshot.current_intent_revision_id is None
            or snapshot.current_execution_revision_id is None
        ):
            raise WorkflowError(
                WORKFLOW_INVALID_STATE,
                f"session {snapshot.session_id!r} has no current intent/execution revision "
                "to summarize",
            )
        return build_confirmation_summary(
            self._repo.get_intent_revision(snapshot.current_intent_revision_id),
            self._repo.get_execution_revision(snapshot.current_execution_revision_id),
        )

    def _load_realization_state(self, session_id: str) -> RealizationState | None:
        """读取会话当前 RealizationState（无则 None；payload 反序列化归本步）。

        与 `review.py::_load_realization_state` 逐字同口径（受控重复，工单 D 不抽
        共享 helper）；`evaluate_carry` 对 None 返回空评估，因此无 state 时零写入。
        """
        stored = self._repo.get_current_realization_state(session_id)
        if stored is None:
            return None
        return RealizationState.model_validate_json(stored.payload)


def _current_intent(repo: Repository, snapshot: SessionSnapshot) -> VisualIntent:
    """当前 Draft Intent；尚未产生任何 revision 时为空 Intent（缺省全 None）。"""
    if snapshot.current_intent_revision_id is None:
        return VisualIntent()
    return repo.get_intent_revision(snapshot.current_intent_revision_id).intent


def _decode_pending_question(snapshot: SessionSnapshot) -> PendingQuestion | None:
    """从会话 payload 反序列化待答问题（payload 形态由本步自定义为 JSON）。"""
    if snapshot.pending_question_id is None or snapshot.pending_question_payload is None:
        return None
    return PendingQuestion.model_validate_json(snapshot.pending_question_payload)


def _has_retryable_failure(resolution: IntentResolution) -> bool:
    """解析/Provider 失败是否属于可重试类别（Step 05 冻结的 code 语义）。

    - `interpreter.unparseable_output*`：Step 05 产生的解析失败一律 `retryable=True`；
    - `provider.*`：仅 `providers.errors.RETRYABLE_CODES`（rate_limited / timeout /
      network / server_error）可重试；auth / invalid_request / unparseable_response 不可。
    """
    for issue in resolution.issues:
        code = issue.code
        if code == INTERPRETER_UNPARSEABLE_OUTPUT or code.startswith(
            f"{INTERPRETER_UNPARSEABLE_OUTPUT}."
        ):
            return True
        if code in RETRYABLE_CODES:
            return True
    return False


__all__ = [
    "WorkflowService",
    "SubmitMessageOutcome",
    "MAX_INTERPRETATION_ATTEMPTS",
    "DEFAULT_OUTPUT_SIZE",
    "SESSION_ID_PREFIX",
    "MESSAGE_ID_PREFIX",
    "INTENT_REVISION_ID_PREFIX",
    "EXECUTION_REVISION_ID_PREFIX",
    "CONFIRMATION_ID_PREFIX",
]
