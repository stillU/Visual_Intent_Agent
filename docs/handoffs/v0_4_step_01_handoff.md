# v0.4 Step 01 交接：知识合同与首批内容

- 日期：2026-09-16
- 范围：**只完成 Step 01**。Step 02～04（检索/KnowledgeBundle、PromptEngine/
  Repository 接入、CLI）未开始，未接线，未实现。
- 工作区基线：HEAD `b039b9b`（v0.3 后置修复之后）；`docs/task_books/README.md`
  的既有修改与未跟踪的 `docs/task_books/mvp_v0.4/` 原样保留，未回滚、未覆盖、
  未 commit/push。
- 生产知识库状态：**`knowledge_base/v0.4/` 9 条全部为 `draft`，0 条 `approved`，
  尚未人工审核**；未虚构 reviewer/reviewed_at；测试夹具里的 approved 不是生产
  审核证据。

## 任务：Step 01

按 `docs/task_books/mvp_v0.4/01_knowledge_contract_corpus.md` 定义知识合同、审核与
消费合同，建立小型 JSONL 权威语料，并补齐离线验收测试；同时为 Step 02 留出最小
公开模型/加载接口，不提前实现检索、`KnowledgeBundle`、PromptEngine、Repository
或 CLI 接线。

## 完成内容

### 1. 知识合同（`visual_intent_agent/knowledge/models.py`）

- 全部模型 `ConfigDict(frozen=True, extra="forbid")`。
- `KnowledgeUnit` 字段：`knowledge_id`、`version`、`content_hash`、`content`、
  `applicable_path`、`candidate_value`、`keywords`、`aliases`、`conditions`、
  `target_models`、`source`、`review_status`、`reviewer`、`reviewed_at`。
- `KnowledgeSource`：`source_type`（封闭枚举）、`title`、`url`、`repository_path`、
  `locator`、`source_revision`、`source_date`、`license`、
  `original_declaration`；强制至少一个入口（URL 或仓库相对路径）、至少一个
  `license`/`original_declaration`、至少一个 `source_revision`/`source_date`。
- `KnowledgeCondition`：`path`（必须是 `INTENT_PATHS` 中已确认字段）、
  `operator`（封闭枚举 `equals`/`in`）、`values`（字面量元组）；无表达式、无正则、
  无 LLM 判定字段。
- `ReviewStatus`：`draft`/`approved`/`rejected`。
- 关键拒绝：`content_hash != sha256(content)`；`applicable_path` 不在三条白名单；
  `candidate_value` 不属于该路径授权候选；缺来源/来源不完整；`approved` 缺
  `reviewer`/`reviewed_at` 或 `reviewed_at` 非 tz-aware；`draft`/`rejected` 携带
  审核字段（伪造审核字段）。
- 不可信字符串加固：`content` ≤800 字符且拒绝 C0（放行 `\t`/`\n`）；`keyword`/
  `alias`/condition value ≤64 字符；来源元数据 ≤256 字符；URL ≤2048；仓库路径
  ≤256；condition 条数 ≤16、枚举值 ≤16；均拒绝 C0 控制字符。

### 2. 路径与候选授权（`visual_intent_agent/knowledge/candidates.py`）

- `KNOWLEDGE_PATHS` 严格限定 `lighting.character`、`composition.framing`、
  `camera.depth_of_field`（恰好 3 条，import 时断言 ⊆ `INTENT_PATHS`）。
- `delegated_candidates()` 在**函数内**惰性 import
  `prompt_engine.engine.DELEGATED_CANDIDATES`；`candidate_values_for()` /
  `is_authorized_candidate()` 复用同一映射，**不建第二份候选表**。
- `knowledge/` 全包禁止模块顶层 import `prompt_engine`，避免 Step 03 让
  `prompt_engine` 依赖 `knowledge` 时成环。

### 3. JSONL 权威语料加载（`visual_intent_agent/knowledge/loader.py`）

`load_corpus(corpus_dir) -> KnowledgeCorpus`，只读、确定性、离线：

- 读 `manifest.json`；校验 `schema_version` 受支持、`tokenizer_version ==
  keyword.v1`、`retrieval_version == lexical.v1`；
- 逐文件：安全相对路径、存在性、`sha256` 文件哈希、UTF-8、`unit_count`；
- 逐行构造严格 `KnowledgeUnit`；同一语料内重复 `knowledge_id` 一律拒绝；
- 拒绝清单之外的多余 `.jsonl`；
- `KnowledgeCorpus.all_units` 供审计，`build_units()` **只返回 approved**。
- 错误统一 `KnowledgeError`，`.code` 使用 `knowledge.*`；消息截断到 512 字符。

### 4. 首批语料（`knowledge_base/v0.4/`，全 draft）

- `manifest.json` + `lighting.character.jsonl` + `composition.framing.jsonl` +
  `camera.depth_of_field.jsonl`（每路径 3 条，共 9 条）+ `README.md`（审核清单与
  状态说明）。
- 语料版本 `v0.4-draft-1`；9 条全部 `project_original`，带原创声明、许可标记、
  `source_revision=v0.4-draft-1` 与完整仓库相对路径
  `knowledge_base/v0.4/<file>.jsonl`；`target_models=["any"]`；未引用/未声称任何
  模型官方能力（`qwen-image-3.0` 别名与 Qwen-Image 官方仓库版本对应关系未核实）；
  `source_date` 留空以免臆造日期。

### 5. 文档

- `docs/handoffs/architecture_decision_005.md`：旧 Step 11 替代、存储/依赖边界、
  候选复用与防循环、`content_hash` 绑定范围（只绑定 `content`；授权字段由清单
  文件 sha256 与语料版本绑定）、conditions 消费语义冻结、审核与安全边界。
  （编号 005 未被占用。）
- `docs/ARCHITECTURE.md`：新增"当前状态（v0.4 Step 01 增量）"一段与 Rev.4 记录；
  未改动 v0.2 Step 01～09 任何既有冻结条目与历史修订。

## 变更文件

新增：

- `visual_intent_agent/knowledge/__init__.py`
- `visual_intent_agent/knowledge/candidates.py`
- `visual_intent_agent/knowledge/models.py`
- `visual_intent_agent/knowledge/loader.py`
- `knowledge_base/v0.4/manifest.json`
- `knowledge_base/v0.4/lighting.character.jsonl`
- `knowledge_base/v0.4/composition.framing.jsonl`
- `knowledge_base/v0.4/camera.depth_of_field.jsonl`
- `knowledge_base/v0.4/README.md`
- `tests/knowledge/knowledge_helpers.py`
- `tests/knowledge/test_knowledge_contract.py`
- `tests/knowledge/test_knowledge_loader.py`
- `tests/knowledge/test_knowledge_security.py`
- `tests/knowledge/test_knowledge_production_corpus.py`
- `tests/knowledge/test_knowledge_public_api.py`
- `docs/handoffs/architecture_decision_005.md`
- `docs/handoffs/v0_4_step_01_handoff.md`

修改（最小）：

- `docs/ARCHITECTURE.md`（当前状态 + Rev.4；保留全部历史修订）

未触碰：`pyproject.toml`、`uv.lock`、`.env`、`config.py`、`visual_intent_agent/`
其他包（含 `prompt_engine`）、`tests/` 其他目录、既有交接文档与冻结物、
`docs/task_books/README.md` 的未提交修改、未跟踪的
`docs/task_books/mvp_v0.4/`。未 commit/push。

## 公开接口

```python
from visual_intent_agent.knowledge import (
    # 路径与候选（复用 PromptEngine，唯一权威）
    KNOWLEDGE_PATHS, delegated_candidates, candidate_values_for,
    is_authorized_candidate,
    # 加载
    load_corpus, KnowledgeCorpus,
    # 常量
    KNOWLEDGE_SCHEMA_VERSION, SUPPORTED_KNOWLEDGE_SCHEMA_VERSIONS,
    KNOWLEDGE_TOKENIZER_VERSION, KNOWLEDGE_RETRIEVAL_VERSION,
    MAX_CONTENT_CHARS, MAX_TERM_CHARS, MAX_TERMS, MAX_CONDITION_VALUES,
    MAX_CONDITIONS_PER_UNIT, MAX_TARGET_MODEL_CHARS, MAX_METADATA_CHARS,
    MAX_URL_CHARS, MAX_REPOSITORY_PATH_CHARS, MAX_ERROR_MESSAGE_CHARS,
    GENERIC_TARGET_MODEL, MANIFEST_FILENAME, compute_content_hash,
    # 枚举与合同
    ReviewStatus, SourceType, ConditionOperator,
    KnowledgeCondition, KnowledgeSource, KnowledgeUnit,
    KnowledgeCorpusFile, KnowledgeManifest,
    # 错误
    KnowledgeError,
    KNOWLEDGE_MANIFEST_MISSING, KNOWLEDGE_MANIFEST_INVALID,
    KNOWLEDGE_SCHEMA_VERSION_UNSUPPORTED, KNOWLEDGE_VERSION_MISMATCH,
    KNOWLEDGE_UNSAFE_PATH, KNOWLEDGE_FILE_MISSING, KNOWLEDGE_FILE_UNLISTED,
    KNOWLEDGE_HASH_MISMATCH, KNOWLEDGE_INVALID_ENCODING,
    KNOWLEDGE_INVALID_UNIT, KNOWLEDGE_DUPLICATE_ID,
    KNOWLEDGE_UNIT_COUNT_MISMATCH, KNOWLEDGE_EMPTY_CORPUS,
)
```

- `load_corpus(corpus_dir: str | Path) -> KnowledgeCorpus`；
  `KnowledgeCorpus.corpus_version` / `.all_units` / `.all_unit_count` /
  `.build_units() -> tuple[KnowledgeUnit, ...]`（approved-only）。
- Issue/错误 code 命名空间：`knowledge.*`（上列 13 个）。
- 供 Step 02 消费的冻结语义（见 `KnowledgeCondition` docstring 与 ADR-005 第 5 节）：
  多条件 AND；空条件恒满足；路径缺失不满足；只读已确认 Intent 字段；类型精确
  比较（`subject.count` 十进制整数，其余字符串逐字）。
- 无新增配置项，无新增依赖。

## 测试命令与结果

```bash
uv run pytest tests/knowledge -q
# 96 passed

git diff --check
# 无输出（通过）
```

覆盖：Schema 严格性/不可变/extra=forbid、路径与候选白名单、候选表复用（monkeypatch
`DELEGATED_CANDIDATES` 后仍生效）、来源完整性与 revision/date、条件封闭算子、
伪造审核字段、draft 排除、重复 ID、单元与文件两级哈希篡改、清单/版本不匹配、
非法路径/候选、清单外 JSONL、恶意正文仅作为数据且无副作用、`knowledge/` 无网络/
无动态执行、无顶层 `prompt_engine` 依赖（AST + 干净解释器子进程）、生产语料
全 draft 且 0 approved。

未运行：全量测试、真实 Provider、图片生成、评测（按任务限定只跑 Step 01 局部
测试与 `git diff --check`）。

## 已知限制

1. **生产语料未人工审核**：9 条全 draft，`build_units()` 为空；当前 RAG 可用知识
   为 0 条。任何"知识已被审核"的说法都不成立。
2. **来源均为项目原创规则**：本批未引用官方模型能力，因为 `qwen-image-3.0` 别名
   与 Qwen-Image 官方仓库的版本对应关系未核实；`target_models` 一律用 `any`。
3. **`content_hash` 只绑定 `content`**：path/candidate/conditions/source 的完整性
   由 manifest 逐文件 sha256 与 `corpus_version` 绑定；程序不声称能检测"文件与
   manifest 同时被改"的自洽伪造。
4. **无检索能力**：无关键词索引、无排序、无回退、无 `KnowledgeBundle`（Step 02）。
5. **审核真实性无法程序证明**：合同拒绝无审核字段的 approved 与 draft 预填，
   但不能验证 reviewer 身份；仍需流程与人工控制。
6. **条件语义只冻结、未实现**：判定代码属 Step 02；Step 01 只提供数据合同。
7. **`knowledge.unsafe_path` 是纵深防御**：manifest/模型的路径校验更早拒绝逃逸
   路径，该 code 主要防未来调用方绕过模型直接调用加载器。
8. **`knowledge/__init__.py` 是单 Step 拥有**：Step 02 追加检索模块时可再导出新
   名字，但不得改动本步已冻结的合同名或语义。

## 对下一步的输入

- Step 02 从 `visual_intent_agent.knowledge` 导入合同与 `load_corpus`，只消费
  `build_units()`（当前为空，须用测试 approved 夹具驱动离线链路，且不得把夹具当
  生产证据）。
- Step 02 的 `KnowledgeBundle` 必须记录 `corpus_version`、`schema_version`、
  `tokenizer_version`、`retrieval_version` 与命中单元的 `knowledge_id` +
  `content_hash`（或等价语料指纹）。
- Step 02 实现条件判定时必须按 ADR-005 第 5 节语义，不得放宽。
- Step 03 让 `prompt_engine` 依赖 `knowledge` 时，保持本包无顶层反向 import；
  知识只能作为推导来源，不能成为新的用户授权类型，不能冒充 `EvidenceRef`。
- 生产语料进入真实验证前必须先完成 `knowledge_base/v0.4/README.md` 的人工审核
  清单并产生 approved 记录。

## 是否满足验收条件：是

Step 01 范围（合同、来源、审核流程、小型知识库与安全合同）已按任务书交付并通过
局部离线测试；生产内容明确仍为 draft、0 approved，未虚构人工审核。
Step 02～04 未开始。