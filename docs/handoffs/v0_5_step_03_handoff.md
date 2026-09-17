# v0.5 Step 03 交接：独立语料发布与真实采用验证

- 日期：2026-09-16
- 依据：`docs/task_books/mvp_v0.5/03_corpus_release.md`、`docs/task_books/mvp_v0.5/README.md`、
  `docs/handoffs/v0_5_knowledge_review.md`、`docs/handoffs/v0_5_step_02_handoff.md`。
- 基线：`HEAD b039b9b`（分支 `feature`），工作区 dirty（含 v0.4 Step 01–05 与后置修复增量），
  未 commit / push / stash。
- 范围：只新增 Step 03 发布语料、离线测试与文档；未改运行时代码、领域合同或 v0.4。

## 0. 结论速览（必须分开陈述）

| 结论项 | 结果 |
|---|---|
| 真实审核决定 | **已录入**：审核人 `change`，`2026-09-16T10:25:59Z`；批准 A1/A2/B3/C1/C2，A3/B1/C3 暂缓，B2 退回生成 v2 后再审 |
| 独立语料发布 | **完成**：`knowledge_base/v0.5/`，`corpus_version = v0.5-approved-1`，5 条 `approved`，仅收录获批单元 |
| 旧快照 / 用户数据库 | **未改**：`knowledge_base/v0.4/` 仍 9 draft / 0 approved，`build_units() == ()` |
| 三路径离线采用验证 | **通过**：5 条规则各有可达正例 + 条件不符反例；PIN 边界、RAG vs 关闭 RAG 差异、active 复用 / retry / 历史兼容均覆盖 |
| 正式评测 | **未执行（本版边界：任务书明确本版不得执行，不是阻塞）**：无真实 Provider、无图片批次、无 A/B、无盲评、无 Gate 判定 |
| Step 01 数据准备 | **仍为部分完成**：COCO-CN 归档精确许可条款**暂不确认**（用户决定）；本轮生产知识均 `project_original`，未借用 COCO 许可 |
| v0.5 整体 | **部分完成**：唯一不完整原因是 Step 01 的 COCO-CN 归档许可暂不确认（数据准备部分完成）；正式评测属本版边界（本版不得执行），不是不完整原因或阻塞 |

## 1. 本步做了什么

1. **更新审核记录** `docs/handoffs/v0_5_knowledge_review.md`：
   - 新增 §0「真实审核决定（已录入）」，逐份绑定 5 个精确 v2 提案文件 SHA-256 与决定；
   - 各条“审核决定”改为已录入状态（A1/A2/B3/C1/C2 approved；A3/B1/C3 暂缓；
     B2 退回生成 v2 后再审，禁止发布旧 v1）；
   - §5 签署区填入 `change` / `2026-09-16T10:25:59Z`，§6 状态更新到审核后；
   - 原“审核前”声明改为**历史记录**并显式标注“已被 §0 取代”，消除与已录入状态的矛盾。
2. **新建独立发布快照** `knowledge_base/v0.5/`：3 个 JSONL（camera 2 / framing 1 / lighting 2）、
   `manifest.json`、`README.md`、`APPROVAL.md`；`corpus_version = v0.5-approved-1`。
3. **机械录入**：相对获批提案只改 `review_status` / `reviewer` / `reviewed_at` 三个字段；
   正文、条件、候选、关键词、别名、来源、版本逐字未改，`content_hash` 因此与提案一致。
4. **新增离线测试**：发布快照守卫 + 三路径真实采用与追溯集成（Fake Provider + 临时真实 SQLite）。
5. **更新状态文档**：`docs/mvp_v0.5/README.md`、`docs/mvp_v0.5/IMPLEMENTATION_REPORT.md`；
   对根 `README.md` 做**最小**准确化编辑（新增 v0.5 快照说明与 `--knowledge-dir` 示例）。

**明确未做**：未改 `visual_intent_agent/**`、`tests/**` 既有文件语义、领域 Schema / Intent /
Policy / PIN / 确认门禁 / 候选白名单 / 条件算子；未改 `knowledge_base/v0.4/`；未调用真实
Provider；未生成正式评测图片；未 commit / push / stash。

## 2. 审核决定（机械录入，未扩大）

- 审核人：`change`；审核时间：`2026-09-16T10:25:59Z`（tz-aware UTC）。
- 批准并发布：A1 `camera.depth_of_field.shallow_for_close_up`、
  A2 `camera.depth_of_field.deep_for_wide_shot`、
  B3 `composition.framing.medium_shot_for_standing_pose`、
  C1 `lighting.character.soft_for_tight_framing`、
  C2 `lighting.character.dramatic_for_angled_framing`。
- 暂缓（不发布）：A3、B1、C3。
- 退回生成 v2 后再审（不发布，禁止发布旧 v1）：B2。
- 本轮未处理的前置问题按“不扩大”处理：`project_original` 无日期原创形态、`target_models = ["any"]`、
  沿用既有词法打分 + 并列回退、B3 的 `studio` 不扩展。任何后续变更须作为新版本重新审核。

提案文件 SHA-256（与审核记录逐份一致，已复核 5/5）：

| 提案 | SHA-256 |
|---|---|
| A1 | `dd12f624ac4d39ab8c65faa23d4fd62293945285912792112fa811bd768dab54` |
| A2 | `5de514666b6ab898bcb2af1ceb78d172395b58aac98918fe99a2ac5458580ad3` |
| B3 | `dadec4245210947f3bf45b12292084e947a8ba7a4fa2ff24f0ed33fa70c73449` |
| C1 | `1dae6d59421f7e90aec4305cf7c843831e704c930f80d47737e7af167b88d8dc` |
| C2 | `4539e45ccc94e42e9d936336eac09e356bc472d11359ea2011444b4743fc605f` |

## 3. 发布快照与哈希/条数

`knowledge_base/v0.5/`（`load_corpus()` 实测：`all_unit_count = 5`、`build_units() = 5`）：

| 文件 | sha256 | unit_count |
|---|---|---|
| `camera.depth_of_field.jsonl` | `9a32c511b0361e181a24f926fbc679dc23be28aef0303d9e95b83eebc5ee62d3` | 2 |
| `composition.framing.jsonl` | `a07af1ac94634362df14079a803c4aef50439e717f4a7ece49635b316b190609` | 1 |
| `lighting.character.jsonl` | `0b159a8fd7455ae9c3bdbc2e6499014f729fe88c5ca35db52253aae2d008b8c5` | 2 |
| `manifest.json` | `0bbeea9bbf7438bd8480e4cc56b1797368e2aa6b47c1601b0f276b44697b37d7` | — |

- manifest 版本：`schema_version = knowledge.v1`、`tokenizer_version = keyword.v1`、
  `retrieval_version = lexical.v1`、`corpus_version = v0.5-approved-1`。
- 同一语料内无重复 `knowledge_id`；清单之外无多余 `.jsonl`（加载器会拒绝未列文件）。
- 随包文档：`README.md`（版本说明/许可/适用限制/复建步骤）、`APPROVAL.md`（决定摘要 + 提案哈希）。

### v0.4 完整性（只读复核，未改）

```text
camera.depth_of_field.jsonl  sha_match=True rows=3/3 OK
composition.framing.jsonl    sha_match=True rows=3/3 OK
lighting.character.jsonl     sha_match=True rows=3/3 OK
manifest.json sha256 = ec013105e452c4fd47733bb0ca7efafa562519c198dc5ef40eea1b615304b9e2
V0.4 UNCHANGED: True
```

## 4. 离线集成测试（最终语料，非 demo 夹具）

测试只用最终发布目录 `knowledge_base/v0.5/` + 临时真实 SQLite Repository +
Fake Image Provider；不新增“验证捷径”，不触网、不调用 LLM。

- `tests/knowledge/test_knowledge_v0_5_release.py`（5 项）：快照可加载、5 条 approved、
  真实审核字段、content_hash 与提案一致、manifest 版本/文件哈希/条数、来源完整且
  `project_original`、v0.4 未改。
- `tests/generation/test_generation_v0_5_release_adoption.py`（15 项）：
  1. **正例**（5 条规则各一）：条件满足 → 检索命中 → 消费端 `adopted` → 实际 Realization /
     PromptArtifact 使用候选值 → Bundle 落库；可从数据库读回 Bundle、单元 ID/version/
     content_hash、`corpus_version`、`corpus_fingerprint`、适用性快照（含 reviewer/reviewed_at）。
  2. **反例**（5 条规则各一）：只改一条条件 → `conditions_not_satisfied` → 回退确定性基线，
     Realization 知识三元组全空、无推荐/无 adopted 裁定。
  3. **PIN 保护边界**：被 PIN 的委托路径根本不进入检索、不产生 Bundle、不被覆盖。
  4. **与关闭 RAG 基线差异**：同一 revision/确认上下文中，RAG 选值与基线不同，且差异只在被
     授权路径（其余 clause 完全一致）；只声明“建议能影响产物”，不声明更优。
  5. **active 复用 / retry / 快照切换 / 旧记录兼容**：active Realization 复用零新检索；
     retry 复用原 Prompt 与同一 Bundle；切换到 `build_units() == ()` 的 v0.4 后旧依据与历史
     Bundle 不被改写；旧 Bundle（缺 `adoption_decisions` / `eligibility_snapshot`）仍可读，
     但消费端明确拒绝 `missing_eligibility_snapshot`，绝不静默采用。

## 5. 实测结果

```bash
uv run pytest -q tests/knowledge/test_knowledge_v0_5_release.py
# 5 passed

uv run pytest -q tests/generation/test_generation_v0_5_release_adoption.py
# 15 passed

uv run pytest -q tests/knowledge tests/generation tests/persistence tests/prompt_engine tests/cli
# 669 passed

uv run pytest -q
# 2580 passed, 3 deselected

uv run python tools/validate_knowledge_review_proposals.py
# RESULT: OK — 5 draft proposal(s) parse, are draft/null/null, reachable, hash-consistent

uv run python -c "from visual_intent_agent.cli import build_knowledge_engine; \
  e,i = build_knowledge_engine(knowledge_dir='knowledge_base/v0.5', demo=False); print(i.corpus_version, i.approved_unit_count)"
# v0.5-approved-1 5

git diff --check  # 通过
```

- `--rag` 默认仍关闭；`resolve_knowledge_dir(None)` 仍为 `knowledge_base/v0.4`；
  `--demo --rag` 仍用内置演示夹具（`v0.4-demo-1`，3 条）并拒绝 `--knowledge-dir`。
- RAG 采用路径无额外 LLM / 网络调用：知识引擎是本地 JSONL + 内存词法检索，一次生成只有一次
  Fake Image Provider 请求。

## 6. 变更文件与哈希

### 6.1 新增（两个测试文件哈希为 Step 04 增补用例后的当前工作区值）

| 文件 | SHA-256 |
|---|---|
| `knowledge_base/v0.5/camera.depth_of_field.jsonl` | `9a32c511b0361e181a24f926fbc679dc23be28aef0303d9e95b83eebc5ee62d3` |
| `knowledge_base/v0.5/composition.framing.jsonl` | `a07af1ac94634362df14079a803c4aef50439e717f4a7ece49635b316b190609` |
| `knowledge_base/v0.5/lighting.character.jsonl` | `0b159a8fd7455ae9c3bdbc2e6499014f729fe88c5ca35db52253aae2d008b8c5` |
| `knowledge_base/v0.5/manifest.json` | `0bbeea9bbf7438bd8480e4cc56b1797368e2aa6b47c1601b0f276b44697b37d7` |
| `knowledge_base/v0.5/README.md` | `b79e84896cbcc16ca2a04121805b3d87b85653e10102ceb8ddfbb16a7247c1ca` |
| `knowledge_base/v0.5/APPROVAL.md` | `7a2fd6e4408827b924241407ecb9b8a2ada1dc6fea90e6911db5b2fc8a967af2` |
| `tests/knowledge/test_knowledge_v0_5_release.py` | `5e95fd98c56ff15cc3a77b267d854ed843e884eb0c12b2bdec55667007fc2c4a` |
| `tests/generation/test_generation_v0_5_release_adoption.py` | `d4efed582f55fc772c4f0c8dd3e06d6eae8131e275201ba8f1f4d3a19dc400bb` |
| `docs/handoffs/v0_5_step_03_handoff.md` | 本文件 |

### 6.2 修改（Step 03 范围内；哈希为 Step 04 文档修正后的当前工作区值）

> 说明：`v0_5_knowledge_review.md`、`docs/mvp_v0.5/README.md`、`docs/mvp_v0.5/IMPLEMENTATION_REPORT.md`
> 在 `b039b9b` 之上为**未跟踪新文件**，此处“修改”指 v0.5 批次内的更新；`README.md` 为已跟踪文件的修改。
> 下表已在 Step 04 修正 review 文档措辞并更新状态文档后重算；Step 04 自身变更见
> `v0_5_release_handoff.md` §6。

| 文件 | 修改内容 | SHA-256（当前工作区） |
|---|---|---|
| `docs/handoffs/v0_5_knowledge_review.md` | 录入真实审核决定、绑定提案哈希、签署区、历史声明去矛盾（Step 04 修正过期措辞） | `6f3f8a07b3f30e161cab8ca36c6ab842e7b2179165a0952161aae595eb996d56` |
| `docs/mvp_v0.5/README.md` | Step 03 状态更新（Step 04 更新至验收完成、修正 F3 状态） | `54780ee76c0dda706af8efad3cf2a19ac89711ece2ad53b104de06862b5bd28c` |
| `docs/mvp_v0.5/IMPLEMENTATION_REPORT.md` | Step 03 汇总更新（Step 04 汇总至 Step 00–04） | `a5d8f93e7f715e44b6c3455fdd6ba850cea3c6746c833b26d9d834ac538d80a1` |
| `README.md` | 最小准确化：v0.5 快照与 `--knowledge-dir` 示例 | `56a786d54348a8dc5be0386cac486ec9ac0a676994f97cce218b77881417e3ac` |

### 6.3 明确未变

- `knowledge_base/v0.4/**`：manifest sha256 仍 `ec013105…`，3 文件哈希/条数一致。
- `visual_intent_agent/**`、`tests/**` 既有文件、`evaluation/**`、`.env`、`pyproject.toml`。
- 未新增数据库 / 迁移；未改领域合同、确认门禁、PIN、候选白名单、条件算子。

## 7. 边界与未越界声明

- **机械录入**：未把未获批单元改为 approved；未填造审核字段；B2 未发布旧 v1。
- **未借用 COCO 许可**：本轮生产知识全部 `project_original`；COCO-CN 许可暂不确认，
  Step 01 保持部分完成，v0.5 整体为部分完成（唯一不完整原因，不得标完整完成）。
- **未执行正式评测**：无真实 Provider、无图片批次、无 A/B、无盲评、无 Gate 判定；
  采用状态与图片质量分开陈述。
- 未 commit / push / stash；未覆盖 dirty 旧成果；`git diff --check` 通过。

## 8. 本步是否满足验收（任务书 03）

| 验收项 | 落实 |
|---|---|
| 最终快照可加载、获批数准确、真实批准证据可追溯 | 是（§1/§2/§3 + `tests/knowledge` 5 项守卫） |
| 三路径正例及保护反例通过 | 是（§4；5 正例 + 5 反例 + PIN 边界） |
| 与关闭 RAG 基线差异只在授权范围、不宣称更优 | 是（§4 第 4 项） |
| active Realization 优先复用；retry 不重新检索 | 是（§4 第 5 项） |
| 快照切换不改写旧生成依据 / 历史 | 是（§4 第 5 项；旧 Bundle 兼容 + 拒绝缺快照） |
| RAG 无额外 LLM / 网络调用 | 是（本地语料 + 内存词法；仅 1 次 Fake Image 请求） |
| 普通数据库读写与历史兼容不变 | 是（全量 `2580 passed`；v0.4 / 旧 Bundle 兼容） |
| 默认 RAG 关闭与默认路径不变；用 `--knowledge-dir` 明确选语料 | 是（§5） |
| 未执行正式 Provider / 图片评测 | 是 |

## 9. 阻塞与下一步

- **无阻断 Step 03 的阻塞**；未发现需改变确认 / PIN / 领域合同的新缺陷。
- **Step 01 仍部分完成**：COCO-CN 归档精确许可条款经用户决定“暂不确认”；该事项不阻断
  Step 03 的 project_original 知识发布，但使 v0.5 整体为部分完成（唯一不完整原因）。
- **Step 04** 可进行工程回归与数据库兼容汇总；正式 Provider / 图片评测继续等待独立授权。
- 后续任何知识条件/来源/模型适用性变更（含 B2 生成 v2、B3 `studio` 扩展）都必须新建版本并重新审核。
