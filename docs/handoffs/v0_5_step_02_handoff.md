# v0.5 Step 02 交接：审核前材料（真实审核未发生，Step 03 禁止开始）

- 日期：2026-09-16
- 依据：`docs/task_books/mvp_v0.5/README.md`、`02_knowledge_review.md`、
  `docs/task_books/rag_resources/KNOWLEDGE_SOURCES.md`、
  `docs/handoffs/post_v0_4_knowledge_review_checklist.md`、
  `docs/handoffs/architecture_decision_005.md`。
- 范围：**只做审核前材料准备**。不越过人工关口，不发布语料，不启动 Step 03/04。
- 基线：`HEAD b039b9b`（分支 `feature`），工作区 dirty（含 v0.4 Step01–05 与本批增量），未 commit/push。

## 0. 结论速览（五项分开报告）

| 结论项 | 结果 |
|---|---|
| 9 条旧规则是否均有处置建议 | **是**：收窄待审 3、保留待审 2、暂缓 3、建议移除 1 |
| 是否生成可解析的独立 draft 提案 | **是**：5 份完整 `KnowledgeUnit` JSON，`draft`/`null`/`null` |
| 离线校验是否通过 | **是**：`tools/validate_knowledge_review_proposals.py` → `RESULT: OK`（exit 0） |
| 生产知识是否已审核 | **否**：v0.4 仍 9 draft / 0 approved / `build_units() == ()` |
| Step 03 是否可开始 | **否，禁止开始**；须先取得真实审核决定 |

## 1. 本步做了什么

1. 读取 9 条 v0.4 draft 与知识合同（`KnowledgeUnit`、条件语义、候选白名单）。
2. 为每条旧规则建立证据卡：事实/偏好分类、外部证据边界、概念纠错、条件可达性、反例、
   冲突/重叠、处置理由、交审核人的未决问题。
3. 对建议“保留待审/收窄待审”的 5 条生成完整 draft 提案（v2），使用现有三路径与候选、
   现有 `equals`/`in` 算子与 AND 语义；正文改写为**可覆盖的项目默认偏好**。
4. 新增最小离线校验脚本，验证提案可解析、审核字段为空、候选/条件受权且可达、哈希一致。
5. 新增 `docs/handoffs/v0_5_knowledge_review.md`（待决定清单，审核决定列留空）。

**明确未做**：未修改 `knowledge_base/v0.4/`、未新建 `knowledge_base/v0.5/`、未改任何运行时代码
（`visual_intent_agent/**`、`tests/**`）、未填任何 `reviewer`/`reviewed_at`、未调用真实 Provider、
未提交/推送/删除历史。

## 2. 处置建议与提案

| # | ID（v1） | 路径 | 候选 | 处置建议 | 提案版本 |
|---|---|---|---|:--:|:--:|
| A1 | `camera.depth_of_field.shallow_for_close_up` | camera.depth_of_field | shallow | 收窄待审 | v2 |
| A2 | `camera.depth_of_field.deep_for_wide_shot` | camera.depth_of_field | deep | 收窄待审 | v2 |
| A3 | `camera.depth_of_field.shallow_for_seated_subject` | camera.depth_of_field | shallow | 暂缓 | — |
| B1 | `composition.framing.close_up_for_single_subject` | composition.framing | close_up | 暂缓 | — |
| B2 | `composition.framing.wide_shot_for_outdoor_context` | composition.framing | wide_shot | 建议移除 | — |
| B3 | `composition.framing.medium_shot_for_standing_pose` | composition.framing | medium_shot | 保留待审 | v2 |
| C1 | `lighting.character.soft_for_tight_framing` | lighting.character | soft | 保留待审 | v2 |
| C2 | `lighting.character.dramatic_for_angled_framing` | lighting.character | dramatic | 收窄待审 | v2 |
| C3 | `lighting.character.natural_for_outdoor` | lighting.character | natural | 暂缓 | — |

三路径均至少 1 份提案：camera 2、framing 1、lighting 2。**均未获批**。

收窄方式（仅用现有算子/字段）：
- A1：`style.primary` 从 `in (cinematic, photorealistic)` 收窄为 `equals cinematic`。
- A2：新增条件 `environment.mode equals outdoor`。
- C2：`camera.angle` 从 `in (low_angle, high_angle)` 收窄为 `equals low_angle`。
- B3 / C1：条件不变，仅改写正文（明确为可覆盖偏好、修正概念）。
- A3 / B1 / C3 暂缓、B2 建议移除：**不生成提案**，不以收窄方式凑数量。

## 3. 概念纠错（已写入提案正文与证据卡）

- **浅景深 ≠ 透视压缩**：浅景深只改变焦外虚化，不改变焦段、视角或空间比例（A1）。
- **`wide_shot` ≠ 广角焦距**：景别描述取景范围，不规定焦段/透视（A2）。
- **单主体不构成“必须近景”的必然**（B1，暂缓）。
- **户外不构成“必须远景 / 必须自然光”的必然**（B2 建议移除、C3 暂缓）。
- 姿态、机位角度、取景方式均**不决定**景深或光质；相关规则一律自认可被用户值/PIN 覆盖。

## 4. 变更文件与哈希

### 4.1 新增（本步唯一写入）

| 文件 | SHA-256 |
|---|---|
| `data/staging/rag_resources_20260916/knowledge_review/README.md` | `e6e0cc92e36784c9e36324f65748f1493c98163ccff235a450aed45b0d80760f` |
| `data/staging/rag_resources_20260916/knowledge_review/evidence_cards/camera.depth_of_field.shallow_for_close_up.md` | `21bd879ab89647f742d2fbb934fcc9328a6857a33cc999a271149d86dddd61f0` |
| `.../evidence_cards/camera.depth_of_field.deep_for_wide_shot.md` | `e22b12661ec942f3d9b9e35f5ad97b434446e0203a2ae4fa907827b4efd0dea2` |
| `.../evidence_cards/camera.depth_of_field.shallow_for_seated_subject.md` | `4be022eb406cda6984be2398ae13693cfa45530e578fb1675264782d6ee79032` |
| `.../evidence_cards/composition.framing.close_up_for_single_subject.md` | `09e44385dc206b1afc05fd7fb2112dcf4507c700758a78220de255e9c7a8ab18` |
| `.../evidence_cards/composition.framing.wide_shot_for_outdoor_context.md` | `0717543cfcd20674b51343331ffa4b56584e82d94d3b80773610573f40bc85a7` |
| `.../evidence_cards/composition.framing.medium_shot_for_standing_pose.md` | `58d84ca128e8916ea57b60e70c65fdf3fd0f1919824e404a7f05c05747cf919e` |
| `.../evidence_cards/lighting.character.soft_for_tight_framing.md` | `2cb523ac3a9fb4288d37a0d6c59ad4bd737789141dbbb7395b14f7dde3458ba0` |
| `.../evidence_cards/lighting.character.dramatic_for_angled_framing.md` | `d5aeea2155ca5fe8a1ba190205208d39d2a74d19cf18542031aad3eaf3d1e8f2` |
| `.../evidence_cards/lighting.character.natural_for_outdoor.md` | `441a6086b464437ac6475402ccfb00627abea456edb209353f41f15b46606e6b` |
| `.../draft_proposals/camera.depth_of_field.shallow_for_close_up.v2.json` | `dd12f624ac4d39ab8c65faa23d4fd62293945285912792112fa811bd768dab54` |
| `.../draft_proposals/camera.depth_of_field.deep_for_wide_shot.v2.json` | `5de514666b6ab898bcb2af1ceb78d172395b58aac98918fe99a2ac5458580ad3` |
| `.../draft_proposals/composition.framing.medium_shot_for_standing_pose.v2.json` | `dadec4245210947f3bf45b12292084e947a8ba7a4fa2ff24f0ed33fa70c73449` |
| `.../draft_proposals/lighting.character.soft_for_tight_framing.v2.json` | `1dae6d59421f7e90aec4305cf7c843831e704c930f80d47737e7af167b88d8dc` |
| `.../draft_proposals/lighting.character.dramatic_for_angled_framing.v2.json` | `4539e45ccc94e42e9d936336eac09e356bc472d11359ea2011444b4743fc605f` |
| `tools/validate_knowledge_review_proposals.py` | `ebe6b3eb165533255b7363a9a76b382c7b593f2c9b61022e4b49990d7a10b0d7` |
| `docs/handoffs/v0_5_knowledge_review.md` | `9651eb534900593983005837a35015e899fff5c465da21c1de1ace1a4b7f2e6e` |
| `docs/handoffs/v0_5_step_02_handoff.md` | 本文件 |

> 说明：`data/` 已被 `.gitignore` 整目录忽略（既有规则，未修改），因此
> `data/staging/rag_resources_20260916/knowledge_review/**` **不入 Git**，仅在当前工作区；
> 上述 SHA-256 即审核关口绑定的“完整提案文件”指纹。`tools/` 与 `docs/` 不在忽略范围。

### 4.2 明确未变

- `knowledge_base/v0.4/`：3 个 JSONL 的 sha256 与 `manifest.json` 逐一一致，
  `unit_count`/行数 3/3/3，`manifest.json` sha256 仍为
  `ec013105e452c4fd47733bb0ca7efafa562519c198dc5ef40eea1b615304b9e2`，**未修改**。
- `visual_intent_agent/**`、`tests/**`、`evaluation/**`、`pyproject.toml`、`.env`：**未修改**。

## 5. 离线校验命令与结果

```bash
uv run python tools/validate_knowledge_review_proposals.py
```

实测输出（节选）：

```text
validating 5 proposal(s) in .../knowledge_review/draft_proposals
camera.depth_of_field.deep_for_wide_shot                   2    draft  5de514666b6ab898…  OK
camera.depth_of_field.shallow_for_close_up                 2    draft  dd12f624ac4d39ab…  OK
composition.framing.medium_shot_for_standing_pose          2    draft  dadec4245210947f…  OK
lighting.character.dramatic_for_angled_framing             2    draft  4539e45ccc94e42e…  OK
lighting.character.soft_for_tight_framing                  2    draft  1dae6d59421f7e90…  OK
RESULT: OK — 5 draft proposal(s) parse, are draft/null/null, reachable, hash-consistent
# exit 0
```

脚本校验项：`KnowledgeUnit` 合同解析（`extra=forbid`、来源完整性、审核自洽）、
`review_status=draft` 且审核字段为 null、候选值受权、条件算子仅 `equals`/`in`、
条件路径白名单且取值在 `DELEGATED_CANDIDATES` 可确认词汇内（可达）、
`content_hash == sha256(content)`、提案间无重复 `knowledge_id`。**全程离线、零 Provider 调用。**

v0.4 语料完整性（只读核对）：

```text
camera.depth_of_field.jsonl      sha_match=True rows=3/3 OK
composition.framing.jsonl        sha_match=True rows=3/3 OK
lighting.character.jsonl         sha_match=True rows=3/3 OK
V0.4 UNCHANGED: True
```

## 6. 边界与未越界声明

- 未把任何 `draft` 改为 `approved`；未填写 `reviewer`/`reviewed_at`；未把测试/demo 夹具当生产证据。
- 提案 `review_status=draft`、`reviewer=null`、`reviewed_at=null`；来源 `project_original` 属实
  （未复制第三方文本，未把外部改写伪装成原创，也未把项目原创伪装成外部证据）。
- 未新建 `knowledge_base/v0.5/`；未改领域 Schema/PIN/确认/候选白名单；未新增路径、候选、算子或字段。
- 未调用真实 Provider、未生成图片、未跑正式评测/盲评/Gate；未 commit/push/stash。

## 7. 阻塞与下一步

**Step 02 的“真实人工审核”关口仍未通过。** 未获真实决定前：

- 不得开始 Step 03（新建 `knowledge_base/v0.5/`、发布 `v0.5-approved-1`、重算清单哈希）。
- 不得进行 Step 04 的“真实内容采用验收”；最多只能声明“工程回归可执行”。
- 不得把本批 5 份提案视为将被采用的知识。

需要用户/审核人决定（详见 `v0_5_knowledge_review.md` §3）：逐条批准与否、三路径覆盖范围、
来源形态（无日期纯原创）、模型适用性（`any` vs 未核实别名）、条件重叠消歧、B3 的 `studio` 是否扩展、
以及批准后的发布方式。

## 8. 本步是否满足验收

| 任务书 02 验收项 | 落实 |
|---|---|
| 九条旧规则均有处置建议 | 是（§2） |
| 三条路径分别报告“有可批准提案 / 证据不足” | 是：camera 有提案（A1/A2）；framing 有提案（B3）；lighting 有提案（C1/C2）；证据不足/建议移除者逐条标注 |
| 来源可追溯、许可可落实 | 是（`project_original` + 原创声明 + `source_revision`；未使用外部文本故无许可复制义务） |
| 条件确实可达 | 是（校验脚本按 `DELEGATED_CANDIDATES` 校验） |
| 未借用保留集答案 | 是（本步未读取或使用任何数据集样本；并行 Step 01 后续生成的数据与保留池未用于知识编写、关键词修改或提案选择） |
| 人工决定与待决定状态严格分开 | 是（清单审核决定列全部留空） |
| **审核关口停下交真实审核人** | **是，未越界** |

**版本结论：Step 02 的“审核前材料”部分完成；真实内容审核未发生；Step 03 禁止开始。**
