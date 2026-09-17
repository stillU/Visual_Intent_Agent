# v0.4 Agent 派发指令

## 工程实施（默认入口）

```text
请执行 docs/task_books/mvp_v0.4/README.md 的 Step 01～04。先阅读 00 前置检查、01～04 任务书、docs/ARCHITECTURE.md 和现有后置修复交接，核对实际 HEAD 与工作区；保留全部既有改动。

目标是最小可追溯 RAG：本地审核 JSONL → 确定性检索 → KnowledgeBundle → 已明确委托且无 active Realization 的 lighting.character/composition.framing/camera.depth_of_field → 原 Prompt/生成链路。默认关闭，检索无效时透明回退。只用现有候选值，不新增向量服务、Embedding API 或 LLM 重写链路，不把检索内容作为用户意图或授权。

按 01→02→03→04 实施，先冻结兼容合同再接线，补齐来源、旧数据库、PIN/确认、回退和重试的离线测试。不得覆盖既有冻结输入、报告或 Artifact。真实审核未完成则交付 draft 和 Fake 功能记录，不伪造 approved。

本轮不运行真实探针、正式 A/B、图片批次、人工盲评或 Gate 判定；第 05 步只准备，RAG 接入完成后等待独立启动。集中离线验收后写各步 docs/handoffs/v0_4_step_XX_handoff.md，说明实际结果与限制。未经要求不提交或推送 Git。
```

## 后续评测准备（工程验收后才使用）

```text
先核对 v0.4 Step 01～04 的实际工程验收与知识审核记录，再依据 docs/task_books/mvp_v0.4/05_evaluation_readiness.md 准备 B/C 消融协议、样本和预算。不得直接执行真实付费批次；列明需要用户确认的预算及人工安排。保留旧冻结物，避免知识库与测试答案污染，解决 revision seed 和语义阶段配对问题后再冻结新版本。
```
