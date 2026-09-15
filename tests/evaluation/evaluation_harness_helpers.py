"""MVP v0.3 Step 03 测试共享工具（唯一命名模块；无 conftest、不跨目录 import）。

自包含、全离线：

- `make_settings()`：伪造 Settings（不读 .env）；
- `ScriptedLLM` / `make_llm_factory`：按 system prompt 分派三角色的脚本化 Fake LLM
  （Baseline 直出 Prompt / Intent Interpreter / Feedback Interpreter），工厂每次调用
  返回**全新**实例（同一脚本），供 Runner 的 "每次（系统 × 重复/案例）取新 Provider"
  语义做确定性重放；
- `write_dataset` / `write_annotations` / `write_config`：合成迷你数据集/标注/配置
  （写入 tmp_path；Runner 全部路径可注入，绝不触碰冻结物）；
- `fixed_clock` / `fixed_monotonic`：固定时间源（确定性重放断言）；
- `structural_projection`：结构投影（掩码产品侧 uuid4 ID），用于"同配置重放产生
  结构一致结果"的断言。
"""

from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from pydantic import SecretStr

from visual_intent_agent.config import Settings
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.llm import LLMRequest, LLMResponse

#: 伪造 Provider 配置（不是真实凭据；任何测试都不得读取项目 .env）。
FAKE_BASE_URL = "https://provider.invalid/v1"
FAKE_SECRET = "unit-test-placeholder-not-a-real-key"
FAKE_LLM_MODEL = "qwen3.8-max"
FAKE_IMAGE_MODEL = "qwen-image-3.0"

FIXED_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: Baseline 系统提示的识别子串（Step 02 冻结模板首句）。
BASELINE_PROMPT_MARKER = "图像 Prompt 生成器"
FEEDBACK_PROMPT_MARKER = "Feedback Interpreter"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "provider_base_url": FAKE_BASE_URL,
        "provider_api_key": SecretStr(FAKE_SECRET),
        "llm_model": FAKE_LLM_MODEL,
        "image_model": FAKE_IMAGE_MODEL,
        "llm_timeout_seconds": 60.0,
        "image_timeout_seconds": 120.0,
        "http_max_retries": 2,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def fixed_clock() -> Callable[[], datetime]:
    return lambda: FIXED_EPOCH


def fixed_monotonic() -> Callable[[], float]:
    """每次调用前进 1ms 的确定性计时器（latency_ms 可重放）。"""
    state = {"t": 1000.0}

    def tick() -> float:
        state["t"] += 0.001
        return state["t"]

    return tick


# ---------------------------------------------------------------------------
# 脚本化 LLM
# ---------------------------------------------------------------------------


def interpreter_response(
    *entries: dict[str, Any],
    detected_conflicts: Iterable[dict[str, Any]] = (),
    unresolved_language: Iterable[str] = (),
) -> str:
    return json.dumps(
        {
            "candidate_deltas": list(entries),
            "detected_conflicts": list(detected_conflicts),
            "unresolved_language": list(unresolved_language),
        }
    )


def set_entry(
    path: str,
    value: str | int,
    *,
    answers: bool = False,
    resolution: str | None = None,
) -> dict[str, Any]:
    return {
        "operation": "SET",
        "path": path,
        "value": value,
        "resolution": resolution,
        "answers_pending_question": answers,
        "evidence_fragment": None,
    }


def pin_entry(path: str) -> dict[str, Any]:
    return {
        "operation": "PIN",
        "path": path,
        "value": None,
        "resolution": None,
        "answers_pending_question": False,
        "evidence_fragment": None,
    }


def clear_entry(path: str) -> dict[str, Any]:
    return {
        "operation": "CLEAR",
        "path": path,
        "value": None,
        "resolution": None,
        "answers_pending_question": False,
        "evidence_fragment": None,
    }


def delegate_entry(path: str, *, answers: bool = True) -> dict[str, Any]:
    return {
        "operation": "SET",
        "path": path,
        "value": None,
        "resolution": "user_delegated",
        "answers_pending_question": answers,
        "evidence_fragment": None,
    }


def feedback_response(
    decision: str,
    *,
    candidate_deltas: Iterable[dict[str, Any]] = (),
    clarify_path: str | None = None,
) -> str:
    payload: dict[str, Any] = {"decision": decision}
    if candidate_deltas:
        payload["candidate_deltas"] = list(candidate_deltas)
    if clarify_path is not None:
        payload["clarify_path"] = clarify_path
        payload["clarify_reason"] = "need user input"
    return json.dumps(payload)


def revise_entry(path: str, value: str | int | None = None) -> dict[str, Any]:
    return {
        "operation": "SET",
        "path": path,
        "value": value,
        "resolution": None,
        "evidence_fragment": None,
    }


#: 一条"首轮即可确认"的 Interpreter 响应（七个阻塞路径全部 SET）。
FULL_INTENT_RESPONSE = interpreter_response(
    set_entry("subject.description", "一只橘色虎斑猫蜷在沙发扶手上睡觉"),
    set_entry("subject.pose_action", "蜷着睡觉"),
    set_entry("style.primary", "photorealistic"),
    set_entry("environment.mode", "indoor"),
    set_entry("environment.location", "洒满暖光的客厅一角"),
    set_entry("composition.framing", "medium_shot"),
    set_entry("lighting.character", "soft warm"),
)

#: 一条覆盖关键词真值的 Baseline Prompt 文本。
BASELINE_PROMPT_TEXT = (
    "photorealistic 写实摄影：一只橘色虎斑猫蜷在客厅沙发扶手上睡觉，"
    "室内，中景 medium shot，柔和温暖的灯光。"
)


class ScriptedLLM:
    """按 system prompt 分派三角色的脚本化 LLM（队列耗尽即抛 invalid_request）。

    - Baseline 直出 Prompt：system 含 "图像 Prompt 生成器"；
    - FeedbackEngine：system 含 "Feedback Interpreter"；
    - 其余一律视为 Intent Interpreter。
    """

    def __init__(
        self,
        *,
        baseline: Iterable[str] = (),
        interpreter: Iterable[str] = (),
        feedback: Iterable[str] = (),
        baseline_default: str | None = None,
        interpreter_default: str | None = None,
        feedback_default: str | None = None,
    ) -> None:
        self.baseline_queue: deque[str] = deque(baseline)
        self.interpreter_queue: deque[str] = deque(interpreter)
        self.feedback_queue: deque[str] = deque(feedback)
        self.baseline_default = baseline_default
        self.interpreter_default = interpreter_default
        self.feedback_default = feedback_default
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        system = request.messages[0].content if request.messages else ""
        if BASELINE_PROMPT_MARKER in system:
            queue, default = self.baseline_queue, self.baseline_default
        elif FEEDBACK_PROMPT_MARKER in system:
            queue, default = self.feedback_queue, self.feedback_default
        else:
            queue, default = self.interpreter_queue, self.interpreter_default
        if queue:
            content = queue.popleft()
        elif default is not None:
            content = default
        else:
            raise ProviderError.invalid_request(
                f"ScriptedLLM script is exhausted for this role (system={system[:40]!r})"
            )
        return LLMResponse(content=content, model=FAKE_LLM_MODEL)


def make_llm_factory(**kwargs: Any) -> Callable[[], ScriptedLLM]:
    """返回"每次调用产出全新 ScriptedLLM（同一脚本）"的工厂（重放确定性）。"""

    def factory() -> ScriptedLLM:
        return ScriptedLLM(**kwargs)

    return factory


# ---------------------------------------------------------------------------
# 合成数据集 / 标注 / 配置
# ---------------------------------------------------------------------------


def make_fixture_case(
    case_id: str,
    turns: list[dict[str, Any]],
    *,
    scenario: str = "single_turn_complete",
    turn_type: str = "single",
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "dataset_version": "synthetic_v1",
        "scenario": scenario,
        "turn_type": turn_type,
        "description": f"合成测试案例 {case_id}",
        "tags": ["synthetic"],
        "turns": turns,
    }


def make_turn(turn_id: str, kind: str, user_text: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"turn_id": turn_id, "kind": kind, "user_text": user_text}
    payload.update(extra)
    return payload


def make_annotation(
    case_id: str,
    turn_annotations: list[dict[str, Any]],
    *,
    scenario: str = "single_turn_complete",
    must_mention_groups: list[list[str]] | None = None,
    must_not_mention: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "dataset_version": "synthetic_v1",
        "scenario": scenario,
        "annotation_basis": "synthetic test annotation",
        "turn_annotations": turn_annotations,
        "image_evaluation_dimensions": [],
        "prompt_expectations": {
            "must_mention_groups": must_mention_groups if must_mention_groups is not None else [["猫", "cat"]],
            "must_not_mention": must_not_mention if must_not_mention is not None else ["狗", "dog"],
        },
    }


def make_turn_annotation(turn_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "turn_id": turn_id,
        "expected_deltas": [],
        "acceptable_extra_deltas": [],
        "expected_rejections": [],
        "forbidden_change_paths": [],
        "paths_must_remain_unset": [],
        "expected_resolution_records": {},
        "blocking_missing_paths_after_turn": [],
        "must_clarify": [],
        "must_not_clarify_paths": [],
        "acceptable_clarify_paths": [],
        "expected_conflicts": [],
        "expected_feedback_decision": None,
        "expected_carry": None,
        "expected_outcome": None,
        "notes": None,
    }
    payload.update(overrides)
    return payload


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def write_config(path: Path, *, repetitions: int = 2) -> Path:
    payload = {
        "config_version": "synthetic_config_v1",
        "credentials": {
            "base_url_env": "VIA_PROVIDER_BASE_URL",
            "api_key_env": "VIA_PROVIDER_API_KEY",
        },
        "llm": {
            "model_env": "VIA_LLM_MODEL",
            "model_default": FAKE_LLM_MODEL,
            "system_b_response_format": {"type": "json_object"},
            "baseline_response_format": None,
        },
        "image": {
            "model_env": "VIA_IMAGE_MODEL",
            "model_default": FAKE_IMAGE_MODEL,
            "size": "1024x1024",
            "images_per_generation": 1,
        },
        "repetitions": {
            "llm_layer_runs_per_case": repetitions,
            "image_runs_per_case": 1,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 结构投影（重放一致性断言）
# ---------------------------------------------------------------------------

#: 产品侧 uuid4 派生 ID（`new_id(prefix)` = `<prefix>_<uuid4().hex>`），重放间必然不同；
#: 以子串模式掩码（路径 / 嵌套文本中的 ID 一并覆盖）。
_DYNAMIC_ID = re.compile(r"(ses|msg|irev|erev|cnf|qst|gen|pra|fbk|rlz)_[0-9a-f]{32}")

#: 其值覆盖 uuid4 派生内容的键（`summary_hash` 哈希了含 evidence `msg_*` 的 Intent）。
_VOLATILE_VALUE_KEYS = frozenset({"summary_hash"})


def structural_projection(value: Any) -> Any:
    """递归掩码重放间必然不同的部分（产品 uuid4 ID / 含 uuid 的内容哈希）。

    评测层哈希 ID（erun/ecase/eturn/emet/eart/ellm/eimg/brun/...）由内容寻址，
    重放稳定，不在掩码范围。
    """
    if isinstance(value, dict):
        return {
            key: (
                "<volatile-value>"
                if key in _VOLATILE_VALUE_KEYS
                else structural_projection(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [structural_projection(item) for item in value]
    if isinstance(value, str):
        return _DYNAMIC_ID.sub("<dynamic-id>", value)
    return value
