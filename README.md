# Visual Intent Agent MVP v0.2：Agent 实施任务索引

本仓库把已冻结的《Visual Intent Agent MVP v0.2 计划书》拆成可独立派发给其他 Agent 的实施设计书。

所有分步任务书与派发提示词统一收录在 [`docs/task_books/`](docs/task_books/) 中。

原始设计依据：[visual_intent_agent_mvp_v0.2.md](../visual_intent_agent_mvp_v0.2.md)

## 使用方法

1. 按本文件的顺序派发任务，不要让后置 Agent 猜测尚未冻结的接口。
2. 每个 Agent 只接收一份任务书，并同时获得原始 MVP 设计书的只读上下文。
3. Agent 完成后必须提交任务书列出的代码、测试和交接记录。
4. 验收未通过时，不开始依赖该结果的后续任务。
5. 后续 Agent 发现上游接口问题时，应提出最小修订，不得自行扩大架构。

## 总体顺序

```text
Step 01 领域合同与数据模型
    ↓
Step 02 Validator 与 Reducer
    ↓
Step 03 DecisionPolicy 与 IntentResolution
    ├──────────────┐
    ↓              ↓
Step 04 Repository 与状态机    Step 05 Interpreter 与 IntentEngine
    └──────────────┬────────────┘
                   ↓
Step 06 澄清、确认与 Workflow
                   ↓
Step 07 PromptEngine 与 PromptArtifact
                   ↓
Step 08 图像生成闭环与 GenerationArtifact
                   ↓
Step 09 FeedbackEngine、RealizationState 与多轮修改
                   ↓
Step 10 Gate A 评测与 MVP 验收
                   ↓ 仅当 Gate A 通过
Step 11 KnowledgeEngine 与 Gate B
```

## 派发清单

| 顺序 | 任务书 | 阶段 | 前置依赖 | 是否允许并行 | 完成标志 |
|---|---|---|---|---|---|
| 01 | [领域合同与数据模型](docs/task_books/01_domain_contracts.md) | P0 | 无 | 否 | Schema 和序列化测试通过 |
| 02 | [Validator 与 Reducer](docs/task_books/02_validator_reducer.md) | P0 | 01 | 否 | 状态不变量测试通过 |
| 03 | [DecisionPolicy 与 IntentResolution](docs/task_books/03_decision_policy.md) | P0 | 01、02 | 否 | 缺失、冲突、Ready 判定通过 |
| 04 | [Repository 与状态机](docs/task_books/04_repository_state_machine.md) | P0/P1 基础 | 01、02、03 | 可与 05 并行 | revision、确认失效、历史不可覆盖测试通过 |
| 05 | [Interpreter 与 IntentEngine](docs/task_books/05_interpreter_intent_engine.md) | P1 | 01、02、03 | 可与 04 并行 | 文本到合法 Delta/Resolution 的闭环通过 |
| 06 | [澄清、确认与 Workflow](docs/task_books/06_clarification_confirmation_workflow.md) | P1 | 04、05 | 否 | Hard Gate 和 P1 端到端测试通过 |
| 07 | [PromptEngine 与 PromptArtifact](docs/task_books/07_prompt_engine.md) | P2 | 06 | 否 | Prompt 来源绑定和越权检查通过 |
| 08 | [图像生成闭环](docs/task_books/08_generation_pipeline.md) | P2 | 07 | 否 | 确认后能真实生成并保存 Artifact |
| 09 | [反馈、Realization 与多轮修改](docs/task_books/09_feedback_realization.md) | P3 | 08 | 否 | 连续 3～5 轮局部修改测试通过 |
| 10 | [Gate A 评测与验收](docs/task_books/10_gate_a_evaluation.md) | Gate A | 09 | 否 | 形成与 Direct LLM 的对比报告和 Go/No-Go 结论 |
| 11 | [KnowledgeEngine 与 Gate B](docs/task_books/11_knowledge_engine_gate_b.md) | 第二阶段 | Gate A 通过 | 否 | RAG 消融报告和保留/移除结论 |

## 可并行范围

唯一推荐的并行点是 Step 04 与 Step 05：

- Step 04 只实现持久化、revision 和确定性状态迁移；
- Step 05 只实现语言理解边界和 IntentEngine 编排；
- 两者必须共同依赖 Step 01～03 已冻结的合同；
- 两者完成后在 Step 06 集成。

不要提前并行 Step 07～09。这些步骤都依赖上一阶段产生的真实 Artifact 和状态约束，提前开发会导致接口返工。

## 全局不变量

所有 Agent 都必须遵守：

1. LLM 负责理解和表达，确定性代码负责状态。
2. LLM 只能提出 Candidate Delta，不能直接修改 Intent。
3. 用户未说明的重要视觉内容不能被系统静默决定。
4. 未被 Delta 指定的字段必须保持不变。
5. 重要 Intent 修改后旧 Confirmation 必须失效。
6. 正式 Prompt 和图片生成必须通过 Hard Confirmation Gate。
7. Intent、Realization、Prompt、Generation 和 Feedback 不得混为同一层。
8. 历史 revision 和 Artifact 不可覆盖。
9. 第一阶段不引入 Milvus、多 Agent 框架、微服务、Event Sourcing 或 Prompt AST。
10. Agent 只完成任务书范围，不顺手建设“以后可能需要”的能力。

## 统一交接格式

每个实现 Agent 完成任务后，提交一份简短交接说明：

```text
任务：Step XX
完成内容：
变更文件：
公开接口：
测试命令与结果：
已知限制：
对下一步的输入：
是否满足验收条件：是 / 否
```

如果“不满足”，必须说明阻塞原因，不得把未完成工作静默移交给下一个 Agent。
