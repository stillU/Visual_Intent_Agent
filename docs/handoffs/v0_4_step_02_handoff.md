# v0.4 Step 02 交接：本地确定性检索与 KnowledgeBundle

- 日期：2026-09-16
- 范围：**只完成 Step 02**。Step 03（PromptEngine/Repository 接入与持久化）、
  Step 04（CLI/开关/工程验收）未由本步实施、未接线、未接开关。
- 工作区基线：HEAD `b039b9b`（v0.3 后置修复之后）。全部既有修改保持原样，未回滚、
  未覆盖、未 commit/push。
- 并行边界：本交接只对 `visual_intent_agent/knowledge/` 与 `tests/knowledge/` 负责。
  工作区同时存在**其他 Agent 对 `persistence/`、`prompt_engine/models.py`、
  `realization/models.py` 及对应测试的并发修改**（Step 03 归属其所有者）；本步未
  触碰这些文件，也不在此评述其内容。
- 生产知识库状态：`knowledge_base/v0.4/` 仍为 **9 条全 `draft`、0 条 `approved`**；
  `build_units()` 为空，真实 RAG 当前可用知识为 0 条。测试夹具中的 `approved`
  只是工程数据，**不是**生产审核证据。

## 任务：Step 02

按 `docs/task_books/mvp_v0.4/02_retrieval_bundle.md` 与
`docs/handoffs/architecture_decision_005.md` 实现：确定性本地检索（查询构建、过滤、
词法评分、排序、回退）、编译级 `KnowledgeBundle`、供 Step 03 注入的最小
`KnowledgeEngine` 检索接口，以及离线验收测试。

## 完成内容

### 1. 规范化与分词（`retrieval.py`，`keyword.v1`，规则/版本冻结）

- `normalize_text(text)`：Unicode **NFKC**（全角/兼容字符/连字折叠为规范形）。
- `tokenize(text)`：
  - 英文/数字：小写字母数字**词**（`top3` 同词；`SOFT`≡`soft`），去重保序；
  - 中文：连续中文字符长度 ≥2 只产出**二元组**（`柔光`→`柔光`；`柔和的光`→
    `柔和/和的/的光`），单字输入保留单字（`光`→`光`）；单字不作为独立 token 嵌在
    长串里（bigram 规则的明确边界）；
  - 混合脚本边界：`soft柔光`→`soft,柔光`；`柔光soft`→`柔光,soft`；
  - `close-up` 与 `close_up` 等价（`-`/`_` 都是分隔符）→ `close,up`；
  - 标点与非 ASCII 非中文字符一律为分隔符。
  - 不下载分词模型、不触网、不新增依赖；token 序列对同一输入固定（去重保序）。
- 中文范围：CJK Ext A / Unified Ideographs / Compatibility Ideographs。
- `unit_retrieval_tokens(unit)`：任务书要求的**正文 + `keywords` + `aliases`** 三者
  同规范化后并集去重。
- `score_tokens(q, u) = |q∩u| / |q|`；空查询得 0（`lexical.v1` 冻结公式）。
- 模块 import 时断言 `KNOWLEDGE_TOKENIZER_VERSION == "keyword.v1"` 且
  `KNOWLEDGE_RETRIEVAL_VERSION == "lexical.v1"`，版本升级必须显式改代码，
  不会静默沿用旧语义。

### 2. 条件语义（ADR-005 第 5 节，冻结，不放宽）

`evaluate_conditions(conditions, intent)`：

- 多条件 **AND**；空元组**恒满足**；
- 条件路径在已确认 Intent 中缺失 → **不满足**（`missing ≠ user_delegated`）；
- 只读已确认 Intent 字段（`INTENT_PATHS`），不读正文、不读未确认字段；
- `subject.count`：条件是**严格十进制字符串**（`^(0|[1-9][0-9]*)$`）转 int 比较；
  前导零/正号/小数/空串/枚举中混入非法值 → **整条不满足**，不抛宽泛异常；
- 其余路径：`equals`/`in` 对字符串**逐字精确**比较，无大小写折叠、无子串、无正则、
  无强制转换；算子只有封闭枚举 `equals`/`in`。

### 3. QueryBuilder（字段白名单与门禁）

`QueryBuilder.build(intent, target_model, pending_paths)`：

- 查询文本**只**取已确认 Intent 中**有值**的 `INTENT_PATHS` 字段（按路径字典序），
  不含未确认聊天/文件/配置；**不含**待实现路径自身的值（该路径必须是 None）、
  不含路径名与目标模型（它们用于过滤与身份绑定）。
- 因此当已确认上下文为空时查询为空 → 得分 0 → 明确 `empty_query` 无命中，不猜测。
- 只接受**在 `INTENT_PATHS` 内、属于三条知识路径、`user_delegated`、无确认值、
  未 PIN** 的路径；违反者是编程错误 → `ValueError`/`TypeError`（**不是**
  `KnowledgeError`，绝不被引擎吞掉）。非知识路径的委托在本版不检索。
- 去重 + 字典序 + 最多 `MAX_RETRIEVAL_PATHS`(3) 条。
- 四个绑定 ID（session/intent/execution/confirmation）由 `RetrievalRequest` 承载并
  写入 Bundle，检索不以自然语言 query 作为身份。

### 4. 过滤、评分、排序、采用/回退（`LocalKnowledgeEngine`）

- 顺序：approved（`build_units()`）→ `target_models` = 目标模型**精确标识**或显式
  `any` → 路径 → conditions → 词法评分。
- 排序：`-score, knowledge_id, version` 字典序稳定；每路径保留 **Top-3** 快照；
  正分但排在 Top-3 之外的单元记为 `below_top_k`。
- **零分不命中**；文件行序不影响选择（拒绝记录也按
  `reason_code/knowledge_id/version` 排序，Bundle 内容与行序无关）。
- 采用：仅当**最高分**对应的 candidate 值唯一时采用（稳定取排序首位），同分同
  candidate 保留全部 Top-3 诊断；
- **ambiguous**：最高分并列且 candidate 不同 → 不采用、回退，并保留命中快照。
  判歧义时考察**全部正分单元的最高分并列集合**，而不只是被保留的 Top-3，因此
  第 4 个（Top-3 之外）同分异 candidate 仍会触发 ambiguous。
- 结果对象：`PathRetrievalResult(outcome ∈ {adopted,no_hit,ambiguous,empty_query})`
  + `hits`（≤3 不可变快照）+ `rejections`（封闭原因枚举：`model_mismatch`/
  `conditions_not_satisfied`/`zero_score`/`below_top_k`/`tie_ambiguous`/
  `tie_not_adopted`）。

### 5. `KnowledgeBundle`（`bundle.py`，frozen + `extra="forbid"`）

- 字段：`bundle_id`、`session_id`、`intent_revision_id`、`execution_revision_id`、
  `confirmation_id`、`target_model`、`status`、`schema_version`、`corpus_version`、
  `tokenizer_version`、`retrieval_version`、`corpus_fingerprint`、`queries`、
  `path_results`、`recommendations`、`reason_code`、`reason`、`created_at`。
- `status ∈ {ok, no_pending_paths, no_approved_units, corpus_error}`；
  `is_fallback`：非 `ok`，或存在未产生推荐的路径（`status=ok` 但某路径无推荐时为
  `True`）。
- 上限：最多 3 条路径 × Top-3；查询/结果不得重复路径；推荐恰好每路径一条且必须
  指向该路径一条**被 adopted 的保留 hit**；非 `ok` 不得携带推荐。
- 单元快照含 `knowledge_id`/`version`/`content_hash`/`applicable_path`/
  `candidate_value`/`source`/`content`/`score`/`rank`/`matched_tokens`。
- **语料指纹** `compute_corpus_fingerprint(manifest)` =
  schema/corpus/tokenizer/retrieval 四版本 + manifest **逐文件 sha256 与
  unit_count**（按 path 排序后 canonical JSON 的 sha256）；manifest 文件顺序不影响
  指纹，任一文件哈希/路径、或任一版本变化都会改变指纹。`corpus_error` Bundle 的
  版本与指纹为 `None`，其余诊断 Bundle 必须记录它们。
- `created_at`：tz-aware UTC，naive 被拒绝；JSON 序列化固定 `+00:00`。
- 无命中/`approved` 为空/语料读取校验失败/没有待实现路径时**仍产生诊断 Bundle**，
  与实际采用明确分离。

### 6. `bundle_id` 与 `kbu` 前缀兼容扩展

- 新增前缀 `KNOWLEDGE_BUNDLE_ID_PREFIX = "kbu"`（knowledge bundle unique），形态
  `kbu_<32hex>`，由 `domain.new_id` 生成。
- **兼容扩展说明**：它是对 ARCHITECTURE 5.1 冻结前缀表
  （`ses/msg/irev/erev/cnf/qst/pra/gen/fbk/rlz`）的**追加**，不与既有前缀冲突，
  也不改变既有 ID 语义。该前缀目前**尚未写入 ARCHITECTURE 5.1 表**；如需正式登记，
  属 Step 03/04 或一次最小架构兼容扩展，本交接先记录。
- `bundle_id` **每次检索唯一、不复用**（append-only 主键）：同一 confirmation 重复
  compile 会产生不同 `bundle_id`。因此可复现性/幂等判断必须依赖内容字段
  （`status`、`corpus_fingerprint`、`queries`、`path_results`、`recommendations`），
  **不得**用 `bundle_id` 去重或匹配历史。

### 7. 检索接口与 Fake 同合同

- `RetrievalRequest`（frozen）：四个绑定 ID + `intent` + `target_model` +
  `pending_paths`（白名单校验、去重）。
- `KnowledgeEngine`：`@runtime_checkable` `Protocol`，
  `retrieve(request: RetrievalRequest) -> KnowledgeBundle`。
- `LocalKnowledgeEngine`：真实本地实现，构造时二选一传 `corpus` 或 `corpus_dir`；
  语料在首次检索时加载并**缓存固定指纹、不热更新**，加载新版本须显式新建引擎。
- 测试中的 `FakeEngine` 与真实实现满足同一 Protocol（`isinstance` 守卫）。

### 8. 错误边界与安全

- 引擎**只**捕获明确的 `KnowledgeError`（语料缺失/哈希/版本/合同校验失败）并转成
  `status=corpus_error` 的诊断 Bundle（`reason_code` 为 `knowledge.*`）；
  **绝不 `except Exception`**：`TypeError`/`AssertionError` 等编程错误照常抛出
  （测试用 monkeypatch 证明）。
- 禁用 RAG：本步不接开关，由调用方**不调用**检索实现；无查询即无 Bundle。
- `knowledge` 包**不在模块顶层 import `prompt_engine`**；`content` 始终是不可信
  数据，只作为不可变快照保存，不执行、不参与采用决策。

## 变更文件

新增（Step 02 拥有的 source）：

- `visual_intent_agent/knowledge/bundle.py`
- `visual_intent_agent/knowledge/retrieval.py`

修改（最小、追加式）：

- `visual_intent_agent/knowledge/__init__.py`（只追加 Step 02 导出；更新模块
  docstring 的边界说明；未改动 Step 01 任何已冻结合同名/语义）
- `tests/knowledge/test_knowledge_public_api.py`（`EXPECTED_PUBLIC_NAMES` 追加
  Step 02 新名字，纯追加）
- `tests/knowledge/knowledge_helpers.py`（追加 `build_intent`/`build_request`/
  `load_test_corpus` 三个夹具工厂；既有函数未改）

新增（Step 02 正式测试）：

- `tests/knowledge/test_knowledge_retrieval.py`（39 项）
- `tests/knowledge/test_knowledge_bundle.py`（56 项）

未触碰：`prompt_engine`、`realization`、`persistence`、`cli`、`generation`、
`pyproject.toml`、`uv.lock`、`.env`、`docs/ARCHITECTURE.md`、
`architecture_decision_005.md`、`v0_4_step_01_handoff.md`。未 commit/push。

## 公开接口

```python
from visual_intent_agent.knowledge import (
    # 规范化 / 评分 / 条件
    normalize_text, tokenize, unit_retrieval_tokens, score_tokens,
    read_confirmed_intent_value, evaluate_conditions, build_query_text,
    SUBJECT_COUNT_PATH,
    # 查询 / 请求 / 引擎接口
    QueryBuilder, RetrievalRequest, KnowledgeEngine, LocalKnowledgeEngine,
    # Bundle 合同
    KnowledgeBundle, KnowledgeQuery, KnowledgeUnitHit, PathRetrievalResult,
    KnowledgeRejection, KnowledgeRecommendation,
    BundleStatus, RetrievalOutcome, RejectionReason,
    compute_corpus_fingerprint,
    KNOWLEDGE_BUNDLE_ID_PREFIX,  # "kbu"
    TOP_K,                       # 3
    MAX_RETRIEVAL_PATHS,         # 3
)
```

- `LocalKnowledgeEngine(corpus=...)` 或 `LocalKnowledgeEngine(corpus_dir=...)`，
  `retrieve(request) -> KnowledgeBundle`；派生只读视图：
  `bundle.is_fallback`、`bundle.recommendation_for(path)`、`bundle.result_for(path)`。
- Bundle 原因/状态枚举均封闭；`reason_code` 在 `corpus_error` 时为
  `knowledge.*`，其余为 `no_pending_paths`/`no_approved_units`/`empty_query`/
  `no_hit`/`ambiguous`/`unique_top_candidate`。
- 无新增配置项，无新增依赖，无网络。

## 测试命令与真实结果

```bash
uv run pytest -q tests/knowledge
# 191 passed in 0.37s

git diff --check
# 无输出（exit 0）
```

构成（2026-09-16 实测，collect-only 计数）：

| 文件 | 数量 |
|---|---|
| `test_knowledge_retrieval.py`（Step 02 新增） | 39 |
| `test_knowledge_bundle.py`（Step 02 新增） | 56 |
| `test_knowledge_contract.py`（Step 01） | 55 |
| `test_knowledge_loader.py`（Step 01） | 24 |
| `test_knowledge_public_api.py`（Step 01 + 追加名） | 7 |
| `test_knowledge_production_corpus.py`（Step 01） | 4 |
| `test_knowledge_security.py`（Step 01） | 6 |
| **合计** | **191** |

覆盖（对应任务书验收）：NFKC/英文大写/`close-up`≡`close_up`/中文二元组与单字/混合
边界；aliases 与短词；空输入 → `empty_query`；无命中；跨模型（精确 vs `any` vs
其他模型）；draft 排除；条件缺失/不满足/AND/空条件；`subject.count` 严格十进制；
字符串逐字比较；先过滤后排序；零分不命中；每路径 Top-3 与 `below_top_k`；行序无关；
manifest 文件顺序无关的指纹；指纹随哈希/版本可观测；缓存不热更新；同分同 candidate
采用、同分异 candidate ambiguous（含 Top-3 之外的第 4 个并列）；至少两个确认场景给出
不同合法推荐；多路径分别检索合并一个 Bundle；生产语料 0 approved → 回退且测试
approved 不冒充生产；`KnowledgeError` → 诊断 Bundle，编程错误不被吞；
`KnowledgeEngine` runtime-checkable Protocol 与 Fake 同合同；`created_at` JSON
`+00:00`；恶意正文只作数据。

未运行：全量测试、真实 Provider、图片生成、评测（按任务限定只跑 Step 02 局部测试
与 `git diff --check`）。

## 生产语料状态

- `knowledge_base/v0.4/`：`corpus_version = v0.4-draft-1`，9 条全部 `draft`、
  0 条 `approved`；`load_corpus()` 可加载校验，但 `build_units() == ()`。
- 因此对生产语料检索只会得到 `status=no_approved_units` 的诊断 Bundle 并回退；
  离线链路由测试夹具的 `approved` 驱动（非生产审核证据）。
- 要让 RAG 真正有可用知识，必须先完成 `knowledge_base/v0.4/README.md` 的人工审核
  清单并产生真实 `approved` 记录。

## 已知限制

1. **生产 0 approved**：真实 RAG 当前无任何可用知识，全部路径都回退到固定候选表。
2. **不判定 active Realization**：`QueryBuilder` 只校验 `user_delegated`/无值/未 PIN；
   "尚无 active Realization" 属 Step 03 传入 `pending_paths` 时的语义，本步不可见。
3. **仅词法召回**：无 embedding/reranker/向量库/新依赖；中文短词（少于 2 字）与
   语义近义召回能力有限。SQLite FTS5 trigram 不匹配 <3 Unicode 字符子串，故未来
   换 FTS5 必须另测中文短词，不能直接等价替代。
4. **分数为 float**：并列判定基于浮点相等；不同分母理论上可舍入相等而被保守判为
   ambiguous（方向安全：回退而非误采用）。
5. **`bundle_id` 唯一不复用**：同一请求两次检索 ID 不同，幂等/去重必须用内容字段
   （见第 6 节）。
6. **`kbu` 前缀未登记进 ARCHITECTURE 5.1**：本交接记录为兼容扩展，正式登记属
   Step 03/04 或一次最小架构更新。
7. **未接 PromptEngine/持久化/CLI**：本步不改变任何现有生成行为，无开关；关闭 RAG
   由调用方不调用。Bundle 如何进入 PromptArtifact/Realization/Repository 属 Step 03。
8. **审核真实性无法程序证明**：Step 01 合同只拒绝无审核字段的 approved 与 draft
   预填，不能验证 reviewer 身份；仍需人工流程。
9. **`content_hash` 只绑定 `content`**：授权字段完整性由 manifest 逐文件 sha256 与
   `corpus_version` 绑定（ADR-005 第 4 节），Bundle 只记录既有指纹，不额外声称能检测
   自洽伪造。

## 对 Step 03 的输入

- 注入 `KnowledgeEngine` 或构造 `LocalKnowledgeEngine`；用当前有效 Confirmation
  解出的 `IntentRevision`/`ExecutionRevision` + 待实现路径（`user_delegated`、
  无值、未 PIN、尚无 active Realization）构造 `RetrievalRequest`。
- 只消费 `bundle.recommendations`（已采用），`bundle.is_fallback` 为 `True` 的路径
  必须走现有固定候选回退；Bundle 只是推导来源，不能成为新的用户授权类型，不能冒充
  `EvidenceRef`，不能改写 `VisualIntent`、PIN 或确认。
- 持久化 Bundle 必须按 append-only 语义处理（`bundle_id` 唯一），并记录
  `corpus_fingerprint` 与四版本以支持历史回溯；重试复用原 PromptArtifact，不因语料
  更新改变已有生成依据。
- 保持 `knowledge` 包无顶层 `prompt_engine` 反向 import；`kbu` 前缀如需入表按最小
  架构兼容扩展处理。

## 是否满足验收条件：是

Step 02 范围（确定性查询、过滤、排序、回退、可复建索引、KnowledgeBundle、检索
接口与离线验收）已按任务书交付，`uv run pytest -q tests/knowledge` 191 passed，
`git diff --check` 无错误。生产语料仍为 9 draft/0 approved，未虚构人工审核；
Step 03/04 未由本步实施或接线。