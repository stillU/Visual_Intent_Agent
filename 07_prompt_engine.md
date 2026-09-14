# Step 07：PromptEngine 与 PromptArtifact

## 派发给 Agent 的任务

实现受限 Prompt 编译：只能从已确认 Intent、ExecutionContext 和合法 delegated 选择生成目标模型 Prompt，并保存可追溯的 PromptArtifact。

## 前置依赖

- Step 06 已验收；
- 可以可靠读取已确认的 Intent / Execution revision；
- Hard Confirmation Gate 已可验证。

## 目标

实现：

```text
Confirmed VisualIntent
+ ExecutionContext
+ RealizationState（当前可为空）
        ↓
CompilationSpec
        ↓
Resolve Delegations
        ↓
Source-bound Clauses
        ↓
Model Renderer
        ↓
PromptArtifact
```

`CompilationSpec` 是轻量内部结构，不扩展为 Prompt AST。

## 授权边界

PromptEngine 可以：

- 改变措辞；
- 调整目标模型语法；
- 组织 token；
- 实现明确 delegated 的决策；
- 复用未失效的 Realization。

PromptEngine 不可以：

- 新增主体；
- 新增显著颜色；
- 新增服装；
- 新增场景；
- 新增视觉风格；
- 重新解释用户偏好。

除非对应 path 已明确 `user_delegated`。

## Source-bound Clause

Prompt 中每项重要视觉内容必须能回溯到：

```text
intent_path
rule_id
delegated_path / realization_id
```

无法回溯的重大内容视为 `unauthorized_addition`，编译失败而不是继续生成。

## Delegation 处理

第一轮遇到 delegated path 时，PromptEngine 可在该 path 的局部范围内选择具体实现，并输出待保存的 Realization value。

已有且未失效的 Realization 必须优先复用。不得因为重新编译 Prompt 而重新随机选择。

## PromptArtifact

至少保存：

```text
PromptArtifact {
    prompt_artifact_id
    based_on_intent_revision
    based_on_confirmation
    target_model
    prompt
    parameters
    source_bindings
    realization_refs
    created_at
}
```

## 实现步骤

1. 定义 CompilationSpec 的最小内部表示。
2. 把每个已解决 Intent path 转换成 clause。
3. 实现 delegated path 的局部选择接口。
4. 实现 Source Binding。
5. 实现唯一目标图像模型的 Renderer。
6. 实现 unauthorized addition 检查。
7. 在保存前再次验证 Confirmation 有效性。
8. 保存 PromptArtifact 和新产生的 Realization 候选。

## 必测场景

- 无有效确认时拒绝编译。
- 已确认 Intent 的每个重要 clause 有来源。
- unspecified + omit 字段不会被具体化。
- delegated lighting 可被实现，但不能顺带决定 color。
- 已有 Realization 被稳定复用。
- Intent revision 变化后旧 PromptArtifact 不被覆盖。
- Renderer 只处理一个目标模型。
- 人为插入无 source clause 时编译失败。

## 交付物

- PromptEngine；
- CompilationSpec；
- 唯一目标模型 Renderer；
- PromptArtifact 模型和 Repository 写入；
- Source Binding / unauthorized addition 检查；
- 单元与集成测试。

## 验收条件

- 无 Confirmation 不生成 Prompt；
- 重要视觉 clause 的来源覆盖率为 100%；
- unspecified 不被等同于 delegated；
- 不引入第二个图像模型 Renderer；
- PromptArtifact 可完整追溯到 Intent 和 Confirmation。

## 禁止范围

- 不调用图像模型；
- 不实现 KnowledgeEngine；
- 不引入复杂 Prompt AST；
- 不根据模型“最佳实践”覆盖用户选择；
- 不把 Prompt 文本当成生成结果。

## 给下一步的输入

Step 08 需要获得：

- 已保存的 PromptArtifact；
- 目标模型参数；
- PromptEngine 的失败类型；
- Provider 调用所需的最小输入。

