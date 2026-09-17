# knowledge_base/v0.5 —— 独立发布快照（`corpus_version = v0.5-approved-1`）

- 日期：2026-09-16
- 性质：**已获真实人工审核批准的不可变发布快照**，只收录本轮获批的 5 条知识单元。
- 审核人：`change`；审核时间：`2026-09-16T10:25:59Z`；审核记录见
  [`docs/handoffs/v0_5_knowledge_review.md`](../../docs/handoffs/v0_5_knowledge_review.md)
  与随包 [`APPROVAL.md`](APPROVAL.md)。
- 本次发布**不修改** `knowledge_base/v0.4/`（仍 9 draft / 0 approved），也不修改用户数据库。

> 本文件是发布随附说明，不是质量结论、不是效果评测结论。被批准的是"可被用户覆盖的项目
> 默认偏好"，不是物理定律；本快照不声称 RAG 优于关闭 RAG，也不声称图片质量提升。

## 1. 收录内容（5 条，全部 `approved`）

| knowledge_id | version | applicable_path | candidate_value | content_hash | 获批提案文件 |
|---|:--:|---|---|---|---|
| `camera.depth_of_field.shallow_for_close_up` | 2 | `camera.depth_of_field` | `shallow` | `8124fdd97f82cded4e9dcfbb02e3a4c73c963aa48d19e7c2ba6db70b67d8a6e5` | `camera.depth_of_field.shallow_for_close_up.v2.json` |
| `camera.depth_of_field.deep_for_wide_shot` | 2 | `camera.depth_of_field` | `deep` | `b32de75822d16f0dc21786543f4422aa8eab24a14869eee07e44636eb9cd1db9` | `camera.depth_of_field.deep_for_wide_shot.v2.json` |
| `composition.framing.medium_shot_for_standing_pose` | 2 | `composition.framing` | `medium_shot` | `94afb6e376cddb41130f9fe2b74b38c3a3f9509a7bf58deaa88c87fdca138669` | `composition.framing.medium_shot_for_standing_pose.v2.json` |
| `lighting.character.soft_for_tight_framing` | 2 | `lighting.character` | `soft` | `fc81f7f5077813e0daf4c5e784c020b97e19b92e0a191c1ae3bae46d5e90fbf2` | `lighting.character.soft_for_tight_framing.v2.json` |
| `lighting.character.dramatic_for_angled_framing` | 2 | `lighting.character` | `dramatic` | `e367bd3046bb44909bfeae10cf5dbd7e6c90f2c07c61a2664fe0cdc75ba62697` | `lighting.character.dramatic_for_angled_framing.v2.json` |

文件布局复用 v0.4 的单路径 JSONL 布局：

| 文件 | sha256 | unit_count |
|---|---|---|
| `camera.depth_of_field.jsonl` | `9a32c511b0361e181a24f926fbc679dc23be28aef0303d9e95b83eebc5ee62d3` | 2 |
| `composition.framing.jsonl` | `a07af1ac94634362df14079a803c4aef50439e717f4a7ece49635b316b190609` | 1 |
| `lighting.character.jsonl` | `0b159a8fd7455ae9c3bdbc2e6499014f729fe88c5ca35db52253aae2d008b8c5` | 2 |
| `manifest.json` | `0bbeea9bbf7438bd8480e4cc56b1797368e2aa6b47c1601b0f276b44697b37d7` | — |

`manifest.json` 冻结版本：`schema_version = knowledge.v1`、`tokenizer_version = keyword.v1`、
`retrieval_version = lexical.v1`、`corpus_version = v0.5-approved-1`。同一语料内无重复
`knowledge_id`。

## 2. 审核决定与机械录入边界

本轮真实决定（详见 `APPROVAL.md` 与审核记录）：

- **批准**（本轮发布）：A1 / A2 / B3 / C1 / C2 共 5 条精确 v2 提案。
- **暂缓**（不发布）：A3、B1、C3。
- **退回生成 v2 后再审**（不发布，禁止发布旧 v1）：B2。

发布录入是**机械的**：相对获批提案文件，只把 `review_status` 改为 `approved`，并填写真实
`reviewer = "change"` 与 tz-aware UTC `reviewed_at = "2026-09-16T10:25:59Z"`。
`content`、`conditions`、`candidate_value`、`keywords`、`aliases`、`source`、`version`
逐字未改，因此每条 `content_hash = sha256(content)` 与获批提案一致。

获批提案文件位于工作区暂存目录（`data/` 依既有 `.gitignore` 不入 Git）：
`data/staging/rag_resources_20260916/knowledge_review/draft_proposals/`。其完整文件 SHA-256
见审核记录与 `APPROVAL.md`；本目录不复制提案正文，只用随附记录保留可追溯关系。

## 3. 许可、署名与来源形态

- 5 条单元 `source_type` 全部为 `project_original`，均携带显式 `original_declaration`：
  由 v0.4 draft 规则收窄/改写而来的项目原创启发式提案，未复制任何第三方文本。
- `license = "internal-project-original"`；`source_revision = "v0.5-draft-proposal-1"`；
  `source_date = null`（无日期纯原创形态，由本轮审核接受为原样发布的既有提案形态）。
- **未借用 COCO 许可**：本轮生产知识提案均为项目原创；COCO-CN 归档精确许可条款仍
  **暂不确认**，本快照不含任何 COCO-CN 文本、图片或标注。
- `target_models = ["any"]` 维持不变；`qwen-image-3.0` 别名与官方版本的对应关系**未核实**，
  本快照不据此声明任何具体模型能力。

## 4. 适用限制（不得省略）

- 这些规则是**可被用户明确值或 PIN 覆盖的项目默认偏好**，不是物理定律，也不是"必然效果"。
- 条件只读已确认 Intent 的精确值；缺字段即不满足；多条件为 AND；消歧沿用现有
  `lexical.v1` 词法打分与"最高分并列且候选不同 → `ambiguous` 回退"。
- 条件重叠（如 C1 与 C2 在同一上下文同时满足）仍由既有确定性词法打分决定，本轮未增删条件。
- B3 的 `studio` 未扩展为 `studio`/`indoor`；任何条件变更都必须作为新版本重新审核。
- 本轮未执行正式 Provider / 图片评测 / A-B / Gate 判定；采用状态与图片质量是分开的结论。

## 5. 如何在 CLI 中选择本快照

RAG 仍**默认关闭**，默认语料路径仍是 `knowledge_base/v0.4`（全 draft，透明回退），本快照
需显式选择：

```bash
uv run python -m visual_intent_agent --rag --knowledge-dir knowledge_base/v0.5
```

`--knowledge-dir` 只用于**明确选择语料目录**；`--demo --rag` 仍使用内置演示夹具且拒绝
`--knowledge-dir`。该命令不启动真实运行，也不构成效果验收。

## 6. 复建步骤（可复现）

1. 取获批的 5 份精确 v2 提案（`.../draft_proposals/<knowledge_id>.v2.json`），先核对
   文件 SHA-256 与审核记录一致；
2. 对每份提案机械录入：`review_status = "approved"`、`reviewer = "change"`、
   `reviewed_at = "2026-09-16T10:25:59Z"`，其余字段不动；
3. 用现有 `KnowledgeUnit` 合同逐条校验（`content_hash == sha256(content)`、审核字段自洽、
   候选受权、条件路径受权）；
4. 按 `applicable_path` 写入对应 JSONL（UTF-8，每行一个单元，`knowledge_id` 排序）；
5. 逐文件重算 sha256 与行数写入 `manifest.json`（版本字段见 §1），再用现有
   `load_corpus()` 整体加载核验 `build_units()`。

## 7. 不可变性

本目录是**快照**。任何内容、条件、候选、审核字段或来源的变更都必须产生**新的
`corpus_version` 与新目录/快照**，不得悄悄覆盖本快照，也不得改写 v0.4。
