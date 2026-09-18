# Visual Intent Agent

一个面向图像生成的结构化意图系统：把自然语言需求转成可确认、可追溯的视觉意图，再编译提示词、生成图片，并通过多轮反馈精确修改指定内容。

## 项目目标

项目关注的不只是“生成更长的提示词”，而是让每一次生成都有清晰依据：

- **需求可确认**：不清楚的内容先澄清，重要变更需重新确认。
- **修改有边界**：保留用户明确要求和锁定项（PIN），减少多轮修改中的属性漂移。
- **结果可追溯**：区分用户要求、系统受委托的选择与最终生成依据。

## 项目架构

![Visual Intent Agent 项目架构](docs/diagrams/architecture.svg)

系统采用模块化单体架构。LLM 负责语言理解和候选变更，确定性代码负责校验、状态更新、确认门禁与提示词编译。

| 层次 | 职责 |
|---|---|
| 意图与工作流 | IntentEngine、Validator / Reducer、DecisionPolicy、WorkflowService 共同完成理解、校验、澄清与显式确认 |
| 编译与生成 | PromptEngine 将已确认意图编译为提示词；GenerationPipeline 通过图片 Provider 生成并保存产物 |
| 反馈修订 | FeedbackEngine 与 ReviewService 将反馈转为新变更，重新进入澄清和确认流程 |
| 业务持久化 | SQLite Repository 保存会话、意图版本、确认、提示词、生成结果与知识审计；图片文件保存在本地 |
| 可选知识辅助 | 本地审核 JSONL 与词法检索，为仍被用户委托的光照、构图和景深提供候选建议 |

**知识库不替代业务数据库，也不替用户作主。** RAG 默认关闭；启用后仍不能覆盖明确值、PIN 或绕过确认。图中按职责聚合模块，数据库连线为示意，编译与生成同样通过 Repository 保存产物。

完整交互为：提出需求 → 澄清 → 确认 → 生成 → 接受或反馈修改。历史记录保留版本关系，重要修改会使旧确认失效。

## 实现边界

目前提供本地 CLI，核心技术为 Python、Pydantic、SQLite 与 HTTPX，通过 Provider 适配器连接语言模型和图片模型。本地 RAG 是受约束的知识选择机制，不是通用文档问答或向量数据库。知识被采用不等于已经证明图片质量提升。

## 文档

- [使用与配置](docs/USAGE.md)
- [详细架构设计](docs/ARCHITECTURE.md)
- [交互式架构图](docs/diagrams/architecture.html)（下载后用浏览器打开；GitHub 不直接运行 HTML）
- [架构图源文件与说明](docs/diagrams/README.md)
- [任务书与实施记录](docs/task_books/README.md)
