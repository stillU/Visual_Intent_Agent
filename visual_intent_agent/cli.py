"""MVP v0.3 工程预览版：最小 CLI 入口（Step 08 提前实现，见 REVISION_002）。

职责边界（任务书 08「实施内容」/「禁止范围」）：

- **只做接线与交互**：复用 `Settings` / `SQLiteRepository` / `WorkflowService` /
  `ReviewService` / `GenerationPipeline` / `PromptEngine`；
- **不复制领域规则**：状态判断只读取 `Repository.get_current_session_snapshot` 暴露的
  `workflow_state`；"下一步允许什么"完全由既有服务决定，CLI 不新增迁移；
- **不绕过硬门禁**：确认必须由用户在终端显式输入确认词触发，CLI 绝不自动确认；
  生成必经 `WorkflowService.confirm_current_intent` + `GenerationPipeline.generate`；
  任何修改都会产生新 `IntentRevision` 并使旧确认天然失效，必须重新确认；
- **不输出密钥**：不打印 `Settings`、`.env` 内容或 Authorization；只显示模型名、
  Revision / Artifact ID 与本地输出路径。

交互流程（任务书 08「最小用户流程」）：

    需求 → 澄清（逐轮单问题）→ 展示 diff-first 摘要并要求人工确认
         → 生成并显示输出路径 → 接受或提交修改反馈 → 重新确认 → 生成下一版 → 退出

模式：

- 默认 **真实模式**：经 `load_settings()` 读取 `Settings`，使用真实
  `OpenAICompatibleLLMProvider` / `OpenAIImageProvider`；
- `--demo` **离线演示模式**：不需要任何凭据，使用确定性 `_DemoLLM` 与
  `FakeImageProvider`；演示逻辑只存在于本模块，属于预览版便利设施，不是产品能力，
  也不改变任何业务合同。

本模块**不修改**其它模块（workflow / policy / evaluation / provider / generation）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Protocol, Sequence

from pydantic import SecretStr

from visual_intent_agent.config import (
    DEFAULT_ENV_FILE,
    PROJECT_ROOT,
    ConfigurationError,
    Settings,
    load_settings,
)
from visual_intent_agent.domain import Issue, Severity
from visual_intent_agent.feedback import FeedbackEngine, FeedbackError
from visual_intent_agent.generation import (
    GenerationArtifact,
    GenerationError,
    GenerationPipeline,
)
from visual_intent_agent.intent_engine import IntentEngine, Interpreter
from visual_intent_agent.intent_engine.models import InterpreterError
from visual_intent_agent.persistence import (
    InvalidStateTransitionError,
    Repository,
    RepositoryError,
    SQLiteRepository,
    SessionSnapshot,
    WorkflowState,
)
from visual_intent_agent.prompt_engine import (
    QWEN_IMAGE_MODEL,
    PromptCompilationError,
    PromptEngine,
    QwenImageRenderer,
)
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_image import FakeImageProvider
from visual_intent_agent.providers.llm import LLMRequest, LLMResponse
from visual_intent_agent.providers.openai_image import OpenAIImageProvider
from visual_intent_agent.providers.openai_llm import OpenAICompatibleLLMProvider
from visual_intent_agent.workflow import (
    QuestionBuilder,
    SubmitMessageOutcome,
    WorkflowError,
    WorkflowService,
    compute_summary_hash,
)
from visual_intent_agent.workflow.confirmation import ConfirmationSummary
from visual_intent_agent.workflow.review import (
    FeedbackOutcome,
    ReviewError,
    ReviewService,
)

__all__ = ["SessionApp", "SystemConsole", "main", "run_repl", "build_demo_app", "build_app"]

#: 预览版标识（正式版本状态见 docs/releases/；本轮不声称正式发布）。
CLI_VERSION = "visual-intent-agent MVP v0.3 engineering preview (not a formal release)"

#: 用户显式确认词（必须整词匹配；任何其他输入都当作修改消息或退出）。
CONFIRM_TOKENS: frozenset[str] = frozenset(
    {"y", "yes", "confirm", "ok", "确认", "是", "生成"}
)
EXIT_TOKENS: frozenset[str] = frozenset({"exit", "quit", "q", "退出", "结束"})
RETRY_TOKENS: frozenset[str] = frozenset({"retry", "重试"})

#: REVIEW 状态下把用户输入当作"接受"时提交给 FeedbackEngine 的原文。
#: 注意：是否 accept 仍由 FeedbackEngine（LLM）裁决，CLI 不能替它下结论。
ACCEPT_FEEDBACK_TEXT = "我接受当前结果，可以结束 (accept the current image)"

#: CLI 能给出可读提示的领域异常集合（其余异常照常上抛，避免掩盖程序错误）。
_HANDLED_ERRORS = (
    WorkflowError,
    ReviewError,
    GenerationError,
    ProviderError,
    RepositoryError,
    InvalidStateTransitionError,
    ConfigurationError,
    FeedbackError,
    InterpreterError,
    PromptCompilationError,
)

_PREVIEW_BANNER = (
    "Visual Intent Agent —— MVP v0.3 工程预览版（非正式发布）\n"
    "本预览版本轮未执行真实 smoke / 正式复评 / 人工盲评；只提供最小可操作闭环。\n"
    "输入 exit 可随时退出；反馈与澄清阶段不会自动确认。"
)


# ---------------------------------------------------------------------------
# 控制台抽象（真实终端 / 测试脚本注入）
# ---------------------------------------------------------------------------


class Console(Protocol):
    """最小 I/O 抽象：真实终端与测试脚本都实现这两个方法。"""

    def print(self, text: str = "") -> None: ...

    def prompt(self, text: str) -> str: ...


class SystemConsole:
    """真实终端实现（stdout + input）。"""

    def __init__(self, stream: Any | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def print(self, text: str = "") -> None:
        print(text, file=self._stream, flush=True)

    def prompt(self, text: str) -> str:
        return input(text)


# ---------------------------------------------------------------------------
# 应用层：把四个既有服务装配成 CLI 可调用的窄接口（不含任何领域规则）
# ---------------------------------------------------------------------------


class SessionApp:
    """CLI 的唯一服务门面（薄编排；不判断状态机、不复制策略）。

    所有用例都直接转发给既有服务；唯一"组合"是
    `confirm_and_generate`（展示摘要 → 用户确认 → 提交 hash → 生成），
    顺序与 client 合同一致：`get_confirmation_summary` → `compute_summary_hash`
    → `confirm_current_intent` → `GenerationPipeline.generate`。
    """

    def __init__(
        self,
        *,
        repo: Repository,
        workflow: WorkflowService,
        review: ReviewService,
        pipeline: GenerationPipeline,
        settings: Settings,
    ) -> None:
        self._repo = repo
        self._workflow = workflow
        self._review = review
        self._pipeline = pipeline
        self._settings = settings

    # -- 构造 ---------------------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        db_path: Path | str | None = None,
        output_dir: Path | str | None = None,
    ) -> "SessionApp":
        """真实模式：真实 LLM / 图像 adapter + `Settings` 中的路径。"""
        return cls._assemble(
            settings=settings,
            repo=SQLiteRepository(db_path if db_path is not None else settings.db_path),
            llm=OpenAICompatibleLLMProvider(settings),
            image=OpenAIImageProvider(settings),
            output_dir=Path(output_dir) if output_dir is not None else settings.output_dir,
        )

    @classmethod
    def _assemble(
        cls,
        *,
        settings: Settings,
        repo: Repository,
        llm: Any,
        image: Any,
        output_dir: Path,
    ) -> "SessionApp":
        workflow = WorkflowService(
            repo=repo,
            intent_engine=IntentEngine(Interpreter(llm)),
            question_builder=QuestionBuilder(),
            settings=settings,
        )
        review = ReviewService(
            repo=repo,
            feedback_engine=FeedbackEngine(llm),
            question_builder=QuestionBuilder(),
        )
        pipeline = GenerationPipeline(
            repo=repo,
            prompt_engine=PromptEngine(QwenImageRenderer(), repo),
            image_provider=image,
            output_dir=output_dir,
        )
        return cls(
            repo=repo,
            workflow=workflow,
            review=review,
            pipeline=pipeline,
            settings=settings,
        )

    # -- 用例（直接转发；错误语义由既有服务决定）----------------------------

    def create_session(self) -> str:
        return self._workflow.create_session().session_id

    def snapshot(self, session_id: str) -> SessionSnapshot:
        return self._workflow.get_session(session_id)

    def submit(self, session_id: str, text: str) -> SubmitMessageOutcome:
        return self._workflow.submit_message(session_id, text)

    def summary(self, session_id: str) -> ConfirmationSummary:
        return self._workflow.get_confirmation_summary(session_id)

    def confirmation_view(
        self, session_id: str
    ) -> tuple[SessionSnapshot, ConfirmationSummary]:
        """读取一次"展示用"的 `(snapshot, summary)`。

        REPL 必须把这里返回的 `summary` 与其绑定的 intent/execution revision id **原样**
        交回 `confirm_and_generate`；确认门禁会按当前快照重算并比对，任何在展示之后发生的
        revision 变化都会被拒绝，而不是被静默确认。
        """
        return (
            self._workflow.get_session(session_id),
            self._workflow.get_confirmation_summary(session_id),
        )

    def confirm_and_generate(
        self,
        session_id: str,
        *,
        summary: ConfirmationSummary,
        intent_revision_id: str | None,
        execution_revision_id: str | None,
    ) -> GenerationArtifact:
        """确认**用户实际看到的那一份摘要**并生成。

        绑定的是展示时捕获的 revision id 与摘要 hash，而不是重新读取"最新"摘要：
        若展示后 revision 已变化，`confirm_current_intent` 会以
        `workflow.stale_revision` / `workflow.summary_hash_mismatch` 拒绝，
        CLI 不会替用户确认一个没看到的新版本。
        """
        if intent_revision_id is None or execution_revision_id is None:
            raise WorkflowError(
                "workflow.invalid_state",
                f"session {session_id!r} has no current revision to confirm",
            )
        self._workflow.confirm_current_intent(
            session_id,
            intent_revision_id,
            execution_revision_id,
            compute_summary_hash(summary),
        )
        return self._pipeline.generate(session_id)

    def latest_generation(self, session_id: str) -> GenerationArtifact | None:
        stored = self._repo.list_generation_artifacts(session_id)
        if not stored:
            return None
        return GenerationArtifact.model_validate_json(stored[-1].payload)

    def feedback(self, session_id: str, text: str) -> FeedbackOutcome:
        generation = self.latest_generation(session_id)
        if generation is None:
            raise ReviewError(
                "workflow.generation_not_found",
                f"session {session_id!r} has no generation to review",
            )
        return self._review.submit_feedback(session_id, generation.generation_id, text)

    def retry_generation(self, session_id: str) -> GenerationArtifact:
        """FAILED 状态下用**当前失败尝试**的 PromptArtifact 重新生成（绝不重新 compile）。

        FAILED 的唯一来源是刚失败的那次 `generate`（或 `retry`）：该次 `generate` 若已编译
        Prompt，本会话**最新已落库** PromptArtifact 就是本次失败的依据。绝不能按"会话最新
        历史 `GenerationArtifact`"挑 Prompt —— 修改后第二次生成失败时，历史 Artifact 属于
        上一版，会取到旧（可能已 stale）的 Prompt。因此一律省略 `generation_id`，由
        `GenerationPipeline.retry` 选择最新已落库 Prompt，并保留 FAILED + 有效确认门禁。
        """
        return self._pipeline.retry(session_id)

    def can_retry_generation(self, session_id: str) -> bool:
        """FAILED 重试是否可行：本会话必须已有**已落库** PromptArtifact。

        编译失败等情况下没有可复用的 Prompt，`retry` 只会得到
        `generation.artifact_not_found`；界面据此如实提示"只能 exit / 新建会话"，
        而不是暗示当前 FAILED 可以直接重新确认。
        """
        return self._repo.get_latest_prompt_artifact(session_id) is not None

    def close(self) -> None:
        close = getattr(self._repo, "close", None)
        if callable(close):
            close()

    # -- 只读展示辅助 -------------------------------------------------------

    @property
    def target_model(self) -> str:
        return self._settings.image_model


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    return text.strip().lower()


def _intent_value(intent: Any, path: str) -> Any:
    facet_name, _, field_name = path.partition(".")
    facet = getattr(intent, facet_name, None)
    return None if facet is None else getattr(facet, field_name, None)


def render_issues(console: Console, issues: Sequence[Issue]) -> None:
    """打印可观察 issue（可恢复失败与观察；不改变状态）。"""
    if not issues:
        return
    console.print("本轮问题/提示:")
    for issue in issues:
        location = f" @{issue.path}" if issue.path else ""
        console.print(f"  - [{issue.severity.value}] {issue.code}{location}: {issue.message}")


def render_summary(console: Console, summary: ConfirmationSummary) -> None:
    """diff-first 确认摘要（六要素；只读展示，不代替用户确认）。"""
    console.print("")
    console.print("—— 确认摘要（diff-first）——")
    change = summary.change_summary
    changed = list(change.changed_paths) if change is not None else []
    cleared = list(change.cleared_paths) if change is not None else []
    if changed:
        console.print("本轮修改:")
        for path in changed:
            value = _intent_value(summary.intent, path)
            if value is None and path in summary.delegated_paths:
                console.print(f"  - {path} = （授权系统决定 / delegated）")
            else:
                console.print(f"  - {path} = {value!r}")
    if cleared:
        console.print("本轮清除:")
        for path in cleared:
            console.print(f"  - {path}")
    if not changed and not cleared:
        console.print("本轮修改: （无路径变化，展示当前已确认内容）")
    console.print(
        "明确保持 (pinned): " + (", ".join(summary.pinned_paths) or "（无）")
    )
    console.print(
        "授权系统决定 (delegated): " + (", ".join(summary.delegated_paths) or "（无）")
    )
    console.print(f"目标模型: {summary.target_model}")
    console.print(f"输出比例: {summary.output_size}")
    console.print("完整 Intent:")
    for path in sorted(_known_paths()):
        value = _intent_value(summary.intent, path)
        if value is not None:
            console.print(f"  - {path} = {value!r}")
    console.print(f"摘要哈希: {compute_summary_hash(summary)[:16]}…")


def _known_paths() -> tuple[str, ...]:
    from visual_intent_agent.domain import INTENT_PATHS

    return tuple(sorted(INTENT_PATHS))


def render_generation(console: Console, artifact: GenerationArtifact) -> None:
    """展示生成文件路径与 Artifact ID（不输出任何凭据）。"""
    console.print("")
    console.print("—— 生成结果 ——")
    console.print(f"generation_id: {artifact.generation_id}")
    console.print(f"prompt_artifact_id: {artifact.prompt_artifact_id}")
    console.print(f"model_version: {artifact.model_version}")
    console.print(f"size: {artifact.parameters.size}")
    for ref in artifact.output_refs:
        local = (Path(PROJECT_ROOT) / ref.path).resolve()
        console.print(f"输出文件: {local}  ({ref.mime_type}, {ref.byte_size} bytes)")


def render_error(console: Console, exc: BaseException) -> None:
    """简洁、可恢复的错误提示（含稳定 code 与下一步建议）。

    Provider 异常只显示安全 code 与非敏感元数据（retryable / status_code），
    **不**回显原始响应体或任何凭据；`Settings` 错误消息本身也保证不含密钥值。
    """
    code = getattr(exc, "code", None) or type(exc).__name__
    console.print("")
    if isinstance(exc, ProviderError):
        status = "" if exc.status_code is None else f", status={exc.status_code}"
        console.print(f"错误 [{code}]: retryable={exc.retryable}{status}")
        console.print("（已省略上游原始响应，避免泄露 Provider 细节/凭据。）")
    else:
        console.print(f"错误 [{code}]: {exc}")
    hint = _error_hint(exc)
    if hint:
        console.print(f"提示: {hint}")


def _error_hint(exc: BaseException) -> str | None:
    code = getattr(exc, "code", None)
    if code == "workflow.invalid_state":
        return "当前状态不允许该操作；按界面提示继续，或输入 exit 退出。"
    if code in {"workflow.stale_revision", "workflow.summary_hash_mismatch"}:
        return "确认绑定已经变化：请重新查看摘要后再确认。"
    if code in {"workflow.generation_not_found", "workflow.generation_mismatch"}:
        return "反馈必须绑定当前待复核的这次生成；输入 exit 后重新运行可重新开始。"
    if code == "generation.no_valid_confirmation":
        return (
            "生成需要绑定当前 revision 的有效确认；"
            "若会话已是 FAILED 且确认已失效，只能 exit 后新建会话。"
        )
    if code == "generation.invalid_state":
        return "当前状态不允许生成；FAILED 时请输入 retry。"
    if code == "generation.artifact_not_found":
        return (
            "本会话没有可复用的已落库 Prompt：当前 FAILED 无法重试，"
            "只能 exit 后新建会话（不能直接重新确认当前 FAILED）。"
        )
    if isinstance(exc, PromptCompilationError) or (
        isinstance(code, str) and code.startswith("prompt.")
    ):
        return (
            "Prompt 编译失败，本次没有生成新的 Prompt；"
            "若本会话没有已落库 Prompt 则无法 retry，只能 exit 后新建会话。"
        )
    if isinstance(exc, ProviderError):
        if exc.retryable:
            return "Provider 调用失败（可重试）：输入 retry 用同一条消息显式重试。"
        return "Provider 调用失败（不可重试）；请检查配置或稍后重新运行。"
    if isinstance(exc, ConfigurationError):
        return (
            "缺少或非法的 Provider 配置；请设置 VIA_PROVIDER_BASE_URL / "
            "VIA_PROVIDER_API_KEY，或使用 --demo 离线试用。"
        )
    if isinstance(exc, RepositoryError):
        return "持久化失败；请检查 --db 路径是否可写。"
    if isinstance(exc, FeedbackError):
        return "反馈上下文不一致；请重试或重新开始会话。"
    return None


def _outcome_failed(outcome: SubmitMessageOutcome | FeedbackOutcome) -> bool:
    """本轮是否"未处理成功"。

    优先使用 workflow 层的显式 `recoverable_failure` 标志（B 工包已落地）；
    在该字段尚未存在的当前接口上，回退为"出现 severity=ERROR 的 issue"。
    **绝不**用 `pending_question is None` 之类的外观推断成功。
    """
    explicit = getattr(outcome, "recoverable_failure", None)
    if explicit is not None:
        return bool(explicit)
    issues = getattr(getattr(outcome, "resolution", None), "issues", None)
    if issues is None:
        issues = getattr(getattr(outcome, "feedback", None), "issues", ())
    return any(issue.severity is Severity.ERROR for issue in issues)


def _failure_codes(outcome: SubmitMessageOutcome | FeedbackOutcome) -> tuple[str, ...]:
    """本轮失败的稳定 code 列表（显式字段优先，其次从 issue 中收集）。"""
    explicit = getattr(outcome, "failure_codes", None)
    if explicit:
        return tuple(str(code) for code in explicit)
    codes = [
        issue.code
        for issue in getattr(getattr(outcome, "resolution", None), "issues", ())
        if issue.severity is Severity.ERROR
    ]
    codes.extend(
        issue.code
        for issue in getattr(getattr(outcome, "feedback", None), "issues", ())
        if issue.severity is Severity.ERROR
    )
    return tuple(dict.fromkeys(codes))


def render_recoverable_failure(
    console: Console, outcome: SubmitMessageOutcome | FeedbackOutcome
) -> None:
    """显式宣布"本轮未处理成功"，并给出可恢复的下一步（不自动重试）。"""
    codes = _failure_codes(outcome)
    suffix = f" codes={list(codes)}" if codes else ""
    console.print(f"本轮未处理成功：状态未推进，也未生成/确认任何内容。{suffix}")
    if "workflow.stale_revision" in codes:
        console.print("提示: 确认绑定已过期，已按最新状态刷新；请重新查看摘要后再确认。")
    else:
        console.print("提示: 可修正措辞后重新输入，或输入 retry 显式重发上一条。")


# ---------------------------------------------------------------------------
# 交互主循环
# ---------------------------------------------------------------------------


def run_repl(app: SessionApp, console: Console) -> int:
    """状态驱动的交互循环；确认只由用户显式输入触发。"""
    session_id = app.create_session()
    console.print(_PREVIEW_BANNER)
    console.print(f"会话 ID: {session_id}")
    question_text: str | None = None
    failed_text: str | None = None

    while True:
        state = app.snapshot(session_id).workflow_state

        if state in (WorkflowState.UNDERSTANDING, WorkflowState.WAITING_CLARIFICATION):
            if question_text:
                console.print(f"澄清问题: {question_text}")
            text = console.prompt("需求/回答 (retry=重发上一条, exit=退出): ")
            token = _norm(text)
            if token in EXIT_TOKENS:
                break
            if token in RETRY_TOKENS and failed_text is not None:
                text = failed_text
                console.print(f"重发上一条: {text!r}")
            try:
                outcome = app.submit(session_id, text)
            except _HANDLED_ERRORS as exc:  # noqa: PERF203 - 逐操作错误边界
                render_error(console, exc)
                failed_text = text
                continue
            render_issues(console, outcome.resolution.issues)
            question_text = (
                outcome.pending_question.question_text
                if outcome.pending_question is not None
                else None
            )
            if _outcome_failed(outcome):
                render_recoverable_failure(console, outcome)
                failed_text = text
            else:
                failed_text = None
            continue

        if state is WorkflowState.WAITING_CONFIRMATION:
            try:
                snapshot, summary = app.confirmation_view(session_id)
            except _HANDLED_ERRORS as exc:
                render_error(console, exc)
                continue
            render_summary(console, summary)
            text = console.prompt("确认并生成? [y/N]（或输入修改 / exit）: ")
            token = _norm(text)
            if token in EXIT_TOKENS:
                break
            if token in CONFIRM_TOKENS:
                try:
                    # 只确认**刚展示的**这份摘要与其 revision 绑定；展示后若有变化，
                    # 服务会拒绝（stale_revision / summary_hash_mismatch），不静默确认新版本。
                    artifact = app.confirm_and_generate(
                        session_id,
                        summary=summary,
                        intent_revision_id=snapshot.current_intent_revision_id,
                        execution_revision_id=snapshot.current_execution_revision_id,
                    )
                except _HANDLED_ERRORS as exc:
                    render_error(console, exc)
                    continue
                console.print("已确认并生成。")
                render_generation(console, artifact)
                question_text = None
                failed_text = None
            else:
                # 用户没有确认 → 当作修改消息；服务会落新 revision 并再次要求确认。
                try:
                    outcome = app.submit(session_id, text)
                except _HANDLED_ERRORS as exc:
                    render_error(console, exc)
                    failed_text = text
                    continue
                render_issues(console, outcome.resolution.issues)
                question_text = (
                    outcome.pending_question.question_text
                    if outcome.pending_question is not None
                    else None
                )
                if _outcome_failed(outcome):
                    render_recoverable_failure(console, outcome)
                    failed_text = text
                else:
                    failed_text = None
            continue

        if state is WorkflowState.WAITING_REVIEW:
            artifact = app.latest_generation(session_id)
            if artifact is not None:
                render_generation(console, artifact)
            console.print('可输入：修改反馈原文；"accept" 表示接受；retry 重发上一条。')
            text = console.prompt("反馈 (accept=接受, retry=重发, exit=退出): ")
            token = _norm(text)
            if token in EXIT_TOKENS:
                break
            if token in RETRY_TOKENS and failed_text is not None:
                text = failed_text
            elif token in CONFIRM_TOKENS or token == "accept" or token == "接受":
                text = ACCEPT_FEEDBACK_TEXT
            try:
                outcome = app.feedback(session_id, text)
            except _HANDLED_ERRORS as exc:
                render_error(console, exc)
                failed_text = text
                continue
            render_issues(console, outcome.feedback.issues)
            if _outcome_failed(outcome):
                render_recoverable_failure(console, outcome)
                failed_text = text
            else:
                failed_text = None
            question_text = (
                outcome.pending_question.question_text
                if outcome.pending_question is not None
                else None
            )
            continue

        if state is WorkflowState.FAILED:
            console.print("")
            if not app.can_retry_generation(session_id):
                # 没有已落库 Prompt（如编译失败/从未编译）：当前 FAILED 无法重试，
                # 也不能直接重新确认；如实提示只能退出后新建会话。
                console.print(
                    "上次生成失败（FAILED），且本会话没有可复用的已落库 Prompt"
                    "（编译失败或从未编译）。"
                )
                console.print("当前 FAILED 无法 retry，也不能直接重新确认；只能输入 exit 后新建会话。")
                text = console.prompt("输入 exit 退出: ")
                if _norm(text) in EXIT_TOKENS:
                    break
                continue
            console.print("上次生成失败（FAILED）。可用同一 PromptArtifact 重新生成。")
            text = console.prompt("输入 retry 重新生成，或 exit 退出: ")
            token = _norm(text)
            if token in EXIT_TOKENS:
                break
            if token not in RETRY_TOKENS:
                continue
            try:
                artifact = app.retry_generation(session_id)
            except _HANDLED_ERRORS as exc:
                render_error(console, exc)
                continue
            console.print("已用同一 PromptArtifact 重新生成。")
            render_generation(console, artifact)
            continue

        if state is WorkflowState.COMPLETED:
            console.print("会话已完成（COMPLETED）。")
            break

        # GENERATING 是瞬时状态；正常流程不会在此处停留。
        console.print(f"会话停留在 {state.value}；请检查上一次执行的日志。")
        break

    console.print("已退出。")
    return 0


def _run_app(app: SessionApp, console: Console) -> int:
    """运行交互循环并确保释放 Repository（`run_repl` 本身不拥有资源）。"""
    try:
        return run_repl(app, console)
    finally:
        app.close()


# ---------------------------------------------------------------------------
# --demo 专用确定性 Provider（只存在于 CLI；不改变任何业务合同）
# ---------------------------------------------------------------------------

_TARGET_RE = re.compile(r"^- target_path: (.+)$", re.MULTILINE)
_DELEGATE_RE = re.compile(r"^- allow_delegate: (true|false)$", re.MULTILINE)
_SUGGESTED_RE = re.compile(r"^- suggested_values: (\[.*\])$", re.MULTILINE)
_USER_MESSAGE_RE = re.compile(r"<<<USER_MESSAGE\n(.*?)\nUSER_MESSAGE", re.DOTALL)
_USER_FEEDBACK_RE = re.compile(r"<<<USER_FEEDBACK\n(.*?)\nUSER_FEEDBACK", re.DOTALL)

#: demo 中"交给系统决定"的触发短语。
_DELEGATE_PHRASES: frozenset[str] = frozenset(
    {"you decide", "你决定", "交给系统", "随便", "delegate", "系统决定"}
)

#: demo 自由文本路径的默认值（用户直接回车时使用）。
_DEMO_DEFAULTS: dict[str, str] = {
    "subject.description": "a cat",
    "environment.location": "a wooden table",
}

#: demo 中文别名 → 预设选项（仅演示便利，不是产品行为）。
_DEMO_OPTION_ALIASES: dict[str, str] = {
    "棚拍": "studio",
    "室内": "indoor",
    "户外": "outdoor",
    "特写": "close_up",
    "中景": "medium_shot",
    "远景": "wide_shot",
    "站": "standing",
    "坐": "sitting",
    "走": "walking",
    "柔光": "soft",
    "戏剧": "dramatic",
    "写实": "photorealistic",
    "电影感": "cinematic",
    "插画": "illustration",
    "暖": "warm",
    "冷": "cool",
}

#: demo 反馈接受短语。
_DEMO_ACCEPT_PHRASES: tuple[str, ...] = (
    "accept",
    "接受",
    "满意",
    "可以了",
    "好的",
    "完成",
    "done",
    "就这样",
)

#: demo 反馈修改规则：命中短语 → (path, value)；命中的短语同时作为证据原文。
_DEMO_REVISE_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("户外", "outdoor"), "environment.mode", "outdoor"),
    (("棚拍", "studio"), "environment.mode", "studio"),
    (("远景", "宽", "wide"), "composition.framing", "wide_shot"),
    (("特写", "近", "close"), "composition.framing", "close_up"),
    (("暖", "warm"), "color.palette", "warm"),
    (("冷", "cool"), "color.palette", "cool"),
)

#: demo clarify 的默认目标路径（无法定位反馈时）。
_DEMO_CLARIFY_PATH = "environment.location"


class _DemoLLM:
    """`--demo` 专用确定性 LLM：把用户输入映射为候选 Delta。

    行为（预览版便利设施，**不是**产品语义）：

    - Interpreter 调用：从提示词中读取当前 Pending Question 的 `target_path` 与预设选项，
      把用户输入匹配为选项值（或原文），返回一条 SET 候选 Delta；
    - Feedback 调用：识别接受短语 → accept；识别修改短语 → revise；否则 → clarify；
    - 不做任何持久化、状态修改或确认；所有结果仍必须经既有 Validator / Reducer /
      工作流硬门禁。
    """

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not isinstance(request, LLMRequest):
            raise ProviderError.invalid_request("_DemoLLM.complete expects an LLMRequest")
        self.requests.append(request)
        system = request.messages[0].content if request.messages else ""
        prompt = request.messages[-1].content if request.messages else ""
        if "Feedback Interpreter" in system:
            content = self._feedback(prompt)
        else:
            content = self._interpret(prompt)
        return LLMResponse(content=content, model="demo-llm")

    # -- Interpreter --------------------------------------------------------

    def _interpret(self, prompt: str) -> str:
        text = (_extract(_USER_MESSAGE_RE, prompt) or "").strip()
        target = _extract(_TARGET_RE, prompt)
        allow_delegate = _extract(_DELEGATE_RE, prompt) == "true"
        options = _load_options(_extract(_SUGGESTED_RE, prompt))

        if target is None:
            delta: dict[str, Any] = {
                "operation": "SET",
                "path": "subject.description",
                "value": text or _DEMO_DEFAULTS["subject.description"],
                "answers_pending_question": False,
                "evidence_fragment": text or None,
            }
        elif allow_delegate and text.lower() in _DELEGATE_PHRASES:
            delta = {
                "operation": "SET",
                "path": target,
                "resolution": "user_delegated",
                "answers_pending_question": True,
                "evidence_fragment": text,
            }
        else:
            value = (
                _match_option(text, options)
                or text
                or _DEMO_DEFAULTS.get(target)
                or (options[0] if options else "unspecified")
            )
            delta = {
                "operation": "SET",
                "path": target,
                "value": value,
                "answers_pending_question": True,
                "evidence_fragment": text or None,
            }
        return json.dumps(
            {
                "candidate_deltas": [delta],
                "detected_conflicts": [],
                "unresolved_language": [],
            }
        )

    # -- Feedback -----------------------------------------------------------

    def _feedback(self, prompt: str) -> str:
        text = (_extract(_USER_FEEDBACK_RE, prompt) or "").strip()
        lowered = text.lower()
        if any(phrase in lowered for phrase in _DEMO_ACCEPT_PHRASES):
            return json.dumps({"decision": "accept", "preserve_paths": []})
        for phrases, path, value in _DEMO_REVISE_RULES:
            for phrase in phrases:
                if phrase in lowered:
                    return json.dumps(
                        {
                            "decision": "revise",
                            "candidate_deltas": [
                                {
                                    "operation": "SET",
                                    "path": path,
                                    "value": value,
                                    "evidence_fragment": phrase,
                                }
                            ],
                            "preserve_paths": [],
                        }
                    )
        return json.dumps(
            {
                "decision": "clarify",
                "clarify_path": _DEMO_CLARIFY_PATH,
                "clarify_reason": "demo 模式无法从这条反馈定位具体修改，请说明要改的视觉方面。",
            }
        )


def _extract(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return None if match is None else match.group(1).strip()


def _load_options(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _match_option(text: str, options: Sequence[str]) -> str | None:
    """把用户输入匹配为预设选项（精确 > 中文别名 > 包含）。"""
    candidate = text.strip().lower()
    if not candidate:
        return None
    for option in options:
        if candidate == option.lower():
            return option
    alias = _DEMO_OPTION_ALIASES.get(text.strip())
    if alias is not None and alias in options:
        return alias
    for option in options:
        if option.lower() in candidate or candidate in option.lower():
            return option
    return None


# ---------------------------------------------------------------------------
# 装配与入口
# ---------------------------------------------------------------------------


def build_app(
    settings: Settings,
    *,
    db_path: Path | str | None = None,
    output_dir: Path | str | None = None,
) -> SessionApp:
    """真实模式装配（可注入 settings/env 便于测试配置错误路径）。"""
    return SessionApp.from_settings(settings, db_path=db_path, output_dir=output_dir)


def build_demo_app(
    *,
    db_path: Path | str | None = None,
    output_dir: Path | str | None = None,
) -> SessionApp:
    """离线演示装配：不需要任何凭据，不访问网络。"""
    resolved_db = Path(db_path) if db_path is not None else PROJECT_ROOT / "data" / "cli_demo.db"
    resolved_out = (
        Path(output_dir)
        if output_dir is not None
        else PROJECT_ROOT / "outputs" / "generations"
    )
    settings = Settings(
        provider_base_url="http://demo.invalid/v1",
        provider_api_key=SecretStr("demo-placeholder-not-a-real-credential"),
        image_model=QWEN_IMAGE_MODEL,
        db_path=resolved_db,
        output_dir=resolved_out,
    )
    return SessionApp._assemble(
        settings=settings,
        repo=SQLiteRepository(resolved_db),
        llm=_DemoLLM(),
        image=FakeImageProvider(),
        output_dir=resolved_out,
    )


_UNSET = object()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m visual_intent_agent",
        description=(
            "Visual Intent Agent 最小 CLI（MVP v0.3 工程预览版，非正式发布）。"
        ),
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            "离线演示模式：确定性 Fake Provider，无需凭据、不访问网络。"
            "演示只做预设选项/关键词映射（脚本式演示），不是真实自然语言理解。"
        ),
    )
    parser.add_argument(
        "--db", default=None, help="SQLite 数据库路径（默认取 Settings / demo 默认）。"
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="生成图片输出目录（默认取 Settings / demo 默认）。",
    )
    parser.add_argument("--version", action="version", version=CLI_VERSION)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    console: Console | None = None,
    env: Any | None = None,
    env_file: Any = _UNSET,
) -> int:
    """CLI 入口。真实模式先加载 `Settings`；配置失败给出可读提示并返回退出码 2。"""
    args = parse_args(argv)
    output = console if console is not None else SystemConsole()
    resolved_env_file = DEFAULT_ENV_FILE if env_file is _UNSET else env_file

    if args.demo:
        output.print(
            "离线演示模式（--demo）：使用确定性 Fake Provider，不调用真实模型。"
        )
        output.print(
            "演示只识别预设选项与少量关键词（脚本式演示），不是真实自然语言理解；"
            "真实语义理解需要配置 Provider 后以默认模式运行。"
        )
        app = build_demo_app(db_path=args.db, output_dir=args.output_dir)
        return _run_app(app, output)

    try:
        settings = load_settings(env=env, env_file=resolved_env_file)
    except ConfigurationError as exc:
        output.print("")
        output.print(f"配置错误 [{type(exc).__name__}]: {exc}")
        output.print(
            "提示: 设置 VIA_PROVIDER_BASE_URL / VIA_PROVIDER_API_KEY 后重试，"
            "或使用 --demo 离线试用。"
        )
        return 2

    app = build_app(settings, db_path=args.db, output_dir=args.output_dir)
    return _run_app(app, output)


if __name__ == "__main__":  # pragma: no cover - 由 __main__.py 与测试覆盖
    raise SystemExit(main())
