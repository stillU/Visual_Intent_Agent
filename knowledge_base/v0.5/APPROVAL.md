# v0.5 语料发布批准记录（随包）

- 语料版本：`v0.5-approved-1`（目录 `knowledge_base/v0.5/`）
- 审核人（真实身份）：`change`
- 审核时间（tz-aware UTC）：`2026-09-16T10:25:59Z`
- 审核对象：Step 02 交付的 9 条处置建议与 5 份精确 v2 draft 提案
  （`v0.4-draft-1` 旧版 + `v0.5-draft-proposal-1` 提案批）
- 权威审核记录：[`docs/handoffs/v0_5_knowledge_review.md`](../../docs/handoffs/v0_5_knowledge_review.md)

本文件是随语料发布的**决定摘要**，用于在语料目录内保留可追溯关系；不替代任务书、审核记录
或交接文档，不改动任何合同校验。

## 1. 逐条决定

| # | knowledge_id（v2） | 提案文件 SHA-256 | 决定 | 本轮是否发布 |
|---|---|---|---|:--:|
| A1 | `camera.depth_of_field.shallow_for_close_up` | `dd12f624ac4d39ab8c65faa23d4fd62293945285912792112fa811bd768dab54` | **approved** | 是 |
| A2 | `camera.depth_of_field.deep_for_wide_shot` | `5de514666b6ab898bcb2af1ceb78d172395b58aac98918fe99a2ac5458580ad3` | **approved** | 是 |
| B3 | `composition.framing.medium_shot_for_standing_pose` | `dadec4245210947f3bf45b12292084e947a8ba7a4fa2ff24f0ed33fa70c73449` | **approved** | 是 |
| C1 | `lighting.character.soft_for_tight_framing` | `1dae6d59421f7e90aec4305cf7c843831e704c930f80d47737e7af167b88d8dc` | **approved** | 是 |
| C2 | `lighting.character.dramatic_for_angled_framing` | `4539e45ccc94e42e9d936336eac09e356bc472d11359ea2011444b4743fc605f` | **approved** | 是 |
| A3 | `camera.depth_of_field.shallow_for_seated_subject` | —（未生成提案） | **暂缓** | 否 |
| B1 | `composition.framing.close_up_for_single_subject` | —（未生成提案） | **暂缓** | 否 |
| B2 | `composition.framing.wide_shot_for_outdoor_context` | —（未生成提案） | **退回生成 v2 后再审** | 否（禁止发布旧 v1） |
| C3 | `lighting.character.natural_for_outdoor` | —（未生成提案） | **暂缓** | 否 |

说明：A3 / B1 / C3 用户确认"暂缓"；B2 用户最终明确为"退回生成 v2 后再审"，本轮不发布，
且不得以旧 v1 形式发布。

## 2. 机械录入声明

发布快照相对获批提案**只**改动了三个审核字段：
`review_status → "approved"`、`reviewer → "change"`、`reviewed_at → "2026-09-16T10:25:59Z"`。
正文、条件、候选值、关键词、别名、来源与版本逐字未改；`content_hash` 因此与获批提案一致。
本批未新增路径、候选、算子、字段或模型适用性声明，也未借任何 COCO 许可（全部
`project_original`）。

## 3. 未在本轮处理的前置问题（保持原样）

- 来源形态：接受既有 `project_original` + `source_revision`、`source_date = null` 形态，原样发布。
- 模型适用性：维持 `target_models = ["any"]`；未核实 `qwen-image-3.0` 官方版本映射。
- 条件重叠：沿用现有词法打分 + 并列回退，未增删条件。
- B3 的 `studio`：未扩展为 `studio`/`indoor`（扩展属条件变更，须重新审核）。
- 发布方式：新建 `knowledge_base/v0.5/` + `corpus_version = v0.5-approved-1`；v0.4 不动。

## 4. 生效边界

本记录只证明"这 5 条精确提案经真实审核人批准并机械录入为发布快照"，不构成知识质量、
RAG 收益或最终图片质量结论，也不构成 COCO-CN 许可结论（该许可仍暂不确认）。
