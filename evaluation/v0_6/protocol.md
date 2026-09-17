# B/C 对照评测协议（MVP v0.6 · Step 01 预注册稿）

- 协议版本：`bc_protocol_v0_6`
- 数据集版本：`bc_cases_v0_6`
- 配置版本：`bc_v0_6`
- 依据：[docs/task_books/mvp_v0.6/README.md](../../docs/task_books/mvp_v0.6/README.md)、
  [Step 01 任务书](../../docs/task_books/mvp_v0.6/01_protocol_and_cases.md)、
  [v0.5 收尾状态复查](../../docs/task_books/mvp_v0.6/00_readiness_review.md)。
- 上游输入：[v0.5 发布交接](../../docs/handoffs/v0_5_release_handoff.md)、
  [v0.5 后置收尾交接](../../docs/handoffs/post_v0_5_fixes_001_handoff.md)。
- 地位：本协议是 v0.6 B/C 对照的**唯一预注册口径**。冻结后不得为提分修改协议、候选集、
  标注或配置；任何修订生成新版本文件与新清单，旧文件只读保留。
- 授权状态：**`real_run_authorized = false`**。G1 未获授权前不得调用真实服务、
  不得执行正式评测、不得产出质量结论。

---

## 1. 比较对象与范围

- **B = 同一修复后系统，关闭 RAG**：`knowledge_engine = None`，不加载语料、不检索、
  不写 Bundle，委托路径只用确定性 `select_delegated_value` 回退。
- **C = 同一系统，开启 RAG 并显式加载 `knowledge_base/v0.5/`**（`corpus_version =
  v0.5-approved-1`，5 条 `approved`，fingerprint
  `c961790c81d22e554672270b03a94474991a9f59315d0dab5585cc09610e81c4`）。
- 主实验**从同一组已确认的结构化 Intent / Execution 开始**，隔离知识选择的影响；
  不同时比较解释器、自由提示词扩写、多轮澄清或模型能力。完整“用户消息 → 确认”流程
  继续由既有离线回归覆盖。
- 复用既有只读指标、身份与图像评审组件；**不改写 v0.3 冻结物**
  （`evaluation/protocol*.md`、`evaluation/fixtures/core_v0_3*.jsonl`、
  `evaluation/annotations/core_v0_3*.jsonl`、`evaluation/frozen_manifest_v0_3*.json`、
  `evaluation/configs/gate_a_v0_3*.json`），也**不把原 A/B（Direct Baseline A ×
  System B）Runner 改名冒充 B/C**。
- 不改业务数据库模式、Intent/PIN/确认语义、候选/条件合同、RAG 默认关闭行为、
  语料快照与历史重试语义。

## 2. 候选集与分层（30 个独立上下文）

默认候选集共 **30 个独立上下文**，语义与分层在查看任何 C 命中或图片之前预注册，
不得事后按命中或观感剔除不利场景。

| 层 | 数量 | 路径分配 | 语义 |
|---|---|---|---|
| `L1_applicable` 知识适用 | 18 | 三路径各 6 | 委托路径条件满足、有唯一可消费 approved 单元，C 采用知识候选、B 走确定性回退 |
| `L2_explicit_pin_control` 明确值/PIN 负对照 | 6 | 三路径各 2（1 明确值 + 1 PIN） | 路径已有确认值：明确值路径不检索；PIN 路径在值已存在后被合法 PIN（`validation.pin_requires_existing_value`），不检索、值保持不变。两类控制都必须让越权**可见**：显式值 ≠ 该上下文下被满足规则的知识候选 |
| `L3_no_hit_fallback` 无命中回退 | 6 | 三路径各 2 | 委托路径无任何可消费命中，C 与 B 一样回退确定性选值 |

- L1 覆盖五条获批规则：`camera.depth_of_field.shallow_for_close_up`（3）、
  `camera.depth_of_field.deep_for_wide_shot`（3）、
  `composition.framing.medium_shot_for_standing_pose`（6）、
  `lighting.character.soft_for_tight_framing`（3）、
  `lighting.character.dramatic_for_angled_framing`（3）。
- 每个上下文是一个独立语义单位（`group_id` 唯一，`parent_case_id = null`），
  不共享父样本、不共享图片，因此重复图不会被当作更多独立样本。
- **已有工程三十场景（v0.5 F2 验收清单 `tests/fixtures/knowledge/
  v0_5_acceptance_cases.json`）是开发/回归池，不计入本候选集，也不是“未见评测样本”。**

### 2.1 来源、原创性与开发/保留分离

- 30 个样本全部 `source_type = project_original`、`license =
  internal-project-original`、`external_ids = []`、`derived_from_dataset = false`，
  由本 Agent 独立撰写中文场景与结构化上下文。
- **未纳入** COCO-CN（许可待定，沿用 v0.5 部分完成状态）；也未引入 GenEval /
  T2I-CompBench 文本、ID 或划分。任何未来外部来源必须记录原始 ID、revision、许可与
  改写说明，并另行冻结。
- 开发池 = 既有 `tests/**`、`evaluation/fixtures/**`、`evaluation/annotations/**`
  （含 v0.5 验收清单；保留池 `evaluation/v0_6/` 本身除外）。离线校验工具按目录**实际
  扫描**上述路径并逐条断言本候选集的 `user_text` / `case_id` **不逐字出现**在开发池
  文本文件中（跳过缓存与非 UTF-8 文件）。
- 规则条件值是获批规则的**固定条件**（如 `close_up` + `cinematic`），L1 必然复用这些
  条件取值；分离性体现在**样本身份、用户文本与其余已确认字段**上，不在条件枚举上。
- **污染风险披露**：本候选集与开发池由同一 Agent 撰写并已通读既有测试，因此
  **不得称为“严格未见测试”**。标注 `annotation_status =
  pre_registered_pending_independent_review`，正确性需由独立审核人在 G1 前预审并
  记录；若无法获得独立预审，结论只作探索性证据。

## 3. 上下文、控制变量与公平性

- 每个 `case × repetition × arm` 使用**独立新 SQLite、新会话**，无 active Realization；
  不先 B 后 C 复用会话，不跨库复制 Prompt / Bundle / 确认记录。
- 两侧分别写入等价的合法评测上下文，经各自确认摘要与确认接口建立绑定；评测脚本
  代表事先批准的场景，**不伪称真实用户交互**。
- **revision ID 控制**：`select_delegated_value(path, seed)` 以 `intent_revision_id`
  为种子。Step 02 冻结 `case_id + repetition` 的**确定性单次哈希映射**
  （candidate-agnostic）：映射只由 `case_id` 与 `repetition` 决定，两侧写入**相同值**的
  合法 `intent_revision_id`，各自保留完整父关系与独立确认绑定。相同 ID 仅在隔离库内
  存在，不跨库查询；汇总主键为 `run/case/repetition/arm`。
- **禁止按候选值挑种子**：映射与生成过程**不得读取** `expected_candidate`、知识命中、
  采用结果或任何 arm 输出；不得搜索、筛选或重试种子以制造差异或让 C 获胜；不得改产品
  默认选值算法，也不得全局 monkeypatch。种子是单次固定哈希的直接结果，不是可调参数。
- 本文件**不固定具体 revision ID**（避免与 Step 02 实现耦合），也**不要求**确定性回退值
  与知识候选值不同。**允许 C 已采用知识候选、但其数值恰好等于 B 的确定性回退值**，
  此时两侧 Prompt 可逐字相同：该对仍计入分母并按平局统计，属合法结果，不是偏离、
  不是失败，不得据此剔除样本、重跑或改阈值。
- 图片 Provider 支持显式 seed 时，B/C 使用相同预先冻结的 seed 对；**不得把 revision
  seed 当图片 seed**。不支持时按固定规则随机化每对调用顺序、保留重复样本并在结论
  披露非确定性限制，不假装像素级可复现。

## 4. 预注册指标

### 4.0 标注词表（预注册期望的封闭取值）

- `expected_retrieval.queried`：该委托路径是否进入检索（明确值路径与 PIN 路径为 `false`）。
- `expected_retrieval.outcome`：`adopted`（唯一 top 候选）/ `no_hit`（无可消费命中）/
  `not_queried`（未进入检索）。
- `expected_retrieval.reason_code`：`unique_top_candidate` / `no_hit` /
  `explicit_value_not_queried` / `pinned_existing_value_not_queried`。
- `expected_retrieval.top_score_positive`：只有真正进入评分并被采用的 `adopted` 为
  `true`；`no_hit`（候选在评分前被条件过滤）与 `not_queried` 无任何正分单元，必须为
  `null`，不得写成 `false` 冒充“评分为零”。
- `overreach_check`（仅 L2 控制）：记录该上下文下被满足的规则 id、其知识候选值与路径
  的显式值，并要求 `differs = true`；显式值与候选值相同时越权不可见，禁止使用。
- `expected_adoption.outcome`：`adopted`（C 实际采用知识候选）/
  `not_adopted_fallback`（检索到但无推荐，回退确定性选值）/
  `path_not_queried`（明确值或 PIN 保护，路径未检索）。
- `expected_adoption.reason_code`：`explicit_value_preserved` / `path_pinned` /
  `no_adopted_result`；`not_queried` 情形下它是**语义原因说明**，不表示存在消费端
  拒绝记录（未检索即无 Bundle、无 adoption decision）。
- PIN 合法性：PIN 只在路径**已有值**时成立（`pin_requires_existing_value`），因此本层
  PIN 控制均为“已有确认显式值 + 被 PIN”，可由合法 delta 序列 `SET → PIN` 构造；不存在
  delegated 且无值的 PIN 上下文。
- `expected_bc_relation`：
  `c_may_adopt_in_authorized_path_only_bc_values_may_coincide`（C 的知识采用只可能发生在
  被授权路径；其余路径与 B 一致；被授权路径上 C 候选值**可以**与 B 回退值相同）/
  `b_and_c_identical_on_target_path`（明确值 / PIN / 无命中：目标路径 B 与 C 一致）。
- `fallback_constraint`：固定为 `none`。协议**不要求** B 的确定性回退值不等于 C 的知识
  候选值，也不存在任何“必须产生差异”的约束。

### 4.1 必备工程门槛（先于任何质量解读）

- 明确值 / PIN / 确认绑定 / 不可变历史违例数 **= 0**。
- B/C 非知识控制变量一致：除被授权路径的委托选值来源外，两侧 Prompt 的其它 clause、
  目标模型、尺寸、上下文与确认记录一致；C 的知识采用**只**允许出现在被授权路径。
  当 C 候选值与 B 回退值恰好相同，两侧 Prompt 可以逐字相同——这仍然是合法对照，
  不需为制造差异而调整种子或样本。
- 出现任一违例即**暂停批次并保留已有结果**，不以“图片更好”抵消安全失败。

### 4.2 质量主指标

- **盲配对偏好净胜率 = (C 胜对数 − B 胜对数) / 已完成有效双图评审对数**；平局留在
  分母，同时报告胜 / 平 / 负绝对数、评分覆盖率与缺失数。
- 仅对双方都有有效图片的对生成随机左右展示，`side→arm` 映射单独保存，评审材料隐藏
  知识开关、候选来源与采用标签；显示用户原始要求与已确认约束。
- **用户约束遵守**单独报告（明确值、PIN、确认绑定未被知识覆盖），不把主观风格偏好
  等同于遵守率。
- 至少一名真实评审人并记录身份；盲评未完成不得解盲，评分不得由 Agent 补造，
  不引入额外付费模型裁判。

### 4.3 必须同时报告的工程口径

- 全部计划对数与 B 成功 / C 成功 / 单侧失败 / 双侧失败 / 未知；**失败不从总完成率
  分母删除**。
- 检索推荐数与实际采用数、回退数与回退原因（`conditions_not_satisfied` /
  `path_pinned` / `explicit_value` / `no_adopted_result` 等）。
- Prompt 覆盖率、端到端耗时与检索耗时、实际调用次数与已知成本。
- 有效图片子集的偏好结果必须明确标注为条件性结果。

### 4.4 判定与分层

- 按**独立上下文聚类**计算 95% 区间；重复图不充作独立样本；净胜率区间跨零则判
  **证据不足**。
- 即使整体正向，也按 `L1_applicable` / `L2_explicit_pin_control` /
  `L3_no_hit_fallback` 分层报告。
- 最小有意义收益、允许的完成率差值、耗时与成本上限由用户在看结果前确认；
  未确认不输出达标判定。
- 30 个上下文仅探索性证据，不保证检验能力，不宣称官方 GenEval/CompBench 分数。

## 5. 失败、记账与续跑口径

- 预算守卫覆盖适配器内部重试：请求发出前持久化预留尝试与最坏成本；失败 / 超时照实
  记账。无法观察内部尝试时按该次调用最大尝试成本预留，耗尽即停。
- 网络超时、进程中断后结果未知的请求标 `unknown`，先核对 Provider 可查询状态；
  不能默认未扣费并自动重发；无查询能力时保持未知并请求决定。
- 运行目录**只追加**；同一身份 / 配置的续跑不覆盖已有成功结果；配置、代码或知识
  变化必须新 run，不混用旧输出。
- 不选择性丢弃差图，不 post-hoc 剔除样本或改阈值制造成功。

## 6. 预算草案（待 G1 确认，当前不是授权）

- 基础：`30 上下文 × 2 次独立生成重复 × B/C 两侧 = 120` 次基础图片请求。
- 若单次最多重试 `r` 次，上限为 `120 × (1 + r)` 次底层尝试；费用不明的超时请求也
  计入已消耗尝试。失败后补跑 / 试运行额外计入，不能暗含免费调用。
- 精确金额上限、请求 / 尝试上限、截止条件、评审人及工作量、指标阈值**均为 `null` /
  `pending_user_confirmation`**；价格需在启动时核实，无法估计上界不启动。
- 连通性小试（若有）单列范围与预算，其输出不得当正式样本，也不得用于改阈值。

## 7. 冻结物、身份与修订

- 冻结物四角色（见 `frozen_manifest.json`）：
  - `protocol` → `evaluation/v0_6/protocol.md`
  - `config` → `evaluation/v0_6/run_config.json`
  - `dataset` → `evaluation/v0_6/cases.jsonl`
  - `annotations` → `evaluation/v0_6/annotations.jsonl`
- 清单另冻结知识语料三文件与 `manifest.json`（`knowledge_base/v0.5/`）的字节哈希。
  冻结清单位于 `evaluation/v0_6/`，角色路径为**仓库根相对路径**，解析时必须显式
  `load_manifest(path, base_dir=<repository root>)`；`verify()` 逐个复核 sha256，
  任何缺失 / 不一致即拒绝启动。
- 正式运行沿用 `evaluation/identity.py` 的真实 commit + clean 门禁；工作区 dirty、
  来源许可或哈希不符、模型身份不明、预算缺失时拒绝真实启动，不得用 diagnostic
  模式冒充正式证据。
- 不为使代码 clean 自动 commit / stash；列出阻塞，请用户安排冻结。

## 8. 已知限制与不可推断声明

1. **词法检索边界**：本地 JSONL + `keyword.v1` / `lexical.v1` 词法检索，非语义 /
   向量检索；命中与排序由关键词、别名与条件决定，存在漏检与并列回退。
2. **L3 回退机制**：在现有冻结语料上，条件满足时条件取值本身必然出现在单元正文 /
   关键词中，故 `zero_score` 型“零命中”不可达；本层可复现的“无命中”是
   `conditions_not_satisfied` 过滤导致的 `no_hit`。该限制由离线校验工具断言并报告，
   不伪装成零分命中。
3. **未核实模型别名**：`qwen-image-3.0` 与官方版本的对应关系未核实；语料
   `target_models = ["any"]`，因此 B/C 的检索适用性不依赖具体模型身份，但正式结论
   必须绑定启动时核实过的真实模型身份。
4. **不强制数值分离**：协议不要求 C 采用的候选值与 B 的确定性回退值不同；当两者恰好
   相同时，两侧 Prompt 可逐字相同，该对按平局处理并保留在分母。因此“L1 适用”不等于
   “每对必然出现 Prompt 差异”，也不据此挑选种子或样本。
5. **本项目不构成画质 / RAG 收益结论**；工程测试通过 ≠ 生成效果改善，也不等于质量
   通过。
6. **授权边界**：`real_run_authorized = false`。没有 G1 授权或盲评未完成，只能报告
   “离线准备完成 / 等待运行或评审”。
7. **v0.5 遗留**：COCO-CN 归档精确许可仍待确认，v0.5 继续部分完成；本文件不改变该
   历史状态。

## 9. 版本与文件清单

| 文件 | 角色 | 版本 |
|---|---|---|
| `evaluation/v0_6/protocol.md` | protocol | `bc_protocol_v0_6` |
| `evaluation/v0_6/run_config.json` | config | `bc_v0_6` |
| `evaluation/v0_6/cases.jsonl` | dataset | `bc_cases_v0_6` |
| `evaluation/v0_6/annotations.jsonl` | annotations | `bc_cases_v0_6` |
| `evaluation/v0_6/frozen_manifest.json` | manifest | `frozen_manifest_v0_6` |
| `tools/validate_v0_6_cases.py` | 离线校验工具（只读） | — |
| `tests/evaluation/test_v0_6_protocol_cases.py` | 离线校验测试 | — |

旧 v0.3 协议、数据、标注与清单保持只读；旧测试继续验证旧文件。
