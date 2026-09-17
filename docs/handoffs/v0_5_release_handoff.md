# v0.5 发布交接（Step 04：集中离线验收与版本结论）

- 日期：2026-09-16
- 依据：[docs/task_books/mvp_v0.5/04_acceptance.md](../task_books/mvp_v0.5/04_acceptance.md)、
  [docs/task_books/mvp_v0.5/README.md](../task_books/mvp_v0.5/README.md)；
  上游交接：[v0_5_step_01_handoff.md](v0_5_step_01_handoff.md)、
  [v0_5_step_02_handoff.md](v0_5_step_02_handoff.md)、
  [v0_5_step_03_handoff.md](v0_5_step_03_handoff.md)、
  [v0_5_knowledge_review.md](v0_5_knowledge_review.md)。
- 基线：`HEAD b039b9b7cd49becd0a25e251b37d930fee07da88`（`b039b9b`），分支 `feature`，
  工作区 **dirty 且未提交**（52 项 status：17 个已跟踪文件修改 + 35 个未跟踪路径），
  未 commit / push / stash。
- 范围：只补最小测试缺口与文档修正；**未改** `visual_intent_agent/**`、领域合同、
  运行时 Schema、默认路径或 Provider，**未调用真实服务 / 真实 Provider**，
  **未执行正式评测**。

## 0. 结论速览（必须分开陈述）

| 结论项 | 结果 |
|---|---|
| 工程（Step 00 / F1 / F2 / Step 04） | **离线通过**：全量 `2583 passed, 3 deselected`；`git diff --check` 通过 |
| 数据准备（Step 01） | **部分完成**：70 样本、49/21 分组、字段映射核验完成；COCO-CN 归档精确许可条款**暂不确认** |
| 知识审核（Step 02） | **完成**：真实审核已发生（`change`，`2026-09-16T10:25:59Z`）；5 条 approved、A3/B1/C3 暂缓、B2 退回生成 v2 后再审 |
| 语料发布（Step 03） | **完成**：`knowledge_base/v0.5/`（`v0.5-approved-1`，5 条 `approved`）三路径可追溯 |
| 数据库兼容 | **通过**：旧库 v1→v2 迁移与旧 payload 可读；知识审计关联正确；v0.4 语料字节冻结 |
| 正式评测 | **未执行（本版边界：任务书明确本版不得执行，不是阻塞）**：无真实 Provider、无图片批次、无 A/B、无盲评、无 Gate 判定 |
| **v0.5 整体** | **部分完成**：唯一不完整原因是 Step 01 的 COCO-CN 归档许可暂不确认；正式评测属本版边界，不是不完整原因或阻塞 |

## 1. 实际 HEAD / 工作区与本批文件范围

- HEAD：`b039b9b`；分支：`feature`；工作区在 HEAD 之上为 dirty 树（v0.2～v0.5 增量与
  v0.4 后置修复均未提交）。`git stash list` 为空。
- 本批（Step 04）只改以下文件，均为**追加/订正**，不回滚、不覆盖既有 dirty 成果：
  - 测试（补缺口）：`tests/knowledge/test_knowledge_v0_5_release.py`、
    `tests/generation/test_generation_v0_5_release_adoption.py`。
  - 文档（状态/哈希/措辞）：`docs/handoffs/v0_5_knowledge_review.md`、
    `docs/handoffs/v0_5_step_03_handoff.md`、`docs/mvp_v0.5/README.md`、
    `docs/mvp_v0.5/IMPLEMENTATION_REPORT.md`、`docs/ARCHITECTURE.md`、
    `docs/task_books/README.md`、`docs/task_books/mvp_v0.5/README.md`、本文件。
- 保护对象：`knowledge_base/v0.4/**`（旧快照）、`data/`（暂存大归档与派生清单，依
  `.gitignore` 不入 Git）、用户数据库、历史 Artifact / Bundle / Prompt 均**未被改写**。
- 未提交、未推送、未 stash、未删除历史；`git diff --check` 通过（无空白/冲突标记）。

## 2. 数据准备（Step 01，**部分完成**）

- 暂存根：`data/staging/rag_resources_20260916/`（`data/` 不入 Git）。
- **7 个锁定文本** bytes / 非空行数 / sha256 **全部通过**，与 `sources.lock.json` 逐一一致。
- **COCO-CN 归档本地安全解包**：bytes `15443026`、sha256
  `6c126cd8455363a404806e452ec75066a8fc96d73922d9357d993fcdd1d40b8a`、21 成员、
  解包总 `76773783` B、跳过 11 个目录/AppleDouble/脚本成员；只提取文本/许可，**未下载图片**；
  人工描述取自归档内精确文件 `imageid.human-written-caption.txt`。
- **样本 70 条**：GenEval 20 / CompBench 30 / COCO-CN 20；**开发 49 / 保留 21**，共 **67 组**，
  组不跨池；派生清单：`derived/download_record.json`、`derived/source_samples.jsonl`、
  `derived/split_manifest.json`；30 条项目派生工程样例（3 路径 × 10 状态）。
- 许可归档：4 份（geneval MIT；t2i_compbench MIT + NOTICE；coco_cn 仓库/数据卡 MIT）。
- **未完成项（故标部分完成）**：COCO-CN 归档自身精确许可条款经用户决定**暂不确认**；
  文本许可不自动覆盖 COCO 原始照片（本步未下载/提取照片）。保留池未用于知识编写或调参。
- 工具/测试：`tools/prepare_v0_5_resources.py`（纯标准库）；`tests/tools` **17 passed**。

## 3. 知识（Step 02 真实审核 + Step 03 发布）

- 审核前材料（暂存，不入 Git）：9 份证据卡 + 5 份 draft 提案
  （`review_status=draft`、`reviewer=null`、`reviewed_at=null`）；离线校验
  `uv run python tools/validate_knowledge_review_proposals.py` → **RESULT: OK（exit 0）**。
- **真实审核已发生并录入**（入口：`docs/handoffs/v0_5_knowledge_review.md` §0、§5）：
  - 审核人 `change`；tz-aware UTC `2026-09-16T10:25:59Z`。
  - **approved（发布 5 条）**：A1 `camera.depth_of_field.shallow_for_close_up`、
    A2 `camera.depth_of_field.deep_for_wide_shot`、
    B3 `composition.framing.medium_shot_for_standing_pose`、
    C1 `lighting.character.soft_for_tight_framing`、
    C2 `lighting.character.dramatic_for_angled_framing`。
  - **暂缓（不发布）**：A3、B1、C3；**退回生成 v2 后再审（不发布旧 v1）**：B2。
  - 决定逐份绑定 5 个 v2 提案文件的完整 SHA-256；机械录入只改审核三字段。
- **发布快照** `knowledge_base/v0.5/`：`corpus_version = v0.5-approved-1`，
  `all_unit_count = 5`、`build_units() = 5`，仅收录获批单元；三路径覆盖
  camera 2 / framing 1 / lighting 2；`corpus_fingerprint =
  c961790c81d22e554672270b03a94474991a9f59315d0dab5585cc09610e81c4`。
- 发布文件哈希（`load_corpus` 已逐文件核验）：

| 文件 | sha256 | unit_count |
|---|---|---|
| `camera.depth_of_field.jsonl` | `9a32c511b0361e181a24f926fbc679dc23be28aef0303d9e95b83eebc5ee62d3` | 2 |
| `composition.framing.jsonl` | `a07af1ac94634362df14079a803c4aef50439e717f4a7ece49635b316b190609` | 1 |
| `lighting.character.jsonl` | `0b159a8fd7455ae9c3bdbc2e6499014f729fe88c5ca35db52253aae2d008b8c5` | 2 |
| `manifest.json` | `0bbeea9bbf7438bd8480e4cc56b1797368e2aa6b47c1601b0f276b44697b37d7` | — |
- `knowledge_base/v0.4/` **未改**：仍 9 draft / 0 approved、`build_units() == ()`，
  `manifest.json` sha256 仍 `ec013105e452c4fd47733bb0ca7efafa562519c198dc5ef40eea1b615304b9e2`。

## 4. 工程（Step 04）：测试、采用证据与数据库兼容

### 4.1 本轮实际运行（命令、退出码、通过数）

```bash
# v0.5 定向（发布守卫 7 + 采用集成 16）
uv run pytest -q tests/knowledge/test_knowledge_v0_5_release.py \
  tests/generation/test_generation_v0_5_release_adoption.py
# 23 passed（exit 0）

# 相关子集（均含知识采用 / 回退 / 数据库 / CLI 边界）
uv run pytest -q tests/knowledge tests/generation tests/persistence \
  tests/prompt_engine tests/cli tests/tools
# 689 passed（exit 0）
#   分目录：knowledge 216 / generation 84 / persistence 205 / prompt_engine 130 / cli 37 / tools 17

# 最终一次全量
uv run pytest -q
# 2583 passed, 3 deselected（exit 0；3 项 smoke 默认排除）

# 语料提案校验（只读、离线）
uv run python tools/validate_knowledge_review_proposals.py
# RESULT: OK — 5 draft proposal(s) …（exit 0）

git diff --check
# 通过（exit 0）
```

- `pyproject.toml` 保持 `addopts = "-m 'not smoke'"`；全部测试使用 Fake 与临时数据库，
  不读取 `.env` / 真实凭据，不触网。
- 新增 3 项用例（1 生成闭环 + 2 知识守卫）：全量从 Step 03 的 `2580 passed` 增至
  `2583 passed`；不为数量重写既有断言。

### 4.2 生产语料（最终快照，非 demo 夹具）采用证据

| 路径 | 实际采用的发布单元（version 2，真实 approved） |
|---|---|
| `camera.depth_of_field` | `camera.depth_of_field.shallow_for_close_up`、`camera.depth_of_field.deep_for_wide_shot` |
| `composition.framing` | `composition.framing.medium_shot_for_standing_pose` |
| `lighting.character` | `lighting.character.soft_for_tight_framing`、`lighting.character.dramatic_for_angled_framing` |

- `tests/knowledge/test_knowledge_v0_5_release.py`（7 项）：发布快照可加载、5 条 approved、
  真实审核字段、`content_hash` 与提案一致、manifest 版本/文件哈希/条数、来源完整且
  `project_original`、v0.4 未改。**Step 04 新增守卫**：
  1. 发布单元相对**暂存获批提案**只允许 `review_status` / `reviewer` / `reviewed_at`
     三字段变化，并重算提案文件完整 SHA-256；提案文件缺失时**显式失败**（当前 5/5 存在）。
  2. `knowledge_base/v0.4/manifest.json` sha256 字节冻结
     `ec013105e452c4fd47733bb0ca7efafa562519c198dc5ef40eea1b615304b9e2`。
- `tests/generation/test_generation_v0_5_release_adoption.py`（16 项）：5 条规则各有可达正例
  与条件不符反例；PIN 边界；RAG vs 关闭 RAG 差异只在授权路径；active 复用 / 原 Prompt retry /
  快照切换 / 旧 Bundle 兼容。**Step 04 新增闭环用例**复用 `run_p1_to_confirmation`：
  `FakeLLMProvider → Interpreter/IntentEngine → 确认门禁 → LocalKnowledgeEngine(v0.5) →
  FakeImageProvider → 临时真实 SQLite`，断言 C1 `soft` 实际采用，且
  `knowledge_unit_id = lighting.character.soft_for_tight_framing`、Bundle 的
  `corpus_version`/`corpus_fingerprint`/`target_model`、采用裁定与 `eligibility.v1` 快照
  （`review_status=approved`、`reviewer=change`、`reviewed_at=2026-09-16T10:25:59Z`）
  均可从真实库读回；一次生成仅 1 次图片 Provider 请求。
- 数据库追溯：Bundle 落库于 `knowledge_bundles`；`RealizationValue` 记录
  `knowledge_bundle_id`/`knowledge_unit_id`/`knowledge_unit_version` 三元组；
  `PromptArtifact.knowledge_bundle_refs` 指回 Bundle。

### 4.3 数据库兼容

- 普通读写（Session / Intent / 确认 / Realization / Prompt / Generation / 反馈）与知识审计
  关联不变：`tests/persistence` **205 passed**。
- 旧库迁移与旧 payload：`tests/persistence/test_persistence_migration_v1_to_v2.py` 通过；
  旧 Bundle（缺 `adoption_decisions` / `eligibility_snapshot`）仍可反序列化，但消费端明确
  拒绝 `missing_eligibility_snapshot`，绝不静默采用（v0.5 采用集成第 5 组用例）。
- `knowledge_base/v0.4/` 字节冻结（manifest sha256 断言）；用户数据库与历史 Artifact 未改写。

## 5. Step 04 验收矩阵逐项落实

| 任务书 04 范围 | 落实与证据 |
|---|---|
| 真实内容 | `knowledge_base/v0.5/` 5 条真实 approved 被实际采用（§4.2 表）；发布守卫 + 闭环用例，非演示夹具 |
| 用户控制 | `tests/prompt_engine/test_prompt_engine_knowledge.py`：明确值、PIN、未确认、非委托字段不被知识覆盖；v0.5 采用集成 PIN 边界用例 |
| 适用性 | `tests/knowledge`（条件缺失/不符、模型不匹配、draft/rejected、缺快照、内容哈希不符均不采用）；v0.5 反例与旧快照拒绝用例 |
| 回退与错误 | 无可用知识/无命中/并列冲突按现行合同回退；`tests/knowledge/test_knowledge_loader.py`、`test_knowledge_security.py`、`tests/cli/test_cli_rag.py` 覆盖非法目录/损坏语料的既有 CLI 错误合同，不伪报启用 |
| 数据库 | §4.3：普通读写 + 知识审计关联 + v1→v2 迁移 + 旧 payload 可读（`tests/persistence` 205 passed） |
| 失败与重试 | `tests/generation/test_generation_persistence_failure.py`（Bundle 写库失败受控失败、图片调用为零）、`test_generation_knowledge_retry.py`（retry 复用原 Prompt、零重新检索）、`tests/cli/test_cli_persistence_failure.py` |
| 关闭 RAG | `tests/generation/test_generation_v0_5_release_adoption.py` 基线差异用例 + `tests/cli/test_cli_rag.py`：不加载语料、不检索、不建 Bundle、业务与基线一致 |
| 可复现性 | 检索确定性用例（`tests/knowledge/test_knowledge_retrieval.py`）+ 快照切换不改写历史/active Realization 用例 |
| 数据隔离 | `tests/tools/test_prepare_v0_5_resources.py`（分组不跨池、保留池隔离）；发布守卫校验来源许可/固定版本/派生标注；保留池未用于知识编写或调参 |

## 6. 变更文件与哈希（Step 04）

| 文件 | 状态 | SHA-256（工作区） |
|---|---|---|
| `tests/knowledge/test_knowledge_v0_5_release.py` | 新增守卫断言（未跟踪新文件） | `5e95fd98c56ff15cc3a77b267d854ed843e884eb0c12b2bdec55667007fc2c4a` |
| `tests/generation/test_generation_v0_5_release_adoption.py` | 新增闭环用例（未跟踪新文件） | `d4efed582f55fc772c4f0c8dd3e06d6eae8131e275201ba8f1f4d3a19dc400bb` |
| `docs/handoffs/v0_5_knowledge_review.md` | 修正过期措辞（未跟踪） | `6f3f8a07b3f30e161cab8ca36c6ab842e7b2179165a0952161aae595eb996d56` |
| `docs/handoffs/v0_5_step_03_handoff.md` | §6.1/§6.2 哈希刷新与说明（未跟踪） | 本批同步刷新，值见该文件 §6.1/§6.2 |
| `docs/mvp_v0.5/README.md` | Step 04 完成、F3 状态、数字（未跟踪） | `54780ee76c0dda706af8efad3cf2a19ac89711ece2ad53b104de06862b5bd28c` |
| `docs/mvp_v0.5/IMPLEMENTATION_REPORT.md` | 汇总至 Step 00–04（未跟踪） | `a5d8f93e7f715e44b6c3455fdd6ba850cea3c6746c833b26d9d834ac538d80a1` |
| `docs/ARCHITECTURE.md` | 当前状态 + Rev.7（已跟踪，修改） | `13ef6b7493a1046c7743d8b8f817e2afab5542f14d3ad65aa8daa8b9e2cdf25d` |
| `docs/task_books/README.md` | 任务索引状态（已跟踪，修改） | `a1974bdf8798c4b5ad60c1f33c5daa1e9f96fa9b3b7ac6c84b987286a1986543` |
| `docs/task_books/mvp_v0.5/README.md` | 任务书状态与 F3（未跟踪） | `e594707fbab72e130005a5c8df497c843614658f515682eee0fbc3a464700ab0` |
| `docs/handoffs/v0_5_release_handoff.md` | 本文件 | — |

## 7. 限制

1. **词法检索边界**：知识引擎为本地 JSONL + 内存 `keyword.v1` / `lexical.v1` 词法检索，
   非语义/向量检索；命中与排序由关键词、别名与条件决定，存在漏检与并列回退。
2. **未核实模型别名**：`target_models = ["any"]`；`qwen-image-3.0` 与官方版本对应关系**未核实**，
   本版不据此声称任何具体模型能力。
3. **未支持语义**：仅三路径、候选白名单与 `equals`/`in` 条件；不引入 Embedding、向量库、
   在线爬虫、额外 LLM 或多模型；不得据词法命中推断因果。
4. **不可推断的质量收益**：本版不构成知识质量、RAG 收益或图片质量结论；工程测试通过
   ≠ 生成效果改善。
5. **许可与数据**：COCO-CN 归档精确许可条款暂不确认；本版生产知识全部 `project_original`，
   未借用 COCO 许可，也未下载图片。
6. **正式评测未执行**：本版不产生真实图片批次、不跑 A/B、盲评或 Gate。

## 8. 版本结论（逐项对应 README 完成标准）

| README 完成标准 | 结论 |
|---|---|
| 三路径各有至少一条真实审核通过、条件可达、许可明确的规则，并在 Fake Provider + 临时真实数据库中完成实际采用验证 | **满足**（camera 2 / framing 1 / lighting 2；§4.2） |
| 任何路径缺乏可证明合理的规则时应回退并报告部分完成，不为凑覆盖批准弱规则 | **满足**（A3/B1/C3 暂缓、B2 退回，未凑数） |
| 无真实审核决定时最多报告“准备/工程部分完成，内容审核待定” | 不适用：真实审核已发生 |
| 有真实知识的集成测试不得只用内置 demo approved 夹具；发布前必须验证最终语料快照 | **满足**（使用 `knowledge_base/v0.5/`，非 demo） |
| 旧数据库、历史 Artifact、原 Prompt retry 与关闭 RAG 行为保持兼容 | **满足**（§4.3；旧库/旧 Bundle/retry/关闭 RAG 回归通过） |
| 正式评测未执行 | **满足且为本版边界**（不得执行，不是阻塞） |
| **v0.5 整体** | **部分完成**：唯一不完整原因是 **Step 01 的 COCO-CN 归档许可暂不确认**（数据准备部分完成）；正式评测属本版边界，不是不完整原因或阻塞 |

## 9. v0.6 输入（不提前批准任何协议）

1. **候选语料快照**：`knowledge_base/v0.5/`（`v0.5-approved-1`，5 条，fingerprint
   `c961790c…`）；A3/B1/C3 若发布须新 v2 提案并重新审核；B2 须先出 v2。
2. **来源 / 划分清单**：`data/staging/rag_resources_20260916/derived/` 的
   `source_samples.jsonl`、`split_manifest.json`（开发 49 / 保留 21，67 组），以及
   许可归档与字段报告；COCO-CN 许可仍待确认。
3. **工程基线**：HEAD `b039b9b` + dirty 工作区；全量 `2583 passed, 3 deselected`；
   发布守卫与知识采用集成已就位，可作评测前回归。
4. **待制定评测协议**：预算、模型 / Provider 版本、指标、停止条件须由用户在 v0.6 另行确定；
   本版不预留或预批准任何评测参数。正式评测须在独立授权后启动。

## 10. 边界与未越界声明

- 未改领域合同、运行时 Schema、默认路径、Provider、`.env`、`pyproject.toml`；未新增数据库/迁移。
- 未把未获批单元改为 `approved`，未填造 `reviewer`/`reviewed_at`；B2 未发布旧 v1。
- 未调用真实 Provider、未生成真实图片、未跑正式评测 / 盲评 / Gate；未下载图片。
- 未 commit / push / stash；未回滚、未覆盖既有 dirty 成果；`data/` 依 `.gitignore` 不入 Git。
- 唯一的版本不完整原因是 Step 01 的 COCO-CN 归档许可暂不确认；不得据此标记 v0.5 完整完成。

---

## 11. 补充说明（2026-09-16，v0.5 后置收尾 F1/F2；本节为追加，不改写上文）

- 上文第 0～10 节的命令、测试数字与结论均为 **Step 04 当时的实测结果**，保持原样，不因后续批次改写。
- 后续独立复查发现审核材料可移交性（F1）与派生样例/验收衔接（F2）两项缺口，已按
  [v0.5 后置收尾策略](../task_books/post_v0.5_fixes/README.md) 修复并通过离线验证：
  - F1 审核依据归档入口：[docs/knowledge_reviews/v0.5-approved-1/README.md](../knowledge_reviews/v0.5-approved-1/README.md)
    （5 份获批 v2 提案原字节 + `archive_manifest.json` 历史路径映射；发布守卫与
    `tools/validate_knowledge_review_proposals.py` 改读可交付归档，归档缺失/篡改/条件或候选变化仍被拒绝）。
  - F2 当前验收清单：[tests/fixtures/knowledge/v0_5_acceptance_cases.json](../../tests/fixtures/knowledge/v0_5_acceptance_cases.json)
    （3 路径 × 10 场景，绑定 `v0.5-approved-1`），实际执行 **30/30 pass**；Step 01 旧派生物为
    **审核前历史准备，不计当前已执行覆盖**。
- F3：COCO-CN 归档精确许可仍**暂不确认**；上文 §2、§7 的许可与数据限制不变，未引入原文、
  未改变裁定、未缩减验收范围。
- 正式评测仍未执行且属本版**范围边界**；**v0.5 整体继续标部分完成**。
- 本批最终交接（已完成）：[post_v0_5_fixes_001_handoff.md](post_v0_5_fixes_001_handoff.md)
  （补齐代码白名单后的无 `data/` 隔离副本全量通过；原工作区全量 `2665 passed, 3 deselected`）。
