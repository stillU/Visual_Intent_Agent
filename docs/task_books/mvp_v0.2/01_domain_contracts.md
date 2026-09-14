# Step 01：领域合同与数据模型

## 派发给 Agent 的任务

实现 MVP 的纯数据合同，为所有后续模块提供唯一、稳定的类型边界。本步骤不实现业务编排、不调用 LLM、不访问数据库、不生成图片。

## 前置依赖

无。这是整个实现链路的第一步。

## 目标

建立可验证、可序列化、可版本化的核心模型：

- `VisualIntent`；
- 字段 Resolution；
- `IntentDelta`；
- Pin / preserve 信息；
- `IntentRevision`；
- `ExecutionRevision`；
- `Issue`、证据引用和基础枚举；
- 后续模块所需的最小 ID 与时间字段类型。

## 必须实现的合同

### 1. VisualIntent

只包含冻结的七类 Facet：

```text
subject
├── description
├── count
└── pose_action

composition
└── framing

environment
├── mode
└── location

style
├── primary
└── description

lighting
└── character

camera
├── angle
└── depth_of_field

color
└── palette
```

不得增加服装、镜头型号、材质、情绪等新顶层字段；更具体的表达暂放入现有 `description`。

### 2. Resolution

合法值仅为：

```text
user_specified
user_confirmed_proposal
user_delegated
not_applicable
```

必须能把 Resolution 绑定到具体路径，而不是只在 Intent 顶层设置一个全局状态。

关键约束：

```text
missing ≠ user_delegated
```

### 3. IntentDelta

合法操作仅为：

```text
SET
CLEAR
PIN
UNPIN
```

最小字段：

```text
operation
path
value?
resolution?
evidence_refs
```

`SET` 可携带 value；`CLEAR`、`PIN`、`UNPIN` 不应借 value 修改其他语义。

### 4. Revision 模型

`IntentRevision` 至少能够表达：

- revision ID；
- session ID；
- 父 revision ID；
- 完整 Intent 快照；
- applied delta；
- 创建时间。

`ExecutionRevision` 至少能够表达当前目标模型和输出比例。不要在本步骤扩展完整模型参数系统。

### 5. Issue 与 Evidence

为后续 Validator 和 DecisionPolicy 提供统一结构：

```text
Issue {
    code
    path?
    message
    severity
}

EvidenceRef {
    message_id
    fragment?
    pending_question_id?
}
```

证据只记录来源，不在本步骤判断其语义是否正确。

## 建议模块边界

```text
domain/
├── intent
├── delta
├── revision
├── issue
└── identifiers
```

具体文件名可根据现有仓库调整，但不能让后续基础模块反向依赖 API 或 Provider 代码。

## 实现步骤

1. 创建所有枚举和路径常量。
2. 创建七类 Facet 与 `VisualIntent` 模型。
3. 创建 Resolution 的路径绑定结构。
4. 创建 `IntentDelta` 及其基础形状约束。
5. 创建 Revision、Issue 和 Evidence 模型。
6. 实现 JSON 序列化与反序列化。
7. 建立 Schema 版本字段，当前固定为 v1。
8. 编写模型级单元测试和固定 JSON fixture。

## 必测场景

- 空 Intent 可以创建，但缺失不被自动标记为 delegated。
- 七类 Facet 均可正确序列化和反序列化。
- 未知顶层字段被拒绝。
- 非法 Resolution 被拒绝。
- 非法 Delta operation 被拒绝。
- `SET` 缺少必要 value 时被拒绝。
- EvidenceRef 可以准确关联消息。
- IntentRevision round-trip 后内容不变。
- Schema v1 fixture 在重复加载后保持稳定。

## 交付物

- 核心 Pydantic v2 模型；
- Schema v1 的 JSON 示例；
- 单元测试；
- 一页公开合同说明，列出后续 Agent 应导入的类型。

## 验收条件

- 所有模型测试通过；
- Schema 只包含冻结字段；
- 无 LLM、数据库、Web API 或图像 Provider 依赖；
- 后续 Agent 不需要阅读内部实现即可使用公开模型；
- 没有以 `dict[str, Any]` 替代核心合同。

## 禁止范围

- 不实现 Validator 或 Reducer；
- 不设计 PromptArtifact、GenerationArtifact 的完整逻辑；
- 不设计 HTTP API；
- 不增加 RAG 字段；
- 不把缺失字段自动转换为系统授权。

## 给下一步的输入

Step 02 必须获得：

- 冻结的路径白名单；
- `VisualIntent` 和 `IntentDelta` 类型；
- Resolution、Issue、Evidence 的公开接口；
- Schema v1 fixture。
