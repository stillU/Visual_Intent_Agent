# v0.4 Step 03 交接：PromptEngine 知识接线与持久化

- 日期：2026-09-16
- 范围：**Step 03**（PromptEngine 接线、来源追溯、Bundle 持久化）。Step 04（CLI /
  开关 / retry E2E）未开始，未实现。
- 工作区基线：HEAD `b039b9b`；所有既有修改原样保留，未回滚、未 commit/push。
- 本次改动仅：`visual_intent_agent/prompt_engine/engine.py`、
  `tests/prompt_engine/test_prompt_engine_knowledge.py`（新增）、
  `tests/prompt_engine/test_prompt_engine_public_api.py`（同步签名断言）。模型与
  Repository 由其他 Agent 完成，本步只读消费，未改模型/Repository/CLI。

## 1. 模型兼容扩展（其他 Agent 交付，本步消费）

- `RealizationValue` 新增**可选** `knowledge_bundle_id` / `knowledge_unit_id` /
  `knowledge_unit_version`：三者必须同时为非空字符串或全为 None（triplet 校验）；
  旧 payload 缺省 None 仍可读。仅说明"为何选这个具体实现"，**不改变**授权
  `source`（仍只能是 `user_delegated`）。
- `PromptArtifact` 新增**可选** `knowledge_bundle_refs`（默认空列表，非空字符串、
  按首次出现顺序稳定去重），是诊断/追溯引用，不是新的授权来源；
  `SourceBinding.source_kind` 集合不变（无 `knowledge`）。
- 复用来源链：Prompt → `source_binding.realization_id` → `RealizationValue` →
  Bundle → 单元快照。

## 2. schema v2 迁移与 Bundle 存储（其他 Agent 交付，本步消费）

- `LATEST_SCHEMA_USER_VERSION = 2`：v1 的 9 张表 + append-only `knowledge_bundles`。
  旧 v1 数据库首次打开时幂等补建（重放 `schema.sql` 的 `IF NOT EXISTS`），不删表、
  不改写旧 payload/refs；`user_version` 高于支持版本时拒绝打开。
- `Repository` 新增 `append_knowledge_bundle` / `get_knowledge_bundle` /
  `list_knowledge_bundles`；`knowledge_bundles` 的 refs 精确三键
  `intent_revision_id` / `execution_revision_id` / `confirmation_id`，各自为真外键，
  且必须属于同一 `session_id`；重复 `bundle_id` 拒绝（append-only，无 UPDATE）。

## 3. PromptEngine 接线（`visual_intent_agent/prompt_engine/engine.py`）

- `__init__(renderer, repo, *, knowledge_engine: KnowledgeEngine | None = None)`：
  keyword-only、默认 None，现有调用逐字兼容；None = 关闭 RAG（不检索、不生成
  Bundle）。
- 检索时机：确认初验与 renderer 支持检查**之后**，在 `_plan_realizations` 中，仅对
  「本版三条知识路径 ∩ `user_delegated` ∩ 无值 ∩ 未 PIN ∩ 无 active Realization」
  的路径**一次**构建 `RetrievalRequest`，绑定同一 session/intent/execution/
  confirmation 与 target_model；复用 active 值**绝不重新检索**。非白名单路径、显式值、
  PIN、missing 原逻辑不变。
- 消费端二次校验（不只相信检索端）：
  - **硬失败**：返回值非 `KnowledgeBundle`（伪造）；身份（四个 ID + target_model）
    不匹配（跨会话/过期/伪造）；出现越出本次 pending 的 query/path_result/
    recommendation（越界 Bundle）。复用现有 `prompt.unauthorized_addition`（未改模型，
    故未新增 error code）。检索返回后立即复核确认，检索期间失效 →
    `prompt.no_valid_confirmation`，且该 Bundle 不落库。
  - **安全回退**：候选值不在唯一权威候选表 `DELEGATED_CANDIDATES`、path 不再
    delegated / 已有值 / 已 PIN、推荐与 Top-1 快照不一致、无 adopted 结果、无推荐
    （诊断 Bundle）→ 逐路径回退现有 `select_delegated_value`；明确的 `KnowledgeError`
    也按无命中回退。编程错误照常抛出，不 `except Exception`。
- 采纳时给新 `RealizationValue` 写三项追溯字段；clause 只用候选值本身，
  知识正文（`content`）绝不进入 clause/Prompt；carry 的 active 值原样保留。
- 持久化顺序：保存前再次确认 → `append_knowledge_bundle`（refs 三键）→
  `append_realization_state` → `append_prompt_artifact`。任一失败硬失败，不产生
  "指向未落库 Bundle 的已保存 Prompt"；允许多次 Repository 调用间留下可辨认的
  未被引用的诊断 Bundle。
- `PromptArtifact.knowledge_bundle_refs` = 本次诊断 Bundle（若有）+ 复用 active
  Realization 实际引用的历史 Bundle，去重稳定；关闭引擎不生成 Bundle，但历史 ref
  仍保留，来源链不断。

## 4. 真实测试结果

- 新增 `tests/prompt_engine/test_prompt_engine_knowledge.py`（17 项）：真实
  `LocalKnowledgeEngine` + approved 夹具证明知识改变新选值与 Prompt；默认 None 与
  无命中回退业务等价（排除随机 ID）；显式值/PIN/missing 不受影响且零检索；
  跨会话/各 ID/模型不匹配与越界路径硬失败；越界候选与恶意正文安全回退；检索期间
  确认过期不生成；active 复用零新检索且历史 Bundle ref 保留；carry 保留知识来源；
  Bundle 写失败无悬空 Prompt、Prompt 写失败无引用未落库 Bundle 的 Prompt；伪造
  非 Bundle 拒绝。
- `test_prompt_engine_public_api.py` 仅同步构造签名断言为 4 参（`knowledge_engine`
  keyword-only、默认 None）；其余既有断言未动。
- 验证命令与结果：
  - `pytest tests/prompt_engine` → 116 passed；
  - `pytest tests/knowledge tests/realization tests/generation tests/persistence
    tests/workflow` → 608 passed；
  - 全量离线 `pytest` → **2480 passed, 3 deselected**；
  - `ruff check` 通过；`git diff --check` 无问题。

## 5. 已知限制与后续（Step 04）

- **generation retry 零新检索** 的 pipeline E2E 留 Step 04：代码路径上
  `GenerationPipeline.retry` 只读已落库 PromptArtifact、绝不调用 `compile`，因此
  结构上零新检索，但尚未用现有 pipeline 写端到端断言。
- **生产知识库 0 approved**：`knowledge_base/v0.4/` 9 条全部 `draft`，真实 RAG 当前
  可用知识为 0 条（实际走诊断 Bundle 回退）；测试夹具中的 approved 不是生产审核证据。
- Bundle 快照按 Step 01/02 合同**不携带** unit 的 `conditions`/`keywords`，消费端
  只能复核身份、授权门禁（delegated/PIN/无值）、候选白名单、目标模型与 Top-1 快照
  一致性；`conditions` 由检索实现基于**同一个**已确认 Intent 判定。
- 未新增知识专用 `prompt.*` error code（`models.py` 由其他 Agent 拥有）；Bundle 身份
  类硬失败复用 `prompt.unauthorized_addition`。若后续要独立 code 供 CLI 提示，需在
  prompt models 侧显式扩展。
- CLI、RAG 开关、B/C 对照独立初始会话属 Step 04。