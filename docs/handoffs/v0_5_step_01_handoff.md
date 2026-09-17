# v0.5 Step 01 交接：开放文本准备（**部分完成**：样本与字段核验完成，许可剩余事项）

- 日期：2026-09-16
- 任务书：[docs/task_books/mvp_v0.5/01_dataset_preparation.md](../task_books/mvp_v0.5/01_dataset_preparation.md)
- 输入：[资源包](../task_books/rag_resources/README.md)、[数据说明](../task_books/rag_resources/DATASETS.md)、
  [固定来源清单](../task_books/rag_resources/sources.lock.json)
- 暂存根：`data/staging/rag_resources_20260916/`
- 版本实施状态入口：[docs/mvp_v0.5/README.md](../mvp_v0.5/README.md)
- 范围：只准备**文本**工程输入；未运行图片生成、官方评分器或正式评测；开放 prompt / 人工描述
  不等于本项目的 Intent/PIN/委托标注。

## 0. 结论速览

| 结论项 | 结果 |
|---|---|
| 7 个锁定文本文件 bytes / 非空行数 / sha256 | **全部通过**，与 `sources.lock.json` 逐一一致 |
| COCO-CN 归档 | **本地副本安全解包成功**：bytes `15443026`、sha256 `6c126cd8…`、21 成员、解包总 `76773783` B、跳过 11 个目录/AppleDouble/脚本成员 |
| 最终样本 | **70 条达成**：GenEval 20 / CompBench 30 / COCO-CN 20 |
| 开发 / 保留分组 | **49 / 21**，共 **67 组**，**组不跨池** |
| 离线测试 | `uv run pytest -q tests/tools` → **17 passed** |
| 许可归档 | 4 份（3 个来源）已归档；**COCO 归档自身精确条款仍未最终确认** → 剩余事项 |
| **本步结论** | **部分完成**：样本目标与字段映射核验达成；许可剩余事项未清 |

## 1. 本步做了什么

1. 依 `sources.lock.json` 把 7 个锁定文本与许可放入独立暂存目录；已有且核验通过的文件
   **直接复用、不覆盖**。最终重建运行的 `download_record.json` 中 `reused=true`、
   `totals.downloaded_bytes=0`，表示该次复核运行未重复联网下载，不代表本阶段从未联网取得资源。
2. 对 7 个文本逐一验证原始字节 sha256、字节数与**非空行数**；校验失败即记缺口，绝不改期望哈希。
3. 用本地 COCO-CN 归档副本（`--coco-archive`）先做 tar 安全检查（成员数、解包总量、路径逃逸、
   链接逃逸），再**只提取文本/许可**、跳过 11 个目录/AppleDouble/脚本成员，并把本次新算的归档哈希写入下载记录
   （锁清单中该归档 `sha256`/`bytes` 为 `null`，故是**新记录**，不是历史回填）。
4. COCO-CN 人工描述取自归档内**精确文件** `imageid.human-written-caption.txt`；已人工核对其
   `<COCO image id>#<caption index>\t<human-written Chinese caption>` 格式，并在工具中只接受该文件，
   显式排除机翻、人工翻译、分词版和标签文件。最终记录标为 `verified_exact_archive_file`。
5. 按固定排序 `sha256("20260916|<dataset_id>|<original_id>")` 升序，先按相同原文 / 同图 ID /
   父样本分组建组去重，再分层抽样、按组约 70/30 划分，**组不跨开发 / 保留池**。
6. 产出可重建的派生清单；`data/` 已被既有 `.gitignore` 整目录忽略，**大归档与派生样本不入 Git**。

## 2. 来源核验（实测值）

### 2.1 已 `byte_verified` 的锁定文本（本次全部通过）

| 来源 | revision | 文件 | bytes | 非空行 | sha256 | 状态 |
|---|---|---|---|---:|---|---|
| `geneval` | `af4902f2…` | `prompts/evaluation_metadata.jsonl` | 85209 | 553 | `5c48e081…` | verified（复用） |
| `t2i_compbench_original` | `4aa40421…` | `examples/dataset/color_val.txt` | 11871 | 300 | `1634259756…` | verified（复用） |
| `t2i_compbench_original` | `4aa40421…` | `examples/dataset/shape_val.txt` | 15935 | 300 | `37e1a276…` | verified（复用） |
| `t2i_compbench_original` | `4aa40421…` | `examples/dataset/texture_val.txt` | 14211 | 300 | `fbb53635…` | verified（复用） |
| `t2i_compbench_original` | `4aa40421…` | `examples/dataset/spatial_val.txt` | 9213 | 300 | `8707a1d7…` | verified（复用） |
| `t2i_compbench_original` | `4aa40421…` | `examples/dataset/non_spatial_val.txt` | 17750 | 300 | `04c56cc9…` | verified（复用） |
| `t2i_compbench_original` | `4aa40421…` | `examples/dataset/complex_val.txt` | 22724 | 300 | `1b808018…` | verified（复用） |

完整 64 位哈希、下载 URL 与复用标记见
`data/staging/rag_resources_20260916/derived/download_record.json`。

### 2.2 COCO-CN 归档（本地安全解包）

| 项 | 实测值 |
|---|---|
| 仓库 revision | `2d8107987074b464a427c07433279de847947df1` |
| 托管 revision | `d40a7aac6fdfa88f5e30c0cd89516feaa8014739` |
| 归档 | `coco-cn-version1805v1.1.tar.gz` |
| bytes | `15443026` |
| sha256 | `6c126cd8455363a404806e452ec75066a8fc96d73922d9357d993fcdd1d40b8a` |
| 成员数 | `21` |
| 跳过目录 / AppleDouble / 脚本 | `11` |
| 解包总字节 | `76773783` |
| 状态 | `safely_extracted`；字段映射 `verified_exact_archive_file` |
| 字段格式 | `<COCO image id>#<caption index>\t<human-written Chinese caption>`（人工核对） |

- 人工描述使用的精确文件：`imageid.human-written-caption.txt`（归档内，解析为 TSV）；机翻、人工翻译、分词版和标签文件均明确排除。
- **未下载任何图片**；归档内图片类成员未被提取。
- 归档 `sha256` 为本次新算并记录；不得把它说成锁清单中“历史已下载”。

### 2.3 许可归档

| 来源 | 许可 / 声明 | 归档文件 | bytes | sha256 |
|---|---|---|---:|---|
| `geneval` | MIT | `licenses/geneval/LICENSE` | 1069 | `e6082bfc…` |
| `t2i_compbench_original` | MIT（**捆绑模型另有条款**） | `licenses/t2i_compbench_original/License.txt` | 1079 | `905b2fb8…` |
| `t2i_compbench_original` | NOTICE | `licenses/t2i_compbench_original/NOTICE.md` | 840 | `c3c88515…` |
| `coco_cn` | 仓库/数据卡标注 MIT（**归档自身条款待确认；不含图片**） | `licenses/coco_cn/LICENSE` | 1057 | `83e4dd21…` |

## 3. 样本与分组（实测值）

| 数据集 | 样本 | 开发 | 保留 | 组数 | 子类配额 |
|---|---:|---:|---:|---:|---|
| GenEval | 20 | 14 | 6 | 20 | single_object 10、counting 10 |
| T2I-CompBench | 30 | 21 | 9 | 30 | color/shape/texture/spatial/non_spatial/complex 各 5 |
| COCO-CN | 20 | 14 | 6 | 17 | 人工中文描述 |
| **合计** | **70** | **49** | **21** | **67** | — |

- 派生记录：`derived/source_samples.jsonl`（70 行）；分组 / 划分：`derived/split_manifest.json`。
- 每条派生记录含：`dataset_id`、`revision`、`source_file`、`original_id`、`group_id`、`image_id`、
  `language`、`original_text`、`supported_scope`、`unsupported_constraints`、`split`、`license`、
  `change_note`、`source_note`。这是**暂存记录，不是领域 Schema**。
- 组规则：相同规范化原文 / 同图 ID / 父样本合并为一组；整组去重、整组抽样、整组落池，
  **组不跨开发 / 保留池**（`split_manifest.grouping_rule`）。
- **保留池本版只校验格式与来源，不用于知识选择、关键词修改或回归断言开发。**

## 4. 工具与测试

| 文件 | 说明 |
|---|---|
| `tools/prepare_v0_5_resources.py` | 唯一的纯标准库准备工具：受限下载与锁定校验、tar 安全提取、COCO-CN 精确字段报告、稳定抽样/分组、30 条项目派生样例与暂存 README |
| `tests/tools/test_prepare_v0_5_resources.py` | 17 项完全离线测试：哈希失败、稳定抽样、组不泄漏、tar 安全、精确人工描述 TSV、AppleDouble/脚本跳过、项目派生标注与端到端重建 |

```bash
uv run pytest -q tests/tools
# 17 passed
```

## 5. 重建方法（离线可复现）

```bash
# 已核验的锁定文本/许可会直接复用，不覆盖；缺失时才从清单 URL 联网获取（受超时与字节上限约束）
uv run python tools/prepare_v0_5_resources.py \
  --lock docs/task_books/rag_resources/sources.lock.json \
  --staging data/staging/rag_resources_20260916 \
  --coco-archive data/staging/rag_resources_20260916/raw/coco_cn/coco-cn-version1805v1.1.tar.gz \
  --timeout 30 --max-file-bytes 26214400 --max-total-bytes 104857600 --offline
# 产出：download_record/source_samples/split_manifest、coco_cn_field_report、
#       project_derived_samples 与暂存 README
```

- 工具**不会自动联网下载 COCO-CN**（锁清单中该归档 `sha256`/`bytes` 为 `null`）；必须用
  `--coco-archive` 指向本地归档。
- 固定 `--stamp 20260916` 保证排序与分组可复现。

## 6. 许可与剩余事项（**本步标“部分完成”的原因**）

1. **字段映射已核验**：COCO-CN 人工描述 TSV 的精确文件、两列格式与同图分组键已经人工核对并由离线测试覆盖；最终下载记录不再把它列为缺口。
2. **归档自身精确条款待确认**：仓库 / 数据卡标注 MIT，但归档内部未附独立许可文本，是否存在附加条款尚待真实审核人确认；**文本许可不自动覆盖 COCO 原始照片**，且本步未下载或提取照片。
3. 因此本步**不宣称许可已完全落实**；许可事项清零前，Step 01 保持“部分完成”。

## 7. 边界与未越界声明

- 未改 `knowledge_base/v0.4/`、未新建 `knowledge_base/v0.5/`、未改任何运行时代码 / 领域合同。
- 未新增向量库、Embedding、在线爬虫、额外 LLM 或图片下载。
- 未调用真实 Provider、未生成图片、未跑正式评测 / 盲评 / Gate。
- 未改 `sources.lock.json` 的期望哈希；校验失败按缺口处理。
- 未 commit / push / stash；`data/` 依既有 `.gitignore` 不入 Git。

## 8. 下一步输入

- **Step 02**：证据卡与 draft 提案已由审核前材料环节产出
  （[v0_5_knowledge_review.md](v0_5_knowledge_review.md)、[v0_5_step_02_handoff.md](v0_5_step_02_handoff.md)），
  **真实人工审核关口仍未通过**。
- **Step 03**：须先取得真实批准与许可落实，方可新建 `knowledge_base/v0.5/` 并做真实知识离线采用验证。
- 保留池不得用于挑知识、调关键词或选择规则。

## 9. 本步是否满足验收

| 任务书 01 验收项 | 落实 |
|---|---|
| 暂存原始文件、许可、下载记录、样本/分组清单 | 是（`raw/`、`licenses/`、`derived/`） |
| 原始文件不清洗改写，派生物独立保存 | 是（`raw/` 只读复用；派生在 `derived/`） |
| 可重建的小型清单 / 工具与必要测试 | 是（一个纯标准库准备工具 + `tests/tools/`，17 passed） |
| 大归档与照片不入 Git | 是（`data/` 整目录忽略） |
| 交接列出成功/失败、哈希、样本数、许可剩余、分组与重建命令 | 是（本文件；**许可剩余事项已如实列出**） |
| 不声称正式测试集已冻结 | 是 |
| 来源暂缺不绕过限制 | 是（本批未遇阻断；COCO 用本地归档，未绕过访问控制） |

**版本结论：Step 01 部分完成 —— 样本目标（70 条）、分组（49/21，67 组）与 COCO-CN 精确人工描述字段映射均已核验，7 个锁定文本与许可可追溯；归档自身精确许可条款仍待真实审核人确认，故不宣布本步完全完成。**

## 10. COCO-CN 字段报告与项目派生工程样例

本节不替换上文实测值；7 文本核验、70 样本、49/21 分组均不变。

### 10.1 COCO-CN 归档实际字段（已核对，离线测试覆盖）

- 归档内**没有** JSON 描述文件。人工中文描述在 `imageid.human-written-caption.txt`，两列 TSV：
  `<COCO image id>#<caption index>\t<中文描述>`。
- 实测子集行数（`data/staging/rag_resources_20260916/derived/coco_cn_field_report.json`）：
  人工撰写 `22218`（**唯一入样**）、人工撰写分词 `22218`、人工翻译 `5000`、人工翻译分词 `5000`、
  机器翻译分词 `616763`、人工标签 `20341`（均登记但不入样，避免子集混算）。
- 20 条样本全部来自人工撰写子集，保留归档实际 `original_id` 与 `image_id`，并按 `image_id` 分组。
- 归档内**无独立 LICENSE/README**；许可只能沿用上游仓库 LICENSE 与托管数据卡（均 MIT）——即上文第 6 节
  的剩余事项。
- 归档内的 AppleDouble `._*` 元数据与 `verify_data.py` 脚本被跳过，未提取、未执行。

### 10.2 项目派生工程样例（任务书 01 第 5 条）

- `derived/project_derived_samples.jsonl`：**30 条 = 3 条知识路径 × 10 种覆盖状态**。
- 覆盖状态：delegated、explicit_value、pinned、unconfirmed_condition、condition_mismatch、
  model_mismatch、unapproved_knowledge、conflict、missing_knowledge、successful_adoption。
- 每条以**开发池样本**为父样本，`original_text` 与来源不变；确认 / PIN / 委托状态全部放在
  `project_added_annotations`，并标 `added_by="project"`、
  `benchmark_metadata_reused_as_user_expression=false`。
- `staging_only=true`、`runtime_schema_change=false`：暂存标注，不是新增运行时 Schema；
  未填写任何 `reviewer` / `reviewed_at`，`successful_adoption` 明确标为 **BLOCKED**（当前 9 draft / 0 approved），
  不伪造批准。断言未在 Step 01 执行，留待 Step 03/04 依冻结合同落地。

### 10.3 工具、测试与隔离

- 唯一准备工具 `tools/prepare_v0_5_resources.py` 同时生成来源清单、字段报告和项目派生样例；支持 `--offline`，并在端到端重建时保留既有 `knowledge_review/`。
- `tests/tools/test_prepare_v0_5_resources.py` 的 17 项离线测试覆盖 TSV 解析、AppleDouble/脚本跳过、tar 安全、分组不泄漏、70/30、派生样例标注与端到端隔离。
- 已核对 30 条派生样例引用的 `group_id` / `split` 与当前 manifest **0 不一致**。
- 暂存根 README：`data/staging/rag_resources_20260916/README.md`；Step 02 的 `knowledge_review/` 未被覆盖。
