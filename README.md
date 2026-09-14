# Visual Intent Agent

Visual Intent Agent 是一个面向图像生成工作流的结构化意图系统。它把用户的自然语言需求转换为可验证、可确认、可追踪的 `VisualIntent`，再据此编译提示词、调用图像模型，并在多轮反馈中精确修改指定内容。

项目目标不是单纯生成更长的 Prompt，而是减少隐式决策、避免多轮修改中的属性漂移，让每次生成都能说明“用户要求了什么、系统代为决定了什么、最终生成依据是什么”。

## 核心架构

```text
用户输入
  → IntentEngine（理解意图并提出变更）
  → Validator / Reducer（校验并更新结构化状态）
  → DecisionPolicy（识别缺失、冲突与委托项）
  → Workflow（澄清与确认）
  → PromptEngine（从已确认意图编译 Prompt）
  → GenerationPipeline（生成并保存图片与 Artifact）
  → FeedbackEngine（将反馈转换为下一轮精确变更）
```

SQLite Repository 保存 Intent、确认记录、Prompt、生成结果和反馈的版本链。LLM 负责语言理解与表达，确定性代码负责状态变更、权限边界和流程约束。

## 项目原则

- 用户未明确表达的重要视觉内容，不由系统静默决定。
- LLM 只能提出候选变更，不能直接修改状态。
- 生成前必须确认当前 Intent，重要修改会使旧确认失效。
- 历史 Revision 与 Artifact 保持可追踪且不可覆盖。

## 当前状态

MVP v0.2 已完成从意图理解到多轮图片修改的核心闭环。MVP v0.3 进入“验证与可用化”阶段：先通过 Gate A 与 Direct LLM 基线进行对照评测，再根据结果做定向修正并提供最小可运行入口。

## 文档

- [架构设计](docs/ARCHITECTURE.md)
- [版本任务书](docs/task_books/README.md)
- [实施交接记录](docs/handoffs/)

项目使用 Python 3.12、Pydantic、SQLite、HTTPX 与 pytest。默认测试为全离线运行：`uv run pytest -q`。
