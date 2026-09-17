# v0.6 Step 01 交接：协议、样本与启动决定（离线准备）

- 日期：2026-09-16
- 依据：[docs/task_books/mvp_v0.6/README.md](../task_books/mvp_v0.6/README.md)、
  [Step 01 任务书](../task_books/mvp_v0.6/01_protocol_and_cases.md)、
  [v0.5 收尾状态复查](../task_books/mvp_v0.6/00_readiness_review.md)。
- 上游：[v0.5 发布交接](v0_5_release_handoff.md)、
  [v0.5 后置收尾交接](post_v0_5_fixes_001_handoff.md)。
- 基线：HEAD `b039b9b7cd49becd0a25e251b37d930fee07da88`（`b039b9b`），分支 `feature`，
  工作区 **dirty 且未提交**（本批交付均为未跟踪新路径，具体 status 项数随工作区变化）；
  `git stash list` 为空。
- 范围：只交付 v0.6 Step 01 的协议、候选集、标注、运行配置、冻结清单与离线校验。
- **本 Step 01 交付批次自身范围**：只交付协议、候选集、标注、运行配置、冻结清单与离线
  校验；本批内**未**实现 B/C 执行器。当前工作区**后续 Step 02 正由另一 Agent 实施**
  （`evaluation/v0_6/{context,budget,records,reporting,paired_runner}.py` 与其测试），
  最终范围与数字**以 Step 02 交接与最终验证为准**。本批未调用真实服务、未执行正式评测、
  未 commit / 未 stash。

## 0. 结论速览（必须分开陈述）

| 结论项 | 结果 |
|---|---|
| Step 01 交付物 | **完成（离线）**：协议 + 30 候选集 + 30 标注 + 运行配置 + 冻结清单 |
| 候选分层 | **满足**：18 适用（三路径各 6）/ 6 明确值+PIN 负对照（各 2）/ 6 无命中回退（各 2） |
| 规则覆盖 | **满足**：五条 v0.5 获批规则全部覆盖（3/3/6/3/3） |
| 冻结校验 | **通过**：共 8 条 sha256（协议/配置/候选集/标注 4 条 + 知识语料 4 条）全部一致，篡改/缺失被拒绝 |
| 检索可达性 | **通过**：30 个 case 的检索结果与预注册标注逐项一致（冻结语料离线复算） |
| 授权状态 | **`real_run_authorized = false`**：预算/阈值/模型身份均待 G1 确认 |
| 正式评测 | **未执行**（等待 G1；属本步边界，不是阻塞） |
| **Step 01 整体** | **离线准备完成**，等待 G1 启动决定与独立人工预审 |

## 1. 交付文件与哈希

| 文件 | 角色/用途 | SHA-256（工作区） |
|---|---|---|
| `evaluation/v0_6/protocol.md` | protocol，`bc_protocol_v0_6` | `70100d6f84b4347f36194ddcb2b7bbe811cff7498d82130c8fe1e3f374e93c92` |
| `evaluation/v0_6/run_config.json` | config，`bc_v0_6` | `f9adae7db16974dd276df4bd3b24f8e2c709c15d07b4ee65e4369bc48439237b` |
| `evaluation/v0_6/cases.jsonl` | dataset，`bc_cases_v0_6`（30 行） | `b5a9df73ae8b3c2b9f27becca51d5b6af6513beeb52a4b253ef377337c0be805` |
| `evaluation/v0_6/annotations.jsonl` | annotations（30 行） | `02f6a7508f285a1cd0ea532e9c57ac1bca037a1c4a3a419b8054ee6219fd4e84` |
| `evaluation/v0_6/frozen_manifest.json` | manifest，`frozen_manifest_v0_6` | `9514acb046b575e59e8b19e0d861e856e67dcec3f0b3fe9d06a212cd919fa4a0` |
| `tools/validate_v0_6_cases.py` | 离线校验工具（只读、无网络） | `f1d71476589dea0d6e6527e0cc90816b9dcefd9a884e817a3d0fb055162b2f32` |
| `tests/evaluation/test_v0_6_protocol_cases.py` | 离线校验测试（34 项） | `845e36f9395c39bd4a4e0645d1438450f3fe127eeba587f7f097626c6fddd753` |

- 冻结清单额外冻结知识语料四个文件（`knowledge_base/v0.5/`）：`manifest.json`
  `0bbeea9b…`、`camera.depth_of_field.jsonl` `9a32c511…`、`composition.framing.jsonl`
  `a07af1ac…`、`lighting.character.jsonl` `0b159a8f…`，与 v0.5 发布交接一致。
- 冻结清单角色路径为**仓库根相对路径**；解析时必须
  `load_manifest(path, base_dir=<repository root>)`（`evaluation/v0_6/` 比
  `evaluation/` 深一层，默认 base_dir 不适用）。

## 2. 协议要点（`bc_protocol_v0_6`）

- **B** = 同一修复后系统关闭 RAG（`knowledge_engine = None`）；**C** = 开启 RAG 并显式
  加载 `knowledge_base/v0.5`（`v0.5-approved-1`，5 条 approved，fingerprint
  `c961790c81d22e554672270b03a94474991a9f59315d0dab5585cc09610e81c4`）。
- 主实验从**同一组已确认结构化 Intent/Execution** 开始，隔离知识选择影响；不比较
  解释器或自由提示词扩写；完整“用户消息 → 确认”流程仍由既有离线回归覆盖。
- 预注册指标：盲配对偏好净胜率 `(C 胜 − B 胜) / 已完成有效双图评审对数`（平局留在
  分母），并报告胜/平/负、覆盖率、缺失、约束遵守、检索推荐与实际采用、耗时与成本；
  按独立上下文聚类 95% 区间，跨零即“证据不足”；失败不从完成率分母删除。
- 预算草案：`30 × 2 × 2 = 120` 基础请求，重试上限 `120 × (1 + r)`；金额、请求/尝试
  上限、阈值均 `null` 并标 `pending_user_confirmation`。
- revision seed：`case_id + repetition` 的确定性单次哈希映射（candidate-agnostic），
  B/C 同值；不读取候选值/知识结果，禁止挑种子。**不要求** B 回退值与 C 候选值不同；
  两者恰同时 Prompt 可相同，按平局计入分母。

## 3. 候选集分层（30 个独立上下文）

| 层 | 数量 | 分配 | 期望 |
|---|---|---|---|
| `L1_applicable` | 18 | 三路径各 6 | 条件满足 → 唯一 top → C 采用知识候选；B 走确定性回退。**数值可与候选相同**（此时两侧 Prompt 可逐字相同），按平局处理，不要求差异 |
| `L2_explicit_pin_control` | 6 | 各 2（1 明确值 + 1 PIN） | 路径已有确认值，不检索；明确值保持。PIN 控制为“已有值 + 合法 PIN”（`pin_requires_existing_value`，可由 `SET → PIN` 构造）。两类控制的显式值均 ≠ 被满足规则的知识候选，越权可见；B 与 C 在目标路径一致 |
| `L3_no_hit_fallback` | 6 | 各 2 | 条件不符 → `no_hit`（条件在评分前过滤，`top_score_positive = null`）；C 回退确定性选值，与 B 一致 |

- L1 规则覆盖：`camera…shallow_for_close_up` 3、`camera…deep_for_wide_shot` 3、
  `composition.framing.medium_shot_for_standing_pose` 6、
  `lighting.character.soft_for_tight_framing` 3、
  `lighting.character.dramatic_for_angled_framing` 3。
- 每个 case 独立 `group_id`、无父样本、无共享图片，重复图不作为独立语义样本。
- 全部 `project_original` / `internal-project-original`，`derived_from_dataset=false`，
  `external_ids=[]`；未纳入 COCO-CN，未引入 GenEval / T2I-CompBench 文本、ID 或划分。

## 4. 实际运行与验证（命令、退出码、结果）

```bash
# Step 01 离线校验工具（结构 + 分层 + 开发池分离 + 授权状态 + 清单哈希 + 检索可达性）
uv run python tools/validate_v0_6_cases.py
# cases=30 annotations=30 layers={'L1_applicable': 18, 'L2_explicit_pin_control': 6, 'L3_no_hit_fallback': 6}
# real_run_authorized=False corpus=v0.5-approved-1
# RESULT: OK（exit 0）

# Step 01 定向测试
uv run pytest -q tests/evaluation/test_v0_6_protocol_cases.py
# 34 passed（exit 0）

git diff --check
# 通过（exit 0）
```

- 说明：Step 02 Agent 正在同一工作区并行改写 `evaluation/v0_6/*.py` 与其
  `tests/evaluation/test_v0_6_{budget,gates,paired_runner}.py`；因此**全量** `pytest -q`
  在并行期间会收录其半成品用例而出现与其自身模块相关的失败（实测为其测试文件内
  `NameError: name 'make_settings' is not defined`）。该现象不由 Step 01 交付物引起，
  本交接不将其计入 Step 01 结果，见 §9。

- 全文离线回归结果见 §7（`uv run pytest -q`），本批未改运行时代码与生产语料。
- 校验工具与测试均**只读**、无网络、不调用 Provider；负例只在临时副本上进行。

## 5. 设计决定与已证事实

1. **检索以已确认 Intent 为输入**：`build_query_text` 只取已确认字段值，故条件值是
   查询词的一部分；L1 的命中已被离线复算为唯一 top（无并列 `ambiguous`）。
2. **无命中回退的真实机制**：在现有冻结语料上，条件满足时条件取值必然出现在单元
   正文/关键词中，`zero_score` 型“零命中”不可达；6 个 L3 均通过
   `conditions_not_satisfied` 过滤得到 `no_hit`。校验工具断言该原因码，不伪装零分命中。
3. **revision seed 规则（candidate-agnostic）**：Step 02 冻结 `case_id + repetition` 的
   确定性**单次固定哈希**映射，B/C 两侧写入同一 `intent_revision_id`。映射不读取
   `expected_candidate`、知识命中或任何 arm 结果，禁止搜索/挑选种子（符合 Step 02
   任务书“禁止搜索让 C 必赢的 ID”）。本步**不固定**具体 revision ID，也**不要求**
   B 回退值与 C 候选值不同：L1 允许 C 已采用但数值恰同 B 回退，此时 Prompt 可逐字相同，
   按平局计入分母。协议/配置/标注/校验均按此修正，不再声明“可分离性”或差异强制。
4. **开发/保留分离（目录实际扫描）**：校验工具按目录扫描 `tests/**`、
   `evaluation/fixtures/**`、`evaluation/annotations/**`（排除保留池
   `evaluation/v0_6/`，跳过缓存与非 UTF-8 文件），逐条断言 30 个 case 的 `case_id` /
   `user_text` **不逐字出现**在开发池。规则条件取值是获批规则的固定条件，L1 必然复用，
   分离性在样本身份与其余字段上。
5. **控制合法性已证**：校验工具为全部 30 个 case（含三个 PIN 控制）成功构造
   `VisualIntent`（值 + `USER_SPECIFIED` 解析 + `pinned_paths`），证明该形状通过 domain
   合同；PIN 控制满足 `pin_requires_existing_value`（先有值后 PIN，可由 `SET → PIN`
   合法序列构造），值在 B/C 两侧均须保持不变。L2 的 `overreach_check.differs = true`
   保证任何越权改写可见。
6. **污染风险披露**：候选集与开发池由同一 Agent 撰写，**不称“严格未见测试”**；
   标注状态为 `pre_registered_pending_independent_review`，`independent_reviewer=null`，
   需独立人工预审后方可作为正式证据。

## 6. 保护对象与未越界声明

- **未改** `visual_intent_agent/**` 运行时、领域合同、Provider、`pyproject.toml`、
  `.env`、数据库 Schema。
- **未改** v0.3 冻结物（`evaluation/protocol*.md`、`fixtures/core_v0_3*`、
  `annotations/core_v0_3*`、`frozen_manifest_v0_3*`、`configs/gate_a_v0_3*`）与
  `knowledge_base/v0.4|v0.5` 语料字节。
- **未改** v0.5 后置收尾归档与 v0.5 验收清单；未回滚、未覆盖既有 dirty 成果。
- **未 commit / push / stash**；未删除历史；未自动使工作区变 clean。
- **本批内未实施 Step 02**：本 Step 01 批次不含 B/C 执行器、配对运行、批量生成或预算
  守卫实现。当前工作区**后续 Step 02 正在实施**，其交付与验证以
  `docs/handoffs/v0_6_step_02_handoff.md`（待产出）及最终验证为准。
- 未调用真实 Provider、未生成真实图片、未执行正式评测/盲评、未伪造人工评分。

## 7. 全量离线回归（Step 01 范围）

```bash
# Step 01 交付物自身的离线校验（只读、无网络）
uv run python tools/validate_v0_6_cases.py
uv run pytest -q tests/evaluation/test_v0_6_protocol_cases.py
```

- Step 01 范围实测见 §9。**全量 `uv run pytest -q` 不作为本步结论**：Step 02 Agent
  正在同一工作区并行开发 `evaluation/v0_6/` 执行器与其测试，全量结果会随其中间状态
  波动；Step 02 稳定后应另行运行全量并以其自身交接为准。
- Step 01 的既有回归安全性：未改 `visual_intent_agent/**`、未改 v0.3/v0.5 冻结物与
  语料字节，新增仅 `evaluation/v0_6/` 数据/文档与两个新测试/工具文件。

## 8. 风险、限制与待决定项

1. **G1 未授权**：`real_run_authorized=false`。预算金额/请求/尝试上限、Provider 与可
   追溯模型身份、图片尺寸与超时、评审人与阈值均未确认（见
   [v0_6_launch_request.md](v0_6_launch_request.md)）。
2. **模型身份未核实**：项目别名 `qwen-image-3.0` 与实际模型版本对应关系仍未核实；
   语料 `target_models=["any"]` 使检索适用性不依赖模型身份，但正式结论必须绑定核实过的
   真实身份。
3. **词法检索限制**：`keyword.v1` / `lexical.v1`，非语义检索；存在漏检与并列回退。
4. **样本量**：30 个上下文仅为探索性证据，不保证检验能力，不宣称官方分数。
5. **独立预审缺失**：标注尚未经独立人工审核；若无独立预审，结论只能作探索性证据。
6. **v0.5 遗留**：COCO-CN 归档精确许可仍待确认，v0.5 继续部分完成；本步不改变该状态。
7. **工作区 dirty**：正式运行沿用真实 commit + clean 门禁；如需冻结应由用户安排提交，
   不由 Agent 自动 commit/stash。

## 9. 实测追加说明（含 seed 与 F1–F4/F6 修正）

- **合规修正（本批内）**：原 annotations 对 18 个适用 case 写了
  `b_fallback_must_differ_from_candidate` 与 `c_differs...`，协议/配置/校验还宣称
  搜索 seed 分离，与 Step 02 任务书“禁止搜索让 C 必赢的 revision ID”冲突。现已：
  1) 30 条标注 `fallback_constraint` 统一为 `none`；适用层 `expected_bc_relation` 改为
  `c_may_adopt_in_authorized_path_only_bc_values_may_coincide`，并记录“候选值与 B 回退
  值相同时两侧 Prompt 可逐字相同”；2) 协议 §3/§4/§8、run_config
  `revision_seed_control` 改为 candidate-agnostic 单次固定哈希，删除可行性搜索声明；
  3) 校验工具删除 `check_fallback_feasibility`，新增
  `check_seed_policy_is_candidate_agnostic` 与标注差异强制检测；4) 测试相应改写。
- **F1 PIN 合法性**：原三个 PIN 控制为 delegated+pinned 且无值，违反
  `validation.pin_requires_existing_value`，无法由合法用户 delta 达成。现改为“路径已有
  确认显式值 + 被 PIN”（`SET → PIN` 合法序列），不检索、值保持不变；校验工具断言
  pinned 路径必须有值且不得 delegated。
- **F2 越权可见性**：明确值与 PIN 控制的显式值原与规则候选相同，越权不可见。现改为与
  被满足规则候选不同的合法值（camera `deep` 对 shallow 候选、framing `close_up` 对
  medium 候选、PIN framing `wide_shot`、PIN lighting `dramatic` 对 soft 候选），并新增
  `overreach_check.differs = true` 预注册字段，校验工具强制断言。
- **F3 开发池扫描**：由固定文件清单改为**目录实际扫描** `tests/**`、
  `evaluation/fixtures/**`、`evaluation/annotations/**`（排除保留池
  `evaluation/v0_6/`，跳过缓存与非 UTF-8 文件），与协议一致。
- **F4 评分语义**：`no_hit` 的条件过滤发生在评分前，`top_score_positive` 由 `false`
  改为 `null`；协议 §4.0 明确“仅 adopted 为 true”，校验工具断言非 adopted 必须为 null。
- **F6 措辞**：冻结校验表述改为“共 8 条 sha256（协议/配置/候选集/标注 4 条 + 知识语料
  4 条）”。**30 个 case 数量与分层不变**，L1 检索期望不变；L2 控制值按 F1/F2 修正；
  未触网、未改运行时代码与语料。
- Step 01 范围实测（修正后最终一次）：

```bash
uv run pytest -q tests/evaluation/test_v0_6_protocol_cases.py
# 34 passed（exit 0）

uv run python tools/validate_v0_6_cases.py
# RESULT: OK（exit 0，含 candidate-agnostic seed policy 与控制合法性检查）

git diff --check
# 通过（exit 0）
```

- **全量回归的当前状态（如实）**：由于 Step 02 Agent 正在同工作区并行编写执行器与测试，
  `uv run pytest -q` 会收录其半成品用例并出现仅与其自身模块相关的失败（实测为
  `tests/evaluation/test_v0_6_{budget,gates,paired_runner}.py` 内
  `NameError: name 'make_settings' is not defined` 等，随其编辑而波动）。这些文件与
  Step 01 交付物无关，本交接不以其作为 Step 01 结论；Step 02 完成后的全量数字应由
  Step 02 交接记录。Step 01 的 34 项在独立与并行运行中均通过。按复审要求，Step 02
  稳定前不再以全量结果作为本步依据。

```bash
uv run pytest -q tests/evaluation/test_v0_6_protocol_cases.py
# 34 passed（exit 0）

uv run python tools/validate_v0_6_cases.py
# RESULT: OK（exit 0，含 candidate-agnostic seed policy 检查）

git diff --check
# 通过（exit 0）
```

- **全量回归的当前状态（如实）**：由于 Step 02 Agent 正在同工作区并行编写执行器与测试，
  `uv run pytest -q` 会收录其半成品用例并出现仅与其自身模块相关的失败（实测为
  `tests/evaluation/test_v0_6_{budget,gates,paired_runner}.py` 内
  `NameError: name 'make_settings' is not defined` 等，随其编辑而波动）。这些文件与
  Step 01 交付物无关，本交接不以其作为 Step 01 结论；Step 02 完成后的全量数字应由
  Step 02 交接记录。Step 01 的 34 项在独立与并行运行中均通过。
- 本批新增路径（均未跟踪，未提交）：`evaluation/v0_6/` 下 Step 01 五件数据/文档、
  `tools/validate_v0_6_cases.py`、`tests/evaluation/test_v0_6_protocol_cases.py`、
  本文件、[v0_6_launch_request.md](v0_6_launch_request.md)。
  （`evaluation/v0_6/__init__.py`、`context.py`、`budget.py`、`records.py`、
  `reporting.py`、`paired_runner.py` 由 Step 02 Agent 并行新增，不属 Step 01 交付物。）
