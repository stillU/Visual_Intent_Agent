# 项目架构图

- [SVG 静态图](architecture.svg)：README 内嵌展示，支持缩放。
- [交互式 HTML](architecture.html)：下载后用浏览器打开，支持明暗主题与节点探索。
- [JSON 源文件](architecture.json)：后续修改图示的唯一编写源。

图示为职责分层，不是部署拓扑或逐函数调用图。意图与工作流聚合 IntentEngine、Validator/Reducer、DecisionPolicy 和 WorkflowService；反馈使用相同语言模型能力，但省略重复外部连线。业务数据库是共享 Repository，图中只绘制代表性持久化连线；PromptEngine、GenerationPipeline、ReviewService 同样读写业务记录。本地图片文件不是额外数据库服务。

依据代码快照：2d3cc7a8aa17ae080e726b9035aeae5bf40b1f1e。

- [CLI 装配](../../visual_intent_agent/cli.py)：工作流、反馈、编译与生成的依赖组装。
- [意图引擎](../../visual_intent_agent/intent_engine/engine.py)与[工作流](../../visual_intent_agent/workflow/service.py)：理解、校验、状态与确认。
- [提示词编译](../../visual_intent_agent/prompt_engine/engine.py)与[本地检索](../../visual_intent_agent/knowledge/retrieval.py)：受委托的候选选择与来源追溯。
- [生成管线](../../visual_intent_agent/generation/pipeline.py)与[持久化](../../visual_intent_agent/persistence/repository.py)：外部图片调用、产物记录和本地存储。

## 生成与图形检查

使用 Archify 技能生成；本次只检查图形产物，不运行项目测试或模型请求。生成器可通过其 bin/archify.mjs 的 validate、deliver、visual-check 命令复建与检查。

- diagram_type: architecture
- validation: showcase 9/9，0 errors，0 warnings
- specification_sha256: e26455cb069d6b7b696d977d4c94177df62d3b07aa35004a4b42f9a1bf3362b2
- artifact_sha256: bb1de474964a61f2a4b8855478be31e3e42837831c35b0516991478ee97c3382
- browser_evidence: passed；1440×900、1600×1000、1920×1080、2048×1320 均无页面溢出
- visual_review: passed；已查看交付 HTML 的明暗主题桌面截图
- correction_rounds: 1

SVG 使用已交付 HTML 自带的标准导出功能生成，保留图示语义，不包含交互控件。图形检查不代表产品功能、模型效果或业务安全评测通过。
