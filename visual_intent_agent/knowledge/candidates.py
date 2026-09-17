"""v0.4 知识库的路径白名单与候选值授权（单一定义）。

本模块只做两件事：

1. 冻结本版知识库**严格限定**辅助的三条用户可委托路径
   （`lighting.character` / `composition.framing` / `camera.depth_of_field`）；
2. 把知识单元的候选值校验**委托**给 `prompt_engine.engine.DELEGATED_CANDIDATES`
   —— 全系统唯一的候选值权威表。

依赖边界（重要）：

- 候选值表**不复制**。本模块只保留一个函数级惰性 import
  （`delegated_candidates()`）。Step 03 会让 `prompt_engine` 依赖 `knowledge`；
  若本模块在顶层 `import prompt_engine.engine`，就会形成循环导入。
- 候选校验发生在**调用时**（pydantic validator / loader），此时
  `prompt_engine.engine` 必然已完全加载，读取到的是同一份映射对象，
  因此不存在可能漂移的第二份候选表。
"""

from __future__ import annotations

from typing import Mapping

from visual_intent_agent.domain.paths import INTENT_PATHS

#: 本版知识库严格限定辅助的三条路径；其余九条路径的知识单元一律拒绝。
KNOWLEDGE_PATHS: frozenset[str] = frozenset(
    {
        "lighting.character",
        "composition.framing",
        "camera.depth_of_field",
    }
)

if not KNOWLEDGE_PATHS <= INTENT_PATHS or len(KNOWLEDGE_PATHS) != 3:
    raise ValueError("KNOWLEDGE_PATHS must be exactly three whitelisted INTENT_PATHS")


def delegated_candidates() -> Mapping[str, tuple[str, ...]]:
    """返回 PromptEngine 的候选值表（惰性读取，永远是最新同一对象）。

    **必须在函数内 import**：见模块 docstring 的依赖边界说明。
    """
    from visual_intent_agent.prompt_engine.engine import DELEGATED_CANDIDATES

    return DELEGATED_CANDIDATES


def candidate_values_for(path: str) -> tuple[str, ...]:
    """返回某条知识路径的**受授权**候选值（无条目时返回空元组）。"""
    return tuple(delegated_candidates().get(path, ()))


def is_authorized_candidate(path: str, value: str) -> bool:
    """候选值是否属于该路径当前受授权的候选集合（精确匹配，不猜测）。"""
    return value in candidate_values_for(path)


__all__ = [
    "KNOWLEDGE_PATHS",
    "delegated_candidates",
    "candidate_values_for",
    "is_authorized_candidate",
]