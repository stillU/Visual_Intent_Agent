# v0.5 知识审核待决定清单（Step 02）＋ 真实审核决定录入

- 日期：2026-09-16
- 性质：**Step 02 的待决定清单，现已录入真实审核决定**（§0 与各条“审核决定”、§5 签署区）。
  本文件同时保留审核**前**材料的历史原文，二者以 §0 的决定为准。
- 输入语料（审核前，历史）：`knowledge_base/v0.4/`，`corpus_version = v0.4-draft-1`，
  **9 条全部 `draft`、0 条 `approved`**；审核产出为 `knowledge_base/v0.5/`（见下「生效状态」）。
- 提案暂存：`data/staging/rag_resources_20260916/knowledge_review/draft_proposals/`（5 份）。
- 证据卡暂存：`data/staging/rag_resources_20260916/knowledge_review/evidence_cards/`（9 份）。
- 生效状态：**真实人工审核已发生**（审核人 `change`，`2026-09-16T10:25:59Z`）；
  5 条获批并发布为 `knowledge_base/v0.5/`（`corpus_version = v0.5-approved-1`），
  A3 / B1 / C3 暂缓，B2 退回生成 v2 后再审；`knowledge_base/v0.4/` 未改。

> **重要声明（原审核前材料，仅作历史记录，已被 §0 取代）**
>
> 1. （审核前）本清单由 agent 离线生成，**没有**也难以做出任何内容批准；当时禁止把任何单元
>    改为 `approved`，禁止填写 `reviewer` / `reviewed_at`。
> 2. `tests/` 里的 approved 夹具、以及 `--demo --rag` 的内置演示语料，**都不是**生产审核证据
>    （该边界至今有效）。
> 3. （审核前）表中“建议/处置”只是 agent 的整理结论；审核决定列当时一律留空。**现已由审核人
>    `change` 于 `2026-09-16T10:25:59Z` 逐条填写，见 §0 与各条“审核决定”。**
> 4. （审核前）未获真实审核前不得创建 `knowledge_base/v0.5/`、不得开始 Step 03；获批后须按 §4
>    重算 `content_hash`、文件 sha256 与 `unit_count`。**该前置条件现已满足，Step 03 已据此执行。**
> 5. 提案正文均为**项目原创启发式改写**，未复制 Blender / Wikibooks / Nikon / Qwen-Image 文本；
>    `source_type = project_original` 属实（至今有效）。
>
> **更新（审核后）**：第 1–4 项是对“审核前”状态的忠实记录；审核已于
> `2026-09-16T10:25:59Z` 由 `change` 完成，决定见 §0，发布快照见
> [`knowledge_base/v0.5/`](../../knowledge_base/v0.5/README.md)。第 4 项的前置条件已满足，
> Step 03 据此执行。

---

## 0. 真实审核决定（已录入，2026-09-16T10:25:59Z）

- 审核人（真实身份）：`change`
- 审核时间（tz-aware UTC）：`2026-09-16T10:25:59Z`
- 审核对象：本节绑定的 5 份精确 v2 提案文件（下表逐份列出完整文件 SHA-256）。
- 录入方式：**机械录入**——只把 `review_status` 改为 `approved` 并填写真实
  `reviewer` / `reviewed_at`；正文、条件、候选、关键词、来源、版本逐字未改，
  `content_hash` 与获批提案一致。未扩大范围、未新增路径/候选/算子/字段。

| # | knowledge_id（v2） | 提案文件 SHA-256（审核绑定） | 决定 | 本轮发布 |
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

- 审批范围：三路径各至少一条（camera A1/A2、framing B3、lighting C1/C2），与任务书最低完成
  标准一致；未要求更多条数，未为凑覆盖批准弱规则。
- 来源形态 / 模型适用性 / 条件重叠 / B3 `studio` 扩展：**本轮均未变更**，按获批提案原样发布
  （`project_original` 无日期原创形态、`target_models = ["any"]`、沿用既有词法打分与并列回退、
  `studio` 不扩展）。
- COCO-CN 归档许可：**暂不确认**；本轮生产知识全部 `project_original`，**不借用 COCO 许可**。
- 发布结果：新建 `knowledge_base/v0.5/`，`corpus_version = v0.5-approved-1`，只收录上表 5 条；
  `knowledge_base/v0.4/` 与用户数据库不变。随包决定摘要见
  [`knowledge_base/v0.5/APPROVAL.md`](../../knowledge_base/v0.5/APPROVAL.md)。

---

## 1. 总览与处置建议

| # | ID（v1） | 路径 | 候选 | 处置建议 | 提案（v2，draft） | 提案文件 SHA-256 |
|---|---|---|---|:--:|---|---|
| A1 | `camera.depth_of_field.shallow_for_close_up` | camera.depth_of_field | shallow | **收窄待审** | ✅ | `dd12f624ac4d39ab8c65faa23d4fd62293945285912792112fa811bd768dab54` |
| A2 | `camera.depth_of_field.deep_for_wide_shot` | camera.depth_of_field | deep | **收窄待审** | ✅ | `5de514666b6ab898bcb2af1ceb78d172395b58aac98918fe99a2ac5458580ad3` |
| A3 | `camera.depth_of_field.shallow_for_seated_subject` | camera.depth_of_field | shallow | 暂缓 | — | —（未生成提案） |
| B1 | `composition.framing.close_up_for_single_subject` | composition.framing | close_up | 暂缓 | — | —（未生成提案） |
| B2 | `composition.framing.wide_shot_for_outdoor_context` | composition.framing | wide_shot | 建议移除 | — | —（未生成提案） |
| B3 | `composition.framing.medium_shot_for_standing_pose` | composition.framing | medium_shot | **保留待审** | ✅ | `dadec4245210947f3bf45b12292084e947a8ba7a4fa2ff24f0ed33fa70c73449` |
| C1 | `lighting.character.soft_for_tight_framing` | lighting.character | soft | **保留待审** | ✅ | `1dae6d59421f7e90aec4305cf7c843831e704c930f80d47737e7af167b88d8dc` |
| C2 | `lighting.character.dramatic_for_angled_framing` | lighting.character | dramatic | **收窄待审** | ✅ | `4539e45ccc94e42e9d936336eac09e356bc472d11359ea2011444b4743fc605f` |
| C3 | `lighting.character.natural_for_outdoor` | lighting.character | natural | 暂缓 | — | —（未生成提案） |

计数：收窄待审 3、保留待审 2、暂缓 3、建议移除 1；三路径均至少 1 份提案（camera 2、framing 1、lighting 2）。
**这是 agent 建议，不是批准。** 审核人可以全部退回、全部暂缓或只批准其中一条。

### 1.1 三条路径分别判断（任务书验收项）

| 路径 | 判断 | 依据 |
|---|---|---|
| `camera.depth_of_field` | **有可批准提案**（A1、A2，收窄后可审） | 条件可由已确认字段精确表达、候选受权、可达；无外部事实证据，只作“可被用户覆盖的项目默认偏好”提交 |
| `composition.framing` | **有可批准提案**（B3，保留待审） | 条件最具体、偏置风险最低；B1/B2 证据不足，分别建议暂缓 / 移除，不生成提案 |
| `lighting.character` | **有可批准提案**（C1 保留、C2 收窄） | 取景/角度与光质无因果关系，但作为可覆盖偏好可审；C3 单条件过宽，证据不足，建议暂缓 |

> “有可批准提案”仅表示**存在完整、合同有效的候选提案可供审核**，**不等于已批准**；
> 该路径内具体条目仍可被审核人 rejected 或退回修改。

## 2. 逐条明细

### A1 · `camera.depth_of_field.shallow_for_close_up` v1 → 建议 **收窄待审**

- 路径 / 候选：`camera.depth_of_field` / `shallow`
- 原始条件：`composition.framing` equals `close_up`；`style.primary` in `cinematic`/`photorealistic`
- 事实 / 偏好：**偏好**（项目默认，可被用户覆盖；浅景深不决定透视）
- 证据 / 许可：无外部原文证据；Blender Cameras 只支持景深概念，不足以推出“特写必浅景深”；`project_original` + 原创声明
- 冲突：与 A3 同候选重叠（A3 暂缓后消失）；与 A2 因 framing 互斥
- 建议：收窄 `style.primary` 为 `equals cinematic`，正文改写（删“压缩背景/透视压缩”，声明可覆盖、无焦段与画质承诺）
- 完整提案：`data/staging/rag_resources_20260916/knowledge_review/draft_proposals/camera.depth_of_field.shallow_for_close_up.v2.json`
- 提案文件 SHA-256：`dd12f624ac4d39ab8c65faa23d4fd62293945285912792112fa811bd768dab54`
- **审核决定（已录入 2026-09-16T10:25:59Z）**：☑ approved　☐ rejected　☐ 退回修改　　审核人：change　UTC：2026-09-16T10:25:59Z

### A2 · `camera.depth_of_field.deep_for_wide_shot` v1 → 建议 **收窄待审**

- 路径 / 候选：`camera.depth_of_field` / `deep`
- 原始条件：`composition.framing` equals `wide_shot`
- 事实 / 偏好：**偏好**（项目默认，可被用户覆盖；`wide_shot` 是景别，不是焦距）
- 证据 / 许可：无外部原文证据；Wikibooks Shot Sizes 只定义景别，不涉及景深；`project_original` + 原创声明
- 冲突：与 A3 不同候选重叠（A3 暂缓后消失）；与 A1 互斥；与 B2 属不同路径可同时命中
- 建议：新增 `environment.mode equals outdoor` 收窄，正文声明 `wide_shot ≠ 广角焦距`、`deep ≠ 无限景深`
- 完整提案：`.../draft_proposals/camera.depth_of_field.deep_for_wide_shot.v2.json`
- 提案文件 SHA-256：`5de514666b6ab898bcb2af1ceb78d172395b58aac98918fe99a2ac5458580ad3`
- **审核决定（已录入 2026-09-16T10:25:59Z）**：☑ approved　☐ rejected　☐ 退回修改　　审核人：change　UTC：2026-09-16T10:25:59Z

### A3 · `camera.depth_of_field.shallow_for_seated_subject` v1 → 建议 **暂缓**

- 路径 / 候选：`camera.depth_of_field` / `shallow`
- 原始条件：`subject.pose_action` equals `sitting`；`lighting.character` in `soft`/`natural`
- 事实 / 偏好：**主观偏好联想**（“坐姿→安静情绪→浅景深”不可由已确认字段验证）
- 证据 / 许可：无；Blender 资料明确不支持“坐姿必须浅景深”；`project_original`
- 冲突：与 A1 同候选重叠、与 A2 不同候选重叠；条件引用同为委托路径的 `lighting.character`，语义可达性差
- 建议：**暂缓，本版不生成提案**（camera 路径已由 A1/A2 覆盖，不为凑数保留）
- 完整提案：无
- **审核决定（已录入 2026-09-16T10:25:59Z）**：☑ 同意暂缓　☐ 要求保留（请写明收窄方向）　☐ 建议移除　　审核人：change　UTC：2026-09-16T10:25:59Z

### B1 · `composition.framing.close_up_for_single_subject` v1 → 建议 **暂缓**

- 路径 / 候选：`composition.framing` / `close_up`
- 原始条件：`subject.count` equals `"1"`；`style.primary` in `cinematic`/`photorealistic`
- 事实 / 偏好：**不可推出的默认偏好**（“单主体必特写”被任务书点名）；反向偏置强
- 证据 / 许可：无；Wikibooks 只定义景别，不能推出“单主体必须 close_up”；`project_original`
- 冲突：与 B2、B3 均可同时命中且候选不同 → 依赖词法打分/并列回退
- 建议：**暂缓，本版不生成提案**（framing 路径由 B3 覆盖）
- 完整提案：无
- **审核决定（已录入 2026-09-16T10:25:59Z）**：☑ 同意暂缓　☐ 要求保留（请写明收窄方向）　☐ 建议移除　　审核人：change　UTC：2026-09-16T10:25:59Z

### B2 · `composition.framing.wide_shot_for_outdoor_context` v1 → 建议 **移除**

- 路径 / 候选：`composition.framing` / `wide_shot`
- 原始条件：`environment.mode` equals `outdoor`（单条件过宽）
- 事实 / 偏好：**不可推出的过强默认偏好**（“户外必远景”被任务书点名）
- 证据 / 许可：无；Wikibooks 不能推出“户外必须 wide_shot”；`project_original`
- 冲突：与 B1 可同时命中且候选相反；与 C3 曾同上下文叠加默认偏好
- 建议：**建议移除**（若审核人不接受移除，应降级为“暂缓”，不得直接收窄保留而不给依据）
- 完整提案：无
- **审核决定（已录入 2026-09-16T10:25:59Z）**：☐ 同意移除　☐ 降级为暂缓　☐ 要求收窄保留（请写明依据）　☑ **退回生成 v2 后再审（本轮不发布；禁止发布旧 v1）**　　审核人：change　UTC：2026-09-16T10:25:59Z

### B3 · `composition.framing.medium_shot_for_standing_pose` v1 → 建议 **保留待审**

- 路径 / 候选：`composition.framing` / `medium_shot`
- 原始条件：`subject.pose_action` in `standing`/`walking`；`environment.mode` equals `studio`
- 事实 / 偏好：**偏好**（项目默认，条件最具体、偏置风险最低；仍非事实必然）
- 证据 / 许可：Wikibooks 支持中景景别的一般概念，但不支持“站姿必须中景”；`project_original` + 原创声明
- 冲突：与 B1 可同时命中（B1 暂缓后减少）；与 B2 互斥
- 建议：保留条件不变，正文改写为“项目默认偏好 + 可覆盖 + 不规定焦段/占比/透视 + 不保证动作效果”
- 完整提案：`.../draft_proposals/composition.framing.medium_shot_for_standing_pose.v2.json`
- 提案文件 SHA-256：`dadec4245210947f3bf45b12292084e947a8ba7a4fa2ff24f0ed33fa70c73449`
- **审核决定（已录入 2026-09-16T10:25:59Z）**：☑ approved　☐ rejected　☐ 退回修改　　审核人：change　UTC：2026-09-16T10:25:59Z

### C1 · `lighting.character.soft_for_tight_framing` v1 → 建议 **保留待审**

- 路径 / 候选：`lighting.character` / `soft`
- 原始条件：`composition.framing` in `close_up`/`medium_shot`；`environment.mode` in `studio`/`indoor`
- 事实 / 偏好：**偏好**（项目默认，可覆盖；取景不决定光质）
- 证据 / 许可：Blender Light Objects 只支持“光源尺寸↔阴影软硬”，**不足以**推出“室内一定 soft”；`project_original` + 原创声明
- 冲突：与 C2 可同时命中且候选不同 → 词法打分/并列回退；与 C3 因 environment 互斥
- 建议：条件不变，正文改写为“项目偏好 + 可覆盖 + 取景不决定光质 + 不承诺画质改善”
- 完整提案：`.../draft_proposals/lighting.character.soft_for_tight_framing.v2.json`
- 提案文件 SHA-256：`1dae6d59421f7e90aec4305cf7c843831e704c930f80d47737e7af167b88d8dc`
- **审核决定（已录入 2026-09-16T10:25:59Z）**：☑ approved　☐ rejected　☐ 退回修改　　审核人：change　UTC：2026-09-16T10:25:59Z

### C2 · `lighting.character.dramatic_for_angled_framing` v1 → 建议 **收窄待审**

- 路径 / 候选：`lighting.character` / `dramatic`
- 原始条件：`camera.angle` in `low_angle`/`high_angle`；`style.primary` equals `cinematic`
- 事实 / 偏好：**偏好**（低角度方向可解释；`high_angle` 一并纳入缺依据）
- 证据 / 许可：无直接证据；Blender Light Objects 只支持一般光比/阴影概念；`project_original` + 原创声明
- 冲突：与 C1、C3 均可同时命中且候选不同 → 词法打分/并列回退
- 建议：收窄 `camera.angle` 为 `equals low_angle`（去掉 `high_angle`），正文声明“角度不决定光比/主光方向”
- 完整提案：`.../draft_proposals/lighting.character.dramatic_for_angled_framing.v2.json`
- 提案文件 SHA-256：`4539e45ccc94e42e9d936336eac09e356bc472d11359ea2011444b4743fc605f`
- **审核决定（已录入 2026-09-16T10:25:59Z）**：☑ approved　☐ rejected　☐ 退回修改　　审核人：change　UTC：2026-09-16T10:25:59Z

### C3 · `lighting.character.natural_for_outdoor` v1 → 建议 **暂缓**

- 路径 / 候选：`lighting.character` / `natural`
- 原始条件：`environment.mode` equals `outdoor`（单条件过宽）
- 事实 / 偏好：**不可推出的过强默认偏好**（“户外必 natural”被任务书点名）
- 证据 / 许可：无；Blender Light Objects 不足以推出“户外一定 natural”；`project_original`
- 冲突：与 C1 互斥；与 C2 可同时命中且候选不同 → 词法打分/并列回退
- 建议：**暂缓，本版不生成提案**（lighting 路径由 C1/C2 覆盖）
- 完整提案：无
- **审核决定（已录入 2026-09-16T10:25:59Z）**：☑ 同意暂缓　☐ 要求保留（请写明依据/收窄方向）　☐ 建议移除　　审核人：change　UTC：2026-09-16T10:25:59Z

## 3. 需要真实审核人决定的事项

1. **逐条决定**：每条给出 `approved` / `rejected` / 退回修改。建议仅作参考。
2. **范围**：是否满足于“三路径各至少 1 条”（README 最低完成标准），或要求更多。
3. **偏好承认**：批准某一偏好时，须明确它是**可被用户要求覆盖的项目默认偏好**，不是物理定律。
4. **来源形态**：接受 `project_original` + `source_revision`、`source_date = null` 的“无日期原创”形态吗？
5. **模型适用性**：维持 `target_models = ["any"]`，还是填写具体模型？（`qwen-image-3.0` 别名与官方版本
   对应关系**未核实**，填写前须先核实 Provider 文档。）
6. **条件重叠**：接受现有“词法打分 + 并列回退 `ambiguous`”消歧，还是要求增删条件？
7. **B3 的 `studio` 是否扩展为 `studio`/`indoor`**；扩展属条件变更，应作为独立新版本/新审核，不得沿用本批决定。
8. **发布方式**：批准后是否按 Step 03 新建 `knowledge_base/v0.5/` + `corpus_version = v0.5-approved-1`（建议）。

> **本轮结果（机械录入，未扩大）**：第 1 项按 §0 逐条给出；第 2 项按“三路径各至少 1 条”执行，
> 未要求更多；第 3 项由获批提案正文的“可被用户覆盖的项目默认偏好”声明承载，本轮未另改；
> 第 4 / 5 / 6 / 7 项**均未变更**，按获批提案原样发布（`project_original` 无日期原创形态、
> `target_models = ["any"]`、沿用既有词法打分与并列回退、`studio` 不扩展）；第 8 项按建议执行。
> 未决问题若将来改变条件/来源/模型适用性，必须作为**新版本重新审核**，不得沿用本批决定。

## 4. 批准后必须执行的哈希/清单流程（已于 Step 03 执行；保留为流程记录）

1. 把该单元 `review_status` 改为 `approved`，填写**真实** `reviewer` 与 tz-aware UTC 的 `reviewed_at`
   （合同拒绝 draft 携带这两个字段，也拒绝缺字段的 approved）。
2. 若修改了正文，重算 `content_hash = sha256(content)`（UTF-8）。
3. 在**新目录** `knowledge_base/v0.5/` 生成 JSONL 与 `manifest.json`，逐文件重算 sha256 与 `unit_count`。
4. 同一语料内不得重复 `knowledge_id`；旧 v0.4 快照与用户数据库保持不变。
5. 运行 `uv run python tools/validate_knowledge_review_proposals.py` 与 `uv run pytest -q tests/knowledge`。
6. 之后才能进行 Step 03 的离线采用验证；**不自动启动真实评测或 Provider 调用**。

## 5. 真实审核人签署区（已由审核人 `change` 填写）

| 字段 | 值 |
|---|---|
| 审核人（真实姓名/身份） | `change` |
| 审核日期（tz-aware UTC） | `2026-09-16T10:25:59Z` |
| 审核的语料/提案版本 | `v0.4-draft-1` 旧版 + `v0.5-draft-proposal-1` 提案批 |
| 提案文件哈希核验 | 逐份对照 §1/§0 的 SHA-256：☑ 已核验（5/5 一致） |
| 逐条决定 | 见 §0 与 §2 各条“审核决定”：A1/A2/B3/C1/C2 approved；A3/B1/C3 暂缓；B2 退回生成 v2 后再审 |
| 附加说明 | 机械录入，未扩大；COCO-CN 许可暂不确认；本轮生产知识均 `project_original` |

## 6. 状态报告（必须分开陈述；已更新至审核后）

- **工程链路**：Step 02 未改运行时代码，提案可用现有 `KnowledgeUnit` 合同解析
  （见 `v0_5_step_02_handoff.md`）；Step 03 新增发布语料、离线测试，并同步更新状态文档
  （见 `v0_5_step_03_handoff.md` §6）。
- **生产知识是否已审核**：**是（仅 v0.5 快照的 5 条）**。`knowledge_base/v0.5/`
  （`corpus_version = v0.5-approved-1`）含 5 条 `approved`，`build_units()` 为 5 条；
  `knowledge_base/v0.4/` 仍 9 draft / 0 approved / `build_units() == ()`，未改。
- **正式评测**：**未执行**；未调用真实 Provider、未生成图片、未跑 A/B 或 Gate 判定。
- **Step 03**：前置（真实批准）已满足；发布与离线采用验证已执行完成
  （见 `v0_5_step_03_handoff.md`）；正式 Provider / 图片评测仍等待独立授权。
- **v0.5 整体**：**部分完成**（唯一不完整原因是 Step 01 的 COCO-CN 许可暂不确认，数据准备
  保持部分完成）；正式评测属本版边界（本版不得执行），不是不完整原因或阻塞。

**本文件既记录审核前材料，也记录真实审核决定；不构成知识质量、RAG 收益或图片质量声明。**
