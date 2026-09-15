"""MVP v0.3 Step 02：Direct LLM Baseline（Baseline A）合同模型。

本模块是 Baseline A 的**唯一数据合同**，只服务评测层，不属于产品业务包：

    BaselineRunConfig            冻结实验配置中基线需要的非密旋钮（尺寸/张数）
    BaselineCase / BaselineTurn  从 evaluation/fixtures/core_v0_3.jsonl 读入的输入
    BaselineProviderSnapshot     只记模型名/超时/重试等非密信息（严禁明文 key）
    BaselineLLMCallRecord        一次 LLM 调用：请求消息、原始输出、耗时、重试、错误
    BaselinePromptRecord         最终 Prompt：稳定 prompt_id + 文本 + sha256
    BaselineImageCallRecord      一次图像调用：最终 Prompt、seed、错误、耗时
    BaselineImageArtifactRecord  一张落盘图片的稳定 artifact_id 与相对路径
    BaselineTurnRecord           逐轮记录（accept 轮 skipped）
    BaselineCaseRecord           单案例聚合
    BaselineRunRecord            Run 级元数据（dataset/config/protocol 哈希、Provider 快照）
    BaselineRunResult            Run 元数据 + 全部案例记录（不落盘为一个 JSON）

稳定关联 ID 方案（冻结 `BASELINE_ID_SCHEME_VERSION`）：全部 ID 都是
`sha256("|".join(parts))[:16]` 的确定性哈希，前缀区分类型；同一次冻结运行内
run → case → turn → LLM/Prompt/Image/Artifact 层层可追溯，且**同输入同 Fake 脚本
两次运行产生相同 ID**（见 direct_baseline.py 的 `make_record_id`）。ID 不含随机数、
不含时钟，便于重放比对。

全部模型 `frozen=True, extra="forbid"`（与仓库既有合同模型一致）；
`created_at` 一律 tz-aware UTC。`provider_api_key` 等凭据**绝不进入任何字段**。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from visual_intent_agent.providers.llm import LLMMessage, LLMUsage

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: 本模块记录信封的版本（独立于产品 `domain.SCHEMA_VERSION`；评测层不 import domain）。
BASELINE_SCHEMA_VERSION = "baseline_v1"
BaselineSchemaVersion = Literal["baseline_v1"]

#: 轮次状态：`skipped` 只用于 `accept` 轮（协议第 3 节：Baseline 不执行 accept）。
BaselineTurnStatus = Literal["completed", "skipped", "failed"]

#: 单次 Provider 调用状态。
BaselineCallStatus = Literal["ok", "failed"]

#: 冻结的轮次种类（与数据集 `turns[].kind` 一致）。
BaselineTurnKind = Literal[
    "user_message",
    "clarification_answer",
    "image_feedback",
    "accept",
]

#: 错误发生阶段：`llm` / `image` 为 Provider 边界；`baseline` 为基线自身判定
#: （如 LLM 返回空白文本，无法构造合法图像请求）。
BaselineErrorStage = Literal["llm", "image", "baseline"]


def _ensure_utc(value: datetime) -> datetime:
    """拒绝 naive datetime；把 tz-aware 值归一到 UTC。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware UTC; naive datetimes are rejected")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    """JSON 序列化形态：ISO 8601 带 `+00:00`。"""
    return value.astimezone(timezone.utc).isoformat()


#: tz-aware UTC datetime：校验 + JSON 序列化形态一次冻结。
_UtcDatetime = Annotated[datetime, AfterValidator(_ensure_utc), PlainSerializer(_iso_utc)]


# ---------------------------------------------------------------------------
# 冻结配置输入（evaluation/configs/gate_a_v0_3.json 的非密子集）
# ---------------------------------------------------------------------------


class BaselineRunConfig(BaseModel):
    """基线执行器从冻结配置文件中读取的非密旋钮。

    只读取基线**必须**知道的三项；Provider 模型名/超时/重试一律来自注入的
    `Settings`（见 `BaselineProviderSnapshot`），基线不自行读环境变量。
    """

    model_config = _FROZEN

    config_version: str
    image_size: str
    images_per_generation: int = Field(ge=1)

    @field_validator("config_version")
    @classmethod
    def _config_version_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("config_version must be a non-empty string")
        return value

    @field_validator("image_size")
    @classmethod
    def _image_size_must_be_width_x_height(cls, value: str) -> str:
        # 复用 Provider 合同的尺寸校验（单一事实来源），不复制正则。
        from visual_intent_agent.providers.image import is_valid_image_size

        if not is_valid_image_size(value):
            raise ValueError(
                'image_size must use the "<width>x<height>" form with positive integers, '
                f"got {value!r}"
            )
        return value


# ---------------------------------------------------------------------------
# 数据集输入（Baseline 只消费 case_id / scenario / turn_type / turns）
# ---------------------------------------------------------------------------


class BaselineTurn(BaseModel):
    """数据集中的一轮输入；`user_text` 对 Baseline 一律逐字使用。"""

    model_config = _FROZEN

    turn_id: str
    kind: BaselineTurnKind
    user_text: str
    #: System B 专用（按待答问题 target_path 选答案）；Baseline 恒用 `user_text`，
    #: 此字段只为忠实读入冻结数据集而保留，绝不参与基线逻辑。
    answer_variants: dict[str, str] | None = None

    @field_validator("turn_id")
    @classmethod
    def _turn_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("turn_id must be a non-empty string")
        return value

    @field_validator("user_text")
    @classmethod
    def _user_text_must_be_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("user_text must be a non-empty string")
        return value


class BaselineCase(BaseModel):
    """数据集中的一个案例（从 frozen JSONL 行读入）。"""

    model_config = _FROZEN

    case_id: str
    dataset_version: str
    scenario: str
    #: R1-B：r1 fixture 记录来源案例 ID（旧 fixture 缺省 None，向后兼容）。
    parent_case_id: str | None = None
    turn_type: Literal["single", "multi"]
    description: str
    tags: list[str] = Field(default_factory=list)
    turns: list[BaselineTurn] = Field(min_length=1)

    @field_validator("case_id")
    @classmethod
    def _case_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("case_id must be a non-empty string")
        return value


# ---------------------------------------------------------------------------
# Provider 快照（非密）
# ---------------------------------------------------------------------------


class BaselineProviderSnapshot(BaseModel):
    """Run 级 Provider 配置快照：只记非密信息。

    明确**不含** `provider_base_url` 与 `provider_api_key`；`temperature_sent` /
    `max_tokens_sent` / `response_format_sent` 恒为 `None`——这是协议第 1 节的冻结
    条件（Baseline 不显式设置这些参数、不使用 response_format），用 `None` 字面量
    类型把该约束固化成合同，而不是只写在文档里。
    """

    model_config = _FROZEN

    llm_model: str
    image_model: str
    llm_timeout_seconds: float = Field(gt=0)
    image_timeout_seconds: float = Field(gt=0)
    http_max_retries: int = Field(ge=0)
    temperature_sent: None = None
    max_tokens_sent: None = None
    response_format_sent: None = None
    image_size: str
    images_per_generation: int = Field(ge=1)

    @field_validator("llm_model", "image_model")
    @classmethod
    def _model_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model name must be a non-empty string")
        return value


# ---------------------------------------------------------------------------
# 调用与 Artifact 记录
# ---------------------------------------------------------------------------


class BaselineErrorRecord(BaseModel):
    """一次失败的结构化记录（协议第 7 节：失败保留在分母，携带错误 code）。

    `code` 为 `provider.*`（Provider 边界）或 `baseline.*`（基线自身判定）。
    `message` 只允许 Provider adapter 已脱敏的文本；本模型不做二次改写。
    """

    model_config = _FROZEN

    code: str
    message: str
    retryable: bool
    status_code: int | None = None
    provider_request_id: str | None = None
    stage: BaselineErrorStage

    @field_validator("code")
    @classmethod
    def _code_must_be_namespaced(cls, value: str) -> str:
        if "." not in value or value != value.lower():
            raise ValueError(f"error code must be a lowercase '<area>.<name>' string, got {value!r}")
        return value


class BaselinePromptRecord(BaseModel):
    """最终 Prompt 记录：稳定 `prompt_id` + 文本 + sha256。

    `text` 是**最终**提交给图像 Provider 的 Prompt（LLM 原始输出仅做首尾空白剥离，
    不做任何改写）。`prompt_id` 由 `baseline_ids_sha256_v1` 方案确定性生成，
    与 run/case/turn 关联。
    """

    model_config = _FROZEN

    schema_version: BaselineSchemaVersion = BASELINE_SCHEMA_VERSION
    prompt_id: str
    run_id: str
    case_id: str
    turn_id: str
    turn_index: int = Field(ge=1)
    text: str
    text_sha256: str
    source: Literal["llm_output"] = "llm_output"
    system_prompt_version: str
    multiturn_format_version: str

    @field_validator("text")
    @classmethod
    def _text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt text must be a non-empty string")
        return value


class BaselineImageArtifactRecord(BaseModel):
    """一张已落盘图片的引用（稳定 `artifact_id` + 相对输出目录的路径）。"""

    model_config = _FROZEN

    schema_version: BaselineSchemaVersion = BASELINE_SCHEMA_VERSION
    artifact_id: str
    run_id: str
    case_id: str
    turn_id: str
    image_call_id: str
    image_index: int = Field(ge=1)
    relative_path: str
    mime_type: str
    byte_size: int = Field(ge=0)
    sha256: str
    multiturn_format_version: str

    @field_validator("relative_path")
    @classmethod
    def _path_must_be_relative(cls, value: str) -> str:
        if not value or value.startswith(("/", "\\")) or ".." in value.split("/"):
            raise ValueError(f"relative_path must stay inside the output dir, got {value!r}")
        return value

    @field_validator("sha256")
    @classmethod
    def _sha256_shape(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class BaselineLLMCallRecord(BaseModel):
    """一次 LLM 调用及其全量可观测信息。

    - `model_requested=None` 表示由真实 adapter 使用 `Settings.llm_model`
      （与 System B 三处调用点一致）；
    - `temperature_sent` / `max_tokens_sent` / `response_format_sent` 恒为 `None`
      （协议第 1 节冻结条件：Baseline 直接 Prompt 生成不使用这些参数）；
    - `retry_count` 只统计**基线层**重试（恒为 0）；adapter 内部冻结重试不通过
      `LLMProvider` Protocol 暴露，故不在此虚构，见 handoff 说明。
    """

    model_config = _FROZEN

    schema_version: BaselineSchemaVersion = BASELINE_SCHEMA_VERSION
    call_id: str
    run_id: str
    case_id: str
    turn_id: str
    turn_index: int = Field(ge=1)
    call_index: int = Field(ge=1)
    messages: list[LLMMessage] = Field(min_length=1)
    model_requested: str | None = None
    model_returned: str | None = None
    temperature_sent: None = None
    max_tokens_sent: None = None
    response_format_sent: None = None
    raw_output: str | None = None
    provider_request_id: str | None = None
    usage: LLMUsage | None = None
    latency_ms: float = Field(ge=0)
    retry_count: int = Field(ge=0, default=0)
    status: BaselineCallStatus
    error: BaselineErrorRecord | None = None

    @model_validator(mode="after")
    def _status_matches_error(self) -> "BaselineLLMCallRecord":
        if self.status == "ok" and (self.error is not None or self.raw_output is None):
            raise ValueError("an ok LLM call must carry raw_output and no error")
        if self.status == "failed" and self.error is None:
            raise ValueError("a failed LLM call must carry an error record")
        return self


class BaselineImageCallRecord(BaseModel):
    """一次图像生成调用及其 Artifact 引用。

    `seed` 只记录 Provider 返回值，未返回则 `None`（禁止伪造）；`retry_count`
    语义同 `BaselineLLMCallRecord`。
    """

    model_config = _FROZEN

    schema_version: BaselineSchemaVersion = BASELINE_SCHEMA_VERSION
    call_id: str
    run_id: str
    case_id: str
    turn_id: str
    turn_index: int = Field(ge=1)
    prompt_id: str
    prompt_text: str
    size: str
    multiturn_format_version: str
    model_requested: str | None = None
    model_returned: str | None = None
    provider_request_id: str | None = None
    seed: int | None = None
    images: list[BaselineImageArtifactRecord] = Field(default_factory=list)
    latency_ms: float = Field(ge=0)
    retry_count: int = Field(ge=0, default=0)
    status: BaselineCallStatus
    error: BaselineErrorRecord | None = None

    @model_validator(mode="after")
    def _status_matches_error(self) -> "BaselineImageCallRecord":
        if self.status == "ok" and (self.error is not None or not self.images):
            raise ValueError("an ok image call must carry at least one artifact and no error")
        if self.status == "failed" and (self.error is None or self.images):
            raise ValueError("a failed image call must carry an error record and no artifact")
        return self


# ---------------------------------------------------------------------------
# 逐轮 / 逐案例 / Run 记录
# ---------------------------------------------------------------------------


class BaselineTurnRecord(BaseModel):
    """逐轮记录：输入、LLM 调用、最终 Prompt、图像调用、错误与耗时。

    `accept` 轮不执行：`skipped=True`、`skip_reason="accept_turn_not_executed"`，
    且不携带任何调用记录（协议第 3 节）。
    """

    model_config = _FROZEN

    schema_version: BaselineSchemaVersion = BASELINE_SCHEMA_VERSION
    record_id: str
    run_id: str
    case_id: str
    turn_id: str
    turn_index: int = Field(ge=1)
    turn_kind: BaselineTurnKind
    input_text: str
    skipped: bool = False
    skip_reason: str | None = None
    multiturn_format_version: str
    history_user_turns: int = Field(ge=0, default=0)
    request_messages: list[LLMMessage] = Field(default_factory=list)
    llm_call: BaselineLLMCallRecord | None = None
    prompt: BaselinePromptRecord | None = None
    image_call: BaselineImageCallRecord | None = None
    status: BaselineTurnStatus
    error: BaselineErrorRecord | None = None
    created_at: _UtcDatetime

    @model_validator(mode="after")
    def _status_consistency(self) -> "BaselineTurnRecord":
        if self.skipped:
            if self.status != "skipped":
                raise ValueError("a skipped turn must have status='skipped'")
            if self.skip_reason is None:
                raise ValueError("a skipped turn must record skip_reason")
            if any(x is not None for x in (self.llm_call, self.prompt, self.image_call, self.error)):
                raise ValueError("a skipped turn must not carry calls, prompts or errors")
            return self
        if self.skip_reason is not None:
            raise ValueError("skip_reason is only meaningful for skipped turns")
        if self.status == "skipped":
            raise ValueError("a non-skipped turn cannot have status='skipped'")
        if self.status == "completed":
            if self.llm_call is None or self.prompt is None or self.image_call is None:
                raise ValueError("a completed turn must carry llm_call, prompt and image_call")
            if self.error is not None:
                raise ValueError("a completed turn must not carry an error")
        if self.status == "failed" and self.error is None:
            raise ValueError("a failed turn must carry an error record")
        return self


class BaselineCaseRecord(BaseModel):
    """单案例聚合：逐轮记录 + 案例级状态。"""

    model_config = _FROZEN

    schema_version: BaselineSchemaVersion = BASELINE_SCHEMA_VERSION
    record_id: str
    run_id: str
    case_id: str
    dataset_version: str
    scenario: str
    turn_type: Literal["single", "multi"]
    turns: list[BaselineTurnRecord] = Field(min_length=1)
    status: Literal["completed", "failed"]
    created_at: _UtcDatetime

    @model_validator(mode="after")
    def _status_matches_turns(self) -> "BaselineCaseRecord":
        failed = any(t.status == "failed" for t in self.turns)
        expected: Literal["completed", "failed"] = "failed" if failed else "completed"
        if self.status != expected:
            raise ValueError(f"case status must be {expected!r} given its turn statuses")
        return self


class BaselineRunRecord(BaseModel):
    """Run 级元数据：冻结输入哈希、代码版本、Provider 快照与冻结格式版本。

    落盘为 `<output_dir>/run.json`（不含逐案例记录，案例记录在
    `cases/<case_id>/case.json`）。
    """

    model_config = _FROZEN

    schema_version: BaselineSchemaVersion = BASELINE_SCHEMA_VERSION
    run_id: str
    baseline_version: str
    baseline_prompt_version: str
    multiturn_format_version: str
    id_scheme_version: str
    code_version: str
    run_nonce: str = ""
    dataset_version: str
    dataset_path: str
    dataset_sha256: str
    config_version: str
    config_path: str
    config_sha256: str
    protocol_version: str
    protocol_path: str
    protocol_sha256: str
    provider: BaselineProviderSnapshot
    system_prompt: str
    system_prompt_template: str
    case_count: int = Field(ge=0)
    case_ids: list[str] = Field(default_factory=list)
    created_at: _UtcDatetime

    @model_validator(mode="after")
    def _case_count_matches_ids(self) -> "BaselineRunRecord":
        if self.case_count != len(self.case_ids):
            raise ValueError("case_count must equal len(case_ids)")
        return self


class BaselineRunResult(BaseModel):
    """`run_dataset` / `run_cases` 的返回值：Run 元数据 + 全部案例记录（内存聚合）。"""

    model_config = _FROZEN

    run: BaselineRunRecord
    cases: list[BaselineCaseRecord] = Field(default_factory=list)


__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "BaselineSchemaVersion",
    "BaselineTurnStatus",
    "BaselineCallStatus",
    "BaselineTurnKind",
    "BaselineErrorStage",
    "BaselineRunConfig",
    "BaselineTurn",
    "BaselineCase",
    "BaselineProviderSnapshot",
    "BaselineErrorRecord",
    "BaselinePromptRecord",
    "BaselineImageArtifactRecord",
    "BaselineLLMCallRecord",
    "BaselineImageCallRecord",
    "BaselineTurnRecord",
    "BaselineCaseRecord",
    "BaselineRunRecord",
    "BaselineRunResult",
]
