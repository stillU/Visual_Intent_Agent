"""Step 05 公开面：Interpreter 与 IntentEngine（ARCHITECTURE.md 4「Step 05」）。

    from visual_intent_agent.intent_engine import (
        IntentEngine, Interpreter, IntentResolveRequest, InterpreterResult,
    )

错误类型与解析 code 从模块路径导入（不扩大包根公开面）：

    from visual_intent_agent.intent_engine.models import (
        InterpreterError, InterpreterParseError,
        INTERPRETER_UNPARSEABLE_OUTPUT, INTERPRETER_FULL_INTENT, ...
    )

本包是"LLM 只能提出、确定性代码负责状态"的编排边界：LLM 只产生 Candidate
`IntentDelta`，随后依次经过 Validator → Reducer → DecisionPolicy。本包不做
持久化、不实现确认、不编译 Prompt、不调用图像模型。
"""

from __future__ import annotations

from .engine import IntentEngine
from .interpreter import Interpreter
from .models import InterpreterResult, IntentResolveRequest

__all__ = [
    "IntentEngine",
    "Interpreter",
    "IntentResolveRequest",
    "InterpreterResult",
]
