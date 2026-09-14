"""唯一目标图像模型 Renderer（ARCHITECTURE.md 4「Step 07」；任务书「授权边界」）。

    ModelRenderer (Protocol)
      └ QwenImageRenderer      ← 唯一目标模型实现，不得出现第二个

本模块只做**表达层**工作：把 `CompilationSpec` 里已经解析、已经绑定来源的 clause
组织成目标模型 Prompt 文本（顺序 / 分隔符 / 模型语法属 implementation 自由度）。

严格边界：

- Renderer **绝不**新增 clause，也绝不添加任何未经用户确认或委托的
  token（例如通用的 "masterpiece, best quality, 8k" 之类"最佳实践"修饰词）——
  那会构成 `prompt.unauthorized_addition`，也违反"不根据模型最佳实践覆盖用户选择"；
- Renderer 不读数据库、不调用 LLM / Provider、不触网、不接触 Intent 对象；
- `RENDERER_VERSION` 是 Prompt 组织方式的版本号（写入 handoff，供 Step 08/10 记录）。

`supports` 只接受冻结的唯一目标模型名与可解析的 `"<w>x<h>"` 尺寸；其余取值一律
由 PromptEngine 转为 `prompt.unsupported_requirement`（不静默降级）。
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from .spec import CompilationSpec

#: 本 Renderer 唯一支持的目标图像模型（与 `Settings.image_model` 默认值一致）。
QWEN_IMAGE_MODEL = "qwen-image-3.0"

#: Prompt 组织方式版本（不是模型版本）。
RENDERER_VERSION = "qwen_image.v1"

#: 冻结尺寸语法 `"<width>x<height>"`（正十进制整数；格式校验的消费方是 Step 08）。
_SIZE_PATTERN = re.compile(r"^([0-9]+)x([0-9]+)$")


def parse_output_size(size: str) -> tuple[int, int] | None:
    """解析 `"<w>x<h>"`；不可解析或非正数返回 None（确定性、不抛异常）。"""
    if not isinstance(size, str):
        return None
    match = _SIZE_PATTERN.match(size.strip())
    if match is None:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    return (width, height)


@runtime_checkable
class ModelRenderer(Protocol):
    """目标模型 Renderer 合同（唯一实现见本模块）。

    `runtime_checkable` 仅用于合同测试（`isinstance(x, ModelRenderer)`），不改变调用面。
    """

    @property
    def target_model(self) -> str:
        """本 Renderer 支持的目标模型名（唯一值）。"""
        ...

    @property
    def renderer_version(self) -> str:
        """Prompt 组织方式的版本号。"""
        ...

    def supports(self, target_model: str, size: str) -> bool:
        """是否支持该目标模型与输出尺寸。"""
        ...

    def render(self, spec: CompilationSpec) -> str:
        """把 spec 的 clause 组织为目标模型 Prompt 文本（不新增内容）。"""
        ...


class QwenImageRenderer:
    """Qwen-Image 目标模型的唯一 Renderer。

    文本组织：按 `spec.clauses` 的既有顺序，用 `", "` 连接各 clause 的 `text`；
    没有前缀、没有后缀、没有质量/风格修饰词，因此渲染结果可被
    `source_bindings` 逐条覆盖（重要 clause 来源覆盖率 100%）。
    """

    @property
    def target_model(self) -> str:
        return QWEN_IMAGE_MODEL

    @property
    def renderer_version(self) -> str:
        return RENDERER_VERSION

    def supports(self, target_model: str, size: str) -> bool:
        return target_model == QWEN_IMAGE_MODEL and parse_output_size(size) is not None

    def render(self, spec: CompilationSpec) -> str:
        if not isinstance(spec, CompilationSpec):
            raise TypeError("QwenImageRenderer.render expects a CompilationSpec")
        return ", ".join(clause.text for clause in spec.clauses)


__all__ = [
    "ModelRenderer",
    "QwenImageRenderer",
    "QWEN_IMAGE_MODEL",
    "RENDERER_VERSION",
    "parse_output_size",
]
