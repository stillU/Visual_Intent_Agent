"""Step 03 policy 单测共享工具（唯一命名模块）：确定性 Intent / ExecutionRevision 工厂。

原位于 `tests/policy/conftest.py`，Rev.1 按测试 helper 命名规则迁入本模块
（见 `docs/handoffs/architecture_decision_001.md` 提案 2 / ARCHITECTURE.md 第 7 节）：
跨用例共享的工厂必须放在唯一命名模块，`conftest.py` 不再定义可导入符号。

所有工厂只使用字面量、可复现的值，不调用 `new_id()` / 时钟 / 随机数；
返回**新**对象，绝不原地修改入参。
"""

from __future__ import annotations

from typing import Any

from visual_intent_agent.domain import (
    EvidenceRef,
    ExecutionRevision,
    Resolution,
    ResolutionRecord,
    VisualIntent,
)

#: 全部 12 条白名单路径（按 Facet 分组顺序；权威清单在 domain.paths）。
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

#: 一组互不触发任何冲突规则的典型值（宽幅/自然光/写实摄影都要避开）。
NON_CONFLICTING_VALUES: dict[str, str | int] = {
    "subject.description": "a lone astronaut",
    "subject.count": 1,
    "subject.pose_action": "standing still",
    "composition.framing": "medium shot",
    "environment.mode": "studio",
    "environment.location": "seamless grey backdrop",
    "style.primary": "cinematic realism",
    "style.description": "soft film grain",
    "lighting.character": "dramatic side light",
    "camera.angle": "eye level",
    "camera.depth_of_field": "shallow",
    "color.palette": "muted teal and amber",
}

#: 使全部 `block` 决策都可解决的最小值集合（camera 维度保持 omit，color 保持 omit）。
READY_VALUES: dict[str, str | int] = {
    "subject.description": NON_CONFLICTING_VALUES["subject.description"],
    "subject.pose_action": NON_CONFLICTING_VALUES["subject.pose_action"],
    "composition.framing": NON_CONFLICTING_VALUES["composition.framing"],
    "environment.mode": NON_CONFLICTING_VALUES["environment.mode"],
    "environment.location": NON_CONFLICTING_VALUES["environment.location"],
    "style.primary": NON_CONFLICTING_VALUES["style.primary"],
    "lighting.character": NON_CONFLICTING_VALUES["lighting.character"],
}


def resolve_path(intent: VisualIntent, path: str) -> Any:
    """按白名单路径读取 Intent 的当前值（只读）。"""
    facet_name, _, field_name = path.partition(".")
    return getattr(getattr(intent, facet_name), field_name)


def path_snapshot(intent: VisualIntent) -> dict[str, Any]:
    """12 条路径的逐值快照。"""
    return {path: resolve_path(intent, path) for path in ALL_PATHS}


def intent_from(values: dict[str, Any] | None = None) -> VisualIntent:
    """由 `{path: value}` 构造 Intent（未给出的路径保持 None）。"""
    payload: dict[str, dict[str, Any]] = {}
    for path, value in (values or {}).items():
        facet_name, field_name = path.split(".", 1)
        payload.setdefault(facet_name, {})[field_name] = value
    return VisualIntent.model_validate(payload)


def empty_intent() -> VisualIntent:
    return VisualIntent()


def full_intent() -> VisualIntent:
    """全部 12 条路径都有值（值本身互不冲突）。"""
    return intent_from(NON_CONFLICTING_VALUES)


def ready_intent() -> VisualIntent:
    """全部 `block` 决策都已解决的 Intent（camera / color 走 omit）。"""
    return intent_from(READY_VALUES)


def with_value(intent: VisualIntent, path: str, value: Any) -> VisualIntent:
    """返回在 `path` 上带值的新 Intent（不修改入参）。"""
    facet_name, field_name = path.split(".", 1)
    facet = getattr(intent, facet_name)
    new_facet = facet.model_copy(update={field_name: value})
    return intent.model_copy(update={facet_name: new_facet})


def with_resolution(
    intent: VisualIntent,
    path: str,
    resolution: Resolution | str,
    *,
    reason: str | None = None,
    message_id: str = "msg_1",
) -> VisualIntent:
    """返回带 `resolutions[path]` 记录的新 Intent（不修改入参）。"""
    resolutions = dict(intent.resolutions)
    resolutions[path] = ResolutionRecord(
        resolution=Resolution(resolution),
        evidence_refs=[EvidenceRef(message_id=message_id)],
        reason=reason,
    )
    return intent.model_copy(update={"resolutions": resolutions})


def execution(output_size: str = "1024x1024") -> ExecutionRevision:
    """确定性 ExecutionRevision（默认方形输出）。"""
    return ExecutionRevision(
        execution_revision_id="erev_policy_test",
        session_id="ses_policy_test",
        parent_revision_id=None,
        target_model="qwen-image-3.0",
        output_size=output_size,
    )


def unresolved_map(intent: VisualIntent, execution_context: ExecutionRevision | None = None):
    """`{path: action_value}`（仅用于断言；保持规则表顺序）。"""
    from visual_intent_agent.policy import assess

    resolution = assess(intent, execution_context)
    return {
        decision.path: decision.action.value
        for decision in resolution.unresolved_decisions
    }


def issue_codes(result: Any) -> list[str]:
    """IntentResolution 的全部 issue code（保持顺序）。"""
    return [issue.code for issue in result.issues]


def conflict_codes(result: Any) -> list[str]:
    """IntentResolution 的全部 conflict code（保持顺序）。"""
    return [issue.code for issue in result.conflicts]
