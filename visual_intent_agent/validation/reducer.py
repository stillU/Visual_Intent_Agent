"""不可变 Reducer：把已验证的 Delta 确定性归并到当前 Intent（Step 02）。

    reduce(current_intent, validated_deltas) -> ReduceResult

冻结语义（任务书「Reducer 责任」+ ARCHITECTURE.md 4「Step 02」）：

- 纯函数、确定性：同一输入与同一 Delta 列表永远得到逐值相同的输出；
- 输入对象**不被原地修改**（domain 模型的 `frozen=True` 只阻止属性赋值，
  `resolutions` / `pinned_paths` 内容仍可原地改；本模块一律构造新对象：
  新 Facet、新 `resolutions` dict、新 `frozenset`、新 `VisualIntent`）；
- `SET` 只改指定路径（值 + 该路径的 Resolution 绑定）；
- `CLEAR` 只清除指定路径的值，并同时移除 `resolutions[path]`；CLEAR **不**被解释
  成 user_delegated（移除后该路径回到 missing 语义，不产生任何 ResolutionRecord）；
- `PIN` 不改变字段值，只把路径加入 `pinned_paths`；
- `UNPIN` 只移除保持标记，不改变字段值、也不授权重新设计；
- 未出现在 Delta 中的字段逐值保持；
- Delta 顺序语义：按列表顺序依次应用，后面的 Delta 覆盖前面同路径的效果
  （last-write-wins，见 `tests/validation/test_scenarios.py::TestDeltaOrdering`）；
- 不创建 IntentRevision（revision 组装与 `parent_revision_id` 属 Step 06）；
- 不校验、不改写 Delta：合法性由 `validation.validator.validate` 负责。收到
  Validator 不可能产出的 Delta（例如 `subject.count` 收到 float）时抛
  `ReducerError`，绝不静默修复。

`confirmation_invalidated` 采用保守规则（任务书原文）：
任何重要视觉字段的 `SET` / `CLEAR`，以及任何生效的 `PIN` / `UNPIN`，都为 True。
"""

from __future__ import annotations

from visual_intent_agent.domain import (
    DeltaOperation,
    IntentDelta,
    Resolution,
    ResolutionRecord,
    VisualIntent,
)

from .models import ChangeSummary, ReduceResult

#: 具有非 str 类型的路径（与 validator 检查 3 同一事实：`subject.count` 是 int）。
_INT_VALUE_PATHS: frozenset[str] = frozenset({"subject.count"})


class ReducerError(Exception):
    """Reducer 收到无法应用的 Delta（程序级失败，违反 Validator 前置条件）。

    带 `.code`（命名空间见 ARCHITECTURE.md 5.5）；由调用方按硬门禁处理。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _split_path(path: str) -> tuple[str, str]:
    facet_name, separator, field_name = path.partition(".")
    if not separator or not facet_name or not field_name:
        raise ReducerError(
            "validation.invalid_delta_path",
            f"path {path!r} is not a '<facet>.<field>' path",
        )
    return facet_name, field_name


def _resolve_field(intent: VisualIntent, path: str) -> tuple[str, str]:
    """把路径解析为 (facet 名, 字段名)；路径不在 Intent 结构上时抛 ReducerError。

    PIN / UNPIN 也必须解析：伪造的 `"style"` 这类路径不能被悄悄写进
    `pinned_paths`（否则会构造出违反 domain 不变量的 Intent）。
    """
    facet_name, field_name = _split_path(path)
    facet = getattr(intent, facet_name, None)
    if facet is None or not hasattr(facet, field_name):
        raise ReducerError(
            "validation.invalid_delta_path",
            f"path {path!r} does not resolve to a field of the Intent",
        )
    return facet_name, field_name


def _check_value_type(path: str, value: object) -> None:
    """防御纵深：Reducer 不改写、但有义务拒绝非法值（不静默写入坏状态）。

    Validator 已做同样检查；此处只覆盖"绕过验证直接把 Delta 交给 Reducer"
    的调用错误，抛 `ReducerError` 而不是悄悄写入 `3.5` 这种值。
    """
    valid = (
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        if path in _INT_VALUE_PATHS
        else isinstance(value, str)
    )
    if not valid:
        raise ReducerError(
            "validation.value_type_mismatch",
            f"path {path!r} cannot hold value {value!r} "
            "(deltas must pass validate() before reduce())",
        )


def _set_facet_value(intent: VisualIntent, path: str, value: object) -> VisualIntent:
    """构造携带新值的新 Intent（不触碰输入对象）。"""
    facet_name, field_name = _resolve_field(intent, path)
    if value is not None:
        _check_value_type(path, value)
    facet = getattr(intent, facet_name)
    new_facet = facet.model_copy(update={field_name: value})
    return intent.model_copy(update={facet_name: new_facet})


def _copy_resolutions(intent: VisualIntent) -> dict[str, ResolutionRecord]:
    """新 dict（值是不可变模型，可安全共享）。"""
    return dict(intent.resolutions)


def _apply_set(intent: VisualIntent, delta: IntentDelta) -> VisualIntent:
    """应用 SET：写值（若携带）并同步该路径的 Resolution 绑定。

    Validator 已保证 `SET` 至少携带 value 或 resolution 之一，并且 resolution
    自报的授权强度有证据支持。Reducer 只做机械搬运，不推断"用户真正想要什么"：

    - 携带 `resolution`：把 `resolutions[path]` 写成该 Resolution（带本 Delta 的
      evidence_refs）；
    - 携带 value 但不携带 `resolution`：写入该路径的值；若该路径当前没有
      ResolutionRecord，才新建一条 `user_specified`（用户给出的具体值就是指定），
      **已有记录原样保留**——Reducer 不隐式改写现有授权语义；
    - 纯 resolution 的 SET：不动任何值，只写 Resolution 绑定（如 user_delegated）。
    """
    intent = _set_facet_value(intent, delta.path, delta.value) if delta.value is not None else intent
    resolutions = _copy_resolutions(intent)

    if delta.resolution is not None:
        resolutions[delta.path] = ResolutionRecord(
            resolution=delta.resolution,
            evidence_refs=list(delta.evidence_refs),
        )
    elif delta.path not in resolutions:
        resolutions[delta.path] = ResolutionRecord(
            resolution=Resolution.USER_SPECIFIED,
            evidence_refs=list(delta.evidence_refs),
        )
    return intent.model_copy(update={"resolutions": resolutions})


def _apply_clear(intent: VisualIntent, delta: IntentDelta) -> VisualIntent:
    """应用 CLEAR：清值 + 移除该路径的 ResolutionRecord（不写 delegated）。"""
    intent = _set_facet_value(intent, delta.path, None)
    resolutions = _copy_resolutions(intent)
    resolutions.pop(delta.path, None)
    return intent.model_copy(update={"resolutions": resolutions})


def _apply_pin(intent: VisualIntent, delta: IntentDelta) -> VisualIntent:
    """应用 PIN：只加保持标记，不改值。"""
    _resolve_field(intent, delta.path)
    return intent.model_copy(update={"pinned_paths": intent.pinned_paths | {delta.path}})


def _apply_unpin(intent: VisualIntent, delta: IntentDelta) -> VisualIntent:
    """应用 UNPIN：只移除保持标记，不改值、不写 Resolution。"""
    _resolve_field(intent, delta.path)
    return intent.model_copy(update={"pinned_paths": intent.pinned_paths - {delta.path}})


def _apply(intent: VisualIntent, delta: IntentDelta) -> VisualIntent:
    if delta.operation is DeltaOperation.SET:
        return _apply_set(intent, delta)
    if delta.operation is DeltaOperation.CLEAR:
        return _apply_clear(intent, delta)
    if delta.operation is DeltaOperation.PIN:
        return _apply_pin(intent, delta)
    if delta.operation is DeltaOperation.UNPIN:
        return _apply_unpin(intent, delta)
    raise ReducerError(
        "validation.operation_invalid",
        f"unsupported operation {delta.operation!r}",
    )


def _append_once(seen: list[str], path: str) -> None:
    if path not in seen:
        seen.append(path)


def reduce(current_intent: VisualIntent, validated_deltas: list[IntentDelta]) -> ReduceResult:
    """按顺序应用已验证 Delta，返回 `ReduceResult(intent, change_summary)`。

    `validated_deltas` 必须是 `validate(...)` 的 `accepted` 输出（或任何已通过
    同一验证的 Delta）；本函数不做二次校验，也不修改任何输入。
    """
    intent = current_intent
    changed_paths: list[str] = []
    pinned_paths: list[str] = []
    unpinned_paths: list[str] = []
    cleared_paths: list[str] = []

    for delta in validated_deltas:
        intent = _apply(intent, delta)
        if delta.operation is DeltaOperation.SET:
            _append_once(changed_paths, delta.path)
        elif delta.operation is DeltaOperation.CLEAR:
            _append_once(cleared_paths, delta.path)
        elif delta.operation is DeltaOperation.PIN:
            _append_once(pinned_paths, delta.path)
        elif delta.operation is DeltaOperation.UNPIN:
            _append_once(unpinned_paths, delta.path)

    invalidated = bool(changed_paths or cleared_paths or pinned_paths or unpinned_paths)
    return ReduceResult(
        intent=intent,
        change_summary=ChangeSummary(
            changed_paths=changed_paths,
            pinned_paths=pinned_paths,
            unpinned_paths=unpinned_paths,
            cleared_paths=cleared_paths,
            confirmation_invalidated=invalidated,
        ),
    )


__all__ = ["reduce", "ReducerError"]
