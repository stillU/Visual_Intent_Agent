"""MVP v0.3 Step 03：A/B 评测 Runner（协议第 3 节运行驱动规则的唯一实现）。

对同一案例以**相同 Provider 条件**驱动 Baseline A（复用 Step 02 执行器）与
System B（复用 v0.2 公开接口，零修改产品代码），产出统一合同记录
（`evaluation.reporting`）与 L1~L3 指标记录。

冻结口径（本模块的权威声明，与交接记录一致）：

1. **重复与图片层**：L1~L3 每案例运行 `repetitions` 次（冻结配置 = 2）。由于多轮案例
   的 `image_feedback` / `accept` 轮必须以一次真实生成为前提，**每次重复都执行完整
   生成链路**（图片在每次重复都产生）；仅第 `l4_repetition` 次（冻结 = 1）重复的图片
   标记 `designated_for_l4=True`，成对进入 L4 盲评（Step 04）。A/B 两侧完全对称。
2. **同一 Provider 条件**：两个系统由同一对 `llm_factory` / `image_factory` 供给
   Provider（每次重复、每个系统各取一个全新实例；真实运行时工厂由同一 `Settings`
   构造 adapter——同 base_url/key/模型/超时/重试）。System B 的 Interpreter /
   FeedbackEngine 用其冻结 `response_format={"type": "json_object"}`，Baseline 自由
   文本——结构差异照实登记于 `EvalProviderSnapshot`。
3. **引用而非复制**：Baseline 的调用细节已在 Step 02 记录中，本 Runner 以
   `baseline_turn_record_id` 引用；System B 的调用细节由记录包装器落
   `LLMCallObservation` / `ImageCallObservation`。
4. **System B 隔离**：每案例使用**全新** SQLite（`<run_dir>/system_b/rep<N>/cases/
   <case_id>/session.db`）与新会话，杜绝跨案例状态泄漏；产品代码零修改。
5. **驱动规则**（协议第 3 节）：
   - `user_message` / `clarification_answer` → `WorkflowService.submit_message`；
     `image_feedback` / `accept` → `ReviewService.submit_feedback`；
   - 会话进入 `WAITING_CONFIRMATION` 时 Runner 始终代表用户**自动确认**
     （重算 `summary_hash`）并立即 `GenerationPipeline.generate`；确认与生成计入该轮；
   - `WAITING_CLARIFICATION` 时不即兴作答：等脚本下一轮；脚本与状态不符仍按脚本
     提交并记 `flow_deviation = true`（不静默跳过、不替用户改写）；
   - `answer_variants` 按 System B 实际待答问题的 `target_path` 选择回答文本，
     无匹配用 `user_text` 并记 `flow_deviation`；Baseline 恒用 `user_text`；
   - 与 `expected_outcome` 的偏离原样记录（`actual_outcome` / `outcome_matches`），
     指标按实际发生计算，失败不从分母剔除。
6. **L1 确认探针**：进入 `WAITING_CONFIRMATION` 后、正式确认前，Runner 先做两个
    只读语义的负向探针——过期 revision 绑定必须被拒（`workflow.stale_revision`）、
    篡改 hash 必须被拒（`workflow.summary_hash_mismatch`）；探针被接受即 L1 违例。
    探针失败路径不落任何状态（`confirm_current_intent` 先校验后写库）。

失败口径（协议第 7 节）：Provider 失败只经 adapter 内冻结策略重试（包装器不重试，
`retry_count` 恒 0 表示"评测层未重试"）；用尽后该轮记 `failed`（携带
`provider.*` code）并保留在分母；LLM 解析失败属被测系统行为，不外部重试；
基础设施级崩溃不在本 Runner 内自动重跑（真实运行如需整案重跑，用相同配置与
不同 `--nonce` 另起 Run，两份原始记录均保留）。

目录约定（协议第 8 节）：

```text
evaluation/runs/<run_id>/
├── run.json                          # EvalRunRecord
├── summary.json                      # RunSummary（含 gate_a_blocked）
├── cases/<case_id>.jsonl             # 逐案例统一记录（case/turn/metric 行）
├── baseline_a/rep<N>/                # Step 02 执行器原始产物（run.json/cases/...）
└── system_b/rep<N>/cases/<case_id>/  # session.db + images/<gen_id>/image_i.png
```

真实运行（默认测试绝不触发；需要环境变量/`.env` 中的凭据）：

```bash
uv run python -m evaluation.runner                      # 全量 22 案例 × 2 重复 × A/B
uv run python -m evaluation.runner --list-cases         # 只列出数据集案例
uv run python -m evaluation.runner --case-id s01-complete-001 --repetitions 1
uv run python -m evaluation.runner --output-root /tmp/via_runs --nonce exp-001
```

注意：真实运行后 `evaluation/runs/` 含二进制图片与 SQLite——Step 01 冻结的凭据
扫描按 UTF-8 读取 `evaluation/` 全部文件，运行默认离线测试套件前请移出/清空
`evaluation/runs/`（该目录已 gitignore；或用 `--output-root` 把产物放到仓库外）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from visual_intent_agent.config import PROJECT_ROOT, Settings
from visual_intent_agent.domain import INTENT_PATHS, VisualIntent
from visual_intent_agent.feedback import FeedbackEngine
from visual_intent_agent.generation import GenerationError, GenerationPipeline
from visual_intent_agent.intent_engine import IntentEngine, Interpreter
from visual_intent_agent.persistence import (
    SQLiteRepository,
    WorkflowState,
)
from visual_intent_agent.prompt_engine import (
    PROMPT_UNAUTHORIZED_ADDITION,
    PromptArtifact,
    PromptCompilationError,
    PromptEngine,
    QwenImageRenderer,
)
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.image import (
    ImageGenerationRequest,
    ImageProvider,
)
from visual_intent_agent.providers.llm import LLMProvider, LLMRequest
from visual_intent_agent.workflow import (
    QuestionBuilder,
    WorkflowService,
    compute_summary_hash,
)
from visual_intent_agent.workflow.confirmation import WorkflowError
from visual_intent_agent.workflow.review import ReviewService

from evaluation.direct_baseline import (
    GATE_A_PROTOCOL_VERSION,
    DirectBaselineRunner,
    default_code_version,
    load_dataset,
    sha256_file,
    sha256_text,
)
from evaluation.identity import (
    IDENTITY_DIAGNOSTIC,
    IDENTITY_FORMAL,
    CodeIdentity,
    enforce_formal_identity,
    resolve_code_identity,
)
from evaluation.manifest import RunManifest, load_manifest
from evaluation.metrics.intent import (
    TurnObservationInput,
    evaluate_clarification_precision,
    evaluate_conflict_detection,
    evaluate_delta_accuracy,
    evaluate_delegation_scope,
    evaluate_missing_decision_recall,
    normalize_conflict_rule_id,
)
from evaluation.metrics.prompt import (
    PromptRequestFace,
    PromptSequenceTurn,
    evaluate_generation_completion,
    evaluate_intent_coverage,
    evaluate_model_compatibility,
    evaluate_preservation,
    evaluate_preservation_r1,
    evaluate_unauthorized_addition,
    evaluate_unauthorized_addition_r1,
)
from evaluation.metrics.state import (
    HISTORY_TABLES,
    L1TurnEvidence,
    TableSnapshot,
    evaluate_case_l1,
)
from evaluation.models import (
    BaselineCase,
    BaselineCaseRecord,
    BaselineTurn,
    BaselineTurnRecord,
)
from evaluation.reporting import (
    ARTIFACT_REF_ID_PREFIX,
    CASE_RECORD_ID_PREFIX,
    EVAL_ID_SCHEME_VERSION,
    EVALUATION_HARNESS_VERSION,
    IMAGE_OBSERVATION_ID_PREFIX,
    L1_INVARIANTS,
    L1_INVARIANT_PINNED,
    L2_METRICS,
    L3_METRICS,
    L3_R1_METRICS,
    LLM_OBSERVATION_ID_PREFIX,
    METRIC_RECORD_ID_PREFIX,
    METRIC_VERSION_R1,
    METRIC_VERSION_V1,
    RUN_ID_PREFIX,
    TURN_RECORD_ID_PREFIX,
    ArtifactRef,
    CaseAnnotation,
    EvalCaseRecord,
    EvalFailureRecord,
    EvalProviderSnapshot,
    EvalRunRecord,
    EvalRunResult,
    EvalTurnRecord,
    ImageCallObservation,
    LLMCallObservation,
    MetricPayload,
    MetricRecord,
    ObservedConfirmation,
    ObservedDelta,
    ObservedGeneration,
    ObservedIssue,
    ObservedQuestion,
    RunSummary,
    SystemBTurnObservation,
    TurnAnnotation,
    aggregate_run,
    load_annotations,
    make_eval_record_id,
    write_case_jsonl,
    write_json,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 冻结输入与清单的默认路径（v0_3；只读保留，旧行为向后兼容）。
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "evaluation" / "configs" / "gate_a_v0_3.json"
DEFAULT_DATASET_PATH = PROJECT_ROOT / "evaluation" / "fixtures" / "core_v0_3.jsonl"
DEFAULT_ANNOTATIONS_PATH = PROJECT_ROOT / "evaluation" / "annotations" / "core_v0_3.jsonl"
DEFAULT_PROTOCOL_PATH = PROJECT_ROOT / "evaluation" / "protocol.md"
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "evaluation" / "frozen_manifest_v0_3.json"
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "evaluation" / "runs"

#: R1-B #10：真实运行默认产物根目录改为 `outputs/evaluation_runs/`，避免二进制
#: 图片/SQLite 与 `evaluation/` 的 UTF-8 凭据扫描冲突；历史 `evaluation/runs/` 不迁移。
DEFAULT_REAL_RUNS_ROOT = PROJECT_ROOT / "outputs" / "evaluation_runs"

#: r1 冻结清单（`--manifest` 未指定时的 r1 候选；默认仍用旧清单保持兼容）。
DEFAULT_R1_MANIFEST_PATH = PROJECT_ROOT / "evaluation" / "frozen_manifest_v0_3_r1.json"

#: r1 数据集不允许出现的字段（R1-B #5：A/B 必须使用同一冻结用户消息）。
_FORBIDDEN_R1_TURN_FIELDS = ("answer_variants",)

#: r1 协议版本标识（与 evaluation/protocol_v0_3_r1.md 首行一致）。
PROTOCOL_VERSION_R1 = "gate_a_protocol_v0_3_r1"


class FormalRunBlockedError(ValueError):
    """正式 Run 前置条件未满足（身份不干净 / r1 判定阈值未冻结）。

    与 `evaluation.identity.IdentityError` 同为代码级门禁；诊断模式不触发。
    """

#: 评测层错误 code（命名空间 `evaluation.*`，不进入产品 Issue code 表）。
EVAL_NO_GENERATION_UNDER_REVIEW = "evaluation.no_generation_under_review"
EVAL_METRIC_ERROR = "evaluation.metric_error"

#: 逐表行内容哈希的列投影（append-only 证据；sessions 是指针表、不在列）。
_TABLE_PROJECTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "messages": ("message_id", ("role", "content")),
    "intent_revisions": ("intent_revision_id", ("revision_json",)),
    "execution_revisions": ("execution_revision_id", ("revision_json",)),
    "confirmations": (
        "confirmation_id",
        ("intent_revision_id", "execution_revision_id", "summary_hash", "binding_hash"),
    ),
    "prompt_artifacts": ("prompt_artifact_id", ("refs_json", "payload")),
    "generation_artifacts": ("generation_id", ("refs_json", "payload")),
    "feedback_results": ("feedback_id", ("refs_json", "payload")),
    "realization_states": ("realization_id", ("refs_json", "payload")),
}


def build_eval_run_id(
    *,
    code_version: str,
    dataset_sha256: str,
    annotations_sha256: str,
    config_sha256: str,
    protocol_sha256: str,
    repetitions: int,
    run_nonce: str = "",
    protocol_version: str = GATE_A_PROTOCOL_VERSION,
) -> str:
    """Run ID：冻结输入 + 代码版本 + 重复次数的确定性哈希（同配置重放同 ID）。"""
    return make_eval_record_id(
        RUN_ID_PREFIX,
        code_version,
        EVALUATION_HARNESS_VERSION,
        EVAL_ID_SCHEME_VERSION,
        protocol_version,
        dataset_sha256,
        annotations_sha256,
        config_sha256,
        protocol_sha256,
        str(repetitions),
        run_nonce,
    )


# ---------------------------------------------------------------------------
# 记录包装器（只观察、不改行为；实现同一 Provider Protocol）
# ---------------------------------------------------------------------------


class _RawLLMCall:
    """一次 LLM 调用的原始观察（内部容器；落盘前转成 LLMCallObservation）。"""

    __slots__ = ("request", "response", "error", "latency_ms", "role")

    def __init__(
        self,
        *,
        role: str,
        request: LLMRequest,
        response: Any = None,
        error: ProviderError | None = None,
        latency_ms: float,
    ) -> None:
        self.role = role
        self.request = request
        self.response = response
        self.error = error
        self.latency_ms = latency_ms


class RecordingLLMProvider:
    """`LLMProvider` 记录包装器：逐次记录请求参数面 / 响应 / 延迟 / 错误。

    只包装、不干预：不重试、不改写、不缓存。`role` 由 Runner 在装配时固定
    （`interpreter` / `feedback_engine`），两个角色包装**同一个**底层 Provider
    （同一 Provider 条件），避免按提示词文本猜测角色。
    """

    def __init__(
        self,
        wrapped: LLMProvider,
        *,
        role: str,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._wrapped = wrapped
        self._role = role
        self._monotonic = monotonic or time.perf_counter
        self.calls: list[_RawLLMCall] = []

    def complete(self, request: LLMRequest):
        started = self._monotonic()
        try:
            response = self._wrapped.complete(request)
        except ProviderError as exc:
            self.calls.append(
                _RawLLMCall(
                    role=self._role,
                    request=request,
                    error=exc,
                    latency_ms=round((self._monotonic() - started) * 1000.0, 3),
                )
            )
            raise
        self.calls.append(
            _RawLLMCall(
                role=self._role,
                request=request,
                response=response,
                latency_ms=round((self._monotonic() - started) * 1000.0, 3),
            )
        )
        return response


class _RawImageCall:
    __slots__ = ("request", "response", "error", "latency_ms")

    def __init__(
        self,
        *,
        request: ImageGenerationRequest,
        response: Any = None,
        error: ProviderError | None = None,
        latency_ms: float,
    ) -> None:
        self.request = request
        self.response = response
        self.error = error
        self.latency_ms = latency_ms


class RecordingImageProvider:
    """`ImageProvider` 记录包装器（seed 只记 Provider 返回值，未返回记 None）。"""

    def __init__(
        self,
        wrapped: ImageProvider,
        *,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._wrapped = wrapped
        self._monotonic = monotonic or time.perf_counter
        self.calls: list[_RawImageCall] = []

    def generate(self, request: ImageGenerationRequest):
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


# ---------------------------------------------------------------------------
# System B 只读观察工具
# ---------------------------------------------------------------------------


def _read_path(intent: VisualIntent, path: str) -> Any:
    facet_name, _, field_name = path.partition(".")
    facet = getattr(intent, facet_name, None)
    return None if facet is None else getattr(facet, field_name, None)


def _intent_snapshots(
    repo: SQLiteRepository, session_id: str
) -> tuple[dict[str, Any], dict[str, str], list[str], str | None]:
    """当前 Intent 的 (值快照, Resolution 快照, pinned 排序列表, revision id)。"""
    snapshot = repo.get_current_session_snapshot(session_id)
    if snapshot.current_intent_revision_id is None:
        intent = VisualIntent()
        revision_id = None
    else:
        revision_id = snapshot.current_intent_revision_id
        intent = repo.get_intent_revision(revision_id).intent
    values = {path: _read_path(intent, path) for path in sorted(INTENT_PATHS)}
    resolutions = {
        path: record.resolution.value for path, record in intent.resolutions.items()
    }
    return values, resolutions, sorted(intent.pinned_paths), revision_id


def _snapshot_tables(repo: SQLiteRepository) -> TableSnapshot:
    """逐表只读快照：计数 + 逐行内容哈希（列投影见 `_TABLE_PROJECTIONS`）。"""
    counts: dict[str, int] = {}
    row_hashes: dict[str, dict[str, str]] = {}
    for table in HISTORY_TABLES:
        pk, columns = _TABLE_PROJECTIONS[table]
        rows = repo.connection.execute(
            f"SELECT {pk}, {', '.join(columns)} FROM {table}"  # noqa: S608 - 列表白名单常量
        ).fetchall()
        counts[table] = len(rows)
        hashed: dict[str, str] = {}
        for row in rows:
            row_id = str(row[0])
            canonical = json.dumps(list(row[1:]), ensure_ascii=False, sort_keys=True)
            hashed[row_id] = sha256_text(canonical)
        row_hashes[table] = hashed
    return TableSnapshot(counts=counts, row_hashes=row_hashes)


def _failure_from_exception(exc: Exception, *, stage: str) -> EvalFailureRecord:
    """把产品/Provider 异常转成结构化失败记录（code 原样保留）。"""
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or "." not in code:
        code = "evaluation.unexpected_error"
    return EvalFailureRecord(
        code=code,
        message=str(exc),
        stage=stage,  # type: ignore[arg-type]
        retryable=getattr(exc, "retryable", None),
        status_code=getattr(exc, "status_code", None),
        provider_request_id=getattr(exc, "provider_request_id", None),
    )


def _conflict_rule_ids(issues: Iterable[Any]) -> list[str]:
    """从 issue 列表提取归一化冲突 rule_id（去重、保序）。"""
    rule_ids: list[str] = []
    for issue in issues:
        rule_id = normalize_conflict_rule_id(str(issue.code))
        if rule_id is not None and rule_id not in rule_ids:
            rule_ids.append(rule_id)
    return rule_ids


def _flatten_deltas(deltas: Iterable[Any]) -> list[ObservedDelta]:
    return [
        ObservedDelta(
            operation=str(delta.operation.value),
            path=delta.path,
            value=delta.value,
            resolution=(delta.resolution.value if delta.resolution is not None else None),
        )
        for delta in deltas
    ]


def _flatten_issues(issues: Iterable[Any]) -> list[ObservedIssue]:
    return [
        ObservedIssue(
            code=issue.code,
            path=issue.path,
            severity=(issue.severity.value if issue.severity is not None else None),
        )
        for issue in issues
    ]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class EvaluationRunner:
    """A/B 评测 Runner（协议第 3 节驱动 + L1~L3 指标编排 + 统一记录落盘）。

    构造参数：

    - `settings`：Provider 模型名/超时/重试的唯一来源（不读环境变量；CLI 入口除外）；
    - `llm_factory` / `image_factory`：每次（系统 × 重复）取一个全新 Provider 实例
      （真实运行：由同一 `Settings` 构造 adapter；测试：脚本化 Fake）；
    - `output_root`：运行产物根目录（默认 `evaluation/runs`；测试传 `tmp_path`）；
    - `repetitions`：L1~L3 每案例重复次数（默认读冻结配置 = 2）；
    - `l4_repetition`：图片进入 L4 的重复序次（默认 1；每次重复都生成图片，仅该次
      标记 `designated_for_l4`；A/B 对称）；
    - `run_nonce`：区分同配置的多次运行（默认空串 = 内容寻址的确定性运行）；
    - `verify_frozen`：运行前按 `frozen_manifest_v0_3.json` 复核四个冻结文件哈希
      （默认 True；测试用合成数据集时传 False）；
    - `clock` / `monotonic`：时间源注入（测试注入固定值实现确定性重放）。

    公开方法：

    - `run(case_ids=None) -> EvalRunResult`：执行全量（或指定子集）A/B 评测并落盘。
    """

    def __init__(
        self,
        *,
        settings: Settings,
        llm_factory: Callable[[], LLMProvider],
        image_factory: Callable[[], ImageProvider],
        output_root: Path = DEFAULT_RUNS_ROOT,
        config_path: Path = DEFAULT_CONFIG_PATH,
        dataset_path: Path = DEFAULT_DATASET_PATH,
        annotations_path: Path = DEFAULT_ANNOTATIONS_PATH,
        protocol_path: Path = DEFAULT_PROTOCOL_PATH,
        manifest_path: Path = DEFAULT_MANIFEST_PATH,
        repetitions: int | None = None,
        l4_repetition: int = 1,
        run_nonce: str = "",
        code_version: str | None = None,
        verify_frozen: bool = True,
        resolve_from_manifest: bool = False,
        identity: CodeIdentity | None = None,
        protocol_version: str | None = None,
        metric_version: str | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._llm_factory = llm_factory
        self._image_factory = image_factory
        self._output_root = Path(output_root)
        self._manifest_path = Path(manifest_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.perf_counter

        # R1-B #9：`--manifest` 由清单解析协议/配置/数据集/标注；未指定时沿用显式
        # 路径（旧行为向后兼容，测试的合成输入不受影响）。
        self._manifest: RunManifest | None = None
        if resolve_from_manifest:
            self._manifest = load_manifest(self._manifest_path, base_dir=PROJECT_ROOT)
            self._config_path = self._manifest.config_path
            self._dataset_path = self._manifest.dataset_path
            self._annotations_path = self._manifest.annotations_path
            self._protocol_path = self._manifest.protocol_path
        else:
            self._config_path = Path(config_path)
            self._dataset_path = Path(dataset_path)
            self._annotations_path = Path(annotations_path)
            self._protocol_path = Path(protocol_path)

        self._protocol_version = protocol_version or (
            self._manifest.protocol_version
            if self._manifest is not None
            else GATE_A_PROTOCOL_VERSION
        )
        self._metric_version = metric_version or (
            METRIC_VERSION_R1
            if self._protocol_version == PROTOCOL_VERSION_R1
            else METRIC_VERSION_V1
        )

        self._config_payload = self._load_config_payload()
        if repetitions is None:
            repetitions = int(
                self._config_payload["repetitions"]["llm_layer_runs_per_case"]
            )
        if repetitions < 1:
            raise ValueError(f"repetitions must be >= 1, got {repetitions}")
        if not 1 <= l4_repetition <= repetitions:
            raise ValueError(
                f"l4_repetition must be within [1, {repetitions}], got {l4_repetition}"
            )
        self._repetitions = repetitions
        self._l4_repetition = l4_repetition
        self._run_nonce = run_nonce
        # R1-B #10：正式运行身份（commit + dirty）由调用方注入；显式 code_version
        # 优先（测试确定性），其次身份解析值，最后包版本。
        self._identity = identity
        if identity is not None and code_version is None:
            code_version = identity.code_version
        self._code_version = code_version or (
            f"{default_code_version()}+{EVALUATION_HARNESS_VERSION}"
        )

        # R1-B #9/#10：正式模式**构造时**即拒绝关闭冻结哈希校验——正式运行必须
        # 校验全部冻结输入，不能用 verify_frozen=False 绕过；诊断/合成测试
        # （identity 为 None 或 mode=unspecified）不受影响。
        if (
            self._identity is not None
            and self._identity.mode == IDENTITY_FORMAL
            and not verify_frozen
        ):
            raise FormalRunBlockedError(
                "formal run refuses verify_frozen=False: frozen input hash verification "
                "cannot be disabled for a gate-eligible run; use --diagnostic for a "
                "non-gate diagnostic run"
            )

        self._hashes = self._compute_hashes(verify_frozen=verify_frozen)
        self._run_id = build_eval_run_id(
            code_version=self._code_version,
            dataset_sha256=self._hashes["dataset"],
            annotations_sha256=self._hashes["annotations"],
            config_sha256=self._hashes["config"],
            protocol_sha256=self._hashes["protocol"],
            repetitions=self._repetitions,
            run_nonce=run_nonce,
            protocol_version=self._protocol_version,
        )

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
    def protocol_version(self) -> str:
        return self._protocol_version

    @property
    def metric_version(self) -> str:
        return self._metric_version

    @property
    def manifest(self) -> RunManifest | None:
        return self._manifest

    # -- 冻结输入与哈希 ------------------------------------------------------

    def _load_config_payload(self) -> dict[str, Any]:
        try:
            return json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load evaluation config {self._config_path}: {exc}") from exc

    def _compute_hashes(self, *, verify_frozen: bool) -> dict[str, str]:
        if self._manifest is not None:
            # 清单驱动：正式运行必须校验全部哈希（不提供关闭开关）。
            if verify_frozen:
                hashes = self._manifest.verify_hashes()
            else:
                hashes = {"manifest": sha256_file(self._manifest.path)}
                for role in ("protocol", "config", "dataset", "annotations"):
                    hashes[role] = sha256_file(self._manifest.resolve(role))
            self._manifest_version = self._manifest.manifest_version
            return hashes

        hashes = {
            "dataset": sha256_file(self._dataset_path),
            "annotations": sha256_file(self._annotations_path),
            "config": sha256_file(self._config_path),
            "protocol": sha256_file(self._protocol_path),
        }
        manifest: dict[str, Any] = {}
        if self._manifest_path.is_file():
            try:
                manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
                hashes["manifest"] = sha256_file(self._manifest_path)
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"cannot load frozen manifest: {exc}") from exc
        elif verify_frozen:
            raise ValueError(
                f"frozen manifest {self._manifest_path} is required when verify_frozen=True"
            )
        else:
            hashes["manifest"] = ""
        self._manifest_version = str(manifest.get("manifest_version", "unverified"))
        if verify_frozen:
            expected_files = manifest.get("files", {})
            actual_by_rel = {
                "evaluation/protocol.md": hashes["protocol"],
                "evaluation/fixtures/core_v0_3.jsonl": hashes["dataset"],
                "evaluation/annotations/core_v0_3.jsonl": hashes["annotations"],
                "evaluation/configs/gate_a_v0_3.json": hashes["config"],
            }
            for rel_path, actual in actual_by_rel.items():
                expected = expected_files.get(rel_path)
                if expected is None:
                    raise ValueError(f"frozen manifest has no entry for {rel_path}")
                if expected != actual:
                    raise ValueError(
                        f"frozen file hash mismatch for {rel_path}: manifest={expected} "
                        f"actual={actual}; the frozen inputs must not be modified "
                        "(issue a new dataset/config version instead)"
                    )
        return hashes

    def _provider_snapshot(self) -> EvalProviderSnapshot:
        credentials = self._config_payload.get("credentials", {})
        llm_cfg = self._config_payload.get("llm", {})
        image_cfg = self._config_payload.get("image", {})
        return EvalProviderSnapshot(
            llm_model=self._settings.llm_model,
            image_model=self._settings.image_model,
            llm_timeout_seconds=self._settings.llm_timeout_seconds,
            image_timeout_seconds=self._settings.image_timeout_seconds,
            http_max_retries=self._settings.http_max_retries,
            image_size=str(image_cfg.get("size", "1024x1024")),
            images_per_generation=int(image_cfg.get("images_per_generation", 1)),
            base_url_env=str(credentials.get("base_url_env", "VIA_PROVIDER_BASE_URL")),
            api_key_env=str(credentials.get("api_key_env", "VIA_PROVIDER_API_KEY")),
            system_b_response_format=llm_cfg.get("system_b_response_format"),
        )

    # -- 主入口 --------------------------------------------------------------

    def _enforce_formal_prerequisites(self) -> None:
        """r1 正式 Run 门禁：判定阈值未冻结时拒绝启动（代码级，不是文档声明）。

        诊断 Run（`identity_mode=diagnostic`）与 v0_3 旧协议不受影响。
        """
        if self._identity is None or self._identity.mode != IDENTITY_FORMAL:
            return
        if self._protocol_version != PROTOCOL_VERSION_R1:
            return
        thresholds = (self._config_payload.get("metrics") or {}).get("gate_thresholds") or {}
        status = thresholds.get("status")
        if status != "frozen":
            raise FormalRunBlockedError(
                "r1 formal run refused: Gate A decision thresholds are not frozen "
                f"(config metrics.gate_thresholds.status={status!r}). Freeze improvement "
                "metric, minimum effect size, clarification/waiting cost cap and "
                "missing-data handling before the formal run; use --diagnostic for a "
                "non-gate diagnostic run."
            )

    def _load_inputs(self) -> tuple[list[BaselineCase], dict[str, CaseAnnotation]]:
        """读入数据集与标注；r1 拒绝任何 `answer_variants`（A/B 必须同一冻结消息）。"""
        dataset = load_dataset(self._dataset_path)
        annotations = {a.case_id: a for a in load_annotations(self._annotations_path)}
        if self._protocol_version == PROTOCOL_VERSION_R1:
            offenders = [
                f"{case.case_id}/{turn.turn_id}"
                for case in dataset
                for turn in case.turns
                if turn.answer_variants
            ]
            if offenders:
                raise ValueError(
                    "r1 dataset must use one frozen user message for A/B; "
                    "answer_variants is not allowed (offending turns: "
                    + ", ".join(sorted(offenders))
                    + "). Move dynamic answers to an independent interaction diagnostic."
                )
        return dataset, annotations

    def run(self, case_ids: Sequence[str] | None = None) -> EvalRunResult:
        """执行 A/B 评测并落盘（`accept` 轮 Baseline 不执行，协议第 3 节）。"""
        # R1-B #10：正式运行必须能解析干净身份；诊断运行显式跳过门禁。
        if self._identity is not None:
            enforce_formal_identity(self._identity)
        self._enforce_formal_prerequisites()

        dataset, annotations = self._load_inputs()
        if case_ids is not None:
            unknown = sorted(set(case_ids) - {case.case_id for case in dataset})
            if unknown:
                raise ValueError(f"unknown case_ids: {unknown}")
            wanted = set(case_ids)
            cases = [case for case in dataset if case.case_id in wanted]
        else:
            cases = list(dataset)
        for case in cases:
            if case.case_id not in annotations:
                raise ValueError(f"missing annotation for case {case.case_id!r}")

        run_dir = self.run_dir
        if run_dir.exists():
            # run_id 是内容寻址的：同配置重跑即重放，替换同配置旧产物，
            # 保证 System B 每案例 SQLite 全新（无陈旧行）。
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        case_records: list[EvalCaseRecord] = []
        turn_records: list[EvalTurnRecord] = []
        metric_records: list[MetricRecord] = []

        baseline_run_records: list[Any] = []
        for repetition in range(1, self._repetitions + 1):
            rep_cases, rep_turns, rep_metrics, baseline_run = self._run_baseline_repetition(
                cases, annotations, repetition, run_dir
            )
            baseline_run_records.append(baseline_run)
            case_records.extend(rep_cases)
            turn_records.extend(rep_turns)
            metric_records.extend(rep_metrics)

        for repetition in range(1, self._repetitions + 1):
            for case in cases:
                rep_case, rep_turns, rep_metrics = self._run_system_b_case(
                    case, annotations[case.case_id], repetition, run_dir
                )
                case_records.append(rep_case)
                turn_records.extend(rep_turns)
                metric_records.extend(rep_metrics)

        first_baseline_run = baseline_run_records[0]
        run_record = EvalRunRecord(
            run_id=self._run_id,
            harness_version=EVALUATION_HARNESS_VERSION,
            id_scheme_version=EVAL_ID_SCHEME_VERSION,
            code_version=self._code_version,
            code_commit=(self._identity.commit if self._identity is not None else None),
            code_dirty=(self._identity.dirty if self._identity is not None else None),
            identity_mode=(
                self._identity.mode if self._identity is not None else "unspecified"
            ),
            metric_version=self._metric_version,
            run_nonce=self._run_nonce,
            protocol_version=self._protocol_version,
            dataset_version=first_baseline_run.dataset_version,
            config_version=str(self._config_payload["config_version"]),
            manifest_version=self._manifest_version,
            dataset_sha256=self._hashes["dataset"],
            annotations_sha256=self._hashes["annotations"],
            config_sha256=self._hashes["config"],
            protocol_sha256=self._hashes["protocol"],
            manifest_sha256=self._hashes["manifest"],
            provider=self._provider_snapshot(),
            repetitions=self._repetitions,
            l4_repetition=self._l4_repetition,
            systems=["baseline_a", "system_b"],
            case_ids=[case.case_id for case in cases],
            case_count=len(cases),
            created_at=self._clock(),
        )
        summary = aggregate_run(run_record, case_records, metric_records, created_at=self._clock())

        write_json(run_dir / "run.json", run_record)
        write_json(run_dir / "summary.json", summary)
        for case in cases:
            write_case_jsonl(
                run_dir / "cases" / f"{case.case_id}.jsonl",
                [r for r in case_records if r.case_id == case.case_id],
                [r for r in turn_records if r.case_id == case.case_id],
                [r for r in metric_records if r.case_id == case.case_id],
            )
        return EvalRunResult(
            run=run_record,
            cases=case_records,
            turns=turn_records,
            metrics=metric_records,
            summary=summary,
        )

    # -----------------------------------------------------------------------
    # Baseline A（复用 Step 02 执行器；逐重复一个 DirectBaselineRunner）
    # -----------------------------------------------------------------------

    def _run_baseline_repetition(
        self,
        cases: list[BaselineCase],
        annotations: dict[str, CaseAnnotation],
        repetition: int,
        run_dir: Path,
    ) -> tuple[list[EvalCaseRecord], list[EvalTurnRecord], list[MetricRecord], Any]:
        output_dir = run_dir / "baseline_a" / f"rep{repetition}"
        runner = DirectBaselineRunner(
            llm=self._llm_factory(),
            image=self._image_factory(),
            settings=self._settings,
            output_dir=output_dir,
            config_path=self._config_path,
            dataset_path=self._dataset_path,
            protocol_path=self._protocol_path,
            code_version=self._code_version,
            run_nonce=f"rep{repetition}",
            protocol_version=self._protocol_version,
            clock=self._clock,
            monotonic=self._monotonic,
        )
        result = runner.run_cases(cases)

        case_records: list[EvalCaseRecord] = []
        turn_records: list[EvalTurnRecord] = []
        metric_records: list[MetricRecord] = []
        for case, record in zip(cases, result.cases, strict=True):
            case_record, turns, metrics = self._map_baseline_case(
                case, annotations[case.case_id], record, repetition, output_dir, run_dir
            )
            case_records.append(case_record)
            turn_records.extend(turns)
            metric_records.extend(metrics)
        return case_records, turn_records, metric_records, result.run

    def _map_baseline_case(
        self,
        case: BaselineCase,
        annotation: CaseAnnotation,
        record: BaselineCaseRecord,
        repetition: int,
        output_dir: Path,
        run_dir: Path,
    ) -> tuple[EvalCaseRecord, list[EvalTurnRecord], list[MetricRecord]]:
        turn_records: list[EvalTurnRecord] = []
        for turn_record in record.turns:
            turn_records.append(
                self._map_baseline_turn(
                    case, annotation, turn_record, repetition, run_dir, output_dir
                )
            )
        case_status = record.status  # completed / failed（Baseline 无 flow_deviation 概念）
        first_error = next((t.error for t in turn_records if t.error is not None), None)
        case_record = EvalCaseRecord(
            record_id=make_eval_record_id(
                CASE_RECORD_ID_PREFIX, self._run_id, case.case_id, "baseline_a", str(repetition)
            ),
            run_id=self._run_id,
            case_id=case.case_id,
            system="baseline_a",
            repetition=repetition,
            scenario=case.scenario,
            turn_type=case.turn_type,
            status=case_status,
            l1_failed=None,
            turn_record_ids=[t.record_id for t in turn_records],
            flow_deviation_turns=[],
            artifact_refs=[
                ArtifactRef(
                    artifact_ref_id=make_eval_record_id(
                        ARTIFACT_REF_ID_PREFIX, self._run_id, case.case_id, "baseline_a",
                        str(repetition), "case_record",
                    ),
                    kind="baseline_case_record",
                    ref_id=record.record_id,
                    path=_relative_to(
                        output_dir / "cases" / case.case_id / "case.json", run_dir
                    ),
                )
            ],
            error=first_error if case_status == "failed" else None,
            created_at=self._clock(),
        )
        metrics = self._baseline_metrics(case, annotation, record, turn_records, repetition)
        return case_record, turn_records, metrics

    def _map_baseline_turn(
        self,
        case: BaselineCase,
        annotation: CaseAnnotation,
        record: BaselineTurnRecord,
        repetition: int,
        run_dir: Path,
        output_dir: Path,
    ) -> EvalTurnRecord:
        turn_annotation = annotation.turn_annotation(record.turn_id)
        # 统一 Artifact 引用合同：Baseline 的调用细节（model/temperature/max_tokens/
        # response_format/provider_request_id/seed/延迟/重试）不复制，以
        # `baseline_turn_record_id` + 逐轮原始 JSON 路径双重引用 Step 02 记录
        #（协议第 7 节；该文件在 run 目录内，可脱机复核字节）。
        artifact_refs: list[ArtifactRef] = [
            ArtifactRef(
                artifact_ref_id=make_eval_record_id(
                    ARTIFACT_REF_ID_PREFIX, self._run_id, case.case_id, "baseline_a",
                    str(repetition), record.turn_id, "baseline_turn_record",
                ),
                kind="baseline_turn_record",
                ref_id=record.record_id,
                path=_relative_to(
                    output_dir / "cases" / case.case_id / "turns"
                    / f"turn_{record.turn_index:02d}.json",
                    run_dir,
                ),
                note="Step 02 逐轮原始记录（LLM/图像调用随机性字段的落盘位置）",
            )
        ]
        if record.prompt is not None:
            artifact_refs.append(
                ArtifactRef(
                    artifact_ref_id=make_eval_record_id(
                        ARTIFACT_REF_ID_PREFIX, self._run_id, case.case_id, "baseline_a",
                        str(repetition), record.turn_id, "prompt",
                    ),
                    kind="baseline_prompt",
                    ref_id=record.prompt.prompt_id,
                    sha256=record.prompt.text_sha256,
                )
            )
        if record.image_call is not None:
            for image in record.image_call.images:
                artifact_refs.append(
                    ArtifactRef(
                        artifact_ref_id=make_eval_record_id(
                            ARTIFACT_REF_ID_PREFIX, self._run_id, case.case_id, "baseline_a",
                            str(repetition), image.artifact_id,
                        ),
                        kind="baseline_image",
                        ref_id=image.artifact_id,
                        path=_relative_to(
                            run_dir / "baseline_a" / f"rep{repetition}" / image.relative_path,
                            run_dir,
                        ),
                        sha256=image.sha256,
                        designated_for_l4=(repetition == self._l4_repetition),
                    )
                )
        error = None
        if record.error is not None:
            error = EvalFailureRecord(
                code=record.error.code,
                message=record.error.message,
                stage=record.error.stage,
                retryable=record.error.retryable,
                status_code=record.error.status_code,
                provider_request_id=record.error.provider_request_id,
            )
        return EvalTurnRecord(
            record_id=make_eval_record_id(
                TURN_RECORD_ID_PREFIX, self._run_id, case.case_id, "baseline_a",
                str(repetition), record.turn_id,
            ),
            run_id=self._run_id,
            case_id=case.case_id,
            system="baseline_a",
            repetition=repetition,
            turn_id=record.turn_id,
            turn_index=record.turn_index,
            turn_kind=record.turn_kind,
            input_text=None if record.skipped else record.input_text,
            input_source="not_executed" if record.skipped else "user_text",
            status=record.status,
            flow_deviation=False,
            expected_outcome=(
                turn_annotation.expected_outcome if turn_annotation is not None else None
            ),
            actual_outcome=None,  # Baseline 无状态机结局概念（协议第 3 节）
            outcome_matches=None,
            error=error,
            baseline_turn_record_id=record.record_id,
            prompt_text=record.prompt.text if record.prompt is not None else None,
            prompt_sha256=record.prompt.text_sha256 if record.prompt is not None else None,
            llm_calls=[],
            image_calls=[],
            system_b=None,
            artifact_refs=artifact_refs,
            latency_ms=(
                (record.llm_call.latency_ms if record.llm_call else 0.0)
                + (record.image_call.latency_ms if record.image_call else 0.0)
            ),
            created_at=record.created_at,
        )

    def _baseline_metrics(
        self,
        case: BaselineCase,
        annotation: CaseAnnotation,
        record: BaselineCaseRecord,
        turn_records: list[EvalTurnRecord],
        repetition: int,
    ) -> list[MetricRecord]:
        """Baseline 指标：L1/L2 一律 not_applicable（协议第 3 节）；L3 同口径计算。"""
        records: list[MetricRecord] = []
        for metric in (*L1_INVARIANTS, *L2_METRICS):
            layer = "L1" if metric in L1_INVARIANTS else "L2"
            records.append(
                self._metric_record(
                    case, "baseline_a", repetition, layer, metric,
                    MetricPayload(
                        status="not_applicable",
                        details={
                            "reason": "baseline has no clarification/confirmation/state "
                            "machinery (protocol section 3)"
                        },
                    ),
                    evidence_refs=[],
                )
            )
        # Baseline 的图像请求面在 Step 02 记录中（size/model_requested 逐轮记录）；
        # 按引用契约在此展开为 L3 Model Compatibility 的输入。
        request_faces = [
            PromptRequestFace(
                turn_id=t.turn_id,
                size=t.image_call.size,
                model=t.image_call.model_requested or "",
                extra_parameters_free=True,
            )
            for t in record.turns
            if t.image_call is not None
        ]
        records.extend(
            self._l3_metrics(
                case, annotation, "baseline_a", repetition, turn_records,
                request_faces=request_faces,
            )
        )
        return records

    # -----------------------------------------------------------------------
    # System B（v0.2 公开接口，零修改；每案例全新 SQLite 与会话）
    # -----------------------------------------------------------------------

    def _run_system_b_case(
        self,
        case: BaselineCase,
        annotation: CaseAnnotation,
        repetition: int,
        run_dir: Path,
    ) -> tuple[EvalCaseRecord, list[EvalTurnRecord], list[MetricRecord]]:
        case_dir = run_dir / "system_b" / f"rep{repetition}" / "cases" / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        db_path = case_dir / "session.db"
        repo = SQLiteRepository(db_path)

        base_llm = self._llm_factory()
        interpreter_llm = RecordingLLMProvider(base_llm, role="interpreter", monotonic=self._monotonic)
        feedback_llm = RecordingLLMProvider(base_llm, role="feedback_engine", monotonic=self._monotonic)
        image_provider = RecordingImageProvider(self._image_factory(), monotonic=self._monotonic)

        workflow = WorkflowService(
            repo=repo,
            intent_engine=IntentEngine(Interpreter(interpreter_llm)),
            question_builder=QuestionBuilder(),  # 协议第 1 节：确定性模板，不注入 LLM
            settings=self._settings,
        )
        review = ReviewService(
            repo=repo,
            feedback_engine=FeedbackEngine(feedback_llm),
            question_builder=QuestionBuilder(),
        )
        pipeline = GenerationPipeline(
            repo=repo,
            prompt_engine=PromptEngine(QwenImageRenderer(), repo),
            image_provider=image_provider,
            output_dir=case_dir / "images",
        )

        session_id = workflow.create_session().session_id
        turn_records: list[EvalTurnRecord] = []
        l1_evidences: list[L1TurnEvidence] = []
        prior_confirmations: list[ObservedConfirmation] = []
        guard_triggered = 0
        # R1-B #4：首次根因之后无法执行的脚本轮记 blocked（不重复记独立根因、
        # 不自动改送反馈入口）。
        first_failure_turn_id: str | None = None
        try:
            for turn_index, turn in enumerate(case.turns, start=1):
                if first_failure_turn_id is not None:
                    turn_records.append(
                        self._blocked_turn_record(
                            case=case,
                            annotation=annotation.turn_annotation(turn.turn_id),
                            turn=turn,
                            turn_index=turn_index,
                            repetition=repetition,
                            blocked_by_turn_id=first_failure_turn_id,
                        )
                    )
                    continue
                turn_record, evidence, confirmations, guard_hits = self._drive_system_b_turn(
                    case=case,
                    annotation=annotation.turn_annotation(turn.turn_id),
                    turn=turn,
                    turn_index=turn_index,
                    repetition=repetition,
                    repo=repo,
                    workflow=workflow,
                    review=review,
                    pipeline=pipeline,
                    session_id=session_id,
                    prior_confirmations=prior_confirmations,
                    interpreter_llm=interpreter_llm,
                    feedback_llm=feedback_llm,
                    image_provider=image_provider,
                    run_dir=run_dir,
                    case_dir=case_dir,
                )
                turn_records.append(turn_record)
                l1_evidences.append(evidence)
                prior_confirmations.extend(confirmations)
                guard_triggered += guard_hits
                if turn_record.status == "failed":
                    first_failure_turn_id = turn.turn_id
        finally:
            repo.close()

        # ---- 指标 ----------------------------------------------------------
        l1_evaluation = evaluate_case_l1(case.case_id, l1_evidences)
        turn_inputs = [
            TurnObservationInput(
                turn_id=record.turn_id,
                turn_index=record.turn_index,
                annotation=annotation.turn_annotation(record.turn_id),
                observation=record.system_b,
                turn_failed=record.status == "failed",
            )
            for record in turn_records
            # blocked 轮未执行、无独立观察；不重复计入 L2 根因（R1-B #4）。
            if record.status != "blocked"
        ]
        metrics: list[MetricRecord] = []
        for outcome in l1_evaluation.outcomes:
            details: dict[str, Any] = {
                "violations": [v.model_dump(mode="json") for v in outcome.violations]
            }
            if outcome.invariant == L1_INVARIANT_PINNED:
                details["set_on_pinned_events"] = l1_evaluation.set_on_pinned_events
            metrics.append(
                self._metric_record(
                    case, "system_b", repetition, "L1", outcome.invariant,
                    MetricPayload(
                        status="ok",
                        passed=outcome.passed,
                        counts={"violations": len(outcome.violations)},
                        details=details,
                    ),
                    evidence_refs=[t.record_id for t in turn_records],
                )
            )
        l2_thunks: dict[str, Callable[[], MetricPayload]] = {
            "delta_accuracy": lambda: evaluate_delta_accuracy(turn_inputs),
            "missing_decision_recall": lambda: evaluate_missing_decision_recall(turn_inputs),
            "clarification_precision": lambda: evaluate_clarification_precision(turn_inputs),
            "conflict_detection": lambda: evaluate_conflict_detection(turn_inputs),
            "delegation_scope_accuracy": lambda: evaluate_delegation_scope(turn_inputs),
        }
        for metric in L2_METRICS:
            metrics.append(
                self._guarded_metric_record(
                    case, "system_b", repetition, "L2", metric,
                    l2_thunks[metric],
                    turn_records,
                )
            )
        metrics.extend(
            self._l3_metrics(
                case, annotation, "system_b", repetition, turn_records,
                guard_triggered=guard_triggered,
            )
        )

        failed_turns = [t for t in turn_records if t.status == "failed"]
        deviation_turns = [t.turn_id for t in turn_records if t.flow_deviation]
        if failed_turns:
            case_status = "failed"
        elif deviation_turns:
            case_status = "flow_deviation"
        else:
            case_status = "completed"
        case_record = EvalCaseRecord(
            record_id=make_eval_record_id(
                CASE_RECORD_ID_PREFIX, self._run_id, case.case_id, "system_b", str(repetition)
            ),
            run_id=self._run_id,
            case_id=case.case_id,
            system="system_b",
            repetition=repetition,
            scenario=case.scenario,
            turn_type=case.turn_type,
            status=case_status,
            l1_failed=l1_evaluation.l1_failed,
            turn_record_ids=[t.record_id for t in turn_records],
            flow_deviation_turns=deviation_turns,
            artifact_refs=[
                ArtifactRef(
                    artifact_ref_id=make_eval_record_id(
                        ARTIFACT_REF_ID_PREFIX, self._run_id, case.case_id, "system_b",
                        str(repetition), "sqlite",
                    ),
                    kind="sqlite_db",
                    ref_id=f"system_b/rep{repetition}/cases/{case.case_id}/session.db",
                    path=_relative_to(db_path, run_dir),
                    note="System B 全量原始状态（L1 历史不变量的原始证据）",
                )
            ],
            error=(failed_turns[0].error if failed_turns else None),
            created_at=self._clock(),
        )
        return case_record, turn_records, metrics

    # -- System B 单轮驱动 ----------------------------------------------------

    def _blocked_turn_record(
        self,
        *,
        case: BaselineCase,
        annotation: TurnAnnotation | None,
        turn: BaselineTurn,
        turn_index: int,
        repetition: int,
        blocked_by_turn_id: str,
    ) -> EvalTurnRecord:
        """首次根因之后无法执行的脚本轮：不驱动、不产生独立 error（R1-B #4）。"""
        return EvalTurnRecord(
            record_id=make_eval_record_id(
                TURN_RECORD_ID_PREFIX, self._run_id, case.case_id, "system_b",
                str(repetition), turn.turn_id,
            ),
            run_id=self._run_id,
            case_id=case.case_id,
            system="system_b",
            repetition=repetition,
            turn_id=turn.turn_id,
            turn_index=turn_index,
            turn_kind=turn.kind,
            input_text=None,
            input_source="not_executed",
            status="blocked",
            blocked_by_prior_failure=True,
            blocked_by_turn_id=blocked_by_turn_id,
            expected_outcome=(annotation.expected_outcome if annotation is not None else None),
            actual_outcome=None,
            outcome_matches=None,
            created_at=self._clock(),
        )

    def _drive_system_b_turn(
        self,
        *,
        case: BaselineCase,
        annotation: TurnAnnotation | None,
        turn: BaselineTurn,
        turn_index: int,
        repetition: int,
        repo: SQLiteRepository,
        workflow: WorkflowService,
        review: ReviewService,
        pipeline: GenerationPipeline,
        session_id: str,
        prior_confirmations: list[ObservedConfirmation],
        interpreter_llm: RecordingLLMProvider,
        feedback_llm: RecordingLLMProvider,
        image_provider: RecordingImageProvider,
        run_dir: Path,
        case_dir: Path,
    ) -> tuple[EvalTurnRecord, L1TurnEvidence, list[ObservedConfirmation], int]:
        started = self._monotonic()
        snapshot_before = repo.get_current_session_snapshot(session_id)
        state_before = snapshot_before.workflow_state
        values_before, resolutions_before, pinned_before, revision_before = _intent_snapshots(
            repo, session_id
        )
        tables_before = _snapshot_tables(repo)
        llm_calls_before = (len(interpreter_llm.calls), len(feedback_llm.calls))
        image_calls_before = len(image_provider.calls)

        # ---- 输入文本与 flow_deviation（协议第 3 节第 3 条） ----------------
        input_text = turn.user_text
        input_source = "user_text"
        flow_deviation = False
        deviation_reasons: list[str] = []
        pending_target_before: str | None = None
        if snapshot_before.pending_question_payload:
            pending_target_before = json.loads(
                snapshot_before.pending_question_payload
            ).get("target_path")

        if turn.kind == "clarification_answer":
            if state_before is WorkflowState.WAITING_CLARIFICATION and pending_target_before:
                variants = turn.answer_variants or {}
                if variants:
                    if pending_target_before in variants:
                        input_text = variants[pending_target_before]
                        input_source = "answer_variant"
                    else:
                        flow_deviation = True
                        deviation_reasons.append(
                            "answer_variants has no entry for the pending question "
                            f"target_path {pending_target_before!r}; fell back to user_text"
                        )
            else:
                flow_deviation = True
                deviation_reasons.append(
                    f"scripted clarification_answer but the session is {state_before.value} "
                    "(no pending clarification)"
                )
        elif turn.kind == "user_message":
            if state_before is WorkflowState.WAITING_CLARIFICATION:
                flow_deviation = True
                deviation_reasons.append(
                    "session was WAITING_CLARIFICATION but the scripted turn is a "
                    "user_message (not an answer); submitted verbatim"
                )
        else:  # image_feedback / accept
            if state_before is not WorkflowState.WAITING_REVIEW:
                flow_deviation = True
                deviation_reasons.append(
                    f"scripted {turn.kind} but the session is {state_before.value} "
                    "(not WAITING_REVIEW)"
                )

        # ---- 驱动 ------------------------------------------------------------
        error: EvalFailureRecord | None = None
        resolution = None
        pending_question = None
        feedback_result = None
        carry_carried: list[str] = []
        carry_invalidated: list[str] = []
        realization_state_id: str | None = None
        recoverable_failure: bool | None = None
        failure_codes: list[str] = []
        confirmations_created: list[ObservedConfirmation] = []
        generations: list[ObservedGeneration] = []
        stale_probe = "not_applicable"
        bad_hash_probe = "not_applicable"
        guard_hits = 0

        try:
            if turn.kind in ("user_message", "clarification_answer"):
                outcome = workflow.submit_message(session_id, input_text)
                resolution = outcome.resolution
                pending_question = outcome.pending_question
                # B 工包可恢复失败接口：显式标志优先于“有无 pending_question”。
                recoverable_failure = getattr(outcome, "recoverable_failure", None)
                failure_codes = list(getattr(outcome, "failure_codes", ()) or ())
            else:
                generation_id = self._latest_generation_id(repo, session_id)
                if generation_id is None:
                    raise _EvaluationDriveError(
                        EVAL_NO_GENERATION_UNDER_REVIEW,
                        "the script expects a generation under review but none exists",
                    )
                outcome = review.submit_feedback(session_id, generation_id, input_text)
                feedback_result = outcome.feedback
                resolution = outcome.resolution
                pending_question = outcome.pending_question
                if outcome.carry is not None:
                    carry_carried = [v.path for v in outcome.carry.carried_values]
                    carry_invalidated = [v.path for v in outcome.carry.invalidated_values]
                realization_state_id = outcome.realization_state_id
                recoverable_failure = outcome.recoverable_failure
                failure_codes = list(getattr(outcome, "failure_codes", ()) or ())

            snapshot_now = repo.get_current_session_snapshot(session_id)
            if snapshot_now.workflow_state is WorkflowState.WAITING_CONFIRMATION:
                stale_probe = self._probe_stale_revision(
                    workflow, session_id, snapshot_now, prior_confirmations
                )
                bad_hash_probe = self._probe_bad_hash(workflow, session_id, snapshot_now)
                # 确认一旦落库即计入本轮（即使随后的 generate 失败），保证
                # 观察与库内事实一致；generate 抛错由 except 分支记 failed。
                self._auto_confirm_and_generate(
                    workflow=workflow,
                    pipeline=pipeline,
                    repo=repo,
                    session_id=session_id,
                    run_dir=run_dir,
                    confirmations_created=confirmations_created,
                    generations=generations,
                )
        except _EvaluationDriveError as exc:
            error = EvalFailureRecord(code=exc.code, message=str(exc), stage="runner")
        except WorkflowError as exc:
            error = _failure_from_exception(exc, stage="workflow")
        except PromptCompilationError as exc:
            if exc.code == PROMPT_UNAUTHORIZED_ADDITION:
                guard_hits += 1
            error = _failure_from_exception(exc, stage="prompt")
        except GenerationError as exc:
            error = _failure_from_exception(exc, stage="generation")
        except ProviderError as exc:
            error = _failure_from_exception(exc, stage="image")

        # ---- 观察 ------------------------------------------------------------
        snapshot_after = repo.get_current_session_snapshot(session_id)
        state_after = snapshot_after.workflow_state
        values_after, resolutions_after, pinned_after, revision_after = _intent_snapshots(
            repo, session_id
        )
        tables_after = _snapshot_tables(repo)
        status = "failed" if error is not None else "completed"
        # B 工包可恢复失败：r1 口径下显式 `recoverable_failure=True` 即“本轮未处理成功”，
        # 不能凭 pending_question 是否存在推断成功（R1-C / R1-B #4）。
        # v0_3 旧口径把 LLM 解析失败记为被测系统行为（status=completed + 结局偏离），
        # 为向后兼容不改旧行为。
        if (
            error is None
            and recoverable_failure
            and self._protocol_version == PROTOCOL_VERSION_R1
        ):
            code = failure_codes[0] if failure_codes else "workflow.recoverable_failure"
            error = EvalFailureRecord(
                code=code,
                message=(
                    "the system reported a recoverable failure for this turn; "
                    f"failure_codes={failure_codes!r}"
                ),
                stage="workflow",
            )
            status = "failed"

        question = None
        if state_after is WorkflowState.WAITING_CLARIFICATION and pending_question is not None:
            question = ObservedQuestion(
                question_id=pending_question.question_id,
                target_path=pending_question.target_path,
                allow_delegate=pending_question.allow_delegate,
                allow_custom=pending_question.allow_custom,
                suggested_values=list(pending_question.suggested_values),
            )

        issues: list[ObservedIssue] = []
        conflicts: list[str] = []
        blocking_paths: list[str] = []
        unresolved: list[dict[str, str]] = []
        applied: list[ObservedDelta] = []
        ready: bool | None = None
        if resolution is not None:
            applied = _flatten_deltas(resolution.applied_deltas)
            issues = _flatten_issues(resolution.issues)
            conflicts = _conflict_rule_ids(
                [*resolution.issues, *resolution.conflicts]
            )
            blocking_paths = [
                d.path for d in resolution.unresolved_decisions if d.action.value == "block"
            ]
            unresolved = [
                {"path": d.path, "action": d.action.value}
                for d in resolution.unresolved_decisions
            ]
            ready = resolution.ready_for_confirmation
        if feedback_result is not None:
            issues = _flatten_issues(feedback_result.issues) + issues

        delegated_after = sorted(
            path for path, res in resolutions_after.items() if res == "user_delegated"
        )
        actual_outcome = _map_actual_outcome(
            status=status,
            state_before=state_before,
            state_after=state_after,
            revision_before=revision_before,
            revision_after=revision_after,
            generated=bool(generations),
        )
        expected_outcome = annotation.expected_outcome if annotation is not None else None

        llm_observations = self._llm_observations(
            case, turn, turn_index, repetition, interpreter_llm, feedback_llm, llm_calls_before
        )
        image_observations = self._image_observations(
            case, turn, turn_index, repetition, image_provider, image_calls_before
        )
        artifact_refs = self._system_b_artifact_refs(
            case, turn, repetition, generations, run_dir
        )

        # 生成失败但编译已落库（compile 先于 Provider 调用）：把本轮新编译的
        # PromptArtifact 仍记为该轮 Prompt（L3 的"最终就绪态 Prompt"不因此缺失）。
        failed_prompt_text: str | None = None
        failed_prompt_sha256: str | None = None
        if not generations and error is not None and (
            tables_after.counts.get("prompt_artifacts", 0)
            > tables_before.counts.get("prompt_artifacts", 0)
        ):
            latest_prompt = repo.get_latest_prompt_artifact(session_id)
            if latest_prompt is not None:
                compiled = PromptArtifact.model_validate_json(latest_prompt.payload)
                failed_prompt_text = compiled.prompt
                failed_prompt_sha256 = sha256_text(compiled.prompt)

        observation = SystemBTurnObservation(
            session_id=session_id,
            state_before=state_before.value,
            state_after=state_after.value,
            intent_revision_before=revision_before,
            intent_revision_after=revision_after,
            applied_deltas=applied,
            issues=issues,
            blocking_unresolved_paths=blocking_paths,
            unresolved_decisions=unresolved,
            conflict_rule_ids=conflicts,
            question=question,
            ready_for_confirmation=ready,
            auto_confirmed=bool(confirmations_created),
            confirmation=confirmations_created[-1] if confirmations_created else None,
            generations=generations,
            feedback_decision=(
                feedback_result.decision.value if feedback_result is not None else None
            ),
            feedback_id=(feedback_result.feedback_id if feedback_result is not None else None),
            feedback_recoverable_failure=recoverable_failure,
            recoverable_failure=recoverable_failure,
            failure_codes=failure_codes,
            carry_carried=carry_carried,
            carry_invalidated=carry_invalidated,
            realization_state_id=realization_state_id,
            delegated_paths_after=delegated_after,
            intent_values_before=values_before,
            intent_values_after=values_after,
            resolutions_before=resolutions_before,
            resolutions_after=resolutions_after,
            pinned_before=pinned_before,
            pinned_after=pinned_after,
            stale_revision_probe=stale_probe,
            bad_hash_probe=bad_hash_probe,
            table_counts_before=tables_before.counts,
            table_counts_after=tables_after.counts,
        )
        turn_record = EvalTurnRecord(
            record_id=make_eval_record_id(
                TURN_RECORD_ID_PREFIX, self._run_id, case.case_id, "system_b",
                str(repetition), turn.turn_id,
            ),
            run_id=self._run_id,
            case_id=case.case_id,
            system="system_b",
            repetition=repetition,
            turn_id=turn.turn_id,
            turn_index=turn_index,
            turn_kind=turn.kind,
            input_text=input_text,
            input_source=input_source,
            status=status,
            flow_deviation=flow_deviation,
            flow_deviation_reason="; ".join(deviation_reasons) or None,
            expected_outcome=expected_outcome,
            actual_outcome=actual_outcome,
            outcome_matches=(
                None
                if expected_outcome is None or actual_outcome is None
                else actual_outcome == expected_outcome
            ),
            error=error,
            baseline_turn_record_id=None,
            prompt_text=(
                generations[-1].prompt_text if generations else failed_prompt_text
            ),
            prompt_sha256=(
                generations[-1].prompt_sha256 if generations else failed_prompt_sha256
            ),
            llm_calls=llm_observations,
            image_calls=image_observations,
            system_b=observation,
            artifact_refs=artifact_refs,
            latency_ms=round((self._monotonic() - started) * 1000.0, 3),
            created_at=self._clock(),
        )
        evidence = L1TurnEvidence(
            turn_id=turn.turn_id,
            turn_index=turn_index,
            annotation=annotation,
            observation=observation,
            tables_before=tables_before,
            tables_after=tables_after,
        )
        return turn_record, evidence, confirmations_created, guard_hits

    # -- 确认探针与自动确认 + 生成 -------------------------------------------

    def _probe_stale_revision(
        self,
        workflow: WorkflowService,
        session_id: str,
        snapshot: Any,
        prior_confirmations: list[ObservedConfirmation],
    ) -> str:
        """过期 revision 绑定确认必须被拒（L1 不变量 2 的负向探针）。"""
        stale = next(
            (
                c
                for c in reversed(prior_confirmations)
                if c.intent_revision_id != snapshot.current_intent_revision_id
                or c.execution_revision_id != snapshot.current_execution_revision_id
            ),
            None,
        )
        if stale is None:
            return "not_applicable"
        try:
            workflow.confirm_current_intent(
                session_id,
                stale.intent_revision_id,
                stale.execution_revision_id,
                stale.summary_hash,
            )
        except WorkflowError:
            return "rejected"
        return "accepted"

    def _probe_bad_hash(
        self, workflow: WorkflowService, session_id: str, snapshot: Any
    ) -> str:
        """篡改 summary_hash 的确认必须被拒（L1 不变量 2 的负向探针）。"""
        try:
            workflow.confirm_current_intent(
                session_id,
                snapshot.current_intent_revision_id,
                snapshot.current_execution_revision_id,
                "0" * 64,
            )
        except WorkflowError:
            return "rejected"
        return "accepted"

    def _auto_confirm_and_generate(
        self,
        *,
        workflow: WorkflowService,
        pipeline: GenerationPipeline,
        repo: SQLiteRepository,
        session_id: str,
        run_dir: Path,
        confirmations_created: list[ObservedConfirmation],
        generations: list[ObservedGeneration],
    ) -> None:
        """协议第 3 节第 2 条：重算 summary_hash 自动确认并立即 generate。

        确认一旦落库即追加进 `confirmations_created`（即使随后的 generate 失败），
        保证观察与库内事实一致；`PromptCompilationError`（含
        `prompt.unauthorized_addition` 防护触发）与 `ProviderError` 向上抛给驱动层
        统一记录为轮失败。
        """
        snapshot = repo.get_current_session_snapshot(session_id)
        summary = workflow.get_confirmation_summary(session_id)
        summary_hash = compute_summary_hash(summary)
        invalidated = (
            summary.change_summary.confirmation_invalidated
            if summary.change_summary is not None
            else None
        )
        record = workflow.confirm_current_intent(
            session_id,
            snapshot.current_intent_revision_id,
            snapshot.current_execution_revision_id,
            summary_hash,
        )
        confirmations_created.append(
            ObservedConfirmation(
                confirmation_id=record.confirmation_id,
                intent_revision_id=record.intent_revision_id,
                execution_revision_id=record.execution_revision_id,
                summary_hash=record.summary_hash,
                confirmation_invalidated=invalidated,
            )
        )
        snapshot_at_generate = repo.get_current_session_snapshot(session_id)
        artifact = pipeline.generate(session_id)
        prompt_artifact = PromptArtifact.model_validate_json(
            repo.get_prompt_artifact(artifact.prompt_artifact_id).payload
        )
        generations.append(
            ObservedGeneration(
                generation_id=artifact.generation_id,
                prompt_artifact_id=artifact.prompt_artifact_id,
                based_on_confirmation_id=prompt_artifact.based_on_confirmation_id,
                based_on_intent_revision_id=prompt_artifact.based_on_intent_revision_id,
                current_intent_revision_id=snapshot_at_generate.current_intent_revision_id,
                current_execution_revision_id=(
                    snapshot_at_generate.current_execution_revision_id
                ),
                prompt_text=prompt_artifact.prompt,
                prompt_sha256=sha256_text(prompt_artifact.prompt),
                target_model=artifact.target_model,
                size=artifact.parameters.size,
                seed=artifact.seed,
                image_paths=[
                    _relative_to(PROJECT_ROOT / ref.path, run_dir)
                    for ref in artifact.output_refs
                ],
            )
        )

    # -- 观察记录装配 ----------------------------------------------------------

    def _llm_observations(
        self,
        case: BaselineCase,
        turn: BaselineTurn,
        turn_index: int,
        repetition: int,
        interpreter_llm: RecordingLLMProvider,
        feedback_llm: RecordingLLMProvider,
        before: tuple[int, int],
    ) -> list[LLMCallObservation]:
        observations: list[LLMCallObservation] = []
        slices = (
            ("interpreter", interpreter_llm.calls[before[0]:]),
            ("feedback_engine", feedback_llm.calls[before[1]:]),
        )
        index = 0
        for role, calls in (slices[0], slices[1]):
            for call in calls:
                index += 1
                error = (
                    _failure_from_exception(call.error, stage="llm")
                    if call.error is not None
                    else None
                )
                observations.append(
                    LLMCallObservation(
                        call_id=make_eval_record_id(
                            LLM_OBSERVATION_ID_PREFIX, self._run_id, case.case_id,
                            "system_b", str(repetition), turn.turn_id, str(index),
                        ),
                        role=role,  # type: ignore[arg-type]
                        model_requested=call.request.model,
                        model_returned=(
                            call.response.model if call.response is not None else None
                        ),
                        response_format_sent=(
                            dict(call.request.response_format)
                            if call.request.response_format is not None
                            else None
                        ),
                        provider_request_id=(
                            call.response.provider_request_id
                            if call.response is not None
                            else None
                        ),
                        latency_ms=call.latency_ms,
                        retry_count=0,
                        status="ok" if call.error is None else "failed",
                        error=error,
                    )
                )
        return observations

    def _image_observations(
        self,
        case: BaselineCase,
        turn: BaselineTurn,
        turn_index: int,
        repetition: int,
        image_provider: RecordingImageProvider,
        before: int,
    ) -> list[ImageCallObservation]:
        observations: list[ImageCallObservation] = []
        for index, call in enumerate(image_provider.calls[before:], start=1):
            error = (
                _failure_from_exception(call.error, stage="image")
                if call.error is not None
                else None
            )
            observations.append(
                ImageCallObservation(
                    call_id=make_eval_record_id(
                        IMAGE_OBSERVATION_ID_PREFIX, self._run_id, case.case_id,
                        "system_b", str(repetition), turn.turn_id, str(index),
                    ),
                    prompt_sha256=sha256_text(call.request.prompt),
                    size=call.request.size,
                    model_requested=call.request.model,
                    model_returned=(
                        call.response.model if call.response is not None else None
                    ),
                    seed=(call.response.seed if call.response is not None else None),
                    provider_request_id=(
                        call.response.provider_request_id
                        if call.response is not None
                        else None
                    ),
                    latency_ms=call.latency_ms,
                    retry_count=0,
                    status="ok" if call.error is None else "failed",
                    error=error,
                    image_count=(
                        len(call.response.images) if call.response is not None else 0
                    ),
                )
            )
        return observations

    def _system_b_artifact_refs(
        self,
        case: BaselineCase,
        turn: BaselineTurn,
        repetition: int,
        generations: list[ObservedGeneration],
        run_dir: Path,
    ) -> list[ArtifactRef]:
        refs: list[ArtifactRef] = []
        for generation in generations:
            # artifact_ref_id 的哈希成分只用确定性内容（prompt 文本 sha256 / 图片序号
            # 与字节 sha256），不混入产品侧 uuid4 ID，保证同配置重放得到相同评测层 ID。
            refs.append(
                ArtifactRef(
                    artifact_ref_id=make_eval_record_id(
                        ARTIFACT_REF_ID_PREFIX, self._run_id, case.case_id, "system_b",
                        str(repetition), turn.turn_id, "prompt_artifact",
                        generation.prompt_sha256,
                    ),
                    kind="prompt_artifact",
                    ref_id=generation.prompt_artifact_id,
                    sha256=generation.prompt_sha256,
                )
            )
            refs.append(
                ArtifactRef(
                    artifact_ref_id=make_eval_record_id(
                        ARTIFACT_REF_ID_PREFIX, self._run_id, case.case_id, "system_b",
                        str(repetition), turn.turn_id, "generation_artifact",
                        generation.prompt_sha256,
                    ),
                    kind="generation_artifact",
                    ref_id=generation.generation_id,
                )
            )
            for image_index, image_path in enumerate(generation.image_paths, start=1):
                absolute = run_dir / image_path
                image_sha256 = sha256_file(absolute) if absolute.is_file() else None
                refs.append(
                    ArtifactRef(
                        artifact_ref_id=make_eval_record_id(
                            ARTIFACT_REF_ID_PREFIX, self._run_id, case.case_id, "system_b",
                            str(repetition), turn.turn_id, "image_file", str(image_index),
                            image_sha256 or "",
                        ),
                        kind="image_file",
                        ref_id=generation.generation_id,
                        path=image_path,
                        sha256=image_sha256,
                        designated_for_l4=(repetition == self._l4_repetition),
                    )
                )
        return refs

    @staticmethod
    def _latest_generation_id(repo: SQLiteRepository, session_id: str) -> str | None:
        artifacts = repo.list_generation_artifacts(session_id)
        return artifacts[-1].artifact_id if artifacts else None

    # -----------------------------------------------------------------------
    # L3 指标（A/B 同口径）
    # -----------------------------------------------------------------------

    def _l3_metrics(
        self,
        case: BaselineCase,
        annotation: CaseAnnotation,
        system: str,
        repetition: int,
        turn_records: list[EvalTurnRecord],
        *,
        guard_triggered: int = 0,
        request_faces: list[PromptRequestFace] | None = None,
    ) -> list[MetricRecord]:
        r1 = self._metric_version == METRIC_VERSION_R1
        metric_names = L3_R1_METRICS if r1 else L3_METRICS
        expectations = annotation.prompt_expectations
        if expectations is None:
            # 冻结标注对本案例不提供关键词真值（如 s06-delegate-vague-002）：
            # 全部 L3 文本层指标一律 missing_data（注明原因，不剔分母、不伪造评分）。
            return [
                self._metric_record(
                    case, system, repetition, "L3", metric,
                    MetricPayload(
                        status="missing_data",
                        details={
                            "reason": "the frozen annotation carries no prompt_expectations "
                            "for this case (no ready-state prompt is evaluable)"
                        },
                    ),
                    evidence_refs=[],
                )
                for metric in metric_names
            ]
        prompts = [t.prompt_text for t in turn_records]
        final_prompt = next((p for p in reversed(prompts) if p is not None), None)
        final_turn = next(
            (t for t in reversed(turn_records) if t.prompt_text is not None), None
        )
        coverage = self._guarded_metric_record(
            case, system, repetition, "L3", "intent_coverage",
            lambda: evaluate_intent_coverage(expectations.must_mention_groups, final_prompt),
            [final_turn] if final_turn is not None else [],
        )
        addition = self._guarded_metric_record(
            case, system, repetition, "L3", "unauthorized_addition",
            lambda: (
                evaluate_unauthorized_addition_r1(
                    expectations.must_not_mention, final_prompt, guard_triggered=guard_triggered
                )
                if r1
                else evaluate_unauthorized_addition(
                    expectations.must_not_mention, final_prompt, guard_triggered=guard_triggered
                )
            ),
            [final_turn] if final_turn is not None else [],
        )
        sequence = [
            PromptSequenceTurn(
                turn_id=t.turn_id,
                prompt_text=t.prompt_text,
                # 修改轮次 = 标注 forbidden_change_paths 非空且非 accept 轮
                #（accept 是终局批准、不是修改，不参与 Preservation 配对）。
                is_modification_turn=(
                    t.turn_kind != "accept"
                    and bool(
                        (
                            annotation.turn_annotation(t.turn_id)
                            or TurnAnnotation(turn_id=t.turn_id)
                        ).forbidden_change_paths
                    )
                ),
                # r1：应生成标志始终由冻结标注的 expected_outcome 派生，blocked 也不
                # 改成 None —— 阻断轮只是没有执行，它仍是“预定生成而未产出”的目标，
                # 必须留在完成率分母（根因去重不得缩减分母，F2）。旧 v1 分支保持 None。
                expected_prompt=(
                    None
                    if not r1
                    else (
                        (annotation.turn_annotation(t.turn_id) or TurnAnnotation(turn_id=t.turn_id)).expected_outcome
                        == "ready_and_generate"
                    )
                ),
                preserve_path_groups=(
                    (annotation.turn_annotation(t.turn_id) or TurnAnnotation(turn_id=t.turn_id)).preserve_path_groups
                    if r1
                    else {}
                ),
                # R1-B #2：合法改变路径（SET/CLEAR）跨无 Prompt 轮累计，供 r1
                # Preservation 在下一有效 Prompt 对排除；blocked 轮未执行不记。
                changed_paths=(
                    []
                    if (not r1 or t.status == "blocked")
                    else sorted(
                        {
                            delta.path
                            for delta in (
                                annotation.turn_annotation(t.turn_id)
                                or TurnAnnotation(turn_id=t.turn_id)
                            ).expected_deltas
                            if delta.operation in ("SET", "CLEAR")
                        }
                    )
                ),
            )
            for t in turn_records
        ]
        preservation = self._guarded_metric_record(
            case, system, repetition, "L3", "preservation",
            lambda: (
                evaluate_preservation_r1(sequence)
                if r1
                else evaluate_preservation(expectations.must_mention_groups, sequence)
            ),
            turn_records,
        )
        if request_faces is None:
            # System B：每次图像调用的请求面（size/model；参数面仅 size 由构造保证）。
            request_faces = [
                PromptRequestFace(
                    turn_id=t.turn_id,
                    size=call.size,
                    model=call.model_requested or "",
                    extra_parameters_free=True,
                )
                for t in turn_records
                for call in t.image_calls
            ]
        compatibility = self._guarded_metric_record(
            case, system, repetition, "L3", "model_compatibility",
            lambda: evaluate_model_compatibility(
                request_faces,
                expected_size=self._provider_snapshot().image_size,
                expected_model=self._settings.image_model,
            ),
            turn_records,
        )
        records = [coverage, addition, preservation, compatibility]
        if r1:
            records.append(
                self._guarded_metric_record(
                    case, system, repetition, "L3", "generation_completion",
                    lambda: evaluate_generation_completion(sequence),
                    turn_records,
                )
            )
        return records

    # -----------------------------------------------------------------------
    # MetricRecord 装配
    # -----------------------------------------------------------------------

    def _metric_record(
        self,
        case: BaselineCase,
        system: str,
        repetition: int,
        layer: str,
        metric: str,
        payload: MetricPayload,
        evidence_refs: list[Any],
    ) -> MetricRecord:
        refs = [getattr(r, "record_id", str(r)) for r in evidence_refs]
        return MetricRecord(
            record_id=make_eval_record_id(
                METRIC_RECORD_ID_PREFIX, self._run_id, case.case_id, system,
                str(repetition), layer, metric,
            ),
            run_id=self._run_id,
            case_id=case.case_id,
            system=system,  # type: ignore[arg-type]
            repetition=repetition,
            turn_type=case.turn_type,
            layer=layer,  # type: ignore[arg-type]
            metric=metric,
            metric_version=self._metric_version,
            status=payload.status,
            score=payload.score,
            passed=payload.passed,
            counts=payload.counts,
            details=payload.details,
            evidence_refs=refs,
            failure=None,
            created_at=self._clock(),
        )

    def _guarded_metric_record(
        self,
        case: BaselineCase,
        system: str,
        repetition: int,
        layer: str,
        metric: str,
        payload_thunk: Callable[[], MetricPayload],
        evidence_refs: list[Any],
    ) -> MetricRecord:
        """指标计算防御性包装：异常 → `failed` 三态（保留在分母，不静默吞）。"""
        try:
            payload = payload_thunk()
            return self._metric_record(
                case, system, repetition, layer, metric, payload, evidence_refs
            )
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            refs = [getattr(r, "record_id", str(r)) for r in evidence_refs]
            return MetricRecord(
                record_id=make_eval_record_id(
                    METRIC_RECORD_ID_PREFIX, self._run_id, case.case_id, system,
                    str(repetition), layer, metric,
                ),
                run_id=self._run_id,
                case_id=case.case_id,
                system=system,  # type: ignore[arg-type]
                repetition=repetition,
                turn_type=case.turn_type,
                layer=layer,  # type: ignore[arg-type]
                metric=metric,
                metric_version=self._metric_version,
                status="failed",
                failure=EvalFailureRecord(
                    code=EVAL_METRIC_ERROR, message=str(exc), stage="runner"
                ),
                evidence_refs=refs,
                created_at=self._clock(),
            )


class _EvaluationDriveError(Exception):
    """评测层驱动失败（带 `.code`，命名空间 `evaluation.*`）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


def _map_actual_outcome(
    *,
    status: str,
    state_before: WorkflowState,
    state_after: WorkflowState,
    revision_before: str | None,
    revision_after: str | None,
    generated: bool,
) -> str | None:
    """把驱动观察映射到 `expected_outcome` 的四值词汇（无法干净映射 → None）。"""
    if status == "failed":
        return None
    if generated:
        return "ready_and_generate"
    if state_after is WorkflowState.COMPLETED:
        return "accepted_completed"
    if state_after is WorkflowState.WAITING_CLARIFICATION:
        return "clarification_expected"
    if state_after is state_before and revision_before == revision_after:
        return "no_state_change"
    return None


def _relative_to(path: Path, root: Path) -> str:
    """相对 run 根目录的 POSIX 路径（run 根外退化回绝对路径，保证可定位）。"""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# CLI（真实 LLM 层评测的显式入口；默认测试绝不触发）
# ---------------------------------------------------------------------------


def _real_provider_factories(
    settings: Settings,
) -> tuple[Callable[[], LLMProvider], Callable[[], ImageProvider]]:
    """由同一 `Settings` 构造真实 adapter 的工厂（延迟 import，模块导入不触网）。"""
    from visual_intent_agent.providers.openai_image import OpenAIImageProvider
    from visual_intent_agent.providers.openai_llm import OpenAICompatibleLLMProvider

    def llm_factory() -> LLMProvider:
        return OpenAICompatibleLLMProvider(settings)

    def image_factory() -> ImageProvider:
        return OpenAIImageProvider(settings)

    return llm_factory, image_factory


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evaluation.runner",
        description=(
            "Gate A A/B 评测 Runner（MVP v0.3 Step 03 / R1-B）：对冻结数据集以相同 "
            "Provider 条件运行 Baseline A 与 System B，产出 L1~L3 指标与逐案例记录。"
        ),
        epilog=(
            "凭据只从环境变量 / .env 读取（VIA_PROVIDER_BASE_URL / VIA_PROVIDER_API_KEY 等），"
            "绝不接受命令行明文 key。默认产物落 outputs/evaluation_runs/<run_id>/。"
            "--manifest 解析并校验 r1 清单；正式运行要求干净工作区与可解析 commit。"
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_REAL_RUNS_ROOT,
        help=(
            "运行产物根目录（默认 outputs/evaluation_runs；避免与 evaluation/ 的 "
            "UTF-8 凭据扫描冲突）"
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "冻结清单路径：由清单解析协议/配置/数据集/标注并校验全部哈希"
            "（r1 用 evaluation/frozen_manifest_v0_3_r1.json；缺省沿用旧默认输入）"
        ),
    )
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help=(
            "诊断模式：允许 dirty 工作区与不可解析 commit，结果标记 "
            "identity_mode=diagnostic，不作为正式 Gate 证据"
        ),
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=None,
        help="只运行指定案例（可重复）；默认全量 22 案例",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=None,
        help="L1~L3 每案例重复次数（默认读冻结配置 = 2）",
    )
    parser.add_argument(
        "--nonce",
        default="",
        help="同配置多次运行的区分串（进入 run_id；默认空串）",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="只列出数据集案例 ID 并退出（不需要凭据）",
    )
    return parser


def _cli_dataset_path(args: argparse.Namespace) -> Path:
    if args.manifest is not None:
        return load_manifest(args.manifest, base_dir=PROJECT_ROOT).dataset_path
    return DEFAULT_DATASET_PATH


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口：真实 Provider 由 `load_settings()` 构造（凭据不进入任何输出）。"""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_cases:
        for case in load_dataset(_cli_dataset_path(args)):
            print(f"{case.case_id}\t{case.scenario}\t{case.turn_type}")
        return 0

    from visual_intent_agent import __version__ as package_version

    from visual_intent_agent.config import ConfigurationError, load_settings

    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(f"配置错误（凭据缺失或非法）：{exc}", file=sys.stderr)
        return 2

    mode = IDENTITY_DIAGNOSTIC if args.diagnostic else IDENTITY_FORMAL
    identity = resolve_code_identity(
        PROJECT_ROOT,
        package_version=f"visual_intent_agent-{package_version}",
        component_version=EVALUATION_HARNESS_VERSION,
        mode=mode,
    )
    llm_factory, image_factory = _real_provider_factories(settings)
    manifest_path = args.manifest if args.manifest is not None else DEFAULT_MANIFEST_PATH
    try:
        runner = EvaluationRunner(
            settings=settings,
            llm_factory=llm_factory,
            image_factory=image_factory,
            output_root=args.output_root,
            repetitions=args.repetitions,
            run_nonce=args.nonce,
            manifest_path=manifest_path,
            resolve_from_manifest=args.manifest is not None,
            identity=identity,
        )
        result = runner.run(case_ids=args.case_id)
    except (ValueError, IdentityError) as exc:
        if isinstance(exc, IdentityError) or mode == IDENTITY_FORMAL:
            print(
                f"正式运行前置条件不满足：{exc}\n"
                "如需诊断运行请显式加 --diagnostic（结果不作为正式 Gate 证据）。",
                file=sys.stderr,
            )
            return 3
        raise
    summary: RunSummary = result.summary
    print(f"run_id: {result.run.run_id}")
    print(f"产物目录: {runner.run_dir}")
    print(
        f"身份: mode={result.run.identity_mode} commit={result.run.code_commit} "
        f"dirty={result.run.code_dirty} metric_version={result.run.metric_version}"
    )
    print(f"案例数: {result.run.case_count}（每案例 {result.run.repetitions} 次重复 × A/B）")
    print(f"案例状态计数: {json.dumps(summary.case_status_counts, ensure_ascii=False)}")
    print(f"Gate A 阻断（L1 失败）: {summary.gate_a_blocked}")
    if summary.l1_blocking:
        for entry in summary.l1_blocking:
            print(
                f"  - {entry.case_id} rep{entry.repetition}: "
                f"{', '.join(entry.failed_invariants)}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DATASET_PATH",
    "DEFAULT_ANNOTATIONS_PATH",
    "DEFAULT_PROTOCOL_PATH",
    "DEFAULT_MANIFEST_PATH",
    "DEFAULT_R1_MANIFEST_PATH",
    "DEFAULT_RUNS_ROOT",
    "DEFAULT_REAL_RUNS_ROOT",
    "PROTOCOL_VERSION_R1",
    "EVAL_NO_GENERATION_UNDER_REVIEW",
    "EVAL_METRIC_ERROR",
    "EvaluationRunner",
    "RecordingLLMProvider",
    "RecordingImageProvider",
    "build_eval_run_id",
    "build_arg_parser",
    "main",
]
