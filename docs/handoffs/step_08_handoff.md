# Step 08 交接记录：图像生成闭环与 GenerationArtifact

任务：Step 08 — 唯一 Image Provider Adapter：从已确认的 PromptArtifact 真实生成图片，保存可
追踪的 GenerationArtifact，并正确处理 `WAITING_REVIEW` / `FAILED` / retry。**不修改 Intent 或
Prompt，不实现多模型与 Reference Image。**

- 依据：`08_generation_pipeline.md`（任务书）、`README.md`（10 条全局不变量与统一交接格式）、
  `docs/ARCHITECTURE.md` 第 4 节「Step 08 — providers(image) + generation」冻结表 / 第 5.6 节
  错误处理 / 第 6 节 Provider 事实与错误六分类 / 第 7 节测试策略（Rev.1）、
  `docs/handoffs/step_04_handoff.md`、`step_04_patch_001.md`、`step_05_handoff.md`、
  `step_06_handoff.md`、`step_07_handoff.md`、`architecture_decision_001.md`。
- 范围纪律：只新增 `visual_intent_agent/providers/{image,openai_image,fake_image}.py`、
  `visual_intent_agent/generation/{models,pipeline,__init__}.py`、`tests/providers/` 的图像侧文件、
  `tests/generation/**`、`tests/smoke/test_image_smoke.py` 与本文件；**未修改任何上游或既有文件**
  （`domain/`、`validation/`、`policy/`、`persistence/`、`intent_engine/`、`providers/` 的 LLM 侧、
  `workflow/`、`prompt_engine/`、`realization/`、`config.py`、`pyproject.toml`、`uv.lock`、`.env`、
  根目录任务书、`README.md`、`docs/ARCHITECTURE.md`）。
  `providers/__init__.py` 保持 **0 字节**；`generation/` 是**单 Step 拥有**的包，故
  `generation/__init__.py`（原 0 字节）按冻结表再导出公开面。**无新增依赖**（httpx 已在 Step 05
  引入；图像侧只用 pydantic + httpx + stdlib）。

---

## 完成内容

### 1. `providers/image.py` — 唯一图像合同与 Protocol

| 公开名 | 形状（与冻结表逐字一致） |
|---|---|
| `ImageGenerationRequest` | `prompt: str`；`size: str`（必须 `"<w>x<h>"`）；`model: str\|None=None`（None→`Settings.image_model`） |
| `GeneratedImage` | `content: bytes`；`mime_type: str` |
| `ImageGenerationResult` | `model: str`；`provider_request_id: str\|None`；`images: list[GeneratedImage]`（≥1）；`seed: int\|None=None` |
| `ImageProvider(Protocol)` | `generate(ImageGenerationRequest) -> ImageGenerationResult`（`runtime_checkable`） |

- 全部模型 `frozen=True, extra="forbid"`；`size` 用正则 `^[1-9]\d{0,4}x[1-9]\d{0,4}$` 在边界拒绝
  （`"1024"`、`"0x1024"`、`"1024X1024"`、前后空白、非法字符等一律拒绝，不猜测修复）；
- `GeneratedImage.content` 用 `mode="before"` 校验**只接受原始字节**（pydantic 的 lax 模式会把
  `str` 编码成 bytes，这里显式拒绝，避免文本被当成图片）；
- `seed` / `provider_request_id` **不伪造**：缺失即 `None`；`seed=True` 被显式拒绝（bool 不是 seed）；
- 结果**没有 URL 字段**（`ImageGenerationResult` / `GeneratedImage` 均无 `url`，有测试钉住）；
- 便捷纯函数 `is_valid_image_size(size)` + 常量 `IMAGE_MIME_TYPE_PNG` /
  `IMAGE_MIME_TYPE_OCTET_STREAM`。

### 2. `providers/openai_image.py` — 唯一真实 adapter

`OpenAIImageProvider(settings: Settings, client: httpx.Client | None = None)`：

1. `POST {base_url}/images/generations`，payload **恰好** `{model, prompt, size}`；
2. 头只含 `Authorization: Bearer <key>` / `Content-Type` / `Accept`；**不设置任何 `async` 头**
   （该 key 会 403；有运行时断言 + AST 静态断言）；
3. 成功 → 解析 `{created, data:[{url}], usage}`，**在同一 adapter 内**对每个 `url` 发
   `GET` 下载字节；`ImageGenerationResult.images[i].content` 即字节；
4. 错误六分类复用 `providers/errors.py`：401/403→auth、429→rate_limited、5xx→server_error、
   超时→timeout、连接/DNS→network、其余 4xx→invalid_request、JSON 结构不符→unparseable_response；
   仅可重试类按 `0.5s × 2^n` 退避、最多 `Settings.http_max_retries` 次；
5. 超时用 `Settings.image_timeout_seconds`（**含下载**）；
6. **下载失败只重试下载，绝不重新 POST**（POST 会再次出图/计费）；有测试断言 POST 恰好一次；
7. **下载签名 URL 绝不携带 `Authorization`**（签名 URL 属于第三方 OSS 主机；带 key 等于泄露凭据）；
8. MIME 先取响应头 `image/*`，否则依据 PNG/JPEG/GIF/WEBP magic，最后诚实回退
   `application/octet-stream`（不猜测格式）；
9. 脱敏：错误文本先替换 key，再用正则把所有 `http(s)://…`（含签名 query）替换为 `<url>`，最后
   截断 200 字符；`ProviderError` 消息**绝不含** httpx 异常类型名以外的原始异常细节、key 或 URL；
10. `close()` 只关闭自建 client（注入的 client 归调用方）。

### 3. `providers/fake_image.py` — 默认测试唯一图像依赖

- `FakeImageProvider(responses=None, *, model=..., mime_type=..., provider_request_id=..., seed=None)`；
- 默认（`responses=None`）每次返回**确定性 70 字节 1×1 RGBA PNG 常量** `FAKE_PNG_BYTES`
  （magic `\x89PNG\r\n\x1a\n`，IHDR/IDAT/IEND 三个 chunk 的 CRC 均已验证）；
- 记录 `.requests: list[ImageGenerationRequest]`；接口与真实 adapter 完全一致（同一 Protocol，
  有 `isinstance` 合同测试）；构造不读 `Settings`、不触网（模块 import 也不触网）；
- 支持 `Iterable[ImageGenerationResult | bytes]` 脚本与 `Callable` 回调（回调内可抛
  `ProviderError` 模拟失败）；脚本耗尽或元素非法抛 `ProviderError.invalid_request`；
- `seed` 恒为 `None`（Fake 也遵守"不返回就不得伪造"）。

### 4. `generation/models.py` — OutputRef / GenerationArtifact

| 公开名 | 形状 |
|---|---|
| `OutputRef` | `path: str`（相对项目根，形如 `outputs/generations/<generation_id>/image_1.png`，拒绝绝对路径与 URL）；`mime_type: str`；`byte_size: int`（≥0）——**不存临时 URL** |
| `GenerationArtifact` | `schema_version`；`generation_id`；`session_id`；`prompt_artifact_id`；`target_model`；`model_version: str\|None`；`parameters: PromptParameters`（**复用 Step 07 类型**）；`seed: int\|None`；`output_refs: list[OutputRef]`；`provider_request_id: str\|None`；`created_at` |
| `GenerationError` | 带 `.code`（`generation.*`，构造时自检，未知 code → `ValueError`） |

- 全部 `frozen=True, extra="forbid"`；`created_at` 与 Step 01/07 同约定（naive 拒绝、UTC 归一、
  JSON `+00:00`）；`seed` 显式拒绝 bool；`model_version` / `provider_request_id` 为 `None` 或非空。
- 定位四问全部可答：`prompt_artifact_id`（哪个 Prompt）、`target_model` + `model_version` +
  `parameters`（哪个模型/参数）、`provider_request_id`（哪次 Provider 请求，缺失即 None）、
  `generation_id`（反馈外键目标）。

### 5. `generation/pipeline.py` — GenerationPipeline

`GenerationPipeline(repo, prompt_engine, image_provider, output_dir)`：

**`generate(session_id)`（严格按冻结流程）**

1. 快照必须 `WAITING_CONFIRMATION`（否则 `generation.invalid_state`，状态不变）；
2. `latest_confirmation_id` 非空且 `is_confirmation_valid` 为真（否则
   `generation.no_valid_confirmation`，状态不变、不调用 Provider）；
3. `transition GENERATING` → `PromptEngine.compile(PromptCompileRequest)`（其内部复核确认）；
4. `ImageProvider.generate(ImageGenerationRequest(prompt, size, model))` —— 三字段全部来自
   PromptArtifact，不新增参数面；
5. 字节写入 `{output_dir}/{generation_id}/image_{i}.png`（i 从 1 起）；
6. `append_generation_artifact(gen_id, sid, {"prompt_artifact_id": ...}, payload)`；
7. `transition WAITING_REVIEW`。

**失败语义（绝不停留在 GENERATING）**

| 失败点 | 状态 | Artifact | 上抛 |
|---|---|---|---|
| `ProviderError` | `FAILED` | **不写** | `ProviderError` 原样 |
| `PromptCompilationError`（含 `prompt.no_valid_confirmation`） | `FAILED` | 不写 | `PromptCompilationError` 原样 |
| 字节落盘 `OSError` | `FAILED` | 不写 | `GenerationError(generation.output_write_failed)` |
| `append_generation_artifact` 失败 | `FAILED` | 不写 | `RepositoryError` 原样 |
| 复核迁移失败（Artifact 已保存） | `FAILED` | **已保存** | 原异常（见已知限制 5） |

每次失败都写一条结构化日志：`event="generation.failed"` + `session_id` / `generation_id` /
`prompt_artifact_id` / `reason_code`（`provider.*` / `prompt.*` / `persistence.*` /
`generation.*` 注册 code）/ `retryable` / `status_code` / `provider_request_id`；不含 key 与 URL。

**`retry(session_id, generation_id)`（仅 FAILED）**

1. 快照必须 `FAILED`（否则 `generation.invalid_state`，状态不变）；
2. 读原 GenerationArtifact（缺失 → `generation.artifact_not_found`；跨会话 →
   `generation.session_mismatch`）→ 取 `refs["prompt_artifact_id"]` → 读回**同一** PromptArtifact；
3. 校验该 PromptArtifact 的 `based_on_confirmation_id` 仍有效（否则
   `generation.no_valid_confirmation`，状态不变、不调用 Provider）；
4. `transition GENERATING` → **直接用同一 PromptArtifact 调用 Provider（绝不重新 compile）** →
   新 `generation_id` → 落盘 → 新 GenerationArtifact（同一 `prompt_artifact_id`）→
   `WAITING_REVIEW`。旧 Artifact 与旧文件逐字节保持。

**边界**：不修改 Intent、不重新编译 Prompt（retry）、不判断图片质量、不做多模型路由、无
Reference Image、无队列；output 只是本地下载件，不是 RealizationState 的事实来源。

### 6. `generation/__init__.py`

再导出 `GenerationPipeline` / `GenerationArtifact` / `OutputRef` / `GenerationError` /
`GENERATION_FAILED_EVENT` / 5 个 `generation.*` code / `GENERATION_ID_PREFIX` /
`OUTPUT_FILE_NAME_TEMPLATE` / `GENERATION_ERROR_CODES`。

### 7. 测试

- `tests/providers/`（图像侧 80 例）：`test_image_models.py`、`test_fake_image.py`、
  `test_openai_image.py`，共享工具放唯一命名模块 `image_helpers.py`（无 `from conftest import`，
  复用 Step 05 的 `fake_settings` / `mock_client_factory` fixture）。
- `tests/generation/`（56 例）：`generation_helpers.py`（唯一命名模块，自包含）+ 模型 / 流水线 /
  公开面 / P2 端到端四个测试文件。
- `tests/smoke/test_image_smoke.py`：`@pytest.mark.smoke`，真实出图。

---

## 变更文件

新增：

- `visual_intent_agent/providers/image.py`
- `visual_intent_agent/providers/openai_image.py`
- `visual_intent_agent/providers/fake_image.py`
- `visual_intent_agent/generation/models.py`
- `visual_intent_agent/generation/pipeline.py`
- `tests/providers/image_helpers.py`
- `tests/providers/test_image_models.py`
- `tests/providers/test_fake_image.py`
- `tests/providers/test_openai_image.py`
- `tests/generation/generation_helpers.py`
- `tests/generation/test_generation_models.py`
- `tests/generation/test_generation_pipeline.py`
- `tests/generation/test_generation_public_api.py`
- `tests/generation/test_generation_e2e.py`
- `tests/smoke/test_image_smoke.py`
- `docs/handoffs/step_08_handoff.md`（本文件）

修改（唯一一处，且属本步拥有的包）：

- `visual_intent_agent/generation/__init__.py`（原 0 字节空文件 → 再导出本步公开面）

未触碰：根目录任务书 md、`README.md`、`AGENT_DISPATCH_PROMPTS.md`、`api.md`、
`docs/ARCHITECTURE.md`、其余所有 handoff、`visual_intent_agent/{domain,validation,policy,
persistence,intent_engine,workflow,prompt_engine,realization,config.py}`、
`visual_intent_agent/providers/{__init__.py,errors.py,llm.py,openai_llm.py,fake_llm.py}`、
`tests/{domain,validation,policy,persistence,intent_engine,workflow,prompt_engine,realization,e2e,
fixtures}`、`tests/conftest.py`、`tests/test_sanity.py`、`pyproject.toml`、`uv.lock`、`.env`。
`visual_intent_agent/providers/__init__.py` 与 `realization/__init__.py` 仍为 0 字节（有测试钉住前者）。

---

## 公开接口

```python
from visual_intent_agent.providers.image import (
    ImageProvider, ImageGenerationRequest, GeneratedImage, ImageGenerationResult,
    IMAGE_MIME_TYPE_PNG, IMAGE_MIME_TYPE_OCTET_STREAM, IMAGE_SIZE_PATTERN, is_valid_image_size,
)
from visual_intent_agent.providers.openai_image import OpenAIImageProvider, BACKOFF_BASE_SECONDS
from visual_intent_agent.providers.fake_image import (
    FakeImageProvider, FAKE_PNG_BYTES, FAKE_IMAGE_MODEL, FAKE_IMAGE_MIME_TYPE,
    FAKE_PROVIDER_REQUEST_ID,
)
from visual_intent_agent.generation import (
    GenerationPipeline, GenerationArtifact, OutputRef, GenerationError,
    GENERATION_FAILED_EVENT, GENERATION_ID_PREFIX, OUTPUT_FILE_NAME_TEMPLATE,
    GENERATION_ERROR_CODES, GENERATION_INVALID_STATE, GENERATION_NO_VALID_CONFIRMATION,
    GENERATION_ARTIFACT_NOT_FOUND, GENERATION_SESSION_MISMATCH,
    GENERATION_OUTPUT_WRITE_FAILED,
)
# 内部（测试与诊断）：
from visual_intent_agent.generation.pipeline import _project_relative_path
```

### 本步新增 Issue / Error code（命名空间 `generation.*`，ARCHITECTURE 5.5）

| code | 承载 | 触发条件 |
|---|---|---|
| `generation.invalid_state` | `GenerationError` | `generate` 非 `WAITING_CONFIRMATION`；`retry` 非 `FAILED` |
| `generation.no_valid_confirmation` | `GenerationError` | 无 `latest_confirmation_id` / `is_confirmation_valid` 为假 / retry 的原 PromptArtifact 绑定已失效的确认 |
| `generation.artifact_not_found` | `GenerationError` | retry 找不到原 GenerationArtifact（或它引用的 PromptArtifact 缺失） |
| `generation.session_mismatch` | `GenerationError` | retry 引用的 Generation/Prompt Artifact 属于另一个会话 |
| `generation.output_write_failed` | `GenerationError` | 生成字节落盘失败 |

`ProviderError` / `PromptCompilationError` / `RepositoryError` 的 code 语义不变，本步只消费不改写。

### Repository 使用（不绕过协议直读 SQLite）

`get_current_session_snapshot`、`is_confirmation_valid`、`transition_state`、
`append_generation_artifact`、`get_generation_artifact`、`list_generation_artifacts`、
`get_prompt_artifact`。`append_generation_artifact` 的 refs 严格为冻结必填键
`{"prompt_artifact_id"}`（有测试断言逐字一致）。

### Provider 事实落地（ARCHITECTURE 6.2，不重复探测）

`POST {base}/images/generations` 同步 200；`model="qwen-image-3.0"`；`size` 必须 `"<w>x<h>"`；
响应 `{created, data:[{url}], usage}`；`url` 为带 `Expires` 的临时签名地址 → adapter 在响应内
下载字节，系统任何位置（Artifact / 日志 / 异常 / 测试）都不得只存 URL；禁止 `X-DashScope-Async`。

### 对 ARCHITECTURE.md 的最小修订建议（未自行修改文档）

1. **retry 与"失败不写 Artifact"存在冻结设计缺口**（最重要，见已知限制 1）：
   `generate` 在 `ProviderError` 时不写 GenerationArtifact，而 `retry(session_id, generation_id)`
   又被冻结为"加载原 GenerationArtifact 的 prompt_artifact_id"。二者无法同时满足：Provider 失败
   后**没有**原 Artifact，retry 拿不到要复用的 PromptArtifact（Repository 也没有
   `list_prompt_artifacts` / `get_latest_prompt_artifact`）。请架构方在下列最小方案中裁定其一：
   (a) Repository 增加只读 `list_prompt_artifacts(session_id)`（或
   `get_latest_prompt_artifact(session_id)`），retry 在无原 Artifact 时回退到"当前确认最近的
   PromptArtifact"；仍然不得重新 compile；
   (b) `retry` 增加可选 `prompt_artifact_id` 参数（由调用方在失败响应中带回）；
   (c) 明确"失败即不可 retry，必须重新确认/重新生成"，并把任务书必测场景 5 限定为
   "复核迁移失败"这一类失败现场。
   本步按冻结签名实现，并额外把"Artifact 已保存但复核迁移失败"处理成 `FAILED`，使该路径下的
   retry 真实可用（见已知限制 5）。若架构方采纳 (a)/(b)，Step 08 只需极小改动。
2. **`model_version` 语义**：冻结表未说明取值。本步取 `ImageGenerationResult.model`（Provider 在
   响应里报告的模型标识；Provider 未报告时 adapter 回填请求/配置模型名，不编造版本号）。建议在
   Rev.2 注明"`target_model` = 请求的目标模型；`model_version` = Provider 报告的模型标识"。
3. **`OutputRef.path` 的相对基准**：冻结为"相对项目根"。默认 `output_dir` 下即
   `outputs/generations/<gen>/image_1.png`；当调用方传入项目根之外的 `output_dir`（测试 `tmp_path`）
   时，本步用 `os.path.relpath` 生成 `../../…` 形态的相对路径（仍满足"相对项目根、可重新定位、
   绝不是 URL"）。建议在冻结表补一句"用 `PROJECT_ROOT / path` 还原"。
4. **测试布局**：ARCHITECTURE 第 1 节把 P2(08) 端到端放在 `tests/e2e/`，本步派发提示词要求放在
   `tests/generation/`。本步按派发提示词放在 `tests/generation/test_generation_e2e.py`（与
   Step 07 把编译端到端放 `tests/prompt_engine/` 的先例一致）；若架构方要求归位 `tests/e2e/`，
   迁移是纯移动 + import 调整，不影响产品代码。

---

## 测试命令与结果

```text
$ cd /home/change/projects/image_system

# 基线（实施前）
$ uv run pytest -q
1585 passed, 2 deselected

# 全量（基线 1585 + 本步 136 条离线用例）
$ uv run pytest -q
1721 passed, 3 deselected in 4.10s

# 本步范围
$ uv run pytest tests/generation -q
56 passed
$ uv run pytest tests/providers -q
150 passed        # 其中图像侧新增 80 条（image_models + fake_image + openai_image）
$ uv run pytest tests/generation tests/providers -q
206 passed

# Rev.1 上游多目录验收命令（未回归）
$ uv run pytest tests/persistence tests/domain tests/validation tests/policy tests/test_sanity.py -q
1247 passed in 2.88s
$ uv run pytest tests/prompt_engine tests/realization -q
113 passed

# 跨进程确定性
$ PYTHONHASHSEED=0 / 7 / 424242 uv run pytest tests/generation tests/providers -q
-> 均 206 passed

# 依赖边界（干净子进程导入 generation + providers.image）
-> 只加载 domain/persistence/prompt_engine/realization/providers.{image,errors}/config；
   未拉入 httpx、workflow、intent_engine、providers.fake_image、providers.openai_image。

# 真实 Provider smoke（默认被 pyproject 排除）
$ uv run pytest -m smoke -q
3 passed, 1721 deselected in 75.65s        # 2 条 Step 05 LLM smoke + 本步 1 条图像 smoke
$ uv run pytest tests/smoke/test_image_smoke.py -m smoke -v
tests/smoke/test_image_smoke.py::test_real_image_generation_produces_exactly_one_png PASSED
1 passed in 49.30s
```

Smoke 的真实结果（不含 key、URL 全文）：真实 `POST /images/generations` 同步返回 200，adapter 在
响应内下载到**非空字节且以 PNG magic `\x89PNG\r\n\x1a\n` 开头**的图片（恰好 1 张），
`GenerationArtifact` 成功落库、`refs == {"prompt_artifact_id": …}`、文件存在于 `tmp_path` 输出目录、
状态进入 `WAITING_REVIEW`；Artifact payload 与日志中均无明文 key、无 `http(s)://`、无 `Expires=` /
`Signature=`；smoke 全程输出到 `tmp_path`，仓库 `outputs/` 运行后仍为空。

默认测试全部离线：FakeImageProvider + tmp_path SQLite/输出目录；零网络、零真实凭据
（对新增文件 grep `sk-` 0 命中；`openai_image.py` 的 `httpx` 只在 `httpx.MockTransport` 下被测试）。

### 任务书「必测场景」→ 测试映射（7/7）

| # | 场景 | 测试 |
|---|---|---|
| 1 | 未确认时无法调用 Provider | `test_generation_pipeline.py::test_generate_requires_waiting_confirmation_state`、`::test_generate_without_any_confirmation_is_rejected_before_any_provider_call` |
| 2 | PromptArtifact 与当前 Confirmation 不匹配时拒绝生成 | `::test_generate_rejects_a_confirmation_invalidated_by_a_new_revision`、`::test_retry_rejects_a_prompt_artifact_whose_confirmation_is_stale` |
| 3 | 成功后 Artifact 完整且 `WAITING_REVIEW` | `::test_successful_generation_writes_bytes_and_saves_a_complete_artifact`、`::test_provider_request_is_built_only_from_the_prompt_artifact`、`::test_multiple_returned_images_are_written_in_order`、`test_generation_e2e.py::test_p2_p1_script_reaches_waiting_review_with_a_traceable_artifact` |
| 4 | Provider 失败 → `FAILED`，无伪造 Artifact | `::test_provider_failure_transitions_to_failed_without_an_artifact`、`::test_prompt_compile_failure_transitions_to_failed_without_an_artifact`、`test_generation_e2e.py::test_p2_provider_failure_is_observable_and_writes_no_artifact` |
| 5 | Retry 使用同一 PromptArtifact | `::test_retry_reuses_the_same_prompt_artifact_without_recompiling`、`::test_retry_provider_failure_returns_to_failed_and_keeps_history`、`::test_retry_requires_failed_state`、`::test_retry_rejects_an_artifact_from_another_session`、`test_generation_e2e.py::test_p2_retry_reuses_the_original_prompt_artifact_and_keeps_history` |
| 6 | 多次生成不同 generation ID，不覆盖历史 | `::test_multiple_generations_have_distinct_ids_and_preserve_history`、`::test_generate_never_modifies_the_intent_or_existing_prompt_artifacts` |
| 7 | Feedback 外键可精确引用 GenerationArtifact | `::test_generation_artifact_is_referenceable_by_a_feedback_foreign_key`、`::test_feedback_cannot_reference_another_sessions_generation` |

另覆盖：模型字段面 / frozen / extra / 时间戳 / 无 URL（`test_generation_models.py` 19 例）、
公开面与依赖纪律（`test_generation_public_api.py` 11 例）、OpenAI 与 Fake 合同
（`tests/providers/` 图像侧 80 例，含错误六分类、退避、下载不重 POST、下载不带 Authorization、
key + URL 脱敏、默认 timeout、client 所有权、Protocol 一致性）。

---

## 已知限制

1. **Provider 失败后的 retry 无法复用 PromptArtifact（冻结设计缺口，最重要）**：任务书 Workflow
   规则 4 与冻结表要求"Provider 失败不写 Artifact"，而 `retry` 又要求"加载原 GenerationArtifact
   的 `prompt_artifact_id`"。Provider 失败现场因此**没有**原 Artifact，`retry` 会抛
   `generation.artifact_not_found`（有测试钉住该行为）。本步不写任何"失败 Artifact"（遵守
   "不伪造 Artifact"），也不新增 Repository 方法（不扩大架构）。最小修订建议见「公开接口」第 1 条，
   由架构方裁定；Step 09 如需"失败后一键重试"，应先落地该修订。
2. **`retry` 的可达失败现场只有一类**：Artifact 已保存但 `WAITING_REVIEW` 迁移失败。本步把这种
   情况显式转为 `FAILED`（避免停留在 `GENERATING`），从而让该 Artifact 可被 retry 复用。测试用
   `monkeypatch` 模拟这一迁移失败（`generation_helpers.fail_once_on_review_transition`）；这是
   通过系统路径得到"FAILED + 原 GenerationArtifact"的唯一方式。
3. **失败即转 `FAILED` 的范围**：冻结表只写了"`ProviderError` → `FAILED`"。本步把
   `PromptCompilationError`、落盘 `OSError`、Artifact 落库失败、复核迁移失败也一并转为 `FAILED`，
   以免会话卡在 `GENERATING`（`GENERATING → FAILED` 是已冻结迁移，未新增任何迁移）。若架构方
   希望编译失败回到 `WAITING_CONFIRMATION`，属行为变更，需架构裁定。
4. **`model_version` 取 Provider 报告的模型名**（`ImageGenerationResult.model`），可能等于
   `target_model`；Provider 不报告时 adapter 回填请求/配置模型名（不编造版本号）。
5. **`OutputRef.path` 对项目根之外的 `output_dir` 会含 `..`**（如测试 `tmp_path`）：仍是"相对项目
   根"的稳定路径且可用 `PROJECT_ROOT / path` 还原，不是 URL；默认 `output_dir` 下就是冻结表给出的
   `outputs/generations/<gen>/image_1.png`（有专门用例钉住该形态）。
6. **smoke 不跑真实 LLM**：为把真实调用控制在**恰好 1 次图像生成**，smoke 用确定性代码装配
   "已确认会话"（Intent/Execution/Confirmation），只让 `PromptEngine.compile` 与
   `OpenAIImageProvider` 走真实路径；P1 全链路（真实 LLM 多轮）由离线 P2 e2e 覆盖。
7. **smoke 的日志断言只覆盖本项目包 logger**（`visual_intent_agent`）：第三方 HTTP 库的 DEBUG 日志
   不在本步职责内（默认不开启 DEBUG）。本步 adapter 自身不记录 key/URL。
8. **未实现（属禁止范围 / 后续步骤）**：多模型路由、图片理解与质量自动判断、队列集群、
   Reference Image、seed/negative_prompt 等额外参数、FeedbackEngine / carry / 多轮修改（09）、
   Web API、RAG（11）。`OutputRef` 的 `mime_type` 由响应头/magic 决定但落盘扩展名按冻结流程固定
   `.png`（真实 Provider 返回 PNG，smoke 已证实）；若未来 Provider 改变返回格式，需要架构裁定。

---

## 对下一步的输入

### Step 09（FeedbackEngine / RealizationState / ReviewService）

**1. 读取 GenerationArtifact（唯一推荐路径）**

```python
from visual_intent_agent.generation import GenerationArtifact

stored = repo.get_generation_artifact(generation_id)          # StoredArtifact 信封
generation = GenerationArtifact.model_validate_json(stored.payload)
assert stored.refs == {"prompt_artifact_id": generation.prompt_artifact_id}
history = repo.list_generation_artifacts(session_id)          # 按 created_at, rowid 升序，append-only
prompt_artifact = PromptArtifact.model_validate_json(
    repo.get_prompt_artifact(generation.prompt_artifact_id).payload
)
```

- `GenerationArtifact` 只有 **ID 引用**（prompt_artifact_id / session_id），不含 Intent/Prompt/Realization
  对象：FeedbackEngine 需要 Intent 时从 `prompt_artifact.based_on_intent_revision_id` 经
  `repo.get_intent_revision` 取；需要 Realization 时用 `prompt_artifact.realization_refs` /
  `repo.get_current_realization_state`。
- `parameters` 是 Step 07 的 `PromptParameters`（只有 `size`）；`seed` 可能为 `None`（Provider 不返回），
  Step 09 不得把它当成"用了 seed=0/随机"来推断视觉是否可复现。
- 图片字节：`(PROJECT_ROOT / ref.path).read_bytes()`；`ref.mime_type` / `ref.byte_size` 可用于校验。
  **不要**期待任何临时 URL（系统任何位置都不存 URL）。

**2. 反馈入口的精确关联方式**

- `feedback_results` 的冻结必填 ref 键是 `{"generation_id"}`：
  `repo.append_feedback_result(feedback_id, session_id, {"generation_id": generation.generation_id}, payload)`。
  外键存在性由 SQLite 保证（不存在的 generation_id → `persistence.foreign_key_violation`；
  跨会话 → `persistence.ref_session_mismatch`；均有测试）。
- `FeedbackResult.generation_id` 应与该 ref 一致；`PromptArtifact` 由 `generation.prompt_artifact_id`
  解析得到，供 `FeedbackRequest` 使用。

**3. retry / 失败状态**

- 失败可观察：`session.workflow_state is FAILED` + 结构化日志（logger
  `visual_intent_agent.generation.pipeline`，`record.event == "generation.failed"`，
  `record.reason_code` ∈ `provider.*` / `prompt.*` / `persistence.*` / `generation.*`）。
- `GenerationPipeline.retry(session_id, generation_id)` **仅在 `FAILED` 可用**，复用原 PromptArtifact、
  产生**新** generation_id；失败时回到 `FAILED` 且不新增 Artifact。
- ReviewService 若在 `WAITING_REVIEW` 提供"重做/重试"，直接调用 `retry` 会抛
  `generation.invalid_state`（`WAITING_REVIEW → FAILED` 不是冻结迁移）。Step 09 不要自行扩展迁移表：
  Provider 失败后的重试缺口与可达性见「已知限制 1/2」，需要时走最小修订（建议 a/b/c）。
- 失败后**不要**用 `generate` 代替 retry：`generate` 会重新 compile（可能产生不同 Prompt/Realization），
  违反"retry 必须引用原 PromptArtifact"。

**4. FakeImageProvider 测试夹具**

```python
from visual_intent_agent.providers.fake_image import FakeImageProvider, FAKE_PNG_BYTES

provider = FakeImageProvider()                                  # 每次返回确定性 70B PNG 常量
provider = FakeImageProvider([b"one", b"two"])                  # 脚本化字节
provider = FakeImageProvider(lambda request: my_result)         # 回调（可抛 ProviderError 模拟失败）
assert provider.requests[-1].prompt == expected_prompt          # 记录全部入参
```

`tests/generation/generation_helpers.py` 已提供 `make_pipeline` / `seed_confirmed_session` /
`run_p1_to_confirmation` / `fail_once_on_review_transition` / `output_file` 等装配工具（自包含、全离线）；
Step 09 若跨目录复用，请按 Rev.1 规则在本目录建唯一命名 helper 模块，不要 `from conftest import ...`。

### 通用

- `generation/` 依赖方向是单向的：`generation → prompt_engine / providers.image / persistence`；
  `prompt_engine`、`providers.image` 不反向依赖 `generation`（有导入边界测试）。
- 新测试目录无 `conftest.py`、不 `from conftest import ...`、不跨目录 import。
- 上游问题继续走"最小修订提案 → 架构方裁定"（见「公开接口」第 1～4 条）。

---

## 验收条件逐条核对

| # | 任务书验收条件 | 核对结果 |
|---|---|---|
| 1 | 从确认到真实图片的链路可运行 | **是**。真实 smoke（`uv run pytest -m smoke -q` → 3 passed；`tests/smoke/test_image_smoke.py::test_real_image_generation_produces_exactly_one_png PASSED`，49.30s）从已确认会话经 `PromptEngine.compile` → 真实 `POST /images/generations` → adapter 内下载 → **非空 PNG magic 字节** → 落盘 → GenerationArtifact 落库 → `WAITING_REVIEW`；默认离线 P2 e2e 覆盖同一链路。 |
| 2 | 每次成功调用都有可追踪 GenerationArtifact | **是**。成功后 `append_generation_artifact`（refs 精确 `{"prompt_artifact_id"}`）+ 字节落盘 + `OutputRef`（相对项目根路径 / MIME / 字节数）；Artifact 回答四问（`prompt_artifact_id` / `target_model`+`model_version`+`parameters` / `provider_request_id` / `generation_id`）；`model_validate_json` 往返一致；`seed`/`provider_request_id` 缺失即 `None`，不伪造。 |
| 3 | 失败和重试行为可观察 | **是**。失败 → `FAILED` + 结构化日志（`event="generation.failed"`、`reason_code`、`retryable`、`status_code`、`provider_request_id`）+ **不写 Artifact**（有测试与 caplog 断言）；retry 仅 `FAILED` 可用，复用同一 PromptArtifact、新的 generation_id、历史逐字节不覆盖，失败再回到 `FAILED`（各有测试）；Provider 失败后无原 Artifact 的场景给出显式 `generation.artifact_not_found` 并记入已知限制。 |
| 4 | 生成调用不修改 Intent / Prompt | **是**。`test_generate_never_modifies_the_intent_or_existing_prompt_artifacts` 断言两次 generate 前后当前 IntentRevision 逐字节不变、`intent_revisions` 计数不变、已有 PromptArtifact payload 逐字节不变（新 PromptArtifact 是新 ID，不覆盖）；retry 用例断言 `prompt_artifacts` 计数不变（不重新 compile）。 |
| 5 | 仅支持一个目标图像模型 | **是**。真实 adapter 只有一个 `OpenAIImageProvider`，Fake 只有一个；无路由、无模型注册表、无第二 Renderer；Provider 请求的模型取自已确认 `PromptArtifact.target_model`（即 `ExecutionRevision.target_model`），非唯一模型在 compile 阶段即 `prompt.unsupported_requirement` → `FAILED`（有测试）。 |

## 是否满足验收条件

**是。** 5 条验收条件逐条通过；任务书 7 条「必测场景」全部有显式测试映射（7/7）；本步新增
136 条离线用例 + 1 条真实 smoke，全量 `uv run pytest -q` → **1721 passed, 3 deselected**（零网络、
零真实凭据），范围命令 `tests/generation` → 56 passed、`tests/providers` → 150 passed、
Rev.1 多目录 → 1247 passed；三个 `PYTHONHASHSEED` 下 206 passed；真实 smoke 已实际出图（PNG magic、
Artifact 落库、无 key/URL 泄露、仓库无残留文件）。未修改任何上游或既有文件（唯一修改是本步拥有的
`generation/__init__.py`），未新增依赖，未实现多模型 / 图片理解 / 队列 / Reference Image，
源码/测试/本文件中无明文 API key。已知限制 1（Provider 失败后 retry 无原 Artifact 可复用）是
**上游冻结设计的缺口**，本步按冻结契约实现并在「公开接口」给出三条最小修订方案，等待架构方裁定；
不阻塞本步验收条件的达成。
