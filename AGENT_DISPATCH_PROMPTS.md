# Agent 派发提示词

使用方式：把对应任务书和下面的提示词一起发给负责实现的 Agent。每次只派发已经满足前置依赖的步骤。

## 通用要求

所有任务均追加以下要求：

```text
以《Visual Intent Agent MVP v0.2 计划书》和本步骤任务书为唯一设计依据。
只实现本步骤范围，不扩展新架构，不提前实现后续步骤。
开始前检查当前仓库和上游交接记录，复用已冻结公开接口。
完成代码后运行任务书要求的测试，并逐条核对验收条件。
不要覆盖其他 Agent 或用户的既有修改。
最终按“任务、完成内容、变更文件、公开接口、测试结果、已知限制、下一步输入、是否验收通过”的格式交接。
```

## Step 01

```text
请执行 Step 01《领域合同与数据模型》。建立 VisualIntent Schema v1、Resolution、IntentDelta、Revision、Issue 和 Evidence 的 Pydantic v2 合同及测试。不得实现 Validator、Reducer、LLM、数据库或 API。Schema 只能包含冻结字段。完成后提交公开合同说明和 Schema fixture。
```

## Step 02

```text
请执行 Step 02《Validator 与 Reducer》。严格复用 Step 01 的路径和模型，实现 Candidate Delta 验证、不可变 Reducer、ChangeSummary 及状态不变量测试。任何非法 Delta 必须显式拒绝；未指定字段必须保持不变。不得实现 DecisionPolicy、LLM 或持久化。
```

## Step 03

```text
请执行 Step 03《DecisionPolicy 与 IntentResolution》。基于已冻结的九项 Decision 建立数据驱动规则，确定性计算缺失、冲突、下一待询问目标和 ready_for_confirmation。不得使用 LLM 做规则判断，不得增加新视觉维度。
```

## Step 04

```text
请执行 Step 04《Repository 与状态机》。实现 SQLite revision 快照、Confirmation 绑定、合法状态迁移和历史不可覆盖测试。严格使用 Step 01～03 的合同；不得实现 Event Sourcing、Web API、LLM 或生成逻辑。本任务可与 Step 05 并行。
```

## Step 05

```text
请执行 Step 05《Interpreter 与 IntentEngine》。接入唯一 LLM Provider，只允许输出有证据的 Candidate IntentDelta，并依次调用 Validator、Reducer、DecisionPolicy。重点验证 Pending Question 的局部授权范围。不得持久化、确认或生成图片。本任务可与 Step 04 并行。
```

## Step 06

```text
请执行 Step 06《澄清、确认与 Workflow》。集成 Step 04 和 Step 05，实现单问题澄清、diff-first 确认摘要、revision/hash 绑定和 Hard Confirmation Gate。完成 P1 端到端测试，但不要实现 PromptEngine 或图片生成。
```

## Step 07

```text
请执行 Step 07《PromptEngine 与 PromptArtifact》。只从已确认 Intent、ExecutionContext 和合法 Realization 编译 Prompt。所有重要 clause 必须有 source binding；unspecified 不得被当成 delegated。只支持一个目标模型，不调用图片 Provider，不接 RAG。
```

## Step 08

```text
请执行 Step 08《图像生成闭环与 GenerationArtifact》。实现唯一 Image Provider Adapter，从已确认的 PromptArtifact 真实生成图片，保存可追踪 GenerationArtifact，并正确处理 WAITING_REVIEW、FAILED 和 retry。不得修改 Intent 或 Prompt，不实现多模型和参考图。
```

## Step 09

```text
请执行 Step 09《FeedbackEngine、RealizationState 与多轮修改》。让反馈精确关联真实 GenerationArtifact，输出 Candidate Delta / preserve paths，实现 Realization 继承与失效，并完成 3～5 轮局部修改测试。不得跳过重新确认，不得承诺图片视觉完全不变。
```

## Step 10

```text
请执行 Step 10《Gate A 评测与核心 MVP 验收》。停止开发新能力，建立同模型条件的 Direct LLM baseline，对 P0～P3 系统执行单轮和多轮 A/B 评测，分四层报告指标、失败案例和 Go/No-Go 结论。评测期间不得修改规则或加入 RAG。
```

## Step 11

```text
只有 Gate A 明确为 Go 时，请执行 Step 11《KnowledgeEngine 与 Gate B》。以 JSONL 为权威源，用 100～300 条以内审核知识和 Milvus Lite 建立最小 RAG，保证 Knowledge 不修改 Intent，并完成 B/C 消融实验。若无稳定增益，明确建议移除 RAG，不扩充知识库。
```

