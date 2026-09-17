# v0.5 Step 00 交接：基线核查

- 日期：2026-09-16
- 任务书：[docs/task_books/mvp_v0.5/README.md](../task_books/mvp_v0.5/README.md) Step 00
- 范围：**只做基线只读核查**。Step 01～04 未开始；未改代码、未改语料、未新增/批准知识、
  未调用真实服务。
- 版本实施状态入口：[docs/mvp_v0.5/README.md](../mvp_v0.5/README.md)

## 1. 工作区与代码身份

| 项目 | 实测值 |
|---|---|
| `git rev-parse HEAD` | `b039b9b7cd49becd0a25e251b37d930fee07da88`（`b039b9b`） |
| 分支 | `feature` |
| 工作区 | **dirty，未提交**（`git status --short` 42 项） |
| `git stash list` | 空（未 stash） |
| 项目规则文件 | **无 `AGENTS.md`**（全仓搜索无结果，`.venv` / `.git` 除外） |
| commit / push | 未执行 |

工作区同时含 v0.4 未提交增量与 v0.4 后置修复（F1/F2/F3）改动，无法按提交边界区分批次；
本步以**实际工作区**为基线，未回滚、未覆盖任何既有成果。

## 2. 测试与回归核查（全部离线）

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **2543 passed, 3 deselected** |
| 定向（5 个文件，见下） | **45 passed** |
| `git diff --check` | 通过（无空白错误 / 冲突标记） |

定向命令：

```bash
uv run pytest -q \
  tests/generation/test_generation_persistence_failure.py \
  tests/cli/test_cli_persistence_failure.py \
  tests/prompt_engine/test_prompt_engine_knowledge.py \
  tests/knowledge/test_knowledge_production_corpus.py \
  tests/persistence/test_persistence_migration_v1_to_v2.py
# 45 passed（5 + 1 + 31 + 4 + 4）
```

- 全量命令由本步实测；定向 45 项由 v0.5 派发方复测，本步已**复现**同一结果。
- `pyproject.toml`：`addopts = "-m 'not smoke'"`，真实 Provider smoke 测试**默认排除**；
  `tests/conftest.py` 在无凭据时自动 skip smoke。默认运行完全离线。
- 未运行：真实 Provider、图片生成、正式评测、A/B、人工盲评、Gate 判定。

## 3. fix 书（post_v0.4_fixes）继承核查

| 项 | 核查 | 结论 |
|---|---|---|
| F1 写库失败 → 受控 FAILED | `generation/pipeline.py` 在 compile 调用边界含 `except RepositoryError`，复用 `_log_and_mark_failed` + `GENERATING → FAILED`，保留原 `persistence.*` code 与异常因果（bare raise） | **已实现，离线通过，不重做** |
| F2 适用性快照与消费端复核 | `knowledge/bundle.py` 含 `KnowledgeEligibilitySnapshot`（`eligibility.v1`）、`KnowledgeUnitHit.eligibility_snapshot`、`AdoptionRejectionReason`、`adoption_decisions` / `with_adoption_decisions`；`prompt_engine/engine.py::evaluate_recommendation` 九步复核 | **已实现，离线通过，不重做** |
| F3 生产知识审核 | `knowledge_base/v0.4/` 9 条全 `draft`、0 `approved`，`build_units()` 为空；待审清单已存在 | **未闭环**，转 v0.5 Step 02 / 03 |

- 合同 / 版本未变：`knowledge.v1` / `keyword.v1` / `lexical.v1` / `eligibility.v1`；
  `extra="forbid"`、Intent、Policy、确认门禁与候选白名单**未放宽**。
- 本步对 `visual_intent_agent/` 代码**零改动**。

## 4. 知识语料实际状态

- 目录：`knowledge_base/v0.4/`；语料版本 `v0.4-draft-1`；3 文件 × 3 条 = **9 条**。
- 审核字段：**9 `draft` / 0 `approved` / 0 `rejected`**；`reviewer` / `reviewed_at` 全 `null`。
- `build_units()` 为空 → 生产 RAG 可用知识 **0 条**，`--rag` 路径透明回退（预期行为，非缺陷）。
- 待审清单：[post_v0_4_knowledge_review_checklist.md](post_v0_4_knowledge_review_checklist.md)
  （真实审核人签署区留空）。
- 本步**未**批准任何知识，**未**填造 `reviewer` / `reviewed_at`，**未**把测试夹具 `approved`
  当生产审核证据。

## 5. 本步变更文件

新增：

- `docs/mvp_v0.5/README.md`（版本实施状态入口，不搬迁任务书）
- `docs/handoffs/v0_5_step_00_handoff.md`（本文件）

最小编辑：

- `docs/task_books/mvp_v0.5/README.md`（新增“与 fix 书的关系”一节，明确继承与不重做 F1/F2）
- `docs/task_books/README.md`（增加 `docs/mvp_v0.5/` 状态入口链接）

未触碰：`visual_intent_agent/` 全部代码、`tests/`、`knowledge_base/`、`pyproject.toml`、
`.env`、`uv.lock`、既有交接与冻结物。未 commit / push / stash。

## 6. 已知限制与风险

1. **未冻结代码身份**：工作区 dirty 且未提交，无可复现干净快照；本步以实际工作区为基线。
2. **生产知识未审核**：可用知识 0 条；不得声称“生产 RAG 已在使用审核知识”。
3. **Step 00 时点状态（已更新）**：本步执行时 `data/staging/`、`knowledge_base/v0.5/` 均不存在；
   后续 Step 01 已建立 `data/staging/rag_resources_20260916/`（`knowledge_base/v0.5/` 仍未创建）。
4. **正式评测未启动**：未调用真实 Provider，未跑图片批次 / 评分 / Gate 判定。
5. **Step 01 已完成实际准备（本项已更新）**：用户已授权按计划实施；7 个锁定文本核验复用、
   COCO-CN 归档本地安全解包均已完成，见 [v0_5_step_01_handoff.md](v0_5_step_01_handoff.md)。
   该步标“部分完成”；COCO 人工描述字段映射已核验，剩余为归档精确许可条款待确认。
6. **Step 02 人工审核关口**：无真实审核人及决定时 v0.5 不能完成，最多报告
   “准备 / 工程部分完成，内容审核待定”。

## 7. 下一步输入

- **Step 01**：已按 [资源包](../task_books/rag_resources/README.md) 与
  [sources.lock.json](../task_books/rag_resources/sources.lock.json) 完成文本准备到
  `data/staging/rag_resources_20260916/`（**部分完成**，见
  [v0_5_step_01_handoff.md](v0_5_step_01_handoff.md)）；COCO 字段映射已核验，剩余归档精确许可条款确认。
- **Step 02**：审核前材料已完成（9 证据卡 + 5 draft 提案），见
  [v0_5_step_02_handoff.md](v0_5_step_02_handoff.md) 与
  [v0_5_knowledge_review.md](v0_5_knowledge_review.md)；**真实审核未发生，停下等审核人决定**。
- **Step 03**：仅在真实批准后发布 `knowledge_base/v0.5/`，并用最终快照做离线采用验证。
- 全部步骤遵守 v0.5 README 的范围与安全底线：不新增向量库 / Embedding / 在线爬虫 / 额外 LLM，
  不改领域合同，不提交推送。

## 是否满足 Step 00 验收条件：是

已记录实际 HEAD / dirty 状态、无 `AGENTS.md`、真实 Provider 测试默认排除，并以定向与全量离线
回归确认 F1/F2 修复通过、无阻断回归；未重做已有修复，未改代码或语料。
