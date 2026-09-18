# Visual Intent Agent MVP v0.2 — 架构设计（冻结）

版本：architecture/v1（Step 00 架构基线）
依据：`docs/task_books/mvp_v0.2/` 中的 11 份任务书（01～11）与任务索引、
`docs/task_books/mvp_v0.2/AGENT_DISPATCH_PROMPTS.md`、
`../visual_intent_agent_mvp_design_v0.1.md`、api.md（凭据，只引用不复制）。

本文是 Step 01～09 实现 Agent 的唯一架构依据。任务书与本文冲突时，以任务书的
**范围约束**为准、以本文的**命名与路径**为准；发现上游接口问题时按 README 规则
提出最小修订，不得自行扩大架构。

当前状态（2026-09-16，v0.5 语料发布与集中验收）：在不改动 v0.2 Step 01～09 冻结面
的前提下，`visual_intent_agent/knowledge/`（严格冻结的 KnowledgeUnit /
KnowledgeSource / KnowledgeCondition 合同、JSONL 权威语料加载与清单/哈希/版本
校验、复用 `prompt_engine.engine.DELEGATED_CANDIDATES` 的候选授权）与
`tests/knowledge/` 已在 v0.4 Rev.4～Rev.6 落地检索、`KnowledgeBundle`、
PromptEngine/Repository 接入与 CLI（见下文 Rev.5～Rev.7）。v0.5 新增独立发布快照
`knowledge_base/v0.5/`（`corpus_version = v0.5-approved-1`，5 条真实人工审核 `approved`）；
`knowledge_base/v0.4/` 保持不变（9 条 draft、0 approved、`build_units() == ()`），
仍为 CLI 默认知识目录。`knowledge` 包禁止在模块顶层 import `prompt_engine`
（防循环依赖）。本版取代旧 v0.2 Step 11 的 Milvus/Embedding/Gate B 方案，依据见
`docs/handoffs/architecture_decision_005.md`；v0.5 发布与验收见
`docs/handoffs/v0_5_release_handoff.md`。

候选交付整理（2026-09-18，应用版本 v1.0.0rc1）：v1.0 任务书的 Step 01～04 只统一了版本展示、
补充无凭据配置模板、整理文档并产出交接，**未改动**本文冻结的 Step 01～09 接口、数据库 Schema、
领域合同、已批准语料或评测冻结物；本轮未运行测试或验证。范围与未执行项见
`docs/releases/v1.0.0rc1.md` 与 `docs/handoffs/v1_0_handoff.md`。

实施范围：仅 Step 01～09（P0～P3 核心闭环）。Step 10（Gate A）与 Step 11
（KnowledgeEngine）本轮不实施，本文不为其定义任何模块、表或配置；仅在第 10 节
说明现有边界为何不阻碍其后续接入。

---

## 1. 仓库布局

```text
image_system/
├── README.md                      # 项目介绍与架构概览
├── api.md                         # Provider 凭据源（既有，只读；内容只准进入 .env）
├── pyproject.toml                 # 依赖 + pytest 配置（已建）
├── uv.lock                        # uv 锁文件（已建）
├── .env                           # 真实凭据（已建；严禁提交/打包/复制进任何交付物）
├── .gitignore                     # 防误提交保护（已建；本仓库不初始化 git）
│
├── docs/
│   ├── ARCHITECTURE.md            # 本文件
│   ├── task_books/                # 按版本归档的实施任务书
│   │   ├── README.md              # 版本任务书索引与归档约定
│   │   ├── mvp_v0.2/
│   │   │   ├── README.md          # MVP v0.2 任务索引
│   │   │   ├── 01_domain_contracts.md … 11_knowledge_engine_gate_b.md
│   │   │   └── AGENT_DISPATCH_PROMPTS.md
│   │   └── mvp_v0.3/              # Gate A、定向修正与最小 CLI 任务书
│   └── handoffs/                  # 各 Step 交接记录（统一格式）
│       ├── step_00_architecture_handoff.md
│       └── step_01_handoff.md … step_09_handoff.md   # 由各实现 Agent 填写
│
├── visual_intent_agent/           # 唯一业务包（扁平布局，非安装型包）
│   ├── __init__.py                # 仅 docstring + __version__，不导出符号
│   ├── config.py                  # 【已实现】配置加载（本骨架唯一允许实现的基础设施）
│   │
│   ├── domain/                    # Step 01 拥有
│   │   ├── __init__.py            # 可再导出本步冻结表面（单 Step 拥有，允许）
│   │   ├── constants.py           # SCHEMA_VERSION
│   │   ├── identifiers.py         # Id、new_id()、utc_now()
│   │   ├── paths.py               # INTENT_PATHS 路径白名单（全系统唯一定义）
│   │   ├── intent.py              # Resolution、七类 Facet、ResolutionRecord、VisualIntent
│   │   ├── delta.py               # DeltaOperation、IntentDelta
│   │   ├── revision.py            # IntentRevision、ExecutionRevision
│   │   └── issue.py               # Severity、Issue、EvidenceRef
│   │
│   ├── validation/                # Step 02 拥有
│   │   ├── __init__.py            # 再导出 validate/reduce/ValidationResult/ChangeSummary/ReduceResult/EvidenceContext
│   │   ├── models.py              # EvidenceContext、RejectedDelta、ValidationResult、ChangeSummary、ReduceResult
│   │   ├── validator.py           # validate()
│   │   └── reducer.py             # reduce()
│   │
│   ├── policy/                    # Step 03 拥有
│   │   ├── __init__.py            # 再导出 assess/DecisionPolicy/IntentResolution/QuestionSpec 等
│   │   ├── models.py              # Materiality、PolicyAction、DecisionPolicy、UnresolvedDecision、QuestionSpec、IntentResolution
│   │   └── decision_policy.py     # POLICY_VERSION、DECISION_POLICIES 规则表、assess()
│   │
│   ├── persistence/               # Step 04 拥有
│   │   ├── __init__.py            # 再导出 Repository/SQLiteRepository/WorkflowState/ConfirmationRecord 等
│   │   ├── state_machine.py       # WorkflowState、ALLOWED_TRANSITIONS、InvalidStateTransitionError
│   │   ├── records.py             # ConfirmationRecord、SessionSnapshot、StoredArtifact（存储信封）
│   │   ├── repository.py          # Repository(Protocol)、SQLiteRepository、RepositoryError
│   │   └── schema.sql             # SQLite DDL + user_version 最小迁移
│   │
│   ├── providers/                 # Step 05 与 Step 08 共同拥有（__init__.py 必须保持空白）
│   │   ├── __init__.py            # 空白（多 Step 拥有，禁止再导出，避免写冲突）
│   │   ├── errors.py              # 【Step 05 建】ProviderError 错误分类（Step 08 复用）
│   │   ├── llm.py                 # 【Step 05】LLMProvider Protocol、LLMMessage/LLMRequest/LLMResponse
│   │   ├── openai_llm.py          # 【Step 05】OpenAICompatibleLLMProvider
│   │   ├── fake_llm.py            # 【Step 05】FakeLLMProvider
│   │   ├── image.py               # 【Step 08】ImageProvider Protocol、ImageGenerationRequest/Result
│   │   ├── openai_image.py        # 【Step 08】OpenAIImageProvider（/images/generations + 下载字节）
│   │   └── fake_image.py          # 【Step 08】FakeImageProvider
│   │
│   ├── intent_engine/             # Step 05 拥有
│   │   ├── __init__.py            # 再导出 IntentEngine/Interpreter/IntentResolveRequest/InterpreterResult
│   │   ├── models.py              # IntentResolveRequest、InterpreterResult、InterpreterError
│   │   ├── prompts.py             # INTERPRETER_SYSTEM_PROMPT_V1、INTERPRETER_PROMPT_VERSION、输出 JSON Schema
│   │   ├── interpreter.py         # Interpreter（LLM → Candidate Delta 的唯一边界）
│   │   └── engine.py              # IntentEngine.resolve() 编排器
│   │
│   ├── workflow/                  # Step 06 拥有（Step 09 仅追加 review.py，不改既有文件）
│   │   ├── __init__.py            # 再导出 WorkflowService/PendingQuestion/ConfirmationSummary 等
│   │   ├── questions.py           # QuestionBuilder、PendingQuestion（含 to_spec()）
│   │   ├── confirmation.py        # ConfirmationSummary、compute_summary_hash()
│   │   ├── service.py             # WorkflowService（P1 用例：create_session/submit_message/get_session/confirm_current_intent）
│   │   └── review.py              # 【Step 09 追加】ReviewService.submit_feedback()（P3 反馈用例）
│   │
│   ├── prompt_engine/             # Step 07 拥有
│   │   ├── __init__.py            # 再导出 PromptEngine/PromptArtifact/SourceBinding/PromptParameters
│   │   ├── models.py              # PromptArtifact、SourceBinding、PromptParameters、PromptCompilationError
│   │   ├── spec.py                # CompilationSpec（轻量内部结构，不导出，不扩展为 Prompt AST）
│   │   ├── renderer.py            # ModelRenderer(Protocol)、QwenImageRenderer（唯一目标模型）
│   │   └── engine.py              # PromptEngine.compile()
│   │
│   ├── realization/               # Step 07 建 models.py；Step 09 加 carry.py（__init__.py 保持空白）
│   │   ├── __init__.py            # 空白（多 Step 拥有）
│   │   ├── models.py              # 【Step 07】RealizationState、RealizationValue
│   │   └── carry.py               # 【Step 09】evaluate_carry()、CarryEvaluation
│   │
│   ├── generation/                # Step 08 拥有
│   │   ├── __init__.py            # 再导出 GenerationPipeline/GenerationArtifact/OutputRef
│   │   ├── models.py              # GenerationArtifact、OutputRef
│   │   └── pipeline.py            # GenerationPipeline（generate/retry，确认链校验 + 状态迁移 + 落盘）
│   │
│   └── feedback/                  # Step 09 拥有
│       ├── __init__.py            # 再导出 FeedbackEngine/FeedbackResult/FeedbackDecision
│       ├── models.py              # FeedbackDecision、FeedbackRequest、FeedbackResult
│       └── engine.py              # FeedbackEngine.analyze()（只提 Candidate Delta，不改状态）
│
├── tests/
│   ├── conftest.py                # 【已建】smoke 凭据缺失自动 skip
│   ├── test_sanity.py             # 【已建】骨架 sanity（pydantic v2 + config 契约）
│   ├── domain/                    # Step 01 单测
│   ├── validation/                # Step 02 单测（table-driven）
│   ├── policy/                    # Step 03 单测（golden）
│   ├── persistence/               # Step 04 集成测试（临时 SQLite）
│   ├── providers/                 # Step 05/08 adapter 单测（Fake + 合同测试）
│   ├── intent_engine/             # Step 05 集成测试（FakeLLMProvider）
│   ├── workflow/                  # Step 06 单测/集成
│   ├── prompt_engine/             # Step 07 单测/集成
│   ├── generation/                # Step 08 集成测试（FakeImageProvider）+ P2 端到端（test_generation_e2e.py，Rev.2 追认位置）
│   ├── feedback/                  # Step 09 单测 + P3 端到端（Rev.2 规则：端到端随本步目录）
│   ├── e2e/                       # P1(06) 端到端（Step 06 已交付）；P2/P3 按 Rev.2 放本步测试目录，默认全 Fake、全离线
│   ├── smoke/                     # 真实 Provider smoke（@pytest.mark.smoke，默认不运行）
│   └── fixtures/
│       ├── schema_v1/             # Step 01 Schema v1 JSON fixture
│       ├── policy_cases/          # Step 03 golden 决策案例
│       └── multiturn/             # Step 09 多轮修改场景
│
├── data/                          # SQLite 默认目录（运行时生成；不入交付）
└── outputs/
    └── generations/               # 生成图片字节的本地下载目录（运行时生成；不入交付）
```

规则：
- 已归档版本的任务书一律不覆盖；新版本在 `docs/task_books/` 下建立独立目录。
- `visual_intent_agent` 是扁平布局的非安装型包，测试经 pytest `pythonpath=["."]` 导入；
  不引入 build backend、不做 `pip install -e`。
- 跨包导入一律用完整路径（如 `from visual_intent_agent.domain.intent import VisualIntent`）。
  单 Step 拥有的包允许在 `__init__.py` 再导出本文冻结的公开面；
  **多 Step 拥有的包（`providers/`、`realization/`）`__init__.py` 必须保持空白**。
- `data/`、`outputs/` 只存运行时产物；测试一律用 `tmp_path`，不得写这两个目录。

## 2. 技术栈与依赖

| 项 | 冻结值 | 理由 |
|---|---|---|
| Python | 3.14.6（uv 建 .venv，`.venv/bin/python`）；`requires-python = ">=3.12"` | 已实测 pydantic 2.13.5（pydantic-core 2.46.5）在 3.14 正常工作；若未来环境变化，可回退 `uv sync --python 3.12`（3.12.14 本机已备） |
| pydantic | >=2.13（锁 2.13.5） | 全部数据合同：frozen 模型、extra 禁止、序列化、SecretStr |
| httpx | >=0.28（锁 0.28.1） | 唯一 HTTP 客户端。Provider adapter 需要精细超时（connect/read 分离）、状态码与网络错误分类、文件下载；相对 urllib 显著减小 adapter 复杂度，相对 requests 是现代同步 API 且为 OpenAI 生态事实标准。**不引入** openai SDK（协议简单，避免重型依赖与隐藏行为） |
| pytest | >=8.3（锁 9.1.1，dev 依赖） | 唯一测试框架；marker、tmp_path、monkeypatch 足够 |
| SQLite | Python 标准库 `sqlite3` | Step 04 持久化；**不引入 ORM** |
| 配置 | `config.py` 内置 20 行 .env 解析 | **不引入** python-dotenv / pydantic-settings（保持最小依赖） |
| Web 框架 | **不引入** | Step 06 任务书原文为"如仓库已有 FastAPI，可提供最薄路由"；本仓库没有 FastAPI，应用层用例即边界，新增 Web 框架属于任务书外建设 |

环境创建与测试命令（精确）：

```bash
cd /home/change/projects/image_system
uv sync --python 3.14        # 创建 .venv 并按 uv.lock 安装依赖（幂等）
uv run pytest                # 默认测试：全离线，自动排除 smoke
uv run pytest -m smoke       # 真实 Provider smoke（需 .env 凭据；不属默认验收）
uv run pytest tests/test_sanity.py -v   # 骨架自检
```

## 3. 配置与密钥策略

唯一配置入口：`visual_intent_agent/config.py`（已实现）。业务代码不得自行读取
`os.environ` 或 `.env`；测试用伪造环境变量构造 `Settings`，不得读取真实 `.env`。

配置项命名表（唯一权威）：

| 环境变量 | Settings 字段 | 类型 / 默认值 | 用途 |
|---|---|---|---|
| `VIA_PROVIDER_BASE_URL` | `provider_base_url` | str，**必填** | OpenAI 兼容端点 base_url（LLM 与图像共用） |
| `VIA_PROVIDER_API_KEY` | `provider_api_key` | SecretStr，**必填** | Bearer key；任何日志/异常/Artifact/测试不得出现明文 |
| `VIA_LLM_MODEL` | `llm_model` | str，默认 `qwen3.8-max` | Interpreter / FeedbackEngine 用 LLM |
| `VIA_IMAGE_MODEL` | `image_model` | str，默认 `qwen-image-3.0` | 唯一目标图像模型 |
| `VIA_LLM_TIMEOUT_SECONDS` | `llm_timeout_seconds` | float，默认 60.0 | chat/completions 总超时 |
| `VIA_IMAGE_TIMEOUT_SECONDS` | `image_timeout_seconds` | float，默认 120.0 | images/generations + 图片下载总超时 |
| `VIA_HTTP_MAX_RETRIES` | `http_max_retries` | int，默认 2 | 可重试错误的最大重试次数 |
| `VIA_DB_PATH` | `db_path` | Path，默认 `<项目根>/data/visual_intent.db` | SQLite 位置（仅 Step 06 接线用；Repository 构造总是显式传路径，测试传 tmp_path） |
| `VIA_OUTPUT_DIR` | `output_dir` | Path，默认 `<项目根>/outputs/generations` | 生成图片落盘根目录 |

密钥策略（硬约束）：
- 真实值只存在于 `api.md`（只读源）与 `.env`（已写入真实凭据）。
- `.env` 不得进入任何交付物、打包、文档、测试断言；`.gitignore` 已拦截。
- 源码、测试、fixture、handoff 文档中**禁止出现明文 key**；引用方式统一写
  "凭据来自 api.md / .env"。
- `Settings.provider_api_key` 为 `pydantic.SecretStr`，adapter 仅在构造
  Authorization 头时调用 `get_secret_value()`。
- `load_settings(env=..., env_file=...)` 支持注入，优先级：环境变量 > .env > 字段默认值；
  缺必填项抛 `ConfigurationError`（消息不含敏感值）。
- `has_provider_credentials()` 仅供 smoke 测试 skip 判断。

LLM 默认模型选择理由（冻结，可配置更换）：`qwen3.8-max`。
1. 端点已验证可用的非推理模型：`deepseek-v4.1-flash` 是推理模型，reasoning
   tokens 会吞噬 max_tokens、content 可能为空——对 Interpreter 的结构化 JSON
   输出合同是真实 operational 风险，不作默认；
2. Interpreter 需要强指令遵循与受约束 JSON 输出，旗舰通用模型更合适；
3. 与图像模型同属该阿里云 MaaS 工作空间，单一 base_url/key 即可管理；
4. 更换模型只需设 `VIA_LLM_MODEL`（备选 `kimi-k3`），代码零改动。

## 4. Step 01～09 模块划分与接口冻结表

通用约定：本节只冻结**公开面**（模块路径 + 公开名 + 关键签名）；未列出的内部
结构由各 Step 自定。所有合同模型为 pydantic v2，
`model_config = ConfigDict(frozen=True, extra="forbid")`（见第 5 节）。
括号内为该类型的定义位置；import 路径即从该模块导入。

### Step 01 — domain（前置：无）

| 模块 | 公开名 | 冻结签名 / 形状 |
|---|---|---|
| `domain/constants.py` | `SCHEMA_VERSION: Literal["v1"]` | 全系统唯一 schema 版本常量，值为 `"v1"` |
| `domain/identifiers.py` | `Id = str`；`new_id(prefix: str) -> str`；`utc_now() -> datetime` | `new_id` 返回 `f"{prefix}_{uuid4().hex}"`；`utc_now` 返回 tz-aware UTC |
| `domain/paths.py` | `INTENT_PATHS: frozenset[str]`；`SYSTEM_FIELD_NAMES: frozenset[str]` | 路径白名单**唯一定义**，后续步骤只 import 不另维护 |
| `domain/issue.py` | `Severity`；`EvidenceRef`；`Issue` | 见下 |
| `domain/intent.py` | `Resolution`；七个 Facet 模型；`ResolutionRecord`；`VisualIntent` | 见下 |
| `domain/delta.py` | `DeltaOperation`；`IntentDelta` | 见下 |
| `domain/revision.py` | `IntentRevision`；`ExecutionRevision` | 见下 |

冻结细节：

```python
# domain/paths.py —— 恰好 12 条路径，不多不少
INTENT_PATHS = frozenset({
    "subject.description", "subject.count", "subject.pose_action",
    "composition.framing",
    "environment.mode", "environment.location",
    "style.primary", "style.description",
    "lighting.character",
    "camera.angle", "camera.depth_of_field",
    "color.palette",
})
# Validator 系统字段保护用的字段名集合（防御纵深；Delta 本就只能命中白名单）
SYSTEM_FIELD_NAMES = frozenset({
    "schema_version", "intent_id", "session_id", "created_at",
    "intent_revision_id", "execution_revision_id", "parent_revision_id",
    "workflow_state", "confirmation_id", "summary_hash",
})

# domain/issue.py
class Severity(str, Enum): ERROR="error"; WARNING="warning"; INFO="info"
class EvidenceRef(BaseModel):  # frozen
    message_id: str                       # 必填：每条用户输入都是已存消息
    fragment: str | None = None           # 用户原文片段
    pending_question_id: str | None = None  # 回答 Pending Question 时填
class Issue(BaseModel):  # frozen
    code: str            # 命名空间见第 5.5 节
    message: str
    path: str | None = None
    severity: Severity = Severity.ERROR

# domain/intent.py
class Resolution(str, Enum):
    USER_SPECIFIED="user_specified"; USER_CONFIRMED_PROPOSAL="user_confirmed_proposal"
    USER_DELEGATED="user_delegated"; NOT_APPLICABLE="not_applicable"
class ResolutionRecord(BaseModel):  # frozen，Resolution 的路径级绑定
    resolution: Resolution
    evidence_refs: list[EvidenceRef] = []
    reason: str | None = None
# 七个 Facet（全部字段默认 None；除 count 外均为 str|None）
class SubjectFacet(BaseModel):      description: str|None=None; count: int|None=Field(default=None, gt=0); pose_action: str|None=None
class CompositionFacet(BaseModel):  framing: str|None=None
class EnvironmentFacet(BaseModel):  mode: str|None=None; location: str|None=None
class StyleFacet(BaseModel):        primary: str|None=None; description: str|None=None
class LightingFacet(BaseModel):     character: str|None=None
class CameraFacet(BaseModel):       angle: str|None=None; depth_of_field: str|None=None
class ColorFacet(BaseModel):        palette: str|None=None
class VisualIntent(BaseModel):  # frozen + extra="forbid"
    schema_version: Literal["v1"] = "v1"
    intent_id: str | None = None        # 由 Step 06 在首次持久化时赋值；domain 允许为空
    subject: SubjectFacet = SubjectFacet()
    composition: CompositionFacet = CompositionFacet()
    environment: EnvironmentFacet = EnvironmentFacet()
    style: StyleFacet = StyleFacet()
    lighting: LightingFacet = LightingFacet()
    camera: CameraFacet = CameraFacet()
    color: ColorFacet = ColorFacet()
    resolutions: dict[str, ResolutionRecord] = {}   # key ⊆ INTENT_PATHS（model_validator 校验）
    pinned_paths: frozenset[str] = frozenset()      # ⊆ INTENT_PATHS（model_validator 校验）
# 语义不变量：missing = 该路径无值且 resolutions 中无记录；missing ≠ user_delegated。

# domain/delta.py
class DeltaOperation(str, Enum): SET="SET"; CLEAR="CLEAR"; PIN="PIN"; UNPIN="UNPIN"
class IntentDelta(BaseModel):  # frozen；LLM 输出的解析目标，故模型层不校验白名单（属 Step 02）
    operation: DeltaOperation
    path: str
    value: str | int | None = None        # count 路径用 int，其余 str
    resolution: Resolution | None = None
    evidence_refs: list[EvidenceRef] = []
# model_validator（Step 01 实现）：
#   SET 必须至少携带 value 或 resolution 之一（resolution-only SET = 授权变更，如 user_delegated）；
#   CLEAR / PIN / UNPIN 必须两者都不携带。

# domain/revision.py
class IntentRevision(BaseModel):  # frozen
    schema_version: Literal["v1"] = "v1"
    intent_revision_id: str
    session_id: str
    parent_revision_id: str | None
    intent: VisualIntent
    applied_deltas: list[IntentDelta] = []
    created_at: datetime = Field(default_factory=utc_now)   # tz-aware，Step 01 加 validator 保证
class ExecutionRevision(BaseModel):  # frozen；即任务书中的 ExecutionContext 最小形态
    schema_version: Literal["v1"] = "v1"
    execution_revision_id: str
    session_id: str
    parent_revision_id: str | None
    target_model: str                      # 默认取 Settings.image_model
    output_size: str = "1024x1024"         # 形如 "<w>x<h>"，比例由此派生，不另设 aspect_ratio 字段
    created_at: datetime = Field(default_factory=utc_now)
```

交付物映射：模型+fixture（`tests/fixtures/schema_v1/*.json`）+ 单测（`tests/domain/`）+
公开合同说明写入 `docs/handoffs/step_01_handoff.md`。

### Step 02 — validation（前置：01）

| 模块 | 公开名 | 冻结签名 / 形状 |
|---|---|---|
| `validation/models.py` | `EvidenceContext` | `available_message_ids: frozenset[str]`；`pending_question_id: str\|None=None`；`pending_question_path: str\|None=None` |
| 同上 | `RejectedDelta` | `delta: IntentDelta`；`issues: list[Issue]` |
| 同上 | `ValidationResult` | `accepted: list[IntentDelta]`；`rejected: list[RejectedDelta]`；`issues: list[Issue]` |
| 同上 | `ChangeSummary` | `changed_paths: list[str]`；`pinned_paths: list[str]`；`unpinned_paths: list[str]`；`cleared_paths: list[str]`；`confirmation_invalidated: bool` |
| 同上 | `ReduceResult` | `intent: VisualIntent`；`change_summary: ChangeSummary` |
| `validation/validator.py` | `validate(candidate_deltas: list[IntentDelta], evidence_context: EvidenceContext, current_intent: VisualIntent) -> ValidationResult` | 任务书写作 `validate(candidate_deltas, evidence_context)`；冻结增加 `current_intent` 参数，因为任务书检查项 4/7（证据存在性、CLEAR pinned 拒绝、PIN/UNPIN 目标合法）必须读取当前状态 |
| `validation/reducer.py` | `reduce(current_intent: VisualIntent, validated_deltas: list[IntentDelta]) -> ReduceResult` | 任务书写作返回 `new_intent`；冻结为 `ReduceResult`，因为任务书同时要求 Reducer 输出 change summary |

公开 import：`from visual_intent_agent.validation import validate, reduce, ValidationResult, ChangeSummary, ReduceResult, EvidenceContext`。

Validator 证据范围规则（冻结其一即可，Step 02 细化但不得更松）：
- 每个 Delta 必须带非空 `evidence_refs`；`message_id` 必须 ∈ `available_message_ids`；
- 带 `pending_question_id` 的证据必须等于当前 `pending_question_id`，且该 Delta 的
  `path` 必须等于 `pending_question_path`（回答问题的授权不扩大）；
- 非法 Delta 整个拒绝进 `rejected`，禁止改写路径或静默忽略。

Reducer 冻结语义：纯函数、不改输入、未命中路径逐值保持；CLEAR 同时移除
`resolutions[path]`；`confirmation_invalidated` 在重要视觉字段 SET/CLEAR 或
PIN/UNPIN 后为 True（保守规则）。Reducer 不创建 IntentRevision（持久化职责在
Step 06 service，组装时填 `parent_revision_id`）。

### Step 03 — policy（前置：01、02）

| 模块 | 公开名 | 冻结签名 / 形状 |
|---|---|---|
| `policy/models.py` | `Materiality` | 枚举：`core`/`perceptual`/`implementation` |
| 同上 | `PolicyAction` | 枚举：`block`/`omit`/`runtime` |
| 同上 | `DecisionPolicy` | `path: str`；`materiality: Materiality`；`required_if: str`；`delegatable: bool`；`dependencies: list[str]`；`conflict_rules: list[str]`；`invalidates_realization: bool` |
| 同上 | `UnresolvedDecision` | `path: str`；`materiality: Materiality`；`action: PolicyAction`；`reason: str` |
| 同上 | `QuestionSpec` | `target_path: str`；`reason: str`；`allow_delegate: bool`；`allow_custom: bool=True`；`suggested_values: tuple[str, ...]=()`；`question_text: str\|None=None`；`question_id: str\|None=None`（后两个字段由 Step 06 填充；Step 03 产生时为空） |
| 同上 | `IntentResolution` | `intent: VisualIntent`；`applied_deltas: list[IntentDelta]`（任务书 `applied_delta` 冻结为复数）；`issues: list[Issue]`；`unresolved_decisions: list[UnresolvedDecision]`；`conflicts: list[Issue]`（冲突以 Issue 表达，code 用 `policy.*`）；`question: QuestionSpec\|None`；`ready_for_confirmation: bool` |
| `policy/decision_policy.py` | `POLICY_VERSION: str = "policy.v1"`；`DECISION_POLICIES: tuple[DecisionPolicy, ...]` | 恰好 9 条（Primary Subject、Style、Environment、Framing、Pose/Action、Lighting、Camera Angle、Color、Depth of Field），数据驱动，写在代码常量中（不引入 YAML） |
| 同上 | `assess(intent: VisualIntent, execution_context: ExecutionRevision \| None = None) -> IntentResolution` | 纯确定性；`applied_deltas` 恒为空列表，由 IntentEngine 用 `model_copy(update={"applied_deltas": ...})` 回填 |

`required_if` 的取值必须是 Step 03 定义的**封闭枚举字符串集**（如 `"always"`、
`"never"`、若干命名条件），逐值写入 `step_03_handoff.md`；不得开放为自由表达式。
判定优先级冻结：`policy.hard_conflict` > core missing > perceptual missing >
`policy.execution_conflict`；每轮最多选一个 `question`。

### Step 04 — persistence（前置：01、02、03；可与 05 并行）

| 模块 | 公开名 | 冻结签名 / 形状 |
|---|---|---|
| `persistence/state_machine.py` | `WorkflowState` | str 枚举：`UNDERSTANDING`、`WAITING_CLARIFICATION`、`WAITING_CONFIRMATION`、`GENERATING`、`WAITING_REVIEW`、`FAILED`、`COMPLETED`（无 RETRIEVING/REFINING） |
| 同上 | `ALLOWED_TRANSITIONS: frozenset[tuple[WorkflowState, WorkflowState]]` | 恰好 11 条：任务书"至少覆盖"的 10 条 + Rev.1 增补 `WAITING_CONFIRMATION → UNDERSTANDING`（依据见文末「修订记录」Rev.1） |
| 同上 | `InvalidStateTransitionError(Exception)` | 带 `.code = "persistence.invalid_state_transition"` |
| `persistence/records.py` | `ConfirmationRecord` | `schema_version`；`confirmation_id`；`session_id`；`intent_revision_id`；`execution_revision_id`；`summary_hash: str`；`confirmed_at: datetime` |
| 同上 | `SessionSnapshot` | `session_id`；`workflow_state: WorkflowState`；`current_intent_revision_id: str\|None`；`current_execution_revision_id: str\|None`；`latest_confirmation_id: str\|None`；`pending_question_id: str\|None`；`pending_question_payload: str\|None`；`message_ids: tuple[str, ...]` |
| 同上 | `StoredArtifact` | 存储信封：`artifact_id`；`session_id`；`refs: dict[str, str]`（外键）；`payload: str`（业务模型 JSON）；`created_at` |
| `persistence/repository.py` | `Repository(Protocol)`；`SQLiteRepository(db_path: str \| Path)`；`RepositoryError(Exception)`（带 `.code`，`persistence.*` 命名空间） | 方法表见下 |
| `persistence/schema.sql` | — | 9 张表；`PRAGMA user_version` 做最小迁移；`PRAGMA foreign_keys=ON` |

Repository 方法冻结（任务书 9 个 + pending question 与 Artifact 信封 + Rev.1 追认的
2 个只读 getter + Rev.2 追认的 1 个只读 getter，总数 24）：

```python
class Repository(Protocol):
    # 会话与消息
    def create_session(self, session_id: str) -> None: ...          # 初始状态 UNDERSTANDING
    def append_message(self, session_id: str, message_id: str, role: str, content: str, created_at: datetime) -> None: ...
    # revision：只增不改（无 UPDATE）
    def append_intent_revision(self, revision: IntentRevision) -> None: ...
    def append_execution_revision(self, revision: ExecutionRevision) -> None: ...
    # 确认
    def save_confirmation(self, record: ConfirmationRecord) -> None: ...
    def is_confirmation_valid(self, confirmation_id: str) -> bool: ...   # 当前两类 revision 与 hash 均匹配才有效
    # 读取
    def get_current_session_snapshot(self, session_id: str) -> SessionSnapshot: ...
    def get_intent_revision(self, intent_revision_id: str) -> IntentRevision: ...
    def get_execution_revision(self, execution_revision_id: str) -> ExecutionRevision: ...   # Rev.1 追认：Step 07 compile 需读 target_model/output_size
    def get_confirmation(self, confirmation_id: str) -> ConfirmationRecord: ...              # Rev.1 追认：任务书 04 必测场景「旧确认记录仍可审计」
    # 状态机（非法迁移抛 InvalidStateTransitionError，不落库）
    def transition_state(self, session_id: str, to_state: WorkflowState) -> None: ...
    # pending question（payload 的序列化/反序列化归 Step 06）
    def save_pending_question(self, session_id: str, question_id: str, payload: str) -> None: ...
    def clear_pending_question(self, session_id: str) -> None: ...
    # Artifact 信封（refs 必填键冻结如下；payload 归各 Step 模型）
    def append_prompt_artifact(self, prompt_artifact_id: str, session_id: str, refs: dict[str, str], payload: str) -> None: ...
    def get_prompt_artifact(self, prompt_artifact_id: str) -> StoredArtifact: ...
    def get_latest_prompt_artifact(self, session_id: str) -> StoredArtifact | None: ...   # Rev.2 追认：Step 08 retry 回退用；按 created_at, rowid 取本会话最新一条，无则 None；schema 不变
    def append_generation_artifact(self, generation_id: str, session_id: str, refs: dict[str, str], payload: str) -> None: ...
    def get_generation_artifact(self, generation_id: str) -> StoredArtifact: ...
    def list_generation_artifacts(self, session_id: str) -> list[StoredArtifact]: ...
    def append_feedback_result(self, feedback_id: str, session_id: str, refs: dict[str, str], payload: str) -> None: ...
    def append_realization_state(self, realization_id: str, session_id: str, refs: dict[str, str], payload: str) -> None: ...
    def get_current_realization_state(self, session_id: str) -> StoredArtifact | None: ...
```

`refs` 必填键：`prompt_artifacts`→`{"intent_revision_id","confirmation_id"}`；
`generation_artifacts`→`{"prompt_artifact_id"}`；`feedback_results`→`{"generation_id"}`；
`realization_states`→`{"based_on_intent_revision_id"}`。外键不存在的写入必须失败
（SQLite FK 约束）。所有写操作在事务内；revision/artifact 表无 UPDATE 路径。

### Step 05 — providers(llm) + intent_engine（前置：01、02、03；可与 04 并行）

| 模块 | 公开名 | 冻结签名 / 形状 |
|---|---|---|
| `providers/errors.py` | `ProviderError(Exception)` | 字段：`code: str`（`provider.*` 命名空间）、`retryable: bool`、`status_code: int\|None`、`provider_request_id: str\|None`；工厂方法：`ProviderError.auth()/rate_limited()/timeout()/network()/server()/invalid_request()/unparseable_response()` |
| `providers/llm.py` | `LLMMessage`、`LLMRequest`、`LLMUsage`、`LLMResponse`；`class LLMProvider(Protocol): def complete(self, request: LLMRequest) -> LLMResponse` | `LLMRequest{messages: list[LLMMessage], model: str\|None=None（None→Settings.llm_model）, temperature: float\|None, max_tokens: int\|None, response_format: dict\|None}`；`LLMResponse{content: str, model: str, provider_request_id: str\|None, usage: LLMUsage\|None}` |
| `providers/openai_llm.py` | `OpenAICompatibleLLMProvider(settings: Settings, client: httpx.Client \| None = None)` | POST `{base}/chat/completions`；错误分类与重试见第 6 节 |
| `providers/fake_llm.py` | `FakeLLMProvider(responses: Iterable[LLMResponse \| str] \| Callable[[LLMRequest], LLMResponse \| str])` | 确定性；记录 `.requests: list[LLMRequest]`；队列耗尽抛 `ProviderError.invalid_request` |
| `intent_engine/models.py` | `IntentResolveRequest` | `current_intent: VisualIntent`；`message_id: str`；`message_text: str`；`pending_question: QuestionSpec\|None=None`；`available_message_ids: frozenset[str]` |
| 同上 | `InterpreterResult` | `candidate_deltas: list[IntentDelta]`；`detected_conflicts: list[Issue]`；`unresolved_language: list[str]`；`evidence_refs: list[EvidenceRef]` |
| 同上 | `InterpreterError(Exception)` | 带 `.code`（`interpreter.*`）与 `.retryable: bool`；仅用于程序级失败（如上下文构造失败） |
| `intent_engine/prompts.py` | `INTERPRETER_PROMPT_VERSION = "interpreter.v1"`；`INTERPRETER_SYSTEM_PROMPT_V1: str`；`INTERPRETER_OUTPUT_SCHEMA: dict` | prompt 与 schema 的版本记录处 |
| `intent_engine/interpreter.py` | `class Interpreter: def __init__(self, llm: LLMProvider) -> None`；`def interpret(self, request: IntentResolveRequest) -> InterpreterResult` | LLM 输出无法解析时**不抛异常**，由 IntentEngine 转为可恢复 issue |
| `intent_engine/engine.py` | `class IntentEngine: def __init__(self, interpreter: Interpreter) -> None`；`def resolve(self, request: IntentResolveRequest) -> IntentResolution` | 即任务书 `resolve(current_intent, message, pending_question)`；请求对象承载同名三要素 |

IntentEngine 编排顺序（冻结）：Interpreter → validate（EvidenceContext 由
request 构造：`pending_question_id/path` 取自 `request.pending_question`）→
被拒 Delta 的 issues 保留 → reduce（仅 accepted）→ assess → 用
`model_copy(update={"applied_deltas": accepted})` 回填后返回。
LLM 解析失败 / ProviderError → 返回 `IntentResolution`（`issues` 含
`interpreter.unparseable_output` 或对应 `provider.*`，`ready_for_confirmation=False`，
intent 原样），**不抛异常、不猜测修复**。

### Step 06 — workflow（前置：04、05）

| 模块 | 公开名 | 冻结签名 / 形状 |
|---|---|---|
| `workflow/questions.py` | `PendingQuestion` | `question_id`；`session_id`；`target_path`；`reason`；`allow_delegate: bool`；`allow_custom: bool`；`suggested_values: tuple[str,...]`；`question_text: str`；`created_at`；方法 `to_spec() -> QuestionSpec`（连同已渲染文本） |
| 同上 | `class QuestionBuilder: def __init__(self, llm: LLMProvider \| None = None)`；`def build(self, spec: QuestionSpec, session_id: str) -> PendingQuestion` | 无 LLM 时用确定性模板（默认）；LLM 仅可改写措辞，`target_path/allow_delegate/suggested_values` 必须原样来自 QuestionSpec |
| `workflow/confirmation.py` | `ConfirmationSummary` | `intent: VisualIntent`；`change_summary: ChangeSummary\|None`（diff-first 的本轮修改）；`delegated_paths: list[str]`；`pinned_paths: list[str]`；`target_model: str`；`output_size: str` |
| 同上 | `compute_summary_hash(summary: ConfirmationSummary) -> str` | 冻结算法：`hashlib.sha256(json.dumps(summary.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()` |
| 同上 | `class WorkflowError(Exception)` | 带 `.code`（`workflow.*`）：`workflow.stale_revision`、`workflow.summary_hash_mismatch`、`workflow.invalid_state` |
| `workflow/service.py` | `class WorkflowService: def __init__(self, repo: Repository, intent_engine: IntentEngine, question_builder: QuestionBuilder, settings: Settings) -> None` | 用例如下 |

用例冻结（任务书四个名字）：

```python
def create_session(self) -> SessionSnapshot
    # new_id("ses")；初始 ExecutionRevision（target_model=settings.image_model, output_size="1024x1024"）；状态 UNDERSTANDING
def submit_message(self, session_id: str, text: str) -> SubmitMessageOutcome
    # 存 message → transition UNDERSTANDING → IntentEngine.resolve（pending_question 从快照 payload 反序列化并 to_spec()）
    # → 有新 delta 则组装 IntentRevision（parent=当前）落库 →【Rev.3 增补】evaluate_carry：
    #   ChangeSummary 由既有 derive_change_summary(applied_deltas) 派生；无 RealizationState
    #   或无失效时零写入；有失效则 build_carry_state 落新 RealizationState（refs 精确
    #   {"based_on_intent_revision_id"}）；返回形状不变（carry 不进 SubmitMessageOutcome）
    # → 有 question：存 PendingQuestion + WAITING_CLARIFICATION；
    #   ready：WAITING_CONFIRMATION → 返回 SubmitMessageOutcome{snapshot, resolution, pending_question|None}
def get_session(self, session_id: str) -> SessionSnapshot
def confirm_current_intent(self, session_id: str, intent_revision_id: str, execution_revision_id: str, summary_hash: str) -> ConfirmationRecord
    # 仅在 WAITING_CONFIRMATION；revision 必须等于当前快照；重算 hash 比对；
    # 失败抛 WorkflowError；成功 save_confirmation（本步不生成 Prompt/图片）
```

`SubmitMessageOutcome` 形状由 Step 06 冻结并写入 handoff，至少含
`snapshot / resolution / pending_question|None`。

### Step 07 — prompt_engine + realization/models（前置：06）

| 模块 | 公开名 | 冻结签名 / 形状 |
|---|---|---|
| `realization/models.py` | `RealizationValue` | `path: str`；`value: str`；`source: Literal["user_delegated"]`；`first_prompt_artifact_id: str`；`carry_policy: Literal["preserve_until_invalidated"]="preserve_until_invalidated"`；`status: Literal["active","invalidated"]="active"`；`invalidated_reason: str\|None=None`；`invalidated_at: datetime\|None=None` |
| 同上 | `RealizationState` | `schema_version`；`realization_id`；`session_id`；`based_on_intent_revision_id`；`values: list[RealizationValue]`；`created_at`（失效产生**新** state，历史不覆盖） |
| `prompt_engine/models.py` | `SourceBinding` | `clause_id: str`；`text: str`；`source_kind: Literal["intent","delegation","realization","runtime"]`；`intent_path: str\|None`；`rule_id: str\|None`；`realization_id: str\|None` |
| 同上 | `PromptParameters` | `size: str = "1024x1024"`（对应 /images/generations 的 `"<w>x<h>"`；为本模型唯一参数面） |
| 同上 | `PromptArtifact` | `schema_version`；`prompt_artifact_id`；`session_id`；`based_on_intent_revision_id`；`based_on_confirmation_id`；`target_model`；`prompt: str`；`parameters: PromptParameters`；`source_bindings: list[SourceBinding]`；`realization_refs: list[str]`；`created_at` |
| 同上 | `PromptCompilationError(Exception)` | 带 `.code`（`prompt.*`）：`prompt.no_valid_confirmation`、`prompt.unauthorized_addition`、`prompt.missing_source_binding`、`prompt.unsupported_requirement` |
| `prompt_engine/renderer.py` | `class ModelRenderer(Protocol)`；`class QwenImageRenderer(ModelRenderer)`；`RENDERER_VERSION = "qwen_image.v1"` | 唯一目标模型 renderer；不得出现第二个 |
| `prompt_engine/engine.py` | `class PromptEngine: def __init__(self, renderer: ModelRenderer, repo: Repository) -> None`；`def compile(self, request: PromptCompileRequest) -> PromptArtifact` | 见下 |

```python
class PromptCompileRequest(BaseModel):  # prompt_engine/models.py
    session_id: str
    confirmation_id: str
```

compile 冻结流程：`repo.is_confirmation_valid` 复核（无效抛
`prompt.no_valid_confirmation`）→ 读当前 Intent/Execution revision 与
`get_current_realization_state` → 逐 clause 生成 SourceBinding → delegated 路径
局部实现并优先复用 active Realization → unauthorized addition 检查（任何重要
clause 无合法 binding 即抛 `prompt.unauthorized_addition`）→ 新 Realization 落库
（`append_realization_state`）→ `append_prompt_artifact` → 返回。
unspecified + omit 的字段必须保持不具体化。

### Step 08 — providers(image) + generation（前置：07）

| 模块 | 公开名 | 冻结签名 / 形状 |
|---|---|---|
| `providers/image.py` | `ImageGenerationRequest` | `prompt: str`；`size: str`（必须 `"<w>x<h>"`）；`model: str\|None=None`（None→Settings.image_model） |
| 同上 | `GeneratedImage` | `content: bytes`；`mime_type: str` |
| 同上 | `ImageGenerationResult` | `model: str`；`provider_request_id: str\|None`；`images: list[GeneratedImage]`；`seed: int\|None=None`（Provider 不返回则 None，禁止伪造） |
| 同上 | `class ImageProvider(Protocol): def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult` | 字节级结果：URL→下载在 adapter 内完成 |
| `providers/openai_image.py` | `OpenAIImageProvider(settings: Settings, client: httpx.Client \| None = None)` | POST `{base}/images/generations`；**禁止** `X-DashScope-Async` 头（该 key 403）；错误分类与重试同第 6 节 |
| `providers/fake_image.py` | `FakeImageProvider(...)` | 返回确定性小 PNG 字节常量；记录 `.requests`；接口与真实 adapter 完全一致 |
| `generation/models.py` | `OutputRef` | `path: str`（相对项目根，形如 `outputs/generations/<generation_id>/image_1.png`，用 `PROJECT_ROOT / path` 还原；`output_dir` 在项目根外时允许 `../..` 形态——Rev.2 追认）；`mime_type: str`；`byte_size: int`（不存临时 URL） |
| 同上 | `GenerationArtifact` | `schema_version`；`generation_id`；`session_id`；`prompt_artifact_id`；`target_model`（请求的目标模型）；`model_version: str\|None`（Provider 响应报告的模型标识；Provider 未报告时 adapter 回填请求/配置模型名，**不编造版本号**——Rev.2 追认）；`parameters: PromptParameters`（复用 Step 07 类型）；`seed: int\|None`；`output_refs: list[OutputRef]`；`provider_request_id: str\|None`；`created_at` |
| `generation/pipeline.py` | `class GenerationPipeline: def __init__(self, repo: Repository, prompt_engine: PromptEngine, image_provider: ImageProvider, output_dir: Path) -> None` | 用例如下 |

```python
def generate(self, session_id: str) -> GenerationArtifact
    # 快照必须 WAITING_CONFIRMATION 且存在有效确认 → transition GENERATING
    # → PromptEngine.compile(PromptCompileRequest)（其内部复核确认）
    # → ImageProvider.generate → 字节写入 {output_dir}/{generation_id}/image_{i}.png
    # → append_generation_artifact（refs={"prompt_artifact_id": ...}）→ transition WAITING_REVIEW
    # GENERATING 阶段内任何失败（ProviderError / PromptCompilationError / 落盘 OSError /
    # Artifact 落库失败 / 复核迁移失败）→ transition FAILED + 结构化日志，异常向上抛；
    # 失败不写任何成功 Artifact（不伪造 Artifact）——Rev.2 追认失败转换范围；
    # 门禁失败（状态不对 / 无有效确认）在 transition 之前拒绝，状态保持不变
def retry(self, session_id: str, generation_id: str) -> GenerationArtifact
    # 仅 FAILED 状态；加载原 GenerationArtifact 的 prompt_artifact_id 重新调用 Provider
    # （同一 PromptArtifact，不得重新编译）；新 generation_id，历史不覆盖
    # Rev.2 回退规则：原 GenerationArtifact 不存在时（纯 Provider 失败不写 Artifact），
    # 回退到 get_latest_prompt_artifact(session_id)——即该次失败 generate 刚编译落库的
    # 原 PromptArtifact；会话无任何 PromptArtifact 仍抛 generation.artifact_not_found；
    # 无论原 Artifact 路径还是回退路径，该 PromptArtifact 的 based_on_confirmation_id
    # 都必须通过 is_confirmation_valid（即仍绑定当前有效确认），否则
    # generation.no_valid_confirmation；绝不重新 compile
```

### Step 09 — feedback + realization/carry + workflow/review（前置：08）

| 模块 | 公开名 | 冻结签名 / 形状 |
|---|---|---|
| `feedback/models.py` | `FeedbackDecision` | 枚举：`accept`/`revise`/`clarify` |
| 同上 | `FeedbackRequest` | `session_id`；`message_id`；`feedback_text: str`；`generation: GenerationArtifact`；`prompt_artifact: PromptArtifact`；`current_intent: VisualIntent`；`realization_state: RealizationState\|None` |
| 同上 | `FeedbackResult` | `schema_version`；`feedback_id`；`session_id`；`generation_id`；`intent_revision_id`；`decision: FeedbackDecision`；`candidate_deltas: list[IntentDelta]`；`preserve_paths: list[str]`；`compile_feedback: str\|None`；`issues: list[Issue]`；`evidence_refs: list[EvidenceRef]`；`created_at` |
| `feedback/engine.py` | `class FeedbackEngine: def __init__(self, llm: LLMProvider) -> None`；`def analyze(self, request: FeedbackRequest) -> FeedbackResult` | 只提 Candidate Delta 与 preserve 范围，不改状态；模糊反馈 → `clarify`，不得猜具体值 |
| `realization/carry.py` | `CarryEvaluation` | `carried_values: list[RealizationValue]`；`invalidated_values: list[RealizationValue]`（status 已置 invalidated + reason） |
| 同上 | `evaluate_carry(state: RealizationState \| None, change_summary: ChangeSummary, policies: tuple[DecisionPolicy, ...] = DECISION_POLICIES) -> CarryEvaluation` | 失效条件冻结：用户 SET/CLEAR 同路径、其 dependency 路径变化、PIN/UNPIN 影响；默认继承 active。「新 Intent 与旧 Realization 冲突」只以 ChangeSummary 可判定形态（同路径 SET/CLEAR，含纯 resolution 的 SET）落地，语义级冲突检测不做（Rev.3 追认）。冻结调用点两处（Rev.3）：`ReviewService` revise 分支与 `WorkflowService.submit_message` 落新 revision 后 |
| `workflow/review.py`（新增文件，不改 Step 06 既有文件） | `class ReviewService: def submit_feedback(self, session_id: str, generation_id: str, text: str) -> FeedbackOutcome` | 仅 `WAITING_REVIEW` 可用；accept→`COMPLETED`；revise→走 validate→reduce→assess→重新确认（`WAITING_CONFIRMATION`）→ 再生成；clarify→存 PendingQuestion→`WAITING_CLARIFICATION`；`FeedbackOutcome` 形状由 Step 09 冻结并写入 handoff |

P3 闭环（冻结调用链）：`ReviewService.submit_feedback` → FeedbackEngine →
validate/reduce/assess → evaluate_carry（新 RealizationState 落库，旧值显式
invalidated 记录）→ 重新确认（每次重要修改必须过）→ PromptEngine.compile
（复用 carried Realization）→ GenerationPipeline → 新 GenerationArtifact。

Rev.3 增补与追认（依据 `docs/handoffs/architecture_decision_003.md`）：
- clarify 的回答（以及一切经 `WorkflowService.submit_message` 的修改，含确认页
  修改）在落新 IntentRevision 后同样执行 evaluate_carry / build_carry_state
  失效链路（调用点见 Step 06 用例冻结）；Step 09 冻结表中「`workflow/review.py`
  不改 Step 06 既有文件」是 Step 09 的范围纪律，Rev.3 工单 D 对 `service.py` 的
  carry 增补是经裁定的独立补丁，不溯及该表述。
- `review.py` 不 import `prompt_engine`/`generation`、以
  `FeedbackRequest.model_validate(payload)` 校验合同对象的兼容设计追认为冻结形态；
  `tests/workflow/test_workflow_public_api.py` 的依赖边界扫描维持覆盖
  `workflow/*.py` 全包，不收窄、不豁免。

## 5. 跨步骤统一约定

### 5.1 ID
- 类型：`str`（`domain.identifiers.Id` 别名）。
- 生成：`new_id(prefix)` → `"<prefix>_<uuid4hex>"`，只能在确定性代码中调用，LLM 输出中的 ID 一律忽略并由系统重填。
- 前缀冻结：`ses`(session)、`msg`(message)、`irev`(intent_revision)、`erev`(execution_revision)、`cnf`(confirmation)、`qst`(question)、`pra`(prompt_artifact)、`gen`(generation)、`fbk`(feedback)、`rlz`(realization)。

### 5.2 时间戳
- 一律 `domain.identifiers.utc_now()`：tz-aware UTC（`datetime.now(timezone.utc)`）。
- 序列化：ISO 8601 带 `+00:00`；合同模型的 `created_at` 必须有 tz-aware validator（Step 01）。

### 5.3 schema_version
- 唯一定义：`domain/constants.py` 的 `SCHEMA_VERSION = "v1"`（`Literal["v1"]`）。
- 每个可持久化合同（VisualIntent、IntentRevision、ExecutionRevision、ConfirmationRecord、PromptArtifact、GenerationArtifact、RealizationState、FeedbackResult）都有 `schema_version` 字段且默认 `"v1"`；不支持版本的反序列化必须显式拒绝，禁止静默丢字段。

### 5.4 路径白名单
- 唯一定义：`domain/paths.py::INTENT_PATHS`（12 条）与 `SYSTEM_FIELD_NAMES`。
- 任何步骤（Validator、DecisionPolicy、PromptEngine、FeedbackEngine、测试）只允许 import，不得维护第二份清单。

### 5.5 Issue code 命名空间
- 格式：`<area>.<name>`，全小写 snake_case（如 `prompt.unauthorized_addition`；任务书中的裸写术语视为其等价物）。
- 保留 area：`validation`（02）、`policy`（03）、`persistence`（04）、`provider`（05/08 共享）、`interpreter`（05）、`workflow`（06/09）、`prompt`（07）、`generation`（08）、`feedback`（09）、`config`（骨架）。
- 每个 Step 新增的 code 必须列入该步 handoff 的"公开接口"一节。

### 5.6 错误处理与"可恢复 issue"
- **可恢复业务失败**（LLM 解析失败、Provider 调用失败、用户输入不足以形成 Delta）→
  不抛异常，在结果对象的 `issues` 中放 `severity=error` 且 code ∈
  {`interpreter.*`, `provider.*`, `feedback.*`} 的 Issue，由 Workflow 决定重试或澄清。
- **硬门禁 / 程序级失败** → 抛带 `.code` 的类型化异常：`PromptCompilationError`、
  `WorkflowError`、`InvalidStateTransitionError`、`RepositoryError`、`ConfigurationError`、
  `ProviderError`（adapter 边界内抛出，由调用方转换为 issue 或状态 FAILED）。
- ProviderError 永远不得泄露原始 SDK/HTTP 异常文本中的敏感头；日志不记 key。

### 5.7 不可变模型
- 所有合同模型 `model_config = ConfigDict(frozen=True, extra="forbid")`。
- "修改"一律构造新对象（Reducer 内部可用 `model_copy(update=...)`，但输入对象不得原地改变）。
- 核心合同禁止 `dict[str, Any]`；唯一允许的 dict 字段：`StoredArtifact.refs`（存储信封外键）。

## 6. Provider Adapter 设计

### 6.1 拓扑（冻结：恰好两个 interface、两个真实 adapter、两个 Fake）

```text
LLMProvider (Protocol)            ImageProvider (Protocol)
  └ OpenAICompatibleLLMProvider     └ OpenAIImageProvider      ← 唯一真实实现
  └ FakeLLMProvider                 └ FakeImageProvider        ← 默认测试唯一依赖
```

二者共用同一 OpenAI 兼容端点（base_url/key 来自 `Settings`），但 interface、
adapter、配置项各自独立；禁止互相 import 实现细节（可共享 `providers/errors.py`）。

### 6.2 已探测 Provider 事实（直接采信，Step 05/08 不得重复探测图像生成）

- `POST {base}/chat/completions` 200 可用；`deepseek-v4.1-flash` 是推理模型
  （reasoning 消耗 max_tokens，content 可能为空），默认不用于 Interpreter。
- `POST {base}/images/generations` 200 可用：`model="qwen-image-3.0"`，
  `size` 必须是 `"<width>x<height>"`；响应 `{created, data:[{url}], usage}`，
  url 为带 Expires 的 OSS 签名临时地址 → **adapter 必须在响应内下载字节**，
  系统任何位置不得只存 URL。
- 该 key **不支持** `X-DashScope-Async`（403）：adapter 禁止设置异步头。
- DashScope 原生同步端点为备选事实，MVP 不实现。

### 6.3 超时、错误分类、重试（两个真实 adapter 完全一致的最小策略）

- 超时：`httpx.Client(timeout=...)`，LLM 用 `llm_timeout_seconds`，图像用
  `image_timeout_seconds`（含下载）。
- 分类（HTTP/网络异常 → `ProviderError`，绝不外泄 httpx 异常类型）：
  401/403→`provider.auth`（不可重试）；429→`provider.rate_limited`（可重试）；
  5xx→`provider.server_error`（可重试）；超时→`provider.timeout`（可重试）；
  连接/DNS→`provider.network`（可重试）；4xx 其他→`provider.invalid_request`
  （不可重试）；响应 JSON 结构不符→`provider.unparseable_response`（不可重试）。
- 重试：仅 `retryable=True` 的类别；最多 `settings.http_max_retries` 次（默认 2）；
  指数退避 `0.5s × 2^n`；不得重试不可重试类别；重试用尽后抛最后一次错误。

### 6.4 Fake Provider 一致性要求

- 与真实 adapter 实现同一 Protocol，构造函数不读 Settings、不触网；
  记录全部入参（`.requests`）供断言；输出对相同输入逐字节确定。
- Fake 可用于合同测试：同一组 interface 级测试对 Fake 与真实 adapter 都能跑
  （真实实例仅在 `tests/smoke/` 内构造）。
- 位置冻结：`providers/fake_llm.py`（05）、`providers/fake_image.py`（08）。

### 6.5 Smoke test 规则

- 一律放 `tests/smoke/`，打 `@pytest.mark.smoke`；
- 默认运行被 pyproject `addopts = "-m 'not smoke'"` 排除（默认测试完全离线）；
- `tests/conftest.py` 在无凭据时自动 skip（凭据判断只用 `has_provider_credentials()`）；
- smoke 测试不是任何 Step 的验收条件；每个 smoke 必须可在 `uv run pytest -m smoke` 单独运行；
- smoke 中断言与日志禁止出现明文 key。

## 7. 测试策略

- 布局：`tests/<package>/` 与业务包一一镜像（见第 1 节目录树）；
  `tests/e2e/` 放 Step 06 已交付的 P1 端到端；自 Step 07 起，端到端用例随本步放在
  对应 `tests/<package>/`（先例：Step 07 编译端到端在 `tests/prompt_engine/`；
  Rev.2 追认：Step 08 的 P2 端到端在 `tests/generation/test_generation_e2e.py`，
  Step 09 的 P3 端到端放 `tests/feedback/`），与本目录唯一命名 helper 模块自包含、
  全 Fake、全离线；`tests/smoke/` 见 6.5。
- fixture：`tests/fixtures/schema_v1/`（01）、`tests/fixtures/policy_cases/`（03
  golden）、`tests/fixtures/multiturn/`（09）。fixture 为版本化 JSON，加载后必须
  round-trip 稳定；fixture 中禁止真实凭据与真实临时 URL。
- table-driven：Step 02 四种 operation 用参数化表；Step 03 用 golden JSON 案例
  驱动断言 `IntentResolution` 全字段。
- 临时 SQLite：一律 `tmp_path`（如 `SQLiteRepository(tmp_path/"t.db")`）；
  Repository 构造不接受 Settings（settings 的 db_path 仅供 Step 06 运行接线）。
- 端到端默认离线：P1 用 FakeLLMProvider；P2 再加 FakeImageProvider 与
  `tmp_path` 输出目录；P3 用多轮 fixture。
- 每个 Step 的默认测试必须 `uv run pytest` 全绿且零网络访问。
- 测试共享 helper 命名规则（Rev.1 冻结）：跨用例共享的工厂/装配工具必须放入
  **唯一命名模块**（约定 `<area>_helpers.py`，如 `validation_helpers.py`、
  `policy_helpers.py`；既有 `persistence_helpers.py`、`ie_helpers.py` 已合规）；
  `conftest.py` 只放 pytest fixture/钩子；测试文件**禁止** `from conftest import ...`
  （pytest 多目录参数下 `sys.modules["conftest"]` 按 basename 缓存会互相覆盖，
  导致收集期 ImportError）。
- 验收命令约定（Rev.1 冻结）：默认全量 `uv run pytest -q` 必须全绿；任务书/handoff
  给出的多目录范围命令（如
  `uv run pytest tests/persistence tests/domain tests/validation tests/policy tests/test_sanity.py -q`）
  必须可按字面执行并通过；不允许以"只用全量递归"替代范围验证。

## 8. 派发与验收流程

顺序冻结：`01 → 02 → 03 → (04 ∥ 05) → 06 → 07 → 08 → 09`。

- 每步完成后由实现 Agent 写 `docs/handoffs/step_XX_handoff.md`，格式按
  `docs/task_books/mvp_v0.2/README.md`
  "统一交接格式"（任务/完成内容/变更文件/公开接口/测试命令与结果/已知限制/
  对下一步的输入/是否满足验收条件），"公开接口"一节必须列出本步新增的
  Issue code 与任何对本文的最小修订提议。
- 验收未通过不派发后续步骤；上游接口问题只提最小修订，不自行扩大架构。

**04 ∥ 05 并行写冲突边界（冻结）：**

| Step 04 允许触碰 | Step 05 允许触碰 |
|---|---|
| `visual_intent_agent/persistence/**` | `visual_intent_agent/providers/errors.py`、`llm.py`、`openai_llm.py`、`fake_llm.py`、`__init__.py`（建为空白） |
| `tests/persistence/**`、`tests/fixtures/persistence/**`（如需） | `visual_intent_agent/intent_engine/**` |
| `docs/handoffs/step_04_handoff.md` | `tests/providers/**`、`tests/intent_engine/**`、`tests/smoke/test_llm_smoke.py` |
| — | `docs/handoffs/step_05_handoff.md` |

双方**禁止**触碰：`pyproject.toml`、`uv.lock`、`.env`、`config.py`、
`domain/`、`validation/`、`policy/`、对方的目录、`providers/__init__.py`
以外的对方文件（Step 05 建 `providers/__init__.py` 为空白后，Step 04 不得修改；
Step 08 后续也不得修改它）。需要新依赖或改配置时写入各自 handoff 的"已知限制"，
由架构方裁定，不并行修改。

## 9. 全局不变量落地检查清单

| # | README 不变量 | 落地模块与机制 |
|---|---|---|
| 1 | LLM 负责理解和表达，确定性代码负责状态 | LLM 仅存在于 `Interpreter`/`FeedbackEngine`/`QuestionBuilder`(可选措辞)；状态迁移只在 `persistence/state_machine.py`；`ready_for_confirmation` 只在 `policy.decision_policy.assess()` |
| 2 | LLM 只能提出 Candidate Delta | `IntentDelta` 是 LLM 输出的唯一状态载体；`IntentEngine`/`ReviewService` 强制过 `validation.validate`；Interpreter 返回完整 Intent 视为解析失败 |
| 3 | 未说明的重要视觉内容不被静默决定 | `missing ≠ user_delegated`（domain 语义）；`policy` 的 block 规则；`prompt_engine` 的 unauthorized addition 检查（`prompt.unauthorized_addition`） |
| 4 | 未被 Delta 指定的字段保持不变 | `validation.reducer.reduce` 纯函数 + "非目标字段保持"属性测试覆盖七 Facet |
| 5 | 重要修改后旧 Confirmation 失效 | `ChangeSummary.confirmation_invalidated`；`Repository.is_confirmation_valid` 绑定双 revision + summary_hash；Step 06 确认用例重算 hash |
| 6 | 正式 Prompt 和生成必过 Hard Confirmation Gate | `PromptEngine.compile` 复核 `is_confirmation_valid`（`prompt.no_valid_confirmation`）；`GenerationPipeline.generate` 要求 `WAITING_CONFIRMATION` + 有效确认；确认只认当前 revision（`workflow.stale_revision`） |
| 7 | Intent / Realization / Prompt / Generation / Feedback 分层 | 分包即分层：`domain` / `realization` / `prompt_engine` / `generation` / `feedback`；跨层只经冻结接口与 revision 引用，禁止互存对方对象 |
| 8 | 历史 revision 与 Artifact 不可覆盖 | SQLite 表只 INSERT（无 UPDATE 路径）；`parent_revision_id` 链；FK 约束；artifact 全部带独立 ID 与 `created_at` |
| 9 | 第一阶段不引入 Milvus / 多 Agent / 微服务 / Event Sourcing / Prompt AST | 依赖白名单仅 pydantic+httpx+pytest；SQLite 快照式存储；`CompilationSpec` 为内部轻量结构不导出；无服务拆分 |
| 10 | 只完成任务书范围 | 本文目录树不含 Step 10/11 任何模块；`required_if` 封闭枚举；依赖锁定在 uv.lock |

## 10. 对 Step 10/11 的边界说明（不建设，仅保证不阻碍）

- Step 10 需要的"同一 Provider 配置"已由 `Settings` 统一承载；Direct LLM
  baseline 可直接复用 `providers/llm.py` 与 `providers/image.py`，无需改动。
- Step 11 若启动：KnowledgeBundle 可作为新 artifact 种类复用 Repository 信封
  模式（新增表，不改旧表）；`PromptCompileRequest` 可增加可选字段（frozen 模型
  允许向后兼容的可选扩展，但那是 Step 11 的显式版本决策，现在不预留字段）。
- 当前代码中不存在任何为评测/RAG 预留的接口、配置或空壳模块。

---

附：骨架已实现并验证（详见 `docs/handoffs/step_00_architecture_handoff.md`）：
目录树、`pyproject.toml`+`uv.lock`、`.venv`（Python 3.14.6，pydantic 2.13.5 /
httpx 0.28.1 / pytest 9.1.1）、`.env`（真实凭据）、`config.py`、
`tests/test_sanity.py` + `tests/conftest.py`，`uv run pytest` 5 passed。

---

## 修订记录

### Rev.1 — 2026-09-14（对应 `docs/handoffs/architecture_decision_001.md`）

针对 Step 04/05 实现 Agent 按流程提出的三项最小修订提案，架构方裁定如下
（提案原文摘要与完整论证见 `docs/handoffs/architecture_decision_001.md`）：

| # | 提案 | 裁定 | 影响的冻结条目 |
|---|---|---|---|
| 1 | 状态迁移表缺口 `WAITING_CONFIRMATION → UNDERSTANDING` | 采纳 (a)：增补为第 11 条合法迁移 | 第 4 节 Step 04 `ALLOWED_TRANSITIONS`（10 → 11 条） |
| 2 | `tests/validation` 与 `tests/policy` 的 conftest 同名冲突 | 采纳 (a)：授权最小修复（共享 helper 迁入唯一命名模块） | 第 7 节测试策略（新增 helper 命名规则与验收命令约定各一条） |
| 3 | Step 04 已实现的 `get_execution_revision` / `get_confirmation` | 采纳 (a)：追认并写入冻结表 | 第 4 节 Step 04 Repository 方法表（读取区 +2 个只读 getter） |

Rev.1 不新增模块、不新增状态、不新增依赖；第 11 条迁移与 conftest 修复的代码
落地分别由「Step 04 补丁任务」与「conftest 微修复任务」执行（范围与验收命令见
decision 文档），其余冻结内容不变。

### Rev.2 — 2026-09-14（对应 `docs/handoffs/architecture_decision_002.md`）

针对 Step 08 实现 Agent 按流程提出的 4 条最小修订建议与 1 条行为追认请求，架构方
裁定如下（完整论证见 `docs/handoffs/architecture_decision_002.md`）：

| # | 提案 | 裁定 | 影响的冻结条目 |
|---|---|---|---|
| 1 | retry 与"失败不写 Artifact"的冻结设计缺口 | 采纳 (a)：Repository 增加只读 `get_latest_prompt_artifact(session_id) -> StoredArtifact \| None`；retry 在无原 GenerationArtifact 时回退到本会话最新 PromptArtifact，仍须其确认有效，绝不重新 compile | 第 4 节 Step 04 Repository 方法表（+1 只读 getter，总数 24）；第 4 节 Step 08 retry 语义 |
| 2 | `model_version` 语义 | 追认：`target_model` = 请求的目标模型；`model_version` = Provider 报告的模型标识，未报告时回填配置模型名，不编造版本号 | 第 4 节 Step 08 `GenerationArtifact` 行 |
| 3 | `OutputRef.path` 相对基准 | 追认：相对项目根，`PROJECT_ROOT / path` 还原；项目根外 `output_dir` 允许 `../..` 形态 | 第 4 节 Step 08 `OutputRef` 行 |
| 4 | P2 端到端测试位置 | 追认现位置 `tests/generation/test_generation_e2e.py`（与 Step 07 先例一致）；规则改为"端到端随本步测试目录" | 第 1 节目录树、第 7 节测试策略 |
| 5 | FAILED 转换范围 | 追认为冻结行为：GENERATING 阶段内任何失败（ProviderError / PromptCompilationError / 落盘 OSError / Artifact 落库失败 / 复核迁移失败）均转 FAILED；未新增迁移 | 第 4 节 Step 08 `generate` 注释 |

Rev.2 不新增模块、不新增状态、不新增依赖、不改 schema（9 张表与既有索引不动）；
代码落地由「工单 C：Step 08 retry 回退微修复」执行（范围与验收命令见 decision
文档），其余冻结内容不变。

### Rev.3 — 2026-09-14（对应 `docs/handoffs/architecture_decision_003.md`）

针对 Step 09 实现 Agent 按流程提出的 3 条最小修订建议（`step_09_handoff.md`「对
ARCHITECTURE.md 的最小修订建议」1～3）与 2 条已知限制确认请求（同文「已知限制」
7/8），架构方裁定如下（完整论证见 decision 文档）：

| # | 提案 | 裁定 | 影响的冻结条目 |
|---|---|---|---|
| 1 | clarify 回答经 `submit_message` 不触发 `evaluate_carry`（dependency 变化不失效） | 采纳 (i)：`WorkflowService.submit_message` 在落新 IntentRevision 后执行 evaluate_carry；无 RealizationState/无失效零写入，有失效落新 RealizationState（refs 照旧 `{"based_on_intent_revision_id"}`）；`SubmitMessageOutcome` 形状不变，Step 06 公开面零变化 | 第 4 节 Step 06 用例冻结（submit_message 增补 carry 步）；第 4 节 Step 09 evaluate_carry 行（调用点两处） |
| 2 | 收窄 workflow 依赖边界测试范围（限定 Step 06 三文件或豁免 review.py） | 不采纳收窄：追认 `review.py` 的 payload→`FeedbackRequest.model_validate` 兼容设计为冻结形态，边界测试维持全包扫描 | 第 4 节 Step 09 P3 闭环段（追认记录） |
| 3 | `ChangeSummary`→`evaluate_carry` 表达力边界（语义级冲突检测不做） | 追认为 MVP 冻结口径；未来如需 new_intent 快照属 Step 02/09 联合修订，按最小修订流程处理 | 第 4 节 Step 09 evaluate_carry 行 |

已知限制确认（零动作）：限制 7（反馈解析失败不做「最多一次」重试）与限制 8
（`WAITING_REVIEW` 无重做入口）均维持现状，结论见 decision 文档。

Rev.3 不新增模块、不新增状态、不新增依赖、不改 schema、不改任何用例签名与返回
形状；代码落地由「工单 D：submit_message 接入 evaluate_carry」执行（范围与验收
命令见 decision 文档），其余冻结内容不变。

### Rev.4 — 2026-09-16（对应 `docs/handoffs/architecture_decision_005.md`）

v0.4 Step 01 以 `architecture_decision_005.md` 冻结知识合同与语料层：新增
`visual_intent_agent/knowledge/`、`knowledge_base/v0.4/`、`tests/knowledge/`；
JSONL 为权威源，`manifest.json` 绑定逐文件 sha256 与 Schema/分词/检索版本；
`applicable_path` 严格限定 `lighting.character` / `composition.framing` /
`camera.depth_of_field`，`candidate_value` 复用
`prompt_engine.engine.DELEGATED_CANDIDATES`（仅函数级惰性 import，禁止顶层反向
依赖，不建第二份候选表）；`content_hash` 只绑定 `content`，其余授权字段完整性由
清单文件 sha256 与语料版本绑定（Step 02 的 `KnowledgeBundle` 必须记录这些指纹）；
conditions 冻结 AND / 空条件恒满足 / 缺字段不满足 / 仅读已确认 Intent /
类型精确比较语义；构建消费只允许 `approved`。本 Rev 不新增依赖，不在 Step 01
实施检索、`KnowledgeBundle`、PromptEngine、Repository 或 CLI 接线，也不修改
v0.2 Step 01～09 的任何既有冻结条目。

### Rev.5 — 2026-09-16（v0.4 Step 02～04 兼容扩展）

v0.4 在 Rev.4 合同之上完成本地、可关闭、可追溯的知识辅助闭环：

- `knowledge/retrieval.py` 与 `knowledge/bundle.py` 实现 `keyword.v1` / `lexical.v1`、
  approved-only 过滤、条件判定、稳定 Top-3、歧义回退及编译级 `KnowledgeBundle`；
  Bundle ID 使用新增兼容前缀 `kbu_`，可复现性由查询、排序结果与语料指纹保证，
  不以随机 ID 或时间作为等价判断依据。
- `RealizationValue` 兼容追加 `knowledge_bundle_id` / `knowledge_unit_id` /
  `knowledge_unit_version`（三项全有或全空）；`PromptArtifact` 兼容追加
  `knowledge_bundle_refs`（默认空）。知识仅解释具体实现值的选择原因，不新增
  `source_kind=knowledge`，不构成用户授权。
- SQLite schema 升为 `user_version=2`，新增第 10 张 append-only 表
  `knowledge_bundles`；Repository 追加 `append_knowledge_bundle`、
  `get_knowledge_bundle`、`list_knowledge_bundles`。v1→v2 只补表，不改写旧 payload、
  refs 或历史 Artifact。
- `PromptEngine(renderer, repo, *, knowledge_engine=None)` 默认关闭知识检索；仅对本版
  三条已确认委托、无值、未 PIN、无 active Realization 的路径检索，并在消费端复核
  Bundle 身份、候选与授权边界。持久化顺序为 Bundle→Realization→Prompt；retry 复用
  原 Prompt，不重新检索。
- CLI 以 `--rag` 显式开启、`--knowledge-dir` 指定本地目录；默认关闭，拒绝远程 URL。
  `--demo --rag` 的 approved 单元明确是非生产测试夹具。生产语料仍为 9 条 draft、
  0 approved，因此当前真实语料启用时只会透明回退，不能据此声称图片质量改善。

本 Rev 不新增外部依赖、网络知识源、Embedding、向量库或额外 LLM 调用；正式评测仍须
满足 `docs/task_books/mvp_v0.4/05_evaluation_readiness.md` 的独立启动条件。

### Rev.6 — 2026-09-16（对应 `docs/handoffs/architecture_decision_006.md`）

v0.4 后置修复 F2 在 Rev.5 之上补齐消费端纵深校验，全部为兼容追加，不改 schema、
不迁移数据、不重写历史：

- `knowledge/bundle.py` 新增 `KnowledgeEligibilitySnapshot`（`conditions` /
  `target_models` / `review_status` / `reviewer` / `reviewed_at`，版本标识
  `eligibility.v1` 落在嵌套快照内）；`KnowledgeUnitHit` 追加可选
  `eligibility_snapshot`（默认 `None` = 旧记录未提供条件证据，**不得**当作无限适用）。
  快照只由 `LocalKnowledgeEngine` 从已校验的 `KnowledgeUnit` 原样复制。
- `KnowledgeBundle` 兼容追加默认空的 `adoption_decisions`
  （`KnowledgeAdoptionDecision`：`adopted` / `rejected` + 封闭原因码），由
  `with_adoption_decisions` 返回**新**对象后落库，原检索器对象不被原地修改；
  旧 payload 缺字段仍可读。检索器推荐与编译器裁定由此可区分、可查询。
- `PromptEngine` 消费端新增 `evaluate_recommendation`：复核快照版本、审核、模型适用
  与 conditions（复用 `evaluate_conditions` / `target_model_applies`），对实际内容
  重算 `content_hash`；任何失败回退固定候选表并记录拒绝原因，身份不符、越界或无法
  重新通过合同校验的伪 Bundle 仍硬失败。
- CLI 只读展示持久化裁定：被拒推荐绝不显示成 adopted，旧 Bundle 明确标注"未记录
  裁定"。
- 不新增依赖、表或配置，不放宽 `extra="forbid"`、Intent、Policy、确认门禁与候选
  白名单；旧 active Realization 复用与原 Prompt retry 行为不变。Rev.6 时生产语料仍为
  9 条 draft、0 approved，本 Rev 不构成知识审核或 RAG 收益结论。

### Rev.7 — 2026-09-16（v0.5 语料发布与集中验收，无运行时变更）

v0.5 不新增或修改运行时模块、领域合同、Schema、表或默认配置；只新增独立发布语料、
离线测试与文档：

- 新增 `knowledge_base/v0.5/`（`corpus_version = v0.5-approved-1`）：3 个 JSONL
  （camera 2 / framing 1 / lighting 2）共 5 条**真实人工审核** `approved`
  （审核人 `change`，`2026-09-16T10:25:59Z`），由获批 v2 提案机械录入（只改
  `review_status` / `reviewer` / `reviewed_at`），文件哈希/条数与 `content_hash` 可核验。
- `knowledge_base/v0.4/` 字节未改（`manifest.json` sha256 仍 `ec013105…`），
  仍为 9 draft / 0 approved、`build_units() == ()`；`resolve_knowledge_dir(None)` 与
  `--rag` 默认行为不变，使用新语料须显式 `--knowledge-dir knowledge_base/v0.5`。
- 发布守卫 `tests/knowledge/test_knowledge_v0_5_release.py` 自动断言"发布相对获批提案
  仅审核三字段可变"与 v0.4 manifest 字节冻结；离线采用验证见
  `tests/generation/test_generation_v0_5_release_adoption.py`（含 P1 真实工作流闭环）。
- 本 Rev 不构成知识质量、RAG 收益或图片质量结论；正式评测未执行，属 v0.5 边界，
  见 `docs/handoffs/v0_5_release_handoff.md`。
