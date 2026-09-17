"""MVP v0.6 Step 02：最小可复现 B/C 配对执行器（离线优先、fail-closed）。

对每个 `case × repetition × arm`：

- 独立新 SQLite + 新会话（`cases/<case_id>/rep<N>/<arm>/session.db`），无 active
  Realization；不先 B 后 C 复用会话，不跨库复制 Prompt / Bundle / 确认记录；
- 用 `context.seed_confirmed_session` 写入**固定且两侧相同**的 `intent_revision_id`
  （单次 `case_id+repetition` 哈希，不搜索、不挑 ID），并经
  `WorkflowService.confirm_current_intent` 建立各自独立的合法确认绑定；
- B = `PromptEngine(knowledge_engine=None)`；C = 注入
  `LocalKnowledgeEngine(knowledge_base/v0.5)`，其余条件（Intent / Execution /
  目标模型 / 尺寸 / Provider 构造参数）逐项相同；
- 预算守卫在请求**发出前**持久化最坏预留（覆盖 adapter 内部重试），timeout / 网络 /
  进程中断标 `unknown`，绝不自动重发；账本与记录只追加，续跑不覆盖已有成功结果。

真实入口 `fail-closed`：授权、formal clean 身份、冻结哈希、知识快照、可追溯模型身份、
预算与阈值任一缺失即拒绝；**本步骤不构造任何真实 Provider**（G1 后由 Step 03 接线）。

默认 `--provider fake`：FakeImageProvider + Fake，全程零网络。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from visual_intent_agent.config import PROJECT_ROOT, Settings
from visual_intent_agent.generation import (
    GenerationArtifact,
    GenerationError,
    GenerationPipeline,
)
from visual_intent_agent.knowledge import KNOWLEDGE_PATHS, KnowledgeBundle
from visual_intent_agent.persistence import RepositoryError, SQLiteRepository
from visual_intent_agent.prompt_engine import (
    PromptArtifact,
    PromptCompilationError,
    PromptEngine,
    QwenImageRenderer,
)
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.image import (
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageProvider,
)
from visual_intent_agent.realization.models import RealizationState

from evaluation.identity import (
    IDENTITY_DIAGNOSTIC,
    IDENTITY_FORMAL,
    CodeIdentity,
    IdentityError,
    enforce_formal_identity,
    resolve_code_identity,
)
from evaluation.manifest import load_manifest, sha256_file

from .budget import (
    LEDGER_FILENAME,
    BudgetExceededError,
    BudgetLedger,
    BudgetPolicy,
    budget_policy_from_config,
)
from .context import (
    CALL_ORDER_RULE,
    REVISION_SEED_RULE,
    ConfirmedContext,
    arm_knowledge_engine,
    call_order_for,
    execution_revision_id_for,
    knowledge_fingerprint,
    knowledge_manifest_sha256,
    load_annotations,
    load_cases,
    load_knowledge_corpus,
    revision_seed_for,
    seed_confirmed_session,
    session_id_for,
)
from .records import (
    BC_HARNESS_VERSION,
    BC_ID_SCHEME_VERSION,
    CALL_RECORD_ID_PREFIX,
    DECISION_ID_PREFIX,
    PAIR_RECORD_ID_PREFIX,
    RUN_ID_PREFIX,
    AnnotationSpec,
    Arm,
    CaseSpec,
    DecisionRequired,
    ErrorRecord,
    PairRecord,
    PairedRunResult,
    ProviderCallRecord,
    RetrievalTrace,
    RunKnowledgeSnapshot,
    RunProviderSnapshot,
    RunRecordV06,
    RunSummaryV06,
    dump_json,
    error_record,
    make_record_id,
    sha256_text,
    utc_now,
)
from .reporting import (
    DECISIONS_FILENAME,
    RECORDS_FILENAME,
    TruncatedRecordsError,
    append_jsonl,
    planned_counts_by_layer,
    read_records,
    summarize,
    write_json,
)

# ---------------------------------------------------------------------------
# 默认路径与常量
# ---------------------------------------------------------------------------

DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "evaluation" / "v0_6" / "frozen_manifest.json"
DEFAULT_CASES_PATH = PROJECT_ROOT / "evaluation" / "v0_6" / "cases.jsonl"
DEFAULT_ANNOTATIONS_PATH = PROJECT_ROOT / "evaluation" / "v0_6" / "annotations.jsonl"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "evaluation" / "v0_6" / "run_config.json"
DEFAULT_PROTOCOL_PATH = PROJECT_ROOT / "evaluation" / "v0_6" / "protocol.md"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "evaluation_runs_v0_6"

#: 评测层错误 code（命名空间 `evaluation.*`）。
EVAL_BUDGET_EXHAUSTED = "evaluation.budget_exhausted"
EVAL_UNKNOWN_REQUEST = "evaluation.unknown_request_result"
EVAL_FAILED_BEFORE_RECORD = "evaluation.failed_before_record"
EVAL_CONSTRAINT_VIOLATION = "evaluation.constraint_violation"

#: 结果未知的 Provider 错误（可能已被上游接受/计费）。
UNKNOWN_PROVIDER_CODES = frozenset(
    {"provider.timeout", "provider.network", "provider.server_error"}
)
#: 确定未被接受的 Provider 错误（不计费；是否重试仍受冻结策略约束）。
DETERMINISTIC_PROVIDER_CODES = frozenset({"provider.auth", "provider.invalid_request"})

#: 真实启动的冻结阈值状态。
REQUIRED_THRESHOLD_STATUS = "confirmed"
#: 真实启动必须逐项非空且合法的四项预注册阈值。
REQUIRED_THRESHOLD_FIELDS: tuple[str, ...] = (
    "minimum_meaningful_gain",
    "allowed_completion_rate_difference",
    "max_latency_seconds",
    "max_cost",
)
#: 允许的离线 factory 标识：`dry_run` 只接受显式离线/测试 factory，防止真实 factory 绕过门禁。
OFFLINE_FACTORY_MODES: frozenset[str] = frozenset({"fake", "offline", "test"})


class PairedRunBlockedError(ValueError):
    """B/C Run 前置条件未满足（真实入口 fail-closed）。"""


# ---------------------------------------------------------------------------
# 记录包装器（只观察、不改行为）
# ---------------------------------------------------------------------------


class _RawImageCall:
    __slots__ = ("request", "response", "error", "latency_ms")

    def __init__(
        self,
        *,
        request: ImageGenerationRequest,
        response: ImageGenerationResult | None = None,
        error: ProviderError | None = None,
        latency_ms: float,
    ) -> None:
        self.request = request
        self.response = response
        self.error = error
        self.latency_ms = latency_ms


class RecordingImageProvider:
    """`ImageProvider` 记录包装器：逐次记录请求面 / 响应 / 延迟 / 错误。

    只包装、不重试、不改写；adapter 内部重试不可见，调用次数由外层预算账本覆盖。
    """

    def __init__(
        self,
        wrapped: ImageProvider,
        *,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._wrapped = wrapped
        self._monotonic = monotonic or time.perf_counter
        self.calls: list[_RawImageCall] = []

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        started = self._monotonic()
        try:
            result = self._wrapped.generate(request)
        except ProviderError as exc:
            self.calls.append(
                _RawImageCall(
                    request=request,
                    error=exc,
                    latency_ms=round((self._monotonic() - started) * 1000.0, 3),
                )
            )
            raise
        self.calls.append(
            _RawImageCall(
                request=request,
                response=result,
                latency_ms=round((self._monotonic() - started) * 1000.0, 3),
            )
        )
        return result


def redact_endpoint(url: str | None) -> str | None:
    """去除 endpoint 的敏感查询参数（真实运行记录用；Step 02 恒为 None）。"""
    if not url:
        return None
    return url.split("?", 1)[0]


def _read_intent_value(intent: Any, path: str) -> Any:
    """按白名单路径只读读取 Intent 值（与 PromptEngine 同语义，不改状态）。"""
    facet_name, _, field_name = path.partition(".")
    facet = getattr(intent, facet_name, None)
    return None if facet is None else getattr(facet, field_name, None)


# ---------------------------------------------------------------------------
# 真实入口 fail-closed 门禁（纯函数，便于离线测试）
# ---------------------------------------------------------------------------


def real_run_blockers(
    *,
    config: dict[str, Any],
    identity: CodeIdentity | None,
    settings: Settings,
    budget: BudgetPolicy,
    manifest_verified: bool,
    knowledge_verified: bool,
) -> list[str]:
    """返回拒绝真实启动的全部原因（空列表 = 全部通过）。"""
    blockers: list[str] = []

    if not config.get("real_run_authorized"):
        blockers.append(
            "config.real_run_authorized is not true (G1 authorization not recorded)"
        )
    authorization = config.get("authorization") or {}
    if authorization.get("status") != "granted":
        blockers.append(
            "config.authorization.status must be exactly 'granted' "
            f"(found {authorization.get('status')!r}); a missing/blank authorization is refused"
        )

    if identity is None:
        blockers.append("a formal CodeIdentity is required for a real run")
    elif not isinstance(identity, CodeIdentity):
        blockers.append(
            f"identity must be an evaluation.identity.CodeIdentity, got {type(identity).__name__}"
        )
    else:
        if identity.mode != IDENTITY_FORMAL:
            blockers.append(
                f"identity.mode={identity.mode!r} is not 'formal' "
                "(diagnostic mode is not gate evidence)"
            )
        try:
            enforce_formal_identity(identity)
        except IdentityError as exc:
            blockers.append(f"identity gate: {exc}")

    if not manifest_verified:
        blockers.append(
            "frozen manifest hashes are not verified (protocol/config/dataset/annotations/"
            "knowledge must match the frozen manifest)"
        )
    if not knowledge_verified:
        blockers.append(
            "knowledge snapshot is not verified (corpus version/fingerprint/manifest sha256)"
        )

    providers = config.get("providers") or {}
    llm_provider = providers.get("llm") or {}
    if not llm_provider.get("model_identity_verified"):
        blockers.append(
            "config.providers.llm.model_identity_verified is not true "
            "(any real LLM call / probe must use a traceable model identity)"
        )
    llm_timeout = llm_provider.get("timeout_seconds")
    if llm_timeout is None:
        blockers.append("config.providers.llm.timeout_seconds is null")
    elif float(llm_timeout) != float(settings.llm_timeout_seconds):
        blockers.append(
            f"settings.llm_timeout_seconds={settings.llm_timeout_seconds} does not match "
            f"the frozen config {llm_timeout}"
        )
    llm_retries = llm_provider.get("max_retries")
    if llm_retries is None:
        blockers.append("config.providers.llm.max_retries is null")
    elif int(llm_retries) != int(settings.http_max_retries):
        blockers.append(
            f"settings.http_max_retries={settings.http_max_retries} does not match the "
            f"frozen LLM config {llm_retries}"
        )
    llm_model = llm_provider.get("model") or llm_provider.get("model_default")
    if not isinstance(llm_model, str) or not llm_model.strip():
        blockers.append(
            "config.providers.llm.model is missing; a traceable frozen LLM model identity "
            "is required for any real LLM call / probe (forward-compatible G1 config field)"
        )
    elif llm_model != settings.llm_model:
        blockers.append(
            f"settings.llm_model={settings.llm_model!r} does not match the frozen LLM "
            f"config model {llm_model!r}"
        )

    image_provider = providers.get("image") or {}
    if not image_provider.get("model_identity_verified"):
        blockers.append(
            "config.providers.image.model_identity_verified is not true "
            "(traceable model identity required)"
        )
    expected_model = image_provider.get("model_default")
    if isinstance(expected_model, str) and settings.image_model != expected_model:
        blockers.append(
            f"settings.image_model={settings.image_model!r} does not match the frozen "
            f"config model {expected_model!r}"
        )
    timeout_seconds = image_provider.get("timeout_seconds")
    if timeout_seconds is None:
        blockers.append("config.providers.image.timeout_seconds is null")
    elif float(timeout_seconds) != float(settings.image_timeout_seconds):
        blockers.append(
            f"settings.image_timeout_seconds={settings.image_timeout_seconds} does not match "
            f"the frozen config {timeout_seconds}"
        )
    max_retries = image_provider.get("max_retries")
    if max_retries is None:
        blockers.append("config.providers.image.max_retries is null")
    elif int(max_retries) != int(settings.http_max_retries):
        blockers.append(
            f"settings.http_max_retries={settings.http_max_retries} does not match the "
            f"frozen config {max_retries}"
        )

    missing_budget = budget.missing_fields()
    if missing_budget:
        blockers.append(
            "budget is incomplete; missing fields: " + ", ".join(missing_budget)
        )
    if not budget.unknown_counts_as_consumed:
        blockers.append("budget.unknown_cost_requests_counted_as_consumed must be true")

    thresholds = config.get("thresholds") or {}
    if thresholds.get("status") != REQUIRED_THRESHOLD_STATUS:
        blockers.append(
            f"config.thresholds.status={thresholds.get('status')!r} is not "
            f"{REQUIRED_THRESHOLD_STATUS!r} (freeze thresholds before a real run)"
        )
    for field in REQUIRED_THRESHOLD_FIELDS:
        value = thresholds.get(field)
        if value is None:
            blockers.append(f"config.thresholds.{field} is null (freeze it before a real run)")
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            blockers.append(
                f"config.thresholds.{field}={value!r} is not a non-negative number"
            )
    return blockers


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class PairedRunner:
    """B/C 配对执行器。

    构造参数：

    - `settings`：目标模型 / 超时 / 重试的唯一来源；
    - `image_factory`：每次（arm × 重复）取一个全新图片 Provider（Fake 或 G1 后接线）；
    - `output_root`：Run 产物根目录（默认 `outputs/evaluation_runs_v0_6`）；
    - `dry_run`：默认 True（Fake、零网络）；False 时执行 fail-closed 真实门禁且**本步
      不构造真实 Provider**；
    - `factory_mode`：调用方声明的 factory 性质（`fake` / `offline` / `test` / `real` /
      `injected`）。`dry_run` 只接受 {fake, offline, test}。**注意**：程序化依赖注入无法在
      类型层证明 provider 一定是离线的，`factory_mode` 只是调用方契约标签——本步因此不提供
      真实 factory，正式运行由 G1 后的 Step 03 接线；不要把它当作类型级安全保证；
    - `verify_frozen`：运行时复核冻结清单 sha256（真实/正式运行必须 True）；
    - `identity` / `code_version`：身份记录；
    - `clock` / `monotonic`：时间源注入（测试确定性）。
    """

    def __init__(
        self,
        *,
        settings: Settings,
        image_factory: Callable[[], ImageProvider],
        output_root: Path = DEFAULT_OUTPUT_ROOT,
        manifest_path: Path = DEFAULT_MANIFEST_PATH,
        cases_path: Path | None = None,
        annotations_path: Path | None = None,
        config_path: Path | None = None,
        protocol_path: Path | None = None,
        repetitions: int | None = None,
        run_nonce: str = "",
        dry_run: bool = True,
        code_version: str | None = None,
        identity: CodeIdentity | None = None,
        verify_frozen: bool = True,
        knowledge_corpus_dir: Path | None = None,
        retry_deterministic_failures: bool = False,
        max_attempts_per_call: int = 1,
        retry_failed_on_resume: bool = False,
        budget_policy: BudgetPolicy | None = None,
        factory_mode: str = "injected",
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._image_factory = image_factory
        self._output_root = Path(output_root)
        self._manifest_path = Path(manifest_path)
        self._dry_run = dry_run
        self._factory_mode = str(factory_mode)
        self._identity = identity
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.perf_counter
        self._retry_deterministic_failures = retry_deterministic_failures
        self._max_attempts_per_call = max(1, int(max_attempts_per_call))
        self._retry_failed_on_resume = retry_failed_on_resume

        self._manifest = None
        if self._manifest_path.is_file():
            self._manifest = load_manifest(self._manifest_path, base_dir=PROJECT_ROOT)
            if verify_frozen:
                self._manifest.verify()

        self._cases_path = Path(cases_path or (self._manifest.dataset_path if self._manifest else DEFAULT_CASES_PATH))
        self._annotations_path = Path(
            annotations_path or (self._manifest.annotations_path if self._manifest else DEFAULT_ANNOTATIONS_PATH)
        )
        self._config_path = Path(config_path or (self._manifest.config_path if self._manifest else DEFAULT_CONFIG_PATH))
        self._protocol_path = Path(
            protocol_path
            or (self._manifest.protocol_path if self._manifest else DEFAULT_PROTOCOL_PATH)
        )
        self._verify_frozen = verify_frozen

        self._config = self._load_config()
        self._hashes = self._compute_hashes(verify_frozen=verify_frozen)

        self._repetitions = (
            int(repetitions)
            if repetitions is not None
            else int((self._config.get("repetitions") or {}).get("independent_generations_per_case_per_arm", 2))
        )
        if self._repetitions < 1:
            raise ValueError(f"repetitions must be >= 1, got {self._repetitions}")

        # 知识语料（B/C 共用同一冻结快照；C 才注入引擎）。
        knowledge = self._config.get("knowledge_snapshot") or {}
        self._corpus_dir = Path(knowledge_corpus_dir or PROJECT_ROOT / str(knowledge.get("corpus_dir", "knowledge_base/v0.5")))
        self._corpus = load_knowledge_corpus(self._corpus_dir)
        self._corpus_fingerprint = knowledge_fingerprint(self._corpus)
        self._corpus_manifest_sha256 = knowledge_manifest_sha256(self._corpus_dir)
        self._knowledge_verified = True
        expected_fingerprint = knowledge.get("corpus_fingerprint")
        if verify_frozen and expected_fingerprint and self._corpus_fingerprint != expected_fingerprint:
            raise PairedRunBlockedError(
                f"knowledge corpus fingerprint {self._corpus_fingerprint} does not match the "
                f"frozen config {expected_fingerprint}"
            )
        expected_manifest_sha = knowledge.get("manifest_sha256")
        if verify_frozen and expected_manifest_sha and self._corpus_manifest_sha256 != expected_manifest_sha:
            raise PairedRunBlockedError(
                f"knowledge manifest sha256 {self._corpus_manifest_sha256} does not match the "
                f"frozen config {expected_manifest_sha}"
            )

        self._budget_policy = budget_policy or budget_policy_from_config(
            self._config, max_internal_retries=self._settings.http_max_retries
        )

        if code_version is None and identity is not None:
            code_version = identity.code_version
        self._code_version = code_version or (
            f"visual-intent-agent+{BC_HARNESS_VERSION}"
        )
        self._run_id = self._build_run_id(run_nonce)

    # -- 只读属性 -----------------------------------------------------------

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def run_dir(self) -> Path:
        return self._output_root / self._run_id

    @property
    def repetitions(self) -> int:
        return self._repetitions

    @property
    def budget_policy(self) -> BudgetPolicy:
        return self._budget_policy

    @property
    def config(self) -> dict[str, Any]:
        return dict(self._config)

    # -- 冻结输入 -----------------------------------------------------------

    def _load_config(self) -> dict[str, Any]:
        try:
            return json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load v0.6 config {self._config_path}: {exc}") from exc

    def _compute_hashes(self, *, verify_frozen: bool) -> dict[str, str]:
        if self._manifest is not None and verify_frozen:
            return self._manifest.verify_hashes()
        return {
            "manifest": sha256_file(self._manifest_path) if self._manifest_path.is_file() else "",
            "protocol": sha256_file(self._protocol_path),
            "config": sha256_file(self._config_path),
            "dataset": sha256_file(self._cases_path),
            "annotations": sha256_file(self._annotations_path),
        }

    def _build_run_id(self, run_nonce: str) -> str:
        image_cfg = ((self._config.get("providers") or {}).get("image") or {})
        return make_record_id(
            RUN_ID_PREFIX,
            self._code_version,
            BC_HARNESS_VERSION,
            BC_ID_SCHEME_VERSION,
            self._hashes.get("protocol", ""),
            self._hashes.get("config", ""),
            self._hashes.get("dataset", ""),
            self._hashes.get("annotations", ""),
            self._hashes.get("manifest", ""),
            self._corpus_fingerprint,
            str(self._repetitions),
            # 有效 Provider 配置 + factory 标识进入 Run ID：不同模型/超时/重试/尺寸
            # 或不同 factory 模式绝不落到同一目录（防止旧新证据混写）。
            self._settings.image_model,
            self._settings.llm_model,
            str(self._settings.image_timeout_seconds),
            str(self._settings.llm_timeout_seconds),
            str(self._settings.http_max_retries),
            str(image_cfg.get("size", "1024x1024")),
            str(image_cfg.get("images_per_generation", 1)),
            self._factory_mode,
            run_nonce,
        )

    def _provider_snapshot(self) -> RunProviderSnapshot:
        providers = self._config.get("providers") or {}
        image = providers.get("image") or {}
        credentials = self._config.get("credentials") or {}
        return RunProviderSnapshot(
            image_model=self._settings.image_model,
            model_identity_verified=bool(image.get("model_identity_verified", False)),
            image_size=str(image.get("size", "1024x1024")),
            images_per_generation=int(image.get("images_per_generation", 1)),
            http_max_retries=self._settings.http_max_retries,
            image_timeout_seconds=self._settings.image_timeout_seconds,
            base_url_env=str(credentials.get("base_url_env", "VIA_PROVIDER_BASE_URL")),
            api_key_env=str(credentials.get("api_key_env", "VIA_PROVIDER_API_KEY")),
        )

    def _knowledge_snapshot(self) -> RunKnowledgeSnapshot:
        knowledge = self._config.get("knowledge_snapshot") or {}
        return RunKnowledgeSnapshot(
            corpus_dir=str(knowledge.get("corpus_dir", "knowledge_base/v0.5")),
            corpus_version=self._corpus.corpus_version,
            corpus_fingerprint=self._corpus_fingerprint,
            manifest_sha256=self._corpus_manifest_sha256,
            approved_unit_count=len(self._corpus.build_units()),
        )

    # -- 门禁 ---------------------------------------------------------------

    def assert_real_run_allowed(self) -> None:
        """真实入口 fail-closed：任一缺失即抛 `PairedRunBlockedError`。"""
        blockers = real_run_blockers(
            config=self._config,
            identity=self._identity,
            settings=self._settings,
            budget=self._budget_policy,
            manifest_verified=bool(self._verify_frozen and self._manifest is not None),
            knowledge_verified=self._knowledge_verified,
        )
        if blockers:
            raise PairedRunBlockedError(
                "real B/C run refused:\n  - " + "\n  - ".join(blockers)
            )
        raise PairedRunBlockedError(
            "real B/C run refused: Step 02 does not construct a real image provider; "
            "wire the G1-approved factory in Step 03 after the user authorizes the exact batch"
        )

    # -- 主入口 -------------------------------------------------------------

    def run(self, case_ids: Sequence[str] | None = None) -> PairedRunResult:
        """执行（或按幂等策略续跑）B/C 配对并只追加落盘。"""
        if self._dry_run and self._factory_mode not in OFFLINE_FACTORY_MODES:
            raise PairedRunBlockedError(
                f"dry_run requires an explicit offline/fake image factory; "
                f"factory_mode={self._factory_mode!r} is not in {sorted(OFFLINE_FACTORY_MODES)}"
            )
        if not self._dry_run:
            self.assert_real_run_allowed()
        if self._identity is not None and not self._dry_run:
            enforce_formal_identity(self._identity)

        all_cases = load_cases(self._cases_path)
        annotations = load_annotations(self._annotations_path)
        cases = self._select_cases(all_cases, case_ids)
        for case in cases:
            if case.case_id not in annotations:
                raise ValueError(f"missing annotation for case {case.case_id!r}")
            # E：逐 case repetitions 只允许与全局一致；混合计划 fail-closed。
            if case.effective_repetitions() != self._repetitions:
                raise PairedRunBlockedError(
                    f"case {case.case_id!r} declares pair.repetitions="
                    f"{case.effective_repetitions()} but the run uses {self._repetitions}; "
                    "a mixed per-case/global repetition plan is refused"
                )

        case_ids_by_layer: dict[str, list[str]] = {}
        for case in cases:
            case_ids_by_layer.setdefault(case.layer, []).append(case.case_id)
        planned_counts = planned_counts_by_layer(case_ids_by_layer, self._repetitions)
        # 真正按每对固定的调用顺序执行（不只是记录）：B_first → B,C；C_first → C,B。
        keys: list[tuple[str, int, str]] = []
        for layer in case_ids_by_layer:
            for case_id in case_ids_by_layer[layer]:
                for repetition in range(1, self._repetitions + 1):
                    order = call_order_for(case_id=case_id, repetition=repetition)
                    arms = ("C", "B") if order == "C_first" else ("B", "C")
                    for arm in arms:
                        keys.append((case_id, repetition, arm))

        run_dir = self.run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        records_path = run_dir / RECORDS_FILENAME
        ledger = BudgetLedger(run_dir / LEDGER_FILENAME, self._budget_policy, clock=self._clock)
        decisions_path = run_dir / DECISIONS_FILENAME
        existing_decisions = self._read_decision_keys(decisions_path)

        by_key: dict[tuple[str, int, str], PairRecord] = {
            (record.case_id, record.repetition, record.arm): record
            for record in read_records(records_path)
        }
        case_by_id = {case.case_id: case for case in cases}
        annotation_by_id = {case_id: annotations[case_id] for case_id in case_by_id}
        selected_keys = set(keys)

        # C1 进程中断：账本里未结算的预留（可能是上一次崩溃留下的）绝不当作新任务重发。
        # 在启动时把它们重建为可追溯的 `unknown` 记录 + 人工决定；DB 不可读时停止整批。
        interrupted_stop = self._reconcile_interrupted_reservations(
            run_dir=run_dir,
            records_path=records_path,
            decisions_path=decisions_path,
            existing_decisions=existing_decisions,
            ledger=ledger,
            by_key=by_key,
            case_by_id=case_by_id,
            annotation_by_id=annotation_by_id,
            selected_keys=selected_keys,
        )

        resumed_ok_skipped = 0
        budget_exhausted = False
        stopped = interrupted_stop
        decisions = 0
        unattempted: list[str] = []
        safety_violations: list[str] = []

        for case_id, repetition, arm in keys:
            key = (case_id, repetition, arm)
            pair_key = f"{case_id}/rep{repetition}/{arm}"
            previous = by_key.get(key)
            attempt = 1
            if previous is not None:
                if previous.status == "ok":
                    resumed_ok_skipped += 1
                    continue
                if previous.status == "unknown":
                    decisions += self._ensure_decision(
                        decisions_path,
                        existing_decisions,
                        run_id=self._run_id,
                        case_id=case_id,
                        repetition=repetition,
                        arm=arm,
                        reason="previous attempt has an unknown provider result; "
                        "resolve with the provider before re-running",
                        pair_key=pair_key,
                    )
                    continue
                if previous.status == "failed" and not self._retry_failed_on_resume:
                    continue
                # skipped / 允许重试的 failed：用**全新** attempt 目录/SQLite/会话重跑
                # （绝不复用旧 session.db，避免 persistence.session_exists）。
                attempt = previous.attempt + 1

            if stopped:
                unattempted.append(pair_key)
                continue

            record, violations = self._execute_pair(
                case=case_by_id[case_id],
                annotation=annotation_by_id[case_id],
                repetition=repetition,
                arm=arm,  # type: ignore[arg-type]
                attempt=attempt,
                run_dir=run_dir,
                ledger=ledger,
            )
            append_jsonl(records_path, record)
            by_key[key] = record
            if record.status == "unknown":
                decisions += self._ensure_decision(
                    decisions_path,
                    existing_decisions,
                    run_id=self._run_id,
                    case_id=case_id,
                    repetition=repetition,
                    arm=arm,
                    reason="provider result unknown (timeout / network / interruption); "
                    "never auto-resent",
                    pair_key=pair_key,
                )
            if record.error is not None and record.error.code == EVAL_BUDGET_EXHAUSTED:
                budget_exhausted = True
                stopped = True
            safety_violations.extend(violations)
            if safety_violations:
                # 安全违例：暂停批次，保留已有结果（协议第 4.1 / 5 节）。
                stopped = True

        # H1 子集续跑：只汇总**本次选中计划**的记录，绝不把 run 目录里其它 case 的
        # 记录算进分母（否则 not_run 可能为负、分母虚高）。
        latest = [by_key[key] for key in keys if key in by_key]
        summary = summarize(
            run_id=self._run_id,
            planned_counts=planned_counts,
            records=latest,
            ledger=ledger.snapshot(),
            decisions_required=len(existing_decisions),
            resumed_ok_skipped=resumed_ok_skipped,
            budget_exhausted=budget_exhausted,
            cost_basis=self._budget_policy.cost_basis,
            currency=self._budget_policy.currency,
            unattempted_keys=unattempted,
            safety_violations=safety_violations,
            clock=self._clock,
        )
        run_record = RunRecordV06(
            run_id=self._run_id,
            code_version=self._code_version,
            code_commit=(self._identity.commit if self._identity is not None else None),
            code_dirty=(self._identity.dirty if self._identity is not None else None),
            identity_mode=(self._identity.mode if self._identity is not None else "unspecified"),
            protocol_version=str(self._config.get("protocol_version", "")),
            dataset_version=str(self._config.get("dataset_version", "")),
            config_version=str(self._config.get("config_version", "")),
            manifest_version=(
                self._manifest.manifest_version if self._manifest is not None else "unverified"
            ),
            protocol_sha256=self._hashes.get("protocol", ""),
            config_sha256=self._hashes.get("config", ""),
            dataset_sha256=self._hashes.get("dataset", ""),
            annotations_sha256=self._hashes.get("annotations", ""),
            manifest_sha256=self._hashes.get("manifest", ""),
            knowledge=self._knowledge_snapshot(),
            provider=self._provider_snapshot(),
            repetitions=self._repetitions,
            case_ids=[case.case_id for case in cases],
            case_count=len(cases),
            call_order_rule=CALL_ORDER_RULE,
            revision_seed_rule=REVISION_SEED_RULE,
            image_seed_rule=(
                "no explicit image seed is supported by ImageGenerationRequest/OpenAIImageProvider; "
                "seed is never fabricated and per-pair call order is recorded"
            ),
            dry_run=self._dry_run,
            real_run_authorized=bool(self._config.get("real_run_authorized", False)),
            created_at=self._clock(),
        )
        canonical_run = self._write_run_record_once(run_dir, run_record)
        self._write_summary_snapshot(run_dir, summary)
        return PairedRunResult(run=canonical_run, records=latest, summary=summary)

    # -- 只追加落盘（续跑不覆盖已有 run/summary/session.db） -----------------

    def _write_run_record_once(self, run_dir: Path, run_record: RunRecordV06) -> RunRecordV06:
        """`run.json` 只写一次；续跑校验身份/配置一致后**复用**，绝不覆盖。

        比较时忽略 `created_at`（首次落盘时间）与 `case_ids`/`case_count`：两者是**逐次
        调用的范围**（全量或 `--case-id` 子集），不是 run 身份；子集续跑共享同一 run 目录，
        其范围与结果只记在 append-only 的 summary 快照里。其余字段（代码身份、协议/配置/
        数据集/标注/清单哈希、知识指纹、重复数、Provider 快照）必须完全一致，否则说明
        身份或配置漂移，必须新 run。
        """
        path = run_dir / "run.json"
        if not path.is_file():
            write_json(path, run_record)
            return run_record
        existing = RunRecordV06.model_validate_json(path.read_text(encoding="utf-8"))
        ignorable = {"created_at", "case_ids", "case_count"}
        if existing.model_dump(exclude=ignorable) != run_record.model_dump(exclude=ignorable):
            raise PairedRunBlockedError(
                f"run directory {run_dir} already holds a different run identity/config; "
                "refusing to overwrite it — start a new run (new nonce) instead"
            )
        return existing

    def _write_summary_snapshot(self, run_dir: Path, summary: RunSummaryV06) -> Path:
        """`summary` 只追加：写 `summaries/summary_NNNN.json`，首次另写 `summary.json`。

        取最新 = `summaries/` 中序号最大的文件；`summary.json` 是首次快照（写一次，
        续跑不覆盖），仅作兼容/快捷入口。
        """
        snapshot_dir = run_dir / "summaries"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        sequence = 1 + max(
            (
                int(match.group(1))
                for match in (
                    re.fullmatch(r"summary_(\d+)\.json", entry.name) for entry in snapshot_dir.iterdir()
                )
                if match is not None
            ),
            default=0,
        )
        target = snapshot_dir / f"summary_{sequence:04d}.json"
        write_json(target, summary)
        legacy = run_dir / "summary.json"
        if not legacy.is_file():
            write_json(legacy, summary)
        return target

    # -- 单配对 -------------------------------------------------------------

    def _execute_pair(
        self,
        *,
        case: CaseSpec,
        annotation: AnnotationSpec,
        repetition: int,
        arm: Arm,
        attempt: int,
        run_dir: Path,
        ledger: BudgetLedger,
    ) -> tuple[PairRecord, list[str]]:
        started = self._monotonic()
        # 续跑重试使用全新 attempt 目录/SQLite（绝不复用旧库）。
        suffix = "" if attempt == 1 else f"_r{attempt}"
        pair_dir = run_dir / "cases" / case.case_id / f"rep{repetition}" / f"{arm}{suffix}"
        pair_dir.mkdir(parents=True, exist_ok=True)
        db_path = pair_dir / "session.db"
        pair_key = f"{case.case_id}/rep{repetition}/{arm}"
        call_order = call_order_for(case_id=case.case_id, repetition=repetition)

        repo = SQLiteRepository(db_path)
        try:
            context = seed_confirmed_session(
                repo=repo,
                case=case,
                repetition=repetition,
                arm=arm,
                settings=self._settings,
                attempt=attempt,
            )
            engine = arm_knowledge_engine(arm, self._corpus if arm == "C" else None)
            recorder = RecordingImageProvider(self._image_factory(), monotonic=self._monotonic)
            pipeline = GenerationPipeline(
                repo=repo,
                prompt_engine=PromptEngine(QwenImageRenderer(), repo, knowledge_engine=engine),
                image_provider=recorder,
                output_dir=pair_dir / "images",
            )
            status, error, artifact, provider_calls = self._generate_with_budget(
                repo=repo,
                pipeline=pipeline,
                recorder=recorder,
                context=context,
                pair_key=pair_key,
                ledger=ledger,
            )
            retrieval = self._retrieval_trace(
                repo=repo, case=case, note=annotation, context=context, arm=arm
            )
            prompt_artifact_id: str | None = None
            prompt_sha256: str | None = None
            generation_id: str | None = None
            seed_returned: int | None = None
            image_rel_paths: list[str] = []
            realization_id: str | None = None
            if artifact is not None:
                generation_id = artifact.generation_id
                seed_returned = artifact.seed
                prompt_artifact_id = artifact.prompt_artifact_id
                stored = repo.get_prompt_artifact(artifact.prompt_artifact_id)
                prompt = PromptArtifact.model_validate_json(stored.payload)
                prompt_sha256 = sha256_text(prompt.prompt)
                image_rel_paths = [str(ref.path) for ref in artifact.output_refs]
            stored_state = repo.get_current_realization_state(context.session_id)
            if stored_state is not None:
                realization_id = stored_state.artifact_id
            record = PairRecord(
                record_id=make_record_id(
                    PAIR_RECORD_ID_PREFIX,
                    self._run_id,
                    case.case_id,
                    str(repetition),
                    arm,
                    f"attempt{attempt}",
                ),
                run_id=self._run_id,
                case_id=case.case_id,
                repetition=repetition,
                arm=arm,
                attempt=attempt,
                layer=case.layer,
                category=case.category,
                primary_path=case.primary_path,
                call_order=call_order,  # type: ignore[arg-type]
                status=status,  # type: ignore[arg-type]
                session_id=context.session_id,
                db_rel_path=self._relative(db_path, run_dir),
                intent_revision_id=context.intent_revision_id,
                execution_revision_id=context.execution_revision_id,
                confirmation_id=context.confirmation_id,
                summary_hash=context.summary_hash,
                confirmation_valid=context.confirmation_valid,
                prompt_artifact_id=prompt_artifact_id,
                prompt_sha256=prompt_sha256,
                generation_id=generation_id,
                realization_id=realization_id,
                generation_seed_returned=seed_returned,
                image_rel_paths=image_rel_paths,
                retrieval=retrieval,
                provider_calls=provider_calls,
                error=error,
                latency_ms=round((self._monotonic() - started) * 1000.0, 3),
                created_at=self._clock(),
            )
            violations = self._constraint_violations(
                repo=repo, case=case, context=context, record=record
            )
            return record, violations
        finally:
            repo.close()

    # -- 公平性 / 安全不变量检查 -------------------------------------------

    def _constraint_violations(
        self,
        *,
        repo: SQLiteRepository,
        case: CaseSpec,
        context: ConfirmedContext,
        record: PairRecord,
    ) -> list[str]:
        """逐项检查明确值 / PIN / 确认绑定 / 不可变历史 / 控制变量；返回违例说明。"""
        violations: list[str] = []
        session_id = context.session_id
        prefix = f"{case.case_id}/rep{record.repetition}/{record.arm}"

        # 1) 确认绑定必须仍然有效且绑定当前 revision。
        if not repo.is_confirmation_valid(context.confirmation_id):
            violations.append(f"{prefix}: confirmation binding is no longer valid")

        # 2) Intent / Execution revision 不被生成改写（历史只追加）。
        snapshot = repo.get_current_session_snapshot(session_id)
        if snapshot.current_intent_revision_id != context.intent_revision_id:
            violations.append(
                f"{prefix}: current intent revision changed from "
                f"{context.intent_revision_id!r} to {snapshot.current_intent_revision_id!r}"
            )
        if snapshot.current_execution_revision_id != context.execution_revision_id:
            violations.append(
                f"{prefix}: current execution revision changed from "
                f"{context.execution_revision_id!r} to {snapshot.current_execution_revision_id!r}"
            )

        # 3) 历史 append-only：本步骤只允许 1 条 intent / execution / confirmation，0 条消息。
        counts = {
            table: int(repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("messages", "intent_revisions", "execution_revisions", "confirmations")
        }
        if counts["messages"] != 0:
            violations.append(f"{prefix}: messages were appended unexpectedly ({counts['messages']})")
        if counts["intent_revisions"] != 1:
            violations.append(
                f"{prefix}: expected exactly 1 intent revision, found {counts['intent_revisions']}"
            )
        if counts["execution_revisions"] != 1:
            violations.append(
                f"{prefix}: expected exactly 1 execution revision, found "
                f"{counts['execution_revisions']}"
            )
        if counts["confirmations"] != 1:
            violations.append(
                f"{prefix}: expected exactly 1 confirmation, found {counts['confirmations']}"
            )

        # 4) 明确值必须原样保留；PIN 路径不得被知识覆盖，也不得被赋值。
        state = None
        stored_state = repo.get_current_realization_state(session_id)
        if stored_state is not None:
            state = RealizationState.model_validate_json(stored_state.payload)
        for path in case.explicit_paths:
            expected = case.confirmed_intent.get(path)
            actual = state.active_value_for(path).value if state is not None and state.active_value_for(path) else case.confirmed_intent.get(path)
            if actual != expected:
                violations.append(
                    f"{prefix}: explicit value for {path!r} changed "
                    f"({expected!r} -> {actual!r})"
                )
        for path in case.pinned_paths:
            # 合法 PIN：路径携带**现存值**并被 PIN；值必须原样保留，且不得被知识覆盖。
            expected = case.confirmed_intent.get(path)
            value = state.active_value_for(path) if state is not None else None
            actual = value.value if value is not None else case.confirmed_intent.get(path)
            if expected is not None and actual != expected:
                violations.append(
                    f"{prefix}: pinned value for {path!r} changed ({expected!r} -> {actual!r})"
                )
            if value is not None and value.knowledge_unit_id is not None:
                violations.append(
                    f"{prefix}: pinned path {path!r} was overridden by knowledge unit "
                    f"{value.knowledge_unit_id!r}"
                )

        # 5) 非授权字段不得变化：当前 Intent 的值/Resolution 必须与装配时一致。
        intent = repo.get_intent_revision(context.intent_revision_id).intent
        expected_values = dict(case.confirmed_intent)
        for path, expected in expected_values.items():
            actual = _read_intent_value(intent, path)
            if actual != expected:
                violations.append(
                    f"{prefix}: non-authorized field {path!r} changed "
                    f"({expected!r} -> {actual!r})"
                )
        for path in case.delegated_paths:
            if _read_intent_value(intent, path) is not None:
                violations.append(
                    f"{prefix}: delegated path {path!r} gained a confirmed value"
                )

        # 6) 知识只在被授权的知识路径上被采用；B 臂绝不产生 Bundle。
        if record.arm == "B":
            bundles = repo.list_knowledge_bundles(session_id)
            if bundles:
                violations.append(
                    f"{prefix}: RAG-disabled arm produced {len(bundles)} knowledge bundle(s)"
                )
        else:
            if state is not None:
                for value in state.values:
                    if value.knowledge_unit_id is None:
                        continue
                    if value.path not in KNOWLEDGE_PATHS:
                        violations.append(
                            f"{prefix}: knowledge adopted on non-knowledge path {value.path!r}"
                        )
                    if value.path in case.pinned_paths:
                        violations.append(
                            f"{prefix}: knowledge adopted on pinned path {value.path!r}"
                        )
                    if value.path in case.confirmed_intent:
                        violations.append(
                            f"{prefix}: knowledge adopted on already-resolved path {value.path!r}"
                        )

        # 7) PromptArtifact（若有）必须绑定本会话的当前 revision 与确认。
        if record.prompt_artifact_id is not None:
            stored_prompt = repo.get_prompt_artifact(record.prompt_artifact_id)
            prompt = PromptArtifact.model_validate_json(stored_prompt.payload)
            if prompt.based_on_intent_revision_id != context.intent_revision_id:
                violations.append(
                    f"{prefix}: prompt is bound to a different intent revision "
                    f"{prompt.based_on_intent_revision_id!r}"
                )
            if prompt.based_on_confirmation_id != context.confirmation_id:
                violations.append(
                    f"{prefix}: prompt is bound to a different confirmation "
                    f"{prompt.based_on_confirmation_id!r}"
                )
        return violations

    def _generate_with_budget(
        self,
        *,
        repo: SQLiteRepository,
        pipeline: GenerationPipeline,
        recorder: RecordingImageProvider,
        context: ConfirmedContext,
        pair_key: str,
        ledger: BudgetLedger,
    ) -> tuple[str, ErrorRecord | None, Any, list[ProviderCallRecord]]:
        """预算守卫下的（可选重试）图片生成；返回 (status, error, artifact, calls)。"""
        provider_calls: list[ProviderCallRecord] = []
        artifact: Any = None
        attempts = 0
        while attempts < self._max_attempts_per_call:
            attempts += 1
            allowed, reason = ledger.can_reserve()
            if not allowed:
                return (
                    "skipped",
                    ErrorRecord(
                        code=EVAL_BUDGET_EXHAUSTED,
                        message=f"budget guard stopped the run before request {attempts}: {reason}",
                        stage="runner",
                    ),
                    None,
                    provider_calls,
                )
            reservation_id = make_record_id(
                CALL_RECORD_ID_PREFIX, self._run_id, context.session_id, str(attempts)
            )
            try:
                ledger.reserve(
                    reservation_id=reservation_id, pair_key=pair_key, attempt_index=attempts
                )
            except BudgetExceededError as exc:
                return (
                    "skipped",
                    ErrorRecord(
                        code=EVAL_BUDGET_EXHAUSTED,
                        message=f"budget guard rejected reservation before request {attempts}: {exc}",
                        stage="runner",
                    ),
                    None,
                    provider_calls,
                )

            calls_before = len(recorder.calls)
            try:
                if attempts == 1:
                    artifact = pipeline.generate(context.session_id)
                else:
                    artifact = pipeline.retry(context.session_id)
            except (PromptCompilationError, GenerationError, RepositoryError) as exc:
                ledger.settle(
                    reservation_id,
                    status="failed",
                    charged_cost_minor=0,
                    cost_source="no_provider_call_or_persistence_failure",
                )
                provider_calls.extend(
                    self._provider_records(recorder, calls_before, attempts, "failed", 0)
                )
                return ("failed", error_record(exc, stage=self._stage_for(exc)), artifact, provider_calls)
            except ProviderError as exc:
                outcome = self._classify_provider_error(exc)
                if outcome == "unknown":
                    charge = self._budget_policy.worst_case_cost_minor
                    source = "unknown_worst_case_unreconciled"
                else:
                    charge = 0
                    source = "deterministic_rejection_not_billed"
                ledger.settle(
                    reservation_id, status=outcome, charged_cost_minor=charge, cost_source=source
                )
                provider_calls.extend(
                    self._provider_records(recorder, calls_before, attempts, outcome, charge)
                )
                error = error_record(exc, stage="image")
                if outcome == "unknown":
                    return ("unknown", error, None, provider_calls)
                if not self._retry_deterministic_failures or attempts >= self._max_attempts_per_call:
                    return ("failed", error, None, provider_calls)
                continue
            else:
                ledger.settle(
                    reservation_id,
                    status="ok",
                    charged_cost_minor=self._budget_policy.worst_case_cost_minor,
                    cost_source=self._budget_policy.cost_basis,
                )
                provider_calls.extend(
                    self._provider_records(
                        recorder,
                        calls_before,
                        attempts,
                        "ok",
                        self._budget_policy.worst_case_cost_minor,
                    )
                )
                return ("ok", None, artifact, provider_calls)
        return ("failed", None, artifact, provider_calls)

    def _provider_records(
        self,
        recorder: RecordingImageProvider,
        calls_before: int,
        attempt_index: int,
        status: str,
        charged_minor: int,
    ) -> list[ProviderCallRecord]:
        records: list[ProviderCallRecord] = []
        for call in recorder.calls[calls_before:]:
            call_status = (
                status if call.error is not None or status != "ok" else "ok"
            )
            if call.error is not None:
                call_status = status
            records.append(
                ProviderCallRecord(
                    call_id=make_record_id(
                        CALL_RECORD_ID_PREFIX,
                        self._run_id,
                        str(calls_before),
                        str(attempt_index),
                        sha256_text(call.request.prompt)[:16],
                    ),
                    attempt_index=attempt_index,
                    prompt_sha256=sha256_text(call.request.prompt),
                    size=call.request.size,
                    model_requested=call.request.model,
                    model_returned=(
                        call.response.model if call.response is not None else None
                    ),
                    seed_requested=None,  # ImageGenerationRequest 无 seed 字段：绝不伪造
                    seed_returned=(
                        call.response.seed if call.response is not None else None
                    ),
                    provider_request_id=(
                        call.response.provider_request_id if call.response is not None else None
                    ),
                    endpoint_redacted=None,
                    latency_ms=call.latency_ms,
                    internal_attempts_observed=None,
                    worst_case_attempts=self._budget_policy.worst_case_attempts,
                    status=call_status,  # type: ignore[arg-type]
                    cost_minor=charged_minor if call_status != "failed" else 0,
                    cost_source=self._budget_policy.cost_basis,
                    currency=self._budget_policy.currency,
                    error=(
                        error_record(call.error, stage="image")
                        if call.error is not None
                        else None
                    ),
                )
            )
        return records

    @staticmethod
    def _classify_provider_error(exc: ProviderError) -> str:
        if exc.code in UNKNOWN_PROVIDER_CODES:
            return "unknown"
        if exc.code in DETERMINISTIC_PROVIDER_CODES:
            return "failed"
        return "unknown"

    @staticmethod
    def _stage_for(exc: BaseException) -> str:
        code = getattr(exc, "code", "")
        if isinstance(code, str) and code.startswith("prompt."):
            return "prompt"
        if isinstance(code, str) and code.startswith("persistence."):
            return "persistence"
        if isinstance(code, str) and code.startswith("generation."):
            return "generation"
        return "runner"

    # -- 检索/采用观察 ------------------------------------------------------

    def _retrieval_trace(
        self,
        *,
        repo: SQLiteRepository,
        case: CaseSpec,
        note: AnnotationSpec,
        context: ConfirmedContext,
        arm: Arm,
    ) -> RetrievalTrace:
        primary = case.primary_path
        fallback_value: str | None = None
        if primary in case.delegated_paths:
            from visual_intent_agent.prompt_engine.engine import select_delegated_value

            fallback_value = select_delegated_value(primary, context.intent_revision_id)
        elif primary in case.confirmed_intent:
            fallback_value = case.confirmed_intent[primary]

        actual_value: str | None = None
        actual_source: str | None = None
        stored_state = repo.get_current_realization_state(context.session_id)
        if stored_state is not None:
            state = RealizationState.model_validate_json(stored_state.payload)
            value = state.active_value_for(primary)
            if value is not None:
                actual_value = value.value
                actual_source = "knowledge_unit" if value.knowledge_unit_id else "deterministic_fallback"
        if actual_value is None and primary in case.confirmed_intent:
            actual_value = case.confirmed_intent[primary]
            actual_source = "explicit_user_value"

        if arm == "B":
            return RetrievalTrace(
                queried=False,
                outcome="not_queried",
                reason_code="rag_disabled",
                fallback_value=fallback_value,
                actual_value=actual_value,
                actual_value_source=actual_source,
                overreach_check=note.overreach_check,
            )

        bundle_ids = [stored.artifact_id for stored in repo.list_knowledge_bundles(context.session_id)]
        if not bundle_ids:
            # PIN 优先于 explicit：合法 PIN 是“现存显式值 + pinned”，冻结词表要求
            # `pinned_existing_value_not_queried` / `path_pinned`，不能记成 explicit 分支。
            if primary in case.pinned_paths:
                reason = "pinned_existing_value_not_queried"
            elif primary in case.explicit_paths:
                reason = "explicit_value_not_queried"
            else:
                reason = "no_pending_knowledge_paths"
            not_queried_path = primary in case.pinned_paths or primary in case.explicit_paths
            return RetrievalTrace(
                queried=False,
                outcome="not_queried",
                reason_code=reason,
                fallback_value=fallback_value,
                actual_value=actual_value,
                actual_value_source=actual_source,
                adoption_outcome=("path_not_queried" if not_queried_path else None),
                adoption_rejection_reason=(
                    "path_pinned"
                    if primary in case.pinned_paths
                    else ("path_already_resolved" if primary in case.explicit_paths else None)
                ),
                overreach_check=note.overreach_check,
            )

        bundle = KnowledgeBundle.model_validate_json(
            repo.get_knowledge_bundle(bundle_ids[0]).payload
        )
        result = bundle.result_for(primary)
        recommendation = bundle.recommendation_for(primary)
        decision = bundle.decision_for(primary)
        outcome = result.outcome.value if result is not None else "no_path_result"
        reason_code = result.reason_code if result is not None else None
        adoption_outcome: str | None = None
        rejection: str | None = None
        if decision is not None:
            adoption_outcome = decision.outcome.value
            rejection = (
                decision.reason_code.value if decision.reason_code is not None else None
            )
        elif outcome != "adopted":
            adoption_outcome = "not_adopted_fallback"
            rejection = "no_adopted_result"
        adopted_without_delta: bool | None = None
        if adoption_outcome == "adopted":
            adopted_without_delta = actual_value == fallback_value
        return RetrievalTrace(
            queried=bool(bundle.queries),
            outcome=outcome,
            reason_code=reason_code,
            corpus_version=bundle.corpus_version,
            corpus_fingerprint=bundle.corpus_fingerprint,
            manifest_sha256=self._corpus_manifest_sha256,
            bundle_ids=bundle_ids,
            knowledge_id=(recommendation.knowledge_id if recommendation is not None else None),
            candidate_value=(recommendation.candidate_value if recommendation is not None else None),
            adoption_outcome=adoption_outcome,
            adoption_rejection_reason=rejection,
            fallback_value=fallback_value,
            actual_value=actual_value,
            actual_value_source=actual_source,
            adopted_without_prompt_delta=adopted_without_delta,
            overreach_check=note.overreach_check,
        )

    # -- 续跑/决策 ----------------------------------------------------------

    def _reconcile_interrupted_reservations(
        self,
        *,
        run_dir: Path,
        records_path: Path,
        decisions_path: Path,
        existing_decisions: set[str],
        ledger: BudgetLedger,
        by_key: dict[tuple[str, int, str], PairRecord],
        case_by_id: dict[str, CaseSpec],
        annotation_by_id: dict[str, AnnotationSpec],
        selected_keys: set[tuple[str, int, str]],
    ) -> bool:
        """把“账本有预留/结算、但无 PairRecord”的崩溃窗口重建为可追溯记录。

        覆盖两种中断窗口（续跑**绝不能**把该 arm 当新任务重跑，否则 `session_exists`
        卡死或重复付费）：

        - **reserve 后、settle 前**（结果未知）；
        - **settle 后、PairRecord append 前**（账本已有 ok/failed/unknown 结算，但无记录）。

        对每个选中计划中缺失记录的预留：读取原 attempt 目录 DB 的 revision / confirmation /
        已落 Prompt sha256 / GenerationArtifact。若账本 settle 状态可确定且 DB 证据一致
        （`ok` 需有 GenerationArtifact；`failed` 需 DB 可读），重建对应 `ok`/`failed` 记录；
        无法可靠重建时保守写 `unknown` + `DecisionRequired` 且不重发；DB 缺失/不可读时写入
        独立 interruption 记录并**停止整批**。所有重建记录 `interrupted=True`，不调用 factory。
        """
        if not ledger.reservations:
            return False
        stopped = False
        for reservation in ledger.reservations:
            parsed = re.fullmatch(r"(?P<case>.+)/rep(?P<rep>\d+)/(?P<arm>[BC])", reservation.pair_key)
            if parsed is None:
                stopped = True
                continue
            case_id = parsed.group("case")
            repetition = int(parsed.group("rep"))
            arm = parsed.group("arm")
            key = (case_id, repetition, arm)
            if key not in selected_keys or key in by_key:
                continue  # 非本次计划，或已有记录 → 不重复重建
            case = case_by_id.get(case_id)
            if case is None:
                stopped = True
                continue
            attempt = reservation.attempt_index
            suffix = "" if attempt == 1 else f"_r{attempt}"
            db_path = run_dir / "cases" / case_id / f"rep{repetition}" / f"{arm}{suffix}" / "session.db"
            session_id = session_id_for(
                case_id=case_id, repetition=repetition, arm=arm, attempt=attempt
            )
            settle = ledger.settle_entry(reservation.reservation_id)
            settle_status = settle.status if settle is not None else None
            confirmation_id = "unknown_interrupted"
            summary_hash = ""
            confirmation_valid = False
            prompt_artifact_id: str | None = None
            prompt_sha256: str | None = None
            realization_id: str | None = None
            generation_id: str | None = None
            generation_seed: int | None = None
            image_rel_paths: list[str] = []
            intent_revision_id = revision_seed_for(case_id=case_id, repetition=repetition)
            execution_revision_id = execution_revision_id_for(
                case_id=case_id, repetition=repetition
            )
            annotation = annotation_by_id.get(case_id)
            trace: RetrievalTrace | None = None
            readable = False
            if db_path.is_file():
                try:
                    repo = SQLiteRepository(db_path)
                    try:
                        snapshot = repo.get_current_session_snapshot(session_id)
                        session_id = snapshot.session_id
                        if snapshot.current_intent_revision_id is not None:
                            intent_revision_id = snapshot.current_intent_revision_id
                        if snapshot.current_execution_revision_id is not None:
                            execution_revision_id = snapshot.current_execution_revision_id
                        if snapshot.latest_confirmation_id:
                            confirmation_id = snapshot.latest_confirmation_id
                            confirmation_valid = repo.is_confirmation_valid(confirmation_id)
                            try:
                                summary_hash = repo.get_confirmation(confirmation_id).summary_hash
                            except RepositoryError:
                                summary_hash = ""
                        stored_prompt = repo.get_latest_prompt_artifact(session_id)
                        if stored_prompt is not None:
                            prompt_artifact_id = stored_prompt.artifact_id
                            prompt_sha256 = sha256_text(
                                PromptArtifact.model_validate_json(stored_prompt.payload).prompt
                            )
                        stored_state = repo.get_current_realization_state(session_id)
                        if stored_state is not None:
                            realization_id = stored_state.artifact_id
                        generations = repo.list_generation_artifacts(session_id)
                        if generations:
                            generation = GenerationArtifact.model_validate_json(
                                generations[-1].payload
                            )
                            generation_id = generation.generation_id
                            generation_seed = generation.seed
                            image_rel_paths = [str(ref.path) for ref in generation.output_refs]
                            prompt_artifact_id = generation.prompt_artifact_id
                            stored_gen_prompt = repo.get_prompt_artifact(
                                generation.prompt_artifact_id
                            )
                            prompt_sha256 = sha256_text(
                                PromptArtifact.model_validate_json(stored_gen_prompt.payload).prompt
                            )
                        execution = repo.get_execution_revision(execution_revision_id)
                        if annotation is not None:
                            trace = self._retrieval_trace(
                                repo=repo,
                                case=case,
                                note=annotation,
                                context=ConfirmedContext(
                                    session_id=session_id,
                                    intent_revision_id=intent_revision_id,
                                    execution_revision_id=execution_revision_id,
                                    confirmation_id=confirmation_id,
                                    summary_hash=summary_hash,
                                    confirmation_valid=confirmation_valid,
                                    target_model=execution.target_model,
                                    output_size=execution.output_size,
                                ),
                                arm=arm,  # type: ignore[arg-type]
                            )
                        readable = True
                    finally:
                        repo.close()
                except (RepositoryError, ValueError):
                    readable = False
                    trace = None
                    generation_id = None

            # 依据账本结算状态 + DB 证据决定重建状态；无法可靠重建时保守取 unknown。
            status = "unknown"
            error = ErrorRecord(
                code=EVAL_UNKNOWN_REQUEST,
                message=(
                    "process interruption near the record append; the budget ledger has a "
                    "reservation"
                    + (f" settled as {settle_status!r}" if settle_status else " with no settle")
                    + (" but the attempt database is missing/unreadable" if not readable else "")
                    + "; never auto-resent, reconcile with the provider"
                ),
                stage="runner",
            )
            if settle_status == "ok" and readable and generation_id is not None:
                status = "ok"
                error = None
            elif settle_status == "failed" and readable:
                status = "failed"
                error = ErrorRecord(
                    code=EVAL_FAILED_BEFORE_RECORD,
                    message=(
                        "budget ledger settled this attempt as failed, but the process "
                        "interrupted before the PairRecord was appended; reconstructed from the "
                        "ledger + attempt database"
                    ),
                    stage="runner",
                )
            if trace is None:
                trace = RetrievalTrace(
                    queried=False,
                    outcome="not_queried",
                    reason_code="interrupted_before_record",
                    overreach_check=(annotation.overreach_check if annotation else None),
                )
            record = PairRecord(
                record_id=make_record_id(
                    PAIR_RECORD_ID_PREFIX,
                    self._run_id,
                    case_id,
                    str(repetition),
                    arm,
                    f"attempt{attempt}",
                ),
                run_id=self._run_id,
                case_id=case_id,
                repetition=repetition,
                arm=arm,  # type: ignore[arg-type]
                attempt=attempt,
                layer=case.layer,
                category=case.category,
                primary_path=case.primary_path,
                call_order=call_order_for(case_id=case_id, repetition=repetition),  # type: ignore[arg-type]
                status=status,  # type: ignore[arg-type]
                session_id=session_id,
                db_rel_path=self._relative(db_path, run_dir),
                intent_revision_id=intent_revision_id,
                execution_revision_id=execution_revision_id,
                confirmation_id=confirmation_id,
                summary_hash=summary_hash,
                confirmation_valid=confirmation_valid,
                prompt_artifact_id=prompt_artifact_id,
                prompt_sha256=prompt_sha256,
                generation_id=generation_id,
                realization_id=realization_id,
                generation_seed_returned=generation_seed,
                image_rel_paths=image_rel_paths,
                retrieval=trace,
                provider_calls=[],
                error=error,
                interrupted=True,
                created_at=self._clock(),
            )
            append_jsonl(records_path, record)
            by_key[key] = record
            self._ensure_decision(
                decisions_path,
                existing_decisions,
                run_id=self._run_id,
                case_id=case_id,
                repetition=repetition,
                arm=arm,
                reason=(
                    "crash window recovered (settle="
                    f"{settle_status or 'none'} -> {status}); reconcile with the provider before "
                    "any retry"
                ),
                pair_key=reservation.pair_key,
            )
            if not readable:
                stopped = True
        return stopped

    def _select_cases(
        self, cases: list[CaseSpec], case_ids: Sequence[str] | None
    ) -> list[CaseSpec]:
        if case_ids is None:
            return list(cases)
        known = {case.case_id for case in cases}
        unknown = sorted(set(case_ids) - known)
        if unknown:
            raise ValueError(f"unknown case_ids: {unknown}")
        wanted = set(case_ids)
        return [case for case in cases if case.case_id in wanted]

    @staticmethod
    def _read_decision_keys(path: Path) -> set[str]:
        if not path.is_file():
            return set()
        keys: set[str] = set()
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                keys.add(json.loads(raw)["pair_key"])
        return keys

    def _ensure_decision(
        self,
        path: Path,
        existing: set[str],
        *,
        run_id: str,
        case_id: str,
        repetition: int,
        arm: str,
        reason: str,
        pair_key: str,
    ) -> int:
        if pair_key in existing:
            return 0
        append_jsonl(
            path,
            DecisionRequired(
                decision_id=make_record_id(
                    DECISION_ID_PREFIX, run_id, case_id, str(repetition), arm
                ),
                run_id=run_id,
                case_id=case_id,
                repetition=repetition,
                arm=arm,  # type: ignore[arg-type]
                reason=reason,
                pair_key=pair_key,
                created_at=self._clock(),
            ),
        )
        existing.add(pair_key)
        return 1

    @staticmethod
    def _relative(path: Path, root: Path) -> str:
        import os

        return Path(os.path.relpath(Path(path).resolve(), Path(root).resolve())).as_posix()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evaluation.v0_6.paired_runner",
        description=(
            "MVP v0.6 B/C 配对执行器（Step 02）：默认 Fake、零网络；真实入口 fail-closed。"
        ),
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--case-id", action="append", default=None)
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--nonce", default="")
    parser.add_argument(
        "--provider",
        choices=("fake", "real"),
        default="fake",
        help="fake = FakeImageProvider（默认，零网络）；real = 仅做 fail-closed 门禁，不在本步构造真实 Provider",
    )
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--retry-deterministic-failures", action="store_true")
    parser.add_argument("--max-attempts-per-call", type=int, default=1)
    parser.add_argument("--retry-failed-on-resume", action="store_true")
    parser.add_argument("--list-cases", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_cases:
        for case in load_cases(DEFAULT_CASES_PATH):
            print(f"{case.case_id}\t{case.layer}\t{case.category}\t{case.primary_path}")
        return 0

    from visual_intent_agent import __version__ as package_version

    settings = Settings(
        provider_base_url="https://provider.invalid/v1",
        provider_api_key="not-a-real-key",  # type: ignore[arg-type]
        image_model="qwen-image-3.0",
    )
    identity = resolve_code_identity(
        PROJECT_ROOT,
        package_version=f"visual_intent_agent-{package_version}",
        component_version=BC_HARNESS_VERSION,
        mode=IDENTITY_DIAGNOSTIC if args.diagnostic else IDENTITY_FORMAL,
    )
    image_factory: Callable[[], ImageProvider]
    if args.provider == "real":
        image_factory = lambda: (_ for _ in ()).throw(  # pragma: no cover - 门禁先拒绝
            PairedRunBlockedError("Step 02 never constructs a real provider")
        )
    else:
        from visual_intent_agent.providers.fake_image import FakeImageProvider

        image_factory = FakeImageProvider

    try:
        runner = PairedRunner(
            settings=settings,
            image_factory=image_factory,
            output_root=args.output_root,
            manifest_path=args.manifest,
            repetitions=args.repetitions,
            run_nonce=args.nonce,
            dry_run=args.provider != "real",
            identity=identity,
            retry_deterministic_failures=args.retry_deterministic_failures,
            max_attempts_per_call=args.max_attempts_per_call,
            retry_failed_on_resume=args.retry_failed_on_resume,
            factory_mode="fake" if args.provider == "fake" else "real",
        )
        result = runner.run(case_ids=args.case_id)
    except (ValueError, RepositoryError, TruncatedRecordsError, IdentityError, PairedRunBlockedError) as exc:
        print(f"B/C run refused: {exc}", file=sys.stderr)
        return 3

    summary = result.summary
    print(f"run_id: {result.run.run_id}")
    print(f"产物目录: {runner.run_dir}")
    print(
        f"身份: mode={result.run.identity_mode} commit={result.run.code_commit} "
        f"dirty={result.run.code_dirty}"
    )
    print(
        f"计划比较对(case×rep): {summary.planned_comparison_pairs}；"
        f"计划 arm 运行: {summary.planned_arm_runs}（已记录 {summary.records_total}）"
    )
    print(f"状态计数: {json.dumps(summary.status_counts, ensure_ascii=False)}")
    print(f"配对结局: {json.dumps(summary.pair_outcomes, ensure_ascii=False)}")
    print(f"未开始 arm 运行: {summary.unattempted_arm_runs}")
    print(f"未知请求: {len(summary.unknown_pair_keys)}；需人工决定: {summary.decisions_required}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DEFAULT_MANIFEST_PATH",
    "DEFAULT_CASES_PATH",
    "DEFAULT_ANNOTATIONS_PATH",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_OUTPUT_ROOT",
    "EVAL_BUDGET_EXHAUSTED",
    "EVAL_UNKNOWN_REQUEST",
    "EVAL_FAILED_BEFORE_RECORD",
    "EVAL_CONSTRAINT_VIOLATION",
    "UNKNOWN_PROVIDER_CODES",
    "DETERMINISTIC_PROVIDER_CODES",
    "REQUIRED_THRESHOLD_FIELDS",
    "OFFLINE_FACTORY_MODES",
    "TruncatedRecordsError",
    "PairedRunBlockedError",
    "RecordingImageProvider",
    "redact_endpoint",
    "real_run_blockers",
    "PairedRunner",
    "build_arg_parser",
    "main",
]
