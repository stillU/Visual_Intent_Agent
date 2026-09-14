# Step 05 交接记录：Interpreter 与 IntentEngine

任务：Step 05 — Interpreter 与 IntentEngine（唯一 LLM Provider 接入、受限语言解释、Pending Question 局部授权、Validator→Reducer→DecisionPolicy 编排）

## 完成内容

### 1. `providers/`（LLM 侧；`__init__.py` 保持空白）

| 文件 | 内容 |
|---|---|
| `providers/errors.py` | `ProviderError`（字段 `code` / `retryable` / `status_code` / `provider_request_id`；工厂 `auth/rate_limited/timeout/network/server/invalid_request/unparseable_response`）。错误六分类 + 可重试映射在构造时自检（code 与 retryable 矛盾即 `ValueError`）。异常文本绝不包含 key / Authorization 头。 |
| `providers/llm.py` | `LLMMessage` / `LLMRequest` / `LLMUsage` / `LLMResponse`（pydantic v2 frozen + extra=forbid）与 `LLMProvider` Protocol（`complete(request) -> LLMResponse`；`@runtime_checkable` 仅用于合同测试）。 |
| `providers/openai_llm.py` | `OpenAICompatibleLLMProvider(settings, client=None)`：`POST {base}/chat/completions`（httpx）、超时用 `Settings.llm_timeout_seconds`、仅可重试类按 `0.5s × 2^n` 退避且最多 `Settings.http_max_retries` 次、**不发送任何异步头**、响应体片段先做 key 脱敏再进错误消息。 |
| `providers/fake_llm.py` | `FakeLLMProvider(responses)`（队列或 Callable）：不读 Settings、不触网、记录 `.requests`、队列耗尽抛 `ProviderError.invalid_request`、输出逐字节确定。 |

### 2. `intent_engine/`

| 文件 | 内容 |
|---|---|
| `models.py` | `IntentResolveRequest`（`current_intent` / `message_id` / `message_text` / `pending_question: QuestionSpec｜None` / `available_message_ids`）、`InterpreterResult`（`candidate_deltas` / `detected_conflicts` / `unresolved_language` / `evidence_refs`）、`InterpreterError`（`.code` + `.retryable`，程序级）与 `InterpreterParseError`（解析失败，可恢复）。 |
| `prompts.py` | `INTERPRETER_PROMPT_VERSION = "interpreter.v1"`、`INTERPRETER_SYSTEM_PROMPT_V1`、`INTERPRETER_OUTPUT_SCHEMA`、`build_interpreter_user_prompt()`（最小上下文：当前 Intent JSON + 12 路径白名单 + 唯一 Pending Question 的 path/选项/是否可委托/文案 + 用户原文）。 |
| `interpreter.py` | `Interpreter(llm).interpret(request) -> InterpreterResult`：一次 LLM 调用 + 严格 JSON 解析；LLM 不输出 ID，`EvidenceRef.message_id` 由系统重填 `request.message_id`，回答 Pending Question 的 Delta 附上 `pending_question_id`；非法路径**不修复**，原样交 Validator。 |
| `engine.py` | `IntentEngine(interpreter).resolve(request) -> IntentResolution`：构建 `EvidenceContext` → Interpreter → `validate` → 保留被拒 issue → `reduce`（仅 accepted）→ `assess` → `model_copy(update={"applied_deltas": accepted, "issues": merged})`。 |
| `__init__.py` | 只再导出 `IntentEngine / Interpreter / IntentResolveRequest / InterpreterResult`（错误类型从 `intent_engine.models` 导入）。 |

### 3. Interpreter 边界落地（任务书「可以 / 不可以」）

- 只输出 Candidate Delta：完整 Intent payload（含 `subject/composition/.../resolutions/pinned_paths/intent`）被识别并判为解析失败 `interpreter.unparseable_output.full_intent`；**不猜测修复**。
- 证据：每条 Delta 必带 `EvidenceRef`（`message_id` 由系统重填），LLM 只提供 `evidence_fragment`（用户原文片段）；系统绝不把"用户没说"变成 `user_delegated`。
- 非法路径：输出 Schema 故意不把 `path` 限定为白名单（否则非法路径会被 Provider/解析层提前吞掉），任何非白名单路径都作为 Candidate Delta 交给 Step 02 Validator 显式拒绝。
- 不计算 `ready_for_confirmation`、不写 Workflow 状态、不持久化、不编译 Prompt、不调用图像模型。
- 解析失败 / Provider 失败：`IntentEngine.resolve` **不抛异常**，返回 `IntentResolution`（intent 原样、`applied_deltas=[]`、`ready_for_confirmation=False`、issue 可观察），交 Step 06 决定重试或澄清；Step 05 自身**不重试、不修复**（设计书"最多修复一次"由 Step 06 依 `InterpreterError.retryable` 执行）。
- Pending Question 局部授权：上下文只注入当前唯一 Pending Question；`answers_pending_question=true` 的 Delta 证据带上 `pending_question_id`，Validator 强制 `delta.path == pending_question_path`；`user_delegated` / `user_confirmed_proposal` 没有该绑定一律被拒（`validation.unauthorized_resolution_claim`）——**委托范围不可能扩大**。

### 4. 版本记录（prompt / schema）

| 项 | 值 |
|---|---|
| `INTERPRETER_PROMPT_VERSION` | `"interpreter.v1"` |
| 输出 Schema | `INTERPRETER_OUTPUT_SCHEMA`（顶层 `candidate_deltas` 必填；Delta 键 `operation/path/value/resolution/answers_pending_question/evidence_fragment`；`additionalProperties=false`；`operation` 与 `resolution` 为闭集枚举；`path` 故意为自由 string） |
| 调用参数 | `response_format={"type": "json_object"}`、`temperature` / `max_tokens` 不设置（保证真实模型默认行为）；真实模型默认 `VIA_LLM_MODEL`（`qwen3.8-max`） |
| 版本变更规则 | 改系统提示词或 Schema 必须同步提升 `INTERPRETER_PROMPT_VERSION` 并回写本记录 |

### 5. Interpreter 错误类型清单（命名空间 `interpreter.*`）

| code | 触发 | 可恢复 | `retryable` |
|---|---|---|---|
| `interpreter.unparseable_output.empty` | 模型返回空文本 | 是 | True |
| `interpreter.unparseable_output.invalid_json` | 文本非合法 JSON | 是 | True |
| `interpreter.unparseable_output.schema_violation` | JSON 与输出 Schema 不符（含未知键、未知 operation/resolution） | 是 | True |
| `interpreter.unparseable_output.full_intent` | 返回完整 Intent（禁止路径） | 是 | True |
| `interpreter.unparseable_output.invalid_delta` | 单条 Delta 违反 `domain.IntentDelta` 形状（如 SET 无 value/resolution） | 是 | True |
| `interpreter.unresolved_language`（Issue，severity=warning） | LLM 自报无法映射为明确值的语言片段 | 是 | — |
| `interpreter.conflict_detected`（Issue） | LLM 自报冲突（code 由系统覆盖，LLM 自报 code 只进 message） | 是 | — |
| `interpreter.pending_question_missing_id` | 传入 Pending Question 但无 `question_id`（程序级） | **否**（`IntentEngine` 向上抛） | False |
| `interpreter.pending_question_path_invalid` | Pending Question 的 `target_path` 不在白名单（程序级） | **否** | False |

> 多段 code 说明：`interpreter.unparseable_output.*` 以架构冻结名 `interpreter.unparseable_output`
> 为前缀（分类用前缀判断），诊断信息用后缀，与 Step 03 `policy.hard_conflict.*` 的既有约定一致。

## 变更文件

新增（**未修改任何上游或既有文件**；`providers/__init__.py` 保持 0 字节空白）：

- `visual_intent_agent/providers/errors.py`、`llm.py`、`openai_llm.py`、`fake_llm.py`
- `visual_intent_agent/intent_engine/__init__.py`、`models.py`、`prompts.py`、`interpreter.py`、`engine.py`
- `tests/providers/conftest.py`、`test_provider_errors.py`、`test_llm_models.py`、`test_fake_llm.py`、`test_openai_llm.py`
- `tests/intent_engine/ie_helpers.py`、`test_interpreter_prompts.py`、`test_interpreter_parsing.py`、`test_intent_engine_scenarios.py`、`test_intent_engine_failures.py`、`test_intent_engine_public_api.py`
- `tests/smoke/test_llm_smoke.py`
- `docs/handoffs/step_05_handoff.md`（本文件）

未触碰：根目录任务书 md、`README.md`、`AGENT_DISPATCH_PROMPTS.md`、`api.md`、`docs/ARCHITECTURE.md`、`domain/**`、`validation/**`、`policy/**`、`persistence/**`、`config.py`、`pyproject.toml`、`uv.lock`、`.env`、`tests/conftest.py`、`tests/test_sanity.py`、既有测试与 fixture。**无新增依赖**（仅用已装的 pydantic / httpx / pytest）。源码、测试、本文档中无明文 key（已用 `.env` 中 key 全文 grep 上述新文件，0 命中）。

## 公开接口

```python
# LLM Provider（providers/__init__.py 保持空白，从模块路径导入）
from visual_intent_agent.providers.llm import (
    LLMProvider,            # Protocol: complete(request: LLMRequest) -> LLMResponse
    LLMMessage, LLMRequest, LLMUsage, LLMResponse,
)
from visual_intent_agent.providers.openai_llm import OpenAICompatibleLLMProvider
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
from visual_intent_agent.providers.errors import (
    ProviderError, PROVIDER_ERROR_CODES, RETRYABLE_CODES,
    PROVIDER_AUTH, PROVIDER_RATE_LIMITED, PROVIDER_TIMEOUT,
    PROVIDER_NETWORK, PROVIDER_SERVER_ERROR, PROVIDER_INVALID_REQUEST,
    PROVIDER_UNPARSEABLE_RESPONSE,
)

# IntentEngine
from visual_intent_agent.intent_engine import (
    IntentEngine,           # IntentEngine(interpreter).resolve(IntentResolveRequest) -> IntentResolution
    Interpreter,            # Interpreter(llm).interpret(IntentResolveRequest) -> InterpreterResult
    IntentResolveRequest, InterpreterResult,
)
from visual_intent_agent.intent_engine.models import (
    InterpreterError, InterpreterParseError, PARSE_FAILURE_CODES, <全部 interpreter.* code 常量>
)
from visual_intent_agent.intent_engine.prompts import (
    INTERPRETER_PROMPT_VERSION, INTERPRETER_SYSTEM_PROMPT_V1,
    INTERPRETER_OUTPUT_SCHEMA, build_interpreter_user_prompt,
)
from visual_intent_agent.intent_engine.interpreter import (
    INTERPRETER_RESPONSE_FORMAT, unresolved_language_issues,
)
```

### 本步新增 Issue code

- `provider.*`：`provider.auth`、`provider.rate_limited`、`provider.timeout`、`provider.network`、`provider.server_error`、`provider.invalid_request`、`provider.unparseable_response`（Step 08 复用同一命名空间）。
- `interpreter.*`：见上文「Interpreter 错误类型清单」。
- `IntentResolution.issues` 合并顺序（冻结）：`validation.issues` → `interpreter.conflict_detected` / `interpreter.unresolved_language` → `policy.issues`；失败路径为 `[failure_issue, *policy.issues]`。

### 对 ARCHITECTURE.md 的最小修订建议（未自行修改文档）

1. **Step 06 依赖**：`IntentResolveRequest` 携带的 `pending_question` 若是 `PendingQuestion.to_spec()` 产物，必须带非空 `question_id`，否则 `Interpreter` 抛 `interpreter.pending_question_missing_id`（Step 02 的证据范围校验需要它）。建议在架构 4「Step 05/06」写明该前置条件。
2. **`unresolved_language` / `detected_conflicts` 的出口**：`IntentResolution` 没有对应字段，本步把它们转成 `issues` 中的 warning / `interpreter.conflict_detected`。若架构希望单独字段承载，需要一次显式模型扩展。
3. **`QuestionSpec.allow_delegate` 不参与确定性判定**：确定性门禁是 Step 03 的 `DecisionPolicy.delegatable`（两者在真实流程中同源）。建议在架构写明"`allow_delegate` 只约束问题展示与 LLM 提示"。

## 测试命令与结果

```text
$ cd /home/change/projects/image_system

# 本步范围（Fake + MockTransport，全离线）
$ uv run pytest tests/providers tests/intent_engine -q
138 passed in 0.29s        # providers 70 + intent_engine 68

# 上游既有测试未回归（逐目录运行；原因见「已知限制 1」）
$ uv run pytest tests/domain -q          -> 143 passed
$ uv run pytest tests/validation -q      -> 684 passed
$ uv run pytest tests/policy -q          -> 240 passed
$ uv run pytest tests/test_sanity.py -q  -> 5 passed

# 全量（含 Step 04 当前提交）
$ uv run pytest -q
1383 passed, 2 deselected in 3.32s

# 跨进程确定性
$ PYTHONHASHSEED=0 / 7 / 424242  uv run pytest tests/providers tests/intent_engine -q
-> 均 138 passed
$ 同一 resolve（Pending Question=lighting，「第二个」→ SET lighting.character）
  model_dump_json() 的 sha256 在三个 seed 下均为
  f79373750a830f131cd2af70fd46c9dce35ef6c547b77252d99f94eced0bd27f

# 真实 Provider smoke（唯一真实调用，共 2 次）
$ uv run pytest -m smoke -q
2 passed, 1383 deselected in 62.39s (0:01:02)
```

### 任务书「必测场景」→ 测试映射（8/8）

| # | 场景 | 测试 |
|---|---|---|
| 1 | "镜头拉远"只产生 `SET composition.framing` | `test_intent_engine_scenarios.py::test_scenario_1_zoom_out_only_sets_composition_framing` |
| 2 | "人物不变"产生 subject 范围 PIN 且不改值 | `::test_scenario_2_keep_the_person_pins_subject_without_changing_values` |
| 3 | "光线你决定"只委托 lighting 路径 | `::test_scenario_3_you_decide_lighting_only_delegates_lighting`（+ `3b` 不可委托路径被 policy 标记） |
| 4 | "背景不要海边"输出需澄清信息、不猜 location | `::test_scenario_4_negative_background_without_a_target_asks_for_clarification` |
| 5 | LLM 非法路径被 Validator 拒绝 | `::test_scenario_5_illegal_path_is_rejected_by_the_validator` |
| 6 | LLM 输出完整 Intent 解析失败 | `::test_scenario_6_full_intent_output_fails_to_parse` |
| 7 | Pending Question 为 lighting 时"第二个"只作用 lighting | `::test_scenario_7_second_option_only_touches_the_pending_lighting_path`（+ `7b` 越界回答被拒） |
| 8 | 相同输入经 Fake 得稳定 `IntentResolution` | `::test_scenario_8_same_input_yields_a_stable_intent_resolution` |

另覆盖：被拒 Delta 不静默吞、每个 applied Delta 可回溯到 message 证据、沉默绝不解释为 delegated、policy 与 validation issue 合并、pending 期间普通消息仍可改其它路径。

### 真实 smoke 结果（不含 key）

- `test_real_chat_completions_returns_json_text`：真实端点接受 `response_format={"type":"json_object"}`，返回非空、可 `json.loads` 且含 `candidate_deltas` 的对象。**通过**。
- `test_real_interpreter_parses_a_structured_response`：真实模型响应经 `Interpreter` 严格 Schema 解析为 `InterpreterResult`，每条 Delta 带系统重填的证据（`message_id=msg_smoke_0001`）。**通过**（该用例不强制 Delta 条数，避免把模型切分差异当失败，故不声称具体条数）。
- 两次调用均无 `ProviderError`；未出现 `provider.*` 错误分类。未做任何伪造。

## 已知限制

1. **显式多目录 pytest 命令与上游 `conftest` 命名冲突（既有问题，非本步引入）**：`tests/validation`、`tests/policy`、`tests/persistence` 的测试模块都用 `from conftest import ...`。当同一命令显式传入多个此类目录时，pytest 会先加载所有 conftest（同名模块互相覆盖），导致其中一方的测试收集期 `ImportError`。实证：`uv run pytest tests/domain tests/validation tests/policy tests/test_sanity.py -q` 在**不含本步任何文件**时同样失败；而递归全量 `uv run pytest -q` 与逐目录运行都全绿。本步因此按「逐目录 + 全量」验收。**最小修订建议**：各测试目录把共享工具放进唯一命名模块（本步用 `tests/intent_engine/ie_helpers.py`）或改用 fixture，不再 `from conftest import`。
2. **`unresolved_language` / `detected_conflicts` 只能经 `issues` 观察**：`IntentResolution` 无对应字段（架构冻结），本步转成 warning / `interpreter.conflict_detected` issue。若 Step 06 需要结构化读取未解析语言，建议一次显式模型扩展。
3. **解析失败不重试**：设计书"不合法输出最多修复一次"由 Step 06 按 `InterpreterError.retryable=True` 执行；Step 05 单次调用即返回可恢复 issue（有测试 `test_parser_never_retries_the_provider_by_itself` 钉住）。
4. **空 `candidate_deltas` 是合法结果**：用户消息无需修改时 LLM 可返回空列表，系统不视为失败；"无修改"与"解析失败"的区分由 code 保证。
5. **`allow_delegate` 不进确定性判定**：委托的确定性门禁是 Step 03 `DecisionPolicy.delegatable`（真实流程中 `allow_delegate` 由它派生）；若调用方手工构造不一致的 `QuestionSpec`，policy 而非 spec 说了算（`test_scenario_3b` 覆盖不可委托路径）。
6. **短回答的路径绑定依赖 LLM 标注 `answers_pending_question`**：委托类（`user_delegated` / `user_confirmed_proposal`）即使漏标也会被 Validator 拒绝，范围不可能扩大；但"值型 SET"在 pending 期间仍按普通消息证据处理（Step 02 冻结语义：待答问题只约束回答它的证据）。Step 06/评测需按此口径设计澄清文案。
7. **响应体片段进错误消息**：`provider.invalid_request` 等会带最多 200 字符的响应体片段以便排障，已先做 key 脱敏；若上游错误体含其它敏感信息，属上游责任（架构只要求不泄露凭据）。
8. **`InterpreterError`（程序级）会向上抛**：`IntentEngine` 只把 `InterpreterParseError` 与 `ProviderError` 转成可恢复 issue；Pending Question 缺 `question_id` 属调用方错误，按架构 5.6 抛出。

## 对下一步的输入

**Step 06（Workflow）**：

```python
from visual_intent_agent.intent_engine import IntentEngine, Interpreter, IntentResolveRequest
from visual_intent_agent.intent_engine.models import InterpreterError, InterpreterParseError

engine = IntentEngine(Interpreter(llm_provider))
resolution = engine.resolve(IntentResolveRequest(
    current_intent=current_intent,
    message_id=msg_id,
    message_text=text,
    pending_question=pending.to_spec() if pending else None,   # 必须带 question_id
    available_message_ids=frozenset(stored_message_ids) | {msg_id},
))
```

- `resolution.applied_deltas` 可回溯到 `EvidenceRef`；`resolution.question` 至多一个（Step 03 冻结）；
- `ready_for_confirmation=False` 且 issues 含 `interpreter.unparseable_output.*` / `provider.*` 时：按 `.retryable` 决定"最多一次重试"或转澄清；
- 真实调用错误分类：`provider.auth` / `provider.invalid_request` / `provider.unparseable_response` 不可重试，`provider.rate_limited` / `provider.timeout` / `provider.network` / `provider.server_error` 可重试（adapter 内部已按 `http_max_retries` 退避，最多再重试一次）。

**Fake Provider 夹具（Step 06/09 复用）**：

```python
from visual_intent_agent.providers.fake_llm import FakeLLMProvider
provider = FakeLLMProvider(['{"candidate_deltas": [...], "unresolved_language": []}'])      # 队列
provider = FakeLLMProvider(lambda request: '{"candidate_deltas": []}')                      # 回调（可抛 ProviderError）
provider.requests  # 记录全部 LLMRequest，供断言提示词/上下文
```

**Step 08（图像 Provider）**：复用 `providers/errors.py`（`ProviderError` 六分类 + 可重试映射）与第 6.3 节策略；`providers/__init__.py` 已被本步保持空白，Step 08 不得修改。

## 验收条件逐条核对

| # | 任务书验收条件 | 核对结果 |
|---|---|---|
| 1 | LLM 无法绕过 Validator / Reducer / DecisionPolicy | 是：`engine.resolve` 是唯一编排入口，顺序固定 Interpreter→`validate`→`reduce`→`assess`；非法路径（场景 5）、越界授权（场景 7b、沉默→delegated）均在 Validator 被拒且状态零变化；`test_intent_engine_public_api.py` 断言 intent_engine 不 import persistence/config/httpx/sqlite3。 |
| 2 | 所有状态修改都能回溯到 Candidate Delta 和 EvidenceRef | 是：`IntentResolution.applied_deltas` 即 Validator 接受的 Delta，每条带系统重填的 `EvidenceRef`（`test_every_applied_delta_traces_back_to_the_user_message`）；Reducer 只应用 accepted。 |
| 3 | Pending Question 的委托范围不会扩大 | 是：上下文只注入唯一 Pending Question 的 path/选项/是否可委托；`user_delegated` / `user_confirmed_proposal` 必须有绑定该问题的证据，且 `delta.path == target_path`（否则 `validation.evidence_pending_question_path_mismatch`）；场景 3/7/7b 覆盖。 |
| 4 | 无数据库硬依赖，便于与 Step 04 独立测试 | 是：本步全部测试离线（Fake + `httpx.MockTransport`），不 import sqlite3/persistence；净子进程导入断言零 DB/config/httpx。 |
| 5 | 解析失败可观察、可恢复 | 是：5 类解析失败 → `interpreter.unparseable_output.*` code + `retryable=True`，`ready_for_confirmation=False`、intent 原样、不抛异常、不猜测修复（`test_intent_engine_failures.py` 8 例）；Provider 失败同理输出 `provider.*`。 |

## 是否满足验收条件

是。5 条验收条件逐条通过；任务书 8 条「必测场景」全部有测试映射；本步范围 138 个用例全绿，上游既有测试逐目录全绿，递归全量 `uv run pytest -q` → **1383 passed, 2 deselected**（零网络；Step 04 并行工作未被触碰）；真实 smoke `uv run pytest -m smoke -q` → **2 passed**（明确记录，未伪造）；三个 `PYTHONHASHSEED` 下本步测试与示例 `IntentResolution` 逐字节一致；未实现任何后续步骤能力（无持久化/确认/Prompt 编译/图像调用/第二 Provider/Agent 框架），未修改任何上游或既有文件，未新增依赖，源码/测试/本文件中无明文 key。
