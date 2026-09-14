# Step 03：DecisionPolicy 与 IntentResolution

## 派发给 Agent 的任务

实现第一版确定性 `DecisionPolicy`，判断当前 Intent 中有哪些必须解决的缺失、歧义和冲突，并输出统一的 `IntentResolution`。

## 前置依赖

- Step 01 数据合同已验收；
- Step 02 Validator 与 Reducer 已验收。

## 目标

回答唯一核心问题：

> 当前还有哪些重要视觉决策必须由用户解决？

该模块不理解自然语言、不生成 Prompt、不直接询问用户。

## 第一版 Decision 清单

只处理：

```text
Primary Subject
Style
Environment
Framing
Pose / Action
Lighting
Camera Angle
Color
Depth of Field
```

不要增加新决策维度。

## 决策分类

### Core

直接定义用户目标：Primary Subject、Style、核心 Environment。适用时缺失通常阻塞确认。

### Perceptual

明显影响视觉结果：Framing、Pose / Action、Lighting、Camera Angle、Color、Depth of Field。是否阻塞由显式 `required_if` 决定。

### Implementation

Prompt token 顺序、模型语法、sampling 参数等实现细节。它们不需要用户确认，标记为 runtime。

## Policy 数据结构

```text
DecisionPolicy {
    path
    materiality: core | perceptual | implementation
    required_if
    delegatable
    dependencies
    conflict_rules
    invalidates_realization
}
```

策略结果：

```text
block
omit
runtime
```

- `block`：必须解决后才能确认；
- `omit`：允许 unspecified，PromptEngine 也不能自行补成具体重大要求；
- `runtime`：仅限实现参数。

## IntentResolution

统一输出：

```text
IntentResolution {
    intent
    applied_delta
    issues
    unresolved_decisions
    conflicts
    question?
    ready_for_confirmation
}
```

本步骤只产生“问题需求”，不负责写自然语言问题；`question` 可先记录待询问的 path、原因和允许选项类型。

## 判定优先级

```text
Hard Conflict
>
Core Missing
>
Perceptual Missing
>
Execution Conflict
```

每次 Resolution 只选择最高优先级的一个待询问目标，以配合 MVP 的默认单问题策略。

## 第一批规则原则

1. `subject.description` 缺失时阻塞。
2. Style 和核心 Environment 是否阻塞必须写成显式规则，不能由 LLM 临时判断。
3. Perceptual 字段只在 `required_if` 成立时阻塞。
4. `user_delegated` 只有在该 path 为 delegatable 时才算已解决。
5. `not_applicable` 只有在规则条件成立时才算已解决。
6. `omit` 不等于授权 PromptEngine 决定。
7. 冲突必须显式输出，不能通过覆盖其中一个字段解决。

## 实现步骤

1. 为九项 Decision 建立数据驱动规则表。
2. 实现 applicable / required_if 计算。
3. 实现 Resolution 合法性检查。
4. 实现缺失决策收集。
5. 实现第一批字段冲突检测。
6. 按优先级选择下一待询问目标。
7. 计算 `ready_for_confirmation`。
8. 使用固定案例建立 golden tests。

## 必测场景

- 空 Intent 不可 Ready。
- 只有 subject 但核心必需项缺失时不可 Ready。
- 用户明确 delegated 且 path 可委托时视为已解决。
- 不可委托字段被标记 delegated 时返回 issue。
- not_applicable 不满足规则条件时返回 issue。
- omit 的字段不阻塞，但仍保持 unspecified。
- 存在 Hard Conflict 时优先于 Missing Decision。
- 所有 block 项解决且无冲突时 Ready。
- Policy 结果不依赖 LLM 或随机数。

## 交付物

- DecisionPolicy v1 规则表；
- Policy evaluator；
- IntentResolution 模型与构造函数；
- 决策案例 fixture；
- Missing Decision、Conflict、Ready 测试。

## 验收条件

- 九项 Decision 均有明确规则；
- 每个 block 都能说明 path 和原因；
- 每个 delegated / not_applicable 均经过规则验证；
- `ready_for_confirmation` 完全由确定性代码产生；
- 无 LLM、数据库或 Prompt 代码依赖。

## 禁止范围

- 不写自然语言 Interpreter；
- 不生成最终澄清文案；
- 不自动选择 delegated 的具体实现；
- 不引入知识库；
- 不为未列出的视觉维度增加 Policy。

## 给下一步的输入

Step 04 和 Step 05 需要获得：

- DecisionPolicy v1 配置；
- `assess(intent, execution_context)`；
- `IntentResolution`；
- 决策优先级与 golden tests。
