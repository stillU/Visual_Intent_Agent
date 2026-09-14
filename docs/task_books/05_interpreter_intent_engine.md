# Step 05：Interpreter 与 IntentEngine

## 派发给 Agent 的任务

接入唯一的 LLM Provider，实现“用户语言 → Candidate IntentDelta”的受限解释，并把 Step 02、03 的确定性组件编排成 IntentEngine。该任务可与 Step 04 并行。

## 前置依赖

- Step 01～03 已验收；
- Candidate Delta、Validator、Reducer、DecisionPolicy 和 IntentResolution 接口已冻结。

## 目标

完成：

```text
Current Intent
+ User Message
+ Pending Question
        ↓
Interpreter
        ↓
Candidate IntentDelta
        ↓
Validator
        ↓
Reducer
        ↓
DecisionPolicy
        ↓
IntentResolution
```

## Interpreter 边界

LLM 可以：

- 识别用户本轮表达的修改；
- 输出一个或多个 Candidate Delta；
- 为每个 Delta 标注证据；
- 识别用户是否回答了 Pending Question；
- 识别明确委托、明确保持和解除保持。

LLM 不可以：

- 返回完整的新 Intent 替代 Delta；
- 直接设置 Workflow 状态；
- 计算 `ready_for_confirmation`；
- 自动确认用户需求；
- 创建超出 Schema 白名单的路径；
- 把“用户没说”解释为 delegated。

## LLM 输出合同

要求 Provider 返回结构化结果：

```text
InterpreterResult {
    candidate_deltas[]
    detected_conflicts[]
    unresolved_language[]
    evidence_refs[]
}
```

所有 candidate delta 仍必须通过 Validator。即使 Provider 声称输出已验证，也不能跳过确定性验证。

## Pending Question 处理

Interpreter 输入必须包含当前唯一 Pending Question。用户回答“第二个”“就按你推荐的”“你决定”等短句时，只能在 Pending Question 的局部范围内解释。

例如，待回答路径为 `lighting.character` 时，“你决定”只产生该路径的 `user_delegated`，不得扩展到 color、style 或 environment。

## IntentEngine 编排

IntentEngine 负责调用顺序与错误边界，不拥有领域规则：

1. 构建最小 LLM 上下文；
2. 调用 Interpreter；
3. 调用 Validator；
4. 若存在被拒 Delta，保留 issue，不静默吞掉；
5. 对合法 Delta 调用 Reducer；
6. 调用 DecisionPolicy；
7. 返回 IntentResolution。

如果 LLM 输出无法解析，不得猜测修复用户意图；返回可恢复 issue，由 Step 06 决定重试或澄清。

## 实现步骤

1. 定义唯一 LLM Provider interface。
2. 编写 Interpreter 的系统约束和结构化输出 Schema。
3. 实现 Pending Question 的局部上下文构建。
4. 实现结构化响应解析和错误分类。
5. 实现 IntentEngine 编排器。
6. 使用 Fake Provider 编写确定性集成测试。
7. 使用少量真实 Provider smoke test 验证格式，不把网络测试放入默认单元测试。

## 必测场景

- “镜头拉远”只产生 `SET composition.framing`。
- “人物不变”产生 subject 范围的 PIN，不改变 subject 值。
- “光线你决定”只委托 lighting 路径。
- “背景不要海边”无法映射为明确新 location 时输出需澄清信息，不猜城市或森林。
- LLM 输出非法路径时 Validator 拒绝。
- LLM 输出完整 Intent 时解析失败或被拒绝。
- Pending Question 为 lighting 时，“第二个”只作用于 lighting。
- 相同输入通过 Fake Provider 得到稳定 IntentResolution。

## 交付物

- LLM Provider interface 和一个实际 Provider adapter；
- Interpreter；
- IntentEngine 编排器；
- Fake Provider；
- 单元与集成测试；
- Interpreter prompt / schema 版本记录。

## 验收条件

- LLM 无法绕过 Validator / Reducer / DecisionPolicy；
- 所有状态修改都能回溯到 Candidate Delta 和 EvidenceRef；
- Pending Question 的委托范围不会扩大；
- 无数据库硬依赖，便于与 Step 04 独立测试；
- 解析失败可观察、可恢复。

## 禁止范围

- 不持久化 Workflow；
- 不实现确认页面或 API；
- 不编译图像 Prompt；
- 不调用图像模型；
- 不加入第二个 LLM Provider 或 Agent Framework。

## 给下一步的输入

Step 06 需要获得：

- `resolve(current_intent, message, pending_question)`；
- IntentResolution；
- Interpreter 的错误类型；
- Fake Provider 测试夹具。
