# 生产知识审核交接清单（F3）

- 生成日期：2026-09-16
- 任务来源：`docs/task_books/post_v0.4_fixes/README.md` 第 4 节 F3
- 语料目录：`knowledge_base/v0.4/`
- 语料版本：`v0.4-draft-1`
- 本文件性质：**待真实审核人决定的核查清单，不是审核结论，不是批准记录。**
- 生效状态：**9 条全部 `draft`，0 条 `approved`，0 条 `rejected`；生产 RAG 实际可用知识 0 条。**

> **重要声明（请先读）**
>
> 本清单由 agent 离线核查生成，**没有**也难以做出任何内容批准。agent：
> 1. **不得**把任何单元改为 `approved`；
> 2. **不得**填写 `reviewer` / `reviewed_at`（合同层也会拒绝 draft 携带这两个字段）；
> 3. **不得**把 `tests/` 里的测试夹具 `approved` 当作生产审核证据；
> 4. **不得**把“工程链路能加载语料”当作“生产知识已审核”。
>
> 是否批准每一条规则、以及批准后语料如何发布新版本与哈希，**必须由真实审核人决定并在本文件签署区记录**。在获得真实审核前，`build_units()` 返回空、`--rag` 路径透明回退，这是预期且正确的行为，不代表缺陷。

---

## 1. 本次核查范围与方法

只做只读核查，未运行真实 Provider、未运行正式评测、未启动图片批次或 Gate 判定，未修改 `knowledge_base/` 任何文件。

使用的离线证据：

| 核查项 | 方法 |
|---|---|
| 实际条数 / 审核字段 / 适用路径 / 条件 / 来源 | `visual_intent_agent.knowledge.load_corpus("knowledge_base/v0.4")` 实际加载并逐条枚举 |
| 文件哈希与单元数一致性 | 对每个 `.jsonl` 重新计算 `sha256` 与行数，与 `manifest.json` 比对 |
| 单元内容哈希 | 逐条重算 `sha256(content.encode("utf-8"))` 与 `content_hash` 比对 |
| 候选值授权 | 对照 `prompt_engine.engine.DELEGATED_CANDIDATES`（全系统唯一候选表） |
| 条件合法性 | 对照 `domain.paths.INTENT_PATHS`、封闭算子 `equals`/`in`、各路径领域取值集合 |
| 回归守卫 | `uv run pytest -q tests/knowledge/test_knowledge_production_corpus.py` → **4 passed** |

已有的知识目录说明（本清单不重复其内容，请一并阅读）：
`knowledge_base/v0.4/README.md`（含来源说明与人工审核逐条检查项）、
`docs/task_books/mvp_v0.4/01_knowledge_contract_corpus.md`（合同与来源核查注意事项）、
`visual_intent_agent/knowledge/models.py`（冻结合同与 `extra="forbid"`）。

---

## 2. 总体核查结论

| 项目 | 实际状态 | 判定 |
|---|---|---|
| 语料版本 | `v0.4-draft-1` | 与目录说明一致 |
| Schema 版本 | `knowledge.v1` | 等于冻结常量 `KNOWLEDGE_SCHEMA_VERSION` |
| 分词版本 | `keyword.v1` | 等于冻结常量 `KNOWLEDGE_TOKENIZER_VERSION` |
| 检索版本 | `lexical.v1` | 等于冻结常量 `KNOWLEDGE_RETRIEVAL_VERSION` |
| 单元总数 | 9（3 文件 × 3 条） | 与 `manifest.json` 及守卫测试一致 |
| `approved` | **0** | 生产消费集合为空 |
| `draft` | **9** | 全部待人工审核 |
| `rejected` | 0 | — |
| `reviewer` / `reviewed_at` | 9 条全部为 `null` | 无伪造审核字段 |
| `build_units()` 可消费单元 | **0** | RAG 实际可用知识为 0 条 |
| 文件 sha256 与 `manifest.json` | 3/3 一致 | 清单哈希有效 |
| `unit_count` 与实际行数 | 3/3 一致 | — |
| `content_hash == sha256(content)` | 9/9 一致 | 无篡改/过期哈希 |
| 候选值授权 | 9/9 属于对应路径候选集合 | — |
| 条件路径/算子/取值 | 全部合法（见 §4） | 无越界路径或取值 |
| `target_models` | 9 条均为 `["any"]` | 显式通用标识，无模型能力臆断 |
| 来源类型 | 9 条均 `project_original` | 未引用未核实的官方能力断言 |
| `source_date` | 9 条均为 `null` | 未臆造日期；以 `source_revision` 支撑可审计性 |

**结论：工程链路对这份语料是“可加载、可校验、可回退”的；但生产知识审核尚未发生，正式内容交付未完成。**

### 2.1 清单 / 哈希一致性明细（本次实测值）

`manifest.json` 自身 sha256（仅作此刻证据，审核后必然变化）：
`ec013105e452c4fd47733bb0ca7efafa562519c198dc5ef40eea1b615304b9e2`

| 文件 | `manifest.sha256` | 实测 sha256 | 一致 | `manifest.unit_count` | 实测行数 | 一致 |
|---|---|---|---|:--:|---|:--:|
| `camera.depth_of_field.jsonl` | `8d1a0f64856ad4bd229d746a90f8eb102c22f45057a395a178e4fc82c5ffb002` | 同左 | ✅ | 3 | 3 | ✅ |
| `composition.framing.jsonl` | `0f4d3025768d15e7362cfea369978ae3178afb15d25ecd87364ec7975f20efb8` | 同左 | ✅ | 3 | 3 | ✅ |
| `lighting.character.jsonl` | `1924393f84417dfc27bf6e737d8d4882a05a9ef3b75c883cfaa4a555d342c8af` | 同左 | ✅ | 3 | 3 | ✅ |

`manifest.json` 只登记上述 3 个 `.jsonl`；目录内无未登记的额外 `.jsonl`（`README.md` 与 `manifest.json` 不受清单哈希约束）。

---

## 3. 逐条待审核清单

通用待审核状态：**9 条全部为 `draft`，`待真实审核人决定`；本次核查未改变任何字段。**

对每条单元，请审核人确认：(a) 规则内容准确、可独立理解；(b) `applicable_path` 与 `candidate_value` 属于受权集合；(c) `conditions` 只引用已确认 Intent 字段且可被已确认场景满足；(d) `source` 完整（类型/标题/入口/定位/revision 或日期/许可或原创声明）；(e) `keywords`/`aliases` 来自领域词汇而非测试答案或评测样本；(f) 与同路径其他单元不冲突，或冲突已明确消歧策略。

### 3.1 路径 `camera.depth_of_field`（3 条 draft）

#### A1 · `camera.depth_of_field.shallow_for_close_up` · version `1`

- 候选值：`shallow`（属于该路径受权候选 `shallow`/`deep`）
- 适用条件：`composition.framing` **equals** `close_up`；`style.primary` **in** `cinematic` / `photorealistic`
- 目标模型：`["any"]`
- 来源：`project_original`；标题《领域规则：近景特写的浅景深优先（项目原创）》；入口 `repository_path = knowledge_base/v0.4/camera.depth_of_field.jsonl`；`locator = camera.depth_of_field.shallow_for_close_up`；`source_revision = v0.4-draft-1`；`source_date = null`；`url = null`；`license = internal-project-original`；含 `original_declaration`（声明未复制第三方文本、不声称已核实模型官方能力）
- 内容哈希：`0a38b7757b64fd0e7517fcb18f3520c13ac6bf41dcbc797b688cb5ac8139cc5b`（`sha256(content)` 一致）；正文 212 字符（≤800）
- 审核字段：`draft` / `null` / `null`
- 规则正文：

  > 当构图被确认为 close_up，且风格为 cinematic 或 photorealistic 时，景深优先选择 shallow：浅景深可压缩背景、隔离杂讯，让近景主体的视觉权重更集中。建议仅在 camera.depth_of_field 为用户委托、未 PIN 且无 active Realization 时提出；用户明确景深或需要环境可读时以用户值为准。shallow 是候选值层面的启发式，不描述具体光圈或虚化程度。

- **疑点 / 待审核**：
  - 与 A3（`shallow_for_seated_subject`）在 `close_up + sitting + soft/natural` 场景可同时适用；两者候选值同为 `shallow`，属“同候选重叠”，不产生消歧冲突，但需确认这是否为有意设计。
  - 与 A2（`deep`）因 `composition.framing` 互斥，无重叠。
  - 需确认为何 `style.primary` 用 `in (cinematic, photorealistic)` 而不含 `illustration`：是否有意排除插画风格。

#### A2 · `camera.depth_of_field.deep_for_wide_shot` · version `1`

- 候选值：`deep`
- 适用条件：`composition.framing` **equals** `wide_shot`
- 目标模型：`["any"]`
- 来源：`project_original`；标题《领域规则：广角场景的深景深优先（项目原创）》；`locator = camera.depth_of_field.deep_for_wide_shot`；`repository_path = knowledge_base/v0.4/camera.depth_of_field.jsonl`；`source_revision = v0.4-draft-1`；`source_date = null`；`url = null`；`license = internal-project-original`；含 `original_declaration`
- 内容哈希：`e2c6c5ab559684c7845c26bd42499cbe13525751c23353cfa9b9110ef9606048`（一致）；正文 187 字符
- 审核字段：`draft` / `null` / `null`
- 规则正文：

  > 当构图被确认为 wide_shot 时，景深优先选择 deep：大范围场景需要前景、主体与背景同时可辨，深景深能保留地点信息与空间层次，避免环境线索被虚化吞掉。建议仅在 camera.depth_of_field 为用户委托、未 PIN 且无 active Realization 时提出；用户明确的景深或浅景深需求优先。deep 不代表无限景深，也不承诺空间透视的准确性。

- **疑点 / 待审核**：
  - 与 A3 无共享条件路径：`wide_shot + sitting + soft/natural` 可同时满足 A2 与 A3，候选值 `deep` vs `shallow` 不同；是否采用取决于词法打分，最高分并列且候选不同时按既有设计回退为 `ambiguous`（不强行采用）。请确认这种“适用重叠 + 打分消歧”是否可接受，还是应给 A3 增加构图约束以避免双命中。
  - 需确认“wide_shot ⇒ deep”作为默认启发式是否过强（存在需要浅景深的广角创作意图）。

#### A3 · `camera.depth_of_field.shallow_for_seated_subject` · version `1`

- 候选值：`shallow`
- 适用条件：`subject.pose_action` **equals** `sitting`；`lighting.character` **in** `soft` / `natural`
- 目标模型：`["any"]`
- 来源：`project_original`；标题《领域规则：坐姿柔光的浅景深优先（项目原创）》；`locator = camera.depth_of_field.shallow_for_seated_subject`；`repository_path = knowledge_base/v0.4/camera.depth_of_field.jsonl`；`source_revision = v0.4-draft-1`；`source_date = null`；`url = null`；`license = internal-project-original`；含 `original_declaration`
- 内容哈希：`92dafc890f531d6e0efaed541be6c463349d00cc05b56b2e83b75a9af0c8384e`（一致）；正文 211 字符
- 审核字段：`draft` / `null` / `null`
- 规则正文：

  > 当姿态被确认为 sitting，且照明质感为 soft 或 natural 时，景深优先选择 shallow：坐姿常伴随更安静的情绪与更近的观看距离，浅景深能弱化背景陈设干扰，使主体成为唯一焦点。建议仅在 camera.depth_of_field 为用户委托、未 PIN 且无 active Realization 时提出；用户明确景深需求优先。该规则不使用主体身份、服装或语义级冲突判断，只依据已确认字段的精确值组合。

- **疑点 / 待审核**：
  - 条件引用了 `lighting.character`，而该路径本身也是知识可委托路径。若 `lighting.character` 尚未被确认（仍处于委托状态），按冻结条件语义“路径在已确认 Intent 中缺失 → 不满足”，本规则保守不命中。请确认这是期望行为。
  - 与 A1、A2 均存在适用重叠（见上），同/异候选情况不同，请确认消歧结果可接受。
  - 正文含“坐姿常伴随更安静的情绪”等主观推断；该表述不参与条件判定，但请确认作为审核用说明是否可接受。

### 3.2 路径 `composition.framing`（3 条 draft）

#### B1 · `composition.framing.close_up_for_single_subject` · version `1`

- 候选值：`close_up`（属于该路径受权候选 `close_up`/`medium_shot`/`wide_shot`）
- 适用条件：`subject.count` **equals** `"1"`；`style.primary` **in** `cinematic` / `photorealistic`
- 目标模型：`["any"]`
- 来源：`project_original`；标题《领域规则：单主体近景优先（项目原创）》；`locator = composition.framing.close_up_for_single_subject`；`repository_path = knowledge_base/v0.4/composition.framing.jsonl`；`source_revision = v0.4-draft-1`；`source_date = null`；`url = null`；`license = internal-project-original`；含 `original_declaration`
- 内容哈希：`c155a5095c14d78cd5396ac654a3d4b68cc0158b64682cb62b35a80b33a0334b`（一致）；正文 215 字符
- 审核字段：`draft` / `null` / `null`
- 规则正文：

  > 当主体数量被确认为 1，且风格为 cinematic 或 photorealistic 时，构图优先选择 close_up：单主体近景能提高面部与关键细节的像素占比、减少背景干扰，适合强调人物情绪或材质。建议仅在 composition.framing 为用户委托、未 PIN 且无 active Realization 时提出；用户已明确构图时不得覆盖。close_up 与镜头焦段、模型实际渲染能力无绑定关系，不承诺画质提升。

- **疑点 / 待审核**：
  - `subject.count` 条件的值写作字符串 `"1"`：合同 `values` 为字符串元组，消费者按严格十进制整数比较（拒绝前导零/正号/空串），故 `"1"` 合法；请确认该表示符合预期。
  - 与 B2、B3 均可同时适用（例：`outdoor + count 1 + photorealistic` 触发 B1/B2；`studio + count 1 + standing + cinematic` 触发 B1/B3），候选值不同，依赖词法打分/并列回退。请确认为何不加 `environment.mode` 等约束来消除重叠。
  - 请确认为何 `style.primary` 不含 `illustration`。

#### B2 · `composition.framing.wide_shot_for_outdoor_context` · version `1`

- 候选值：`wide_shot`
- 适用条件：`environment.mode` **equals** `outdoor`
- 目标模型：`["any"]`
- 来源：`project_original`；标题《领域规则：户外环境交代的广角优先（项目原创）》；`locator = composition.framing.wide_shot_for_outdoor_context`；`repository_path = knowledge_base/v0.4/composition.framing.jsonl`；`source_revision = v0.4-draft-1`；`source_date = null`；`url = null`；`license = internal-project-original`；含 `original_declaration`
- 内容哈希：`d30261281ba0b9fd9f6883c7fb2f256fd81883bf09280b6b0156764ec8e9a099`（一致）；正文 197 字符
- 审核字段：`draft` / `null` / `null`
- 规则正文：

  > 当环境被确认为 outdoor 时，构图优先选择 wide_shot：广角取景能同时容纳主体与场景关系，使地点特征成为画面信息的一部分，适合需要交代环境的委托。建议仅在 composition.framing 为用户委托、未 PIN 且无 active Realization 时提出；用户明确的构图或近景需求优先。wide_shot 不规定具体焦段或主体占比，属于候选值层面的场景匹配启发式。

- **疑点 / 待审核**：
  - 与 B3 因 `environment.mode`（`outdoor` vs `studio`）互斥；与 B1 可重叠。
  - 仅凭 `outdoor` 单条件即推荐 `wide_shot`，可能对“户外近景/特写”委托产生偏置；请确认为何不附加 `subject.count` 或姿态条件，以及是否接受该强度。

#### B3 · `composition.framing.medium_shot_for_standing_pose` · version `1`

- 候选值：`medium_shot`
- 适用条件：`subject.pose_action` **in** `standing` / `walking`；`environment.mode` **equals** `studio`
- 目标模型：`["any"]`
- 来源：`project_original`；标题《领域规则：站姿动作的中景优先（项目原创）》；`locator = composition.framing.medium_shot_for_standing_pose`；`repository_path = knowledge_base/v0.4/composition.framing.jsonl`；`source_revision = v0.4-draft-1`；`source_date = null`；`url = null`；`license = internal-project-original`；含 `original_declaration`
- 内容哈希：`fc4d2f0e5f7a9169856fd4873cb720c93c1deeca88ebb9e215aa068ef91a9536`（一致）；正文 218 字符
- 审核字段：`draft` / `null` / `null`
- 规则正文：

  > 当姿态被确认为 standing 或 walking，且环境为 studio 时，构图优先选择 medium_shot：中景能在保留人物完整动作轮廓的同时维持主体画面占比，避免近景裁掉肢体动作、远景削弱姿态可读性。建议仅在 composition.framing 为用户委托、未 PIN 且无 active Realization 时提出；用户已明确的构图与 PIN 优先。medium_shot 是启发式候选排序，不保证动作表达效果。

- **疑点 / 待审核**：
  - 与 B1 可重叠（`studio + count 1 + standing + cinematic`）。
  - `environment.mode` 限定为 `studio`，未覆盖 `indoor`；而 A1/A3/G1 等规则大量使用 `indoor`。请确认该不对称是有意（动作拍摄多在棚内）还是遗漏。

### 3.3 路径 `lighting.character`（3 条 draft）

#### C1 · `lighting.character.soft_for_tight_framing` · version `1`

- 候选值：`soft`（属于该路径受权候选 `soft`/`dramatic`/`natural`）
- 适用条件：`composition.framing` **in** `close_up` / `medium_shot`；`environment.mode` **in** `studio` / `indoor`
- 目标模型：`["any"]`
- 来源：`project_original`；标题《领域规则：近景人像的柔光优先（项目原创）》；`locator = lighting.character.soft_for_tight_framing`；`repository_path = knowledge_base/v0.4/lighting.character.jsonl`；`source_revision = v0.4-draft-1`；`source_date = null`；`url = null`；`license = internal-project-original`；含 `original_declaration`
- 内容哈希：`9060dca2b57809f8d47980b9a10b8b251e04cde4facdb5618c90bc6e27b20825`（一致）；正文 231 字符
- 审核字段：`draft` / `null` / `null`
- 规则正文：

  > 当构图已被确认为 close_up 或 medium_shot，且环境为 studio 或 indoor 时，人像照明质感优先选择 soft：近距离取景会放大皮肤与服饰的表面细节，柔和光比可压低硬阴影与高光溢出，使主体过渡更可控。该规则只在 lighting.character 仍为用户委托且尚无 active Realization 时提出建议；若用户已明确光质、或场景本身需要硬光，则以用户值为准。soft 只是候选值层面的启发式排序，不保证图片质量提升。

- **疑点 / 待审核**：
  - 与 C2 无共享条件路径：`close_up + studio + low_angle + cinematic` 可同时满足 C1 与 C2，候选 `soft` vs `dramatic` 不同，依赖词法打分/并列回退。请确认消歧可接受。
  - 与 C3 因 `environment.mode`（`studio`/`indoor` vs `outdoor`）互斥。
  - 请确认正文“场景本身需要硬光”是否会让审核人误以为存在自动判断；工程上并无该判断，规则只比较已确认字段。

#### C2 · `lighting.character.dramatic_for_angled_framing` · version `1`

- 候选值：`dramatic`
- 适用条件：`camera.angle` **in** `low_angle` / `high_angle`；`style.primary` **equals** `cinematic`
- 目标模型：`["any"]`
- 来源：`project_original`；标题《领域规则：斜角电影感的戏剧光优先（项目原创）》；`locator = lighting.character.dramatic_for_angled_framing`；`repository_path = knowledge_base/v0.4/lighting.character.jsonl`；`source_revision = v0.4-draft-1`；`source_date = null`；`url = null`；`license = internal-project-original`；含 `original_declaration`
- 内容哈希：`ffedf23b9b3f54e42502530c3cc4c180483698ebb193cff4205a43059cff68c4`（一致）；正文 223 字符
- 审核字段：`draft` / `null` / `null`
- 规则正文：

  > 当相机角度被确认为 low_angle 或 high_angle，且整体风格为 cinematic 时，照明质感优先选择 dramatic：斜角取景本身制造方向感与张力，强光比与明确主光方向可与透视配合，强化轮廓和体积。仅在 lighting.character 为用户委托且无既有实现值时作为候选提出；用户明确的光质需求、PIN 与已有 Realization 均优先于本规则。dramatic 是候选值层面的启发式，不代表必然获得戏剧化效果。

- **疑点 / 待审核**：
  - 与 C1 可重叠；与 C3 亦可重叠（`outdoor + low_angle + cinematic` → `dramatic` vs `natural`）。请确认多规则同时适用时的取舍是否可接受。
  - `camera.angle` 的 `eye_level` 被排除；请确认为何 `eye_level` 不触发戏剧光（合理性由审核人判断）。

#### C3 · `lighting.character.natural_for_outdoor` · version `1`

- 候选值：`natural`
- 适用条件：`environment.mode` **equals** `outdoor`
- 目标模型：`["any"]`
- 来源：`project_original`；标题《领域规则：户外场景的自然光优先（项目原创）》；`locator = lighting.character.natural_for_outdoor`；`repository_path = knowledge_base/v0.4/lighting.character.jsonl`；`source_revision = v0.4-draft-1`；`source_date = null`；`url = null`；`license = internal-project-original`；含 `original_declaration`
- 内容哈希：`ff132689df02098ce7adf9ac4f1e8025865bb4fa773d4ee4320d0e392cf4f94f`（一致）；正文 216 字符
- 审核字段：`draft` / `null` / `null`
- 规则正文：

  > 当环境被确认为 outdoor 时，照明质感优先选择 natural：户外场景的自然光方向、色温与阴影形态已由环境设定承载，natural 可避免在 Prompt 中叠加与之冲突的人造布光语义。该建议只在 lighting.character 为用户委托且无 active Realization 时提出；若用户已指定光质或明确要求人造光效果，以用户值为准。natural 不等同于任何具体时段的真实光照，属于候选值层面的保守选择。

- **疑点 / 待审核**：
  - 与 C2 可重叠（`outdoor + low/high_angle + cinematic`）；与 C1 互斥。
  - 仅凭 `outdoor` 单条件即推荐 `natural`，请确认是否过强、是否需要结合时段/光线条件（当前无此类已确认字段）。

---

## 4. 跨单元适用性重叠（供审核人集中决策）

下表由各单元 `conditions` 的取值集合求交集得到（“重叠”= 存在一组已确认 Intent 取值可同时满足两条规则）。重叠本身不是缺陷，但决定多条规则命中同一路径时的行为，需审核人确认是否接受或要求增加约束。

| 路径 | 单元对 | 是否可同时适用 | 候选值 | 影响 |
|---|---|:--:|---|---|
| `lighting.character` | C1 `soft` ↔ C2 `dramatic` | 可重叠（无共享路径） | 不同 | 依赖词法打分；并列且不同候选 → `ambiguous` 回退，不强行采用 |
| `lighting.character` | C2 `dramatic` ↔ C3 `natural` | 可重叠（无共享路径） | 不同 | 同上 |
| `lighting.character` | C1 `soft` ↔ C3 `natural` | 互斥（`environment.mode`） | — | 不会同时命中 |
| `composition.framing` | B1 `close_up` ↔ B2 `wide_shot` | 可重叠（无共享路径） | 不同 | 依赖词法打分/并列回退 |
| `composition.framing` | B1 `close_up` ↔ B3 `medium_shot` | 可重叠（无共享路径） | 不同 | 依赖词法打分/并列回退 |
| `composition.framing` | B2 `wide_shot` ↔ B3 `medium_shot` | 互斥（`environment.mode`） | — | 不会同时命中 |
| `camera.depth_of_field` | A1 `shallow` ↔ A3 `shallow` | 可重叠 | 相同 | 同候选重叠，不产生消歧冲突 |
| `camera.depth_of_field` | A2 `deep` ↔ A3 `shallow` | 可重叠（无共享路径） | 不同 | 依赖词法打分/并列回退 |
| `camera.depth_of_field` | A1 `shallow` ↔ A2 `deep` | 互斥（`composition.framing`） | — | 不会同时命中 |

工程侧已实现的回退语义（供审核人参考，非审核结论）：检索按 `-score, knowledge_id, version` 稳定排序、每路径 Top-3；只有最高分给出**唯一**候选才可采用，最高分并列且候选不同时回退为 `ambiguous`；条件不满足、非 `approved`、模型不匹配等分别有独立拒绝原因码（`RejectionReason`）。因此上表重叠不会导致静默强采，但会降低 `adopted` 覆盖率。

---

## 5. 需要真实审核人决定的事项

1. **逐条内容批准与否**：9 条规则（§3）分别给出 `approved` / `rejected` / 退回修改的决定。内容准确性与图片质量主张属领域判断，agent 不做结论。
2. **来源充分性**：本批全部为 `project_original`，无外部官方文档；`source_date = null` 是刻意留空以避免臆造日期，以 `source_revision = v0.4-draft-1` 承担可审计性。请确认是否接受“无日期、纯项目原创”的来源形态。
3. **模型适用性**：全部 `target_models = ["any"]`。此前已裁定 `qwen-image-3.0` 别名与 Qwen-Image 官方仓库版本对应关系**未核实**，故未引用任何官方能力断言。如后续要写具体模型标识，须先核对实际 Provider 文档并单独记录。
4. **适用条件与重叠**：§3 各条疑点与 §4 重叠表；决定是“接受现有打分消歧”还是“增删条件以消除重叠”。
5. **词汇质量**：`keywords` / `aliases` 是否为领域词汇而非测试答案或评测样本（当前检查未见与评测样本的直接映射，但词典取舍需人工判断）。
6. **发布方式**：批准后是否维持 `v0.4` 目录原地升级，或以新 `corpus_version` 发布新快照。

---

## 6. 批准后必须执行的哈希/清单流程（不得跳过）

仅在真实审核人给出 `approved` 决定后执行；**agent 不得代填**：

1. 把该单元 `review_status` 改为 `approved`，并填写**真实** `reviewer` 与 **tz-aware UTC** 的 `reviewed_at`（合同会拒绝 draft 携带这两个字段；`approved` 缺少二者也会被拒绝）。
2. 若修改了正文，同步重算 `content_hash = sha256(content)`（UTF-8）。
3. 重新计算该 JSONL 文件 sha256 与单元数，更新 `manifest.json` 的 `files[].sha256` 与 `files[].unit_count`。
4. 如需保留旧内容，使用新的 `knowledge_id` 或新 `version` 并归档旧文件，不得在同一语料内出现重复 `knowledge_id`。
5. 更新 `knowledge_base/v0.4/` 目录说明中的审核状态表，并给出新的 `corpus_version`。
6. 运行离线校验：`load_corpus` 通过、`build_units()` 非空、`tests/knowledge/` 全绿；**仅改 `review_status` 而不同步哈希会被 `load_corpus` 拒绝**。
7. 完成离线采用验证后单独记录；**不自动启动真实评测或 Provider 调用**。

> 注意：本清单中的哈希值只描述**当前 draft 快照**，审核后必然变化，不得当作最终冻结哈希。

---

## 7. 真实审核人签署区（由审核人填写，agent 留空）

| 字段 | 值 |
|---|---|
| 审核人（真实姓名/身份） | ______________________ |
| 审核日期（tz-aware UTC） | ______________________ |
| 语料版本 | `v0.4-draft-1`（审核后如需发布请另立新版本号） |
| 逐条决定 | 见 §3 各条“待审核状态”，请逐条标注 approved / rejected / 退回修改 |
| 附加说明 | ______________________ |

---

## 8. 状态报告（必须分开陈述）

- **代码/工程链路**：本次 F3 仅做只读核查，未改代码；语料可被 `load_corpus` 正常加载并通过全部合同校验，`tests/knowledge/test_knowledge_production_corpus.py` 4 项守卫测试通过。
- **旧数据库兼容**：本次未涉及，不做结论。
- **生产知识是否已审核**：**否**。9 条全部 `draft`，`build_units()` 为空，生产 RAG 实际可用知识 0 条；本清单只提供待审材料，未产生任何批准。
- **正式评测**：**未执行**；未启动真实 Provider、图片批次、A/B 或 Gate 判定。
- **语料/仓库变更**：未修改 `knowledge_base/`，未提交、未推送、未 stash。

**本文件为待审核清单与交接记录，不构成生产知识可用性声明。**
