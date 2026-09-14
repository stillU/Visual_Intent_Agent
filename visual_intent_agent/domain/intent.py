"""Intent 领域合同：Resolution、七类 Facet、ResolutionRecord、VisualIntent。

冻结语义（ARCHITECTURE.md 4/5；README 不变量 3）：

- Resolution 必须绑定到具体路径（`VisualIntent.resolutions: dict[path, ResolutionRecord]`），
  而不是在 Intent 顶层放一个全局状态；
- missing = 该路径既没有 facet 值、也不在 `resolutions` 中留下记录；
- **missing ≠ user_delegated**：缺失绝不会被自动转换为系统授权。

字段面冻结：只允许任务书列出的七类 Facet 字段，不得增加服装、镜头型号、材质、
情绪等新顶层字段；更具体的表达放入现有 `description`。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from .constants import SCHEMA_VERSION
from .issue import EvidenceRef
from .paths import INTENT_PATHS

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class Resolution(str, Enum):
    """字段级 Resolution：合法值仅四个。"""

    USER_SPECIFIED = "user_specified"
    USER_CONFIRMED_PROPOSAL = "user_confirmed_proposal"
    USER_DELEGATED = "user_delegated"
    NOT_APPLICABLE = "not_applicable"


class ResolutionRecord(BaseModel):
    """Resolution 的路径级绑定：一个路径一条记录。"""

    model_config = _FROZEN

    resolution: Resolution
    evidence_refs: list[EvidenceRef] = []
    reason: str | None = None


class SubjectFacet(BaseModel):
    """subject 主体：description / count / pose_action。"""

    model_config = _FROZEN

    description: str | None = None
    count: int | None = Field(default=None, gt=0)
    pose_action: str | None = None


class CompositionFacet(BaseModel):
    """composition 构图：framing。"""

    model_config = _FROZEN

    framing: str | None = None


class EnvironmentFacet(BaseModel):
    """environment 环境：mode / location。"""

    model_config = _FROZEN

    mode: str | None = None
    location: str | None = None


class StyleFacet(BaseModel):
    """style 风格：primary / description。"""

    model_config = _FROZEN

    primary: str | None = None
    description: str | None = None


class LightingFacet(BaseModel):
    """lighting 光线：character。"""

    model_config = _FROZEN

    character: str | None = None


class CameraFacet(BaseModel):
    """camera 相机：angle / depth_of_field。"""

    model_config = _FROZEN

    angle: str | None = None
    depth_of_field: str | None = None


class ColorFacet(BaseModel):
    """color 色彩：palette。"""

    model_config = _FROZEN

    palette: str | None = None


class VisualIntent(BaseModel):
    """VisualIntent Schema v1（frozen + extra="forbid"）。

    `intent_id` 允许为空：由 Step 06 在首次持久化时赋值；domain 层不生成。
    `resolutions` 与 `pinned_paths` 的键必须 ⊆ `INTENT_PATHS`（model_validator 校验）。
    """

    model_config = _FROZEN

    schema_version: Literal["v1"] = SCHEMA_VERSION
    intent_id: str | None = None
    subject: SubjectFacet = SubjectFacet()
    composition: CompositionFacet = CompositionFacet()
    environment: EnvironmentFacet = EnvironmentFacet()
    style: StyleFacet = StyleFacet()
    lighting: LightingFacet = LightingFacet()
    camera: CameraFacet = CameraFacet()
    color: ColorFacet = ColorFacet()
    resolutions: dict[str, ResolutionRecord] = {}
    pinned_paths: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _paths_must_be_whitelisted(self) -> "VisualIntent":
        unknown_resolutions = sorted(set(self.resolutions) - INTENT_PATHS)
        if unknown_resolutions:
            raise ValueError(
                "resolutions contains paths outside the frozen whitelist: "
                f"{unknown_resolutions}"
            )
        unknown_pinned = sorted(self.pinned_paths - INTENT_PATHS)
        if unknown_pinned:
            raise ValueError(
                "pinned_paths contains paths outside the frozen whitelist: "
                f"{unknown_pinned}"
            )
        return self

    @field_serializer("pinned_paths", when_used="json")
    def _serialize_pinned_paths(self, value: frozenset[str]) -> list[str]:
        """按字典序输出 frozenset，保证 JSON 序列化跨进程稳定（frozenset 迭代序随
        PYTHONHASHSEED 变化；Step 06 的 summary hash 依赖逐字节稳定）。
        """
        return sorted(value)


__all__ = [
    "Resolution",
    "ResolutionRecord",
    "SubjectFacet",
    "CompositionFacet",
    "EnvironmentFacet",
    "StyleFacet",
    "LightingFacet",
    "CameraFacet",
    "ColorFacet",
    "VisualIntent",
]
