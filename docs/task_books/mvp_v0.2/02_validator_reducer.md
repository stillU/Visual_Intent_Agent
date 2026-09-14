# Step 02：Validator 与 Reducer

## 派发给 Agent 的任务

基于 Step 01 的冻结合同，实现 Candidate IntentDelta 的确定性验证和状态归并。该模块是“LLM 不能直接管理状态”的核心边界。

## 前置依赖

- Step 01 已验收；
- `VisualIntent`、`IntentDelta`、Resolution、Issue、Evidence 和路径白名单已经冻结。

## 目标

实现两个纯代码组件：

```text
Candidate IntentDelta
        ↓
Validator
        ↓
Validated IntentDelta
        ↓
Reducer(Current Intent, Delta)
        ↓
New Intent
```

本步骤不判断哪些视觉字段必须补齐；该职责属于 Step 03 的 `DecisionPolicy`。

## Validator 责任

依次检查：

1. operation 是否属于 `SET / CLEAR / PIN / UNPIN`；
2. path 是否属于 Schema v1 白名单；
3. value 与目标路径类型是否兼容；
4. 修改是否存在用户消息或当前 Pending Question 证据；
5. operation 是否越过证据所覆盖的授权范围；
6. 是否试图修改 revision ID、创建时间、workflow state 等系统字段；
7. PIN / UNPIN 是否针对可保持的 Intent 路径。

输出必须显式区分：

```text
accepted deltas
rejected deltas
issues
```

非法 Delta 不得静默忽略，也不得被 Validator 自动改写为“它认为正确”的路径。

## Reducer 责任

Reducer 必须是确定性函数：

```text
reduce(current_intent, validated_deltas) -> new_intent
```

必须保证：

- `SET` 只修改指定路径；
- `CLEAR` 只清除指定路径；
- `PIN` 不改变字段值；
- `UNPIN` 只解除保持，不授权重新设计；
- 未出现在 Delta 中的字段逐值保持；
- 输入对象不被原地修改；
- 同一输入和同一 Delta 始终得到相同输出；
- 新 revision 可追溯到父 revision。

## 修改影响

Reducer 输出同时提供最小 change summary：

```text
changed_paths
pinned_paths
unpinned_paths
cleared_paths
confirmation_invalidated
```

只要重要视觉字段发生 `SET` 或 `CLEAR`，`confirmation_invalidated` 必须为真。PIN / UNPIN 是否使确认失效，按冻结规则采用保守做法：保持约束变化后重新确认。

## 实现步骤

1. 从 Step 01 导入路径白名单，不再维护第二份路径清单。
2. 实现 operation 和 path 验证。
3. 实现 value 类型验证。
4. 实现证据存在性与系统字段保护。
5. 实现不可变 Reducer。
6. 实现 change summary。
7. 为每种 operation 建立 table-driven tests。
8. 增加属性级“不相关字段保持”测试。

## 必测场景

### SET

- 修改 `composition.framing` 后其他所有字段不变。
- 使用非法路径时整个该 Delta 被拒绝。
- 没有 EvidenceRef 的 LLM 候选被拒绝。

### CLEAR

- 只清除目标字段和其 Resolution；
- 不把 CLEAR 解释成 delegated；
- pinned 路径的清除按明确规则拒绝并返回 issue。

### PIN / UNPIN

- PIN 后值不变；
- UNPIN 后值仍不变；
- PIN 不存在的路径被拒绝或返回明确 issue；
- UNPIN 未 pin 的路径不产生隐藏修改。

### 不变量

- 原对象在 reduce 后完全不变；
- Delta 顺序可观察且有测试；
- 非目标路径深比较一致；
- 重要修改使旧确认失效；
- 重放相同 revision 的结果可复现。

## 交付物

- Validator；
- Reducer；
- ValidationResult 和 ChangeSummary；
- 完整单元测试；
- 对 Step 03、04、05 可调用的公开函数说明。

## 验收条件

- State Correctness 相关测试 100% 通过；
- Validator 和 Reducer 均无 LLM、数据库或网络依赖；
- 非目标字段保持测试覆盖七类 Facet；
- 所有拒绝均有可观察 issue；
- 无静默修复或隐式字段填充。

## 禁止范围

- 不生成澄清问题；
- 不判断 Ready for Confirmation；
- 不持久化数据库；
- 不解释自然语言；
- 不编译 Prompt。

## 给下一步的输入

Step 03 需要获得：

- `validate(candidate_deltas, evidence_context)`；
- `reduce(current_intent, validated_deltas)`；
- ValidationResult；
- ChangeSummary；
- 全部状态不变量测试。
