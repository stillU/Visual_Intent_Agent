# knowledge_base/v0.4 — 生产知识语料（当前全部为 draft）

- 语料版本：`v0.4-draft-1`
- Schema 版本：`knowledge.v1`
- 分词版本：`keyword.v1`（Step 02 实现）
- 检索版本：`lexical.v1`（Step 02 实现）
- 权威源：本目录 JSONL；`manifest.json` 记录每个文件的 sha256 与单元数。
- 加载接口：`visual_intent_agent.knowledge.load_corpus("knowledge_base/v0.4")`。

## 审核状态（截至 2026-09-16）

| 项目 | 实际状态 |
|---|---|
| 单元总数 | 9 |
| `approved` | **0** |
| `draft` | 9 |
| `rejected` | 0 |
| 人工 reviewer | 无（`reviewer` 与 `reviewed_at` 一律为 `null`） |
| 可进入构建消费（`build_units()`） | 0 条 |

**生产知识库尚未通过人工审核。** agent 不得填写 `reviewer`/`reviewed_at`；
`visual_intent_agent.knowledge` 的构建消费 `build_units()` 只返回 `approved`，
因此当前 RAG 实际可用知识为 0 条。测试夹具中的 `approved` 只是工程测试数据，
不能作为生产审核证据。

## 来源说明（重要）

本批 9 条全部为 `project_original` 项目原创领域规则，来源字段带
`original_declaration`、许可标记、来源 revision（`v0.4-draft-1`）与仓库相对路径
`knowledge_base/v0.4/<file>.jsonl`。

本批**未**引用任何模型官方能力断言：任务书已裁定本项目请求的 `qwen-image-3.0`
别名与 Qwen-Image 官方仓库版本对应关系未核实，因此在核对实际 Provider 文档前，
不得把官方仓库其他版本的能力、参数或提示词当作该别名的保证。所有单元的
`target_models` 都是显式通用标识 `any`，`source_date` 留空以避免臆造日期。

## 审核清单（人工审核时逐条确认）

对每条单元：

- [ ] 规则内容是否准确、可独立理解，且不超过 800 字符；
- [ ] `applicable_path` 是否属于 `lighting.character` / `composition.framing` /
      `camera.depth_of_field`；
- [ ] `candidate_value` 是否属于 `prompt_engine.engine.DELEGATED_CANDIDATES`
      对应路径的候选集合；
- [ ] `conditions` 的路径是否为已确认 Intent 字段、是否只用精确值/枚举集合、
      是否可被已确认场景满足；
- [ ] `keywords` / `aliases` 是否来自领域词汇而非测试答案或评测样本；
- [ ] `source` 是否完整（类型/标题/入口/定位/revision 或日期/许可或原创声明），
      官方材料是否已核对实际 Provider 文档；
- [ ] 是否与同路径其他单元冲突；冲突时先解决再批准；
- [ ] 批准时填写真实 `reviewer` 与 tz-aware UTC 的 `reviewed_at`，把
      `review_status` 改为 `approved`。

审核后必须重算哈希（否则 `load_corpus` 会拒绝）：

1. 修改单元正文时，同步更新该单元的 `content_hash = sha256(content)`（UTF-8）；
2. 重新计算该 JSONL 文件的 sha256 与单元数，更新 `manifest.json` 的
   `files[].sha256` 与 `files[].unit_count`；
3. 需要保留旧内容时，使用新的 `knowledge_id` 或新 `version` 并归档旧文件，
   不得在同一语料内出现重复 `knowledge_id`。

## 本目录布局

```text
knowledge_base/v0.4/
├── manifest.json                     # 语料版本 + 每文件 sha256 + 单元数
├── lighting.character.jsonl          # 3 条 draft
├── composition.framing.jsonl         # 3 条 draft
├── camera.depth_of_field.jsonl       # 3 条 draft
└── README.md                         # 本文件（非 JSONL，不受清单哈希约束）
```

加载器拒绝清单之外的多余 `.jsonl` 文件；新增语料文件必须先在 `manifest.json`
登记并写入哈希。