"""MVP v0.3 Step 02：Direct LLM Baseline（Baseline A）执行器。

链路（协议第 1 节，唯一范围依据）：

    单轮：用户文本 → LLM（冻结系统提示，自由文本）→ 最终 Prompt → 图像 Provider
    多轮：完整历史对话 + 前轮 Prompt 摘要 → LLM → 新 Prompt → 图像 Provider

冻结边界：

- **复用** `visual_intent_agent.providers` 的同一 `LLMProvider` / `ImageProvider`
  Protocol 与同一错误分类；真实 adapter 与 Fake 走完全相同的公开接口（构造注入）；
- 不提问、不维护字段状态、不输出 IntentDelta、不使用 System B 的确认信息；
- LLM 调用**不**设置 `model` / `temperature` / `max_tokens` / `response_format`
  （与 System B 三处调用点一致；`model=None` 由 adapter 解析为 `Settings.llm_model`）；
- Provider 失败只按 `ProviderError` 分类记录，**不**换模型、不换样本、不在基线层重试；
- `accept` 轮不执行（`skipped`）；
- 不 import `domain` / `validation` / `policy` / `workflow` / `prompt_engine` /
  `realization`（依赖边界由测试静态扫描强制）。

多轮输入格式 `baseline_multiturn_v1`（本步冻结，版本写入 Run 与每轮记录）：

1. 历史 = 本案例**此前已执行**的非 `accept` 轮，按数据集顺序；`accept` 轮不进入历史。
2. 每轮用户文本逐字作为一条 `role="user"` 消息（不加任何前缀/后缀）。
3. 该轮若成功产生最终 Prompt，则紧随一条 `role="assistant"` 摘要消息；该轮
   LLM 失败或被判定失败时只保留用户消息（没有可摘要的 Prompt）。
4. 摘要构造完全规则化、无 LLM 参与：空白折叠为单个空格 → 超过
   `PROMPT_SUMMARY_MAX_CHARS`（240）字符则按字符边界截断并追加 `…`。
5. 摘要消息模板：`[第 N 轮 Prompt 摘要] <摘要>`，截断时在 `摘要` 后带 `（已截断）`
   标记（见 `format_history_summary`）。
6. 当前轮用户文本作为最后一条 `role="user"` 消息；系统提示恒为第一条。

ID 方案 `baseline_ids_sha256_v1`：全部 ID = `sha256(parts 以 "|" 连接)[:16]`，
前缀区分类型（`brun` / `bcase` / `bturn` / `bllm` / `bprompt` / `bimg` / `bart`），
不含随机数与时钟，保证同输入重放得到相同 ID。

落盘布局（`output_dir` 由调用方注入；默认测试传 `tmp_path`）：

    <output_dir>/
    ├── run.json                               # BaselineRunRecord（Run 级元数据）
    └── cases/<case_id>/
        ├── case.json                          # BaselineCaseRecord（含逐轮记录）
        └── images/
            └── turn_<NN>_image_<i>.<ext>      # 图片字节；记录里存相对路径 + sha256
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ValidationError

from visual_intent_agent import __version__ as _VIA_PACKAGE_VERSION
from visual_intent_agent.config import PROJECT_ROOT, Settings
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.image import (
    GeneratedImage,
    ImageGenerationRequest,
    ImageProvider,
)
from visual_intent_agent.providers.llm import LLMMessage, LLMProvider, LLMRequest

from evaluation.models import (
    BaselineCase,
    BaselineCaseRecord,
    BaselineErrorRecord,
    BaselineErrorStage,
    BaselineImageArtifactRecord,
    BaselineImageCallRecord,
    BaselineLLMCallRecord,
    BaselinePromptRecord,
    BaselineProviderSnapshot,
    BaselineRunConfig,
    BaselineRunRecord,
    BaselineRunResult,
    BaselineTurn,
    BaselineTurnRecord,
)

# ---------------------------------------------------------------------------
# 冻结常量（版本号 / 路径 / 模板）
# ---------------------------------------------------------------------------

#: 基线实现版本（实现变更时递增）。
BASELINE_VERSION = "direct_llm_baseline_v1"

#: 冻结系统提示版本（协议第 1 节：只允许"你是 Prompt 生成器"级别的适配指令）。
BASELINE_PROMPT_VERSION = "baseline_prompt_v1"

#: 多轮输入格式版本（本步冻结；写入 Run 级与逐轮记录）。
BASELINE_MULTITURN_FORMAT_VERSION = "baseline_multiturn_v1"

#: 稳定关联 ID 方案版本。
BASELINE_ID_SCHEME_VERSION = "baseline_ids_sha256_v1"

#: Gate A 协议版本（与 evaluation/protocol.md 首行一致；文件 sha256 同时入 Run 记录）。
GATE_A_PROTOCOL_VERSION = "gate_a_protocol_v0_3"

#: 前轮 Prompt 摘要的最大字符数（规则化截断，无 LLM 参与）。
PROMPT_SUMMARY_MAX_CHARS = 240

#: 摘要消息模板（`truncated` 非空时插入截断标记）。
PROMPT_SUMMARY_TEMPLATE = "[第 {turn_index} 轮 Prompt 摘要{truncated}] {summary}"

#: `accept` 轮的 skip 原因（协议第 3 节：Baseline 不执行 accept）。
SKIP_REASON_ACCEPT = "accept_turn_not_executed"

#: 基线自身判定失败的错误 code（LLM 返回空白文本，无法构造合法图像请求）。
#: 命名空间 `baseline.*` 属于评测层，不进入产品 Issue code 表。
BASELINE_EMPTY_LLM_OUTPUT = "baseline.empty_llm_output"

#: 冻结系统提示模板：唯一变量是目标图像模型名（避免复制模型名常量）。
BASELINE_SYSTEM_PROMPT_TEMPLATE = """\
你是图像 Prompt 生成器。请根据下面的用户对话，输出一段可直接提交给 {image_model} 文生图模型的 Prompt。

要求：
1. 只输出 Prompt 文本本身（自由文本，不要 JSON、不要 Markdown 代码块、不要解释或前后缀）。
2. 用目标图像模型容易理解的描述性语言，覆盖用户已经表达的全部视觉需求。
3. 不要添加用户没有表达的关键视觉元素，不要向用户提问。"""

#: ID 前缀（冻结，便于人工阅读与关联查询）。
RUN_ID_PREFIX = "brun"
CASE_ID_PREFIX = "bcase"
TURN_ID_PREFIX = "bturn"
LLM_CALL_ID_PREFIX = "bllm"
PROMPT_ID_PREFIX = "bprompt"
IMAGE_CALL_ID_PREFIX = "bimg"
ARTIFACT_ID_PREFIX = "bart"

#: 冻结输入路径默认值（可注入覆盖；测试用 tmp_path）。
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "evaluation" / "configs" / "gate_a_v0_3.json"
DEFAULT_DATASET_PATH = PROJECT_ROOT / "evaluation" / "fixtures" / "core_v0_3.jsonl"
DEFAULT_PROTOCOL_PATH = PROJECT_ROOT / "evaluation" / "protocol.md"

#: MIME → 文件扩展名（未知格式用 `.bin`，不猜测格式）。
_MIME_EXTENSIONS: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


# ---------------------------------------------------------------------------
# 纯函数：哈希、ID、摘要、消息拼接
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    """字节内容的 sha256（小写 hex）。"""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """文本的 sha256；统一 UTF-8 编码（与落盘 JSON 无关，只哈希语义内容）。"""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    """文件内容的 sha256（冻结输入以内容哈希标识，协议第 9 节）。"""
    return sha256_bytes(Path(path).read_bytes())


def digest(*parts: str) -> str:
    """确定性摘要：`sha256("|".join(parts))` 的小写 hex。

    parts 中不允许出现 `|`（各字段本身不含 `|`；如未来需要，应改用长度前缀编码）。
    """
    return sha256_text("|".join(parts))


def make_record_id(prefix: str, *parts: str) -> str:
    """稳定关联 ID：`<prefix>_<digest[:16]>`（方案 `baseline_ids_sha256_v1`）。"""
    return f"{prefix}_{digest(*parts)[:16]}"


def build_run_id(
    *,
    code_version: str,
    dataset_version: str,
    dataset_sha256: str,
    config_version: str,
    config_sha256: str,
    protocol_version: str = GATE_A_PROTOCOL_VERSION,
    run_nonce: str = "",
) -> str:
    """Run ID：由冻结输入与代码版本确定性派生（同输入 → 同 run_id）。

    `run_nonce` 供 Step 03 区分重复实验（协议第 7 节 L1~L3 每案例 2 次）；默认空串
    表示"不带重复标记的确定性运行"，测试重放用默认值。
    """
    return make_record_id(
        RUN_ID_PREFIX,
        code_version,
        dataset_version,
        dataset_sha256,
        config_version,
        config_sha256,
        protocol_version,
        BASELINE_PROMPT_VERSION,
        BASELINE_MULTITURN_FORMAT_VERSION,
        BASELINE_ID_SCHEME_VERSION,
        run_nonce,
    )


def default_code_version() -> str:
    """默认代码版本标识（真实运行时 Step 03 可注入更精确的标识，如 git sha）。"""
    return f"visual_intent_agent-{_VIA_PACKAGE_VERSION}+{BASELINE_VERSION}"


def render_system_prompt(image_model: str) -> str:
    """渲染冻结系统提示（版本 `BASELINE_PROMPT_VERSION`）。"""
    return BASELINE_SYSTEM_PROMPT_TEMPLATE.format(image_model=image_model)


def collapse_whitespace(text: str) -> str:
    """把任意空白串折叠为单个空格并去掉首尾空白（确定性，无 LLM）。"""
    return " ".join(text.split())


def summarize_prompt(
    prompt: str, *, max_chars: int = PROMPT_SUMMARY_MAX_CHARS
) -> tuple[str, bool]:
    """规则化 Prompt 摘要：折叠空白 + 字符边界截断。

    返回 `(summary, truncated)`；截断时不切断字符（Python 字符串按码位切片），
    超出部分直接丢弃并追加 `…`。
    """
    normalized = collapse_whitespace(prompt)
    if len(normalized) <= max_chars:
        return normalized, False
    return normalized[:max_chars] + "…", True


def format_history_summary(turn_index: int, prompt: str) -> str:
    """按冻结模板构造一条 assistant 摘要消息。"""
    summary, truncated = summarize_prompt(prompt)
    marker = "（已截断）" if truncated else ""
    return PROMPT_SUMMARY_TEMPLATE.format(
        turn_index=turn_index, truncated=marker, summary=summary
    )


class BaselineHistoryEntry(NamedTuple):
    """历史中的一轮：序号、逐字用户文本、该轮最终 Prompt（失败则为 None）。"""

    turn_index: int
    user_text: str
    prompt_text: str | None


def build_request_messages(
    system_prompt: str,
    history: Sequence[BaselineHistoryEntry],
    current_user_text: str,
) -> list[LLMMessage]:
    """按 `baseline_multiturn_v1` 拼接一次 LLM 请求的消息列表（确定性）。"""
    messages: list[LLMMessage] = [LLMMessage(role="system", content=system_prompt)]
    for entry in history:
        messages.append(LLMMessage(role="user", content=entry.user_text))
        if entry.prompt_text is not None:
            messages.append(
                LLMMessage(
                    role="assistant",
                    content=format_history_summary(entry.turn_index, entry.prompt_text),
                )
            )
    messages.append(LLMMessage(role="user", content=current_user_text))
    return messages


# ---------------------------------------------------------------------------
# 冻结输入加载
# ---------------------------------------------------------------------------


def load_run_config(path: Path = DEFAULT_CONFIG_PATH) -> BaselineRunConfig:
    """从冻结配置文件读取基线需要的非密旋钮（尺寸 / 张数 / 版本）。"""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        image = payload["image"]
        return BaselineRunConfig(
            config_version=payload["config_version"],
            image_size=image["size"],
            images_per_generation=image["images_per_generation"],
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load baseline config from {source}: {exc}") from exc


def load_dataset(path: Path = DEFAULT_DATASET_PATH) -> list[BaselineCase]:
    """读入 frozen JSONL 数据集；同一文件内 case_id 必须唯一。"""
    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read dataset {source}: {exc}") from exc

    cases: list[BaselineCase] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}:{lineno}: invalid JSON: {exc}") from exc
        try:
            case = BaselineCase.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"{source}:{lineno}: invalid case: {exc}") from exc
        if case.case_id in seen:
            raise ValueError(f"{source}:{lineno}: duplicate case_id {case.case_id!r}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"dataset {source} contains no cases")
    return cases


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------


class DirectBaselineRunner:
    """Baseline A 执行器（依赖注入：LLM / Image Provider、Settings、输出目录）。

    一个实例对应**一次冻结运行**：`run_id` 在构造时由注入的输入哈希确定性派生，
    因此同输入重放得到相同的 run/case/turn/call/artifact ID。

    公开接口（供 Step 03 Runner 集成）：

        runner = DirectBaselineRunner(llm=..., image=..., settings=...,
                                      output_dir=<runs/<run_id>/baseline>)
        result = runner.run_dataset()          # -> BaselineRunResult
        record = runner.run_case(case)         # -> BaselineCaseRecord
    """

    def __init__(
        self,
        *,
        llm: LLMProvider,
        image: ImageProvider,
        settings: Settings,
        output_dir: Path,
        config_path: Path = DEFAULT_CONFIG_PATH,
        dataset_path: Path = DEFAULT_DATASET_PATH,
        protocol_path: Path = DEFAULT_PROTOCOL_PATH,
        code_version: str | None = None,
        run_nonce: str = "",
        protocol_version: str = GATE_A_PROTOCOL_VERSION,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        """构造执行器。

        - `settings`：Provider 模型名/超时/重试的唯一来源（**不读环境变量**）；
        - `output_dir`：运行产物根目录；测试传 `tmp_path`（不得写 data/ 或 outputs/）；
        - `code_version`：代码版本标识；默认由包版本 + 基线版本派生，可注入覆盖；
        - `run_nonce`：多次重复实验的区分串（协议第 7 节），默认空串；
        - `clock` / `monotonic`：时间源注入，默认真实时钟；测试注入固定值可实现
          逐字节确定性重放。
        """
        self._llm = llm
        self._image = image
        self._settings = settings
        self._output_dir = Path(output_dir)
        self._config_path = Path(config_path)
        self._dataset_path = Path(dataset_path)
        self._protocol_path = Path(protocol_path)

        self._config = load_run_config(self._config_path)
        self._dataset_sha256 = sha256_file(self._dataset_path)
        self._config_sha256 = sha256_file(self._config_path)
        self._protocol_sha256 = sha256_file(self._protocol_path)
        self._code_version = code_version or default_code_version()
        self._run_nonce = run_nonce
        self._protocol_version = protocol_version
        self._run_id = build_run_id(
            code_version=self._code_version,
            dataset_version=self._dataset_version(),
            dataset_sha256=self._dataset_sha256,
            config_version=self._config.config_version,
            config_sha256=self._config_sha256,
            protocol_version=self._protocol_version,
            run_nonce=run_nonce,
        )
        self._system_prompt = render_system_prompt(settings.image_model)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.perf_counter

    # -- 只读属性（Step 03 集成用） -----------------------------------------

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    @property
    def run_config(self) -> BaselineRunConfig:
        return self._config

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def code_version(self) -> str:
        return self._code_version

    @property
    def dataset_sha256(self) -> str:
        return self._dataset_sha256

    # -- 运行入口 -----------------------------------------------------------

    def run_dataset(self) -> BaselineRunResult:
        """读取冻结数据集并逐案执行（`accept` 轮跳过）。"""
        return self.run_cases(load_dataset(self._dataset_path))

    def run_cases(self, cases: Iterable[BaselineCase]) -> BaselineRunResult:
        """逐案执行给定案例并写出 `run.json`。"""
        case_list = list(cases)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        records = [self.run_case(case) for case in case_list]
        run_record = self._build_run_record(records)
        _write_json(self._output_dir / "run.json", run_record)
        return BaselineRunResult(run=run_record, cases=records)

    def run_case(self, case: BaselineCase) -> BaselineCaseRecord:
        """执行单个案例：逐轮调用 LLM + 图像 Provider，并逐轮落盘。"""
        case_record_id = make_record_id(CASE_ID_PREFIX, self._run_id, case.case_id)
        history: list[BaselineHistoryEntry] = []
        turns: list[BaselineTurnRecord] = []

        for index, turn in enumerate(case.turns, start=1):
            record = self._run_turn(case, turn, index, history)
            turns.append(record)
            if not record.skipped:
                prompt_text = record.prompt.text if record.prompt is not None else None
                history.append(BaselineHistoryEntry(index, turn.user_text, prompt_text))
            _write_json(self._turn_path(case.case_id, index), record)

        status: Literal["completed", "failed"] = (
            "failed" if any(t.status == "failed" for t in turns) else "completed"
        )
        case_record = BaselineCaseRecord(
            record_id=case_record_id,
            run_id=self._run_id,
            case_id=case.case_id,
            dataset_version=case.dataset_version,
            scenario=case.scenario,
            turn_type=case.turn_type,
            turns=turns,
            status=status,
            created_at=self._now(),
        )
        _write_json(self._case_path(case.case_id), case_record)
        return case_record

    # -- 单轮链路 -----------------------------------------------------------

    def _run_turn(
        self,
        case: BaselineCase,
        turn: BaselineTurn,
        index: int,
        history: Sequence[BaselineHistoryEntry],
    ) -> BaselineTurnRecord:
        created_at = self._now()
        common = {
            "record_id": make_record_id(TURN_ID_PREFIX, self._run_id, case.case_id, turn.turn_id),
            "run_id": self._run_id,
            "case_id": case.case_id,
            "turn_id": turn.turn_id,
            "turn_index": index,
            "turn_kind": turn.kind,
            "input_text": turn.user_text,
            "multiturn_format_version": BASELINE_MULTITURN_FORMAT_VERSION,
            "history_user_turns": len(history),
            "created_at": created_at,
        }

        if turn.kind == "accept":
            return BaselineTurnRecord(
                **common,
                skipped=True,
                skip_reason=SKIP_REASON_ACCEPT,
                status="skipped",
            )

        messages = build_request_messages(self._system_prompt, history, turn.user_text)
        llm_call = self._call_llm(case, turn, index, messages)
        if llm_call.status == "failed":
            assert llm_call.error is not None
            return BaselineTurnRecord(
                **common,
                request_messages=messages,
                llm_call=llm_call,
                status="failed",
                error=llm_call.error,
            )

        raw_output = llm_call.raw_output or ""
        final_prompt = raw_output.strip()
        if not final_prompt:
            return BaselineTurnRecord(
                **common,
                request_messages=messages,
                llm_call=llm_call,
                status="failed",
                error=BaselineErrorRecord(
                    code=BASELINE_EMPTY_LLM_OUTPUT,
                    message=(
                        "the LLM returned blank text; no valid image prompt could be built "
                        "(no retry, no model switch)"
                    ),
                    retryable=False,
                    stage="baseline",
                ),
            )

        prompt_record = BaselinePromptRecord(
            prompt_id=make_record_id(PROMPT_ID_PREFIX, self._run_id, case.case_id, turn.turn_id),
            run_id=self._run_id,
            case_id=case.case_id,
            turn_id=turn.turn_id,
            turn_index=index,
            text=final_prompt,
            text_sha256=sha256_text(final_prompt),
            system_prompt_version=BASELINE_PROMPT_VERSION,
            multiturn_format_version=BASELINE_MULTITURN_FORMAT_VERSION,
        )
        image_call = self._call_image(case, turn, index, prompt_record)
        if image_call.status == "failed":
            assert image_call.error is not None
            return BaselineTurnRecord(
                **common,
                request_messages=messages,
                llm_call=llm_call,
                prompt=prompt_record,
                image_call=image_call,
                status="failed",
                error=image_call.error,
            )

        return BaselineTurnRecord(
            **common,
            request_messages=messages,
            llm_call=llm_call,
            prompt=prompt_record,
            image_call=image_call,
            status="completed",
        )

    def _call_llm(
        self,
        case: BaselineCase,
        turn: BaselineTurn,
        index: int,
        messages: Sequence[LLMMessage],
    ) -> BaselineLLMCallRecord:
        """一次 LLM 调用；只发送 messages（model/temperature/max_tokens/response_format 全为 None）。"""
        call_id = make_record_id(LLM_CALL_ID_PREFIX, self._run_id, case.case_id, turn.turn_id)
        base = {
            "call_id": call_id,
            "run_id": self._run_id,
            "case_id": case.case_id,
            "turn_id": turn.turn_id,
            "turn_index": index,
            "call_index": 1,
            "messages": list(messages),
            "model_requested": None,
            "retry_count": 0,
        }
        request = LLMRequest(messages=list(messages))
        started = self._monotonic()
        try:
            response = self._llm.complete(request)
        except ProviderError as exc:
            latency_ms = self._elapsed_ms(started)
            return BaselineLLMCallRecord(
                **base,
                latency_ms=latency_ms,
                status="failed",
                error=_provider_error_record(exc, stage="llm"),
            )
        latency_ms = self._elapsed_ms(started)
        return BaselineLLMCallRecord(
            **base,
            model_returned=response.model,
            raw_output=response.content,
            provider_request_id=response.provider_request_id,
            usage=response.usage,
            latency_ms=latency_ms,
            status="ok",
        )

    def _call_image(
        self,
        case: BaselineCase,
        turn: BaselineTurn,
        index: int,
        prompt_record: BaselinePromptRecord,
    ) -> BaselineImageCallRecord:
        """一次图像调用：把最终 Prompt 与冻结尺寸/模型交给同一 ImageProvider。"""
        call_id = make_record_id(IMAGE_CALL_ID_PREFIX, self._run_id, case.case_id, turn.turn_id)
        model_requested = self._settings.image_model
        base = {
            "call_id": call_id,
            "run_id": self._run_id,
            "case_id": case.case_id,
            "turn_id": turn.turn_id,
            "turn_index": index,
            "prompt_id": prompt_record.prompt_id,
            "prompt_text": prompt_record.text,
            "size": self._config.image_size,
            "multiturn_format_version": BASELINE_MULTITURN_FORMAT_VERSION,
            "model_requested": model_requested,
            "retry_count": 0,
        }
        request = ImageGenerationRequest(
            prompt=prompt_record.text,
            size=self._config.image_size,
            model=model_requested,
        )
        started = self._monotonic()
        try:
            result = self._image.generate(request)
        except ProviderError as exc:
            latency_ms = self._elapsed_ms(started)
            return BaselineImageCallRecord(
                **base,
                latency_ms=latency_ms,
                status="failed",
                error=_provider_error_record(exc, stage="image"),
            )
        latency_ms = self._elapsed_ms(started)

        artifacts = [
            self._write_image(case, turn, index, call_id, image_index, image)
            for image_index, image in enumerate(result.images, start=1)
        ]
        return BaselineImageCallRecord(
            **base,
            model_returned=result.model,
            provider_request_id=result.provider_request_id,
            seed=result.seed,
            images=artifacts,
            latency_ms=latency_ms,
            status="ok",
        )

    # -- 落盘 ---------------------------------------------------------------

    def _write_image(
        self,
        case: BaselineCase,
        turn: BaselineTurn,
        index: int,
        image_call_id: str,
        image_index: int,
        image: GeneratedImage,
    ) -> BaselineImageArtifactRecord:
        content = bytes(image.content)
        mime_type = image.mime_type
        extension = _MIME_EXTENSIONS.get(mime_type, ".bin")
        relative_path = f"cases/{case.case_id}/images/turn_{index:02d}_image_{image_index}{extension}"
        target = self._output_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return BaselineImageArtifactRecord(
            artifact_id=make_record_id(
                ARTIFACT_ID_PREFIX,
                self._run_id,
                case.case_id,
                turn.turn_id,
                str(image_index),
            ),
            run_id=self._run_id,
            case_id=case.case_id,
            turn_id=turn.turn_id,
            image_call_id=image_call_id,
            image_index=image_index,
            relative_path=relative_path,
            mime_type=mime_type,
            byte_size=len(content),
            sha256=sha256_bytes(content),
            multiturn_format_version=BASELINE_MULTITURN_FORMAT_VERSION,
        )

    def _turn_path(self, case_id: str, index: int) -> Path:
        return self._case_dir(case_id) / "turns" / f"turn_{index:02d}.json"

    def _case_path(self, case_id: str) -> Path:
        return self._case_dir(case_id) / "case.json"

    def _case_dir(self, case_id: str) -> Path:
        return self._output_dir / "cases" / case_id

    # -- Run 记录 -----------------------------------------------------------

    def _build_run_record(self, cases: Sequence[BaselineCaseRecord]) -> BaselineRunRecord:
        return BaselineRunRecord(
            run_id=self._run_id,
            baseline_version=BASELINE_VERSION,
            baseline_prompt_version=BASELINE_PROMPT_VERSION,
            multiturn_format_version=BASELINE_MULTITURN_FORMAT_VERSION,
            id_scheme_version=BASELINE_ID_SCHEME_VERSION,
            code_version=self._code_version,
            run_nonce=self._run_nonce,
            dataset_version=self._dataset_version(),
            dataset_path=_display_path(self._dataset_path),
            dataset_sha256=self._dataset_sha256,
            config_version=self._config.config_version,
            config_path=_display_path(self._config_path),
            config_sha256=self._config_sha256,
            protocol_version=self._protocol_version,
            protocol_path=_display_path(self._protocol_path),
            protocol_sha256=self._protocol_sha256,
            provider=self._provider_snapshot(),
            system_prompt=self._system_prompt,
            system_prompt_template=BASELINE_SYSTEM_PROMPT_TEMPLATE,
            case_count=len(cases),
            case_ids=[c.case_id for c in cases],
            created_at=self._now(),
        )

    def _provider_snapshot(self) -> BaselineProviderSnapshot:
        """只记非密信息：模型名/超时/重试/尺寸；绝不含 base_url 或 key。"""
        return BaselineProviderSnapshot(
            llm_model=self._settings.llm_model,
            image_model=self._settings.image_model,
            llm_timeout_seconds=self._settings.llm_timeout_seconds,
            image_timeout_seconds=self._settings.image_timeout_seconds,
            http_max_retries=self._settings.http_max_retries,
            image_size=self._config.image_size,
            images_per_generation=self._config.images_per_generation,
        )

    def _dataset_version(self) -> str:
        """数据集版本：取冻结数据集首行的 `dataset_version`（不额外读全文件）。"""
        for raw in self._dataset_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line:
                payload = json.loads(line)
                version = payload.get("dataset_version")
                if isinstance(version, str) and version:
                    return version
                break
        raise ValueError(f"dataset {self._dataset_path} has no dataset_version")

    # -- 时钟 ---------------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock()

    def _elapsed_ms(self, started: float) -> float:
        return round((self._monotonic() - started) * 1000.0, 3)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _provider_error_record(exc: ProviderError, *, stage: BaselineErrorStage) -> BaselineErrorRecord:
    """把 `ProviderError` 转成结构化记录；不重试、不改写 code。"""
    return BaselineErrorRecord(
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        status_code=exc.status_code,
        provider_request_id=exc.provider_request_id,
        stage=stage,
    )


def _display_path(path: Path) -> str:
    """路径记录：项目根内的文件记相对路径，根外记绝对路径（便于复现与审计）。"""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def _write_json(path: Path, payload: BaseModel) -> None:
    """确定性 JSON 落盘：`sort_keys=True` + 固定缩进，保证同输入逐字节一致。"""
    data = payload.model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


__all__ = [
    "BASELINE_VERSION",
    "BASELINE_PROMPT_VERSION",
    "BASELINE_MULTITURN_FORMAT_VERSION",
    "BASELINE_ID_SCHEME_VERSION",
    "GATE_A_PROTOCOL_VERSION",
    "PROMPT_SUMMARY_MAX_CHARS",
    "PROMPT_SUMMARY_TEMPLATE",
    "SKIP_REASON_ACCEPT",
    "BASELINE_EMPTY_LLM_OUTPUT",
    "BASELINE_SYSTEM_PROMPT_TEMPLATE",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DATASET_PATH",
    "DEFAULT_PROTOCOL_PATH",
    "BaselineHistoryEntry",
    "DirectBaselineRunner",
    "build_request_messages",
    "build_run_id",
    "collapse_whitespace",
    "default_code_version",
    "digest",
    "format_history_summary",
    "load_dataset",
    "load_run_config",
    "make_record_id",
    "render_system_prompt",
    "sha256_bytes",
    "sha256_file",
    "sha256_text",
    "summarize_prompt",
]
