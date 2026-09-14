# Step 04 补丁交接 001：状态机第 11 条迁移 + conftest 微修复

任务：执行 `docs/handoffs/architecture_decision_001.md` 落地工单 A（Step 04 状态机补丁）
与工单 B（tests/validation、tests/policy 的 conftest 机械重命名）。两项均为 Step 06 前置。

- 依据：`docs/handoffs/architecture_decision_001.md`（架构裁定 Rev.1）、`docs/ARCHITECTURE.md`
  第 4 节 Step 04 冻结表（迁移 11 条、Repository 23 方法）与第 7 节测试策略（Rev.1 helper
  命名规则与验收命令约定）、`docs/handoffs/step_04_handoff.md`（现有实现）。
- 范围纪律：严格照冻结文件清单执行，未触碰产品代码、`pyproject.toml`、`uv.lock`、`.env`、
  其他测试目录，未修改 `step_04_handoff.md` 原文，未修改 `docs/ARCHITECTURE.md`（架构方已改）。

## 完成内容

### 工单 A：状态机补丁（`WAITING_CONFIRMATION → UNDERSTANDING` 第 11 条）

1. `visual_intent_agent/persistence/state_machine.py`
   - `ALLOWED_TRANSITIONS` frozenset 新增
     `(WorkflowState.WAITING_CONFIRMATION, WorkflowState.UNDERSTANDING)`，位于
     `WAITING_CONFIRMATION -> GENERATING` 之后，共 **11 条**；原 10 条逐条不变。
   - 模块 docstring "恰好覆盖任务书列出的 10 条迁移" → "恰好 11 条：任务书'至少覆盖'的
     10 条 + Rev.1 增补"，并注明依据文档 `architecture_decision_001.md` 提案 1。
   - 迁移清单注释头更新为 "10 条 + Rev.1 增补第 11 条"，并追加第 11 条注释行。
   - 未改动 `WorkflowState`（仍恰好 7 个持久状态）、`InvalidStateTransitionError`、
     `_state_label`、`__all__`。
2. `tests/persistence/test_persistence_state_machine.py`
   - `EXPECTED_TRANSITIONS` 追加同一对（autouse 参数化用例自动覆盖 11 条合法迁移）。
   - `ILLEGAL_PAIRS` 为计算值（`ALL_PAIRS - EXPECTED_TRANSITIONS`，排除同状态），
     由 32 条自动变为 31 条，无需手改；非法组合参数化总数仍为 42（10+32 → 11+31）。
   - `assert len(ALLOWED_TRANSITIONS) == 10` → `== 11`。
   - 文件 docstring 与 `EXPECTED_TRANSITIONS` 上方注释同步为 11 条。
   - **新增 1 条显式用例**
     `test_rev1_confirmation_page_message_returns_to_understanding`：断言
     (a) 原 10 条仍是 `ALLOWED_TRANSITIONS` 的真子集且表长 11（纯 additive）；
     (b) 从 `WAITING_CONFIRMATION` 出发的未冻结目标（`WAITING_CLARIFICATION` /
     `WAITING_REVIEW` / `COMPLETED` / `FAILED`）仍抛 `InvalidStateTransitionError` 且零写入；
     (c) `WAITING_CONFIRMATION → UNDERSTANDING` 可落库（确认页收到修改消息的路径）。
   - 测试体其余零改动；未新增/删除其他用例。

用例数变化：`10 条合法 + 32 条非法 = 42` 参数化用例数不变，仅新增 1 条显式用例，故
`tests/persistence` 由 **174 → 175**。

### 工单 B：conftest 机械重命名（测试基建修复）

- `tests/validation/`：
  - 新建唯一命名模块 `validation_helpers.py`：原 `conftest.py` 的全部 helper 与常量
    （`ALL_PATHS`、`TYPICAL_VALUES`、`resolve_path`、`path_snapshot`、
    `non_target_snapshot`、`full_intent`、`_facet_models`、`intent_with`、
    `with_resolution`、`with_pinned`、`evidence`、`context`、`delta`、`set_delta`、
    `clear_delta`、`pin_delta`、`unpin_delta`、`issue_codes`、`rejected_codes`）
    **逐字搬移**，仅模块 docstring 更新为"唯一命名模块 + 迁移依据"。
  - `conftest.py` 收敛为**仅含模块 docstring**（AST 校验：0 个函数/类、0 个赋值、0 个 import），
    不再定义任何可导入符号；该目录原本零 pytest fixture/钩子，故无 fixture 需要保留。
  - 5 个测试文件由 `from conftest import ...` 改为 `from validation_helpers import ...`
    （与既有 `persistence_helpers` / `ie_helpers` 的目录内 import 惯例一致）：
    `test_validation_change_summary.py`、`test_validation_public_api.py`、
    `test_validation_reducer.py`、`test_validation_scenarios.py`、
    `test_validation_validator.py`。
- `tests/policy/`：
  - 新建唯一命名模块 `policy_helpers.py`：原 `conftest.py` 的全部 helper 与常量
    （`ALL_PATHS`、`NON_CONFLICTING_VALUES`、`READY_VALUES`、`resolve_path`、
    `path_snapshot`、`intent_from`、`empty_intent`、`full_intent`、`ready_intent`、
    `with_value`、`with_resolution`、`execution`、`unresolved_map`、`issue_codes`、
    `conflict_codes`）逐字搬移，仅 docstring 更新。
  - `conftest.py` 同样收敛为**仅 docstring**（AST 校验同上）。
  - 6 个测试文件改为 `from policy_helpers import ...`：`test_policy_conflicts.py`、
    `test_policy_determinism.py`、`test_policy_question_selection.py`、
    `test_policy_resolution_legality.py`、`test_policy_rules.py`、
    `test_policy_scenarios.py`。
- `test_policy_golden.py`、`test_policy_models.py`、`test_policy_public_api.py`、
  `test_validation_models.py` 原本未 import conftest，未改动。
- 纯机械搬移：**未改动任何断言逻辑**（测试体零改动，仅 import 行与 helper 所在模块变化）；
  未改动产品代码、`pyproject.toml`、`uv.lock`、`.env`、其他测试目录。

## 变更文件

工单 A（2 改 + 1 新建文档）：
- 修改 `visual_intent_agent/persistence/state_machine.py`
- 修改 `tests/persistence/test_persistence_state_machine.py`

工单 B（2 新建 + 2 收敛 + 11 个 import 行）：
- 新建 `tests/validation/validation_helpers.py`
- 新建 `tests/policy/policy_helpers.py`
- 修改 `tests/validation/conftest.py`（仅 docstring）
- 修改 `tests/policy/conftest.py`（仅 docstring）
- 修改 `tests/validation/test_validation_{change_summary,public_api,reducer,scenarios,validator}.py`（仅 import 行）
- 修改 `tests/policy/test_policy_{conflicts,determinism,question_selection,resolution_legality,rules,scenarios}.py`（仅 import 行）

交接文档（本文件）：
- 新建 `docs/handoffs/step_04_patch_001.md`

未触碰（确认范围外零改动）：`visual_intent_agent/persistence/repository.py`、`records.py`、
`schema.sql`、`visual_intent_agent/**` 其他模块、`tests/persistence/**`、`tests/domain/**`、
`tests/providers/**`、`tests/intent_engine/**`、`tests/{workflow,e2e,generation,feedback,prompt_engine,smoke}/**`、
`tests/conftest.py`、`tests/test_sanity.py`、`pyproject.toml`、`uv.lock`、`.env`、
`docs/ARCHITECTURE.md`、`docs/handoffs/step_04_handoff.md` 及其他既有 md。

## 公开接口

### 产品接口变化（唯一一处，纯 additive）

```python
# visual_intent_agent/persistence/state_machine.py
ALLOWED_TRANSITIONS: frozenset[tuple[WorkflowState, WorkflowState]]  # 10 条 -> 11 条
```

Rev.1 冻结迁移表（11 条，`*` 为本次新增第 11 条）：

```text
UNDERSTANDING          -> WAITING_CLARIFICATION
UNDERSTANDING          -> WAITING_CONFIRMATION
WAITING_CLARIFICATION  -> UNDERSTANDING
WAITING_CONFIRMATION   -> GENERATING
GENERATING             -> WAITING_REVIEW
GENERATING             -> FAILED
FAILED                 -> GENERATING
FAILED                 -> WAITING_CONFIRMATION
WAITING_REVIEW         -> UNDERSTANDING
WAITING_REVIEW         -> COMPLETED
WAITING_CONFIRMATION   -> UNDERSTANDING   (* Rev.1 第 11 条)
```

- `WorkflowState` 不变（恰好 7 个持久状态）；`InvalidStateTransitionError`
  （`.code = "persistence.invalid_state_transition"`）不变；`Repository` 协议与 23 个方法
  完全不变（本补丁不触及）。
- 同状态幂等语义不变；`COMPLETED` 仍为终态；未新增状态、模块或依赖。

### 测试侧接口变化（非产品面）

- 新增模块名 `tests.validation.validation_helpers`、`tests.policy.policy_helpers`
  （仅测试导入路径；符号名与函数签名逐字不变）。
- `tests/validation/conftest.py`、`tests/policy/conftest.py` 不再提供任何可导入符号。

## 测试命令与结果

```text
$ cd /home/change/projects/image_system

# 工单 A 范围（架构裁定要求 174 -> 175）
$ uv run pytest tests/persistence -q
175 passed in 0.64s

# 验收命令 1：全量（基线 1384 passed, 2 deselected；+1 = 工单 A 新增显式用例）
$ uv run pytest -q
1385 passed, 2 deselected in 3.36s

# 验收命令 2：修复前收集期 5 个 ImportError；修复后
$ uv run pytest tests/validation tests/policy -q
924 passed in 2.03s

# 验收命令 3：Step 04 handoff 中此前无法按字面执行的多目录范围命令
$ uv run pytest tests/persistence tests/domain tests/validation tests/policy tests/test_sanity.py -q
1247 passed in 2.83s
```

- 三条验收命令全部通过，零失败、零错误、零跳过（2 deselected 为默认离线的 `@pytest.mark.smoke`，
  与基线一致）；零网络访问。
- 修复前复现：`uv run pytest tests/validation tests/policy -q` →
  `ImportError: cannot import name 'TYPICAL_VALUES' from 'conftest' (.../tests/policy/conftest.py)`，
  5 个 validation 测试模块收集失败（与 architecture_decision_001.md 记录一致）。

## 已知限制

1. **`tests/persistence/` 中两处文字说明已过时但未改**（越界）：
   `tests/persistence/persistence_helpers.py` 与 `tests/persistence/conftest.py` 的模块 docstring
   仍写"上游 `tests/validation` 与 `tests/policy` 都使用 `from conftest import ...`"。二者只是
   **散文说明**（非可执行 import），不影响行为；因工单 B 禁改其他测试目录，保留原文，待后续
   有授权的清理时同步。
2. **`docs/handoffs/step_04_handoff.md` 的「已知限制 1」与「最小修订提案 3」、测试命令小节
   现已过时**（其中"10 条迁移""多目录命令无法按字面执行"等表述已被本补丁与 Rev.1 取代）。
   按裁定不得修改该文件原文，故以本补丁记录为准。
3. `docs/handoffs/architecture_decision_001.md` 工单 B 提到的
   `docs/handoffs/conftest_fix_001.md` 为"可选但建议"。按本次派发要求，工单 A/B 结果**合并**
   记入本文件，未另建 `conftest_fix_001.md`。
4. helper 模块采用**目录内 import**（`from validation_helpers import ...` /
   `from policy_helpers import ...`），依赖 pytest `pythonpath = ["."]` 与 rootdir
   `prepend` 机制把测试目录加入 `sys.path`（`tests/**` 无 `__init__.py`）。这与既有
   `persistence_helpers` / `ie_helpers` 惯例一致；若未来给测试目录加 `__init__.py`，
   需同步改为包路径 import（`from tests.validation.validation_helpers import ...`）。
5. 本补丁不新增能力：仅放宽一条状态迁移 + 测试基础设施重命名；Step 06 的
   `submit_message` 集成、确认失效流程等仍归 Step 06 实现与验收。

## 对下一步的输入

### Step 06（澄清、确认与 Workflow）—— 硬前置已就绪

- `WAITING_CONFIRMATION → UNDERSTANDING` 现为合法迁移，可直接依赖：
  确认页收到用户消息时 `append_message(...)` → `transition_state(sid, WorkflowState.UNDERSTANDING)`
  合法落库，**不得**在应用层拒绝该消息或先强制确认。
- "修改 Intent / output ratio 后旧确认失效"标准路径可用：`WAITING_CONFIRMATION` →
  `submit_message` → 第 11 条迁移回 `UNDERSTANDING` → IntentEngine → 新 revision 落库 →
  `is_confirmation_valid(旧 id) is False`，`get_confirmation(旧 id)` 仍可审计读回。
- 同状态幂等语义保持有效：`transition_state(sid, UNDERSTANDING)` 每轮可无条件调用。
- `confirm_current_intent` 仍仅在 `WAITING_CONFIRMATION` 可用；有效性判定只走
  `is_confirmation_valid`。
- 新增测试目录（`tests/workflow/`、`tests/e2e/`）必须遵守 Rev.1 命名规则：共享工具放
  `workflow_helpers.py` 等唯一命名模块，**禁止** `from conftest import ...`。

### Step 04 相关

- Repository 冻结公开面不变（23 方法，含 Rev.1 追认的 `get_execution_revision` /
  `get_confirmation`），无需任何适配。

### 通用

- 多目录范围验收命令现已可按字面执行（`uv run pytest tests/validation tests/policy -q`
  与 `tests/persistence tests/domain tests/validation tests/policy tests/test_sanity.py`
  均绿），Step 06～09 的逐范围验证链路已打通。
- 新增测试目录一律遵循"唯一命名 helper 模块 + conftest 只放 fixture/钩子"。
- 如发现新的冻结表缺口，继续走"最小修订提案 → 架构方裁定"，不得自行扩大架构。

## 是否满足验收条件

是。逐条核对：

| # | 验收项 | 结果 |
|---|---|---|
| A1 | `ALLOWED_TRANSITIONS` 恰好 11 条，第 11 条为 `WAITING_CONFIRMATION → UNDERSTANDING`，原 10 条不变 | 是 |
| A2 | 计数断言 `== 10` 改 `== 11`；`ILLEGAL_PAIRS` 自动 32 → 31；新增 1 条显式用例（合法落库 / 原 10 条不变 / 非法仍拒绝） | 是 |
| A3 | `uv run pytest tests/persistence -q` 全绿，174 → **175 passed** | 是 |
| B1 | 共享 helper 迁入 `validation_helpers.py` / `policy_helpers.py`，`conftest.py` 仅 docstring（无任何可导入符号） | 是 |
| B2 | 11 个测试文件改用新唯一命名模块 import，纯机械搬移、测试体零改动 | 是 |
| B3 | 未改产品代码 / `pyproject.toml` / `uv.lock` / `.env` / 其他测试目录 / 既有 md | 是 |
| B4 | 验收命令 1 `uv run pytest -q` → **1385 passed, 2 deselected** | 是 |
| B5 | 验收命令 2 `uv run pytest tests/validation tests/policy -q` → **924 passed**（此前 5 个收集期 ImportError） | 是 |
| B6 | 验收命令 3 `uv run pytest tests/persistence tests/domain tests/validation tests/policy tests/test_sanity.py -q` → **1247 passed** | 是 |

无明文 API key：源码、测试、helper 模块、本交接文档均不含任何凭据/`sk-` 形态字符串。
