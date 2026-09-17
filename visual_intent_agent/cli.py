"""MVP v0.4 工程预览版：最小 CLI 入口（Step 08 起，v0.4 Step 04 增加 RAG 开关）。

职责边界（任务书 08「实施内容」/「禁止范围」、v0.4 Step 04）：

- **只做接线与交互**：复用 `Settings` / `SQLiteRepository` / `WorkflowService` /
  `ReviewService` / `GenerationPipeline` / `PromptEngine`；
- **不复制领域规则**：状态判断只读取 `Repository.get_current_session_snapshot` 暴露的
  `workflow_state`；"下一步允许什么"完全由既有服务决定，CLI 不新增迁移；
- **不绕过硬门禁**：确认必须由用户在终端显式输入确认词触发，CLI 绝不自动确认；
  生成必经 `WorkflowService.confirm_current_intent` + `GenerationPipeline.generate`；
  任何修改都会产生新 `IntentRevision` 并使旧确认天然失效，必须重新确认；
- **不输出密钥**：不打印 `Settings`、`.env` 内容或 Authorization；只显示模型名、
  Revision / Artifact ID 与本地输出路径。

v0.4 Step 04（`04_cli_acceptance.md`）在本模块内新增**可选、默认关闭**的本地 RAG：

- `--rag` 显式开启；默认关闭时**完全不读取知识、不检索、不生成 Bundle**，旧流程逐字兼容；
- `--knowledge-dir` 只接受本地目录，**拒绝任何远程 URL、不自动下载模型**；
- 仅当开启 RAG 时才创建 `LocalKnowledgeEngine` 并注入 `PromptEngine`；若当前
  `PromptEngine` 尚不支持注入（Step 03 未接入），CLI **明确报错退出**，绝不以
  "无 RAG 继续运行"伪装启用成功；
- 语料路径/格式/版本错误在启动时安全可见（非零退出码），不会伪装成启用成功；
- 确认前展示"知识仅辅助明确委托项"，但**不改变确认哈希算法**；
- 生成后只读展示本次采用/回退/复用、受影响路径与 Bundle/知识单元来源 ID；明确说明
  这不代表用户确认过任何知识取值；
- `--demo --rag` 使用本模块内置、明确标注为演示/测试的 approved 夹具语料，绝不冒充
  生产人工审核或真实检索效果，也不修改 `knowledge_base/v0.4/` 的生产 draft。

交互流程（任务书 08「最小用户流程」）：

    需求 → 澄清（逐轮单问题）→ 展示 diff-first 摘要并要求人工确认
         → 生成并显示输出路径 → 接受或提交修改反馈 → 重新确认 → 生成下一版 → 退出

模式：

- 默认 **真实模式**：经 `load_settings()` 读取 `Settings`，使用真实
  `OpenAICompatibleLLMProvider` / `OpenAIImageProvider`；
- `--demo` **离线演示模式**：不需要任何凭据，使用确定性 `_DemoLLM` 与
  `FakeImageProvider`；演示逻辑只存在于本模块，属于预览版便利设施，不是产品能力，
  也不改变任何业务合同。

本模块**不修改**其它模块（knowledge 核心 / workflow / policy / evaluation / provider /
generation / PromptEngine / Repository / Realization 模型）。
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
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
from visual_intent_agent.knowledge import (
    KNOWLEDGE_RETRIEVAL_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    KNOWLEDGE_TOKENIZER_VERSION,
    AdoptionOutcome,
    BundleStatus,
    KnowledgeAdoptionDecision,
    KnowledgeBundle,
    KnowledgeCorpus,
    KnowledgeCorpusFile,
    KnowledgeError,
    KnowledgeManifest,
    KnowledgeUnit,
    LocalKnowledgeEngine,
    RetrievalOutcome,
    ReviewStatus,
    SourceType,
    compute_content_hash,
    load_corpus,
)
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
    PromptArtifact,
    PromptCompilationError,
    PromptEngine,
    QwenImageRenderer,
)
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_image import FakeImageProvider
from visual_intent_agent.providers.llm import LLMRequest, LLMResponse
from visual_intent_agent.providers.openai_image import OpenAIImageProvider
from visual_intent_agent.providers.openai_llm import OpenAICompatibleLLMProvider
from visual_intent_agent.realization.models import RealizationState, RealizationValue
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

__all__ = [
    "SessionApp",
    "SystemConsole",
    "main",
    "run_repl",
    "build_app",
    "build_demo_app",
    "parse_args",
    "build_knowledge_engine",
    "build_demo_knowledge_corpus",
    "resolve_knowledge_dir",
    "render_knowledge_report",
    "KnowledgeActivationError",
    "KnowledgeReport",
    "KnowledgeSourceInfo",
    "DEFAULT_KNOWLEDGE_DIR",
    "RETRY_READY",
    "RETRY_NO_PROMPT",
    "RETRY_PROMPT_EXPIRED",
]

#: 预览版标识（正式版本状态见 docs/releases/；本轮不声称正式发布）。
CLI_VERSION = (
    "visual-intent-agent MVP v0.4 engineering preview "
    "(optional local RAG, off by default; not a formal release)"
)

#: `--rag` 未给 `--knowledge-dir` 时的默认本地语料目录（生产 v0.4 语料，当前全 draft）。
DEFAULT_KNOWLEDGE_DIR: Path = PROJECT_ROOT / "knowledge_base" / "v0.4"

#: `--rag` 无法安全启用时的 CLI 稳定错误码（不伪装成功）。
CLI_KNOWLEDGE_DIR_REMOTE = "cli.knowledge_dir_remote"
CLI_KNOWLEDGE_DIR_INVALID = "cli.knowledge_dir_invalid"
CLI_KNOWLEDGE_RAG_UNSUPPORTED = "cli.rag_unsupported"
CLI_KNOWLEDGE_DEMO_CONFLICT = "cli.demo_knowledge_conflict"

#: PromptEngine 注入 KnowledgeEngine 的候选 keyword-only 参数名（Step 03 合同）。
_PROMPT_ENGINE_KNOWLEDGE_KWARGS: tuple[str, ...] = ("knowledge_engine", "knowledge")

#: FAILED 重试的**只读预检查**结果（不是授权；底层 `GenerationPipeline.retry` 仍是唯一门禁）：
#: 无已落库 Prompt / 历史 Prompt 绑定的确认已失效 / 可以复用同一 Prompt。
RETRY_READY = "ready"
RETRY_NO_PROMPT = "no_prompt"
RETRY_PROMPT_EXPIRED = "prompt_expired"

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
    KnowledgeError,
)

_PREVIEW_BANNER = (
    "Visual Intent Agent —— MVP v0.4 工程预览版（本地 RAG 可选、默认关闭；非正式发布）\n"
    "本预览版本轮未执行真实 smoke / 正式复评 / 人工盲评；只提供最小可操作闭环。\n"
    "知识库尚未人工批准时，--rag 只验证工程链路，不代表知识质量或真实收益。\n"
    "输入 exit 可随时退出；反馈与澄清阶段不会自动确认。"
)


# ---------------------------------------------------------------------------
# v0.4 Step 04：RAG 开关的错误/来源/展示数据（CLI 自有，不执行知识正文）
# ---------------------------------------------------------------------------


class KnowledgeActivationError(Exception):
    """`--rag` 无法安全启用时抛出。

    语义与 `KnowledgeError` 一致：**可见失败，绝不伪装启用成功**。`.code` 使用
    `cli.*` 命名空间；调用方（`main`）会打印稳定 code、返回非零退出码，并且
    不会退化成"无 RAG 静默继续"。
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - 便于统一渲染
        return self.message


@dataclass(frozen=True)
class KnowledgeSourceInfo:
    """本次启用的是**哪一个本地语料**（只读展示；不是授权，也不声称审核真实性）。"""

    label: str
    path: str | None
    corpus_version: str
    all_unit_count: int
    approved_unit_count: int
    is_demo: bool


@dataclass(frozen=True)
class KnowledgeReport:
    """生成后只读的"知识使用情况"快照（不含正文、不改状态、不构成授权）。

    - `adopted`：本次编译新采用的、带知识来源的 active Realization 值；
    - `reused`：本次编译**复用**的、来自历史编译的带知识来源 active 值；
    - `fallback_notes`：检索未采用/语料诊断的可读原因；
    - `bundles`：PromptArtifact 引用到的 Bundle 快照（含历史复用 Bundle）。
    """

    prompt_artifact_id: str | None
    source: KnowledgeSourceInfo
    bundles: tuple[KnowledgeBundle, ...] = ()
    adopted: tuple[RealizationValue, ...] = ()
    reused: tuple[RealizationValue, ...] = ()
    #: 编译器对检索推荐的最终裁定（持久化在 Bundle 内；旧 Bundle 可能为空）。
    adoption_decisions: tuple[KnowledgeAdoptionDecision, ...] = ()
    fallback_notes: tuple[str, ...] = ()
    note: str | None = None


#: `KnowledgeActivationError` 在 REPL 内也应给出可读提示（类定义之后才能加入）。
_HANDLED_ERRORS = _HANDLED_ERRORS + (KnowledgeActivationError,)


# ---------------------------------------------------------------------------
# v0.4 Step 04：本地知识语料的启用（默认关闭；拒绝远程链接）
# ---------------------------------------------------------------------------

#: 明确标注为演示/测试的 CLI 内置 approved 夹具（非生产审核、非真实检索效果）。
DEMO_KNOWLEDGE_CORPUS_VERSION = "v0.4-demo-1"
_DEMO_KNOWLEDGE_FILE = "demo.approved.jsonl"
_DEMO_KNOWLEDGE_REVIEWER = "cli-demo-fixture-not-a-human-review"
_DEMO_KNOWLEDGE_REVIEWED_AT = datetime(2026, 9, 16, tzinfo=timezone.utc)
_DEMO_KNOWLEDGE_SOURCE: dict[str, Any] = {
    "source_type": SourceType.PROJECT_ORIGINAL.value,
    "title": "CLI demo fixture (non-production; not a human review)",
    "repository_path": "visual_intent_agent/cli.py",
    "locator": "build_demo_knowledge_corpus() embedded fixture",
    "source_revision": DEMO_KNOWLEDGE_CORPUS_VERSION,
    "original_declaration": (
        "Project-original deterministic CLI demo/test fixture. NOT production-reviewed "
        "knowledge and NOT evidence of retrieval or image quality."
    ),
}

#: 三条演示单元：与 `_DemoLLM` 的确定性剧本配套（棚拍 / 写实场景）。
_DEMO_KNOWLEDGE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "knowledge_id": "demo.lighting.character.soft",
        "version": "v1",
        "content": "演示夹具：棚拍写实场景下，被委托的光线方向宜取柔和光以保持稳定观感。",
        "applicable_path": "lighting.character",
        "candidate_value": "soft",
        "keywords": ("photorealistic", "studio", "soft lighting"),
        "aliases": ("柔光", "soft"),
        "target_models": ("any",),
    },
    {
        "knowledge_id": "demo.composition.framing.medium_shot",
        "version": "v1",
        "content": "演示夹具：棚拍写实场景下，构图上中景通常比极端景别更稳妥。",
        "applicable_path": "composition.framing",
        "candidate_value": "medium_shot",
        "keywords": ("photorealistic", "studio", "medium shot"),
        "aliases": ("中景",),
        "target_models": ("any",),
    },
    {
        "knowledge_id": "demo.camera.depth_of_field.shallow",
        "version": "v1",
        "content": "演示夹具：棚拍写实人像可用浅景深突出主体，但这是场景相关建议而非通则。",
        "applicable_path": "camera.depth_of_field",
        "candidate_value": "shallow",
        "keywords": ("photorealistic", "depth of field", "shallow"),
        "aliases": ("浅景深",),
        "target_models": ("any",),
    },
)

_DEMO_CORPUS_CACHE: KnowledgeCorpus | None = None


def build_demo_knowledge_corpus() -> KnowledgeCorpus:
    """构造 CLI 内置的**演示/测试** approved 语料（内存、确定性、不写磁盘）。

    这不是生产知识库，也不修改 `knowledge_base/v0.4/` 的 9 条 draft；`reviewer` 字段
    明确写着"not-a-human-review"，绝不被当作人工审核证据。
    """
    global _DEMO_CORPUS_CACHE
    if _DEMO_CORPUS_CACHE is not None:
        return _DEMO_CORPUS_CACHE
    units: list[KnowledgeUnit] = []
    for spec in _DEMO_KNOWLEDGE_SPECS:
        payload = dict(spec)
        payload["content_hash"] = compute_content_hash(payload["content"])
        payload["source"] = dict(_DEMO_KNOWLEDGE_SOURCE)
        payload["review_status"] = ReviewStatus.APPROVED.value
        payload["reviewer"] = _DEMO_KNOWLEDGE_REVIEWER
        payload["reviewed_at"] = _DEMO_KNOWLEDGE_REVIEWED_AT
        units.append(KnowledgeUnit.model_validate(payload))
    jsonl = (
        "\n".join(
            json.dumps(unit.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            for unit in units
        )
        + "\n"
    )
    manifest = KnowledgeManifest(
        schema_version=KNOWLEDGE_SCHEMA_VERSION,
        corpus_version=DEMO_KNOWLEDGE_CORPUS_VERSION,
        tokenizer_version=KNOWLEDGE_TOKENIZER_VERSION,
        retrieval_version=KNOWLEDGE_RETRIEVAL_VERSION,
        files=(
            KnowledgeCorpusFile(
                path=_DEMO_KNOWLEDGE_FILE,
                sha256=sha256(jsonl.encode("utf-8")).hexdigest(),
                unit_count=len(units),
            ),
        ),
    )
    _DEMO_CORPUS_CACHE = KnowledgeCorpus(manifest=manifest, all_units=tuple(units))
    return _DEMO_CORPUS_CACHE


def _looks_remote(raw: str) -> bool:
    """URL/远程入口判定：任何 `scheme://` 或已知远程 scheme 前缀都拒绝。"""
    lowered = raw.strip().lower()
    if "://" in lowered:
        return True
    return lowered.startswith(
        ("http:", "https:", "ftp:", "ftps:", "sftp:", "s3:", "gs:", "ssh:")
    )


def resolve_knowledge_dir(
    raw: str | None, *, default: Path = DEFAULT_KNOWLEDGE_DIR
) -> Path:
    """把 `--knowledge-dir` 规范为**本地**目录（拒绝远程 URL；不联网、不下载）。"""
    if raw is None:
        return Path(default)
    text = raw.strip()
    if not text:
        raise KnowledgeActivationError(
            CLI_KNOWLEDGE_DIR_INVALID, "--knowledge-dir 需要非空本地目录路径"
        )
    if _looks_remote(text):
        raise KnowledgeActivationError(
            CLI_KNOWLEDGE_DIR_REMOTE,
            f"--knowledge-dir 只接受本地目录，拒绝远程 URL/入口 {text!r}；"
            "本版不读取任意远程链接，也不自动下载模型或语料",
        )
    return Path(text).expanduser()


def build_knowledge_engine(
    *, knowledge_dir: str | None, demo: bool
) -> tuple[LocalKnowledgeEngine, KnowledgeSourceInfo]:
    """启用 RAG 时构造 `LocalKnowledgeEngine` 与只读来源信息。

    - 生产/测试本地目录：**启动时**完整校验加载；路径/格式/版本错误立即抛出
      `KnowledgeError`/`KnowledgeActivationError`，绝不退化为"无 RAG 静默继续"；
    - `--demo`：只用内置演示/测试夹具，且拒绝 `--knowledge-dir`（避免拿未标注的
      外部目录冒充演示 approved 数据）。
    """
    if demo:
        if knowledge_dir is not None:
            raise KnowledgeActivationError(
                CLI_KNOWLEDGE_DEMO_CONFLICT,
                "--demo --rag 使用内置演示/测试 approved 夹具，不能同时指定 "
                "--knowledge-dir；如需使用本地语料，请去掉 --demo 改用真实模式",
            )
        corpus = build_demo_knowledge_corpus()
        info = KnowledgeSourceInfo(
            label="内置演示/测试 approved 夹具（非生产审核，不代表真实检索效果）",
            path=None,
            corpus_version=corpus.corpus_version,
            all_unit_count=corpus.all_unit_count,
            approved_unit_count=len(corpus.build_units()),
            is_demo=True,
        )
        return LocalKnowledgeEngine(corpus=corpus), info

    path = resolve_knowledge_dir(knowledge_dir)
    if not path.is_dir():
        raise KnowledgeActivationError(
            CLI_KNOWLEDGE_DIR_INVALID,
            f"--knowledge-dir {path} 不是已存在的本地目录",
        )
    corpus = load_corpus(path)
    info = KnowledgeSourceInfo(
        label=f"本地审核语料目录 {path}",
        path=str(path),
        corpus_version=corpus.corpus_version,
        all_unit_count=corpus.all_unit_count,
        approved_unit_count=len(corpus.build_units()),
        is_demo=False,
    )
    return LocalKnowledgeEngine(corpus=corpus), info


def _knowledge_engine_kwarg(engine_cls: type) -> str | None:
    """探测 `PromptEngine.__init__` 是否支持注入 KnowledgeEngine（Step 03 合同）。"""
    try:
        parameters = inspect.signature(engine_cls.__init__).parameters
    except (TypeError, ValueError):  # pragma: no cover - 内建/扩展对象
        return None
    for name in _PROMPT_ENGINE_KNOWLEDGE_KWARGS:
        if name in parameters:
            return name
    return None


def build_prompt_engine(
    repo: Repository, *, knowledge_engine: Any | None = None
) -> PromptEngine:
    """构造 `PromptEngine`；仅当 RAG 开启时才注入 KnowledgeEngine。

    当前 `PromptEngine` 尚未接入注入参数时**明确失败**：CLI 绝不以"无 RAG 继续运行"
    伪装 `--rag` 已生效。
    """
    renderer = QwenImageRenderer()
    if knowledge_engine is None:
        return PromptEngine(renderer, repo)
    kwarg = _knowledge_engine_kwarg(PromptEngine)
    if kwarg is None:
        raise KnowledgeActivationError(
            CLI_KNOWLEDGE_RAG_UNSUPPORTED,
            "当前 PromptEngine 构造不支持注入 KnowledgeEngine（Step 03 尚未接入）；"
            "--rag 未启用，且不会以无 RAG 静默继续",
        )
    return PromptEngine(renderer, repo, **{kwarg: knowledge_engine})


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
        knowledge_source: KnowledgeSourceInfo | None = None,
    ) -> None:
        self._repo = repo
        self._workflow = workflow
        self._review = review
        self._pipeline = pipeline
        self._settings = settings
        self._knowledge_source = knowledge_source

    # -- 构造 ---------------------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        db_path: Path | str | None = None,
        output_dir: Path | str | None = None,
        knowledge_engine: Any | None = None,
        knowledge_source: KnowledgeSourceInfo | None = None,
    ) -> "SessionApp":
        """真实模式：真实 LLM / 图像 adapter + `Settings` 中的路径。"""
        return cls._assemble(
            settings=settings,
            repo=SQLiteRepository(db_path if db_path is not None else settings.db_path),
            llm=OpenAICompatibleLLMProvider(settings),
            image=OpenAIImageProvider(settings),
            output_dir=Path(output_dir) if output_dir is not None else settings.output_dir,
            knowledge_engine=knowledge_engine,
            knowledge_source=knowledge_source,
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
        knowledge_engine: Any | None = None,
        knowledge_source: KnowledgeSourceInfo | None = None,
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
            prompt_engine=build_prompt_engine(repo, knowledge_engine=knowledge_engine),
            image_provider=image,
            output_dir=output_dir,
        )
        return cls(
            repo=repo,
            workflow=workflow,
            review=review,
            pipeline=pipeline,
            settings=settings,
            knowledge_source=knowledge_source,
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
        """FAILED 重试是否可行：最新已落库 PromptArtifact 存在**且其确认仍有效**。

        只检查"本会话最新已落库 PromptArtifact"这一条：修改 Intent 后第二次生成失败时，
        历史 `GenerationArtifact` 属于上一版，不能据此判断；编译失败等情况没有可复用
        Prompt，`retry` 只会得到 `generation.artifact_not_found`。

        这里**不复制**确认判定规则：一律调用 `Repository.is_confirmation_valid(...)`。
        预检查与真正 `retry` 之间状态可能变化，届时仍由底层
        `GenerationPipeline.retry` 按同一门禁拒绝（预检查不是授权）。
        """
        return self.retry_status(session_id) == RETRY_READY

    def retry_status(self, session_id: str) -> str:
        """FAILED 重试的只读预检查结果（`RETRY_READY` / `RETRY_NO_PROMPT` / `RETRY_PROMPT_EXPIRED`）。

        - 无已落库 PromptArtifact（编译失败 / 从未编译）→ `RETRY_NO_PROMPT`；
        - 存在但 payload 的 `based_on_confirmation_id` 按 Repository 判定已失效
          （例如修改 Intent 后旧确认天然失效）→ `RETRY_PROMPT_EXPIRED`；
        - 否则 `RETRY_READY`。

        只读、不写状态、不自动重新确认 / 重新 compile；仅用于给出准确提示。
        """
        stored = self._repo.get_latest_prompt_artifact(session_id)
        if stored is None:
            return RETRY_NO_PROMPT
        artifact = PromptArtifact.model_validate_json(stored.payload)
        if not self._repo.is_confirmation_valid(artifact.based_on_confirmation_id):
            return RETRY_PROMPT_EXPIRED
        return RETRY_READY

    def close(self) -> None:
        close = getattr(self._repo, "close", None)
        if callable(close):
            close()

    # -- 只读展示辅助 -------------------------------------------------------

    @property
    def target_model(self) -> str:
        return self._settings.image_model

    @property
    def rag_enabled(self) -> bool:
        return self._knowledge_source is not None

    @property
    def knowledge_source(self) -> KnowledgeSourceInfo | None:
        return self._knowledge_source

    def knowledge_report(
        self, session_id: str, *, prompt_artifact_id: str | None = None
    ) -> KnowledgeReport | None:
        """只读解析最新 `PromptArtifact` + 引用的 Bundle + active Realization。

        RAG 未开启时返回 `None`（不读取任何知识相关记录）。本方法只读、不写状态、
        不调用检索；即使 `PromptEngine` 尚未写入追溯字段也如实显示"未记录 Bundle"。
        """
        if self._knowledge_source is None:
            return None
        artifact: PromptArtifact | None = None
        if prompt_artifact_id is not None:
            artifact = PromptArtifact.model_validate_json(
                self._repo.get_prompt_artifact(prompt_artifact_id).payload
            )
        else:
            stored = self._repo.get_latest_prompt_artifact(session_id)
            if stored is not None:
                artifact = PromptArtifact.model_validate_json(stored.payload)

        bundles: list[KnowledgeBundle] = []
        if artifact is not None:
            for ref in artifact.knowledge_bundle_refs:
                try:
                    bundles.append(
                        KnowledgeBundle.model_validate_json(
                            self._repo.get_knowledge_bundle(ref).payload
                        )
                    )
                except RepositoryError:
                    # 只读展示：缺失引用如实跳过，绝不编造来源。
                    continue

        fallback_notes: list[str] = []
        decisions: list[KnowledgeAdoptionDecision] = []
        for bundle in bundles:
            decisions.extend(bundle.adoption_decisions)
            if bundle.status is not BundleStatus.OK:
                fallback_notes.append(
                    f"bundle {bundle.bundle_id}: {bundle.status.value}"
                    f"（{bundle.reason_code or 'no_reason'}）"
                )
                continue
            for result in bundle.path_results:
                if result.outcome is not RetrievalOutcome.ADOPTED:
                    fallback_notes.append(
                        f"{result.path}: {result.outcome.value}（{result.reason_code}）"
                    )
            # 编译器实际裁定：明确区分"检索器推荐"与"编译器采用/拒绝"。
            for decision in bundle.adoption_decisions:
                if decision.outcome is AdoptionOutcome.REJECTED:
                    fallback_notes.append(
                        f"{decision.path}: 编译复核拒绝推荐 {decision.candidate_value!r}"
                        f"（{decision.reason_code.value if decision.reason_code else 'rejected'}）"
                    )

        adopted: list[RealizationValue] = []
        reused: list[RealizationValue] = []
        state_stored = self._repo.get_current_realization_state(session_id)
        if state_stored is not None:
            state = RealizationState.model_validate_json(state_stored.payload)
            for value in state.active_values():
                if value.knowledge_unit_id is None:
                    continue
                if (
                    artifact is not None
                    and value.first_prompt_artifact_id == artifact.prompt_artifact_id
                ):
                    adopted.append(value)
                else:
                    reused.append(value)

        note = None
        if not bundles:
            note = (
                "本次编译未记录任何知识 Bundle：没有采用知识"
                "（可能未产生检索，或知识追溯字段尚不可用）。"
            )
        return KnowledgeReport(
            prompt_artifact_id=None if artifact is None else artifact.prompt_artifact_id,
            source=self._knowledge_source,
            bundles=tuple(bundles),
            adopted=tuple(adopted),
            reused=tuple(reused),
            adoption_decisions=tuple(decisions),
            fallback_notes=tuple(fallback_notes),
            note=note,
        )


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


def render_summary(
    console: Console, summary: ConfirmationSummary, *, rag_enabled: bool = False
) -> None:
    """diff-first 确认摘要（六要素；只读展示，不代替用户确认）。

    `rag_enabled` 只追加一行**说明文字**：确认哈希算法与摘要内容不变，用户确认的
    仍是这里展示的 Intent 与委托范围，而不是任何知识取值。
    """
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
    if rag_enabled:
        console.print(
            "知识说明: 知识仅辅助明确委托项；用户确认的是以上 Intent 与委托范围，"
            "具体实现由系统在该范围内决定，知识不会覆盖用户指定值或 PIN。"
        )


def _known_paths() -> tuple[str, ...]:
    from visual_intent_agent.domain import INTENT_PATHS

    return tuple(sorted(INTENT_PATHS))


def render_generation(
    console: Console,
    artifact: GenerationArtifact,
    *,
    knowledge_report: KnowledgeReport | None = None,
) -> None:
    """展示生成文件路径与 Artifact ID（不输出任何凭据）。

    `knowledge_report` 非空时追加只读的"知识使用情况"；内容不改变生成结果。
    """
    console.print("")
    console.print("—— 生成结果 ——")
    console.print(f"generation_id: {artifact.generation_id}")
    console.print(f"prompt_artifact_id: {artifact.prompt_artifact_id}")
    console.print(f"model_version: {artifact.model_version}")
    console.print(f"size: {artifact.parameters.size}")
    for ref in artifact.output_refs:
        local = (Path(PROJECT_ROOT) / ref.path).resolve()
        console.print(f"输出文件: {local}  ({ref.mime_type}, {ref.byte_size} bytes)")
    if knowledge_report is not None:
        render_knowledge_report(console, knowledge_report)


def render_knowledge_report(console: Console, report: KnowledgeReport) -> None:
    """只读展示本次采用/回退/复用、路径与 Bundle/知识来源 ID。

    明确声明：这不代表用户确认过任何具体知识取值，也不占用任何授权来源。
    """
    console.print("")
    console.print("—— 知识使用情况（RAG 已开启）——")
    console.print(
        "说明: 以下知识仅辅助被明确委托的项，不代表用户确认过任何知识取值，"
        "也不改变用户确认的 Intent；完整来源可按 bundle_id 从 Repository 查回。"
    )
    source = report.source
    console.print(f"知识来源: {source.label}")
    console.print(
        f"语料版本: {source.corpus_version}"
        f"（approved {source.approved_unit_count}/{source.all_unit_count} 条）"
    )
    if report.note:
        console.print(f"提示: {report.note}")
    if report.adopted:
        console.print("本次采用 (adopted):")
        for value in report.adopted:
            console.print(
                f"  - {value.path} = {value.value!r}  ← {value.knowledge_unit_id} "
                f"({value.knowledge_unit_version}, bundle={value.knowledge_bundle_id})"
            )
    else:
        console.print("本次采用 (adopted): （无）")
    if report.reused:
        console.print("复用已有实现 (reused，来自历史编译):")
        for value in report.reused:
            console.print(
                f"  - {value.path} = {value.value!r}  ← {value.knowledge_unit_id} "
                f"({value.knowledge_unit_version}, bundle={value.knowledge_bundle_id})"
            )
    if report.fallback_notes:
        console.print("回退 (fallback):")
        for note in report.fallback_notes:
            console.print(f"  - {note}")
    elif not report.adopted and not report.reused:
        console.print("回退 (fallback): 本次没有采用任何知识；与无 RAG 的固定候选回退一致。")
    if report.bundles:
        console.print("Bundle / 来源 ID:")
        for bundle in report.bundles:
            fingerprint = bundle.corpus_fingerprint or "n/a"
            console.print(
                f"  - {bundle.bundle_id}  status={bundle.status.value} "
                f"corpus={bundle.corpus_version or 'n/a'} fingerprint={fingerprint[:12]}…"
            )
            for recommendation in bundle.recommendations:
                # 只报告"检索器推荐了什么"，并附上持久化的编译器裁定：
                # adopted 才算实际采用；rejected/无记录绝不显示成 adopted。
                decision = bundle.decision_for(recommendation.path)
                if decision is None:
                    verdict = "编译器未记录裁定（旧 Bundle，不作为已采用）"
                elif decision.outcome is AdoptionOutcome.ADOPTED:
                    verdict = "编译器已采用 (adopted)"
                else:
                    code = decision.reason_code.value if decision.reason_code else "rejected"
                    verdict = f"编译器未采用 (rejected: {code})"
                console.print(
                    f"      · [检索器推荐] {recommendation.path} = "
                    f"{recommendation.candidate_value!r}"
                    f"  ← {recommendation.knowledge_id} ({recommendation.version}, "
                    f"score={recommendation.score:.2f}) — {verdict}"
                )


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
            "本会话若没有可复用的已落库 Prompt、或其绑定确认已失效，都无法 retry，"
            "只能 exit 后新建会话。"
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
    if isinstance(exc, KnowledgeActivationError) or (
        isinstance(code, str) and code.startswith("knowledge.")
    ):
        return (
            "本地知识语料未启用（--rag 未生效）：请修正 --knowledge-dir 指向的本地目录，"
            "或移除 --rag；不会以无 RAG 静默继续。"
        )
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
            render_summary(console, summary, rag_enabled=app.rag_enabled)
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
                render_generation(
                    console,
                    artifact,
                    knowledge_report=app.knowledge_report(
                        session_id, prompt_artifact_id=artifact.prompt_artifact_id
                    ),
                )
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
                render_generation(
                    console,
                    artifact,
                    knowledge_report=app.knowledge_report(
                        session_id, prompt_artifact_id=artifact.prompt_artifact_id
                    ),
                )
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
            status = app.retry_status(session_id)
            if status != RETRY_READY:
                # 两种情况分别如实说明：从未编译出 Prompt vs 历史 Prompt 绑定的确认已失效。
                # 都不能直接重新确认，也不能把"已过期"错误描述成"从未编译"。
                if status == RETRY_PROMPT_EXPIRED:
                    console.print(
                        "上次生成失败（FAILED），且本会话最新已落库 Prompt 绑定的确认已失效"
                        "（历史 Prompt 已过期）。"
                    )
                else:
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
            render_generation(
                console,
                artifact,
                knowledge_report=app.knowledge_report(
                    session_id, prompt_artifact_id=artifact.prompt_artifact_id
                ),
            )
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
    rag: bool = False,
    knowledge_dir: str | None = None,
) -> SessionApp:
    """真实模式装配（可注入 settings/env 便于测试配置错误路径）。

    `rag=True` 时**先**完整加载本地语料；路径/格式/版本错误立即抛出，绝不退化为
    无 RAG 静默继续。默认 `rag=False`，旧调用逐字兼容。
    """
    knowledge_engine: Any | None = None
    knowledge_source: KnowledgeSourceInfo | None = None
    if rag:
        knowledge_engine, knowledge_source = build_knowledge_engine(
            knowledge_dir=knowledge_dir, demo=False
        )
    return SessionApp.from_settings(
        settings,
        db_path=db_path,
        output_dir=output_dir,
        knowledge_engine=knowledge_engine,
        knowledge_source=knowledge_source,
    )


def build_demo_app(
    *,
    db_path: Path | str | None = None,
    output_dir: Path | str | None = None,
    rag: bool = False,
    knowledge_dir: str | None = None,
) -> SessionApp:
    """离线演示装配：不需要任何凭据，不访问网络。

    `--demo --rag` 只使用内置、明确标注为演示/测试的 approved 夹具；拒绝同时指定
    `--knowledge-dir`（避免拿外部语料冒充演示审核数据）。默认 `rag=False` 兼容旧调用。
    """
    knowledge_engine: Any | None = None
    knowledge_source: KnowledgeSourceInfo | None = None
    if rag:
        knowledge_engine, knowledge_source = build_knowledge_engine(
            knowledge_dir=knowledge_dir, demo=True
        )
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
        knowledge_engine=knowledge_engine,
        knowledge_source=knowledge_source,
    )


_UNSET = object()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m visual_intent_agent",
        description=(
            "Visual Intent Agent 最小 CLI（MVP v0.4 工程预览版；本地 RAG 可选、"
            "默认关闭；非正式发布）。"
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
        "--rag",
        action="store_true",
        default=False,
        help=(
            "显式开启本地知识检索（默认关闭）。只读取本地 JSONL 语料，"
            "不访问远程 URL、不自动下载模型；未开启时完全不读取知识。"
        ),
    )
    parser.add_argument(
        "--knowledge-dir",
        default=None,
        help=(
            "本地受审核知识目录（JSONL + manifest.json）；默认 "
            f"{DEFAULT_KNOWLEDGE_DIR}。必须是本地路径，远程 URL 一律拒绝。"
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


def _render_knowledge_error(output: Console, exc: BaseException) -> None:
    """安全、可见的知识/RAG 错误：稳定 code + 明确"未启用"（不伪装成功）。"""
    output.print("")
    if isinstance(exc, KnowledgeActivationError):
        output.print(f"知识/RAG 错误 [{exc.code}]: {exc.message}")
    else:
        code = getattr(exc, "code", None) or type(exc).__name__
        output.print(f"知识/RAG 错误 [{code}]: {exc}")
    output.print(
        "提示: --rag 未生效，本次不会以无 RAG 静默继续；请修正本地知识目录"
        "（或去掉 --rag / --knowledge-dir）后重试。"
    )


def _print_rag_status(output: Console, app: SessionApp) -> None:
    """打印已启用 RAG 的真实来源与 approved 计数（0 approved 时明确回退）。"""
    source = app.knowledge_source
    if source is None:
        return
    output.print(f"RAG 已开启：{source.label}")
    output.print(
        f"语料版本 {source.corpus_version}；单元 {source.all_unit_count} 条"
        f"（approved {source.approved_unit_count} 条）。"
    )
    if source.approved_unit_count == 0:
        output.print(
            "提示: 当前语料没有 approved 单元，RAG 将按无命中透明回退，"
            "不会采用任何知识。"
        )
    output.print(
        "说明: 知识仅辅助明确委托项，不能覆盖用户指定值或 PIN，"
        "也不代表用户确认过具体知识取值。"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    console: Console | None = None,
    env: Any | None = None,
    env_file: Any = _UNSET,
) -> int:
    """CLI 入口。真实模式先加载 `Settings`；配置失败给出可读提示并返回退出码 2。

    `--rag` 的语料错误同样以可读提示 + 退出码 2 结束；绝不会在没有知识的情况下
    假装 RAG 已启用。
    """
    args = parse_args(argv)
    output = console if console is not None else SystemConsole()
    resolved_env_file = DEFAULT_ENV_FILE if env_file is _UNSET else env_file

    # `--knowledge-dir` 一旦提供就先做形态校验（拒绝远程 URL），与是否开启 RAG 无关。
    if args.knowledge_dir is not None:
        try:
            resolve_knowledge_dir(args.knowledge_dir)
        except KnowledgeActivationError as exc:
            _render_knowledge_error(output, exc)
            return 2
        if not args.rag:
            output.print(
                "提示: 已提供 --knowledge-dir，但 --rag 未开启（默认关闭）；"
                "本次不读取任何知识目录。"
            )

    if args.demo:
        output.print(
            "离线演示模式（--demo）：使用确定性 Fake Provider，不调用真实模型。"
        )
        output.print(
            "演示只识别预设选项与少量关键词（脚本式演示），不是真实自然语言理解；"
            "真实语义理解需要配置 Provider 后以默认模式运行。"
        )
        if args.rag:
            output.print(
                "演示模式 RAG（--demo --rag）：使用内置演示/测试 approved 夹具语料；"
                "它不是生产人工审核结果，也不代表真实检索或图片效果。"
            )
        try:
            app = build_demo_app(
                db_path=args.db,
                output_dir=args.output_dir,
                rag=args.rag,
                knowledge_dir=args.knowledge_dir,
            )
        except (KnowledgeError, KnowledgeActivationError) as exc:
            _render_knowledge_error(output, exc)
            return 2
        _print_rag_status(output, app)
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

    try:
        app = build_app(
            settings,
            db_path=args.db,
            output_dir=args.output_dir,
            rag=args.rag,
            knowledge_dir=args.knowledge_dir,
        )
    except (KnowledgeError, KnowledgeActivationError) as exc:
        _render_knowledge_error(output, exc)
        return 2
    _print_rag_status(output, app)
    return _run_app(app, output)


if __name__ == "__main__":  # pragma: no cover - 由 __main__.py 与测试覆盖
    raise SystemExit(main())
