# 架构裁定记录 001（Rev.1）：Step 04/05 三项最小修订提案

- 日期：2026-09-14
- 裁定方：架构设计 Agent（ARCHITECTURE.md 维护方）
- 提案方：Step 04 实现 Agent（提案 1、3，见 `step_04_handoff.md`「最小修订提案」与「已知限制 1」）、Step 05 实现 Agent（提案 2 同一问题，见 `step_05_handoff.md`「已知限制 1」）
- 结果：三项全部采纳（1a、2a、3a）；`docs/ARCHITECTURE.md` 已按 Rev.1 更新（第 4 节 Step 04 两处、第 7 节两条、文末「修订记录」）。
- 事实核验（裁定当日执行）：`uv run pytest -q` → 1384 passed, 2 deselected；`uv run pytest tests/validation tests/policy -q` → 收集期 5 个 ImportError（复现提案 2）。

---

## 提案 1：状态迁移表缺口 `WAITING_CONFIRMATION → UNDERSTANDING`

### 提案原文摘要

Step 04 handoff「最小修订提案 3」：Step 06 冻结流程要求"用户消息 → 状态进入
`UNDERSTANDING`"，但冻结的 10 条迁移不含 `WAITING_CONFIRMATION → UNDERSTANDING`；
在确认页收到修改意见时 Step 06 会撞上非法迁移。Step 04 严格遵守冻结表保持 10 条，
请架构方二选一：(a) 增补该迁移（11 条）；(b) 明确 `WAITING_CONFIRMATION` 下拒绝新消息。

### 裁定：采纳 (a)

`ALLOWED_TRANSITIONS` 增补 `(WAITING_CONFIRMATION, UNDERSTANDING)` 为**第 11 条**合法
迁移；ARCHITECTURE.md 第 4 节"恰好 10 条"表述已修订。选项 (b) 否决。

### 理由（原文依据）

1. **任务书 04 的迁移表是下限而非上限**：`docs/task_books/mvp_v0.2/04_repository_state_machine.md`「必须支持的
   状态迁移」原文为"**至少覆盖**："。增补第 11 条不违反任务书范围约束；"恰好 10 条"是
   架构自身的收紧，经最小修订流程放宽一条属正当程序。
2. **原始设计书明确列出该迁移**：`visual_intent_agent_mvp_design_v0.1.md` 第 15 节
   状态机表含行 `| WAITING_CONFIRMATION | 修改内容 | UNDERSTANDING |`；其端点状态约束
   并写明"**messages 在用户等待态可用**"——`WAITING_CONFIRMATION` 是用户等待态，
   选项 (b) 的"拒绝新消息"直接违背设计书。
3. **Step 06 任务书的流程与必测场景依赖该迁移**：`docs/task_books/mvp_v0.2/06_clarification_confirmation_workflow.md`
   「用户消息」第 2 步"状态进入 `UNDERSTANDING`"是每轮无条件动作；必测场景"修改 Intent
   后旧确认失效""修改 output ratio 后旧确认失效"在 P1 流程中必经"确认后
   （`WAITING_CONFIRMATION`）再发修改消息 → 回到 `UNDERSTANDING` → 新 revision →
   旧确认失效"。无该迁移，此场景无法经 `submit_message` 端到端走通（只能在 API 层
   绕过状态机，违反"所有状态迁移合法且落库"的验收条件）。
4. **不扩大架构**：不新增状态、不新增模块、不新增依赖，仅 frozenset 增一项；Step 04
   已实现的"目标状态 == 当前状态为幂等无操作"语义不变、继续有效（新会话首条消息
   `UNDERSTANDING → UNDERSTANDING` 仍按幂等处理）。

## 提案 2：测试基建 conftest 同名冲突

### 提案原文摘要

Step 04 handoff「已知限制 1」与 Step 05 handoff「已知限制 1」：`tests/validation` 与
`tests/policy` 的 conftest.py 均被各自测试文件 `from conftest import ...`；pytest 9.1.1
在**多个目录参数**下按 basename 缓存 `sys.modules["conftest"]`，后者覆盖前者，导致收集期
ImportError（实证：`uv run pytest tests/validation tests/policy -q` 收集期 5 个错误）；
递归全量 `uv run pytest -q` 不受影响（1384 绿）。Step 05 建议："各测试目录把共享工具放进
唯一命名模块……或改用 fixture，不再 `from conftest import`"。

### 裁定：采纳 (a)（授权最小修复）

两个目录把共享 helper 迁入唯一命名模块（`validation_helpers.py` / `policy_helpers.py`），
`conftest.py` 只留 pytest fixture/钩子，测试文件改 import。选项 (b)（不修、验收只用全量
递归）否决。

### 理由（原文依据）

1. **冲突切断任务书要求的范围验证链路**：Step 04 验收需执行的多目录命令
   `uv run pytest tests/persistence tests/domain tests/validation tests/policy tests/test_sanity.py -q`
   因收集期 ImportError 无法按字面执行（step_04_handoff「测试命令与结果」已如实记录）；
   Step 06～09 每步都要新增测试目录并运行范围命令，不修则缺口持续放大。
2. **修复模式已有架构内先例且经实测验证**：Step 04 自建 `tests/persistence/persistence_helpers.py`、
   Step 05 自建 `tests/intent_engine/ie_helpers.py`，两个目录在任意参数组合下均可独立收集
   （1384 全绿中含这两目录），即提案 (a) 的精确形态。
3. **最小性经核实**：两个 conftest.py **只含 helper 函数/常量、零 pytest fixture**，
   共 11 个测试文件 `from conftest import ...`（validation 5 个、policy 6 个）；修复为纯机械
   重命名，不动产品代码、不动 `pyproject.toml`、不动 pytest 配置，测试语义零变化。
4. **选项 (b) 的代价**：把验收命令限制为"只用全量递归"会降低每步范围验证粒度（任何单目录
   修改都要全跑 1384+ 用例才能确认），与任务书逐范围验证的既有实践冲突；而冲突本身是
   import 层面的机械问题，修复成本极低、收益持续。

## 提案 3：Step 04 两个 additive 只读方法追认

### 提案原文摘要

Step 04 handoff「最小修订提案」：架构冻结表缺两个只读 getter 而任务书自身与下游步骤
需要——(1) `get_execution_revision(execution_revision_id) -> ExecutionRevision`：Step 07
`PromptEngine.compile` 冻结流程要"读当前 Intent/Execution revision"（target_model /
output_size），冻结表只有 intent 侧 getter；(2) `get_confirmation(confirmation_id) ->
ConfirmationRecord`：任务书必测场景"旧确认记录仍可审计"需要读回历史确认，
`is_confirmation_valid` 只返回 bool。两者均为新增只读方法、不改既有签名、不改写入语义、
有测试；如否决，删除后其余 21 个方法不受影响。

### 裁定：采纳 (a)（追认并写入冻结表）

两个方法追认为冻结公开面，已写入 ARCHITECTURE.md 第 4 节 Step 04 Repository 方法表
（读取区）。Repository 冻结方法总数由 21 个变为 23 个。

### 理由（原文依据）

1. **`get_execution_revision` 补的是冻结表疏漏，不是新需求**：ARCHITECTURE.md 第 4 节
   Step 07 compile 冻结流程原文要求"读当前 Intent/**Execution revision**"；任务书 06
   「给下一步的输入」原文列"Step 07 需要获得：……**ExecutionRevision**……"。冻结方法表
   只有 `get_intent_revision`，缺它 Step 07 只能绕过 Repository 协议直读 SQLite，破坏
   "Repository 是业务层唯一依赖面"的架构边界。
2. **`get_confirmation` 是任务书必测场景的直接前提**：`docs/task_books/mvp_v0.2/04_repository_state_machine.md`
   必测场景原文"**旧确认记录仍可审计**"；设计书运行保护要求"过期确认……不自动套用"，
   审计读回是前提。`is_confirmation_valid` 返回 bool 无法承载审计。
3. **任务书授权范围**：任务书 04 的 Repository 接口原文为"**至少提供**："9 个方法——
   additive 只读 getter 在任务书授权之内，不属于扩大架构。
4. **最小性已核实**：Step 04 handoff 记录两方法均有测试（`test_persistence_confirmation.py`
   场景 4 已用 `get_confirmation` 逐字段读回旧记录）；不修改任何既有签名与写入语义。

---

## 落地工单（两个最小任务，均为 Step 06 前置）

### 工单 A：Step 04 状态机补丁（裁定 1 落地）

**文件触碰范围（冻结，除此不得触碰任何其他文件）：**

- `visual_intent_agent/persistence/state_machine.py`
  - `ALLOWED_TRANSITIONS` frozenset 增加 `(WorkflowState.WAITING_CONFIRMATION, WorkflowState.UNDERSTANDING)`；
  - 模块 docstring 与迁移清单注释中"恰好覆盖任务书列出的 10 条迁移"更新为 11 条
    （注明 Rev.1 增补及本 decision 文档号）。
- `tests/persistence/test_persistence_state_machine.py`
  - `EXPECTED_TRANSITIONS` 增加同一对（`ILLEGAL_PAIRS` 为计算值，自动 32 → 31，无需手改）；
  - `assert len(ALLOWED_TRANSITIONS) == 10` → `== 11`；
  - 文件 docstring 与第 25 行注释同步更新；
  - 新增一条显式用例：`WAITING_CONFIRMATION → UNDERSTANDING` 可落库（确认页收到修改消息的路径）。
- `docs/handoffs/step_04_patch_001.md`（新建）：记录补丁内容与验证结果；
  **不得修改** `step_04_handoff.md` 原文。

**禁止**：改动 `repository.py`、`records.py`、`schema.sql`、其他测试目录、任务书 md、
ARCHITECTURE.md（架构方已改）、pyproject.toml / uv.lock / .env。

**验收命令（全部必须绿）**：

```bash
cd /home/change/projects/image_system
uv run pytest tests/persistence -q    # 174 → 175（+1 新用例）
uv run pytest -q                      # 全量绿
```

### 工单 B：conftest 微修复（裁定 2 落地）

**文件触碰范围（冻结，仅限 `tests/validation/**` 与 `tests/policy/**` 内的机械重命名）：**

- `tests/validation/`：
  - 新建 `validation_helpers.py`：现 `conftest.py` 全部 helper（`ALL_PATHS`、`TYPICAL_VALUES`、
    `resolve_path`、`path_snapshot`、`non_target_snapshot`、`full_intent`、`intent_with`、
    `with_resolution`、`with_pinned`、`evidence`、`context` 等）原样迁入；
  - `conftest.py` 收敛为仅含模块 docstring（说明 helper 已迁至 `validation_helpers.py`），
    不得再定义任何可导入符号（该目录本无任何 fixture，无需保留 fixture）；
  - 改 import 的 5 个测试文件：`test_validation_change_summary.py`、
    `test_validation_public_api.py`、`test_validation_reducer.py`、
    `test_validation_scenarios.py`、`test_validation_validator.py`
    （`from conftest import ...` → `from tests.validation.validation_helpers import ...`
    或与 `persistence_helpers` 一致的目录内 import 形式，按仓库既有惯例任选其一并全目录统一）。
- `tests/policy/`：
  - 新建 `policy_helpers.py`：现 `conftest.py` 全部 helper（`ALL_PATHS`、`NON_CONFLICTING_VALUES`、
    `empty_intent`、`intent_from`、`ready_intent`、`with_value`、`with_resolution`、`execution` 等）
    原样迁入；
  - `conftest.py` 同样收敛为仅 docstring；
  - 改 import 的 6 个测试文件：`test_policy_conflicts.py`、`test_policy_determinism.py`、
    `test_policy_question_selection.py`、`test_policy_resolution_legality.py`、
    `test_policy_rules.py`、`test_policy_scenarios.py`。
- 新建 `docs/handoffs/conftest_fix_001.md` 记录修复与验证结果（可选但建议）。

**禁止**：改任何产品代码（`visual_intent_agent/**`）、`pyproject.toml`、`uv.lock`、`.env`、
其他测试目录（`tests/persistence`、`tests/intent_engine` 等已合规，不得触碰）、测试断言本身
（只允许 import 行与 helper 搬运，测试体零改动）。

**修复后验收命令（三条全部必须绿，缺一不可）**：

```bash
cd /home/change/projects/image_system
uv run pytest -q                                  # 全量递归：1384 passed, 2 deselected（数不变）
uv run pytest tests/validation tests/policy -q    # 此前收集期 5 个 ImportError，修复后 924 passed
uv run pytest tests/persistence tests/domain tests/validation tests/policy tests/test_sanity.py -q
```

## 对 Step 06～09 实施 Agent 的具体指令

### Step 06（澄清、确认与 Workflow）

1. **可以直接依赖第 11 条迁移**：工单 A 落地后，`submit_message` 在 `WAITING_CONFIRMATION`
   收到用户消息时，按冻结流程"存 message → `transition_state(sid, UNDERSTANDING)`"即为合法
   迁移，**不得**在应用层拒绝该消息或先强制确认。工单 A 是 Step 06 状态相关集成的硬前置
   （Step 06 可在工单 A 完成前先行实现不依赖该迁移的部分）。
2. **"修改 Intent 后旧确认失效"必测场景的标准路径**：确认（状态 `WAITING_CONFIRMATION`）→
   `submit_message`（修改文本）→ 经第 11 条迁移回 `UNDERSTANDING` → IntentEngine → 新
   revision 落库 → `is_confirmation_valid(旧 confirmation_id)` 为 False、
   `get_confirmation(旧 id)` 仍可逐字段读回（审计）。"修改 output ratio 后旧确认失效"同理
   （execution revision 变更路径）。
3. **同状态幂等语义保持有效**：新会话首条消息 `UNDERSTANDING → UNDERSTANDING` 是幂等
   无操作（Step 04 已实现并验收），`submit_message` 每轮可无差别调用
   `transition_state(sid, UNDERSTANDING)`。
4. `confirm_current_intent` 仍仅在 `WAITING_CONFIRMATION` 可用；`get_confirmation` 可用于
   确认页展示与审计读回，**有效性判定一律只走 `is_confirmation_valid`**（不要自行重算
   binding）。
5. Step 06 新增测试目录（`tests/workflow/`、`tests/e2e/`）必须遵守 Rev.1 helper 命名规则：
   共享工具放 `workflow_helpers.py` 等唯一命名模块，**禁止** `from conftest import ...`。

### Step 07（PromptEngine）

- `repo.get_execution_revision(...)` 已追认为冻结公开面，直接用于读取 `target_model` /
  `output_size`；不得绕过 Repository 直读 SQLite。

### Step 08 / Step 09

- 无新增依赖面变化；Step 08 门禁（`WAITING_CONFIRMATION` + 有效确认）与 Step 09 的
  clarify 两步走（`WAITING_REVIEW → UNDERSTANDING → WAITING_CLARIFICATION`）均不受
  本次修订影响。
- Step 09 revise 路径（反馈后重新确认）同样受益于第 11 条迁移：`WAITING_REVIEW →
  UNDERSTANDING`（既有第 9 条）已覆盖反馈入口，无需额外迁移。

### 通用

- 全部 Step 的验收命令均按 Rev.1 约定执行：默认全量 `uv run pytest -q` 绿 **且** 本步相关
  多目录范围命令可按字面执行并绿。
- 本次修订未新增任何模块、状态或依赖；如发现新的冻结表缺口，继续按"最小修订提案 →
  架构方裁定"流程处理，不得自行扩大架构。
