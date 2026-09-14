"""Step 03 DecisionPolicy v1：数据驱动规则表 + 确定性 evaluator。

核心问题（任务书「目标」）：

    当前还有哪些重要视觉决策必须由用户解决？

本模块只做确定性规则判断：不理解自然语言、不调用 LLM、不生成澄清文案、不接触
数据库或网络、不编译 Prompt。所有规则都是**代码内常量**（ARCHITECTURE.md 4：
不引入 YAML）。

    从 visual_intent_agent.policy import assess, DECISION_POLICIES, POLICY_VERSION

规则表
======

`DECISION_POLICIES` 恰好 9 条，对应任务书「第一版 Decision 清单」：

    Primary Subject / Style / Environment / Framing / Pose-Action /
    Lighting / Camera Angle / Color / Depth of Field

其中 Environment 跨两条白名单路径（`environment.mode` + `environment.location`），
通过 `DecisionPolicy.dependencies` 表达：成员路径 `(path, *dependencies)` 共享同一
`materiality` / `required_if` / `delegatable`，任一成员未解决则整个 Decision 未解决，
问题目标取按声明顺序第一个未解决的成员路径。

`required_if` 是**封闭枚举字符串集**（`REQUIRED_IF_CONDITIONS`），逐值语义：

    always                      条件恒成立（core 决策定义用户目标）
    never                       条件恒不成立（policy v1 不强制该维度）
    when_subject_specified      subject.description 有值
    when_environment_specified  environment.mode 或 environment.location 有值
    when_camera_specified       camera.angle 或 camera.depth_of_field 有值

策略结果
========

    未解决 + required_if 成立   -> block（阻塞确认）
    未解决 + required_if 不成立 -> omit（允许 unspecified，且不授权 PromptEngine）
    已解决                      -> 不进入 unresolved_decisions

Resolution 合法性（任务书「第一批规则原则」4/5）
================================================

    user_delegated  仅当该 path 所属 Decision 的 delegatable=True 时**由委托本身**
                    算已解决，否则 issue `policy.delegation_not_allowed`
                    （若该 path 已有具体值，则路径因值而仍算已解决）；
    not_applicable  仅当该 path 所属 Decision 的 required_if **不成立**时算已解决
                    （"不适用"不能用来掩盖遗漏，即使带着"理由值"也视为未解决），
                    否则 issue `policy.not_applicable_not_allowed`；
    user_specified / user_confirmed_proposal
                    要求该 path 确有值，否则 issue `policy.resolution_requires_value`。

未列为独立 Decision 的路径（`subject.count`、`style.description`）没有规则条目：
它们的普通取值随 Intent 原样保留、永不阻塞，但**不**具备委托/不适用授权
（没有 delegatable=True 的规则），因此其 `user_delegated` / `not_applicable`
记录会得到显式 issue（见 step_03_handoff.md「已知限制」）。

判定优先级（ARCHITECTURE.md 4 / 任务书「判定优先级」）
======================================================

    policy.hard_conflict > core missing > perceptual missing > policy.execution_conflict

每次 Resolution 最多产生**一个** `question`（MVP 默认单问题策略）。
`ready_for_confirmation` = 所有 `block` 项已解决 且 无 Hard Conflict；
Execution Conflict 只报告、不阻塞（任务书验收口径）。

确定性：纯函数、无随机数、无时钟、无环境变量、无 LLM；输入相同则输出逐字段相同
（含列表顺序稳定）。`assess` 绝不修改传入的 Intent。
"""

from __future__ import annotations

from typing import Callable

from visual_intent_agent.domain import (
    INTENT_PATHS,
    ExecutionRevision,
    Issue,
    Resolution,
    VisualIntent,
)

from .models import (
    DecisionPolicy,
    IntentResolution,
    Materiality,
    PolicyAction,
    QuestionSpec,
    UnresolvedDecision,
)

#: 规则表版本（写入交接记录与后续 Policy 评估口径）。
POLICY_VERSION: str = "policy.v1"


# ---------------------------------------------------------------------------
# required_if 封闭枚举条件集
# ---------------------------------------------------------------------------

REQUIRED_IF_ALWAYS: str = "always"
REQUIRED_IF_NEVER: str = "never"
REQUIRED_IF_SUBJECT_SPECIFIED: str = "when_subject_specified"
REQUIRED_IF_ENVIRONMENT_SPECIFIED: str = "when_environment_specified"
REQUIRED_IF_CAMERA_SPECIFIED: str = "when_camera_specified"

#: 全部合法 `required_if` 取值（封闭枚举；规则表在 import 时校验）。
REQUIRED_IF_CONDITIONS: frozenset[str] = frozenset(
    {
        REQUIRED_IF_ALWAYS,
        REQUIRED_IF_NEVER,
        REQUIRED_IF_SUBJECT_SPECIFIED,
        REQUIRED_IF_ENVIRONMENT_SPECIFIED,
        REQUIRED_IF_CAMERA_SPECIFIED,
    }
)


# ---------------------------------------------------------------------------
# Issue code 常量（命名空间 policy.*，ARCHITECTURE.md 5.5）
# ---------------------------------------------------------------------------

#: 路径所属 Decision 不可委托，却被标记 user_delegated。
DELEGATION_NOT_ALLOWED: str = "policy.delegation_not_allowed"
#: 路径所属 Decision 当前 required，却被标记 not_applicable（掩盖遗漏）。
NOT_APPLICABLE_NOT_ALLOWED: str = "policy.not_applicable_not_allowed"
#: user_specified / user_confirmed_proposal 要求该路径确有值。
RESOLUTION_REQUIRES_VALUE: str = "policy.resolution_requires_value"

#: Hard Conflict 的 code 前缀（`policy.hard_conflict.<rule_id>`）。
HARD_CONFLICT_PREFIX: str = "policy.hard_conflict"
#: Execution Conflict 的 code 前缀（`policy.execution_conflict.<rule_id>`）。
EXECUTION_CONFLICT_PREFIX: str = "policy.execution_conflict"


# ---------------------------------------------------------------------------
# 冲突规则：稳定 rule id -> 确定性谓词
# ---------------------------------------------------------------------------
#
# 每条规则有稳定 rule id，通过 `DecisionPolicy.conflict_rules` 声明在相关
# Decision 上；evaluator 按规则表顺序去重求值。冲突只显式输出，绝不覆盖字段。

_ENCLOSED_ENVIRONMENT_MODES: frozenset[str] = frozenset(
    {"studio", "indoor", "indoors", "interior"}
)
_OPEN_AIR_LOCATION_TERMS: frozenset[str] = frozenset(
    {
        "outdoor",
        "outdoors",
        "street",
        "forest",
        "beach",
        "mountain",
        "field",
        "garden",
        "desert",
        "ocean",
        "sea",
        "sky",
        "park",
        "rooftop",
    }
)
_NATURAL_LIGHT_TERMS: frozenset[str] = frozenset(
    {"natural light", "natural lighting", "sunlight", "daylight", "golden hour"}
)
_PHOTO_STYLE_TERMS: frozenset[str] = frozenset(
    {"photorealistic", "photorealism", "photograph", "photography", "hyperrealistic"}
)
_NON_PHOTO_STYLE_TERMS: frozenset[str] = frozenset(
    {
        "anime",
        "cartoon",
        "oil painting",
        "watercolor",
        "watercolour",
        "sketch",
        "pencil drawing",
        "3d render",
        "pixel art",
        "comic",
    }
)
_WIDE_FRAMING_TERMS: frozenset[str] = frozenset(
    {
        "wide",
        "wide shot",
        "wide angle",
        "panorama",
        "panoramic",
        "landscape",
        "establishing shot",
        "long shot",
        "full shot",
    }
)

#: 宽幅构图要求的最小宽高比（输出尺寸 `<w>x<h>`，比例由此派生）。
_WIDE_FRAMING_MIN_ASPECT: float = 1.5


def _words(text: str | None) -> list[str]:
    """规范化自由文本：小写、`_`/`-` 视为分隔符、压缩空白。"""
    if text is None:
        return []
    normalized = text.strip().lower().replace("_", " ").replace("-", " ")
    return normalized.split()


def _contains_phrase(words: list[str], phrase: str) -> bool:
    """短语按"连续完整单词序列"匹配，避免 `photo` 误命中 `photograph` 之外的内容。"""
    target = phrase.split()
    width = len(target)
    if width == 0 or width > len(words):
        return False
    return any(words[index : index + width] == target for index in range(len(words) - width + 1))


def _contains_any(text: str | None, vocabulary: frozenset[str]) -> bool:
    """文本是否命中词表（词表按排序遍历，仅返回 bool，结果与顺序无关）。"""
    words = _words(text)
    return any(_contains_phrase(words, phrase) for phrase in sorted(vocabulary))


def _read_path(intent: VisualIntent, path: str) -> object | None:
    """按白名单路径只读读取 Intent 的当前值（不改状态）。"""
    facet_name, _, field_name = path.partition(".")
    facet = getattr(intent, facet_name, None)
    if facet is None:
        return None
    return getattr(facet, field_name, None)


def _aspect_ratio(output_size: str) -> float | None:
    """从 `<w>x<h>` 解析宽高比；不可解析返回 None（格式校验属 Step 08）。"""
    width_text, separator, height_text = output_size.partition("x")
    if not separator:
        return None
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _environment_mode_location_conflict(
    intent: VisualIntent, execution_context: ExecutionRevision | None
) -> bool:
    """封闭/室内环境模式与明确户外地点互相矛盾。"""
    mode = _read_path(intent, "environment.mode")
    location = _read_path(intent, "environment.location")
    if not isinstance(mode, str) or not isinstance(location, str):
        return False
    return _contains_any(mode, _ENCLOSED_ENVIRONMENT_MODES) and _contains_any(
        location, _OPEN_AIR_LOCATION_TERMS
    )


def _lighting_environment_source_conflict(
    intent: VisualIntent, execution_context: ExecutionRevision | None
) -> bool:
    """自然光要求与封闭/室内环境模式互相矛盾。"""
    mode = _read_path(intent, "environment.mode")
    lighting = _read_path(intent, "lighting.character")
    if not isinstance(mode, str) or not isinstance(lighting, str):
        return False
    return _contains_any(mode, _ENCLOSED_ENVIRONMENT_MODES) and _contains_any(
        lighting, _NATURAL_LIGHT_TERMS
    )


def _style_medium_mismatch_conflict(
    intent: VisualIntent, execution_context: ExecutionRevision | None
) -> bool:
    """风格主值（写实摄影）与风格补充说明（非摄影媒介）互相矛盾。"""
    primary = _read_path(intent, "style.primary")
    description = _read_path(intent, "style.description")
    if not isinstance(primary, str) or not isinstance(description, str):
        return False
    return _contains_any(primary, _PHOTO_STYLE_TERMS) and _contains_any(
        description, _NON_PHOTO_STYLE_TERMS
    )


def _framing_aspect_mismatch_conflict(
    intent: VisualIntent, execution_context: ExecutionRevision | None
) -> bool:
    """宽幅构图要求与执行侧输出比例（<w>x<h>）互相矛盾。"""
    if execution_context is None:
        return False
    framing = _read_path(intent, "composition.framing")
    if not isinstance(framing, str) or not _contains_any(framing, _WIDE_FRAMING_TERMS):
        return False
    aspect = _aspect_ratio(execution_context.output_size)
    if aspect is None:
        return False
    return aspect < _WIDE_FRAMING_MIN_ASPECT


class _ConflictRule:
    """内部冲突规则条目：rule id + 类别 + 涉及路径 + 确定性谓词。"""

    __slots__ = ("rule_id", "kind", "path", "message", "predicate")

    def __init__(
        self,
        rule_id: str,
        kind: str,
        path: str,
        message: str,
        predicate: Callable[[VisualIntent, ExecutionRevision | None], bool],
    ) -> None:
        self.rule_id = rule_id
        self.kind = kind
        self.path = path
        self.message = message
        self.predicate = predicate


#: 稳定 rule id -> 冲突规则。`kind` 决定 code 前缀（hard / execution）。
CONFLICT_RULES: dict[str, _ConflictRule] = {
    "hard_conflict.environment_mode_location": _ConflictRule(
        rule_id="hard_conflict.environment_mode_location",
        kind="hard",
        path="environment.mode",
        message=(
            "environment.mode declares an enclosed/indoor setting while "
            "environment.location names an open-air place"
        ),
        predicate=_environment_mode_location_conflict,
    ),
    "hard_conflict.lighting_environment_source": _ConflictRule(
        rule_id="hard_conflict.lighting_environment_source",
        kind="hard",
        path="lighting.character",
        message=(
            "lighting.character requires natural light while environment.mode "
            "declares an enclosed/indoor setting"
        ),
        predicate=_lighting_environment_source_conflict,
    ),
    "hard_conflict.style_medium_mismatch": _ConflictRule(
        rule_id="hard_conflict.style_medium_mismatch",
        kind="hard",
        path="style.primary",
        message=(
            "style.primary specifies photographic realism while style.description "
            "specifies a non-photographic medium"
        ),
        predicate=_style_medium_mismatch_conflict,
    ),
    "execution_conflict.framing_aspect_mismatch": _ConflictRule(
        rule_id="execution_conflict.framing_aspect_mismatch",
        kind="execution",
        path="composition.framing",
        message=(
            "composition.framing requires a wide composition but the execution "
            f"output aspect ratio is below {_WIDE_FRAMING_MIN_ASPECT}"
        ),
        predicate=_framing_aspect_mismatch_conflict,
    ),
}


# ---------------------------------------------------------------------------
# DecisionPolicy v1 规则表（恰好 9 条，数据驱动）
# ---------------------------------------------------------------------------

DECISION_POLICIES: tuple[DecisionPolicy, ...] = (
    DecisionPolicy(
        path="subject.description",
        materiality=Materiality.CORE,
        required_if=REQUIRED_IF_ALWAYS,
        delegatable=False,
        dependencies=[],
        conflict_rules=[],
        invalidates_realization=True,
    ),
    DecisionPolicy(
        path="style.primary",
        materiality=Materiality.CORE,
        required_if=REQUIRED_IF_ALWAYS,
        delegatable=True,
        dependencies=[],
        conflict_rules=["hard_conflict.style_medium_mismatch"],
        invalidates_realization=True,
    ),
    DecisionPolicy(
        path="environment.mode",
        materiality=Materiality.CORE,
        required_if=REQUIRED_IF_ALWAYS,
        delegatable=True,
        dependencies=["environment.location"],
        conflict_rules=[
            "hard_conflict.environment_mode_location",
            "hard_conflict.lighting_environment_source",
        ],
        invalidates_realization=True,
    ),
    DecisionPolicy(
        path="composition.framing",
        materiality=Materiality.PERCEPTUAL,
        required_if=REQUIRED_IF_SUBJECT_SPECIFIED,
        delegatable=True,
        dependencies=[],
        conflict_rules=["execution_conflict.framing_aspect_mismatch"],
        invalidates_realization=True,
    ),
    DecisionPolicy(
        path="subject.pose_action",
        materiality=Materiality.PERCEPTUAL,
        required_if=REQUIRED_IF_SUBJECT_SPECIFIED,
        delegatable=True,
        dependencies=[],
        conflict_rules=[],
        invalidates_realization=True,
    ),
    DecisionPolicy(
        path="lighting.character",
        materiality=Materiality.PERCEPTUAL,
        required_if=REQUIRED_IF_ENVIRONMENT_SPECIFIED,
        delegatable=True,
        dependencies=[],
        conflict_rules=["hard_conflict.lighting_environment_source"],
        invalidates_realization=True,
    ),
    DecisionPolicy(
        path="camera.angle",
        materiality=Materiality.PERCEPTUAL,
        required_if=REQUIRED_IF_CAMERA_SPECIFIED,
        delegatable=True,
        dependencies=[],
        conflict_rules=[],
        invalidates_realization=True,
    ),
    DecisionPolicy(
        path="color.palette",
        materiality=Materiality.PERCEPTUAL,
        required_if=REQUIRED_IF_NEVER,
        delegatable=True,
        dependencies=[],
        conflict_rules=[],
        invalidates_realization=True,
    ),
    DecisionPolicy(
        path="camera.depth_of_field",
        materiality=Materiality.PERCEPTUAL,
        required_if=REQUIRED_IF_CAMERA_SPECIFIED,
        delegatable=True,
        dependencies=[],
        conflict_rules=[],
        invalidates_realization=True,
    ),
)


#: 建议选项（QuestionSpec.suggested_values 的唯一来源；QuestionBuilder 原样使用）。
#: 这只是各决策维度的一组通用选项 token，不是知识库、不是澄清文案：
#: `subject.description` 是开放式输入，故意不给预设选项。
_SUGGESTED_VALUES: dict[str, tuple[str, ...]] = {
    "subject.description": (),
    "style.primary": ("photorealistic", "cinematic", "illustration"),
    "environment.mode": ("studio", "indoor", "outdoor"),
    "environment.location": (),
    "composition.framing": ("close_up", "medium_shot", "wide_shot"),
    "subject.pose_action": ("standing", "sitting", "walking"),
    "lighting.character": ("soft", "dramatic", "natural"),
    "camera.angle": ("eye_level", "low_angle", "high_angle"),
    "color.palette": ("warm", "cool", "monochrome"),
    "camera.depth_of_field": ("shallow", "deep"),
}


def _resolve_member_owner() -> dict[str, DecisionPolicy]:
    """成员路径 -> 所属 DecisionPolicy；import 时校验规则表封闭且无歧义。"""
    if len(DECISION_POLICIES) != 9:
        raise ValueError(
            "DECISION_POLICIES must contain exactly 9 decisions; "
            f"got {len(DECISION_POLICIES)}"
        )
    owner: dict[str, DecisionPolicy] = {}
    for policy in DECISION_POLICIES:
        if policy.path not in INTENT_PATHS:
            raise ValueError(f"decision path {policy.path!r} is not in INTENT_PATHS")
        if policy.required_if not in REQUIRED_IF_CONDITIONS:
            raise ValueError(
                f"required_if {policy.required_if!r} for {policy.path!r} is not in the "
                f"closed condition set {sorted(REQUIRED_IF_CONDITIONS)}"
            )
        for rule_id in policy.conflict_rules:
            if rule_id not in CONFLICT_RULES:
                raise ValueError(
                    f"unknown conflict rule {rule_id!r} declared on {policy.path!r}"
                )
        for member in (policy.path, *policy.dependencies):
            if member not in INTENT_PATHS:
                raise ValueError(f"decision member {member!r} is not in INTENT_PATHS")
            if member in owner:
                raise ValueError(
                    f"path {member!r} is covered by two decisions "
                    f"({owner[member].path!r} and {policy.path!r})"
                )
            owner[member] = policy
    return owner


#: 成员路径 -> DecisionPolicy（唯一覆盖；未列出的白名单路径为补充信息）。
_DECISION_BY_MEMBER_PATH: dict[str, DecisionPolicy] = _resolve_member_owner()


# ---------------------------------------------------------------------------
# applicable / required_if 计算
# ---------------------------------------------------------------------------


def required_if_holds(condition: str, intent: VisualIntent) -> bool:
    """确定性求值一个封闭条件名；未知条件名是程序级错误（import 时已拦）。"""
    if condition == REQUIRED_IF_ALWAYS:
        return True
    if condition == REQUIRED_IF_NEVER:
        return False
    if condition == REQUIRED_IF_SUBJECT_SPECIFIED:
        return _read_path(intent, "subject.description") is not None
    if condition == REQUIRED_IF_ENVIRONMENT_SPECIFIED:
        return (
            _read_path(intent, "environment.mode") is not None
            or _read_path(intent, "environment.location") is not None
        )
    if condition == REQUIRED_IF_CAMERA_SPECIFIED:
        return (
            _read_path(intent, "camera.angle") is not None
            or _read_path(intent, "camera.depth_of_field") is not None
        )
    raise ValueError(f"unknown required_if condition {condition!r}")


# ---------------------------------------------------------------------------
# Resolution 合法性检查
# ---------------------------------------------------------------------------


def _path_resolution_status(
    intent: VisualIntent, path: str, decision: DecisionPolicy | None
) -> tuple[bool, str | None]:
    """返回 `(该路径是否已解决, 不合法时的 issue code)`。

    路径语义（domain 冻结）：missing = 无值且 resolutions 无记录；missing ≠
    user_delegated。Resolution 的合法性由所属 Decision 的规则验证；没有规则条目的
    补充路径没有 delegatable/not_applicable 授权，委托或不适用都会得到 issue。
    """
    value_present = _read_path(intent, path) is not None
    record = intent.resolutions.get(path)
    if record is None:
        return (value_present, None)

    resolution = record.resolution
    if resolution is Resolution.USER_DELEGATED:
        if decision is not None and decision.delegatable:
            return (True, None)
        # 委托不合法：显式 issue。已有具体值的路径仍算已解决（值本身即规范），
        # 但没有值时不因一次非法委托而被视为已解决。
        return (value_present, DELEGATION_NOT_ALLOWED)
    if resolution is Resolution.NOT_APPLICABLE:
        if decision is None or required_if_holds(decision.required_if, intent):
            # 规则条件不成立时的 not_applicable 会掩盖遗漏：即使带着"理由值"，
            # 该 Decision 仍视为未解决（"不适用"不能替代缺失的重要决策）。
            return (False, NOT_APPLICABLE_NOT_ALLOWED)
        return (True, None)
    # USER_SPECIFIED / USER_CONFIRMED_PROPOSAL：必须有具体值支撑
    if not value_present:
        return (False, RESOLUTION_REQUIRES_VALUE)
    return (True, None)


def _resolution_issues(intent: VisualIntent) -> list[Issue]:
    """对 `intent.resolutions` 中**每一条**记录做规则验证（按路径排序，确定性）。

    未列为独立 Decision 的路径同样被验证：没有规则条目 => 不具备委托/不适用授权。
    """
    issues: list[Issue] = []
    for path in sorted(intent.resolutions):
        resolution = intent.resolutions[path].resolution
        decision = _DECISION_BY_MEMBER_PATH.get(path)
        _, code = _path_resolution_status(intent, path, decision)
        if code is None:
            continue
        if code == DELEGATION_NOT_ALLOWED:
            message = (
                f"{path!r} does not declare delegatable=True; "
                "user_delegated is not a valid resolution for it"
            )
        elif code == NOT_APPLICABLE_NOT_ALLOWED:
            message = (
                f"{path!r} is required by rule "
                f"required_if={decision.required_if!r}; not_applicable would mask a "
                "missing important decision"
            ) if decision is not None else (
                f"{path!r} has no decision rule, so not_applicable has no rule "
                "condition to satisfy"
            )
        else:  # RESOLUTION_REQUIRES_VALUE
            message = (
                f"resolution {resolution.value!r} for {path!r} requires an explicit "
                "value; none is present"
            )
        issues.append(Issue(code=code, message=message, path=path))
    return issues


# ---------------------------------------------------------------------------
# 缺失决策收集
# ---------------------------------------------------------------------------


def _unresolved_reason(
    policy: DecisionPolicy,
    target: str,
    required: bool,
    code: str | None,
    resolution: Resolution | None,
) -> str:
    """确定性原因文本（包含 path、Decision、规则条件与具体原因）。"""
    action_text = "blocking" if required else "omittable"
    if code is not None and resolution is not None:
        detail = (
            f"resolution {resolution.value!r} is not valid for {target!r} ({code})"
        )
    else:
        detail = f"{target!r} has no value and no resolution record"
    return (
        f"{action_text} decision {policy.path!r} ({policy.materiality.value}) is "
        f"unresolved: {detail}; rule required_if={policy.required_if!r}"
    )


def _collect_unresolved_decisions(
    intent: VisualIntent,
) -> list[UnresolvedDecision]:
    """按规则表顺序收集未解决的 Decision（含 `omit`），确定性且稳定。"""
    unresolved: list[UnresolvedDecision] = []
    for policy in DECISION_POLICIES:
        required = required_if_holds(policy.required_if, intent)
        action = PolicyAction.BLOCK if required else PolicyAction.OMIT
        for member in (policy.path, *policy.dependencies):
            resolved, code = _path_resolution_status(intent, member, policy)
            if resolved:
                continue
            resolution = (
                intent.resolutions[member].resolution
                if member in intent.resolutions
                else None
            )
            unresolved.append(
                UnresolvedDecision(
                    path=member,
                    materiality=policy.materiality,
                    action=action,
                    reason=_unresolved_reason(
                        policy, member, required, code, resolution
                    ),
                )
            )
            break  # 每个 Decision 只报第一条未解决的成员路径（供单问题策略使用）
    return unresolved


# ---------------------------------------------------------------------------
# 冲突检测
# ---------------------------------------------------------------------------


def _detect_conflicts(
    intent: VisualIntent, execution_context: ExecutionRevision | None
) -> list[Issue]:
    """按规则表顺序、每个 rule id 只求值一次，输出全部显式冲突（不覆盖字段）。"""
    conflicts: list[Issue] = []
    evaluated: set[str] = set()
    for policy in DECISION_POLICIES:
        for rule_id in policy.conflict_rules:
            if rule_id in evaluated:
                continue
            evaluated.add(rule_id)
            rule = CONFLICT_RULES[rule_id]
            if rule.predicate(intent, execution_context):
                prefix = (
                    HARD_CONFLICT_PREFIX
                    if rule.kind == "hard"
                    else EXECUTION_CONFLICT_PREFIX
                )
                conflicts.append(
                    Issue(
                        code=f"{prefix}.{rule_id.split('.', 1)[1]}",
                        message=rule.message,
                        path=rule.path,
                    )
                )
    return conflicts


def is_hard_conflict(issue: Issue) -> bool:
    """Hard Conflict 判定（code 前缀 `policy.hard_conflict`）。"""
    return issue.code == HARD_CONFLICT_PREFIX or issue.code.startswith(
        f"{HARD_CONFLICT_PREFIX}."
    )


def is_execution_conflict(issue: Issue) -> bool:
    """Execution Conflict 判定（code 前缀 `policy.execution_conflict`）。"""
    return issue.code == EXECUTION_CONFLICT_PREFIX or issue.code.startswith(
        f"{EXECUTION_CONFLICT_PREFIX}."
    )


# ---------------------------------------------------------------------------
# 下一待询问目标（优先级：hard conflict > core missing > perceptual missing >
# execution conflict；每轮最多一个）
# ---------------------------------------------------------------------------


def _question_for_decision(decision: UnresolvedDecision) -> QuestionSpec:
    """由缺失决策生成 QuestionSpec（question_text / question_id 留空给 Step 06）。"""
    policy = _DECISION_BY_MEMBER_PATH[decision.path]
    return QuestionSpec(
        target_path=decision.path,
        reason=decision.reason,
        allow_delegate=policy.delegatable,
        allow_custom=True,
        suggested_values=_SUGGESTED_VALUES.get(decision.path, ()),
        question_text=None,
        question_id=None,
    )


def _question_for_conflict(issue: Issue) -> QuestionSpec:
    """由冲突生成 QuestionSpec；冲突没有预设选项。"""
    policy = _DECISION_BY_MEMBER_PATH.get(issue.path)
    return QuestionSpec(
        target_path=issue.path if issue.path is not None else "",
        reason=issue.message,
        allow_delegate=policy.delegatable if policy is not None else False,
        allow_custom=True,
        suggested_values=(),
        question_text=None,
        question_id=None,
    )


def _select_question(
    unresolved: list[UnresolvedDecision],
    hard_conflicts: list[Issue],
    execution_conflicts: list[Issue],
) -> QuestionSpec | None:
    """按冻结优先级选出唯一的待询问目标；无任何目标返回 None。"""
    if hard_conflicts:
        return _question_for_conflict(hard_conflicts[0])

    for materiality in (Materiality.CORE, Materiality.PERCEPTUAL):
        for decision in unresolved:
            if decision.action is PolicyAction.BLOCK and decision.materiality is materiality:
                return _question_for_decision(decision)

    # 兜底：任何仍阻塞的非 core/perceptual 决策（policy v1 无此条目）。
    for decision in unresolved:
        if decision.action is PolicyAction.BLOCK:
            return _question_for_decision(decision)

    if execution_conflicts:
        return _question_for_conflict(execution_conflicts[0])
    return None


# ---------------------------------------------------------------------------
# assess
# ---------------------------------------------------------------------------


def assess(
    intent: VisualIntent,
    execution_context: ExecutionRevision | None = None,
) -> IntentResolution:
    """评估当前 Intent，回答"还有哪些重要视觉决策必须由用户解决"。

    纯确定性函数（无 LLM / 随机数 / 时钟 / IO）；不修改 `intent`。

    返回的 `IntentResolution`：

    - `applied_deltas` 恒为 `[]`（由 Step 05 IntentEngine 回填）；
    - `issues`：Resolution 合法性检查结果（`policy.*`）；
    - `unresolved_decisions`：全部未解决 Decision，`action` 为 `block` / `omit`；
    - `conflicts`：显式冲突（hard + execution），绝不覆盖字段；
    - `question`：最高优先级的唯一待询问目标（ready 时为 None）；
    - `ready_for_confirmation`：无 `block` 项且无 Hard Conflict。
    """
    issues = _resolution_issues(intent)
    unresolved = _collect_unresolved_decisions(intent)
    conflicts = _detect_conflicts(intent, execution_context)

    hard_conflicts = [issue for issue in conflicts if is_hard_conflict(issue)]
    execution_conflicts = [issue for issue in conflicts if is_execution_conflict(issue)]

    blocking = any(
        decision.action is PolicyAction.BLOCK for decision in unresolved
    )
    ready = (not blocking) and (not hard_conflicts)
    question = None if ready else _select_question(unresolved, hard_conflicts, execution_conflicts)

    return IntentResolution(
        intent=intent,
        applied_deltas=[],
        issues=issues,
        unresolved_decisions=unresolved,
        conflicts=conflicts,
        question=question,
        ready_for_confirmation=ready,
    )


__all__ = [
    "POLICY_VERSION",
    "DECISION_POLICIES",
    "REQUIRED_IF_CONDITIONS",
    "REQUIRED_IF_ALWAYS",
    "REQUIRED_IF_NEVER",
    "REQUIRED_IF_SUBJECT_SPECIFIED",
    "REQUIRED_IF_ENVIRONMENT_SPECIFIED",
    "REQUIRED_IF_CAMERA_SPECIFIED",
    "CONFLICT_RULES",
    "DELEGATION_NOT_ALLOWED",
    "NOT_APPLICABLE_NOT_ALLOWED",
    "RESOLUTION_REQUIRES_VALUE",
    "HARD_CONFLICT_PREFIX",
    "EXECUTION_CONFLICT_PREFIX",
    "assess",
    "required_if_holds",
    "is_hard_conflict",
    "is_execution_conflict",
]
