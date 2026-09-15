# MVP v0.3 更改书 002 · 工包 B（可恢复失败）交接记录

- 日期：2026-09-15（R2 构建轮）
- 工包：B 可恢复失败（Flash B）
- 依据：`docs/task_books/mvp_v0.3/REVISION_002_BUILD_FIRST.md`（文件级重新分工 / 实施边界）与
  `REVISION_001_SHORTEST_PATH.md` 的 R1-C（超时恢复与重试记录）、R1-A（数量 / 地点语义边界）。
- Git：HEAD `6f7ca17`（未提交、dirty 状态保留，本工包**未 commit**；未回滚任何既有成果）。
- 写入范围（严格遵守）：`visual_intent_agent/workflow/service.py`、`review.py`、
  **新增** `workflow/models.py`、`visual_intent_agent/feedback/**`、
  `tests/workflow/**`、`tests/feedback/**`。未触碰 `evaluation/**`、CLI、`policy/`、
  `intent_engine/`、`persistence/`、`generation/`、`pyproject.toml`、`.env`、`api.md`。

> 说明：任务书列出的 `workflow/models.py` 在改动前**并不存在**（workflow 包只有
> `confirmation.py` / `questions.py` / `service.py` / `review.py` / `__init__.py`）。
> 本工包新建了该模块，承载跨两个用例共享的"可恢复失败"纯分类函数。

---

## 1. 实际实现

### 1.1 新模块 `visual_intent_agent/workflow/models.py`（纯分类，无副作用）

- `is_engine_failure_code(code)` / `engine_failure_codes(issues)`：
  识别"引擎没能产出可用理解"的 code——
  `interpreter.unparseable_output*`、`feedback.unparseable_output*`、
  `providers.errors.PROVIDER_ERROR_CODES`（全部 `provider.*`，含不可重试类别）。
  **不**把 `interpreter.unresolved_language` / `interpreter.conflict_detected` /
  `policy.*` 观察误判为失败。
- `failure_codes_from_issues(issues)`：按出现顺序去重提取全部 ERROR 级 code（对客户端的
  失败原因列表）。
- `turn_failure_codes(issues, *, stale=False)`：必要时前置既有 `workflow.stale_revision`。
- 依赖边界：本模块**不** import `feedback` / `generation` / `prompt_engine`（`feedback`
  的解析失败前缀以字面量 `FEEDBACK_UNPARSEABLE_OUTPUT_PREFIX` 固定，避免让 Step 06 的
  `service.py` 经 `feedback` 间接依赖 `generation`）。`workflow/__init__.__all__` 未改。

### 1.2 `workflow/service.py`：重试耗尽后可恢复失败 + 过期请求保护

`SubmitMessageOutcome` 新增两个**带默认值**的向后兼容字段（追加在末位）：

| 字段 | 默认 | 语义 |
|---|---|---|
| `recoverable_failure` | `False` | 本轮未处理成功；状态 / Intent / PIN 均未被本轮推进 |
| `failure_codes` | `()` | 失败原因 code（ERROR 级，按序去重）；过期请求含 `workflow.stale_revision` |

`submit_message` 在 `IntentEngine.resolve`（既有 `MAX_INTERPRETATION_ATTEMPTS=2`：初次 +
至多一次修复）之后按顺序判定：

1. **过期请求保护**（新）：重新读取快照，若状态不是本轮入口设置的 `UNDERSTANDING`，
   或当前 intent/execution revision、pending question、消息集任一变化 → 本轮解析结果
   整体作废：`applied_deltas=[]`、`ready_for_confirmation=False`、resolution 换为
   `_stale_resolution(...)`（原 Intent 重新评估 + `workflow.stale_revision` ERROR issue），
   `failure_codes` 含 `workflow.stale_revision`。这替代了原先会抛出
   `persistence.stale_revision` 的异常路径。
2. **重试耗尽 / 不可重试失败**（行为修正）：只要最终 resolution 含引擎失败 code：
   - **保留用户消息**（消息已在解析前落库，不删除）；
   - **原 Intent / 既有 PIN / revision 不变**（`applied_deltas` 恒空，不落 revision、不落 carry）；
   - **不再**把 `assess` 对未变 Intent 生成的"空理解问题"落成新澄清：有既存 Pending
     Question → 保留**同一个**问题（id/payload 未动）并回到 `WAITING_CLARIFICATION`；
     没有 → 留在 `UNDERSTANDING`；
   - 返回 `recoverable_failure=True` + `failure_codes`（底层 `provider.*` /
     `interpreter.unparseable_output.*`）。
3. 成功路径不变：有 Delta → 落**一条**新 revision + carry；ready → `WAITING_CONFIRMATION`；
   有新问题 → `WAITING_CLARIFICATION`。

### 1.3 `workflow/review.py`：反馈可恢复失败显式化 + 过期反馈保护

`FeedbackOutcome` 在既有 `recoverable_failure` 之外新增 `failure_codes: tuple[str, ...] = ()`
（同样追加在末位、默认空）。

- **统一过期检查**（集成定点修正）：在 `analyze` 返回、**一切决策分支之前**统一判定一次。
  参照 `service.py::_request_is_current` 的口径，比较"入口快照 + 本条新增 user 消息"的
  `expected` 与当前会话头：状态必须仍是 `WAITING_REVIEW`，且 intent/execution revision、
  pending question、消息集均未变化。任一变化 → **accept / clarify / revise 一律不推进新
  状态**（过期 accept 不会到 `COMPLETED`、过期 clarify 不会落新问题、过期 revise 不会落
  revision），返回 `recoverable_failure=True` + `failure_codes` 含
  `workflow.stale_revision`，resolution 换成 `_failure_resolution(...)`（无 Delta）。
  反馈结果**已在此之前落库**，过期不阻止日志写入（可追溯）。
- **引擎失败短路**（防御性集中判定）：`result.issues` 命中引擎失败 code → 不应用候选、
  不改状态、不伪造问题，落 `recoverable_failure=True` + `failure_codes`；反馈结果本身
  仍已落库可追溯。
- **Delta 全被拒**（既有语义）：仍为可恢复失败，补全 `failure_codes`（validation/policy code），
  `resolution` 保留可观察。
- **clarify 无法定位目标**（既有语义）：补全 `failure_codes`。
- **clarify 有合法目标**：不变，仍推进到 `WAITING_CLARIFICATION`（不是失败）。
- `accept` / 成功 `revise` 路径不变。

### 1.4 `feedback/engine.py`：R1-A 数量 / 地点语义边界 + 版本 bump

按更改书 001 · R1-A 只补两条已被确认的语义边界（`feedback.v3 → feedback.v4`）：

- **rule 11 `COUNTING`**：显式数词（"两只"/"two"）才允许 `subject.count`；冠词/单数名词
  （"a cat"）**不得默认 1**；未点名不得 CLEAR/PIN `subject.count`。
- **rule 12 `LOCATIVE`**：自由处所细节（"在沙发上"）只作为用户已改的细节，**不得**推导
  `environment.location` 的房间/场景；明确地点（"地点是客厅"）必须提取。

保护语义**逐字保留**：`CLEAR IS PER-PATH`（rule 9）、`PRESERVE SCOPE`（rule 8）、输出
Schema、preserve→PIN 展开实现、decision 语义与全部既有重试逻辑（`MAX_FEEDBACK_ATTEMPTS=2`）
均未改变。提示词长度 3657 → 3993 字符（仍 < 既有 4000 上界守卫）。

### 1.5 重试层级（明确无第三层）

| 层 | 位置 | 预算 | 本次是否改动 |
|---|---|---|---|
| Adapter（HTTP） | `providers/*` 真实 adapter（`http_max_retries` + `0.5s×2^n` 退避） | 冻结配置 | 未动 |
| 引擎层（Interpreter） | `workflow/service.py::MAX_INTERPRETATION_ATTEMPTS = 2` | 初次 + 至多 1 次修复 | 语义不变，仅失败后的**推进行为**修正 |
| 引擎层（Feedback） | `feedback/engine.py::MAX_FEEDBACK_ATTEMPTS = 2` | 初次 + 至多 1 次 | 未动 |
| **Workflow 层** | 本工包 | **无新增** | 明确不新增第三层自动重试 |

可观察性：引擎层重试在评测 `RecordingLLMProvider` 中表现为**两条**同 role 的 `llm_calls`
（`retry_count` 仍只表示 adapter 内计数）；**Adapter 内部尝试次数在 Issue 层不可直接观察**，
未知值不得写成 0（属工包 C 的评测记录口径，见 §4）。

---

## 2. 公开接口变动（供 CLI D 消费）

```python
from visual_intent_agent.workflow import WorkflowService, SubmitMessageOutcome
from visual_intent_agent.workflow.review import ReviewService, FeedbackOutcome
from visual_intent_agent.workflow.models import WORKFLOW_STALE_REVISION  # 可选
```

| 类型 | 新增 | 兼容性 |
|---|---|---|
| `SubmitMessageOutcome` | `recoverable_failure: bool = False`、`failure_codes: tuple[str, ...] = ()` | 追加末位、有默认值；既有构造 / 比较 / 签名不变；`workflow/__init__.__all__` 未改 |
| `FeedbackOutcome` | `failure_codes: tuple[str, ...] = ()`（`recoverable_failure` 原本已有） | 同上 |

- `WorkflowService.submit_message` / `ReviewService.submit_feedback` 的**签名未变**；
- `FeedbackResult` 仍恰好 12 字段、`FEEDBACK_OUTPUT_SCHEMA` 未变；
- 当前 revision / pending question 等既有契约未变。

**CLI 显示建议**：

- `recoverable_failure is True` → 显示"本轮未处理成功，可显式重试"，并列出 `failure_codes`；
- `WORKFLOW_STALE_REVISION in failure_codes` → 显示"会话已被新操作更新，请刷新/重看确认后重试"；
- **不要**用 `pending_question is None` 推断成功；成功一律 `recoverable_failure is False`；
- `resolution.applied_deltas` 在可恢复失败时恒为空，可直接据此判断"本轮没改动 Intent"。

---

## 3. 测试（全部离线、Fake Provider、`tmp_path` SQLite；未真实调用、未跑全量）

```text
$ .venv/bin/python -m pytest tests/workflow tests/feedback -q
191 passed

$ .venv/bin/python -m pytest tests/e2e tests/prompt_engine -q   # 相邻消费者回归（首轮）
105 passed

$ ruff check visual_intent_agent/workflow/models.py visual_intent_agent/workflow/service.py \
      visual_intent_agent/workflow/review.py visual_intent_agent/feedback \
      tests/workflow/test_workflow_recoverable_failure.py \
      tests/workflow/test_workflow_execution_context.py tests/feedback/test_feedback_retry.py \
      tests/feedback/test_feedback_boundaries.py
All checks passed!
```

新增/修改的关键用例：

- `tests/workflow/test_workflow_recoverable_failure.py`（**新增**，R1-C 逐条）：
  1. 连续 timeout → `recoverable_failure=True`、`failure_codes==("provider.timeout",)`、
     `UNDERSTANDING`、无 pending question、无 revision、用户消息保留、调用数 == 2（无第三层）；
  2. 澄清回答失败 → 保留**同一个** `question_id`/payload 与 `WAITING_CLARIFICATION`、原
     revision 不变、消息保留；
  3. PIN 请求失败后恢复：失败零写入 → 显式重试成功**恰好一次**、PIN 保留、新 revision 只 1 条；
  4. 过期请求：解析期间会话被推进 → `failure_codes` 含 `workflow.stale_revision`、
     `applied_deltas==[]`、旧 Delta 绝不落到新 revision（只存在并发那一条）。
- `tests/workflow/test_workflow_service.py`：把固化旧语义的 2 条（失败 → 新澄清问题 /
  WAITING_CLARIFICATION）改为显式失败 + 不推进；`model_fields` 冻结集 +2；timeout→成功
  断言 `recoverable_failure is False` 且只应用一次。
- `tests/workflow/test_workflow_execution_context.py`：按主代理转达的 A 变更，把固化
  "景别 × 方图 = 冲突"的用例改为**景别单独出现不构成比例冲突**；接线证据改用 spy 证明
  `assess` 收到的 `execution_context` 来自会话（不再依赖已停用规则间接证明）。
- `tests/feedback/test_feedback_retry.py`：新增 ReviewService 层 provider timeout 的显式
  可恢复失败，以及**过期 accept / clarify / revise 三条统一过期检查回归**（均断言不推进
  新状态、反馈日志仍 +1、并发 revision 是唯一新增）；既有 6 条引擎级重试回归保留。
- `tests/feedback/test_feedback_boundaries.py`：版本字面量 → `feedback.v4`；新增
  `COUNTING` / `LOCATIVE` 两条语义边界 marker 断言；CLEAR / PRESERVE 保护断言保留。
- `tests/feedback/test_feedback_engine.py` / `test_feedback_public_api.py`：版本字面量 +
  `FeedbackOutcome.model_fields` 增加 `failure_codes`。

---

## 4. 明确未完成 / 不在本工包（不阻塞本工包验收）

1. **R1-C 第 4 条（A/B 调用次数、耗时、失败记录；Adapter 内尝试是否可观测）**：属
   `evaluation/**` + `tests/evaluation/**`（工包 C）。本工包只在引擎层保证"重试 = 两条
   `llm_calls`"可审计；评测报告需区分 `retry_count`（adapter 内）与同 role 多条调用
   （引擎层），未知值不得写 0。
2. **R1-C 第 5 条（更换 Provider/超时/模型参数需另建配置与 Run）**：属评测运行口径（工包 C）。
3. **R2 真实探针 / R3 正式复评 / Gate A**：本轮不做，未伪造结果。
4. `feedback.v4` 尚未写入 `evaluation/reports/fix_traceability.md` 等评测修订日志
   （`evaluation/**` 非本工包写入范围），需由工包 C 在 r1 记录中登记。
5. 主代理正在 `generation/pipeline.py` 增加 `retry(session_id, generation_id=None)` 显式
   最新 Prompt 路径：与本工包不冲突（本工包不改生成链路）；其"过期 generation/revision"
   语义需在集成时与 `workflow.stale_revision` 口径对齐（同一 code 取值，含义一致）。
6. 未提交 Git；未回滚/覆盖任何既有 dirty 成果。

---

## 5. 风险与边界说明

1. **行为修正是有意的**：失败后不再出现"新澄清问题"。若下游（评测 / CLI）此前依赖
   "失败也会有 pending question"来驱动重试，必须改看 `recoverable_failure` / `failure_codes`。
   本轮已同步修改本 scope 内固化的 2 条测试，并保留旧 Issue 在 `resolution.issues` 中可观察。
2. **零 Delta 的成功理解 ≠ 失败**：LLM 返回合法但无 Delta 时仍按正常澄清/确认推进
   （那是有依据的理解结果，不是"空理解失败"）；只有 `interpreter.unparseable_output*` /
   `provider.*` / `feedback.unparseable_output*` 才判为可恢复失败。
3. **过期检测是快照比较**（状态 + intent/execution revision + pending question + 消息集），
   单进程 CLI 下几乎不会触发；触发时宁可作废本轮，也不把旧 Delta 套到新 revision。
4. **PIN / CLEAR / 确认绑定不变量未放宽**：未改 Validator / Reducer / DecisionPolicy /
   Repository / 确认哈希；`SubmitMessageOutcome` 的确认摘要路径未动。
5. 提示词 `feedback.v4` 只是补两条 R1-A 语义边界（+336 字符），不主张因果改善超时率；
   真实语言理解仍需 R2 探针验证（Fake LLM 不能验证真实语义）。

---

## 6. 能否进入下一步

**本工包实现完成、可进入集成验收。** 离线局部测试与相邻消费者回归全绿；公开接口以
向后兼容方式扩展并已同步主代理 / CLI D；未完成项均为其他工包或后续真实实验，且已列明。
