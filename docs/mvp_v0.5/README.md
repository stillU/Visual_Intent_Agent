# MVP v0.5 版本实施状态入口

- 日期：2026-09-16｜状态：**实施完成（v0.5 整体仍为部分完成）：Step 00 完成；Step 01 部分完成（COCO-CN 许可暂不确认）；Step 02 真实审核已发生并录入；Step 03 语料发布与离线采用验证完成；Step 04 集中验收与交接完成**
- 基线：HEAD `b039b9b`，分支 `feature`，工作区 **dirty 且未提交**（无 commit / push / stash）
- 本文件性质：**版本入口与状态摘要**，只做索引与状态陈述，不复制任务书正文，不替代任务书与交接。

## 目标（摘要）

把 v0.4 已接通的 RAG 工程链路变成能够使用**真实审核知识**的功能：在合适的已确认上下文中，
为用户委托的视觉字段选择已有候选，保留正常数据库功能、用户控制和确定性编译。

v0.5 回答“知识是否可信、能否依法使用、能否被正确采用和追溯”；v0.6 再经用户授权回答
“生成效果是否改善”。不以词法命中、测试通过或一张示例图宣称质量提升。

## 与 v0.4 后置修复书（fix 书）的关系

本版是 [v0.4 后置修复书](../task_books/post_v0.4_fixes/README.md) 的**接续**，不重做其代码修复：

| fix 书项 | 状态 | 在 v0.5 中的位置 |
|---|---|---|
| F1 知识写库失败 → 受控 FAILED | 已实现并离线通过（工作区） | Step 00 复核，**不重做** |
| F2 `eligibility.v1` 快照 + 消费端二次校验 + 裁定持久化 | 已实现并离线通过（工作区） | Step 00 复核，**不重做** |
| F3 生产知识审核交接 | **已闭环（限 v0.5 快照）**：`knowledge_base/v0.5/` 有 5 条真实 `approved`（审核人 `change`）；`knowledge_base/v0.4/` 仍 9 `draft` / 0 `approved` | 由 [Step 02](../task_books/mvp_v0.5/02_knowledge_review.md)（人工审核关口）与 [Step 03](../task_books/mvp_v0.5/03_corpus_release.md)（语料发布）完成 |

- 本版沿用 JSONL 权威语料、内存词法检索、`KnowledgeBundle`、原 PromptEngine 与业务数据库；
  **不重建 RAG、不新增数据库或迁移**。
- fix 书交接：[post_v0_4_fixes_001_handoff.md](../handoffs/post_v0_4_fixes_001_handoff.md)；
  待审清单：[post_v0_4_knowledge_review_checklist.md](../handoffs/post_v0_4_knowledge_review_checklist.md)。

## 步骤与关口

| 步骤 | 任务书 | 产物 / 关口 | 状态 |
|---|---|---|---|
| 00 | 基线检查 | [v0_5_step_00_handoff.md](../handoffs/v0_5_step_00_handoff.md) | **已完成**（只读核查，代码/语料零改动） |
| 01 | 开放文本准备 | [v0_5_step_01_handoff.md](../handoffs/v0_5_step_01_handoff.md) | **部分完成**：70 样本、49/21 分组、7 锁定文本与 COCO 人工描述字段映射核验通过；COCO-CN 归档精确许可条款经用户决定**暂不确认** |
| 02 | 知识证据与人工审核 | 9 证据卡 + 5 draft 提案；[v0_5_step_02_handoff.md](../handoffs/v0_5_step_02_handoff.md)、[v0_5_knowledge_review.md](../handoffs/v0_5_knowledge_review.md) | **完成**：真实审核已发生（审核人 `change`，`2026-09-16T10:25:59Z`）；A1/A2/B3/C1/C2 approved，A3/B1/C3 暂缓，B2 退回生成 v2 后再审 |
| 03 | 语料发布与采用验证 | `knowledge_base/v0.5/`（`v0.5-approved-1`）、[v0_5_step_03_handoff.md](../handoffs/v0_5_step_03_handoff.md) | **完成**：5 条获批单元独立快照 + 三路径离线采用/追溯集成（正例、反例、PIN 边界、基线差异、复用/retry/历史兼容） |
| 04 | 集中验收与交接 | [v0_5_release_handoff.md](../handoffs/v0_5_release_handoff.md) | **完成**：发布守卫补自动断言（提案→发布仅审核三字段、v0.4 manifest 字节冻结）；新增 P1 真实工作流→v0.5 知识闭环用例；全量 `2583 passed, 3 deselected`、`git diff --check` 通过 |

## 当前状态（分开陈述，不得混同）

| 项目 | 状态 |
|---|---|
| Step 00 基线核查 | **已完成**：HEAD `b039b9b` / `feature` / dirty 未提交；全量离线 `2543 passed, 3 deselected`；定向 45 passed；`git diff --check` 通过；无 `AGENTS.md` |
| F1 / F2 代码修复 | **离线通过**（在当前工作区，未提交）；Step 00 已复核，不重做 |
| Step 01 数据准备 | **部分完成**：7 个锁定文本 bytes/rows/sha256 通过；COCO-CN 归档本地安全解包（21 成员 / 76773783 B，跳过 11 个目录/AppleDouble/脚本成员），人工描述字段映射已核验；**70 样本**（20/30/20）、开发 49 / 保留 21、67 组不跨池；`tests/tools` **17 passed**（含 COCO-CN 字段与项目派生样例离线测试）；30 条项目派生样例已落地；**归档精确许可条款待确认** |
| Step 02 审核前材料 | **完成**：9 份证据卡 + 5 份 draft 提案（`draft` / `null` / `null`），离线校验 `RESULT: OK`；待决定清单已交付 |
| Step 02 真实人工审核 | **已发生并录入**：审核人 `change`，`2026-09-16T10:25:59Z`；A1/A2/B3/C1/C2 approved，A3/B1/C3 暂缓，B2 退回生成 v2 后再审 |
| Step 03 语料发布 | **完成**：`knowledge_base/v0.5/`（`corpus_version = v0.5-approved-1`）只收录 5 条获批单元；`manifest.json` 文件哈希/条数、`content_hash`、真实审核字段齐备 |
| Step 03 采用验证 | **通过**（离线、Fake Provider + 临时真实 SQLite）：5 条规则各正例 + 条件不符反例；PIN 边界、RAG vs 关闭 RAG 差异、active 复用 / retry / 快照切换 / 旧 Bundle 兼容 |
| Step 04 集中验收 | **完成**：发布守卫新增"发布相对获批提案仅 `review_status`/`reviewer`/`reviewed_at` 三字段可变"与 v0.4 `manifest.json` sha256 字节冻结断言；新增 `FakeLLMProvider → Interpreter/IntentEngine → 确认 → v0.5 知识 → FakeImageProvider → 临时真实 SQLite` 闭环用例（C1 `soft` 实际采用且 Bundle/适用性可追溯） |
| 数据库兼容 | **通过**：旧库 v1→v2 迁移与旧 payload 可读、知识审计关联正确（`tests/persistence` **205 passed**） |
| 本批整合离线回归（Step 04 实测） | `uv run pytest -q` → **2583 passed, 3 deselected**；v0.5 定向 **23 passed**；相关子集（knowledge/generation/persistence/prompt_engine/cli/tools）**689 passed**；`git diff --check` 通过 |
| v0.5 后置收尾（F1/F2/F3，独立批次） | **F1/F2 已完成并通过离线验证**：审核依据原字节归档 [docs/knowledge_reviews/v0.5-approved-1/](../knowledge_reviews/v0.5-approved-1/README.md)（5 份提案 + 历史路径映射，发布守卫/校验工具改读归档）；当前 30 场景清单 [v0_5_acceptance_cases.json](../../tests/fixtures/knowledge/v0_5_acceptance_cases.json) 实际执行 **30/30 pass**；旧 Step 01 派生物为**审核前历史准备，不计当前已执行覆盖**。**F3 COCO-CN 归档精确许可仍暂不确认**；隔离副本回归与最终交接 [post_v0_5_fixes_001_handoff.md](../handoffs/post_v0_5_fixes_001_handoff.md)（已完成；原工作区全量 `2665 passed, 3 deselected`；初始严格副本白名单遗漏 `evaluation/`，补齐代码白名单后的无 `data/` 增强副本全量通过） |
| 生产知识审核（`knowledge_base/v0.4/`） | **未审核**：9 条全 `draft`、0 `approved`，`build_units()` 为空；v0.4 未改 |
| v0.5 语料发布（`knowledge_base/v0.5/`） | **已创建**：5 条 `approved`、`build_units() = 5`；需用 `--knowledge-dir knowledge_base/v0.5` 显式选择 |
| 正式评测 / 真实 Provider / 图片批次 / Gate 判定 | **未执行（本版边界，不得执行；不是阻塞）** |

**本版整体为部分完成。** Step 03 工程/内容通过，Step 04 集中验收与交接已完成；唯一不完整原因是
Step 01 的 COCO-CN 归档许可**暂不确认**（数据准备部分完成）。正式评测属本版**边界**
（任务书明确本版不得执行），不作为不完整原因或阻塞。

> **补充（2026-09-16，v0.5 后置收尾独立批次，不改写上文 Step 04 数字与结论）：** 独立复查另发现审核材料可移交性（F1）与派生样例/验收衔接（F2）两项缺口，均已修复并通过离线验证——审核依据已原字节归档为可交付的 [docs/knowledge_reviews/v0.5-approved-1/](../knowledge_reviews/v0.5-approved-1/README.md)（5 份获批 v2 提案 + `archive_manifest.json` 历史路径映射；归档缺失/篡改仍被拒绝），当前 30 场景清单 [v0_5_acceptance_cases.json](../../tests/fixtures/knowledge/v0_5_acceptance_cases.json) 实际执行 **30/30 pass**，Step 01 旧派生物明确为**审核前历史准备，不计当前已执行覆盖**。F3（COCO-CN 归档精确许可）仍**暂不确认**，未改变许可裁定、未缩减范围、未引入原文；正式评测仍未执行且属本版**范围边界**。收尾批次不含 `data/` 的隔离副本集中回归与最终交接 [post_v0_5_fixes_001_handoff.md](../handoffs/post_v0_5_fixes_001_handoff.md) **已完成**。**v0.5 整体继续标部分完成，不宣称完整完成。**

## 文档索引

- 任务书（本版正文）：[docs/task_books/mvp_v0.5/README.md](../task_books/mvp_v0.5/README.md)
  - [Step 01 开放文本准备](../task_books/mvp_v0.5/01_dataset_preparation.md)
  - [Step 02 知识证据与人工审核](../task_books/mvp_v0.5/02_knowledge_review.md)
  - [Step 03 语料发布与采用验证](../task_books/mvp_v0.5/03_corpus_release.md)
  - [Step 04 集中验收与交接](../task_books/mvp_v0.5/04_acceptance.md)
  - [Agent 派发指令](../task_books/mvp_v0.5/AGENT_DISPATCH_PROMPTS.md)
- 资源包：[RAG 知识与开放测试数据资源包](../task_books/rag_resources/README.md)
- 实施报告：[docs/mvp_v0.5/IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md)（Step 00–04 汇总）
- Step 00 交接：[v0_5_step_00_handoff.md](../handoffs/v0_5_step_00_handoff.md)
- Step 01 交接：[v0_5_step_01_handoff.md](../handoffs/v0_5_step_01_handoff.md)
- Step 02 交接：[v0_5_step_02_handoff.md](../handoffs/v0_5_step_02_handoff.md)
- Step 03 交接：[v0_5_step_03_handoff.md](../handoffs/v0_5_step_03_handoff.md)
- Step 04 集中验收与版本交接：[v0_5_release_handoff.md](../handoffs/v0_5_release_handoff.md)
- 知识审核记录（含真实决定）：[v0_5_knowledge_review.md](../handoffs/v0_5_knowledge_review.md)
- v0.5 后置收尾交接（F1/F2/F3）：[post_v0_5_fixes_001_handoff.md](../handoffs/post_v0_5_fixes_001_handoff.md)
- 审核依据归档（F1）：[docs/knowledge_reviews/v0.5-approved-1/README.md](../knowledge_reviews/v0.5-approved-1/README.md)｜
  当前验收清单（F2）：[v0_5_acceptance_cases.json](../../tests/fixtures/knowledge/v0_5_acceptance_cases.json)
- 发布快照与批准记录：[knowledge_base/v0.5/README.md](../../knowledge_base/v0.5/README.md)｜
  [APPROVAL.md](../../knowledge_base/v0.5/APPROVAL.md)
- 前置修复：[v0.4 后置修复书](../task_books/post_v0.4_fixes/README.md)｜
  [修复交接](../handoffs/post_v0_4_fixes_001_handoff.md)

## 边界声明

- 生产知识审核状态：`knowledge_base/v0.4/` **仍未人工审核**（9 draft / 0 approved）；
  `knowledge_base/v0.5/` 的 5 条已获真实人工审核批准并机械录入（审核人 `change`，
  `2026-09-16T10:25:59Z`），未获批单元（A3/B1/C3/B2）未发布。
- 本版不构成知识质量、RAG 收益或图片质量结论；工程测试通过不等于 RAG 有效。COCO-CN 许可
  暂不确认，故数据准备保持部分完成、v0.5 整体为部分完成（不得标完整完成）。
- 未冻结代码身份：工作区未提交，`b039b9b` 之上为 dirty 树。
- 未获明确要求不提交、推送、stash 或覆盖现有工作；不调用真实 Provider、不启动正式评测。
