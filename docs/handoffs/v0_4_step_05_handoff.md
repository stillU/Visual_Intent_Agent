# v0.4 Step 05 交接：评测准备（实际就绪评估与 B/C 消融启动清单）

- 日期：2026-09-16
- 依据：`docs/task_books/mvp_v0.4/05_evaluation_readiness.md`、
  `docs/task_books/mvp_v0.4/README.md`、`evaluation/protocol.md`、
  `evaluation/protocol_v0_3_r1.md`、`evaluation/manifest.py`、
  `docs/handoffs/v0_4_step_01_handoff.md`。
- 本步**只做文档准备**：不执行真实评测、真实 Provider/API/图片调用或任何付费调用；
  不修改代码、测试、协议、数据集、标注、配置或旧冻结物；不 commit/push。
  旧 v0.3 历史与 v0.4 Step 01 已交付物原样保留，未覆盖、未回滚。

## 结论先行

**当前未达到 B/C 消融的启动条件；本文件不构成任何真实批次的执行授权，也不写
Go / No-Go。** 未执行评测时不得给出方向性判定；检索命中率不得等同于图像质量提升。

阻塞项（详见第 2 节）：

1. **Step 01～04 尚未完成最终全量工程验收**：Step 01、02 已交付并有 handoff；Step 03
   在工作区中已有实现代码但**无对应 handoff**；Step 04 的 `--rag` / `--knowledge-dir`
   CLI 开关在撰写本文件期间已由并行工作落地，但**无 Step 04 handoff，也无"01～04
   完成后一次集中全量回归"的验收记录**。
2. **生产知识实际审核未完成**：`knowledge_base/v0.4/` 9 条**全部 `draft`、0 `approved`**，
   `build_units()` 为空，RAG 实际可用知识为 0 条；测试夹具中的 approved 不是生产证据。
3. **无可复现的冻结代码身份**：HEAD 为 `b039b9b`，工作区 dirty（含 Step 02/03 增量），
   正式 Run 要求干净工作区与可解析 commit，当前状态不能冒充正式版本。
4. **v0.4 评测协议/数据/阈值/预算尚未建立，也未获用户确认**；revision seed 差异
   尚未控制；调试集与保留集尚未分离。

---

## 1. 实际就绪核查

| # | 前置项（任务书） | 实际状态 | 证据 / 位置 | 判定 |
|---|---|---|---|---|
| 1 | Step 01 工程验收 | 已交付；生产语料仍全 draft | `docs/handoffs/v0_4_step_01_handoff.md`；`knowledge_base/v0.4/README.md` | 部分满足 |
| 2 | Step 02 检索/KnowledgeBundle 验收 | 代码与局部测试存在：`retrieval.py`、`bundle.py`、`tests/knowledge/test_knowledge_retrieval.py`、`test_knowledge_bundle.py`；`v0_4_step_02_handoff.md` 已在撰写期间出现（未独立复核） | `visual_intent_agent/knowledge/`、`tests/knowledge/`、`docs/handoffs/v0_4_step_02_handoff.md` | 待独立复核 |
| 3 | Step 03 PromptEngine/持久化接入验收 | 已有增量：`prompt_engine/engine.py` 引入 `KnowledgeEngine`/`KnowledgeBundle`；`RealizationValue` 三项知识来源字段；`PromptArtifact.knowledge_bundle_refs`；`knowledge_bundles` 表与迁移测试；**无 `v0_4_step_03_handoff.md`** | `git diff`（engine.py / models.py / realization/models.py / persistence/**）、`tests/persistence/test_persistence_migration_v1_to_v2.py` | 未最终验收 |
| 4 | Step 04 CLI 与集中验收 | `--rag` / `--knowledge-dir` 已在撰写期间由并行工作落地（`cli.py`）；**仍无 `v0_4_step_04_handoff.md`**，无 CLI RAG Fake 端到端验收与集中验收记录 | `visual_intent_agent/cli.py`（`--rag`/`--knowledge-dir`）；`docs/handoffs/` 仅 01/02/05 | 未最终验收 |
| 5 | 01～04 后一次全量回归 | 本步只读运行当前工作区：`uv run pytest -q` → **2463 passed, 3 deselected**；但这不是任务书要求的 Step 04 集中验收（CLI 尚未交付） | 本步只读命令输出 | 不足以替代 |
| 6 | 生产知识人工审核 | 9 draft / 0 approved / 0 rejected；`reviewer`、`reviewed_at` 全为 null | `knowledge_base/v0.4/*.jsonl`、`manifest.json`、`README.md` | **未满足** |
| 7 | 三项后置修复继续通过 | 当前工作区离线测试全绿；`EVALUATION_HARNESS_VERSION = evaluation_harness_v2` | `evaluation/reporting.py:56`、本步只读回归 | 表面满足，待集中验收确认 |
| 8 | 可复现代码快照 | HEAD `b039b9b`，12 个已修改 + 若干未跟踪文件，未 commit | `git status --short`、`git diff --stat` | **未冻结** |
| 9 | 版本/哈希冻结 | v0.4 协议、配置、数据集、标注、清单**均未建立** | `evaluation/` 仅有 v0.3 冻结物 | **未满足** |
| 10 | 用户预算与人工评审确认 | 尚未向用户取得单独确认 | — | **未满足** |

> 说明：第 5 行的 `2463 passed` 是本步对当时工作区的只读观测，用于说明"没有已知离线
> 红灯"，**不能**被引用为"Step 01～04 已通过最终全量验收"。撰写期间工作区仍在被并行
> 修改（Step 02 handoff 与 Step 04 CLI 先后出现），因此第 1 节是**某一时刻的快照**；
> 启动前必须按第 9 节重新核对实际状态，不得直接引用本节作为验收结论。

## 2. 启动阻塞项（必须逐条关闭后才讨论启动）

| ID | 阻塞项 | 关闭判据（可验证） |
|---|---|---|
| B1 | Step 03/04 无 handoff、缺集中验收 | 交付 `v0_4_step_03/04_handoff.md`；复核已落地的 `--rag` / `--knowledge-dir`（默认关闭、无远程下载）；Fake 端到端覆盖任务书 04 §集中验收 7 项；一次集中全量回归有记录 |
| B2 | 生产 0 approved | 按 `knowledge_base/v0.4/README.md` 审核清单完成人工审核，产生真实 reviewer/reviewed_at 的 approved 记录，并重算 `content_hash`、文件 sha256 与 unit_count；`build_units()` 非空 |
| B3 | 工作区 dirty / 无可复现身份 | 冻结一个可复现代码快照（commit + 干净工作区）；正式 Run 由 Runner 记录 `code_commit`/`code_dirty`，dirty 时正式 Run 必须被拒 |
| B4 | revision seed 差异未控制 | 见第 4.3 节：要么冻结基线 seed 映射，要么记录并平衡差异；不得改产品默认算法 |
| B5 | v0.4 协议/数据/配置/清单未建立 | 新建 v0.4 版本文件与 `frozen_manifest_v0_4.json`（建议字段见第 8 节），旧 v0.3 冻结物只读保留 |
| B6 | 阈值未冻结、预算/人工安排未确认 | 看结果前冻结改善指标、最小实际改善幅度、等待/澄清成本上限与缺失处理；用户对调用预算、样本量、评审安排单独确认 |
| B7 | 调试集与保留集未分离 | 建立独立调试集与保留测试集，知识库不含最终样本答案 |

## 3. 冻结项清单（启动前必须冻结；标注当前状态）

| # | 冻结项 | 当前实际值 / 状态 | 冻结方式 | 已冻结？ |
|---|---|---|---|---|
| F1 | 代码快照 | HEAD `b039b9b` + dirty 工作区 | commit + 干净工作区；Runner 记录 commit/dirty/identity_mode | 否 |
| F2 | 评测 harness / 指标版本 | `evaluation_harness_v2` | 记入 Run 身份与报告；改口径升版本 | 是（沿用） |
| F3 | 生产知识语料 | `v0.4-draft-1`，9 draft；manifest 逐文件 sha256 | 审核后生成新 corpus_version + 新 manifest 哈希 | 否（draft，是易变物） |
| F4 | 知识 schema / 分词 / 检索版本 | `knowledge.v1` / `keyword.v1` / `lexical.v1` | 记入 corpus manifest 与每个 Bundle | 是（合同已冻结） |
| F5 | Prompt / Interpreter / Policy / Feedback 版本 | 由代码快照决定 | 随 F1 冻结；正式 Run 不伪造 commit | 否 |
| F6 | Provider 参数 | LLM `qwen3.8-max`、Image `qwen-image-3.0`、`1024x1024`、每次 1 张、不设 seed、超时/重试冻结 | 写入 v0.4 config；B/C 同条件 | 否（待 v0.4 config） |
| F7 | 评测协议 / 配置 / 数据集 / 标注 / 清单 | 未建立（evaluation/ 仅有 v0.3） | 新版本文件 + `frozen_manifest_v0_4.json`，正式 Run 全量校验哈希 | 否 |
| F8 | 产物根目录 | 历史约定 `outputs/evaluation_runs/`、`outputs/evaluation_reviews/` | v0.4 沿用或另立新根，不覆盖历史 | 否 |
| F9 | RAG 开关语义 | `--rag` 默认关闭；关闭时行为等价修复后基线 | CLI 已落地（待复核），语义仍须写入 v0.4 协议 | 否（协议未建立） |

> 当前 draft 语料的 sha256（审核后必然变化，仅作此刻证据，**不得**当作最终冻结哈希）：
> `lighting.character.jsonl` `1924393f…34c8af`、`composition.framing.jsonl`
> `0f4d3025…5f20efb8`、`camera.depth_of_field.jsonl` `8d1a0f64…c5ffb002`、
> `manifest.json` `ec013105…04b9e2`。

## 4. B/C 消融设计（启动条件满足后才执行）

### 4.1 对照定义

- **B（关闭知识检索）**：修复后的同一系统，`--rag` 关闭 / 不注入 KnowledgeEngine。
- **C（开启知识检索）**：同一代码、同一开关路径开启，消费 approved 语料。
- 先做 B/C；A（Direct LLM）可随后单独补充，**不得**把未做 A 组的 B/C 描述成"完整
  Gate A 通过"。

### 4.2 会话与范围一致性

- B/C 各建**新会话**，会话内**无已有 active Realization**，避免复用历史实现掩盖差异。
- 两臂使用**内容相同的已确认 Intent、Execution 与委托范围**；对同一冻结用户消息逐字输入。
- 保留各自真实确认记录；**不复制、不伪造**跨会话 ID（session/intent/execution/confirmation/bundle）。
- 相同语料与查询下候选、排序、分数必须一致；文件行序变化不得改变选择。
- 禁止在同一会话内切开关冒充控制变量相同（任务书 03 明令）。

### 4.3 revision seed 差异控制（必须显式处理）

- 现有 `select_delegated_value(path, seed)` 的 `seed = intent_revision_id`，按
  `sha256("{path}\x1f{seed}")` 取候选下标；不同随机 revision ID 会让**无 RAG 的 B 臂**
  也产生不同选值，从而与非 RAG 因素混淆。
- 可选控制方式（启动前二选一并写明）：
  1. **冻结基线 seed 映射**：为评测夹具固定（或在报告中记录）每个委托路径使用的
     revision ID / seed，使 B 臂选值可复现、可对齐；
  2. **记录并平衡差异**：不固定 ID，但逐案例记录实际 seed 与 B 臂选值，在报告中
     把该差异单列，不用它解释 C 臂收益。
- **不得**为改善对比而修改产品默认选值算法；`DELEGATED_CANDIDATES` 与候选授权边界不变。
- C 臂选值若来自知识建议，必须可追溯 `knowledge_bundle_id` + `knowledge_unit_id` +
  `knowledge_unit_version`；B 臂不得出现知识来源字段。

## 5. 样本集组成（最低覆盖）

- **正对照**：委托路径（`lighting.character` / `composition.framing` /
  `camera.depth_of_field`）缺实现且可由 approved 知识给出**合法候选**，预期 C 臂采用。
- **负对照**：用户明确指定值与 PIN 的字段——知识给出其他建议也**必须不变**；预期 B/C
  在这些点上无差异。这是"知识只在委托实现上产生收益"的关键反证。
- **无命中回退**：无可召回单元、draft-only、空语料、语料损坏/哈希不匹配；B/C 都应透明
  回退到固定候选并记录原因，不得悄悄宣称应用了知识。
- **多轮**：修改 → 重新确认 → Realization carry/失效；重试复用原 Prompt（查询调用数不增）。
- **注入攻击**：把指令型文本放进知识正文/关键词/别名，验证其**仅作数据**，不成为用户意图、
  不绕过确认、不产生越权 clause；非法候选、跨会话/过期 Bundle 必须被拒。
- **边界**：中英文、同义词、短词、空输入、跨模型、conditions 缺失、最高分并列
  （ambiguous）回退。
- 至少两个不同确认场景导致不同合法推荐，证明不是固定返回一条知识。

### 5.1 调试集与保留集隔离

- 调试集（用于修检索/提示/流程）与**最终保留测试集**物理分离、样本不重叠。
- **禁止把最终样本的答案写入知识库**；知识 keywords/aliases 必须来自领域词汇而非评测答案。
- 检索召回检查只作工程诊断，不构成正式收益评测。

## 6. 分层指标（四层分开报告，禁止单层代替结论）

- **安全**：未授权修改、确认绑定（摘要/hash）、PIN、不可变历史（append-only）、
  来源完整性；知识注入无副作用；Bundle/来源可追溯。
- **检索**：适用性、有效命中、采用/回退比例、冲突建议、ambiguous 回退；**不能只报命中次数**。
- **Prompt**（使用修复后 harness 身份）：覆盖（coverage）、保留（preservation，无 Prompt
  轮记 `not_applicable`）、完成率（generation_completion）；关键词违例与编译期来源防护
  分开报告，不得互相派生。
- **图片**：同用户阶段盲配对，展示用户约束，区分"遵守要求"与"主观偏好"；PIN 下偏好删除后
  的图片可记录，但不能算遵守保护约束的成功；不要仅按脚本轮序强行配对。
- **成本**：查询耗时、总耗时、模型调用与失败、知识审核维护量；与收益指标同表报告。

## 7. 预算与人工评审（须用户单独确认，本文件未授权）

执行任何真实批次前，用户需分别确认：

- **调用预算**：LLM/图片调用上限、样本量、重复次数（建议 L1~L3 每案 ≥2 次测稳定性、
  L4 每系统每案 1 次成对产生；禁止单图重抽）。
- **人工评审安排**：至少两名独立评审、盲法去标签、A/B 呈现顺序随机化并记录、分歧保留；
  自动确认只是实验代理，不算真实验证。
- **缺失/失败处理**：Provider 失败不从分母剔除；级联轮记 `blocked` 并指向首个根因；
  `not_applicable` / `missing_data` / `failed` 三态逐案逐指标显式记录。
- **阈值冻结时点**：改善指标、最小实际改善幅度、等待/澄清成本上限、缺失处理必须在
  **看到结果前**冻结，不得事后补定。

## 8. 未来目录与 manifest 字段建议（仅建议，本步未创建任何文件）

> 以下为下一 agent 建立 v0.4 评测时的建议结构，**本步不创建正式结果、不创建冻结数据**。

### 8.1 建议目录

```text
evaluation/                      # 旧 v0.3 冻结物只读保留
├── protocol_v0_4.md             # 新协议（B/C 消融口径）
├── configs/gate_b_v0_4.json     # 新配置（Provider 同条件 + RAG 开关 + 阈值）
├── fixtures/core_v0_4.jsonl     # 新数据集（B/C 共用输入）
├── annotations/core_v0_4.jsonl  # 新标注（对被测系统保密）
├── fixtures/debug_v0_4.jsonl    # 调试集（与保留集隔离，可公开）
├── frozen_manifest_v0_4.json    # 新清单（含知识语料角色与哈希）
└── (reports/ 历史只读)

outputs/evaluation_runs/         # 真实运行产物（不入冻结清单）
outputs/evaluation_reviews/      # 盲评产物
```

### 8.2 manifest 建议字段（基于现有 `evaluation/manifest.py` 扩展）

现有清单角色为 `protocol` / `config` / `dataset` / `annotations`（`MANIFEST_ROLES`，
封闭四元组）。v0.4 若引入知识语料角色，需在**新版本**清单与解析器中显式扩展，
不得改动 v0.3 默认行为：

```json
{
  "manifest_version": "frozen_manifest_v0_4",
  "protocol_version": "gate_b_protocol_v0_4",
  "config_version": "gate_b_v0_4",
  "dataset_version": "core_v0_4",
  "algorithm": "sha256",
  "roles": {
    "protocol": "evaluation/protocol_v0_4.md",
    "config": "evaluation/configs/gate_b_v0_4.json",
    "dataset": "evaluation/fixtures/core_v0_4.jsonl",
    "annotations": "evaluation/annotations/core_v0_4.jsonl",
    "knowledge_corpus": "knowledge_base/v0.4/manifest.json"
  },
  "knowledge": {
    "corpus_version": "v0.4-approved-1",
    "schema_version": "knowledge.v1",
    "tokenizer_version": "keyword.v1",
    "retrieval_version": "lexical.v1",
    "approved_unit_count": 0,
    "corpus_manifest_sha256": "<审核后重算>"
  },
  "arms": {
    "B": {"rag_enabled": false},
    "C": {"rag_enabled": true, "knowledge_corpus_role": "knowledge_corpus"}
  },
  "revision_seed_policy": "frozen_map | recorded_and_balanced",
  "files": {"<repo-relative path>": "<sha256>", "...": "..."}
}
```

### 8.3 Run 记录建议新增字段（供协议所有者裁定，本步不实现）

`knowledge`：`corpus_version` / `corpus_hashes` / `tokenizer_version` /
`retrieval_version` / `rag_enabled` / `arm`（B/C）；
每条检索记录：`query`、`path`、`knowledge_id`、`unit_version`、`content_hash`、`score`、
`adopted` / `fallback_reason`、`bundle_id`；
`revision_seed`：每案例每委托路径的实际 seed 与 B 臂选值，用于第 4.3 节的对齐/平衡记录。

## 9. 启动前检查清单（全部勾选后才可提请用户授权）

- [ ] B1 Step 02～04 handoff 齐全（02 已出现，仍需独立复核）；已落地的 `--rag`/`--knowledge-dir` 默认关闭、拒绝远程下载。
- [ ] B1 Step 04 集中全量 `uv run pytest -q` + `git diff --check` 通过并记录（针对冻结快照，而非本文件的时刻快照）。
- [ ] B2 生产语料完成人工审核，approved 记录真实，`content_hash`/文件 sha256/unit_count 重算一致。
- [ ] B3 代码快照 commit 且工作区干净，Runner 能记录 `code_commit`/`code_dirty`。
- [ ] B4 revision seed 策略二选一确定并写入协议。
- [ ] B5 v0.4 协议/配置/数据集/标注/清单为新版本文件，旧冻结物未改。
- [ ] B6 阈值在使用结果前冻结；缺失处理与级联规则明确。
- [ ] B7 调试集与保留集隔离；知识库不含最终答案。
- [ ] 第 5 节样本覆盖（正/负对照、无命中、多轮、注入、边界）在数据集中可核对。
- [ ] 第 6 节四层指标定义完整，安全与完成率口径不被美化。
- [ ] 用户已单独确认调用预算、样本量、重复次数与人工评审安排。

## 10. 本步变更与未越界声明

- **新增**：本文件 `docs/handoffs/v0_4_step_05_handoff.md`（唯一写入）。
- **未修改**：`visual_intent_agent/**`、`tests/**`、`evaluation/**`（含全部 v0.3 冻结物）、
  `knowledge_base/**`、`docs/task_books/**`、既有 handoffs、`outputs/**`、
  `pyproject.toml`、`uv.lock`、`.env`。
- **未执行**：真实评测、真实 Provider/API/图片调用、付费调用、真实批次；
  仅做了只读核查与一次离线 `uv run pytest -q`（2463 passed, 3 deselected）。
- **未 commit/push**；工作区既有改动（Step 02/03 增量、`docs/task_books/README.md` 等）
  原样保留。

## 11. 已知限制

1. 本步对 Step 02/03 的"已有代码"只做静态核查与离线回归观测，**未**逐条核对任务书 02/03
   验收项，因此不能认定其已验收通过。
2. 第 1 节的 `2463 passed` 是当前 dirty 工作区的离线结果，不构成正式版本回归证据。
3. `knowledge_base/v0.4` 的 sha256 为 draft 指纹，人工审核后必然变化，不能作为最终冻结哈希。
4. 本文的目录/manifest/Run 字段均为**建议**；是否采用由下一 agent 与协议所有者裁定，
   且任何新增角色/字段都不得改动 v0.3 默认行为与旧冻结物。
5. 未核实 `qwen-image-3.0` 别名与官方仓库版本对应关系；Provider 适用性需在真实启动前核实。
6. 预算、样本量与人工评审未获用户确认前，不得进行任何真实调用。

## 12. 是否满足验收条件

**是（限本步范围）。** 任务书第 05 步本阶段只要求"准备、不自动运行真实批次"：

| # | 核对 | 结果 |
|---|---|---|
| 1 | 给出实际就绪评估 | 是。第 1 节逐项核查，明确 Step 01-04 未最终验收、生产 0 approved |
| 2 | 明确"未达到启动条件"、不写 Go/No-Go | 是。结论先行与第 2 节 |
| 3 | 列出冻结项、独立会话同范围、seed 差异控制 | 是。第 3、4 节 |
| 4 | 样本含正负对照/无命中/多轮/注入，调试与保留隔离 | 是。第 5 节 |
| 5 | 分层安全/检索/Prompt/图片/成本指标 | 是。第 6 节 |
| 6 | 预算与人工评审需用户单独确认 | 是。第 7 节 |
| 7 | 可提供未来目录/manifest 建议，但不创建正式结果/冻结数据 | 是。第 8 节仅为建议，未创建文件 |
| 8 | 不修改代码/测试/旧冻结物，不 commit/push | 是。第 10 节 |