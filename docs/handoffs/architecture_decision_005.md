# 架构裁定记录 005（v0.4 Step 01）：知识合同与 JSONL 权威语料

- 日期：2026-09-16
- 裁定方：MVP v0.4 Step 01 实现 Agent
- 上游依据：
  - `docs/task_books/mvp_v0.4/README.md`、`01_knowledge_contract_corpus.md`、
    `00_readiness_review.md`、`AGENT_DISPATCH_PROMPTS.md`；
  - `docs/ARCHITECTURE.md` 第 10 节（对旧 Step 11 的边界说明）与
    `docs/task_books/mvp_v0.2/11_knowledge_engine_gate_b.md`（旧 Step 11 任务书）；
  - 用户方向："加入 RAG 后再评测"。
- 裁定结果：**建立 v0.4 知识合同与语料加载层，旧 Step 11 的
  KnowledgeEngine/Gate B 方案被本版替代**；本版不引入向量库、Embedding API、
  Reranker、新服务或新依赖，不实现检索（Step 02）、不接 PromptEngine/Repository/
  CLI（Step 03/04）。
- 事实核验（裁定当日）：`uv run pytest tests/knowledge -q` → **96 passed**；
  `git diff --check` 无错误。本 ADR 记录的是**合同与边界**，不是图片质量或 RAG
  收益的结论。

---

## 1. 对旧 Step 11 的替代范围

旧 v0.2 Step 11 规划的是 `KnowledgeEngine` + Milvus Lite/Embedding + Gate B。
本版裁定：

| 旧 Step 11 元素 | v0.4 裁定 |
|---|---|
| Milvus Lite / 向量库 | **不采用**。首版在内存建立可重建的关键词索引（Step 02） |
| Embedding API | **不采用**。不新增 Provider 调用、不新增依赖 |
| Reranker / GraphRAG | **不采用** |
| Gate B | **本轮不实施**。RAG 接入完成后按 `05_evaluation_readiness.md` 独立启动 |
| KnowledgeEngine 单体 | 拆成 Step 01 合同/加载与 Step 02 检索/`KnowledgeBundle`，避免一次接入过多接口 |
| Gate A 前置（v0.2 Step 11 要求） | **不作为本轮约束**。依据用户"加入 RAG 后再评测"的最新方向 |

本替代只覆盖 v0.4 的计划层；v0.2/v0.3 已归档任务书一概不覆盖、不改写。

## 2. 存储与依赖边界

- **JSONL 是权威源**：内容在 `knowledge_base/v0.4/*.jsonl`；`manifest.json`
  记录 `schema_version`、`corpus_version`、`tokenizer_version`、
  `retrieval_version`，以及每个 JSONL 文件的 `sha256` 与 `unit_count`。
- **零新依赖**：只使用 Python 标准库（`json`、`hashlib`、`pathlib`、`re`、
  `urllib.parse`）与既有 pydantic。无网络、无数据库、无新服务。
- **无提前持久化**：Step 01 不新增 SQLite 表、不新增 Artifact 种类；检索结果如何
  进入 PromptArtifact/Realization 属 Step 03 的显式版本决策。
- **加载器只读**：`load_corpus()` 不写任何文件、不修改输入、不执行正文。
- **多 Step 拥有边界**：`visual_intent_agent/knowledge/`、`knowledge_base/v0.4/`、
  `tests/knowledge/` 由 Step 01 建立；Step 02 只追加检索/`KnowledgeBundle`
  模块与测试，不覆盖本 ADR 冻结的合同。

## 3. 候选值授权：复用而非复制

- 本版知识库严格限定辅助 `lighting.character`、`composition.framing`、
  `camera.depth_of_field` 三条路径（`knowledge.candidates.KNOWLEDGE_PATHS`）。
- 候选值集合**唯一权威**是 `prompt_engine.engine.DELEGATED_CANDIDATES`。
  `knowledge/candidates.py` 只保留一个**函数级惰性 import**
  （`delegated_candidates()`），在 pydantic validator / loader 调用时才读取该映射。
- **不建立第二份候选表**，因此不存在漂移风险；测试通过 monkeypatch
  `prompt_engine.engine.DELEGATED_CANDIDATES` 后重新校验，证明知识合同读的是同一
  映射。
- **防循环导入（冻结）**：Step 03 会让 `prompt_engine` 依赖 `knowledge`。因此
  `knowledge` 包**禁止在模块顶层 import `prompt_engine`**；此约束由 AST 测试与
  "干净解释器 import knowledge 不加载 prompt_engine"的子进程测试双重守卫。

## 4. `content_hash` 的绑定范围（明确）

- `KnowledgeUnit.content_hash == sha256(content)`（UTF-8，小写十六进制），
  **只绑定 `content` 字符串本身**。
- `applicable_path` / `candidate_value` / `conditions` / `source` /
  `target_models` / 审核字段**不**被 `content_hash` 覆盖；它们的完整性由
  **manifest 的逐文件 sha256** 与 `corpus_version` / `schema_version` /
  `tokenizer_version` / `retrieval_version` 一起绑定。
- 因此：
  1. 文件内单条授权字段被篡改而未改文件 → `knowledge.hash_mismatch`；
  2. 正文被篡改而 `content_hash` 未同步 → `knowledge.invalid_unit`；
  3. 两者同时被改（保持自洽的伪造）只能通过 `corpus_version` 变更与人工审核流程
     显式处理，**程序不假装能检测自洽伪造**。
- **Step 02 的 `KnowledgeBundle` 必须记录**它消费的
  `corpus_version`、`schema_version`、`tokenizer_version`、`retrieval_version`
  与参与命中的单元 `knowledge_id` + `content_hash`（以及 manifest 逐文件 sha256
  或等价语料指纹），使一次生成可回溯到确定的语料版本。

## 5. 条件语义（冻结，Step 02 实现）

- 多 `conditions` 之间是 **AND**：全部满足才命中。
- `conditions` 为空元组时**恒满足**（该单元无附加适用条件）。
- 条件路径在当前已确认 Intent 中**缺失**（无值且无解析记录）→ **不满足**，
  绝不宽松通过；`missing ≠ user_delegated`。
- 条件只读取**已确认的 Intent 字段**（`INTENT_PATHS`），不读取 `content` 正文、
  不读取未确认字段、不读取知识单元自身。
- 比较是**类型精确**的：`subject.count`（int）把十进制字符串按整数比较（拒绝
  前导零/正号/空串），其余路径按字符串逐字比较；无子串匹配、无大小写折叠、
  无 bool/None 强制转换、无正则。条件值是字面量。
- 条件算子只有 `equals`（恰好一个值）与 `in`（显式枚举集合）两种封闭枚举。

## 6. 审核与消费边界

- `review_status ∈ {draft, approved, rejected}`；**只有 `approved` 进入构建消费**
  （`KnowledgeCorpus.build_units()`）。
- `approved` 必须同时带真实 `reviewer` 与 tz-aware UTC 的 `reviewed_at`；
  `draft`/`rejected` **禁止**携带这两个字段。程序拒绝"无审核的 approved"与
  "draft 预填审核字段"，但**不能代替人类审核**，也不证明审核者身份真实。
- 生产语料当前 `corpus_version = v0.4-draft-1`，9 条**全部为 draft**、0 条
  approved；测试夹具中的 approved 只是工程数据。
- 重复 `knowledge_id` 在任何文件中一律拒绝（`knowledge.duplicate_id`）；更新须
  使用新 `version` 或新 ID 并更新 manifest，禁止静默覆盖。

## 7. 不可信数据与安全

- `content` 是不可信数据：只被 JSON 解码为字符串，不执行、不解释、不作为系统
  指令、不拼入 Interpreter/Feedback 消息。恶意正文（"忽略指令/PIN/读取文件"）
  不产生副作用；静态测试禁止 `knowledge/` 出现网络模块与动态执行
  （`eval`/`exec`/`compile`/`__import__`/`os.system`/`subprocess`）。
- 所有不可信字符串有单项长度上界并拒绝 C0 控制字符；`KnowledgeError` 消息统一
  截断到 512 字符，避免无界回显。
- `source` 必须至少有入口（URL 或仓库相对路径）、许可或原创声明、以及
  `source_revision` 或 `source_date` 之一。
- 审核不能取代程序白名单：路径、候选值与来源完整性始终由程序校验。

## 8. 本版不建设（明确留白）

- 不实现关键词检索、排序、回退（Step 02）。
- 不定义 `KnowledgeBundle`（Step 02）。
- 不修改 `SourceBinding`（Step 03）；知识引用只能作为推导来源，不能成为新的
  用户授权类型，不能冒充 `EvidenceRef`。
- 不新增 Repository 表、不新增 CLI 开关（Step 03/04）。
- 不新增配置项、不改 `pyproject.toml`/`uv.lock`、不触网。

## 9. 对后续步骤的输入

- Step 02 从 `visual_intent_agent.knowledge` 导入合同与 `load_corpus`；消费集合
  只取 `build_units()`（approved-only），并在 `KnowledgeBundle` 里记录第 4 节
  要求的语料版本与单元指纹。
- Step 02 必须按第 5 节实现条件判定；不得放宽"缺字段不满足"与类型精确比较。
- Step 03 让 `prompt_engine` 依赖 `knowledge` 时必须保持本包无顶层反向 import。
- 生产语料要进入真实 RAG 效果验证，必须先完成 `knowledge_base/v0.4/README.md`
  的人工审核清单并产生 approved 记录；在此之前 `build_units()` 为空。