# Step 06：澄清、确认与 Workflow

## 派发给 Agent 的任务

把 IntentEngine 与 Repository 集成为完整 P1：接收用户消息、提出最重要的澄清问题、展示确认摘要，并强制执行 Hard Confirmation Gate。

## 前置依赖

- Step 04 Repository / 状态机已验收；
- Step 05 Interpreter / IntentEngine 已验收。

## 目标

实现：

```text
用户文本
→ IntentEngine
→ 必要澄清
→ Ready
→ WAITING_CONFIRMATION
→ 用户确认当前 revision
```

本步骤结束时还不生成正式 Prompt 或图片。

## 澄清策略

默认每轮只问一个最重要问题，优先级来自 DecisionPolicy：

```text
Hard Conflict
>
Core Missing
>
Perceptual Missing
>
Execution Conflict
```

Question Builder 可以使用 LLM 调整自然语言，但问题的目标 path、是否允许委托和选项边界必须来自 DecisionPolicy。

问题尽量包含：

- 2～3 个具体选项；
- 自定义输入入口；
- 该路径允许时提供“交给系统决定”。

必须保持：

```text
系统推荐 ≠ 用户已经选择
```

## Confirmation 摘要

采用 diff-first：

1. 本轮修改内容；
2. 明确保留内容；
3. 用户授权系统决定的内容；
4. 目标模型；
5. 输出比例；
6. 可展开的完整 Intent。

确认记录：

```text
ConfirmationRecord {
    intent_revision
    execution_revision
    summary_hash
    confirmed_at
}
```

用户的“确认”只能确认当前展示的 revision。客户端或会话状态变化后，不得把迟到的确认应用到新 revision。

## Workflow 编排

### 用户消息

1. 保存 message；
2. 状态进入 `UNDERSTANDING`；
3. 调用 IntentEngine；
4. 保存新 Intent revision；
5. 有问题则保存 Pending Question 并进入 `WAITING_CLARIFICATION`；
6. Ready 则进入 `WAITING_CONFIRMATION`。

### 用户确认

1. 验证请求中的 intent / execution revision；
2. 重新计算并核对 summary hash；
3. 保存 ConfirmationRecord；
4. 仅标记“允许进入生成”，本步骤不实际生成。

## 对外用例接口

实现应用层用例即可，不要求为每个内部概念设计 API：

```text
create_session
submit_message
get_session
confirm_current_intent
```

如仓库已有 FastAPI，可提供最薄的一层路由；业务规则必须留在应用层，不放进路由函数。

## 实现步骤

1. 实现 QuestionSpec → 用户问题的 Builder。
2. 实现 Pending Question 保存和恢复。
3. 实现 diff-first Confirmation Summary。
4. 实现 summary hash。
5. 实现用户消息用例和状态迁移。
6. 实现确认用例和 revision 校验。
7. 集成 Fake LLM 和临时 SQLite 完成 P1 端到端测试。

## 必测场景

- 空需求进入澄清，不进入确认。
- 一轮只返回最高优先级问题。
- 用户回答后继续处理下一个 unresolved decision。
- Ready 后状态为 `WAITING_CONFIRMATION`。
- 未确认时无法进入生成状态。
- 确认旧 revision 被拒绝。
- 修改 Intent 后旧确认失效。
- 修改 output ratio 后旧确认失效。
- summary hash 不匹配时拒绝确认。
- 系统推荐选项不被自动写入 Intent。

## 交付物

- Question Builder；
- Pending Question 管理；
- Confirmation Summary 与 Record；
- P1 Workflow 用例；
- 最薄 API（若当前项目需要）；
- P1 端到端测试。

## 验收条件

- Hard Confirmation Gate 无绕过路径；
- Confirmation 精确绑定当前两类 revision；
- 所有状态迁移合法且落库；
- P1 从多轮澄清到确认可以完整运行；
- 尚未出现 Prompt 或图片生成逻辑。

## 禁止范围

- 不实现 PromptEngine；
- 不调用图像 Provider；
- 不设计前端组件系统；
- 不增加批量问题模式；
- 不弱化每次重要修改后的重新确认。

## 给下一步的输入

Step 07 需要获得：

- 已确认的 VisualIntent；
- ExecutionRevision；
- 有效 ConfirmationRecord；
- 从 Repository 读取确认快照的应用层接口。

