# 架构裁定记录 006（v0.4 后置修复 F2）：适用性快照与消费端二次校验

- 日期：2026-09-16
- 裁定方：post_v0.4_fixes F2 实施 Agent
- 上游依据：
  - `docs/task_books/post_v0.4_fixes/README.md` 第 3 节（F2）；
  - `docs/handoffs/architecture_decision_005.md`（知识合同、conditions 语义、
    语料指纹绑定范围）；
  - `docs/ARCHITECTURE.md` Rev.5（v0.4 Step 02～04 知识闭环）。
- 裁定结果：**只追加**可验证的适用性快照与消费端裁定记录，不新增数据库表、不改
  schema、不迁移数据、不重写历史 Bundle/Prompt/Realization，不放宽
  `extra="forbid"`、Intent、Policy、确认门禁与候选白名单。

---

## 1. 缺口与证据

`knowledge/bundle.py::KnowledgeUnitHit` 只保留 id/version/content_hash/来源/内容/
score，不含 conditions、target_models 与审核状态；`PromptEngine` 的消费端复核明确
把 conditions 交给检索器，缺少纵深防御。检索器一旦误用上下文（例如用 outdoor
上下文返回仅适用于 outdoor 的单元），而四个身份 ID 仍然正确，编译器会采用该建议。

本修复补齐"检索器错误、缓存错误、伪实现"等情况下的第二道校验，**不宣称**当前正常
检索路径存在误判，也不允许把缺失证据当成无限适用。

## 2. `eligibility_snapshot` 合同（嵌套、可选、带版本）

在 `knowledge/bundle.py` 新增：

```
KnowledgeEligibilitySnapshot:
  snapshot_version: str = "eligibility.v1"
  conditions: tuple[KnowledgeCondition, ...] = ()   # 复用现有封闭条件类型
  target_models: tuple[str, ...]                    # 非空；精确标识或显式 "any"
  review_status: ReviewStatus
  reviewer: str | None
  reviewed_at: datetime | None                      # tz-aware UTC
```

- `KnowledgeUnitHit` **追加**可选字段 `eligibility_snapshot`，默认 `None`。
- 校验复用 `models.py` 的**唯一实现**：`clean_target_models`、
  `check_review_integrity`、`ensure_review_utc`；`evaluate_conditions` 与新增
  `retrieval.target_model_applies` 同时服务检索过滤与消费端复核，避免第二套解释器。
- 快照**只**由 `LocalKnowledgeEngine` 从已经校验的权威 `KnowledgeUnit` 原样复制，
  从不读取 query 文本、不从正文重新解析。
- 允许构造 `draft`/`rejected` 快照（诊断可达）；能否采纳由消费端显式要求
  `approved` 决定。

### 版本标识为什么不放在 Bundle 顶层

代码中**不存在** Bundle payload 合同版本标识。`KnowledgeBundle.schema_version` 是
语料 manifest 的 schema 版本（`knowledge.v1`），被 `_status_consistency` 与
`compute_corpus_fingerprint` 绑定；`retrieval_version`（`lexical.v1`）由
`retrieval.py` 冻结断言。复用它们会污染语料指纹或词法语义，因此版本落在**嵌套
快照**内，按 `SUPPORTED_ELIGIBILITY_SNAPSHOT_VERSIONS` 的加载器模式演进（未来新增
版本时旧记录仍可读）。

### `None` 与空条件的语义差别（冻结）

- 整块 `eligibility_snapshot = None`：旧记录**未提供条件证据** → 新推荐**不得采纳**；
- `conditions = ()`：显式的"无附加适用条件" → 可以采纳（其余审核/模型证据仍需满足）。

## 3. 消费端裁定持久化（`adoption_decisions`）

`KnowledgeBundle` 兼容追加默认空字段：

```
adoption_decisions: tuple[KnowledgeAdoptionDecision, ...] = ()
KnowledgeAdoptionDecision:
  path / knowledge_id / version / candidate_value
  outcome: "adopted" | "rejected"
  reason_code: AdoptionRejectionReason | None   # rejected 必填；adopted 必须为空
  reason: str
```

- 跨字段不变量：裁定**必须**镜像同一 Bundle 内实际存在的推荐（path 与
  knowledge_id/version/candidate_value 对齐），且每路径至多一条。
- `PromptEngine` 在落库前调用 `KnowledgeBundle.with_adoption_decisions(...)` 生成
  **新**对象（完整重校验后构造）；检索器返回的原对象不被原地修改，`bundle_id` 不变。
- 落库后即持久化可查询：CLI/审计可区分"检索器推荐了什么"（`recommendations`）与
  "编译器是否实际采用"（`adoption_decisions`）。

## 4. 消费端复核顺序（`prompt_engine.engine.evaluate_recommendation`）

1. 路径已有确认值 / 非 `user_delegated` / 已 PIN → 拒绝（原硬边界不放宽）；
2. 候选值不在唯一权威 `DELEGATED_CANDIDATES` → 拒绝；
3. Bundle 内该路径 query 缺失或目标模型不符 → 拒绝；
4. 无 adopted 路径结果或没有保留 hit → 拒绝；
5. 推荐必须镜像 Top-1 hit 的 path/id/version/content_hash/candidate/score；
6. **实值复核**：`sha256(hit.content) == hit.content_hash`，不是只查哈希字符串形状；
   同一错误哈希同时写进推荐与命中项也照样被拒；
7. 快照存在、版本受支持、`approved` 且审核字段有效；
8. 单元 `target_models` 精确匹配编译器目标模型或显式 `any`；
9. 用 PromptEngine **自己读取的已确认 Intent** 复用 `evaluate_conditions` 判定全部
   条件（缺字段不满足、`subject.count` 严格十进制、`equals`/`in` 语义不变），
   **不**用 Bundle 的查询文本替代事实。

任一步失败：该路径回退现有固定候选表，并写入 `rejected` 裁定 + 封闭原因码
（`AdoptionRejectionReason`）；绝不与 `adopted` 同时显示。身份不符、pending 越界、
非 `KnowledgeBundle`、以及**无法重新通过合同校验**的伪 Bundle 一律硬失败
（`prompt.unauthorized_addition`），不落库非法审计记录。

## 5. 信任边界（明确留白）

快照用于复核与追溯，**不是**数字签名：`content_hash` 重算只证明快照内容自洽，不能
证明检索服务可信或证明快照等同于权威语料中的某条单元；`conditions`/`target_models`/
审核字段仍由检索器复制。信任边界仍是经审核、哈希校验的本地权威语料。原始 `content`
依旧不可执行、不进入 clause、不构成用户授权。

## 6. 兼容性与持久化

- `knowledge_bundles` 仍是信封表（refs + payload JSON），**无需 schema 变更或数据
  迁移**；`append_knowledge_bundle` 对 payload 形状透明。
- 旧 payload 缺 `eligibility_snapshot` / `adoption_decisions` 仍可反序列化
  （默认 `None` / `()`）；新 payload 由旧代码读取会被 `extra="forbid"` 拒绝——只保证
  单向旧→新可读，不做降级兼容。
- 旧 active Realization 复用、原 Prompt retry、历史读取与 v1→v2 迁移测试保持原行为；
  历史 Bundle/Prompt 不被重写。

## 7. 证据（离线）

- 定向回归：`tests/knowledge`、`tests/prompt_engine`、`tests/persistence`、
  `tests/cli`、`tests/generation`、`tests/realization` 全绿。
- 新增覆盖：快照合同/版本/审核不变量/通配拒绝、旧 payload 缺字段读取、检索器快照
  原样复制、outdoor-only 推荐给 indoor 会话被拒并回退、缺字段/equals/in/
  `subject.count` 边界、单元模型不匹配、draft 与审核字段缺失、content/hash 不符、
  合法快照仍被采用、adopted/rejected 裁定持久化 round-trip、旧式 active Realization
  复用零新检索、CLI 被拒建议不误报 adopted。
- 本裁定只记录工程合同，不代表生产知识已审核，也不构成 RAG 收益或图片质量结论。
