# Step 04：Repository 与状态机

## 派发给 Agent 的任务

实现 SQLite 持久化、不可覆盖的 revision / artifact 基础记录，以及 MVP 的确定性状态机。该任务可在 Step 05 同时进行，但必须使用 Step 01～03 的冻结合同。

## 前置依赖

- Step 01～03 已验收；
- revision、IntentResolution 和 ChangeSummary 接口已冻结。

## 目标

建立可靠状态底座：

- 会话和消息可保存；
- Intent / Execution 每次修改产生新 revision；
- Confirmation 绑定具体 revision；
- 历史不可覆盖；
- 状态迁移由代码验证；
- 后续 Artifact 有稳定的引用位置。

## 核心表

```text
sessions
messages
intent_revisions
execution_revisions
confirmations
realization_states
prompt_artifacts
generation_artifacts
feedback_results
```

本步骤只需完整实现前五项及后四项的最小可扩展记录边界；后续 Agent 负责各 Artifact 的业务内容。不要提前加入 `knowledge_bundles`。

## 持久状态

```text
UNDERSTANDING
WAITING_CLARIFICATION
WAITING_CONFIRMATION
GENERATING
WAITING_REVIEW
FAILED
COMPLETED
```

`RETRIEVING` 和 `REFINING` 不作为持久状态。

## 必须支持的状态迁移

至少覆盖：

```text
new session → UNDERSTANDING
UNDERSTANDING → WAITING_CLARIFICATION
UNDERSTANDING → WAITING_CONFIRMATION
WAITING_CLARIFICATION → UNDERSTANDING
WAITING_CONFIRMATION → GENERATING
GENERATING → WAITING_REVIEW
GENERATING → FAILED
FAILED → GENERATING / WAITING_CONFIRMATION
WAITING_REVIEW → UNDERSTANDING
WAITING_REVIEW → COMPLETED
```

不在允许表中的迁移必须拒绝并返回明确错误。

## Revision 规则

1. 每次合法 Intent 修改创建新 `intent_revision`。
2. 不使用 UPDATE 覆盖旧 Intent 快照。
3. ExecutionContext 变化创建新 `execution_revision`。
4. Confirmation 保存 `intent_revision + execution_revision + summary_hash`。
5. 当前 revision 发生重要修改后，旧 Confirmation 仍保留历史记录，但不再有效。
6. 所有写入操作在事务中完成。

## Repository 接口

至少提供：

```text
create_session
append_message
append_intent_revision
append_execution_revision
save_confirmation
get_current_session_snapshot
get_intent_revision
is_confirmation_valid
transition_state
```

使用 repository interface 隔离 SQLite 细节，但不要建设复杂 ORM 抽象或 Event Sourcing。

## 实现步骤

1. 建立 SQLite schema 和最小迁移机制。
2. 实现 session、message 和 revision 写入。
3. 实现当前快照读取。
4. 实现 Confirmation 的绑定和有效性检查。
5. 实现状态迁移表。
6. 实现事务和失败回滚。
7. 为并发或重复请求建立最小幂等保护。
8. 编写临时数据库集成测试。

## 必测场景

- 新 revision 不覆盖旧 revision。
- 父 revision 链正确。
- 旧确认在 Intent 修改后无效。
- 旧确认记录仍可审计。
- execution revision 改变后也要求重新确认。
- 非法状态迁移被拒绝。
- 写入中途失败时事务回滚。
- 重开数据库后 session snapshot 可恢复。
- Feedback / Generation 的外键不能指向不存在的记录。

## 交付物

- SQLite schema / migration；
- Repository 实现；
- 状态机；
- Repository 与状态迁移集成测试；
- 给 Step 06～09 使用的公开接口说明。

## 验收条件

- 历史数据不可覆盖；
- Confirmation 有严格 revision 绑定；
- 非法状态迁移无法落库；
- 测试使用 SQLite 并可重复运行；
- 无 Event Sourcing、Milvus 或 Provider 依赖。

## 禁止范围

- 不实现 Web API；
- 不实现 Interpreter；
- 不实现 Prompt 或图片调用；
- 不加入异步任务队列；
- 不增加微服务或独立数据库服务。

## 给下一步的输入

Step 06 需要获得：

- Repository 接口；
- 状态迁移表；
- Confirmation 有效性检查；
- 可用于端到端测试的临时 SQLite fixture。
