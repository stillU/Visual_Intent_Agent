# v0.5 审核依据归档（`v0.5-approved-1`）

- 归档日期：2026-09-16
- 性质：**随项目交付的审核依据原件副本**。归档的是 2026-09-16 真实人工审核所绑定的
  5 份精确 v2 draft 提案的**原始字节**，用于在暂存目录 `data/`（依 `.gitignore` 不入 Git）
  不可用时仍能核验发布快照。
- 审核入口（权威）：[`docs/handoffs/v0_5_knowledge_review.md`](../../handoffs/v0_5_knowledge_review.md) §0。
- 随包决定摘要：[`knowledge_base/v0.5/APPROVAL.md`](../../../knowledge_base/v0.5/APPROVAL.md)。
- 发布快照：[`knowledge_base/v0.5/`](../../../knowledge_base/v0.5/README.md)，`corpus_version = v0.5-approved-1`。
- 审核人：`change`；审核时间（tz-aware UTC）：`2026-09-16T10:25:59Z`。
- 归档清单（机器可读，含哈希与历史路径映射）：[`archive_manifest.json`](archive_manifest.json)。

> 本归档**不是**发布语料、不是批准记录本身、也不是质量结论。文件保持审核绑定的
> `review_status = "draft"`、`reviewer = null`、`reviewed_at = null`；不得改写为 `approved`，
> 不得格式化或重序列化，不得用发布 JSON 反造"相同字节"。审核决定仍以权威审核记录为准。

## 1. 历史暂存路径 → 归档路径映射

审核绑定的原件原先只存在于工作区暂存目录（`data/` 不入 Git），发布单元
`source.repository_path` 仍指向该历史位置。下表把历史位置明确映射到现存的可交付副本；
运行时加载器**不**据此猜路径，审计工具与发布守卫按归档目录显式读取。

| # | knowledge_id | 历史暂存路径（历史定位，可能缺失） | 归档相对路径（可交付副本） | 归档 SHA-256 | 对应发布单元 |
|---|---|---|---|---|---|
| A1 | `camera.depth_of_field.shallow_for_close_up` | `data/staging/rag_resources_20260916/knowledge_review/draft_proposals/camera.depth_of_field.shallow_for_close_up.v2.json` | `proposals/camera.depth_of_field.shallow_for_close_up.v2.json` | `dd12f624ac4d39ab8c65faa23d4fd62293945285912792112fa811bd768dab54` | `camera.depth_of_field.shallow_for_close_up` @ `knowledge_base/v0.5/camera.depth_of_field.jsonl` |
| A2 | `camera.depth_of_field.deep_for_wide_shot` | `data/staging/rag_resources_20260916/knowledge_review/draft_proposals/camera.depth_of_field.deep_for_wide_shot.v2.json` | `proposals/camera.depth_of_field.deep_for_wide_shot.v2.json` | `5de514666b6ab898bcb2af1ceb78d172395b58aac98918fe99a2ac5458580ad3` | `camera.depth_of_field.deep_for_wide_shot` @ `knowledge_base/v0.5/camera.depth_of_field.jsonl` |
| B3 | `composition.framing.medium_shot_for_standing_pose` | `data/staging/rag_resources_20260916/knowledge_review/draft_proposals/composition.framing.medium_shot_for_standing_pose.v2.json` | `proposals/composition.framing.medium_shot_for_standing_pose.v2.json` | `dadec4245210947f3bf45b12292084e947a8ba7a4fa2ff24f0ed33fa70c73449` | `composition.framing.medium_shot_for_standing_pose` @ `knowledge_base/v0.5/composition.framing.jsonl` |
| C1 | `lighting.character.soft_for_tight_framing` | `data/staging/rag_resources_20260916/knowledge_review/draft_proposals/lighting.character.soft_for_tight_framing.v2.json` | `proposals/lighting.character.soft_for_tight_framing.v2.json` | `1dae6d59421f7e90aec4305cf7c843831e704c930f80d47737e7af167b88d8dc` | `lighting.character.soft_for_tight_framing` @ `knowledge_base/v0.5/lighting.character.jsonl` |
| C2 | `lighting.character.dramatic_for_angled_framing` | `data/staging/rag_resources_20260916/knowledge_review/draft_proposals/lighting.character.dramatic_for_angled_framing.v2.json` | `proposals/lighting.character.dramatic_for_angled_framing.v2.json` | `4539e45ccc94e42e9d936336eac09e356bc472d11359ea2011444b4743fc605f` | `lighting.character.dramatic_for_angled_framing` @ `knowledge_base/v0.5/lighting.character.jsonl` |

历史路径的权威记录见审核记录 §0/§1/§2；本表只是位置映射，不改写任何审核决定，也不改变
发布快照中保留的 `source.repository_path` 历史值。

## 2. 归档与发布的关系（机械录入边界）

发布快照相对归档提案**只**改动三个审核字段：
`review_status → "approved"`、`reviewer → "change"`、`reviewed_at → "2026-09-16T10:25:59Z"`。
正文、条件、候选、关键词、别名、来源、版本逐字未改，`content_hash == sha256(content)` 与
归档提案一致。发布守卫
[`tests/knowledge/test_knowledge_v0_5_release.py`](../../../tests/knowledge/test_knowledge_v0_5_release.py)
按归档目录重新校验归档字节哈希，并断言发布单元与归档提案的差异恰为这三个字段。

**哈希锚定**：守卫与审计工具断言的期望哈希来自既有审核记录（审核记录本身不随本批改动），
不单独信任本归档清单，因此"同时改提案文件与本清单"不会让发布通过。

## 3. 复现与校验

```bash
# 发布守卫（含归档完整性与机械录入边界；不含 data/ 也能运行）
uv run pytest -q tests/knowledge/test_knowledge_v0_5_release.py

# 审核材料离线校验（默认校验本归档；明确 --dir 可校验待审草稿）
uv run python tools/validate_knowledge_review_proposals.py
uv run python tools/validate_knowledge_review_proposals.py --dir data/staging/rag_resources_20260916/knowledge_review/draft_proposals
```

## 4. 边界

- 归档文件**必须**保持原始字节；任何重新序列化、格式化或字段补写都视为证据损坏。
- 不得编辑本目录内容来迁就测试；发现归档缺失或哈希不符时，发布守卫显式失败，禁止 skip。
- 本归档不构成知识质量、RAG 收益或图片质量结论，也不构成 COCO-CN 许可结论
  （该许可仍**暂不确认**，见 [v0.5 后置收尾任务书](../../task_books/post_v0.5_fixes/README.md) F3）。
