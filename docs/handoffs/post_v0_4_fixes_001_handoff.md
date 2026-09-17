# v0.4 后置修复批次 001 交接

- 日期：2026-09-16
- 任务书：[docs/task_books/post_v0.4_fixes/README.md](../task_books/post_v0.4_fixes/README.md)
- 批次范围：F1（知识写库失败进入受控 FAILED）、F2（消费端适用性二次校验）、F3（生产知识审核交接）
- 实际基线：`HEAD = b039b9b7cd49becd0a25e251b37d930fee07da88`，分支 `feature`
- 工作区状态：**dirty，未提交**（本批改动与既有 v0.4 Step 01～04 未提交增量共存）
- 本批未执行：`git commit` / `git push` / `git stash` / 覆盖既有成果

> **归属说明（诚实声明）**：本批开始时工作区已有大量 v0.4 Step 01～04 的未提交成果
> （见 `v0_4_step_01..05_handoff.md`）。由于从未提交，git 无法按提交边界区分批次。
> 本文件按**合同与 ADR**归类 F1/F2/F3 改动；同一文件可能同时含既有 v0.4 增量与本批
> 增量，已在 §4 标注。

---

## 0. 结论速览（四项必须分开报告）

| 结论项 | 结果 | 依据 |
|---|---|---|
| **代码修复是否通过** | **通过（离线）** | F1/F2 定向回归 + 全量离线回归 `2543 passed, 3 deselected`；`git diff --check` 通过 |
| **旧数据库兼容是否通过** | **通过** | v1→v2 迁移、旧 payload 缺字段反序列化、旧 active Realization 复用、原 Prompt retry 测试全绿 |
| **生产知识是否已审核** | **否** | `knowledge_base/v0.4/` 9 条全部 `draft`，0 `approved`，`build_units()` 为空 |
| **正式评测是否执行** | **未执行** | 未启动真实 Provider、图片批次、A/B、人工盲评或 Gate 判定 |

补充：**未运行真实 Provider / 付费图片 / 正式评测 / Gate 判定**；**未 commit / push / stash**。

---

## 1. 基线与工作区实际状态

- `git rev-parse HEAD` → `b039b9b7cd49becd0a25e251b37d930fee07da88`（与任务书审查时 HEAD 一致）。
- `git rev-parse --abbrev-ref HEAD` → `feature`。
- `git status --short`：17 个已修改文件 + 若干未跟踪目录/文件（完整清单见 §4）。
- `git stash list` → 空；无 stash 操作。
- 任务书记录的修复前回归为 **2500 passed, 3 deselected in 7.87s**；该数字是修复前记录，
  **不作为本批次验收**。本批最终实测见 §6。
- 工作区含 v0.4 Step 01～04 未提交增量与本批 F1/F2/F3 改动；二者共同构成当前 dirty 树。

---

## 2. F1：知识持久化失败必须进入受控失败路径

### 2.1 缺口与修复前证据（任务书 §2）

`prompt_engine/engine.py::_append_knowledge_bundle` 直接调用 Repository，可能抛
`RepositoryError`；`generation/pipeline.py::generate` 已先转入 `GENERATING`，但 compile
异常处理只捕获 `PromptCompilationError`。审查用临时数据库 + Fake Image 注入一次
`append_knowledge_bundle` 写入失败，实际结果：

```text
异常：RepositoryError / persistence.database_error
会话状态：GENERATING
retry：generation.invalid_state
图片调用：0
```

既有 `test_bundle_persistence_failure_does_not_leave_a_prompt` 只直接调用 compile，
未覆盖生成管线状态。

### 2.2 实现（`visual_intent_agent/generation/pipeline.py`）

在 `GenerationPipeline.generate` 的 **compile 调用边界**新增 `except RepositoryError`
分支，复用既有 `_log_and_mark_failed` + `GENERATING → FAILED` 迁移：

```python
except RepositoryError as exc:
    # compile 内部的 Bundle / Realization / Prompt 写入失败：同样复用 FAILED
    # 失败记录与迁移机制，绝不停留在 GENERATING。原 `persistence.*` code 与
    # 异常因果原样保留（bare raise），不伪装成知识无命中或编译失败。
    self._log_and_mark_failed(
        session_id,
        reason_code=exc.code,
        detail="failed to persist compile output (knowledge bundle / realization / prompt)",
    )
    raise
```

边界遵守：未添加 `except Exception`、未新增自动重试层、未自动重新确认或重新 compile；
原 `persistence.*` 错误 code 与异常因果保留（`bare raise`）；PromptEngine 独立调用仍可抛
原 `RepositoryError`（未改其公开异常合同，未叠加多套失败处理）；Bundle → Realization →
Prompt 保存顺序不变，未重构多表事务。

### 2.3 修复后证据（离线故障注入，6 个新测试）

`tests/generation/test_generation_persistence_failure.py`（5 个）：

1. `test_bundle_write_failure_marks_failed_without_provider_call_or_prompt` — 真实管线注入
   Bundle 写入失败：状态 `FAILED`、图片调用 0、无新 Prompt/Generation、无悬空引用。
2. `test_realization_write_failure_marks_failed_and_leaves_no_prompt` — 覆盖 Realization 写入失败。
3. `test_prompt_write_failure_marks_failed_and_leaves_no_prompt` — 覆盖 Prompt 写入失败
   （统一捕获 compile 内 `RepositoryError`，不只覆盖 Bundle 表）。
4. `test_second_round_bundle_failure_preserves_history_and_blocks_stale_retry` — 第二轮
   Bundle 写入失败：旧图/旧 Prompt 保留，但不能用旧确认 retry。
5. `test_unwritable_database_keeps_original_error_and_reports_migration_failure` — 失败处理
   自身遇数据库不可写：保留原始错误、不无限重试、不产生成功响应。

`tests/cli/test_cli_persistence_failure.py`（1 个）：

6. `test_cli_reports_bundle_write_failure_safely_and_does_not_offer_retry` — CLI 安全提示，
   不崩溃、不停在 GENERATING 瞬时分支；无有效 Prompt 时不诱导 retry。

Provider 失败重试复用原 Prompt、RAG 关闭流程不变：沿用
`tests/generation/test_generation_knowledge_retry.py`
与 `tests/prompt_engine/test_prompt_engine_knowledge.py` 既有回归，均全绿。

---

## 3. F2：保存适用条件快照并在消费端复核

### 3.1 缺口与修复前证据（任务书 §3）

`knowledge/bundle.py::KnowledgeUnitHit` 只保留命中内容等字段，缺少 `conditions`、
`target_models` 与审核状态快照；`PromptEngine._recommendation_is_usable` 把 conditions
判断交给检索器。审查模拟检索器误用上下文：当前确认 `environment.mode=indoor`，检索器用
`outdoor` 上下文返回仅在 `outdoor` 条件下适用的单元，仍携带正确 session/revision/
confirmation IDs，编译器采用了该建议。问题定性为**违反单元自身适用条件**，不宣称当前正常
检索路径存在误判。

### 3.2 合同扩展与版本（见 `architecture_decision_006.md`）

- 新增 `knowledge/bundle.py::KnowledgeEligibilitySnapshot`：`snapshot_version`
  默认 `eligibility.v1`（受 `SUPPORTED_ELIGIBILITY_SNAPSHOT_VERSIONS` 约束）、
  `conditions`（复用 `KnowledgeCondition` 封闭算子）、`target_models`（非空；精确标识或
  显式 `any`）、`review_status` / `reviewer` / `reviewed_at`（tz-aware UTC）。
- `KnowledgeUnitHit` **追加**可选 `eligibility_snapshot`，默认 `None`。
- `None` 与 `()` 语义不同（冻结）：整块 `None` = 旧记录未提供条件证据 → **不得采纳**；
  `conditions=()` = 显式“无附加适用条件” → 可采纳（其余审核/模型证据仍需满足）。
- 版本标识落在**嵌套快照**内，不污染 Bundle 顶层 `schema_version`（语料指纹）与
  `retrieval_version`（词法语义）。
- 快照只由 `LocalKnowledgeEngine` 从**已校验的权威 `KnowledgeUnit` 原样复制**，不从 query
  猜、不从正文重新解析。校验复用 `models.py` 唯一实现（`clean_target_models` /
  `check_review_integrity` / `ensure_review_utc`）。

### 3.3 消费端复核顺序（`prompt_engine.engine.evaluate_recommendation`）

原硬边界不放宽，按九步复核，任一失败即回退既有固定候选表并记录封闭原因码
（`AdoptionRejectionReason`）：

1. 路径已有确认值 / 非 `user_delegated` / 已 PIN → 拒绝；
2. 候选值不在唯一权威 `DELEGATED_CANDIDATES` → 拒绝；
3. Bundle 内该路径 query 缺失或目标模型不符 → 拒绝；
4. 无 adopted 路径结果或没有保留 hit → 拒绝；
5. 推荐必须镜像 Top-1 hit 的 path/id/version/content_hash/candidate/score；
6. **实值复核** `sha256(hit.content) == hit.content_hash`（非只查哈希形状；推荐与命中项
   同填同一错误哈希也照样被拒）；
7. 快照存在、版本受支持、`approved` 且审核字段有效；
8. 单元 `target_models` 精确匹配编译器目标模型或显式 `any`；
9. 用 PromptEngine **自己读取的已确认 Intent** 复用 `evaluate_conditions` 判定全部条件
   （缺字段不满足、`subject.count` 严格十进制、`equals`/`in` 语义不变），**不**用 Bundle
   查询文本替代事实。

### 3.4 持久化 `adoption_decisions`（无 schema 变更）

- `KnowledgeBundle` 兼容追加默认空 `adoption_decisions`
  （`KnowledgeAdoptionDecision`：`path`/`knowledge_id`/`version`/`candidate_value` +
  `outcome = adopted | rejected` + 必填 `reason_code`（rejected）/ 空（adopted）+ `reason`）。
- 跨字段不变量：裁定必须镜像同一 Bundle 内实际存在的推荐，且每路径至多一条。
- `PromptEngine` 落库前调用 `with_adoption_decisions(...)` 生成**新**对象（完整重校验后
  构造），检索器原对象不被原地修改，`bundle_id` 不变。
- 落库后即可查询：CLI/审计可区分“检索器推荐了什么”（`recommendations`）与“编译器是否
  实际采用”（`adoption_decisions`）。
- **F2 不新增表、不改 schema、不迁移数据**；裁定随不可变 Bundle payload 落库，复用
  v0.4 Step 03 的 `knowledge_bundles` 信封表。

### 3.5 CLI 展示（`visual_intent_agent/cli.py`）

生成后只读展示本次采用/回退/复用、受影响路径与 Bundle/知识单元来源 ID；被拒推荐**绝不**
显示成 `adopted`，旧 Bundle 明确标注“未记录裁定（旧 Bundle，不作为已采用）”；可按
`bundle_id` 从 Repository 查回完整来源。

### 3.6 修复后证据

`architecture_decision_006.md` §7 所列新增覆盖全部为离线测试并通过，包括：快照合同/版本/
审核不变量/通配拒绝、旧 payload 缺字段读取、检索器快照原样复制、outdoor-only 推荐给
indoor 会话被拒并回退、缺字段/equals/in/`subject.count` 边界、单元模型不匹配、draft 与
审核字段缺失、content/hash 不符、合法快照仍被采用、adopted/rejected 裁定持久化
round-trip、旧式 active Realization 复用零新检索、CLI 被拒建议不误报 adopted。相关测试
文件与数量见 §4.2。

---

## 4. 变更文件清单

### 4.1 F1（本批）

| 文件 | 说明 |
|---|---|
| `visual_intent_agent/generation/pipeline.py` | compile 边界新增 `except RepositoryError` → 受控 FAILED（原 code/因果保留） |
| `tests/cli/cli_helpers.py` | 测试助手追加可选 `knowledge_engine` 注入；默认 `None` 行为不变 |
| `tests/generation/test_generation_persistence_failure.py` | 新增，5 个故障注入测试 |
| `tests/cli/test_cli_persistence_failure.py` | 新增，1 个 CLI 安全提示测试 |

### 4.2 F2（本批）

| 文件 | 说明 |
|---|---|
| `visual_intent_agent/knowledge/bundle.py` | 新增 `KnowledgeEligibilitySnapshot`（`eligibility.v1`）、`KnowledgeUnitHit.eligibility_snapshot`、`KnowledgeAdoptionDecision`、`adoption_decisions`、`with_adoption_decisions`、`AdoptionRejectionReason` |
| `visual_intent_agent/knowledge/retrieval.py` | 从已校验 `KnowledgeUnit` 原样复制快照；新增/复用 `target_model_applies` 供检索与消费端共用 |
| `visual_intent_agent/prompt_engine/engine.py` | 新增 `evaluate_recommendation`（九步复核）；`_consume_knowledge_bundle` 写裁定；落库前 `with_adoption_decisions` |
| `visual_intent_agent/cli.py` | 只读展示 adopted/rejected 裁定，旧 Bundle 不误报 |
| `tests/prompt_engine/test_prompt_engine_knowledge.py` | 21 个测试（含 F2 消费端复核/回退） |
| `tests/knowledge/test_knowledge_bundle.py` | 52 个测试（含快照/裁定合同、旧 payload 兼容） |
| `tests/knowledge/test_knowledge_retrieval.py` | 40 个测试（含快照原样复制） |
| `tests/persistence/test_persistence_knowledge_bundles.py` | 12 个测试（Bundle 落库/读取 round-trip） |
| `tests/persistence/test_persistence_public_api.py` | 公共 API 断言更新 |
| `docs/handoffs/architecture_decision_006.md` | 新增，F2 架构裁定记录 |
| `docs/ARCHITECTURE.md` | 追加 Rev.6（F2 合同与信任边界） |

### 4.3 F3（本批）

| 文件 | 说明 |
|---|---|
| `docs/handoffs/post_v0_4_knowledge_review_checklist.md` | 新增，9 条 draft 逐条待审核清单（ID/version、来源、候选、条件、疑点、待审核状态） |
| `docs/post_v0.4_fixes/README.md` | 新增，本批修复索引/状态摘要 |

F3 **未修改** `knowledge_base/` 任何语料；9 条仍全为 `draft`，未填写任何 `reviewer`/`reviewed_at`。

### 4.4 本批之前既有的 v0.4 未提交增量（对照，非本批新增）

以下为 v0.4 Step 01～04 既有成果，本批未回退、未覆盖：

- `visual_intent_agent/knowledge/`（`models.py` / `loader.py` / `candidates.py` / `retrieval.py` /
  `bundle.py` / `__init__.py` 核心）、`knowledge_base/v0.4/`、`tests/knowledge/` 主体；
- `visual_intent_agent/persistence/schema.sql` + `repository.py` + `__init__.py`：schema `user_version=2`
  第 10 张 append-only 表 `knowledge_bundles`、v1→v2 幂等补表、`append/get/list_knowledge_bundle`；
- `visual_intent_agent/prompt_engine/models.py`（`PromptArtifact.knowledge_bundle_refs`）、
  `visual_intent_agent/realization/models.py`（`RealizationValue` 知识三字段，全有或全空）；
- `tests/persistence/test_persistence_migration_v1_to_v2.py`、`tests/persistence/fixtures/`、
  `tests/cli/test_cli_rag.py`、`tests/generation/test_generation_knowledge_retry.py`、
  `tests/realization/test_realization_models.py`、`docs/handoffs/architecture_decision_005.md`、
  `docs/handoffs/v0_4_step_01..05_handoff.md`、`docs/task_books/mvp_v0.4/`、`README.md`、
  `docs/task_books/README.md`、`tests/cli/cli_helpers.py`、`tests/persistence/test_persistence_schema.py`、
  `tests/prompt_engine/test_prompt_models.py`、`tests/prompt_engine/test_prompt_engine_public_api.py`。

---

## 5. 兼容合同与版本

| 合同 / 版本 | 状态 | 兼容性说明 |
|---|---|---|
| SQLite schema `user_version` | **v2** | v2 = v1 的 9 张表 + `knowledge_bundles`；v1→v2 只补表/索引（`IF NOT EXISTS`），不删表、不改写旧 payload/refs；`user_version > LATEST` 显式拒绝 |
| `knowledge_bundles` | 信封表（refs + payload JSON），append-only | F2 **未新增表、未改 schema**；`adoption_decisions` 在 payload 内 |
| 知识 Schema | `knowledge.v1` | 未变 |
| 分词 / 检索版本 | `keyword.v1` / `lexical.v1` | 未变；`retrieval.py` 有冻结断言 |
| 适用性快照 | `eligibility.v1` | 新增，位于嵌套快照；受支持版本集合可演进 |
| `PromptArtifact` | 追加 `knowledge_bundle_refs`（默认空，非空去重稳定顺序） | 旧 payload 可读；非授权来源 |
| `RealizationValue` | 追加 `knowledge_bundle_id`/`knowledge_unit_id`/`knowledge_unit_version` | 三字段全有或全空；旧 payload 默认 None；不改授权 `source` |
| `KnowledgeUnitHit` | 追加可选 `eligibility_snapshot`（默认 `None`） | 旧 payload 可读；`None` = 无证据 → 不采纳 |
| `KnowledgeBundle` | 追加默认空 `adoption_decisions` | 旧 payload 可读；新对象由 `with_adoption_decisions` 生成，`bundle_id` 不变 |
| `extra="forbid"` | **未放宽** | 旧→新单向可读；新 payload 被旧代码读取会被拒绝（不做降级兼容） |
| Intent / Policy / 确认门禁 / 候选白名单 | **未放宽** | F2 只做纵深复核与回退 |

持久化顺序保持 Bundle → Realization → Prompt；旧 active Realization 复用、原 Prompt retry
与历史读取行为不变；历史 Bundle/Prompt/Realization 未被重写；未删除用户数据库；未改
`.env`、Provider 重试预算或旧评测冻结文件。

---

## 6. 测试命令与结果

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **2543 passed, 3 deselected in 8.61s** |
| `uv run pytest -q tests/knowledge/test_knowledge_production_corpus.py` | 4 passed（生产语料 draft-only 守卫） |
| `git diff --check` | 通过（无空白/冲突标记） |

均为离线：使用 Fake Image Provider、临时 SQLite、临时/内置测试语料，**零新 LLM/网络调用**。
任务书记录的修复前 2500 passed 仅作对照，不作为验收。

---

## 7. F3 生产知识审核状态

- 语料：`knowledge_base/v0.4/`，`corpus_version = v0.4-draft-1`；3 个 JSONL × 3 条 = **9 条**。
- 审核字段：**9 `draft` / 0 `approved` / 0 `rejected`**；`reviewer` / `reviewed_at` 全 `null`；
  `build_units()` 返回空 → 真实 RAG 可用知识 **0 条**（默认 `--rag` 生产语料启用时透明回退）。
- 哈希一致性：3 个文件 sha256 与 `manifest.json` 逐一一致；`unit_count` = 行数 = 3；
  9/9 `content_hash == sha256(content)`；`schema_version`/`tokenizer_version`/`retrieval_version`
  等于冻结点量。
- 来源：9/9 `project_original`，含 `original_declaration`；`source_date`/`url` 为 `null`
  （避免臆造日期）；`target_models` 全为 `["any"]`，未引用未核实的官方模型能力。
- 逐条待审核清单（含疑点与跨单元适用性重叠）见
  [post_v0_4_knowledge_review_checklist.md](post_v0_4_knowledge_review_checklist.md)。
- **本批未把任何 draft 改为 approved，未伪造 reviewer/date，未把测试夹具升格为生产 approved。**
- 后续若获真实审核：按语料版本与哈希流程发布新快照（重算 `content_hash`、文件 sha256、
  `unit_count`），仅改 `review_status` 会因哈希不符被 `load_corpus` 拒绝；再做离线采用验证，
  不自动启动真实评测。

---

## 8. 剩余风险与未执行事项

1. **生产知识未审核**：9 条全 `draft`，真实 RAG 可用知识为 0；不得声称“生产 RAG 已在使用
   审核知识”，也不得以工程测试通过推导知识质量或 RAG 收益。
2. **快照不是数字签名**：`eligibility_snapshot` 用于复核与追溯；`content_hash` 重算只证明
   快照内容自洽，不能证明检索服务可信，也不能证明快照等同于权威语料中的某条单元。信任边界
   仍是经审核、哈希校验的本地权威语料。
3. **同路径规则存在适用性重叠**（供审核人决策）：lighting 的 soft↔dramatic、
   dramatic↔natural；framing 的 close_up↔wide_shot、close_up↔medium_shot；dof 的
   deep↔shallow 与同候选 shallow↔shallow。候选不同者依赖词法打分，并列时回退 `ambiguous`；
   重叠会降低 `adopted` 覆盖率但不静默强采。
4. **单向兼容**：旧→新可读；新 payload 被旧代码读取会被 `extra="forbid"` 拒绝，不保证降级兼容。
5. **未冻结代码身份**：HEAD 仍为 `b039b9b`，工作区 dirty 且未提交；本批未按要求之外做
   commit/push，因此没有可复现的干净快照。
6. **正式评测未启动**：未运行真实 Provider、付费图片、正式 A/B、人工盲评或 Gate 判定；
   任务书的“正式评测继续等待独立启动”约束仍有效。
7. **全库不可写边界**：正常可写数据库中的单次 Bundle 写入失败必须转 FAILED；存储彻底故障时
   不承诺仍能持久化状态，仅保留原始失败与迁移失败的可诊断信息。

---

## 9. 与任务书验收条款对照

| 任务书要求 | 落实 |
|---|---|
| 先加入能在修复前失败的定向回归，再改实现 | F1 新测试先复现 GENERATING 卡死；F2 新测试先复现 outdoor-only 被 indoor 采纳 |
| 使用 Fake Provider、临时 SQLite、临时语料，默认完全离线 | 全部离线，零网络/LLM |
| 只运行相关局部测试；集成后运行一次 `uv run pytest -q` 与 `git diff --check` | 已执行，见 §6 |
| 失败针对根因修复，不删测试、不放宽安全断言 | 未删测试；未放宽 `extra="forbid"`/PIN/确认/Revision/来源校验 |
| 保留业务表、旧 payload、历史 Artifact；不删用户数据库、不改 `.env`/重试预算/旧评测冻结文件 | 已遵守 |
| 编写 `docs/handoffs/post_v0_4_fixes_001_handoff.md` | 本文件 |
| 未获明确要求，不提交、推送、stash 或覆盖现有工作 | 已遵守 |

---

## 10. 交接状态

- 代码修复（F1/F2）：**离线完成并通过回归**；旧数据库兼容：**通过**。
- 生产知识（F3）：**未审核**，待真实审核人按清单决定。
- 正式评测：**未执行**，等待独立启动。
- 工作区：dirty、未提交；无 commit / push / stash；既有 v0.4 成果保留。
