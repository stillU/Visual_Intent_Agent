# MVP v0.4：可追溯的 RAG 知识辅助

日期：2026-09-16。状态：计划，尚未实施。当前前置检查见 [工程检查](00_readiness_review.md)。

## 目标与执行裁定

在现有 Visual Intent Agent 中接入小型、可关闭的本地知识库，让用户已明确委托的视觉决策获得上下文支持，而不是仅按 revision ID 从固定候选表选择。

本计划对“RAG 辅助”的首版范围作如下限定：**检索经审核知识 → 选择受授权的实现值 → 原 PromptEngine 编译 → 原图片生成链路**。不是通用文档问答，不是直接把检索文本交给 LLM 改写全部 Prompt。它是检索增强的生成链路，但不承诺已经改善图像质量。

首版只辅助 `lighting.character`、`composition.framing`、`camera.depth_of_field` 三条用户可委托路径，候选值仍使用现有 `DELEGATED_CANDIDATES`。其他路径保持原行为。明确值、PIN、未授权的 missing 字段均不由知识库决定。

执行依据是用户最新方向“加入 RAG 后再评测”。因此：

- 无须先跑 v0.3 Gate A；原 v0.2 Step 11 的 Gate A 前置条件及 Milvus Lite/Embedding 技术选型不作为本轮约束。
- 本轮先交付 RAG 工程闭环与离线验收；正式评测只能在其后按 [评测准备](05_evaluation_readiness.md) 独立启动，禁止接入前消耗真实评测预算。
- 正式评测未执行时不写 Go/No-Go，不将检索命中率等同于图像质量提升。

## 最短架构路径

已确认 Intent 与目标模型 → 仅查询尚无 active Realization 的委托路径 → 本地检索与结构化筛选 → KnowledgeBundle → 原 Realization/Prompt 编译 → 原生成与反馈。

知识库 JSONL 是权威源；首版在内存建立可重建的关键词索引，不新增服务、Embedding API、向量库或额外 LLM 调用。对几十条审核单元，先把检索消费、安全边界和追溯做对。词法召回能力不足属于后续升级依据，不宣称等价于语义向量检索。

## 任务顺序

| 步骤 | 任务书 | 完成条件 |
|---|---|---|
| 00 | [前置工程检查](00_readiness_review.md) | 三项修复已检查，2232 项离线测试通过 |
| 01 | [知识合同与内容](01_knowledge_contract_corpus.md) | Schema、来源、审核流程、小型知识库与安全合同 |
| 02 | [检索与知识包](02_retrieval_bundle.md) | 确定性查询、过滤、排序、回退、可复建索引 |
| 03 | [编译接入与持久化](03_prompt_integration.md) | 真正影响授权实现值；来源可查、历史兼容、重试稳定 |
| 04 | [CLI 与工程验收](04_cli_acceptance.md) | 开关、状态可见、Fake 闭环与一次集中回归 |
| 05 | [RAG 后评测准备](05_evaluation_readiness.md) | 工程通过后制定新协议；真实运行单独启动 |

默认按 01→02→03→04 顺序由一个 agent 实施，避免共享模型与 Repository 接口冲突。第 05 步此时只准备，不自动运行真实批次。可直接使用 [派发指令](AGENT_DISPATCH_PROMPTS.md)。

## 非目标与安全底线

- 不增加 Web、在线爬虫、任意用户上传、参考图、多模型、Reranker、GraphRAG、训练或微调。
- 知识来源不是用户授权；不能作为 EvidenceRef 冒充用户消息，不写回 VisualIntent，不替代 Policy，不绕过确认。
- Renderer 不新增视觉 clause；仍由 PromptEngine 的来源校验与 coverage 校验控制。
- 关闭 RAG 的业务行为与当前修复后基线一致；无命中、知识库不可用时明确回退，不能悄悄宣称应用了知识。
- 现有 active Realization 优先复用，重试复用原 PromptArtifact，不因知识库更新改变已有生成依据。

每步交付 `docs/handoffs/v0_4_step_XX_handoff.md`，记录实际代码状态、修改范围、合同变化、测试、已知限制。未获要求不提交或推送 Git。预计 4～6 个工作日完成工程部分，人工知识审核另计；这是范围估算，不含真实评测或质量承诺。
