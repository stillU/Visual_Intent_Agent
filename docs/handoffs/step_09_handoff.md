# Step 09 交接记录：FeedbackEngine、RealizationState 与多轮修改（P3）

任务：Step 09 — 针对**真实 GenerationArtifact** 的用户反馈解释、Realization 的继承与失效、以及
连续 3～5 轮局部修改闭环。**不实现图片编辑 / 身份锁定 / Reference Image / KnowledgeEngine；
不声称图片视觉不变；不跳过重新确认。**

- 依据：`docs/task_books/mvp_v0.2/09_feedback_realization.md`（任务书）、`docs/task_books/mvp_v0.2/README.md`（10 条全局不变量与统一交接格式）、
  `docs/ARCHITECTURE.md` Rev.2（第 4 节「Step 09 — feedback + realization/carry + workflow/review」
  冻结表、5.1～5.7、第 7 节测试策略、文末 Rev.2）、
  `docs/handoffs/{step_01…step_08}_handoff.md`、`step_04_patch_001.md`、
  `step_08_patch_001.md`、`architecture_decision_001.md`、`architecture_decision_002.md`
  （尤其「对 Step 09 实施 Agent 的指令」）。
- 范围纪律：只新增 `visual_intent_agent/{feedback/**, realization/carry.py, workflow/review.py}`
  与 `tests/{feedback/**, realization/test_realization_carry.py, fixtures/multiturn/**}`；
  **未修改任何上游或既有文件**（`domain/`、`validation/`、`policy/`、`persistence/`、
  `intent_engine/`、`pipeline/`、`providers/`、`prompt_engine/`、`realization/models.py`、
  `workflow/{__init__,questions,confirmation,service}.py`、`config.py`、`pyproject.toml`、
  `uv.lock`、`.env`、MVP v0.2 任务书与任务索引、`docs/ARCHITECTURE.md`、既有 handoff 与既有测试）。
  唯一写入的既有文件是本步拥有的 `visual_intent_agent/feedback/__init__.py`（原 0 字节空文件 →
  按冻结表再导出公开面）；`realization/__init__.py` **保持 0 字节空白**（有既有测试钉住）。
  **无新增依赖**（只用 pydantic + stdlib；默认测试全离线）。

---

## 完成内容

### 1. `feedback/models.py` — 反馈合同与 code 命名空间

| 公开名 | 形状（与冻结表逐字一致） |
|---|---|
| `FeedbackDecision` | str 枚举，恰好三值：`accept` / `revise` / `clarify` |
| `FeedbackRequest` | 恰好 7 字段：`session_id`；`message_id`；`feedback_text`；`generation: GenerationArtifact`；`prompt_artifact: PromptArtifact`；`current_intent: VisualIntent`；`realization_state: RealizationState\|None=None` |
| `FeedbackResult` | 恰好 12 字段：`schema_version`；`feedback_id`；`session_id`；`generation_id`；`intent_revision_id`；`decision`；`candidate_deltas: list[IntentDelta]`；`preserve_paths: list[str]`；`compile_feedback: str\|None`；`issues: list[Issue]`；`evidence_refs: list[EvidenceRef]`；`created_at` |
| `FeedbackError` | 程序级失败（`.code` ∈ `feedback.*`）；基类不校验 code（同 `InterpreterError` 约定） |
| `FeedbackParseError` | LLM 输出无法解析（`.retryable=True`）；只接受冻结的 `PARSE_FAILURE_CODES` |
| `clarify_target_path(result) -> str\|None` | 从 `feedback.clarification_required` issue 的 `path` 取**可执行**目标（非白名单即 None） |
| `is_unparseable(result) -> bool` | 结果是否由解析失败产生（`feedback.unparseable_output.*`） |

- 全部 `frozen=True, extra="forbid"`；`created_at` 拒绝 naive、JSON 序列化 `+00:00`；
  默认列表一律 `Field(default_factory=list)`；`FeedbackResult` / `FeedbackRequest` 均
  `model_validate_json` 往返一致。
- **`intent_revision_id` 的确定性口径**：反馈入口只允许 `WAITING_REVIEW`，此时当前 revision 必等于
  该次生成所依据的 revision，因此引擎取 `prompt_artifact.based_on_intent_revision_id`
  （无须也不允许从 LLM 或调用方猜测）。
- `FeedbackResult` 只存 ID 引用（不内嵌其它层对象），`FeedbackRequest` 按冻结表携带只读对象快照。

### 2. `feedback/engine.py` — FeedbackEngine

`FeedbackEngine(llm: LLMProvider).analyze(request: FeedbackRequest) -> FeedbackResult`
（复用 Step 05 唯一 `LLMProvider`；`response_format={"type":"json_object"}`；
`FEEDBACK_PROMPT_VERSION="feedback.v1"`、`FEEDBACK_SYSTEM_PROMPT_V1`、`FEEDBACK_OUTPUT_SCHEMA`）。

LLM 输出合同（与 `_LLMOutput` 一一对应，`extra="forbid"`）：`decision`（必填）、
`candidate_deltas`、`preserve_paths`、`compile_feedback`、`clarify_path`、`clarify_reason`。

处理规则（任务书「反馈处理规则」）：

- **accept**：无候选 Delta、无 preserve、无 issue；`compile_feedback=None`。
- **revise**：至少一条候选 Delta；preserve 模式展开为白名单路径，其中**当前有值**的路径
  转成合法 **PIN 候选 Delta**（无值 / 已委托路径只留 `preserve_paths` 记录，不伪造值）；
  未知模式（既非白名单路径也非 `<facet>.*`，含裸 `*`）忽略并报
  `feedback.unknown_preserve_path` warning；`compile_feedback` 取 LLM 文本或确定性兜底文本。
- **clarify**：禁止候选 Delta / preserve；`clarify_path` 必须是白名单路径（否则解析失败），
  生成 `feedback.clarification_required`（warning，`path` = 待澄清路径）作为**可执行载体**；
  无法定位目标时生成 `feedback.clarification_target_missing`（error）→ 工作流不猜问题、不改状态。
- **证据**：LLM 不输出 ID；每条 Delta 的 `EvidenceRef.message_id` 由系统重填 `request.message_id`，
  `fragment` 取 LLM 回填原文；反馈**不是** Pending Question 的回答，`pending_question_id` 一律 None。
- **可恢复失败不抛异常**：空文本 / 非法 JSON / Schema 不符 / 完整 Intent / 自相矛盾 decision /
  非法 `clarify_path` / 单条 Delta 违反冻结形状 / Provider 调用失败 → 返回 `FeedbackResult`
  （`decision=clarify`、无候选、error issue ∈ `feedback.unparseable_output.*` / `provider.*`），
  不猜测修复；唯一容错是剥离 Markdown 代码围栏（纯文本规范化）。
- **程序级失败抛异常**：`FeedbackError(feedback.context_mismatch)` —— generation 与 prompt_artifact
  不匹配 / 跨会话 / realization 跨会话（绝不"修正"调用方上下文）。
- 纯函数工具（可单独测试）：`match_preserve_pattern` / `expand_preserve_paths` /
  `preserve_pin_deltas` / `read_intent_value` / `default_compile_feedback` /
  `build_feedback_user_prompt`（确定性、可断言；用户文本标注 untrusted）。

### 3. `realization/carry.py` — evaluate_carry / build_carry_state

```python
class CarryEvaluation(BaseModel):          # 恰好两字段（冻结）
    carried_values: list[RealizationValue]
    invalidated_values: list[RealizationValue]   # status="invalidated" + reason + at

def evaluate_carry(state, change_summary, policies=DECISION_POLICIES) -> CarryEvaluation
def build_carry_state(evaluation, state, *, session_id, based_on_intent_revision_id,
                      realization_id=None, created_at=None) -> RealizationState
def owner_policy_for_path(path, policies=DECISION_POLICIES) -> DecisionPolicy | None
```

失效判定（**只读 `ChangeSummary`**，不读新 Intent，符合冻结签名）：

| 条件 | reason | 说明 |
|---|---|---|
| 用户 SET/CLEAR **同一路径** | `user_changed_delegated_path` | `changed_paths` ∪ `cleared_paths` 命中该路径 |
| 所属 Decision 的 **dependency** 变化 | `decision_dependency_changed` | 用 `DecisionPolicy.{path,dependencies}` 成员关系（如 `environment.mode` 变化使 `environment.location` 失效） |
| 新 Intent 与旧 Realization 冲突 | （由条件 1/4 覆盖） | 冻结签名不接收新 Intent；"冲突"的可判定形态即该路径（含纯 resolution）的 SET/CLEAR；不猜测语义 |
| 用户 **CLEAR** delegated path | `user_changed_delegated_path` | `cleared_paths` 命中 |
| **UNPIN** 影响 | `unpinned_delegated_path` | `unpinned_paths` 命中该路径或同 Decision 成员路径 |
| **PIN** | 不失效（默认继承） | 用户显式"保持"强化复用，绝不因 PIN 使实现失效 |

- 默认继承 active：`status=="active"` 的值原样进入 `carried_values`（原顺序 / 原
  `first_prompt_artifact_id` / 原 `carry_policy`）；已失效值不再进入新快照（旧 state 仍可审计）。
- 失效**显式**：新对象（`model_copy(update={status, invalidated_reason, invalidated_at})`），
  同一批次共享一个 `invalidated_at`；纯函数，绝不修改输入 state。
- `build_carry_state` 产生**新** `RealizationState`（`new_id("rlz")`、`based_on_intent_revision_id`
  = 新 revision、值顺序沿用旧 state、丢弃旧快照里已失效的值）；由 `ReviewService` 先落
  `IntentRevision` 再 `append_realization_state`（refs 精确 `{"based_on_intent_revision_id"}`）。

### 4. `workflow/review.py` — ReviewService（新增文件，不改 Step 06 既有文件）

```python
class ReviewService:
    def __init__(self, repo: Repository, feedback_engine: FeedbackEngine,
                 question_builder: QuestionBuilder) -> None
    def submit_feedback(self, session_id: str, generation_id: str, text: str) -> FeedbackOutcome
```

**构造依赖（最小化设计）**：只注入 `repo` + `feedback_engine` + `question_builder`。

- **不注入 `IntentEngine`**：反馈已经是结构化 Candidate Delta，不需要再次自然语言理解
  （`validate` / `reduce` / `assess` 是确定性纯函数，直接复用）；
- **不注入 `GenerationPipeline` / `settings`**：`submit_feedback` 只把会话推进到
  `WAITING_CONFIRMATION`，"再生成"必须由调用方在**用户重新确认**后调用既有
  `GenerationPipeline.generate(session_id)`（README 不变量 5/6：不得跳过重新确认）。
  P3 驱动序列：`submit_feedback → WorkflowService.confirm_current_intent → GenerationPipeline.generate`。

`submit_feedback` 冻结流程：

1. 仅 `WAITING_REVIEW`（否则 `WorkflowError(workflow.invalid_state)`）；
2. 加载**当前待复核**的 GenerationArtifact 与其 PromptArtifact（不是最新一次生成 →
   `workflow.generation_mismatch`；不存在/空 id → `workflow.generation_not_found`）；
3. 存反馈消息（`msg`）→ `FeedbackEngine.analyze` → `append_feedback_result` 落库
   （refs **精确** `{"generation_id": ...}`）；
4. `accept` → `transition COMPLETED`；
   `revise` → `validate`（EvidenceContext = 本会话全部消息；反馈无 Pending Question 绑定）
   → `reduce` → `assess` → 新 `IntentRevision`（`parent` = 当前 head）落库（**旧确认天然失效**）
   → `evaluate_carry`（有失效则落**新** RealizationState）→
   `WAITING_CONFIRMATION`（不可确认但有 question 时→ `WAITING_CLARIFICATION`）；
   `clarify` → `PendingQuestion`（`qst`）落库 → `WAITING_REVIEW→UNDERSTANDING→WAITING_CLARIFICATION`
   （既有迁移两步走）；无法定位目标的 clarify / 全部 Delta 被拒 → **状态不变**、
   `recoverable_failure=True`（不猜问题、不造空 revision）。

**依赖边界（遵守 Step 06 冻结测试）**：`tests/workflow/test_workflow_public_api.py` 禁止
`workflow/` 包 import `prompt_engine` / `generation` / 图像 adapter。因此 `review.py` **不**
import Step 07/08 模型：把 Repository 信封的 payload 解析为 JSON 字典，用
`FeedbackRequest.model_validate({...})` 让 pydantic 校验成正确合同对象（字段面仍只由
Step 07/08 持有）。`os` / `httpx` / `sqlite3` 同样不 import（持久化只经 `Repository`）。
`workflow/__init__.py` 未改动，因此导入路径是 `visual_intent_agent.workflow.review`。

### 5. PromptEngine 的 Realization carry 接入（**零改动**，仅验证）

`PromptEngine.compile` 读 `get_current_realization_state(session_id)`，因此只要 revise 在编译前把
**最新** state 落库，编译就复用 carry 结果。测试验证：

- 只改镜头（未失效）→ 新 PromptArtifact 的 delegated binding 仍是
  `source_kind="realization"` 且文本与上一轮**逐字相同**（`realization_id` = 旧 state）；
- 用户改 delegated 路径（失效）→ 旧值 `status="invalidated"` 显式落库；新编译对同一路径产生
  **新选择**（`source_kind="delegation"`、新 `realization_id`、PromptEngine 追加新 state）；
- dependency 变化（`environment.mode`）→ `environment.location` 实现失效 → 重新实现。

---

## 变更文件

新增：

- `visual_intent_agent/feedback/models.py`
- `visual_intent_agent/feedback/engine.py`
- `visual_intent_agent/realization/carry.py`
- `visual_intent_agent/workflow/review.py`
- `tests/feedback/feedback_helpers.py`
- `tests/feedback/test_feedback_models.py`
- `tests/feedback/test_feedback_engine.py`
- `tests/feedback/test_feedback_public_api.py`
- `tests/feedback/test_feedback_e2e.py`
- `tests/realization/test_realization_carry.py`
- `tests/fixtures/multiturn/p3_multiturn_v1.json`
- `tests/fixtures/multiturn/p3_carry_cases_v1.json`
- `docs/handoffs/step_09_handoff.md`（本文件）

修改（唯一一处，本步拥有的包）：

- `visual_intent_agent/feedback/__init__.py`（原 0 字节空文件 → 按冻结表再导出公开面）

未触碰：MVP v0.2 任务书、任务索引与 Agent 派发提示词、`api.md`、
`docs/ARCHITECTURE.md`、其余全部 handoff、`visual_intent_agent/{domain,validation,policy,
persistence,intent_engine,providers,prompt_engine,generation,config.py}`、
`visual_intent_agent/realization/{__init__.py,models.py}`、
`visual_intent_agent/workflow/{__init__.py,questions.py,confirmation.py,service.py}`、
`tests/{domain,validation,policy,persistence,intent_engine,workflow,prompt_engine,generation,
providers,realization/test_realization_models.py,e2e,smoke}`、`tests/conftest.py`、
`tests/test_sanity.py`、`pyproject.toml`、`uv.lock`、`.env`。
`visual_intent_agent/realization/__init__.py` 与 `visual_intent_agent/providers/__init__.py`
仍为 0 字节（前者有既有测试钉住）。

---

## 公开接口

```python
from visual_intent_agent.feedback import (
    FeedbackEngine, FeedbackDecision, FeedbackRequest, FeedbackResult,
    FeedbackError, FeedbackParseError, clarify_target_path, is_unparseable,
    FEEDBACK_PROMPT_VERSION, FEEDBACK_RESPONSE_FORMAT, FEEDBACK_SYSTEM_PROMPT_V1,
    FEEDBACK_OUTPUT_SCHEMA, CANONICAL_PATH_ORDER, build_feedback_user_prompt,
    read_intent_value, match_preserve_pattern, expand_preserve_paths,
    preserve_pin_deltas, default_compile_feedback,
)
from visual_intent_agent.realization.carry import (
    CarryEvaluation, evaluate_carry, build_carry_state, owner_policy_for_path,
    REALIZATION_ID_PREFIX,
    CARRY_REASON_USER_CHANGED_PATH, CARRY_REASON_DEPENDENCY_CHANGED,
    CARRY_REASON_UNPINNED, CARRY_INVALIDATION_REASONS,
)
from visual_intent_agent.workflow.review import (
    ReviewService, FeedbackOutcome, ReviewError,
    REVIEW_GENERATION_NOT_FOUND, REVIEW_GENERATION_MISMATCH, REVIEW_ERROR_CODES,
    MESSAGE_ID_PREFIX, INTENT_REVISION_ID_PREFIX, QUESTION_ID_PREFIX,
)
```

### `FeedbackOutcome`（本步冻结，客户端/Step 10 消费）

| 字段 | 形状 | 语义 |
|---|---|---|
| `session_id` | str | 会话 |
| `message_id` | str | 本轮反馈消息 ID（已落库） |
| `generation_id` | str | == `feedback.generation_id`（冗余便利字段） |
| `feedback` | `FeedbackResult` | 已落库的反馈解释结果 |
| `snapshot` | `SessionSnapshot` | 处理后的状态（accept→`COMPLETED`；revise→`WAITING_CONFIRMATION`/`WAITING_CLARIFICATION`；clarify→`WAITING_CLARIFICATION`；可恢复失败→原 `WAITING_REVIEW`） |
| `resolution` | `IntentResolution\|None` | revise（或全部被拒）时的 applied deltas 与全部 issues |
| `pending_question` | `PendingQuestion\|None` | clarify（或 revise 后不可确认）落地的问题 |
| `confirmation_summary` | `ConfirmationSummary\|None` | 进入 `WAITING_CONFIRMATION` 时的摘要（交给 `confirm_current_intent`） |
| `carry` | `CarryEvaluation\|None` | revise 的 Realization 继承/失效评估 |
| `realization_state_id` | `str\|None` | 本轮是否落了新 RealizationState（无失效 = None） |
| `recoverable_failure` | bool | 解析/Provider 失败或 Delta 全部被拒：状态未变、无新 revision/Artifact |

### 本步新增 code

`feedback.*`（Issue code，ARCHITECTURE 5.5）：

| code | severity | 触发 |
|---|---|---|
| `feedback.unparseable_output.empty` | error | LLM 返回空文本 / 反馈文本为空 |
| `feedback.unparseable_output.invalid_json` | error | 文本不是合法 JSON |
| `feedback.unparseable_output.schema_violation` | error | 不符合 `FEEDBACK_OUTPUT_SCHEMA` |
| `feedback.unparseable_output.full_intent` | error | 返回完整 Intent / facet 对象 |
| `feedback.unparseable_output.invalid_delta` | error | 单条 Delta 违反 `IntentDelta` 冻结形状 |
| `feedback.unparseable_output.inconsistent_decision` | error | decision 与字段自相矛盾（revise 无 delta / accept 带 delta / clarify 带 delta） |
| `feedback.unparseable_output.invalid_clarify_path` | error | `clarify_path` 不在白名单（不修正、不猜测） |
| `feedback.clarification_required` | warning | 可执行 clarify 载体（`path` = 待澄清白名单路径） |
| `feedback.clarification_target_missing` | error | clarify 但无法定位白名单路径 → 工作流不猜问题 |
| `feedback.unknown_preserve_path` | warning | preserve 模式既非白名单路径也非 `<facet>.*`，已忽略 |
| `feedback.context_mismatch` | （异常）`FeedbackError` | generation / prompt / realization 不匹配或跨会话 |

`workflow.*`（本步新增 2 个，由 `ReviewError` 承载；既有三个 code 语义不变）：

| code | 触发 |
|---|---|
| `workflow.generation_not_found` | `generation_id` 缺失/该会话不存在该 GenerationArtifact（或其 PromptArtifact 缺失） |
| `workflow.generation_mismatch` | generation 属于其它会话，或不是该会话**当前待复核**的那次生成 |

`RealizationValue.invalidated_reason` 稳定取值：`user_changed_delegated_path` /
`decision_dependency_changed` / `unpinned_delegated_path`。

### Repository 使用（不绕过协议直读 SQLite）

`get_current_session_snapshot`、`append_message`、`get_generation_artifact`、
`list_generation_artifacts`、`get_prompt_artifact`、`get_current_realization_state`、
`get_intent_revision`、`get_execution_revision`、`append_intent_revision`、
`append_realization_state`、`append_feedback_result`、`transition_state`、
`save_pending_question`、`clear_pending_question`、`is_confirmation_valid`。
`append_feedback_result` 的 refs 严格为冻结必填键 `{"generation_id"}`；
`append_realization_state` 的 refs 严格为 `{"based_on_intent_revision_id"}`（各有测试断言）。

### 对 ARCHITECTURE.md 的最小修订建议（未自行修改文档）

1. **Step 06 依赖边界测试的范围需随 Step 09 收窄（已用兼容设计规避，非阻塞）**：
   `tests/workflow/test_workflow_public_api.py::test_workflow_never_imports_later_steps_or_web_db_layers`
   对 `workflow/*.py` 一律禁止 `prompt_engine` / `generation` import，而冻结表又要求新增
   `workflow/review.py`（P3 链路必须消费 Step 07/08 对象）。本步的兼容实现是：`review.py`
   不 import 这两个包，改为把 Repository payload 交给 `FeedbackRequest.model_validate` 校验
   （字段面仍归 Step 07/08）。若架构方希望 `review.py` 能直接持有强类型对象，最小修订是把该
   测试的扫描范围限定为 Step 06 的三个模块（`questions.py` / `confirmation.py` / `service.py`）
   或显式豁免 `review.py`。
2. **`ChangeSummary` → `evaluate_carry` 的表达力边界**：冻结签名只接收 `ChangeSummary`，
   因此"新 Intent 与旧 Realization 冲突"只能以其可判定形态（该路径的 SET/CLEAR，含纯
   resolution 的 SET）落地，无法检测"新值与实现冲突"这类语义冲突。若未来需要，建议给
   `ChangeSummary` 或 `evaluate_carry` 增加显式的 `new_intent`/resolution 快照（属 Step 02/09
   联合修订，本步不自行扩大）。
3. **经由 Step 06 `submit_message` 的修改不经过 `evaluate_carry`**（见「已知限制 1」）：
   若要让 clarify 回答也触发 dependency 失效，可让 `WorkflowService.submit_message` 在落
   revision 后调用 `evaluate_carry`（改 Step 06 文件），或给 `ReviewService` 增加"回答后处理"
   用例（扩大 Step 09 冻结面）。二选一，需架构裁定。

---

## 测试命令与结果

```text
$ cd /home/change/projects/image_system

# 基线（实施前）
$ uv run pytest -q
1733 passed, 3 deselected

# 全量（基线 1733 + 本步 101 条离线用例）
$ uv run pytest -q
1834 passed, 3 deselected in 4.65s

# 本步范围
$ uv run pytest tests/feedback -q
78 passed
$ uv run pytest tests/realization -q
44 passed              # 既有 21 + 本步 carry 新增 23
$ uv run pytest tests/feedback tests/realization -q
122 passed

# 上游范围（未回归）
$ uv run pytest tests/workflow tests/generation tests/prompt_engine tests/providers -q
374 passed
$ uv run pytest tests/persistence tests/domain tests/validation tests/policy tests/test_sanity.py -q
1257 passed

# 跨进程确定性
$ PYTHONHASHSEED=0 / 7 / 424242 uv run pytest tests/feedback tests/realization -q
-> 均 122 passed

# 依赖边界（干净子进程）
$ uv run python -c "import visual_intent_agent.feedback"
-> 未加载 httpx / openai（feedback 只依赖 domain/policy/validation/persistence/
   providers.llm+errors/generation/prompt_engine/realization 的公开面）

# 默认运行完全离线：FakeLLMProvider + FakeImageProvider + tmp_path SQLite/输出目录；
# 零网络、零真实凭据（新增源码/测试/本文件 grep key 前缀 0 命中）。
# 真实 Provider smoke：任务书无要求，本步不新增（节约配额）。
```

### 任务书「必测场景」→ 测试映射（10/10）

| # | 场景 | 测试 |
|---|---|---|
| 1 | "人物不变，只把镜头拉远"只修改 framing | `test_feedback_engine.py::test_revise_maps_the_camera_request_and_preserves_subject_paths`、`test_feedback_e2e.py::test_p3_five_round_loop`（第 1 轮：仅 `composition.framing` SET + `subject.*` PIN） |
| 2 | "只改光线"保持其他 Intent 路径不变 | `test_p3_five_round_loop`（第 2 轮：`_unauthorized_value_change_count == 0`；其余 11 路径逐值不变） |
| 3 | "背景不好"进入 clarify，不自动 SET location | `test_vague_background_feedback_becomes_clarify_without_guessing`、`test_clarify_never_sets_a_location_value`、`test_clarify_without_a_locatable_path_is_not_actionable` |
| 4 | delegated choice 在只改镜头时继续复用原 Realization | `test_p3_five_round_loop`（generation #2 binding `source_kind="realization"` 且文本与 #1 逐字相同）、`test_dependency_change_invalidates_the_realization_before_recompiling` |
| 5 | 用户修改 delegated 路径后旧 Realization 失效 | `test_p3_five_round_loop`（第 2 轮 `lighting.character` 失效）、`test_realization_carry.py`（表驱动 `same_path_set_invalidates_that_value`） |
| 6 | dependency 变化导致相关 Realization 失效 | `test_dependency_change_invalidates_the_realization_before_recompiling`、carry fixture `decision_dependency_change_invalidates_environment_location` |
| 7 | 每次重要修改都要求重新确认 | `test_every_modification_requires_a_fresh_confirmation_and_a_new_generation`、`test_p3_five_round_loop`（每轮 revise 前 `is_confirmation_valid == False` + `generate` 抛 `generation.no_valid_confirmation`） |
| 8 | 新一轮 Feedback 绑定新 GenerationArtifact | `test_feedback_must_bind_the_generation_currently_under_review`、`test_p3_five_round_loop`（每轮 `feedback.generation_id` == 本轮被复核 generation） |
| 9 | 历史 Prompt / Generation / Realization 均不覆盖 | `test_p3_five_round_loop`（每轮前后逐表逐行 payload 逐字节比对）、`test_new_state_is_appended_and_history_is_never_overwritten` |
| 10 | 3～5 轮后未修改路径仍保持 | `test_p3_five_round_loop`（第 5 轮后 `subject.description`/`style.primary`/`subject.pose_action`/PIN 保持） |

另覆盖：模型字段面 / frozen / extra / 时间戳 / 往返（`test_feedback_models.py` 22 例）、
解析失败 10 类参数化 + Provider 失败 + 空文本 + 未知 preserve（`test_feedback_engine.py`）、
公开面与依赖边界（`test_feedback_public_api.py`）、carry 表驱动 8 案例 + build_carry_state +
Repository 集成（`test_realization_carry.py`）、fixture 版本化与 round-trip 稳定。

### P3 结果记录（多轮剧本测量摘要）

剧本：`tests/fixtures/multiturn/p3_multiturn_v1.json`（基础意图：5 个有值路径 +
`lighting.character` / `environment.location` 两个 delegated 路径；每轮**重新确认后才再生成**）。

| 轮 | 反馈 | decision | 结果状态 | carry（carried / invalidated） | 产物 |
|---|---|---|---|---|---|
| 1 | keep the person, pull the camera back | revise | WAITING_CONFIRMATION | [environment.location, lighting.character] / [] | 新 revision + generation #2 |
| 2 | only change the lighting, dramatic | revise | WAITING_CONFIRMATION | [environment.location] / [lighting.character] | 新 revision + 新 RealizationState + generation #3 |
| 3 | the background is not good | clarify | WAITING_CLARIFICATION | — | PendingQuestion(environment.mode)；回答后新 revision + generation #4 |
| 4 | make the environment outdoor | revise | WAITING_CONFIRMATION | [] / [environment.location] | 新 revision + 新 RealizationState + generation #5 |
| 5 | exactly what I wanted, done | accept | COMPLETED | — | 无新产物 |

终局计数（append-only）：`intent_revisions=5`、`prompt_artifacts=5`、`generation_artifacts=5`、
`feedback_results=5`、`realization_states=4`；状态 `COMPLETED`。

- **状态层未授权字段变化为零**：每轮 `_unauthorized_value_change_count(before, after, authorized) == 0`
  （authorized = 该轮 `applied_deltas` 的路径集合），且 12 条白名单路径的集合恒等。
- **Intent Preservation（代码保证）**：终局 `subject.description="a cat"`、
  `style.primary="photorealistic"`、`subject.pose_action="sitting"`、
  `composition.framing="wide_shot"`、`lighting.character="dramatic"`、
  `environment.mode="outdoor"`；第 1 轮的 `pinned_paths={subject.description, subject.pose_action}`
  跨全部后续轮次保持。
- **Image Preservation（只测量，不承诺）**：每轮记录 GenerationArtifact 的
  `generation_id` / `prompt_artifact_id` / `target_model` / `model_version` / `size` / `seed` /
  每个输出文件的 `path` / `mime_type` / `byte_size` / `sha256`。相邻轮属性差异只出现在
  `generation_id` / `prompt_artifact_id` / 输出文件引用；`target_model` / `model_version` /
  `size` / `seed` 保持不变。离线 Fake 的 PNG 字节确定 → sha256 恒定，这只是**测量可复现**，
  **不是**任何"新图片视觉上与原图一致"的结论（任务书「两层 Preservation」）。

---

## 已知限制

1. **经由 Step 06 `submit_message` 的修改不触发 carry 失效（最重要）**：P3 冻结调用链把
   `evaluate_carry` 放在 `ReviewService` 的 revise 路径上；`clarify` 只落 PendingQuestion，
   用户回答由既有 `WorkflowService.submit_message` 处理（Step 06 文件，本步不得修改），
   因此"回答澄清时改了某 Decision 的 dependency 路径"不会失效相关 Realization。多轮剧本的
   第 3 轮（clarify → 回答 `environment.mode=indoor`）刻意不依赖该失效；dependency 失效由
   第 4 轮 revise 覆盖并有专项测试。最小修订建议见「公开接口」第 3 条。
2. **任务书示例中的 "clothing" 不在 12 条冻结白名单内**：`domain/paths.py` 恰 12 条路径且本步
   不得新增。因此 "delegated clothing 复用/失效" 用真实可委托路径等价覆盖：
   `lighting.character`（用户改值后旧实现失效）与 `environment.location`（dependency 变化失效、
   未失效时复用）。这是唯一的语义替换，未新增任何字段或路径。
3. **clarify 问题不带预设选项**：`suggested_values=()`。Step 03 的建议选项表不是公开面
   （`_SUGGESTED_VALUES` 为模块私有），本步不复制第二份表；这同时保证"背景不好"不会被猜成
   任何具体设计（`allow_custom=True`，策略允许时可委托）。目标路径与 `allow_delegate` 取自
   `DECISION_POLICIES`，LLM 无法改变问题指向。
4. **`compile_feedback` 只记录、不注入编译**：`PromptCompileRequest` 冻结签名为
   `(session_id, confirmation_id)`，没有承载反馈的通道；本步不改 `prompt_engine`，因此
   `compile_feedback` 仅供展示 / 审计 / Step 10 使用。若未来要把"仅改 X、保持 Y"这一约束下传，
   需要一次显式的 `PromptCompileRequest` 扩展（属 Step 07/11 版本决策）。
5. **`preserve_paths` 保留声明原文（可含 `<facet>.*`）**：可定位到当前有值路径的模式才转成
   PIN；无值 / delegated 路径只在 `preserve_paths` 留记录（不伪造值）。PIN 要求当前有值
   （Step 02 冻结），因此 delegated（无值）路径永远不会被 PIN。
6. **`PIN` 不使 Realization 失效，`UNPIN` 使其失效**（冻结的"PIN/UNPIN 影响"落地口径）：
   PIN 是"显式保持"，与 `carry_policy=preserve_until_invalidated` 同向；UNPIN 释放保持约束。
   由于 PIN 需要有值、而 realization 只存在于无值 delegated 路径，PIN 分支在正常流程中不可达
   （有测试直接构造覆盖）；UNPIN 分支可经同 Decision 成员路径命中。
7. **反馈不做"最多一次修复"重试**：Step 06 对 Interpreter 的解析失败有"最多一次重试"策略；
   本步 `FeedbackEngine` 自身不重试、`ReviewService` 也不重试，而是在 `issues` 中暴露
   `feedback.unparseable_output.*` / `provider.*` 并保持 `WAITING_REVIEW`，由调用方决定是否
   再提交一次。这是刻意的最小行为（不猜测修复）；若需要与 Step 06 对齐的一次重试，属行为增量。
8. **`WAITING_REVIEW` 不提供"重做/重试"入口**：`WAITING_REVIEW → FAILED` 不是冻结迁移。
   FAILED 会话的重试仍走 `GenerationPipeline.retry(session_id, generation_id)`（`generation_id`
   取自 `generation.failed` 日志事件），本步未新增该入口（`generation_id` 无法在
   `WAITING_REVIEW` 下取得），也未自行实现回退查询 / 直读 SQLite / 用 `generate` 代替 retry
   （遵守 `architecture_decision_002.md` 对 Step 09 的指令）。
9. **`review.py` 不 import Step 07/08 模型**（为满足 Step 06 冻结的依赖边界测试）：请求对象由
   `FeedbackRequest.model_validate(payload_dict)` 构造。代价是 `review.py` 不持有强类型
   `GenerationArtifact` / `PromptArtifact` 变量（只读 `stored.refs` 与 payload 字典）；合同校验
   仍由 pydantic 完成。若架构方收窄该测试范围，可换回直接 import（见建议 1）。
10. **未实现（属禁止范围 / 后续步骤）**：图片编辑 / 身份锁定 / Reference Image /
    KnowledgeEngine / RAG、质量自动判断、多模型、队列、Web API、Direct LLM baseline（Step 10）。

---

## 对下一步的输入

### Step 10（Gate A 评测与 MVP 验收）

1. **多轮 fixture（唯一权威剧本）**：`tests/fixtures/multiturn/p3_multiturn_v1.json`
   （5 轮剧本，含每轮 feedback LLM 输出与期望）与 `p3_carry_cases_v1.json`（carry 表驱动
   8 案例）。两者版本化为 `"fixture_version": "v1"`、加载后 round-trip 稳定、无凭据/URL。
2. **每层 Artifact 与指标采集点**：
   - 层：`IntentRevision`（`intent` + `applied_deltas`）、`ConfirmationRecord`（双 revision +
     summary_hash）、`PromptArtifact`（`source_bindings` 的 `source_kind` / `intent_path` /
     `realization_id` + `prompt`）、`GenerationArtifact`（模型/参数/seed/output_refs/字节 sha256）、
     `RealizationState`（active / invalidated + reason + at）、`FeedbackResult`（decision /
     candidate_deltas / preserve_paths / issues / evidence_refs）；
   - 指标：`tests/feedback/feedback_helpers.py::generation_measurement`（图片层测量点）、
     `artifact_counts` / `artifact_payloads`（append-only 与层级计数）、
     `_unauthorized_value_change_count`（状态层未授权变化 = 0）。
3. **同一 Provider 配置**：`Settings`（`provider_base_url` / `provider_api_key` /
   `llm_model` / `image_model` / 超时与重试）统一承载；`FeedbackEngine` 与 `Interpreter` 复用同一
   `providers.llm.LLMProvider`（真实 adapter `OpenAICompatibleLLMProvider`），Direct LLM baseline
   可直接复用 `providers/{llm,image}.py`，无需改动。
4. **可直接调用的 P3 入口**：
   `ReviewService(repo, FeedbackEngine(llm), QuestionBuilder()).submit_feedback(sid, gen_id, text)`
   →（revise）`WorkflowService.confirm_current_intent(...)` → `GenerationPipeline.generate(sid)`；
   `clarify` 的回答走 `WorkflowService.submit_message(sid, text)`。
5. **评测注意**：`FeedbackResult.intent_revision_id` 是"被反馈的那次生成的 Intent revision"；
   `FeedbackOutcome.recoverable_failure` 表示本轮未改变状态（评测应把它与"成功修改"区分统计）；
   图片层不得把 Fake 的恒定字节当成视觉一致性证据。

### 通用

- 上游问题继续走"最小修订提案 → 架构方裁定"（见「公开接口」三条建议与「已知限制」1/9）。
- 新测试目录无 `conftest.py`、不 `from conftest import ...`、不跨目录 import；
  共享工具在唯一命名模块 `tests/feedback/feedback_helpers.py`。

---

## 验收条件逐条核对

| # | 任务书验收条件 | 核对结果 |
|---|---|---|
| 1 | 连续 3～5 轮局部修改闭环可运行 | **是**。`test_p3_five_round_loop` 用版本化 fixture 跑通 5 轮（4 次修改 + accept）：每轮 FeedbackEngine → Validator/Reducer/Policy → 新 IntentRevision → evaluate_carry（必要时新 RealizationState）→ **重新确认** → `GenerationPipeline.generate` → 新 GenerationArtifact；终局 `COMPLETED`，5 个 generation / 5 个 prompt artifact / 5 条 feedback / 4 个 realization state。 |
| 2 | 状态层未授权字段变化为零 | **是**。每轮 `_unauthorized_value_change_count(...) == 0`（未在 `applied_deltas` 中的路径逐值不变；12 条路径集合恒等）；澄清轮 Intent 与 revision 计数都不变；全部 Delta 被拒时不产生空 revision（`test_rejected_deltas_do_not_create_an_empty_revision`）。 |
| 3 | delegated choice 在未失效时稳定继承 | **是**。只改镜头后新 PromptArtifact 的 delegated binding 仍为 `source_kind="realization"` 且文本逐字相同（`realization_id` = 旧 state，未新建 state）；只改光线时 `environment.location` 继续复用；失效后（用户改值 / dependency 变化）旧值显式 `status="invalidated"` + reason + at 落新 state，编译改为重新实现（新 `delegation` binding + 新 realization_id）。 |
| 4 | 模糊反馈不会被猜成具体设计 | **是**。"背景不好" → `decision=clarify` + `feedback.clarification_required(path="environment.mode")`，候选 Delta 与 preserve 均为空，绝不 SET `environment.location`（专项测试断言无任何 location 候选 / 值，且结果文本不含猜测值）；无法定位目标时 `feedback.clarification_target_missing` + 状态不变、不伪造问题；clarify 问题不带预设选项。 |
| 5 | 所有新图片和反馈均可追溯 | **是**。每条 `FeedbackResult` 经 `append_feedback_result` 落库（refs 精确 `{"generation_id"}`，payload 与返回值逐字节相等）；`generation_id` 必须是当前待复核那次生成；`intent_revision_id` = 该生成的 `prompt_artifact.based_on_intent_revision_id`；每次生成产生新 `generation_id` / `prompt_artifact_id` / `OutputRef`（相对项目根路径 + MIME + 字节数 + sha256 测量点）；历史 revision / Prompt / Generation / Realization / Feedback 逐行 payload 逐字节不被覆盖（append-only，测试逐表比对）。 |

## 是否满足验收条件

**是。** 5 条验收条件逐条通过；任务书 10 条「必测场景」全部有显式测试映射（10/10）；
本步新增 101 条离线用例（`tests/feedback` 78 + `tests/realization` 新增 23），
全量 `uv run pytest -q` → **1834 passed, 3 deselected**（零网络、零真实凭据）；
范围命令 `tests/feedback` → 78 passed、`tests/realization` → 44 passed、
上游 `tests/workflow tests/generation tests/prompt_engine tests/providers` → 374 passed、
Rev.1 多目录 → 1257 passed；三个 `PYTHONHASHSEED` 下 122 passed。
未修改任何上游或既有文件（唯一写入是本步拥有的 `feedback/__init__.py`），未新增依赖，
未实现图片编辑 / 身份锁定 / Reference Image / KnowledgeEngine，未声称图片视觉不变，
未跳过重新确认，源码/测试/本文件中无明文 API key。

发现两处**上游冻结设计边界**并已用不扩大架构的方式处理：（a）Step 06 依赖边界测试禁止
`workflow/` import Step 07/08 模块 —— `review.py` 改用 payload → `FeedbackRequest.model_validate`
的兼容实现；（b）`ChangeSummary` 只承载路径级变化 —— "新 Intent 与旧 Realization 冲突"以
该路径 SET/CLEAR 的形态落地。另有一处**已知行为缺口**（clarify 回答经 Step 06 路径不触发
carry 失效）记录在「已知限制 1」并给出两条最小修订方案，不阻塞本步验收条件。
