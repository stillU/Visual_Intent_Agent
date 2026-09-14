# Step 11：KnowledgeEngine 与 Gate B

## 派发给 Agent 的任务

仅在 Gate A 通过后，实现最小 KnowledgeEngine，并通过消融实验判断 RAG 是否产生稳定价值。这是第二阶段，不属于核心 MVP P0～P3。

## 前置依赖

- Step 10 的 Gate A 结论为 Go；
- System B 已冻结；
- 已明确哪些失败可能由知识表达、模型适配或 delegated 实现改善。

如果 Gate A 为 No-Go，本任务不得启动。

## 目标

实现：

```text
Confirmed Intent
+ delegated scopes
+ target model
        ↓
QueryBuilder
        ↓
Milvus Lite Dense Top-K
        ↓
KnowledgeBundle
        ↓
PromptEngine
```

## 数据边界

```text
JSONL → Embedding → Milvus Lite
```

- JSONL 是唯一权威知识源；
- Milvus Lite 是可重建索引；
- 首批仅 100～300 条人工审核 KnowledgeUnit；
- 不先建设 1500～2500 条知识库；
- 不加入 Hybrid Search、Reranker 或 GraphRAG。

## Knowledge 可以做什么

- 改善目标模型表达；
- 提供模型限制和兼容写法；
- 帮助实现用户已 delegated 的 path；
- 提供经过审核的实现规则。

## Knowledge 不可以做什么

- 决定用户喜欢什么；
- 新增用户未授权的重要视觉要求；
- 替代 DecisionPolicy；
- 绕过 Confirmation；
- 把检索结果直接写回 VisualIntent。

## KnowledgeUnit 最小内容

只保留实验需要的字段：

```text
knowledge_id
content
applicable_paths
target_model?
source
review_status
version
```

不要在实验前建设复杂本体或知识图谱。

## KnowledgeBundle

记录每次检索实际使用的内容：

```text
KnowledgeBundle {
    bundle_id
    intent_revision
    query
    retrieved_units
    scores
    created_at
}
```

PromptArtifact 必须能引用 KnowledgeBundle，但知识生成的 clause 仍需要 source binding。

## 实现步骤

1. 定义并人工审核首批 KnowledgeUnit。
2. 建立 JSONL 校验。
3. 实现 embedding adapter。
4. 从 JSONL 重建 Milvus Lite 索引。
5. 实现 QueryBuilder。
6. 实现 Dense Top-K 检索。
7. 构建 KnowledgeBundle。
8. 在 PromptEngine 中增加可选 KnowledgeBundle 输入。
9. 运行 Gate B 消融实验。

## Gate B 对比

```text
B = Visual Intent Agent without RAG
C = Visual Intent Agent + RAG
```

其他条件必须完全一致，包括 Intent、Confirmation、模型版本、参数和评测集。

重点比较：

- 生成结果；
- Prompt Quality；
- 模型适配；
- 失败率；
- Unauthorized Addition；
- 延迟和维护成本。

## 必测场景

- 删除 Milvus 索引后可从 JSONL 完整重建。
- 检索结果只来自已审核 KnowledgeUnit。
- Knowledge 不改变 VisualIntent。
- 非 delegated 用户偏好不被知识决定。
- Prompt 中知识来源可追溯到 KnowledgeBundle。
- 不提供 KnowledgeBundle 时，系统行为与冻结的 System B 一致。
- 相同输入的 B/C 条件可比较。

## Gate B 结论

如果 RAG 没有稳定改善：

- 不继续扩大知识库；
- Milvus 不进入主产品关键链路；
- 保留实验报告，不因已开发而强行上线。

如果 RAG 有稳定改善：

- 只保留已验证产生增益的知识类型；
- 再决定是否扩大数据规模；
- 扩展仍需单独设计和验证，不在本任务中完成。

## 交付物

- 100～300 条以内的审核 JSONL；
- JSONL Schema Validator；
- Embedding 与 Milvus Lite adapter；
- QueryBuilder 和 KnowledgeBundle；
- PromptEngine 可选接入；
- Gate B 消融报告；
- 保留或移除 RAG 的明确结论。

## 验收条件

- Gate A 已通过；
- JSONL 为权威源且索引可重建；
- Knowledge 不越权修改 Intent；
- B/C 实验控制变量一致；
- 最终结论基于测量结果而非架构偏好。

## 禁止范围

- 不实现 Reference Gallery；
- 不加入参考图理解；
- 不加入 Hybrid Search、Reranker 或 GraphRAG；
- 不扩充未验证的大规模知识库；
- 不继续 P5 或更多架构阶段。
