# MVP v0.5 实施报告（Step 00–04）

- 日期：2026-09-16
- 性质：**阶段性实施汇总**。不是完成声明，不是知识批准记录，不是效果评测结论。
- 基线：`HEAD b039b9b7cd49becd0a25e251b37d930fee07da88`（`b039b9b`），分支 `feature`，
  工作区 **dirty 且未提交**（含 v0.4 Step 01–05 与后置修复 F1/F2/F3 增量）。
- 版本入口：[docs/mvp_v0.5/README.md](README.md)；任务书：
  [docs/task_books/mvp_v0.5/README.md](../task_books/mvp_v0.5/README.md)。

> **一句话结论：Step 00 完成；Step 01 部分完成（COCO-CN 许可暂不确认）；
> Step 02 真实审核已发生并录入；Step 03 语料发布与离线采用验证完成；
> Step 04 集中验收与交接完成。v0.5 整体为部分完成，唯一不完整原因是 Step 01
> 许可暂不确认；正式评测属本版边界，不是不完整原因。**

## 1. 范围与状态

| 步骤 | 范围 | 状态 | 证据 |
|---|---|---|---|
| 00 基线检查 | 只读核查 HEAD/dirty、测试隔离、F1/F2 继承 | **完成** | [v0_5_step_00_handoff.md](../handoffs/v0_5_step_00_handoff.md) |
| 01 开放文本准备 | 锁定文本核验、COCO 安全解包、70 样本与分组 | **部分完成**（COCO-CN 许可暂不确认） | [v0_5_step_01_handoff.md](../handoffs/v0_5_step_01_handoff.md) |
| 02 知识证据与人工审核 | 9 证据卡、5 draft 提案、真实审核决定 | **完成**（审核人 `change`，`2026-09-16T10:25:59Z`） | [v0_5_step_02_handoff.md](../handoffs/v0_5_step_02_handoff.md)、[v0_5_knowledge_review.md](../handoffs/v0_5_knowledge_review.md) |
| 03 语料发布与采用验证 | `knowledge_base/v0.5/` 发布 + 三路径离线采用 | **完成**（工程/内容） | [v0_5_step_03_handoff.md](../handoffs/v0_5_step_03_handoff.md) |
| 04 集中验收与交接 | 工程回归、数据库兼容、发布守卫、版本完成结论 | **完成** | [v0_5_release_handoff.md](../handoffs/v0_5_release_handoff.md) |

## 2. Step 00：基线核查（完成）

- HEAD `b039b9b`、分支 `feature`、工作区 dirty 未提交（42 项）；无 `AGENTS.md`；
  `git stash list` 空；未 commit / push。
- 全量离线 `uv run pytest -q` → **2543 passed, 3 deselected**；定向 45 项 → **45 passed**；
  `git diff --check` 通过。
- fix 书继承核查：F1（写库失败 → 受控 FAILED）、F2（`eligibility.v1` 快照 + 消费端九步复核 +
  裁定持久化）**已实现且离线通过，不重做**；F3 生产知识审核**未闭环**，转 Step 02 / 03。
- 代码 / 语料零改动。

## 3. Step 01：开放文本准备（部分完成）

- 暂存根：`data/staging/rag_resources_20260916/`（`data/` 依既有 `.gitignore` 不入 Git）。
- **7 个锁定文本全部通过** bytes / 非空行 / sha256 校验，与 `sources.lock.json` 逐一一致；
  已核验文件复用，本次 `totals.downloaded_bytes=0`。
- **COCO-CN 归档本地安全解包**：bytes `15443026`、sha256
  `6c126cd8455363a404806e452ec75066a8fc96d73922d9357d993fcdd1d40b8a`、21 成员、
  解包总 `76773783` B、跳过 11 个目录/AppleDouble/脚本成员；只提取文本/许可，**未下载图片**；人工描述取自
  归档内精确文件 `imageid.human-written-caption.txt`（TSV，已人工抽查）。
- **样本 70 条**：GenEval 20 / CompBench 30 / COCO-CN 20；**开发 49 / 保留 21**，共 **67 组**，
  组不跨池。派生清单：`derived/download_record.json`、`derived/source_samples.jsonl`、
  `derived/split_manifest.json`。
- 工具 / 测试：唯一准备工具 `tools/prepare_v0_5_resources.py`（纯标准库）；`uv run pytest -q tests/tools` → **17 passed**。另生成 30 条项目派生工程样例，成功采用断言保持 BLOCKED，未伪造批准。
- **字段映射已核验**：只读取精确文件 `imageid.human-written-caption.txt`，人工核对两列 TSV 格式，并显式排除机翻、人工翻译、分词版与标签文件；离线测试覆盖该格式。
- **剩余事项（故标部分完成）**：COCO 归档自身精确许可条款待真实审核人确认；文本许可不自动覆盖 COCO 原始照片（本步未下载或提取照片）。

## 4. Step 02：真实人工审核（完成）与 Step 03：语料发布与采用验证（完成）

- 暂存（不入 Git）：`data/staging/rag_resources_20260916/knowledge_review/`
  —— `evidence_cards/` 9 份、`draft_proposals/` 5 份、`README.md`。
- 9 条旧规则**均有处置建议**：收窄待审 3、保留待审 2、暂缓 3、建议移除 1；
  三条路径均有提案（camera 2、framing 1、lighting 2）。
- 5 份提案为完整 `KnowledgeUnit` JSON，`review_status=draft`、`reviewer=null`、`reviewed_at=null`；
  离线校验 `uv run python tools/validate_knowledge_review_proposals.py` → `RESULT: OK`（exit 0）。
- **真实审核已发生并录入**：审核人 `change`，`2026-09-16T10:25:59Z`；批准 A1/A2/B3/C1/C2，
  A3/B1/C3 暂缓，B2 退回生成 v2 后再审（禁止发布旧 v1）。决定与 5 份提案完整文件 SHA-256
  绑定，记录于 [v0_5_knowledge_review.md](../handoffs/v0_5_knowledge_review.md)。
- **Step 03 发布**：新建 `knowledge_base/v0.5/`，`corpus_version = v0.5-approved-1`，只收录
  获批的 5 条（camera 2 / framing 1 / lighting 2）；机械录入（只改审核三字段），
  `content_hash` 与提案一致；manifest 文件哈希/条数、README 许可/版本/限制/复建步骤、
  随包 `APPROVAL.md` 齐备。
- **Step 03 离线采用验证**（Fake Provider + 临时真实 SQLite，最终语料非 demo 夹具）：
  5 条规则各有可达正例 + 条件不符反例；PIN 边界；RAG 选值与关闭 RAG 基线差异只在授权路径；
  active Realization 复用、原 Prompt retry 不重新检索、快照切换不改写历史、旧 Bundle 兼容。
- `knowledge_base/v0.4/` **未修改**：3 个 JSONL sha256 与 `manifest.json` 逐一一致，
  `manifest.json` sha256 仍为 `ec013105e452c4fd47733bb0ca7efafa562519c198dc5ef40eea1b615304b9e2`；
  v0.4 仍 **9 draft / 0 approved**，`build_units() == ()`。
- 详见 [v0_5_step_03_handoff.md](../handoffs/v0_5_step_03_handoff.md)。

## 5. Step 04：集中验收与交接（完成）

按 `04_acceptance.md` 的验收矩阵复用既有断言，只补最小缺口：

- **闭环用例补齐**：`tests/generation/test_generation_v0_5_release_adoption.py` 复用
  `run_p1_to_confirmation`，新增 `FakeLLMProvider → Interpreter/IntentEngine → 确认门禁 →
  LocalKnowledgeEngine(v0.5) → FakeImageProvider → 临时真实 SQLite` 端到端用例，断言 C1
  （`lighting.character.soft_for_tight_framing`）实际采用，且 Bundle（语料版本/指纹）、
  采用裁定与 `eligibility.v1` 快照（reviewer `change` / reviewed_at）可从真实库读回。
- **发布守卫补自动断言**：`tests/knowledge/test_knowledge_v0_5_release.py` 新增
  （1）发布单元相对暂存获批提案**只允许** `review_status`/`reviewer`/`reviewed_at` 三字段变化，
  并重新校验提案文件完整 SHA-256（文件缺失显式失败，不静默跳过）；
  （2）`knowledge_base/v0.4/manifest.json` sha256 字节冻结为
  `ec013105e452c4fd47733bb0ca7efafa562519c198dc5ef40eea1b615304b9e2`。
- **验收矩阵实测**：v0.5 定向 **23 passed**；相关子集（`tests/knowledge tests/generation
  tests/persistence tests/prompt_engine tests/cli tests/tools`）**689 passed**；
  全量 `uv run pytest -q` → **2583 passed, 3 deselected**；`git diff --check` 通过
  （详见 [v0_5_release_handoff.md](../handoffs/v0_5_release_handoff.md)）。
- **结论口径**：正式评测仍**未执行**（本版边界，不得执行，不是阻塞）；v0.5 整体
  **部分完成**，唯一不完整原因是 Step 01 的 COCO-CN 许可暂不确认。

## 6. 必须分开报告的结论（截至本报告）

| 结论项 | 状态 |
|---|---|
| 工程（Step 00 / F1 / F2） | **离线通过**；Step 04 本批整合后全量 `2583 passed, 3 deselected`，`git diff --check` 通过 |
| 数据准备（Step 01） | **部分完成**：样本/分组与 COCO 字段映射达成；COCO-CN 归档精确许可条款**暂不确认** |
| 知识审核（Step 02） | **完成**：真实审核已发生；5 条批准、A3/B1/C3 暂缓、B2 退回生成 v2 后再审 |
| 版本发布（Step 03） | **完成（工程/内容）**：`v0.5-approved-1` 快照 + 三路径离线采用与追溯验证通过 |
| 集中验收（Step 04） | **完成**：发布守卫自动断言 + P1 真实工作流闭环用例 + 全量/子集/定向回归 + 数据库兼容复核 |
| 历史 / 数据库兼容 | v0.4 语料未改（manifest 字节冻结）；旧 Bundle 仍可读但不被静默采用；旧库 v1→v2 迁移与旧 payload 由既有回归覆盖（`tests/persistence` 205 passed） |
| 正式评测 | **未执行（本版边界，不得执行；不是阻塞）**：无真实 Provider、无图片批次、无 A/B、无盲评、无 Gate 判定 |
| **v0.5 整体** | **部分完成**：唯一不完整原因是 Step 01 的 COCO-CN 许可暂不确认；正式评测属本版边界（不得执行），不是阻塞 |

## 7. 边界与未越界声明

- 只机械录入真实审核决定：未把未获批单元改为 `approved`，未填造 `reviewer` / `reviewed_at`，
  未把测试/demo 夹具当生产证据；B2 未发布旧 v1。
- 新建 `knowledge_base/v0.5/` 只收录获批的 5 条；未改领域 Schema / Intent / Policy / PIN /
  确认门禁 / 候选白名单 / 条件算子。
- 未改 `knowledge_base/v0.4/` 与 `data/` 之外的既有冻结物；未改 `.env`、Provider 预算或旧评测文件。
- 未调用真实 Provider、未生成图片、未跑正式评测 / 盲评 / Gate。
- 未 commit / push / stash；工作区仍为 dirty；未覆盖既有 dirty 成果。

## 8. 下一步与需要用户/审核人决定的事项

1. **Step 03 已完成**（发布 + 离线采用验证）；如需变更 5 条之一的条件/来源/模型适用性，
   必须作为新版本重新审核，不得改写 `v0.5-approved-1` 快照。
2. **A3 / B1 / C3 暂缓、B2 退回生成 v2 后再审**：若后续要发布，须先产出 v2 提案并重新走
   真实人工审核；B2 尤其不得以旧 v1 形式发布。
3. **Step 01 剩余**：COCO-CN 归档精确许可条款经用户决定**暂不确认**；字段映射已核验。
   该事项使数据准备保持部分完成、v0.5 整体为部分完成（唯一不完整原因，不得标完整完成）。
4. **Step 04 已完成**（工程回归、数据库兼容、发布守卫与版本结论，见
   [v0_5_release_handoff.md](../handoffs/v0_5_release_handoff.md)）；正式 Provider / 图片
   评测与 v0.6 评测协议继续等待独立授权与另行制定。
