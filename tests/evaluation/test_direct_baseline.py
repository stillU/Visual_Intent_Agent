"""MVP v0.3 Step 02：Direct LLM Baseline 离线确定性测试。

覆盖任务书验收条件：

- Fake Provider 下单轮与多轮可确定性重放（注入固定时钟/计时器 → 逐字节一致）；
- 真实与 Fake 走同一公开接口（`LLMProvider` / `ImageProvider` Protocol 注入）；
- 所有输入、Prompt 与图片记录都有稳定关联 ID；
- 依赖边界：`evaluation/models.py` 与 `evaluation/direct_baseline.py` 未 import
  `domain` / `validation` / `policy` / `workflow` / `prompt_engine` / `realization`，
  且不 import 任何具体 Provider 实现（只用 Protocol）；
- `accept` 轮不执行、Provider 失败按错误分类记录且不重试/不换模型。

本目录遵循 v0.2 测试惯例：无 `conftest.py`、不跨测试目录 import、默认全离线
（只用 Fake Provider），全部输出写 `tmp_path`，不触网、不写 `data/` 或 `outputs/`。
"""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from visual_intent_agent.config import Settings
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.fake_image import FAKE_PNG_BYTES, FakeImageProvider
from visual_intent_agent.providers.fake_llm import FAKE_LLM_MODEL, FakeLLMProvider
from visual_intent_agent.providers.image import (
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageProvider,
)
from visual_intent_agent.providers.llm import LLMProvider, LLMRequest, LLMResponse

from evaluation.direct_baseline import (
    BASELINE_EMPTY_LLM_OUTPUT,
    BASELINE_MULTITURN_FORMAT_VERSION,
    BASELINE_PROMPT_VERSION,
    BASELINE_SYSTEM_PROMPT_TEMPLATE,
    BASELINE_VERSION,
    DEFAULT_CONFIG_PATH,
    DEFAULT_DATASET_PATH,
    DEFAULT_PROTOCOL_PATH,
    PROMPT_SUMMARY_MAX_CHARS,
    SKIP_REASON_ACCEPT,
    DirectBaselineRunner,
    build_request_messages,
    build_run_id,
    collapse_whitespace,
    digest,
    format_history_summary,
    load_dataset,
    load_run_config,
    make_record_id,
    render_system_prompt,
    sha256_bytes,
    sha256_file,
    sha256_text,
    summarize_prompt,
)
from evaluation.models import (
    BASELINE_SCHEMA_VERSION,
    BaselineCase,
    BaselineCaseRecord,
    BaselineImageCallRecord,
    BaselineLLMCallRecord,
    BaselinePromptRecord,
    BaselineProviderSnapshot,
    BaselineRunRecord,
    BaselineTurn,
    BaselineTurnRecord,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_DIR = PROJECT_ROOT / "evaluation"
BASELINE_SOURCES = (
    EVALUATION_DIR / "models.py",
    EVALUATION_DIR / "direct_baseline.py",
)

#: 测试用假 key：形态刻意不像真实 key（避免任何密钥模式误报）。
FAKE_SECRET = "unit-test-placeholder-not-a-real-key"


# ---------------------------------------------------------------------------
# 测试工具（不引入 conftest，全部本地）
# ---------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "provider_base_url": "https://provider.invalid/v1",
        "provider_api_key": SecretStr(FAKE_SECRET),
        "llm_model": "qwen3.8-max",
        "image_model": "qwen-image-3.0",
        "llm_timeout_seconds": 60.0,
        "image_timeout_seconds": 120.0,
        "http_max_retries": 2,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _make_case(
    case_id: str = "synthetic-001",
    turns: tuple[tuple[str, str], ...] = (("t1", "user_message"),),
    *,
    scenario: str = "single_turn_complete",
    turn_type: str = "single",
    text: str = "一只橘猫在窗台上",
) -> BaselineCase:
    return BaselineCase(
        case_id=case_id,
        dataset_version="core_v0_3",
        scenario=scenario,
        turn_type=turn_type,  # type: ignore[arg-type]
        description="测试用合成案例",
        tags=["synthetic"],
        turns=[
            BaselineTurn(turn_id=turn_id, kind=kind, user_text=f"{text}（{turn_id}）")  # type: ignore[arg-type]
            for turn_id, kind in turns
        ],
    )


def _load_frozen_case(case_id: str) -> BaselineCase:
    for case in load_dataset():
        if case.case_id == case_id:
            return case
    raise AssertionError(f"case {case_id!r} not in frozen dataset")


def _write_dataset(path: Path, cases: list[BaselineCase]) -> Path:
    path.write_text(
        "".join(json.dumps(c.model_dump(mode="json"), ensure_ascii=False) + "\n" for c in cases),
        encoding="utf-8",
    )
    return path


def _constant_clock() -> object:
    frozen = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    return lambda: frozen


def _counter_monotonic(start: float = 0.0, step: float = 1.0):
    state = {"t": start}

    def now() -> float:
        state["t"] += step
        return state["t"]

    return now


def _runner(tmp_path: Path, **kwargs: object) -> DirectBaselineRunner:
    params: dict[str, object] = {
        "llm": FakeLLMProvider(["默认 Prompt"]),
        "image": FakeImageProvider(),
        "settings": _settings(),
        "output_dir": tmp_path,
        "dataset_path": DEFAULT_DATASET_PATH,
        "config_path": DEFAULT_CONFIG_PATH,
        "protocol_path": DEFAULT_PROTOCOL_PATH,
    }
    params.update(kwargs)
    return DirectBaselineRunner(**params)  # type: ignore[arg-type]


class _ScriptedLLM:
    """自定义 Fake：实现同一 `LLMProvider` Protocol，可脚本化多轮与失败。"""

    def __init__(self, script: list[str | Exception]) -> None:
        self.requests: list[LLMRequest] = []
        self._script = list(script)

    def complete(self, request: LLMRequest) -> LLMResponse:
        assert isinstance(request, LLMRequest)
        self.requests.append(request)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            content=item,
            model="scripted-llm",
            provider_request_id=f"scripted-{len(self.requests)}",
        )


# ---------------------------------------------------------------------------
# 1. 依赖边界（静态扫描）
# ---------------------------------------------------------------------------


class TestDependencyBoundary:
    """任务书验收条件：未 import 禁区模块；只用 Provider Protocol。"""

    _FORBIDDEN_ROOTS = frozenset(
        {"domain", "validation", "policy", "workflow", "prompt_engine", "realization"}
    )
    _ALLOWED_VIA_ROOTS = frozenset({"config", "providers"})
    _FORBIDDEN_CONCRETE_ADAPTERS = frozenset(
        {"fake_llm", "fake_image", "openai_llm", "openai_image"}
    )

    @staticmethod
    def _imported_modules(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    @pytest.mark.parametrize("path", BASELINE_SOURCES, ids=lambda p: p.name)
    def test_no_forbidden_product_modules(self, path: Path):
        for module in self._imported_modules(path):
            parts = module.split(".")
            assert parts[0] not in self._FORBIDDEN_ROOTS, (path.name, module)
            if parts[0] == "visual_intent_agent" and len(parts) > 1:
                assert parts[1] not in self._FORBIDDEN_ROOTS, (path.name, module)

    @pytest.mark.parametrize("path", BASELINE_SOURCES, ids=lambda p: p.name)
    def test_only_config_and_providers_are_imported_from_product_package(self, path: Path):
        for module in self._imported_modules(path):
            parts = module.split(".")
            if parts[0] != "visual_intent_agent" or len(parts) == 1:
                continue
            assert parts[1] in self._ALLOWED_VIA_ROOTS, (path.name, module)

    @pytest.mark.parametrize("path", BASELINE_SOURCES, ids=lambda p: p.name)
    def test_no_concrete_provider_implementation_is_imported(self, path: Path):
        for module in self._imported_modules(path):
            leaf = module.split(".")[-1]
            assert leaf not in self._FORBIDDEN_CONCRETE_ADAPTERS, (path.name, module)

    @pytest.mark.parametrize("path", BASELINE_SOURCES, ids=lambda p: p.name)
    def test_no_environment_access(self, path: Path):
        source = path.read_text(encoding="utf-8")
        assert "os.environ" not in source
        assert "getenv" not in source
        assert "dotenv" not in source
        assert "load_settings" not in source
        modules = self._imported_modules(path)
        assert "os" not in modules

    def test_concrete_fakes_satisfy_the_same_protocols(self):
        assert isinstance(FakeLLMProvider(["x"]), LLMProvider)
        assert isinstance(FakeImageProvider(), ImageProvider)
        assert isinstance(_ScriptedLLM(["x"]), LLMProvider)

    def test_real_adapters_satisfy_the_same_protocols(self):
        # 只做 Protocol 结构检查，不发任何请求；Mock client 避免构造真实连接。
        from unittest.mock import Mock

        from visual_intent_agent.providers.openai_image import OpenAIImageProvider
        from visual_intent_agent.providers.openai_llm import OpenAICompatibleLLMProvider

        settings = _settings()
        assert isinstance(OpenAICompatibleLLMProvider(settings, client=Mock()), LLMProvider)
        assert isinstance(OpenAIImageProvider(settings, client=Mock()), ImageProvider)


# ---------------------------------------------------------------------------
# 2. 冻结输入加载
# ---------------------------------------------------------------------------


class TestFrozenInputs:
    def test_run_config_reads_frozen_knobs(self):
        config = load_run_config(DEFAULT_CONFIG_PATH)
        assert config.config_version == "gate_a_v0_3"
        assert config.image_size == "1024x1024"
        assert config.images_per_generation == 1

    def test_run_config_rejects_invalid_size(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text(
            json.dumps(
                {
                    "config_version": "x",
                    "image": {"size": "1024*1024", "images_per_generation": 1},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_run_config(path)

    def test_dataset_loads_all_frozen_cases(self):
        cases = load_dataset()
        assert len(cases) == 22
        assert cases[0].case_id == "s01-complete-001"
        assert len({c.case_id for c in cases}) == 22

    def test_dataset_rejects_duplicate_case_id(self, tmp_path: Path):
        case = _make_case()
        payload = json.dumps(case.model_dump(mode="json"), ensure_ascii=False)
        path = tmp_path / "dup.jsonl"
        path.write_text(f"{payload}\n{payload}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate case_id"):
            load_dataset(path)

    def test_dataset_rejects_blank_user_text(self, tmp_path: Path):
        payload = json.dumps(
            {
                "case_id": "synthetic-001",
                "dataset_version": "core_v0_3",
                "scenario": "single_turn_complete",
                "turn_type": "single",
                "description": "d",
                "tags": [],
                "turns": [{"turn_id": "t1", "kind": "user_message", "user_text": "   "}],
            },
            ensure_ascii=False,
        )
        path = tmp_path / "bad.jsonl"
        path.write_text(payload + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid case"):
            load_dataset(path)

    def test_answer_variants_are_read_but_never_used_by_baseline(self, tmp_path: Path):
        payload = json.dumps(
            {
                "case_id": "synthetic-av",
                "dataset_version": "core_v0_3",
                "scenario": "missing_core_decision",
                "turn_type": "multi",
                "description": "d",
                "tags": [],
                "turns": [
                    {"turn_id": "t1", "kind": "user_message", "user_text": "文本一"},
                    {
                        "turn_id": "t2",
                        "kind": "clarification_answer",
                        "user_text": "默认回答",
                        "answer_variants": {"style.primary": "变体回答"},
                    },
                ],
            },
            ensure_ascii=False,
        )
        path = tmp_path / "av.jsonl"
        path.write_text(payload + "\n", encoding="utf-8")
        case = load_dataset(path)[0]
        assert case.turns[1].answer_variants == {"style.primary": "变体回答"}

        llm = FakeLLMProvider(["p1", "p2"])
        runner = _runner(tmp_path, llm=llm, dataset_path=path)
        record = runner.run_case(case)
        assert [m.content for m in llm.requests[1].messages if m.role == "user"] == [
            "文本一",
            "默认回答",
        ]
        assert record.turns[1].input_text == "默认回答"


# ---------------------------------------------------------------------------
# 3. 单轮链路
# ---------------------------------------------------------------------------


class TestSingleTurn:
    def test_single_turn_chain_records_everything(self, tmp_path: Path):
        case = _load_frozen_case("s01-complete-001")
        user_text = case.turns[0].user_text
        llm = FakeLLMProvider(["  一只橘猫蜷在沙发扶手，写实摄影，室内暖光  "])
        image = FakeImageProvider()
        runner = _runner(tmp_path, llm=llm, image=image)
        record = runner.run_case(case)

        assert record.status == "completed"
        assert len(record.turns) == 1
        turn = record.turns[0]
        assert turn.turn_id == "t1"
        assert turn.turn_index == 1
        assert turn.turn_kind == "user_message"
        assert turn.input_text == user_text
        assert turn.skipped is False
        assert turn.status == "completed"
        assert turn.multiturn_format_version == BASELINE_MULTITURN_FORMAT_VERSION

        # LLM 调用面：只发 messages；model/temperature/max_tokens/response_format 全为 None。
        call = turn.llm_call
        assert call is not None
        assert [m.role for m in call.messages] == ["system", "user"]
        assert call.messages[0].content == render_system_prompt("qwen-image-3.0")
        assert call.messages[1].content == user_text
        assert call.model_requested is None
        assert call.temperature_sent is None
        assert call.max_tokens_sent is None
        assert call.response_format_sent is None
        assert call.retry_count == 0
        assert call.status == "ok"
        assert call.raw_output == "  一只橘猫蜷在沙发扶手，写实摄影，室内暖光  "
        assert call.model_returned == FAKE_LLM_MODEL

        sent = llm.requests[0]
        assert sent.model is None
        assert sent.temperature is None
        assert sent.max_tokens is None
        assert sent.response_format is None

        # 最终 Prompt：仅剥离首尾空白，不改写内容。
        prompt = turn.prompt
        assert prompt is not None
        assert prompt.text == "一只橘猫蜷在沙发扶手，写实摄影，室内暖光"
        assert prompt.text_sha256 == sha256_text(prompt.text)
        assert prompt.system_prompt_version == BASELINE_PROMPT_VERSION
        assert prompt.source == "llm_output"

        # 图像调用：逐字发送最终 Prompt + 冻结尺寸/模型。
        image_call = turn.image_call
        assert image_call is not None
        assert image_call.prompt_id == prompt.prompt_id
        assert image_call.prompt_text == prompt.text
        assert image_call.size == "1024x1024"
        assert image_call.model_requested == "qwen-image-3.0"
        assert image_call.seed is None  # Provider 未返回就是 None，禁止伪造
        assert image_call.status == "ok"
        assert image_call.retry_count == 0
        assert image_call.multiturn_format_version == BASELINE_MULTITURN_FORMAT_VERSION
        assert image.requests[0].prompt == prompt.text
        assert image.requests[0].size == "1024x1024"
        assert image.requests[0].model == "qwen-image-3.0"

        # 图片 Artifact：落盘字节、sha256 与相对路径可还原。
        assert len(image_call.images) == 1
        artifact = image_call.images[0]
        assert artifact.mime_type == "image/png"
        assert artifact.byte_size == len(FAKE_PNG_BYTES)
        assert artifact.sha256 == sha256_bytes(FAKE_PNG_BYTES)
        assert artifact.multiturn_format_version == BASELINE_MULTITURN_FORMAT_VERSION
        written = tmp_path / artifact.relative_path
        assert written.read_bytes() == FAKE_PNG_BYTES
        assert not artifact.relative_path.startswith("/")
        assert turn.error is None


# ---------------------------------------------------------------------------
# 4. 多轮链路与冻结格式
# ---------------------------------------------------------------------------


class TestMultiTurn:
    """`baseline_multiturn_v1`：逐字用户文本 + 规则化前轮 Prompt 摘要。"""

    def test_history_format_is_frozen_and_deterministic(self, tmp_path: Path):
        case = _load_frozen_case("s09-multiturn-003")  # user_message, image_feedback, ...
        turns = case.turns
        assert [t.kind for t in turns[:3]] == [
            "user_message",
            "image_feedback",
            "clarification_answer",
        ]
        llm = _ScriptedLLM(["第一轮 Prompt", "第二轮 Prompt", "第三轮 Prompt"])
        runner = _runner(tmp_path, llm=llm, image=FakeImageProvider())
        result = runner.run_cases([case])
        record = result.cases[0]

        assert record.status == "completed"
        # 三条非 accept 轮各一次调用；accept 轮不调用。
        assert len(llm.requests) == 3

        first, second, third = llm.requests
        assert [m.role for m in first.messages] == ["system", "user"]
        assert first.messages[1].content == turns[0].user_text

        assert [m.role for m in second.messages] == ["system", "user", "assistant", "user"]
        assert second.messages[1].content == turns[0].user_text
        assert second.messages[2].content == format_history_summary(1, "第一轮 Prompt")
        assert second.messages[3].content == turns[1].user_text

        assert [m.role for m in third.messages] == [
            "system",
            "user",
            "assistant",
            "user",
            "assistant",
            "user",
        ]
        assert third.messages[3].content == turns[1].user_text
        assert third.messages[4].content == format_history_summary(2, "第二轮 Prompt")
        assert third.messages[5].content == turns[2].user_text

        # 版本写入 Run 级与逐轮记录。
        assert result.run.multiturn_format_version == BASELINE_MULTITURN_FORMAT_VERSION
        for turn_record in record.turns:
            assert turn_record.multiturn_format_version == BASELINE_MULTITURN_FORMAT_VERSION

    def test_accept_turn_is_skipped_and_absent_from_history(self, tmp_path: Path):
        case = _make_case(
            turns=(
                ("t1", "user_message"),
                ("t2", "image_feedback"),
                ("t3", "accept"),
            ),
            turn_type="multi",
        )
        llm = _ScriptedLLM(["p1", "p2"])
        image = FakeImageProvider()
        runner = _runner(tmp_path, llm=llm, image=image)
        record = runner.run_case(case)

        assert [t.status for t in record.turns] == ["completed", "completed", "skipped"]
        skipped = record.turns[2]
        assert skipped.skipped is True
        assert skipped.skip_reason == SKIP_REASON_ACCEPT
        assert skipped.llm_call is None and skipped.prompt is None and skipped.image_call is None
        assert skipped.input_text == case.turns[2].user_text
        # accept 轮不产生任何 Provider 调用，也不进入后续历史。
        assert len(llm.requests) == 2
        assert len(image.requests) == 2
        assert case.turns[2].user_text not in [
            m.content for req in llm.requests for m in req.messages
        ]

    def test_failed_llm_turn_has_no_summary_in_later_history(self, tmp_path: Path):
        case = _make_case(
            turns=(
                ("t1", "user_message"),
                ("t2", "image_feedback"),
                ("t3", "clarification_answer"),
            ),
            turn_type="multi",
        )
        llm = _ScriptedLLM(
            [ProviderError.server("upstream 503"), "第二轮 Prompt", "第三轮 Prompt"]
        )
        runner = _runner(tmp_path, llm=llm, image=FakeImageProvider())
        record = runner.run_case(case)

        assert record.turns[0].status == "failed"
        assert record.status == "failed"
        # 第二轮历史只有用户文本（没有可摘要的 Prompt）。
        assert [m.role for m in llm.requests[1].messages] == ["system", "user", "user"]
        assert llm.requests[1].messages[1].content == case.turns[0].user_text
        assert llm.requests[1].messages[2].content == case.turns[1].user_text
        # 第三轮可获得第二轮的摘要。
        assert [m.role for m in llm.requests[2].messages] == [
            "system",
            "user",
            "user",
            "assistant",
            "user",
        ]

    def test_summary_rules_are_rule_based(self):
        assert collapse_whitespace("a\n\n b\t c  ") == "a b c"
        summary, truncated = summarize_prompt("short prompt")
        assert (summary, truncated) == ("short prompt", False)

        long_prompt = "词" * (PROMPT_SUMMARY_MAX_CHARS + 50)
        summary, truncated = summarize_prompt(long_prompt)
        assert truncated is True
        assert summary == "词" * PROMPT_SUMMARY_MAX_CHARS + "…"
        assert len(summary) == PROMPT_SUMMARY_MAX_CHARS + 1

        message = format_history_summary(3, long_prompt)
        assert message.startswith("[第 3 轮 Prompt 摘要（已截断）] ")
        assert format_history_summary(2, "短") == "[第 2 轮 Prompt 摘要] 短"

    def test_build_request_messages_skips_missing_summaries(self):
        from evaluation.direct_baseline import BaselineHistoryEntry

        messages = build_request_messages(
            "SYS",
            [
                BaselineHistoryEntry(1, "u1", None),
                BaselineHistoryEntry(2, "u2", "p2"),
            ],
            "u3",
        )
        assert [(m.role, m.content) for m in messages] == [
            ("system", "SYS"),
            ("user", "u1"),
            ("user", "u2"),
            ("assistant", format_history_summary(2, "p2")),
            ("user", "u3"),
        ]


# ---------------------------------------------------------------------------
# 5. Provider 失败与缺失数据口径
# ---------------------------------------------------------------------------


class TestProviderFailures:
    def test_llm_failure_is_classified_and_not_retried(self, tmp_path: Path):
        case = _make_case(
            turns=(("t1", "user_message"), ("t2", "image_feedback")), turn_type="multi"
        )
        llm = _ScriptedLLM([ProviderError.auth("bad key"), "prompt-2"])
        image = FakeImageProvider()
        runner = _runner(tmp_path, llm=llm, image=image)
        record = runner.run_case(case)

        failed = record.turns[0]
        assert failed.status == "failed"
        assert failed.prompt is None
        assert failed.image_call is None
        error = failed.error
        assert error is not None
        assert error.code == "provider.auth"
        assert error.retryable is False
        assert error.stage == "llm"
        assert failed.llm_call is not None and failed.llm_call.status == "failed"
        assert failed.llm_call.retry_count == 0
        # 失败轮不调用图像 Provider；后续轮继续执行（失败不从分母剔除）。
        assert len(image.requests) == 1
        assert record.turns[1].status == "completed"
        assert record.status == "failed"

    def test_image_failure_keeps_prompt_and_records_stage(self, tmp_path: Path):
        def boom(request: ImageGenerationRequest) -> ImageGenerationResult:
            raise ProviderError.timeout("image call timed out")

        case = _make_case()
        llm = FakeLLMProvider(["prompt-1"])
        runner = _runner(tmp_path, llm=llm, image=FakeImageProvider(boom))
        record = runner.run_case(case)

        turn = record.turns[0]
        assert turn.status == "failed"
        assert turn.prompt is not None and turn.prompt.text == "prompt-1"
        assert turn.image_call is not None and turn.image_call.status == "failed"
        assert turn.image_call.images == []
        error = turn.error
        assert error is not None
        assert error.code == "provider.timeout"
        assert error.retryable is True
        assert error.stage == "image"

    def test_blank_llm_output_is_a_baseline_error_without_image_call(self, tmp_path: Path):
        case = _make_case()
        llm = FakeLLMProvider(["   \n  "])
        image = FakeImageProvider()
        runner = _runner(tmp_path, llm=llm, image=image)
        record = runner.run_case(case)

        turn = record.turns[0]
        assert turn.status == "failed"
        assert turn.prompt is None and turn.image_call is None
        assert turn.llm_call is not None and turn.llm_call.status == "ok"
        error = turn.error
        assert error is not None
        assert error.code == BASELINE_EMPTY_LLM_OUTPUT
        assert error.retryable is False
        assert error.stage == "baseline"
        assert image.requests == []

    def test_failures_stay_in_records(self, tmp_path: Path):
        case = _make_case()
        llm = _ScriptedLLM([ProviderError.rate_limited("429")])
        runner = _runner(tmp_path, llm=llm, image=FakeImageProvider())
        record = runner.run_case(case)
        # 失败被原样保存（不是静默丢弃），失败轮仍占位。
        assert len(record.turns) == 1
        assert record.turns[0].status == "failed"
        # 没有换模型/换样本：只发生了一次 LLM 调用。
        assert len(llm.requests) == 1


# ---------------------------------------------------------------------------
# 6. 稳定关联 ID
# ---------------------------------------------------------------------------


class TestStableAssociationIds:
    def test_digest_and_record_id_are_pure_hash_scheme(self):
        assert digest("a", "b") == hashlib.sha256("a|b".encode("utf-8")).hexdigest()
        assert make_record_id("pfx", "a", "b") == f"pfx_{digest('a', 'b')[:16]}"

    def test_all_records_carry_correlated_stable_ids(self, tmp_path: Path):
        case = _make_case(
            turns=(("t1", "user_message"), ("t2", "image_feedback"), ("t3", "accept")),
            turn_type="multi",
        )
        runner = _runner(tmp_path, llm=_ScriptedLLM(["p1", "p2"]), image=FakeImageProvider())
        record = runner.run_case(case)
        run_id = runner.run_id

        assert run_id.startswith("brun_")
        assert record.run_id == run_id
        assert record.record_id == make_record_id("bcase", run_id, case.case_id)

        seen: set[str] = {run_id, record.record_id}
        for turn in record.turns:
            assert turn.run_id == run_id
            assert turn.record_id == make_record_id("bturn", run_id, case.case_id, turn.turn_id)
            seen.add(turn.record_id)
            if turn.prompt is not None:
                assert turn.prompt.prompt_id == make_record_id(
                    "bprompt", run_id, case.case_id, turn.turn_id
                )
                assert turn.prompt.run_id == run_id
                seen.add(turn.prompt.prompt_id)
            if turn.llm_call is not None:
                assert turn.llm_call.call_id == make_record_id(
                    "bllm", run_id, case.case_id, turn.turn_id
                )
                seen.add(turn.llm_call.call_id)
            if turn.image_call is not None:
                assert turn.image_call.call_id == make_record_id(
                    "bimg", run_id, case.case_id, turn.turn_id
                )
                seen.add(turn.image_call.call_id)
                for artifact in turn.image_call.images:
                    assert artifact.artifact_id == make_record_id(
                        "bart", run_id, case.case_id, turn.turn_id, str(artifact.image_index)
                    )
                    assert artifact.image_call_id == turn.image_call.call_id
                    assert artifact.run_id == run_id
                    seen.add(artifact.artifact_id)
        assert len(seen) == len(
            [
                run_id,
                record.record_id,
                *[t.record_id for t in record.turns],
                *[t.prompt.prompt_id for t in record.turns if t.prompt],
                *[t.llm_call.call_id for t in record.turns if t.llm_call],
                *[t.image_call.call_id for t in record.turns if t.image_call],
                *[
                    a.artifact_id
                    for t in record.turns
                    if t.image_call
                    for a in t.image_call.images
                ],
            ]
        )

    def test_run_id_is_deterministic_and_nonce_sensitive(self):
        base = dict(
            code_version="code-v1",
            dataset_version="core_v0_3",
            dataset_sha256="a" * 64,
            config_version="gate_a_v0_3",
            config_sha256="b" * 64,
        )
        assert build_run_id(**base) == build_run_id(**base)
        assert build_run_id(**base, run_nonce="rep1") != build_run_id(**base, run_nonce="rep2")

    def test_run_id_tracks_dataset_content(self, tmp_path: Path):
        case = _make_case()
        path_a = _write_dataset(tmp_path / "a.jsonl", [case])
        path_b = _write_dataset(
            tmp_path / "b.jsonl", [_make_case(text="另一段用户文本")]
        )
        runner_a = _runner(tmp_path / "a", llm=FakeLLMProvider(["p"]), dataset_path=path_a)
        runner_b = _runner(tmp_path / "b", llm=FakeLLMProvider(["p"]), dataset_path=path_b)
        assert runner_a.dataset_sha256 != runner_b.dataset_sha256
        assert runner_a.run_id != runner_b.run_id


# ---------------------------------------------------------------------------
# 7. 确定性重放
# ---------------------------------------------------------------------------


class TestDeterministicReplay:
    def _run(self, tmp_path: Path, output_name: str) -> DirectBaselineRunner:
        case = _load_frozen_case("s09-multiturn-003")
        runner = DirectBaselineRunner(
            llm=FakeLLMProvider(["prompt-1", "prompt-2", "prompt-3"]),
            image=FakeImageProvider(),
            settings=_settings(),
            output_dir=tmp_path / output_name,
            dataset_path=DEFAULT_DATASET_PATH,
            config_path=DEFAULT_CONFIG_PATH,
            protocol_path=DEFAULT_PROTOCOL_PATH,
            clock=_constant_clock(),  # type: ignore[arg-type]
            monotonic=_counter_monotonic(),  # type: ignore[arg-type]
        )
        runner.run_cases([case])
        return runner

    def test_replay_is_byte_identical(self, tmp_path: Path):
        runner_a = self._run(tmp_path, "run_a")
        runner_b = self._run(tmp_path, "run_b")
        assert runner_a.run_id == runner_b.run_id

        dir_a, dir_b = runner_a.output_dir, runner_b.output_dir
        files_a = sorted(p.relative_to(dir_a) for p in dir_a.rglob("*") if p.is_file())
        files_b = sorted(p.relative_to(dir_b) for p in dir_b.rglob("*") if p.is_file())
        assert files_a == files_b and files_a
        for relative in files_a:
            assert (dir_a / relative).read_bytes() == (dir_b / relative).read_bytes(), relative

    def test_replay_produces_identical_records(self, tmp_path: Path):
        case = _load_frozen_case("s09-multiturn-003")
        records = []
        for name in ("rec_a", "rec_b"):
            runner = DirectBaselineRunner(
                llm=FakeLLMProvider(["prompt-1", "prompt-2", "prompt-3"]),
                image=FakeImageProvider(),
                settings=_settings(),
                output_dir=tmp_path / name,
                clock=_constant_clock(),  # type: ignore[arg-type]
                monotonic=_counter_monotonic(),  # type: ignore[arg-type]
            )
            records.append(runner.run_case(case))
        assert records[0] == records[1]
        assert records[0].model_dump(mode="json") == records[1].model_dump(mode="json")

    def test_latency_and_retry_are_recorded(self, tmp_path: Path):
        case = _make_case()
        runner = DirectBaselineRunner(
            llm=FakeLLMProvider(["p"]),
            image=FakeImageProvider(),
            settings=_settings(),
            output_dir=tmp_path,
            clock=_constant_clock(),  # type: ignore[arg-type]
            monotonic=_counter_monotonic(start=0.0, step=0.25),  # type: ignore[arg-type]
        )
        turn = runner.run_case(case).turns[0]
        assert turn.llm_call is not None and turn.llm_call.latency_ms == 250.0
        assert turn.image_call is not None and turn.image_call.latency_ms == 250.0
        assert turn.llm_call.retry_count == 0
        assert turn.image_call.retry_count == 0


# ---------------------------------------------------------------------------
# 8. 数据集驱动与落盘布局
# ---------------------------------------------------------------------------


class TestDatasetDrivenRun:
    def test_full_frozen_dataset_runs_offline(self, tmp_path: Path):
        cases = load_dataset()
        non_accept = sum(1 for c in cases for t in c.turns if t.kind != "accept")
        accepts = sum(1 for c in cases for t in c.turns if t.kind == "accept")

        # 脚本化回调：同一输入恒定输出，且不依赖轮次计数。
        def handler(request: LLMRequest) -> str:
            return "Prompt: " + request.messages[-1].content

        llm = FakeLLMProvider(handler)
        image = FakeImageProvider()
        runner = _runner(tmp_path, llm=llm, image=image)
        result = runner.run_dataset()

        assert result.run.case_count == 22
        assert result.run.case_ids == [c.case_id for c in cases]
        assert len(result.cases) == 22
        assert all(case.status == "completed" for case in result.cases)
        assert len(llm.requests) == non_accept
        assert len(image.requests) == non_accept
        assert accepts > 0
        skipped = [t for case in result.cases for t in case.turns if t.skipped]
        assert len(skipped) == accepts
        assert all(t.skip_reason == SKIP_REASON_ACCEPT for t in skipped)

    def test_run_json_contains_frozen_metadata_and_no_secrets(self, tmp_path: Path):
        runner = _runner(tmp_path, llm=FakeLLMProvider(["p"]))
        result = runner.run_cases([_make_case()])

        payload = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
        assert payload["run_id"] == runner.run_id
        assert payload["schema_version"] == BASELINE_SCHEMA_VERSION
        assert payload["baseline_version"] == BASELINE_VERSION
        assert payload["baseline_prompt_version"] == BASELINE_PROMPT_VERSION
        assert payload["multiturn_format_version"] == BASELINE_MULTITURN_FORMAT_VERSION
        assert payload["id_scheme_version"] == "baseline_ids_sha256_v1"
        assert payload["code_version"] == result.run.code_version
        assert payload["dataset_sha256"] == sha256_file(DEFAULT_DATASET_PATH)
        assert payload["config_sha256"] == sha256_file(DEFAULT_CONFIG_PATH)
        assert payload["protocol_sha256"] == sha256_file(DEFAULT_PROTOCOL_PATH)
        assert payload["protocol_version"] == "gate_a_protocol_v0_3"
        assert payload["dataset_version"] == "core_v0_3"
        assert payload["case_count"] == 1 and payload["case_ids"] == ["synthetic-001"]
        provider = payload["provider"]
        assert provider["llm_model"] == "qwen3.8-max"
        assert provider["image_model"] == "qwen-image-3.0"
        assert provider["llm_timeout_seconds"] == 60.0
        assert provider["image_timeout_seconds"] == 120.0
        assert provider["http_max_retries"] == 2
        assert provider["temperature_sent"] is None
        assert provider["max_tokens_sent"] is None
        assert provider["response_format_sent"] is None
        assert set(provider) == {
            "llm_model",
            "image_model",
            "llm_timeout_seconds",
            "image_timeout_seconds",
            "http_max_retries",
            "temperature_sent",
            "max_tokens_sent",
            "response_format_sent",
            "image_size",
            "images_per_generation",
        }
        assert payload["system_prompt"] == render_system_prompt("qwen-image-3.0")
        assert payload["system_prompt_template"] == BASELINE_SYSTEM_PROMPT_TEMPLATE

        raw = (tmp_path / "run.json").read_text(encoding="utf-8")
        for forbidden in ("api_key", "provider_api_key", FAKE_SECRET, "Bearer", "base_url"):
            assert forbidden not in raw

    def test_persistence_layout_and_round_trip(self, tmp_path: Path):
        case = _make_case(
            turns=(("t1", "user_message"), ("t2", "accept")), turn_type="multi"
        )
        runner = _runner(tmp_path, llm=FakeLLMProvider(["p"]), image=FakeImageProvider())
        runner.run_cases([case])

        case_dir = tmp_path / "cases" / case.case_id
        assert (case_dir / "case.json").is_file()
        assert (case_dir / "turns" / "turn_01.json").is_file()
        assert (case_dir / "turns" / "turn_02.json").is_file()
        assert (case_dir / "images" / "turn_01_image_1.png").is_file()

        case_payload = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        reloaded = BaselineCaseRecord.model_validate(case_payload)
        assert [t.status for t in reloaded.turns] == ["completed", "skipped"]
        assert reloaded.turns[1].skip_reason == SKIP_REASON_ACCEPT
        turn_payload = json.loads(
            (case_dir / "turns" / "turn_01.json").read_text(encoding="utf-8")
        )
        turn = BaselineTurnRecord.model_validate(turn_payload)
        assert turn.prompt is not None
        assert turn.image_call is not None
        artifact = turn.image_call.images[0]
        assert (runner.output_dir / artifact.relative_path).read_bytes() == FAKE_PNG_BYTES

    def test_run_case_does_not_touch_repo_data_dirs(self, tmp_path: Path):
        before_data = sorted(p.name for p in (PROJECT_ROOT / "data").glob("*"))
        before_outputs = sorted(p.name for p in (PROJECT_ROOT / "outputs").glob("*"))
        _runner(tmp_path, llm=FakeLLMProvider(["p"])).run_case(_make_case())
        assert sorted(p.name for p in (PROJECT_ROOT / "data").glob("*")) == before_data
        assert sorted(p.name for p in (PROJECT_ROOT / "outputs").glob("*")) == before_outputs


# ---------------------------------------------------------------------------
# 9. 合同模型不变量
# ---------------------------------------------------------------------------


class TestContractModels:
    def test_all_record_models_are_frozen_and_forbid_extra(self):
        models = [
            BaselineCase,
            BaselineTurn,
            BaselineProviderSnapshot,
            BaselinePromptRecord,
            BaselineLLMCallRecord,
            BaselineImageCallRecord,
            BaselineTurnRecord,
            BaselineCaseRecord,
            BaselineRunRecord,
        ]
        for model in models:
            config = model.model_config
            assert config.get("frozen") is True, model.__name__
            assert config.get("extra") == "forbid", model.__name__

    def test_provider_snapshot_rejects_secret_fields(self):
        with pytest.raises(ValidationError):
            BaselineProviderSnapshot(
                llm_model="m",
                image_model="i",
                llm_timeout_seconds=1.0,
                image_timeout_seconds=1.0,
                http_max_retries=0,
                image_size="1024x1024",
                images_per_generation=1,
                provider_api_key=FAKE_SECRET,
            )

    def test_sampling_parameters_are_contractually_none(self):
        with pytest.raises(ValidationError):
            BaselineProviderSnapshot(
                llm_model="m",
                image_model="i",
                llm_timeout_seconds=1.0,
                image_timeout_seconds=1.0,
                http_max_retries=0,
                image_size="1024x1024",
                images_per_generation=1,
                response_format_sent={"type": "json_object"},
            )

    def test_records_are_immutable(self, tmp_path: Path):
        record = _runner(tmp_path, llm=FakeLLMProvider(["p"])).run_case(_make_case())
        with pytest.raises(ValidationError):
            record.status = "failed"  # type: ignore[misc]

    def test_turn_record_validators(self):
        base = dict(
            record_id="bturn_x",
            run_id="brun_x",
            case_id="c",
            turn_id="t1",
            turn_index=1,
            turn_kind="accept",
            input_text="文本",
            multiturn_format_version=BASELINE_MULTITURN_FORMAT_VERSION,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        with pytest.raises(ValidationError):
            BaselineTurnRecord(**base, skipped=True, status="skipped")  # 缺 skip_reason
        with pytest.raises(ValidationError):
            BaselineTurnRecord(**base, status="completed")  # completed 缺调用记录
        with pytest.raises(ValidationError):
            BaselineTurnRecord(
                **{**base, "turn_kind": "user_message"},
                skipped=False,
                status="failed",
            )  # failed 缺 error
