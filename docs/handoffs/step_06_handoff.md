# Step 06 交接记录：澄清、确认与 Workflow

任务：Step 06 — 澄清、确认与 Workflow（集成 Step 04 Repository / 状态机与 Step 05
Interpreter / IntentEngine，实现单问题澄清、diff-first 确认摘要、revision/hash 绑定与
Hard Confirmation Gate；完成 P1 端到端测试；**不实现 PromptEngine、不调用图像 Provider**）

- 依据：`06_clarification_confirmation_workflow.md`（任务书）、`README.md`（10 条全局不变量）、
  `docs/ARCHITECTURE.md` 第 4 节「Step 06 — workflow」冻结表 / 5.1 ID 前缀 / 5.5 code 命名空间 /
  7 节测试策略（Rev.1）、`docs/handoffs/step_03/04/05_handoff.md`、
  `docs/handoffs/architecture_decision_001.md`（Rev.1 第 11 条迁移）、`step_04_patch_001.md`。
- 范围纪律：只新增 `visual_intent_agent/workflow/**`、`tests/workflow/**`、`tests/e2e/**`
  与本文件；未修改任何上游或既有文件（`domain/`、`validation/`、`policy/`、`persistence/`、
  `intent_engine/`、`providers/`、`config.py`、`pyproject.toml`、`uv.lock`、`.env`、根目录任务书、
  `README.md`、`docs/ARCHITECTURE.md`）；无新增依赖（只用 pydantic + stdlib 的 hashlib/json）。

---

## 完成内容

### 1. `workflow/questions.py` — QuestionBuilder 与 PendingQuestion（实现步骤 1、2）

| 公开名 | 形状 / 行为 |
|---|---|
| `PendingQuestion`（frozen + extra=forbid） | `question_id`、`session_id`、`target_path`、`reason`、`allow_delegate`、`allow_custom`、`suggested_values: tuple[str,...]`、`question_text`、`created_at`（tz-aware UTC）；方法 `to_spec() -> QuestionSpec`（带已渲染文本与非空 `question_id`） |
| `QuestionBuilder(llm: LLMProvider \| None = None)` | `build(spec: QuestionSpec, session_id: str) -> PendingQuestion`；默认**确定性模板**，注入 LLM 时仅改写 `question_text` |
| `render_question_text(spec)` | 确定性模板：路径 + 最多 3 个 `suggested_values` 选项 + 自定义输入入口（`allow_custom`）+ “you decide”委托入口（`allow_delegate`） |
| `MAX_SUGGESTED_OPTIONS = 3`、`QUESTION_ID_PREFIX = "qst"` | 选项上限与 ID 前缀 |

约束落地：

- `target_path` / `allow_delegate` / `allow_custom` / `suggested_values` **逐值取自 `QuestionSpec`**；
  LLM 只能改措辞（测试注入“改写目标路径/伪造选项/替用户作答”的 LLM，结构化字段不变）。
- LLM 调用失败（`ProviderError`）或返回空白 → 回退确定性模板，问题仍可问、状态零影响；
  其它异常不吞（`RuntimeError` 原样向上抛）。
- **系统推荐 ≠ 用户已选择**：`suggested_values` 只进入问题展示与 `PendingQuestion`，
  绝不写入 Intent；Intent 唯一变化来源仍是经 Validator 接受的 Candidate Delta。
- `question_id` 一律 `new_id("qst")`（ARCHITECTURE 5.1），`to_spec()` 保证非空
  （Step 05 `interpreter.pending_question_missing_id` 的前置条件）。

### 2. `workflow/confirmation.py` — diff-first 摘要、冻结哈希与 WorkflowError（实现步骤 3、4）

`ConfirmationSummary`（frozen）六要素与任务书一一对应：

| 任务书要素 | 字段 |
|---|---|
| 1 本轮修改内容 | `change_summary: ChangeSummary \| None` |
| 2 明确保留内容 | `pinned_paths: list[str]` |
| 3 用户授权系统决定 | `delegated_paths: list[str]` |
| 4 目标模型 | `target_model: str` |
| 5 输出比例 | `output_size: str` |
| 6 可展开的完整 Intent | `intent: VisualIntent` |

- `compute_summary_hash(summary)` **逐字冻结算法**：
  `sha256(json.dumps(summary.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()`。
- `build_confirmation_summary(intent_revision, execution_revision, change_summary=None)`：
  摘要 = **持久化状态的纯函数**（`delegated_paths`/`pinned_paths` 排序；`change_summary` 缺省由
  当前 `IntentRevision.applied_deltas` 确定性派生）。同一快照在任何时刻重算都得到同一 hash。
- `derive_change_summary(deltas)`：SET→`changed_paths`、CLEAR→`cleared_paths`、PIN/UNPIN→
  `pinned_paths`/`unpinned_paths`（按序去重），任一批量变化保守置 `confirmation_invalidated=True`。
- `WorkflowError(code, message)`：`.code` 仅允许三个冻结值，构造时自检（未知 code → `ValueError`）：
  `workflow.invalid_state`、`workflow.stale_revision`、`workflow.summary_hash_mismatch`
  （`WORKFLOW_ERROR_CODES` 导出）。

### 3. `workflow/service.py` — WorkflowService 四用例（实现步骤 5、6）

`WorkflowService(repo, intent_engine, question_builder, settings)`，签名严格照冻结表。

```python
def create_session(self) -> SessionSnapshot
def submit_message(self, session_id: str, text: str) -> SubmitMessageOutcome
def get_session(self, session_id: str) -> SessionSnapshot
def confirm_current_intent(self, session_id, intent_revision_id,
                           execution_revision_id, summary_hash) -> ConfirmationRecord
```

- **create_session**：`new_id("ses")` → `repo.create_session` → 初始 `ExecutionRevision`
  （`new_id("erev")`、`parent=None`、`target_model=settings.image_model`、
  `output_size="1024x1024"`）落库 → 返回快照（UNDERSTANDING，无初始 Intent revision）。
- **submit_message**（严格按冻结流程顺序）：
  1. 预检状态 ∈ {UNDERSTANDING, WAITING_CLARIFICATION, WAITING_CONFIRMATION}
     （否则 `workflow.invalid_state`，**零写入**）；非 str 文本 → `TypeError`；
  2. `append_message(role="user", new_id("msg"), utc_now())`；
  3. `transition_state(sid, UNDERSTANDING)`——新会话同状态为幂等；确认页消息走
     **Rev.1 第 11 条 `WAITING_CONFIRMATION → UNDERSTANDING`**，应用层不拒绝、不强制先确认；
  4. 从会话 payload 反序列化上一轮 `PendingQuestion` → `to_spec()`（非空 question_id）→
     `IntentEngine.resolve(IntentResolveRequest(current_intent, message_id, text,
     pending_question, available_message_ids))`；
  5. 有 `applied_deltas` → 组装 `IntentRevision(new_id("irev"), parent=当前快照)` 落库；
     无 delta 不产生新 revision；`VisualIntent.intent_id` 保持 `None`（P1 无消费方）；
  6. `ready_for_confirmation=True` → `clear_pending_question` + `WAITING_CONFIRMATION`，
     并附带 `confirmation_summary`；否则 `resolution.question` 非空 →
     `QuestionBuilder.build` + `save_pending_question(new_id("qst"), JSON payload)` +
     `WAITING_CLARIFICATION`；两者皆无（可恢复失败且当前 Intent 无待决问题）→ 不伪造问题、
     保持 UNDERSTANDING，failure issue 在 `resolution.issues` 中可见。
- **confirm_current_intent**（Hard Confirmation Gate 唯一入口）：
  1. 仅 `WAITING_CONFIRMATION`，否则 `workflow.invalid_state`；
  2. `intent_revision_id`/`execution_revision_id` 必须等于当前快照，否则 `workflow.stale_revision`
     （**迟到的确认无法套用到新 revision**，只认当前快照）；
  3. 按当前快照重算 `compute_summary_hash` 并与请求比对，不一致 →
     `workflow.summary_hash_mismatch`；
  4. 成功 → `save_confirmation(ConfirmationRecord(new_id("cnf"), …, summary_hash=重算值))`；
     **不变更状态**（保持 WAITING_CONFIRMATION），本步不生成 Prompt、不调用图像 Provider、
     不迁移到 GENERATING。
- **get_confirmation_summary(session_id)**（读取侧最小补充，见「已知限制 3」）：仅
  `WAITING_CONFIRMATION` 可用，返回按当前快照重算的同一摘要，供 `GET 会话` 重建确认视图。
- **“不合法输出最多修复一次”**：`_resolve_with_single_repair` 在 `resolution.issues` 命中
  可重试类别时用**同一请求**再解析一次（总计最多 `MAX_INTERPRETATION_ATTEMPTS = 2` 次）：
  `interpreter.unparseable_output*`（Step 05 一律 `retryable=True`）或
  `providers.errors.RETRYABLE_CODES`（rate_limited / timeout / network / server_error）；
  不可重试类别（`provider.auth` / `invalid_request` / `unparseable_response`）不重试。
  仍失败即可恢复 issue 返回，不猜测修复、不静默吞（Step 05 handoff「已知限制 3」）。

### 4. `SubmitMessageOutcome`（本步冻结，写入本 handoff）

```python
class SubmitMessageOutcome(BaseModel):  # frozen + extra=forbid
    snapshot: SessionSnapshot                   # 处理后的最新快照（状态/指针/消息）
    resolution: IntentResolution                # 本轮 resolve 结果（applied deltas + issues）
    pending_question: PendingQuestion | None    # 当前生效的问题（ready 时为 None）
    confirmation_summary: ConfirmationSummary | None  # 仅进入 WAITING_CONFIRMATION 时给出
```

客户端流程：`submit_message` → 展示 `pending_question.question_text`（用户回答）或
`confirmation_summary`（用户确认）→ 把 `compute_summary_hash(summary)` 交给
`confirm_current_intent`。hash 由服务端按同一纯函数重算比对，因此“客户端展示的摘要”与
“服务端确认的摘要”必然是同一份。

### 5. `workflow/__init__.py`

再导出公开面：`WorkflowService`、`SubmitMessageOutcome`、`PendingQuestion`、
`QuestionBuilder`、`render_question_text`、`ConfirmationSummary`、`WorkflowError`、
`compute_summary_hash`、`build_confirmation_summary`、`derive_change_summary`、
四个 `WORKFLOW_*` code、`DEFAULT_OUTPUT_SIZE`、`MAX_INTERPRETATION_ATTEMPTS`。

---

## 变更文件

新增（**未修改任何上游或既有文件**）：

- `visual_intent_agent/workflow/questions.py`
- `visual_intent_agent/workflow/confirmation.py`
- `visual_intent_agent/workflow/service.py`
- `visual_intent_agent/workflow/__init__.py`（原为 0 字节空文件，现再导出公开面）
- `tests/workflow/workflow_helpers.py`（唯一命名 helper 模块，遵守 Rev.1）
- `tests/workflow/test_workflow_questions.py`
- `tests/workflow/test_workflow_confirmation.py`
- `tests/workflow/test_workflow_service.py`
- `tests/workflow/test_workflow_public_api.py`
- `tests/e2e/p1_e2e_helpers.py`（e2e 目录内自包含 helper，遵守 Rev.1）
- `tests/e2e/test_p1_workflow_e2e.py`
- `docs/handoffs/step_06_handoff.md`（本文件）

未新增 `conftest.py`；两个新测试目录都不 `from conftest import ...`，也不跨目录 import
（pytest 只把每个测试目录自身加入 `sys.path`，e2e helper 是同内容目录内副本）。

未触碰：根目录任务书 md、`README.md`、`AGENT_DISPATCH_PROMPTS.md`、`api.md`、
`docs/ARCHITECTURE.md`、`visual_intent_agent/{domain,validation,policy,persistence,
intent_engine,providers,config.py}`、`tests/{domain,validation,policy,persistence,
providers,intent_engine,smoke,fixtures}`、`tests/conftest.py`、`tests/test_sanity.py`、
`pyproject.toml`、`uv.lock`、`.env`。**无新增依赖。**

---

## 公开接口

```python
from visual_intent_agent.workflow import (
    WorkflowService, SubmitMessageOutcome,
    PendingQuestion, QuestionBuilder, render_question_text,
    ConfirmationSummary, WorkflowError,
    compute_summary_hash, build_confirmation_summary, derive_change_summary,
    WORKFLOW_ERROR_CODES, WORKFLOW_INVALID_STATE, WORKFLOW_STALE_REVISION,
    WORKFLOW_SUMMARY_HASH_MISMATCH, DEFAULT_OUTPUT_SIZE, MAX_INTERPRETATION_ATTEMPTS,
)
from visual_intent_agent.workflow.questions import MAX_SUGGESTED_OPTIONS, QUESTION_ID_PREFIX
```

### 本步新增 Issue / Error code（命名空间 `workflow.*`，ARCHITECTURE 5.5）

| code | 承载 | 触发条件 |
|---|---|---|
| `workflow.invalid_state` | `WorkflowError` | `confirm_current_intent` / `get_confirmation_summary` 不在 `WAITING_CONFIRMATION`；`submit_message` 不在 {UNDERSTANDING, WAITING_CLARIFICATION, WAITING_CONFIRMATION}；会话缺少可摘要的 revision |
| `workflow.stale_revision` | `WorkflowError` | 确认请求的 intent/execution revision ≠ 当前快照 |
| `workflow.summary_hash_mismatch` | `WorkflowError` | 请求 `summary_hash` ≠ 按当前快照重算值（含“迟到/被篡改/hash 非字符串”） |

（`Issue` 不新增 code：可恢复失败的 issue 直接沿用 Step 05 的 `interpreter.*` / `provider.*`。）

### 对 ARCHITECTURE.md 的最小修订建议（未自行修改文档）

1. **`Issue` 不暴露 `retryable`**：Step 05 handoff 要求“按 `.retryable` 决定重试”，但
   `IntentResolution.issues` 只有 `code`。本步按冻结 code 语义推断（`interpreter.unparseable_output*`
   视为可重试；`provider.*` 用 `providers.errors.RETRYABLE_CODES`），语义与 Step 05 一致。
   建议架构在 5.6 或 Step 06 冻结表注明该口径，或未来给 `Issue` 增加可选 `retryable` 字段。
2. **Step 06 冻结表可补两行**：(a) `WorkflowService.get_confirmation_summary`（读取侧，
   `GET 会话`重建确认视图所需，见已知限制 3）；(b) `submit_message` 的“最多一次重试”说明
   （设计书「不合法输出最多修复一次」的落地位置）。
3. **`ConfirmationSummary.change_summary` 的来源**：`ChangeSummary` 不在 `IntentResolution`
   公开面内，本步由 `IntentRevision.applied_deltas` 确定性派生（保证 hash 可由持久化状态重算）。
   建议架构在 Step 06 冻结表写明该派生规则。

---

## 测试命令与结果

```text
$ cd /home/change/projects/image_system

# 基线（实施前）
$ uv run pytest -q
1385 passed, 2 deselected

# 全量（基线 1385 + 本步 87）
$ uv run pytest -q
1472 passed, 2 deselected in 3.50s

# 本步范围（逐目录 + 合并范围命令，可按字面执行）
$ uv run pytest tests/workflow -q
74 passed in 0.31s
$ uv run pytest tests/e2e -q
13 passed in 0.18s
$ uv run pytest tests/workflow tests/e2e -q
87 passed in 0.41s

# Rev.1 上游多目录验收命令（未回归）
$ uv run pytest tests/persistence tests/domain tests/validation tests/policy tests/test_sanity.py -q
1247 passed in 2.84s

# 跨进程确定性（frozenset 排序 + JSON 序列化稳定）
$ PYTHONHASHSEED=0 / 7 / 424242  uv run pytest tests/workflow tests/e2e -q
-> 均 87 passed
$ 同一确认快照（多路径 delegated/pinned）的 summary hash 在三个 seed 下均为
  a71a20de308463890ce31a0db17e83225055651977942e6bf82959a4da45f037

# 依赖边界（干净子进程导入 visual_intent_agent.workflow）
-> 未拉入 visual_intent_agent.prompt_engine / .generation / .providers.image /
   .providers.openai_image / .providers.fake_image / httpx；无 Prompt/图像生成逻辑。
```

全部默认测试离线：只用 `FakeLLMProvider` + `tmp_path` SQLite，零网络访问、零真实凭据
（本步新增 11 个文件逐一对照 `.env` 真实 key 全文 grep，0 命中；无任何疑似 key 前缀的字面量）。

### 任务书「必测场景」→ 测试映射（10/10 + 完整剧本）

| # | 场景 | 测试 |
|---|---|---|
| 1 | 空需求进入澄清，不进入确认 | `tests/e2e/test_p1_workflow_e2e.py::test_scenario_01_empty_requirement_enters_clarification_not_confirmation` |
| 2 | 一轮只返回最高优先级问题 | `::test_scenario_02_one_round_returns_only_the_highest_priority_question` |
| 3 | 回答后继续下一个 unresolved decision | `::test_scenario_03_answering_a_question_continues_with_the_next_unresolved` |
| 4 | Ready 后状态为 `WAITING_CONFIRMATION` | `::test_scenario_04_ready_moves_to_waiting_confirmation` |
| 5 | 未确认时无法进入生成状态 | `::test_scenario_05_unconfirmed_session_cannot_enter_generating`（`transition_state(GENERATING)` 被拒）+ `tests/workflow/test_workflow_service.py::test_confirm_is_the_only_gate_and_does_not_generate`（源码级无 `WorkflowState.GENERATING`） |
| 6 | 确认旧 revision 被拒绝 | `::test_scenario_06_confirming_an_old_revision_is_rejected`（+ `tests/workflow/…::test_confirm_with_a_stale_intent_revision_is_rejected` / `…stale_execution_revision…`） |
| 7 | 修改 Intent 后旧确认失效 | `::test_scenario_07_modifying_the_intent_invalidates_the_old_confirmation` |
| 8 | 修改 output ratio（新 ExecutionRevision）后旧确认失效 | `::test_scenario_08_changing_the_output_ratio_invalidates_the_old_confirmation` |
| 9 | summary hash 不匹配时拒绝确认 | `::test_scenario_09_summary_hash_mismatch_is_rejected` |
| 10 | 系统推荐选项不被自动写入 Intent | `::test_scenario_10_recommended_options_are_not_written_into_the_intent` |
| — | 完整多轮剧本（模糊需求→多轮单问题→Ready→确认；确认页修改→UNDERSTANDING→旧确认失效→重新 Ready→再确认；确认后零 Prompt/图片 Artifact） | `::test_p1_full_multiturn_play_from_vague_request_to_reconfirmation` |
| — | 状态序列确定且逐步落库（重开库读回同一快照） | `::test_p1_state_sequence_is_persisted_across_reads` |
| — | 「交给系统决定」绑定当前可委托问题、授权 ≠ 赋值 | `::test_p1_delegated_answer_is_bound_to_the_pending_question` |

另覆盖（`tests/workflow/` 74 例）：模板确定性/选项上限/回退、结构化字段不可被 LLM 改写、
PendingQuestion 往返与 tz 约束、六要素与冻结哈希逐字核对、hash 对 intent/output_size/
target_model 敏感且对 revision ID 无关、`WorkflowError` 三 code 命名空间、
revision parent 链与“无 delta 不建 revision”、pending question 存取与 ready 时清空、
可重试失败重试恰好一次 / 不可重试不重试 / 仍失败转澄清、状态拒绝时零写入、
非字符串消息拒绝、`intent_id` 保持 None、公开签名与依赖边界、无明文凭据。

---

## 已知限制

1. **`submit_message` 只接受 3 个状态**（UNDERSTANDING / WAITING_CLARIFICATION /
   WAITING_CONFIRMATION）。`GENERATING` / `FAILED` / `COMPLETED` 一律
   `workflow.invalid_state`（预检在写 message 之前，零写入）；`WAITING_REVIEW` 不接受，
   其反馈入口是 Step 09 的 `ReviewService.submit_feedback`（冻结表）。设计书提到的
   “FAILED 时新修改使失败运行快照失效，回到 UNDERSTANDING”需要
   `FAILED → UNDERSTANDING` 迁移，而 Rev.1 状态表没有该条（只有 `FAILED → GENERATING` /
   `FAILED → WAITING_CONFIRMATION`），本步**不自行扩充迁移**，留给 Step 08/架构裁定。
2. **确认页消息解析失败（不可恢复、且当前 Intent 已无待决问题）时状态停留在
   UNDERSTANDING**：无 delta → 不产生新 revision、旧 ConfirmationRecord 的绑定仍未变
   （`is_confirmation_valid` 为 True），但确认用例仅在 `WAITING_CONFIRMATION` 可用，因此用户
   需再发一条消息重新 Ready（同一摘要与 hash）。不伪造问题、不猜测修复、不静默吞 issue。
3. **`get_confirmation_summary` 是冻结四用例之外的 1 个只读方法**：`SessionSnapshot`
   （Step 04 冻结模型）不承载确认摘要，而设计书 `GET /sessions/{id}` 要返回“问题/确认摘要”。
   该方法只按当前两类 revision 重算（与 `confirm_current_intent` 同一纯函数），不写任何状态。
   如需严格四用例，可删除；`submit_message` 的 `confirmation_summary` 已覆盖常规流程。
4. **“最多修复一次”是同一请求的再次解析**，不是新增修复提示词（Step 05 未提供修复入口，
   架构也未冻结该接口）。真实 Provider 下第二次调用可能得到不同输出；Fake 下等价于确定性重放。
5. **`change_summary` 由当前 `IntentRevision.applied_deltas` 派生**（Step 02 `ChangeSummary`
   不在 `IntentResolution` 公开面内）。因此“本轮修改内容”在“无 delta 但重新 Ready”的一轮会
   显示上一个 revision 的 diff；这是为 hash 可重算（同一快照 → 同一 hash）作的可复现取舍。
6. **`VisualIntent.intent_id` 保持 `None`**：dispatch 明确 P1 无消费方；本步未赋值，
   也未提出修订（如未来需要，建议在 Step 07 落地前由架构裁定赋值时机与 ID 前缀）。
7. **无 HTTP/FastAPI 层**：仓库无 FastAPI，任务书允许“如已有则提供最薄路由”；应用层四个
   用例即对外接口（与 ARCHITECTURE 第 2 节一致），本步未新增依赖或 Web 框架。
8. **QuestionBuilder 的 LLM 改写未做“禁止泄露系统内部路径”的文本后处理**：`question_text`
   可能包含路径名（模板本就包含），且结构化字段始终来自 `QuestionSpec`；如未来要求对用户隐藏
   内部路径名，需要在 Step 06 内做一次显式文案策略（非本步范围）。

---

## 对下一步的输入

### Step 07（PromptEngine 与 PromptArtifact）

读取"已确认 Intent / ExecutionRevision / 有效 ConfirmationRecord"的完整应用层路径
（全部经 `Repository`，禁止绕过协议直读 SQLite）：

```python
from visual_intent_agent.workflow import WorkflowService

snapshot = service.get_session(session_id)                  # 当前状态与指针
# 1) 生成门禁：必须 WAITING_CONFIRMATION 且最新确认有效
assert snapshot.workflow_state is WorkflowState.WAITING_CONFIRMATION
assert snapshot.latest_confirmation_id is not None
assert repo.is_confirmation_valid(snapshot.latest_confirmation_id) is True

# 2) 有效 ConfirmationRecord（绑定 intent/execution revision + summary_hash）
record = repo.get_confirmation(snapshot.latest_confirmation_id)

# 3) 已确认的 Intent / ExecutionRevision 快照（target_model / output_size 在此）
intent_revision = repo.get_intent_revision(record.intent_revision_id)
execution_revision = repo.get_execution_revision(record.execution_revision_id)
confirmed_intent = intent_revision.intent

# 4) 确认页要展示的内容（当前快照重算；仅 WAITING_CONFIRMATION 可用）
summary = service.get_confirmation_summary(session_id)
summary_hash = compute_summary_hash(summary)                # 与 record.summary_hash 一致
```

- `PromptCompileRequest(session_id, confirmation_id)` 的 `confirmation_id` 应取
  `snapshot.latest_confirmation_id`（或确认时返回的 `record.confirmation_id`）；
  `repo.is_confirmation_valid(...)` 由其内部复核，**不要**自行重算 binding_hash。
- `WorkflowService` **不会**迁移到 `GENERATING`，也不会写任何 Prompt/Generation Artifact：
  状态迁移到 `GENERATING` 是 Step 08 `GenerationPipeline.generate` 的职责
  （`WAITING_CONFIRMATION` + 有效确认 → `GENERATING`）。
- `confirmation_summary.delegated_paths` 即需要局部实现并复用 Realization 的路径集合；
  `pinned_paths` 即保持约束。

### Step 09（ReviewService）

- 反馈入口用 `WAITING_REVIEW → UNDERSTANDING`（既有第 9 条）→ 走本步
  `submit_message` 同构的解析/落库路径 → 重新 `WAITING_CONFIRMATION` 再确认；
  `WAITING_CONFIRMATION → UNDERSTANDING`（Rev.1 第 11 条）已在 `submit_message` 中落地并有测试。
- `workflow/review.py` 为新增文件，不改本步既有文件（`questions.py` / `confirmation.py` /
  `service.py` / `__init__.py` 的公开面可被追加再导出）。

### 通用

- `tests/workflow/` 与 `tests/e2e/` 已建立：共享工具放唯一命名模块
  （`workflow_helpers.py` / `p1_e2e_helpers.py`），无 `from conftest import ...`，无跨目录 import。
- 新发现冻结表缺口继续走“最小修订提案 → 架构方裁定”（见本文件「公开接口」小节）。

---

## 验收条件逐条核对

| # | 任务书验收条件 | 核对结果 |
|---|---|---|
| 1 | Hard Confirmation Gate 无绕过路径 | **是**。确认唯一入口 `confirm_current_intent`：仅 `WAITING_CONFIRMATION`（`workflow.invalid_state`）+ 双 revision 等于当前快照（`workflow.stale_revision`）+ 重算 hash 比对（`workflow.summary_hash_mismatch`）；`workflow` 包源码无 `WorkflowState.GENERATING`（测试钉住），干净子进程导入不拉入 `prompt_engine`/`generation`/图像 Provider；状态机从 UNDERSTANDING/WAITING_CLARIFICATION 直接转 GENERATING 被拒；未确认时 `latest_confirmation_id` 恒为 None。 |
| 2 | Confirmation 精确绑定当前两类 revision | **是**。`ConfirmationRecord` 同时绑定 intent + execution revision 与 `summary_hash`；hash 由当前快照纯函数重算；两者任一变化（新 `IntentRevision` 或新 `ExecutionRevision`/output_size）都使旧确认 `is_confirmation_valid=False`，且迟到确认被 `workflow.stale_revision` 拒绝（场景 6/7/8/9）。 |
| 3 | 所有状态迁移合法且落库 | **是**。迁移只经 `Repository.transition_state`：UNDERSTANDING 幂等、`UNDERSTANDING→WAITING_CLARIFICATION/WAITING_CONFIRMATION`、`WAITING_CONFIRMATION→UNDERSTANDING`（Rev.1 第 11 条）；非法状态下 `submit_message` 预检零写入；e2e 断言完整状态序列并可重开库读回同一快照。 |
| 4 | P1 从多轮澄清到确认可以完整运行 | **是**。`test_p1_full_multiturn_play_from_vague_request_to_reconfirmation`：模糊需求 → 6 个后续单问题 → Ready → 确认 → 确认页修改（观察点证明先回 UNDERSTANDING）→ 旧确认失效 → 重新 Ready → 再确认；另有全部 10 条场景测试与状态序列测试。 |
| 5 | 尚未出现 Prompt 或图片生成逻辑 | **是**。`workflow/**` 不含 PromptEngine / GenerationPipeline / ImageProvider 引用；干净子进程导入不拉入 `prompt_engine`/`generation`/`providers.image`/`httpx`；e2e 断言确认后 `prompt_artifacts` / `generation_artifacts` / `feedback_results` / `realization_states` 四表计数均为 0；`confirm_current_intent` 不变更状态、不写任何 Artifact。 |

## 是否满足验收条件

**是。** 5 条验收条件逐条通过；任务书 10 条「必测场景」全部有显式测试映射，另加完整多轮
剧本、委托路径与状态落库测试；本步范围 87 个用例全绿（`tests/workflow` 74 + `tests/e2e` 13），
上游既有测试未回归（多目录 1247 passed），全量 `uv run pytest -q` → **1472 passed, 2 deselected**
（零网络、零真实凭据）；三个 `PYTHONHASHSEED` 下逐字节稳定；未实现任何后续步骤能力
（无 Prompt 编译、无图像调用、无前端、无批量提问、无 `GENERATING` 迁移），未修改任何上游
或既有文件，未新增依赖，源码/测试/本文件中无明文 API key。
