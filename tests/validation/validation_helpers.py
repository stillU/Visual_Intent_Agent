"""Step 02 validation 单测共享工具（唯一命名模块）：Intent / Delta / EvidenceContext 工厂与路径快照。

原位于 `tests/validation/conftest.py`，Rev.1 按测试 helper 命名规则迁入本模块
（见 `docs/handoffs/architecture_decision_001.md` 提案 2 / ARCHITECTURE.md 第 7 节）：
跨用例共享的工厂必须放在唯一命名模块，`conftest.py` 不再定义可导入符号。

所有工厂都返回**新**对象（禁止可变默认值共享），并且只使用确定性的字面量 ID，
不依赖 `new_id()`（保证测试可复现）。
"""

from __future__ import annotations

from typing import Any

from visual_intent_agent.domain import (
    DeltaOperation,
    EvidenceRef,
    IntentDelta,
    Resolution,
    ResolutionRecord,
    VisualIntent,
)
from visual_intent_agent.validation import EvidenceContext

#: 全部 12 条白名单路径（按 Facet 分组顺序，仅用于遍历，权威清单在 domain.paths）。
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

#: 典型 Facet 值：subject.count 用 int，其余用 str（与 Validator 检查 3 一致）。
TYPICAL_VALUES: dict[str, str | int] = {
    "subject.description": "a lone astronaut",
    "subject.count": 2,
    "subject.pose_action": "standing still",
    "composition.framing": "medium wide",
    "environment.mode": "photographic",
    "environment.location": "a flooded greenhouse",
    "style.primary": "cinematic realism",
    "style.description": "soft film grain",
    "lighting.character": "overcast soft light",
    "camera.angle": "slightly low angle",
    "camera.depth_of_field": "shallow",
    "color.palette": "muted teal and amber",
}


def resolve_path(intent: VisualIntent, path: str) -> Any:
    """按白名单路径读取 Intent 的当前值（Facet 字段或 None）。"""
    facet_name, _, field_name = path.partition(".")
    return getattr(getattr(intent, facet_name), field_name)


def path_snapshot(intent: VisualIntent) -> dict[str, Any]:
    """12 条路径的逐值快照；用于"非目标路径深比较一致"的属性级断言。"""
    return {path: resolve_path(intent, path) for path in ALL_PATHS}


def non_target_snapshot(intent: VisualIntent, *targets: str) -> dict[str, Any]:
    """除 targets 外全部路径的逐值快照。"""
    return {
        path: resolve_path(intent, path) for path in ALL_PATHS if path not in set(targets)
    }


def full_intent() -> VisualIntent:
    """全部 12 条路径都有值的 Intent（每个 Facet 都被覆盖）。"""
    return VisualIntent.model_validate(
        {
            facet: {
                field: TYPICAL_VALUES[f"{facet}.{field}"]
                for field in facet_model.model_fields
            }
            for facet, facet_model in _facet_models().items()
        }
    )


def _facet_models() -> dict[str, type]:
    return {
        "subject": VisualIntent.model_fields["subject"].annotation,
        "composition": VisualIntent.model_fields["composition"].annotation,
        "environment": VisualIntent.model_fields["environment"].annotation,
        "style": VisualIntent.model_fields["style"].annotation,
        "lighting": VisualIntent.model_fields["lighting"].annotation,
        "camera": VisualIntent.model_fields["camera"].annotation,
        "color": VisualIntent.model_fields["color"].annotation,
    }


def intent_with(path: str, value: Any) -> VisualIntent:
    """只给某一条路径赋值的 Intent（其余保持 None）。"""
    facet_name, field_name = path.split(".", 1)
    return VisualIntent.model_validate({facet_name: {field_name: value}})


def with_resolution(
    intent: VisualIntent, path: str, resolution: Resolution, *, message_id: str = "msg_1"
) -> VisualIntent:
    """返回带 `resolutions[path]` 记录的新 Intent（不修改入参）。"""
    resolutions = dict(intent.resolutions)
    resolutions[path] = ResolutionRecord(
        resolution=resolution, evidence_refs=[evidence(message_id)]
    )
    return intent.model_copy(update={"resolutions": resolutions})


def with_pinned(intent: VisualIntent, *paths: str) -> VisualIntent:
    """返回带保持标记的新 Intent（不修改入参）。"""
    return intent.model_copy(update={"pinned_paths": intent.pinned_paths | set(paths)})


def evidence(message_id: str = "msg_1", pending_question_id: str | None = None) -> EvidenceRef:
    return EvidenceRef(message_id=message_id, pending_question_id=pending_question_id)


def context(
    *message_ids: str,
    pending_question_id: str | None = None,
    pending_question_path: str | None = None,
) -> EvidenceContext:
    """证据上下文工厂；默认可用消息集合为 {"msg_1"}。"""
    return EvidenceContext(
        available_message_ids=frozenset(message_ids or ("msg_1",)),
        pending_question_id=pending_question_id,
        pending_question_path=pending_question_path,
    )


def delta(
    operation: DeltaOperation | str,
    path: str,
    *,
    value: str | int | None = None,
    resolution: Resolution | None = None,
    evidence_refs: list[EvidenceRef] | None = None,
    message_id: str = "msg_1",
    pending_question_id: str | None = None,
) -> IntentDelta:
    """IntentDelta 工厂：默认携带一条 `message_id` 证据（可用消息 ID 为 msg_1）。"""
    return IntentDelta(
        operation=DeltaOperation(operation),
        path=path,
        value=value,
        resolution=resolution,
        evidence_refs=(
            [evidence(message_id, pending_question_id)]
            if evidence_refs is None
            else evidence_refs
        ),
    )


def set_delta(path: str, value: str | int | None = None, **kwargs: Any) -> IntentDelta:
    return delta(DeltaOperation.SET, path, value=value, **kwargs)


def clear_delta(path: str, **kwargs: Any) -> IntentDelta:
    return delta(DeltaOperation.CLEAR, path, **kwargs)


def pin_delta(path: str, **kwargs: Any) -> IntentDelta:
    return delta(DeltaOperation.PIN, path, **kwargs)


def unpin_delta(path: str, **kwargs: Any) -> IntentDelta:
    return delta(DeltaOperation.UNPIN, path, **kwargs)


def issue_codes(result: Any) -> set[str]:
    """ValidationResult 的全部 issue code。"""
    return {issue.code for issue in result.issues}


def rejected_codes(result: Any) -> set[str]:
    """ValidationResult 中被拒 Delta 的 issue code 并集。"""
    return {issue.code for item in result.rejected for issue in item.issues}
