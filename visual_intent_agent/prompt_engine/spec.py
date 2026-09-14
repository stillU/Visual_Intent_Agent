"""CompilationSpec：Prompt 编译的**轻量内部结构**（ARCHITECTURE.md 4「Step 07」）。

冻结约束（README 不变量 9 / 任务书「禁止范围」）：

- **不导出**（`prompt_engine/__init__.py` 不列出本模块任何名字）；
- **不是 Prompt AST**：没有节点层级、没有权重、没有条件分支，只是
  "目标模型 + 参数 + 一串已解析 clause"；clause 顺序即渲染顺序；
- 下游（Renderer）只读它，绝不允许它承载 Intent 全量对象或 Provider 调用细节。

`CompilationClause` 的 `source_kind` 故意是**自由字符串**（而不是 `Literal`）：
只有这样才能把"人为插入/被篡改的 clause"表达出来，交给
`engine.check_source_bindings` 判定为 `prompt.unauthorized_addition` 或
`prompt.missing_source_binding`（编译失败，而不是 pydantic 构造期报错）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .models import PromptParameters

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class CompilationClause(BaseModel):
    """一个待渲染的视觉 clause 及其候选来源字段（内部结构）。

    - `text`：已由确定性代码写定的 clause 文本（wording 属 implementation 自由度）；
    - `intent_path`：值来自/受委托于的 Intent 白名单路径；
    - `source_kind`：`intent` / `delegation` / `realization` / `runtime`（或 None = 无来源）；
    - `rule_id`：`runtime` 类别的稳定规则 id；
    - `realization_id`：`delegation` / `realization` 类别引用的 RealizationState。
    """

    model_config = _FROZEN

    clause_id: str
    text: str
    intent_path: str | None = None
    source_kind: str | None = None
    rule_id: str | None = None
    realization_id: str | None = None


class CompilationSpec(BaseModel):
    """一次编译的全部输入：目标模型、参数、按渲染顺序排列的 clause。"""

    model_config = _FROZEN

    session_id: str
    target_model: str
    parameters: PromptParameters = Field(default_factory=PromptParameters)
    clauses: list[CompilationClause] = Field(default_factory=list)


__all__ = ["CompilationClause", "CompilationSpec"]
