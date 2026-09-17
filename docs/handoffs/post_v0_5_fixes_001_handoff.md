# v0.5 后置收尾批次交接（F1 / F2 / F3）

- 日期：2026-09-16
- 依据：[docs/task_books/post_v0.5_fixes/README.md](../task_books/post_v0.5_fixes/README.md)、
  [docs/task_books/post_v0.5_fixes/ACCEPTANCE.md](../task_books/post_v0.5_fixes/ACCEPTANCE.md)；
  上游：[v0.5 发布交接](v0_5_release_handoff.md)、[v0_5_knowledge_review.md](v0_5_knowledge_review.md)。
- 基线：HEAD `b039b9b7cd49becd0a25e251b37d930fee07da88`（`b039b9b`），分支 `feature`，
  工作区 **dirty 且未提交**（无 commit / push / stash）。
- 范围：审核依据归档（F1）、历史准备材料与当前验收清单对齐（F2）、许可待定项状态（F3）
  及两环境离线回归；**未改** `visual_intent_agent/**` 运行时、领域合同、Provider、
  `knowledge_base/v0.4|v0.5` 语料字节；**未调用真实 Provider / 未执行正式评测**。

## 0. 结论速览（必须分开陈述）

| 结论项 | 结果 |
|---|---|
| F1 审核依据随项目交付 | **完成并通过离线验证**：5 份获批 v2 提案原字节归档，发布守卫/校验工具改读可交付归档；缺失/篡改仍拒绝 |
| F2 历史准备与当前验收分开 | **完成并通过离线验证**：旧 Step 01 派生物为审核前历史准备，不计当前覆盖；当前 3 路径 × 10 场景 **30/30 pass** |
| 工程集中回归（两环境） | **通过**：原工作区全量 `2665 passed, 3 deselected`；初始严格副本白名单遗漏 `evaluation/`，全量收集失败但必测子集通过；补齐代码白名单的无 `data/` 增强副本全量通过 |
| F3 COCO-CN 许可待定 | **仍暂不确认**：保持隔离与用途待定，未引入原文、未缩减范围、未改变裁定 |
| 正式评测 | **未执行（本版范围边界，不是阻塞）** |
| **v0.5 整体** | **部分完成**（不宣称完整完成） |

## 1. F1：审核依据归档与发布守卫

归档入口（可交付，随项目交付）：[docs/knowledge_reviews/v0.5-approved-1/](../knowledge_reviews/v0.5-approved-1/README.md)
与机器可读清单 [`archive_manifest.json`](../knowledge_reviews/v0.5-approved-1/archive_manifest.json)。

### 1.1 五份获批提案归档哈希（原始字节，审核绑定值）

| # | knowledge_id | 归档相对路径 | SHA-256 |
|---|---|---|---|
| A1 | `camera.depth_of_field.shallow_for_close_up` | `proposals/camera.depth_of_field.shallow_for_close_up.v2.json` | `dd12f624ac4d39ab8c65faa23d4fd62293945285912792112fa811bd768dab54` |
| A2 | `camera.depth_of_field.deep_for_wide_shot` | `proposals/camera.depth_of_field.deep_for_wide_shot.v2.json` | `5de514666b6ab898bcb2af1ceb78d172395b58aac98918fe99a2ac5458580ad3` |
| B3 | `composition.framing.medium_shot_for_standing_pose` | `proposals/composition.framing.medium_shot_for_standing_pose.v2.json` | `dadec4245210947f3bf45b12292084e947a8ba7a4fa2ff24f0ed33fa70c73449` |
| C1 | `lighting.character.soft_for_tight_framing` | `proposals/lighting.character.soft_for_tight_framing.v2.json` | `1dae6d59421f7e90aec4305cf7c843831e704c930f80d47737e7af167b88d8dc` |
| C2 | `lighting.character.dramatic_for_angled_framing` | `proposals/lighting.character.dramatic_for_angled_framing.v2.json` | `4539e45ccc94e42e9d936336eac09e356bc472d11359ea2011444b4743fc605f` |

- 五份均保持 `review_status = "draft"`、`reviewer = null`、`reviewed_at = null`，未格式化/重序列化；
  发布快照相对归档提案**只**改 `review_status/reviewer/reviewed_at` 三字段。
- 期望哈希锚定既有审核记录与 `APPROVAL.md`，**不**单独信任归档清单；"同时改提案与本清单"不会让发布通过。
- 工具：`uv run python tools/validate_knowledge_review_proposals.py`（默认读归档）→
  `RESULT: OK — 5 draft proposal(s) parse, are draft/null/null, reachable, hash-consistent`。

### 1.2 v0.4 / v0.5 语料 8 个哈希（修复前后字节一致）

| 文件 | SHA-256 |
|---|---|
| `knowledge_base/v0.4/manifest.json` | `ec013105e452c4fd47733bb0ca7efafa562519c198dc5ef40eea1b615304b9e2` |
| `knowledge_base/v0.4/camera.depth_of_field.jsonl` | `8d1a0f64856ad4bd229d746a90f8eb102c22f45057a395a178e4fc82c5ffb002` |
| `knowledge_base/v0.4/composition.framing.jsonl` | `0f4d3025768d15e7362cfea369978ae3178afb15d25ecd87364ec7975f20efb8` |
| `knowledge_base/v0.4/lighting.character.jsonl` | `1924393f84417dfc27bf6e737d8d4882a05a9ef3b75c883cfaa4a555d342c8af` |
| `knowledge_base/v0.5/manifest.json` | `0bbeea9bbf7438bd8480e4cc56b1797368e2aa6b47c1601b0f276b44697b37d7` |
| `knowledge_base/v0.5/camera.depth_of_field.jsonl` | `9a32c511b0361e181a24f926fbc679dc23be28aef0303d9e95b83eebc5ee62d3` |
| `knowledge_base/v0.5/composition.framing.jsonl` | `a07af1ac94634362df14079a803c4aef50439e717f4a7ece49635b316b190609` |
| `knowledge_base/v0.5/lighting.character.jsonl` | `0b159a8fd7455ae9c3bdbc2e6499014f729fe88c5ca35db52253aae2d008b8c5` |

- v0.4 仍 9 draft / 0 approved（`build_units() == ()`），v0.5 仍 5 approved；两版 JSONL/manifest 字节未变，
  无需数据库迁移。破坏性篡改测试只在临时副本进行，未改真实归档。

## 2. F2：当前可执行验收清单（3 路径 × 10 场景）

- 清单：[`tests/fixtures/knowledge/v0_5_acceptance_cases.json`](../../tests/fixtures/knowledge/v0_5_acceptance_cases.json)，
  绑定 `corpus_version = v0.5-approved-1`、`knowledge_base/v0.5/manifest.json` sha256
  `0bbeea9bbf7438bd8480e4cc56b1797368e2aa6b47c1601b0f276b44697b37d7`；
  `dataset_derivation = none`（项目原创工程用例，未复制 COCO-CN/GenEval/T2I-CompBench 文本、ID 或划分）。
- 用例模块：`tests/generation/test_generation_v0_5_acceptance_matrix.py`；复用模块：
  `tests/generation/test_generation_v0_5_release_adoption.py`；运行工具：`tools/run_v0_5_acceptance_cases.py`。
- 逐 case 的节点 ID、准备条件和预期结果随上述 JSON 清单交付；本节矩阵记录本次实际结果。
  运行工具另在被忽略的 `outputs/acceptance/` 生成机器 JSON/Markdown（本次摘要：
  `executed 30/30, pass 30, not_pass 0, skipped 0, deselected 0, structure_ok true`）；
  这些可重建运行输出不是交付输入，不能替代清单与本交接。

### 2.1 三路径 × 十场景结果矩阵（全部 pass）

| 路径 \ 场景 | delegated | explicit_value | pinned | unconfirmed_condition | condition_mismatch | model_mismatch | unapproved_knowledge | conflict | missing_knowledge | successful_adoption |
|---|---|---|---|---|---|---|---|---|---|---|
| `camera.depth_of_field` | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| `composition.framing` | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| `lighting.character` | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass |

- `condition_mismatch` 与成功采用复用 `test_generation_v0_5_release_adoption.py` 的确切参数化节点，
  其余场景由矩阵模块执行；`missing_knowledge` / `model_mismatch` / `conflict` 等负例只隔离被测原因，
  不用 draft 过滤掩盖条件/模型/冲突分支。
- 旧 Step 01 派生物（`tools/prepare_v0_5_resources.py` 产物）已明确为**审核前历史准备**，
  **不计当前已执行覆盖**；当前清单才是本批验收输入。

## 3. 原工作区集中离线回归

```bash
# F1 定向（归档/发布守卫/校验工具）
uv run pytest -q tests/knowledge/test_knowledge_v0_5_release.py \
  tests/tools/test_validate_knowledge_review_proposals_tool.py
# 41 passed（exit 0）

# F2 定向（矩阵 + 采用 + runner 回归）
uv run pytest -q tests/generation/test_generation_v0_5_acceptance_matrix.py \
  tests/generation/test_generation_v0_5_release_adoption.py \
  tests/tools/test_run_v0_5_acceptance_cases.py
# 63 passed（exit 0）
uv run python tools/run_v0_5_acceptance_cases.py
# executed 30/30, pass=30, skipped=0, deselected=0（exit 0）

# 兼容相关（持久化/旧 payload/retry/关闭 RAG）
uv run pytest -q tests/persistence tests/prompt_engine/test_prompt_engine_knowledge.py \
  tests/generation/test_generation_knowledge_retry.py tests/cli/test_cli_rag.py
# 259 passed（exit 0）

# 集中全量
uv run pytest -q
# 2665 passed, 3 deselected（exit 0）
git diff --check
# 通过（exit 0）
```

- 记录实际退出码与数量，不复制旧测试数字；3 项 smoke 默认排除（`-m 'not smoke'`），未读 `.env`、未触网。
- 本批未改运行时与语料，新增仅为归档/清单/工具/测试/文档。

## 4. 不含本机暂存的隔离副本回归

### 4.1 严格副本（无 `data/`，导入来自副本）

- 路径：`/tmp/post_v0_5_isolated_20260916_192101`
- 复制方式：按显式白名单复制**当前未提交**可交付代码、配置、`tests`、`knowledge_base` 及所需
  `docs`/审核归档；排除 `.git`、`.env`、`api.md`、`data`、`outputs`、用户数据库与缓存。
  副本内**无 `data/`**；已确认测试导入来自副本而非原工作区。
- 结果：审核材料校验工具 **RESULT: OK**；定向回归 **77 passed, 1 skipped**；
  当前清单工具 **30/30**（`collected_nodes = 43`）；`tests/persistence` **205 passed**。
- 严格全量：`uv run pytest -q` → **exit 2，11 collection errors**：初始白名单漏拷顶层
  `evaluation/`，`tests/evaluation/*` 导入 `evaluation.*` 失败（`ModuleNotFoundError`）。
  该次命令不构成通过证据；它暴露了初始副本白名单不完整，随后以补入该项目代码包的增强副本重新执行全量。
- 加 `--ignore=tests/evaluation` 后严格全量：**2362 passed, 1 skipped, 3 deselected**（exit 0）。

### 4.2 增强副本 `_plus_eval`（仍无 `data/`）

- 路径：`/tmp/post_v0_5_isolated_20260916_192101_plus_eval`
- 在严格副本白名单基础上补入顶层 `evaluation/`；仍**无 `data/`**。
- 全量：`uv run pytest -q` → **2664 passed, 1 skipped, 3 deselected**（exit 0）。

## 5. F3 与版本状态

- **F3 COCO-CN 许可仍暂不确认**：保持隔离与用途待定，未将相关原文提交、发布、灌库或用于新验收集；
  未删除既有暂存，未缩减样本数或任务定义，未改变许可裁定。F1/F2 不依赖此决定，已先行完成。
- **正式评测未执行**，属本版**范围边界**（任务书明确本版不得执行），不是不完整原因或阻塞。
- **v0.5 整体为部分完成**：工程收尾 F1/F2 完成、审核依据可移交、当前 30 场景清单可交付；
  未决项为 COCO-CN 归档精确许可。不得据此标记 v0.5 完整完成。
- 历史报告（[v0_5_release_handoff.md](v0_5_release_handoff.md) 等）中的旧测试数字与当时结论**未改写**，
  本批只追加补充说明与链接。

## 6. 变更文件（本批，未提交）

| 文件 | 状态 |
|---|---|
| `docs/knowledge_reviews/v0.5-approved-1/README.md`、`archive_manifest.json`、`proposals/*.v2.json`（5） | 新增（未跟踪） |
| `tests/fixtures/knowledge/v0_5_acceptance_cases.json` | 新增（未跟踪） |
| `tests/generation/test_generation_v0_5_acceptance_matrix.py` | 新增（未跟踪） |
| `tools/run_v0_5_acceptance_cases.py`、`tools/validate_knowledge_review_proposals.py`、`tools/prepare_v0_5_resources.py` | 修改/新增（未跟踪/已改） |
| `tests/knowledge/test_knowledge_v0_5_release.py`、`tests/tools/*` | 修改/新增（未跟踪） |
| `docs/task_books/post_v0.5_fixes/README.md`、`docs/task_books/README.md`、`docs/mvp_v0.5/README.md`、`docs/handoffs/v0_5_release_handoff.md`、本文件 | 状态/索引/补充（未跟踪或已跟踪修改） |

- 保护对象：`knowledge_base/v0.4|v0.5`、`data/`、用户数据库、历史 Artifact / Bundle / Prompt 均未被改写。
- 未 commit / push / stash；`git diff --check` 通过。

## 7. 限制与未越界声明

1. 词法检索边界、`target_models = ["any"]` 未核实模型别名、仅三路径与 `equals`/`in` 条件等
   限制与 [v0_5_release_handoff.md](v0_5_release_handoff.md) §7 相同，本批未改变。
2. 本批不构成知识质量、RAG 收益或图片质量结论；工程测试通过 ≠ 生成效果改善。
3. 未引入 COCO-CN 原文；F3 许可仍待用户裁定。
4. 未执行正式评测 / 盲评 / Gate；未调用真实 Provider、未读取真实凭据。
