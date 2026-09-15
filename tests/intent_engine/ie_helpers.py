"""Step 05 intent_engine 测试共享工具。

`tests/` 下各目录不是 Python package（与 Step 01～03 测试一致），因此这里用
模块名直接导入（`import ie_helpers`；pytest 会把测试文件所在目录加入 `sys.path`）。

只使用确定性的字面量 ID（不调用 `new_id()`），保证测试可复现。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from visual_intent_agent.domain import ExecutionRevision, IntentDelta, VisualIntent
from visual_intent_agent.intent_engine import (
    IntentEngine,
    Interpreter,
    IntentResolveRequest,
    InterpreterResult,
)
from visual_intent_agent.policy import IntentResolution, QuestionSpec
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.providers.llm import LLMRequest, LLMResponse

#: 固定字面量 ID。
MESSAGE_ID = "msg_0001"
QUESTION_ID = "qst_0001"

#: 12 条白名单路径（按 Facet 分组顺序；权威清单在 domain.paths）。
ALL_PATHS: tuple[str, ...] = (
    "subject.description",
    "subject.count",
    "subject.pose_action",
    "composition.framing",
    "environment.mode",
    "environment.location",
    "style.primary",
    "style.description",
    "lighting.character",
    "camera.angle",
    "camera.depth_of_field",
    "color.palette",
)


def read_path(intent: VisualIntent, path: str) -> object | None:
    facet_name, _, field_name = path.partition(".")
    return getattr(getattr(intent, facet_name), field_name)


def path_value_map(intent: VisualIntent) -> dict[str, object | None]:
    return {path: read_path(intent, path) for path in ALL_PATHS}


def resolution_map(intent: VisualIntent) -> dict[str, str]:
    return {
        path: record.resolution.value for path, record in intent.resolutions.items()
    }


def changed_paths(before: VisualIntent, after: VisualIntent) -> set[str]:
    return {path for path in ALL_PATHS if read_path(before, path) != read_path(after, path)}


def make_request(
    current_intent: VisualIntent | None = None,
    message_text: str = "镜头拉远",
    *,
    message_id: str = MESSAGE_ID,
    pending_question: QuestionSpec | None = None,
    available_message_ids: frozenset[str] | None = None,
) -> IntentResolveRequest:
    return IntentResolveRequest(
        current_intent=current_intent if current_intent is not None else VisualIntent(),
        message_id=message_id,
        message_text=message_text,
        pending_question=pending_question,
        available_message_ids=(
            frozenset({message_id})
            if available_message_ids is None
            else available_message_ids
        ),
    )


def pending_question(
    target_path: str,
    *,
    question_id: str | None = QUESTION_ID,
    allow_delegate: bool = True,
    allow_custom: bool = True,
    suggested_values: tuple[str, ...] = (),
    reason: str = "this decision blocks confirmation",
    question_text: str | None = "请选择这一项？",
) -> QuestionSpec:
    return QuestionSpec(
        target_path=target_path,
        reason=reason,
        allow_delegate=allow_delegate,
        allow_custom=allow_custom,
        suggested_values=suggested_values,
        question_text=question_text,
        question_id=question_id,
    )


def delta(
    operation: str,
    path: str,
    *,
    value: str | int | None = None,
    resolution: str | None = None,
    answers_pending_question: bool = False,
    evidence_fragment: str = "镜头拉远",
) -> dict[str, Any]:
    """构造 LLM 输出里的单条候选 Delta（dict 形态，用于脚本化响应）。"""
    payload: dict[str, Any] = {
        "operation": operation,
        "path": path,
        "answers_pending_question": answers_pending_question,
        "evidence_fragment": evidence_fragment,
    }
    if value is not None:
        payload["value"] = value
    if resolution is not None:
        payload["resolution"] = resolution
    return payload


def script(
    candidate_deltas: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    *,
    detected_conflicts: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    unresolved_language: list[str] | tuple[str, ...] = (),
) -> str:
    """构造一条完整的 LLM JSON 响应文本。"""
    return json.dumps(
        {
            "candidate_deltas": list(candidate_deltas),
            "detected_conflicts": list(detected_conflicts),
            "unresolved_language": list(unresolved_language),
        },
        ensure_ascii=False,
    )


def run(request: IntentResolveRequest, *responses: str) -> tuple[IntentResolution, FakeLLMProvider]:
    """用队列式 Fake 跑一次完整 resolve。"""
    provider = FakeLLMProvider(list(responses))
    engine = IntentEngine(Interpreter(provider))
    return engine.resolve(request), provider


def run_with(
    request: IntentResolveRequest,
    handler: Callable[[LLMRequest], LLMResponse | str],
) -> tuple[IntentResolution, FakeLLMProvider]:
    provider = FakeLLMProvider(handler)
    engine = IntentEngine(Interpreter(provider))
    return engine.resolve(request), provider


def interpret(request: IntentResolveRequest, *responses: str) -> InterpreterResult:
    """只跑 Interpreter（不经过 Validator/Reducer/Policy）。"""
    return Interpreter(FakeLLMProvider(list(responses))).interpret(request)


def issue_codes(resolution: IntentResolution) -> list[str]:
    return [issue.code for issue in resolution.issues]


def delta_paths(deltas: list[IntentDelta]) -> list[str]:
    return [delta.path for delta in deltas]


def execution(output_size: str = "1024x1024") -> ExecutionRevision:
    """确定性 ExecutionRevision（Step 06 起随 `IntentResolveRequest` 进入 `assess`）。"""
    return ExecutionRevision(
        execution_revision_id="erev_ie_test",
        session_id="ses_ie_test",
        parent_revision_id=None,
        target_model="qwen-image-3.0",
        output_size=output_size,
    )
