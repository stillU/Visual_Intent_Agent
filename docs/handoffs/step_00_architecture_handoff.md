# Step 00 架构交接记录

任务：Step 00 — 架构设计与最小项目骨架（Step 01～09 的唯一架构依据）

## 完成内容

1. 通读全部设计依据（README、01～11 任务书、派发提示词、原始设计书 v0.1、api.md），实施范围冻结为 Step 01～09；Step 10/11 不建设任何能力，仅保证边界不阻碍（ARCHITECTURE.md 第 10 节）。
2. 产出 `docs/ARCHITECTURE.md`：仓库布局、技术栈、配置与密钥策略、Step 01～09 接口冻结表（模块路径 + 公开名 + 关键签名）、跨步骤统一约定（ID/时间戳/schema_version/路径白名单/Issue code/错误与可恢复 issue/不可变模型）、Provider adapter 设计（LLM 与图像各一 interface + 真实 adapter + Fake，超时/错误分类/重试最小策略）、测试策略、派发与验收流程（含 04∥05 写冲突边界表）、全局不变量落地检查清单。
3. 搭建并验证最小骨架：目录树、`pyproject.toml`+`uv.lock`、`.venv`（uv，Python 3.14.6）、`.env`（真实凭据，来自 api.md）、`config.py`（本骨架唯一实现的公共基础设施模块）、`tests/test_sanity.py` + `tests/conftest.py`（smoke 无凭据自动 skip）。
4. 环境实测：pydantic 2.13.5 + pydantic-core 2.46.5 在 Python 3.14.6 正常工作（frozen 模型、SecretStr、extra="forbid" 均验证），无需回退 3.12（如需可用 `uv sync --python 3.12`，3.12.14 本机已备）。
5. LLM 默认模型冻结为 `qwen3.8-max`（理由：非推理模型，无 reasoning tokens 吞噬 max_tokens 导致 content 为空的风险，适合 Interpreter 结构化 JSON 输出；可经 `VIA_LLM_MODEL` 更换）。图像模型冻结 `qwen-image-3.0`。

## 变更文件

- 新建：`docs/ARCHITECTURE.md`、`docs/handoffs/step_00_architecture_handoff.md`
- 新建：`pyproject.toml`、`uv.lock`、`.env`（真实凭据，严禁外泄）、`.gitignore`
- 新建：`visual_intent_agent/__init__.py`、`visual_intent_agent/config.py`，及 11 个业务子包的空 `__init__.py`（domain/validation/policy/persistence/providers/intent_engine/workflow/prompt_engine/realization/generation/feedback）
- 新建：`tests/conftest.py`、`tests/test_sanity.py`，及 tests 下 13 个空子目录、`data/`、`outputs/generations/`
- 未修改任何任务书 md 与原始设计书；未引入 git/Docker/CI。

## 公开接口（架构约定摘要）

- 配置：`visual_intent_agent.config` → `Settings`、`load_settings()`、`has_provider_credentials()`、`ConfigurationError`；环境变量命名表 `VIA_*` 见 ARCHITECTURE.md 第 3 节；凭据来自 api.md / .env。
- Step 01：`domain/` → `VisualIntent`、七 Facet、`Resolution`、`ResolutionRecord`、`IntentDelta`/`DeltaOperation`、`IntentRevision`、`ExecutionRevision`、`Issue`/`EvidenceRef`/`Severity`、`INTENT_PATHS`（路径白名单唯一定义）、`SCHEMA_VERSION="v1"`、`new_id()`/`utc_now()`。
- Step 02：`validation` → `validate(candidate_deltas, evidence_context, current_intent)`、`reduce(current_intent, validated_deltas) -> ReduceResult`、`ValidationResult`/`ChangeSummary`/`EvidenceContext`。
- Step 03：`policy` → `assess(intent, execution_context)`、`DecisionPolicy`（9 条规则表）、`IntentResolution`、`QuestionSpec`。
- Step 04：`persistence` → `Repository` Protocol（任务书 9 方法 + pending question 与 Artifact 信封方法）、`SQLiteRepository`、`WorkflowState`（7 态）、`ConfirmationRecord`、`SessionSnapshot`、`StoredArtifact`。
- Step 05：`providers.llm` → `LLMProvider` Protocol、`OpenAICompatibleLLMProvider`、`FakeLLMProvider`、`ProviderError`；`intent_engine` → `IntentEngine.resolve(IntentResolveRequest) -> IntentResolution`、`Interpreter`、`InterpreterResult`。
- Step 06：`workflow` → `WorkflowService`（create_session/submit_message/get_session/confirm_current_intent）、`QuestionBuilder`、`PendingQuestion`、`ConfirmationSummary`、`compute_summary_hash`。
- Step 07：`prompt_engine` → `PromptEngine.compile(PromptCompileRequest) -> PromptArtifact`、`SourceBinding`、`QwenImageRenderer`（唯一 renderer）；`realization.models` → `RealizationState`/`RealizationValue`。
- Step 08：`providers.image` → `ImageProvider` Protocol、`OpenAIImageProvider`（adapter 内下载图片字节到本地，禁止只存 URL、禁止异步头）、`FakeImageProvider`；`generation` → `GenerationPipeline`（generate/retry）、`GenerationArtifact`/`OutputRef`。
- Step 09：`feedback` → `FeedbackEngine.analyze(FeedbackRequest) -> FeedbackResult`；`realization.carry` → `evaluate_carry()`；`workflow.review` → `ReviewService.submit_feedback()`。

## 测试命令与结果

```text
$ cd /home/change/projects/image_system && uv sync --python 3.14
  + httpx==0.28.1, pydantic==2.13.5, pydantic-core==2.46.5, pytest==9.1.1 等 15 个包（uv.lock 锁定）
$ uv run pytest -v
  platform linux -- Python 3.14.6, pytest-9.1.1
  tests/test_sanity.py::test_pydantic_is_v2 PASSED
  tests/test_sanity.py::test_settings_load_from_env_only PASSED
  tests/test_sanity.py::test_settings_env_file_fallback PASSED
  tests/test_sanity.py::test_settings_missing_credentials_raise PASSED
  tests/test_sanity.py::test_settings_are_frozen PASSED
  5 passed in 0.02s
```

另实测：`.env` 真实凭据经 `load_settings()` 加载成功（key 未打印）；默认 pytest 运行通过 `-m 'not smoke'` 保持完全离线。

## 已知限制

1. 骨架不含任何 Step 01～09 业务代码（按要求）；`config.py` 是唯一实现的公共基础设施。
2. 任务书两处签名按架构需要做了显式冻结并已在文档注明：`validate()` 增加 `current_intent` 参数；`reduce()` 返回 `ReduceResult`（携带 ChangeSummary）；`IntentResolution.applied_delta` 冻结为复数 `applied_deltas`。
3. PendingQuestion（Step 06 持久化形态）与 QuestionSpec（Step 03 策略形态）分离，经 `to_spec()` 衔接，避免 05/06 循环依赖。
4. smoke 测试机制已就位（marker + 无凭据 skip + 默认排除），但 smoke 用例本身属 Step 05/08。
5. 本机为该架构首次落地，尚无 git；`.gitignore` 仅为防误提交保护。

## 对下一步的输入

Step 01 可立即开始，依据 `docs/ARCHITECTURE.md` 第 4 节 Step 01 冻结表：
- 在 `visual_intent_agent/domain/` 实现 constants/identifiers/paths/intent/delta/revision/issue；
- 路径白名单 12 条、`SCHEMA_VERSION="v1"`、frozen+extra="forbid" 约定已定；
- fixture 放 `tests/fixtures/schema_v1/`，单测放 `tests/domain/`；
- 完成后按统一格式写 `docs/handoffs/step_01_handoff.md`。

## 是否满足验收条件

是。目录树、依赖与锁文件、可运行 pytest（5 passed）、.env 凭据、config 模块、ARCHITECTURE.md 九项必需内容、handoff 目录与本记录全部就位；源码与测试中无明文 key；未实现任何业务代码；未触碰只读设计文件。
