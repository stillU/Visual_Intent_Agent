# Gate A 评测协议 r1（MVP v0.3 · R1-B 修订版）

- 协议版本：`gate_a_protocol_v0_3_r1`
- 数据集版本：`core_v0_3_r1`
- 配置版本：`gate_a_v0_3_r1`
- 父版本：`gate_a_protocol_v0_3`（`evaluation/protocol.md`，只读保留，继续验证旧冻结物）
- 依据：`docs/task_books/mvp_v0.3/REVISION_001_SHORTEST_PATH.md` §R1-B、
  `docs/task_books/mvp_v0.3/REVISION_002_BUILD_FIRST.md`、A 工包语义裁定
  （室内自然光 / 摄影水彩 / 远景方图 / 摄影棚海滩四条启发式冲突规则停用）
- 地位：r1 正式 A/B 复评的**唯一口径**。r1 冻结后不得为提分修改本协议、数据集、
  标注或配置；任何再次修订生成新版本文件，旧版本只读保留。

本协议**继承**父协议全部未被本文件覆盖的条款（对比系统、Provider 同条件、
四层指标定义、Gate A Go 条件、重复与随机性、失败口径）。本文件只列 r1 的差异，
避免复制后漂移。所有 r1 记录带 `metric_version = "v0_3_r1"`，与 v0_3 记录分开标识。

---

## 1. R1-A 语义裁定在 r1 数据中的落地

r1 fixture/annotation 只修改有**独立语义理由**的部分；未受影响案例逐字保留。
旧 `core_v0_3` 冻结文件不改动，新清单 `frozen_manifest_v0_3_r1.json` 只哈希 r1 文件。

1. **数量**：中文明确数词（"一只/两只/三位"…）允许抽取为 `subject.count` 整数；
   未陈述数量不默认（不补 1）。r1 把出现过明确数词的轮次从
   `paths_must_remain_unset` 移出 `subject.count`，并记入 `acceptable_extra_deltas`
   （允许而非强制）。
   **评测容许两种合法的结构化表达**：抽取结构化 `subject.count`，或省略结构化
   count；但无论哪种，**原用户的明确数量语义必须在可观察 Prompt 中保留**。
   因此 r1 在这些案例的 case 级 `prompt_expectations.must_mention_groups` 中
   增加数量语义组（原短语 / 数字形式 / 明确英文数词如 "one "），由 L3
   Intent Coverage 检查。`interpreter.v5` 要求抽取明确数词是**产品要求**；
   评测层容许“省略结构化 count”是**评测口径**，两者差异不影响“数量内容完全丢失
   必须被罚”——不得出现“抽与不抽都不罚、完全丢失也不罚”。
2. **地点**：明确"地点是客厅"必须提取（原标注已如此）；自由处所短语
   （"壁炉边/沙发上"）可作为场景细节保留，不要求固定句式。
   *未完成细项*：自由处所短语是否**直接解决** `environment.location` 决策取决于
   A 工包最终实现；在无独立确证前 r1 不重写该对话分支，留待 A/R2 真实探针后另起
   标注修订。详见 `evaluation/reports/annotation_revision_log_v0_3_r1.md`。
3. **冲突停用**：移除四条不可证明的启发式 `expected_conflicts`：
   `hard_conflict.environment_mode_location`、
   `hard_conflict.lighting_environment_source`、
   `hard_conflict.style_medium_mismatch`、
   `execution_conflict.framing_aspect_mismatch`。
   s04 三例不再是冲突案例，其 t2 由 `clarification_answer` 改为 `image_feedback`
   的合法单字段修改轮（必要对话轮次修正）。**r1 核心集当前不含可证明的硬冲突案例**，
   这是明确的能力/覆盖缺口，不伪造新冲突。
4. **CLEAR/PIN**：冻结的局部 CLEAR/PIN 约束不放宽，安全不变量测试不得为通过而放宽。

### 1.1 产品语义修订版本（记实，A/B 工包交付）

r1 数据与口径对应以下产品侧版本；正式 Run 的 `code_version` / commit / dirty 状态由
Runner 身份记录，不在此伪造：

| 组件 | 版本 | r1 相关语义 |
|---|---|---|
| Interpreter prompt | `interpreter.v5` | 中文明确数词可抽取、自由处所短语表达、不靠词表脑补房间 |
| Decision policy | `policy.v2` | 四条启发式冲突规则 predicate 停用（合同保留，不再阻断） |
| Feedback prompt | `feedback.v4` | 模糊反馈不猜值、CLEAR/PIN 边界与数量/处所规则同步 |

旧 v0_3 报告与旧冻结物不动；r1 结论只在本表声明的版本下成立。

## 2. 输入一致性（A/B 同一冻结用户消息）

1. r1 修订集对 A/B **逐字使用同一份预先冻结的用户消息**：每个
   `clarification_answer` 轮携带的 `user_text` 即两系统共同输入；fixture 中
   `answer_variants` 字段在 r1 **不允许出现**，出现即拒绝启动（防止动态回答混入正式比较）。
2. Runner 记录 `input_text` 与 `input_source`；r1 正常路径恒为 `user_text`。
   需要改变用户消息才成立的路径记 `flow_deviation = true`，不静默改写、不替用户作答。
3. 动态回答（依赖待答问题 target_path 选中）只保留给**独立交互诊断**，不进入正式 A/B。

## 3. 级联失败：首次根因与后续 blocked 分开

1. 某案例某重复中出现**首个**执行失败轮（`status = "failed"`，携带 `error`）时，
   该轮即首次根因，记录完整 error code 与 stage。
2. 其后脚本中无法执行的轮次**不再驱动**，逐轮记 `status = "blocked"`、
   `blocked_by_prior_failure = true`、`blocked_by_turn_id = <首个失败轮 ID>`；
   不把后续每轮重复记成独立根因。
3. **不自动把澄清答案改送反馈入口**：脚本 `clarification_answer` 仍走
   `WorkflowService.submit_message`；入口映射只由 `turns[].kind` 决定。
4. 案例失败与级联阻断仍进入总完成率；首次根因与 blocked 计数分开报告。

## 4. L3 指标修订

### 4.1 Preservation（r1）

只比较**实际产生的相邻有效 Prompt**中"仍被要求保留"的属性：

1. 保留单元按**路径关联**：annotation 的 `preserve_path_groups`
   （path → 截至该轮的最新期望关键词组）。用户显式改掉的路径不再计入保留；
   **跨澄清轮累计合法修改路径**：后续轮以最新值参与保留检查。
2. 逐相邻有效 Prompt 对评估：前轮命中某保留组、本轮未命中 → 记丢失；
   `kept / (kept + lost)` 逐对计算，逐案例取均值。
3. **正常澄清/拒绝轮无 Prompt → `not_applicable`**，不再按"保留 0"保守计。
   只有标注 `expected_outcome = ready_and_generate` 却未产生 Prompt 的轮次才记
   **失败**，并另由 `generation_completion` 指标报告完成率；
   不通过删除分母美化结果。
4. v1 Preservation（`evaluate_preservation`）保留，供旧 Run 复用；r1 Run 使用
   `evaluate_preservation_r1`。

### 4.2 Unauthorized Addition：关键词违例与来源校验分开

1. `forbidden_keyword_hit`：`must_not_mention` 关键词在最终 Prompt 中的命中**计数**
   （纯文本事实；越低越好）。
2. `source_guard_triggered`：真实可观测的 `prompt.unauthorized_addition` 编译期
   防护触发次数（来自实际编译 issue，不由关键词推导）。
3. `source_guard_bypassed`：**只有在存在真实编译 source-guard 被绕过的可观测证据时
   才可以取值**；当前评测记录结构无法观测该事实，故一律为 `None` 并标注
   `not_observable`。**禁止**用“关键词命中且防护零触发”推导 `guard_bypassed`
   （001 §2/§R1-B #8 明令：关键词不是来源校验事实）。
4. 两个口径分别聚合；`forbidden_keyword_hit` 与来源校验失败不得互相替代或相互派生。

### 4.3 指标版本

每条 r1 MetricRecord 记 `metric_version = "v0_3_r1"`；v0_3 记录记 `"v1"`。
新旧指标不得直接拼成"同口径提升"。

## 5. 运行身份与产物路径

1. Runner 增加 `--manifest`：由清单解析协议、配置、数据集与标注路径及哈希；
   未提供时默认旧清单 `frozen_manifest_v0_3.json`，旧行为向后兼容。
   **正式运行必须校验清单内全部哈希**，不得关闭校验导入外部数据。
2. 正式 Run 记录实际 Git `commit` 与工作区 `dirty` 状态；工作区不干净时正式 Run
   拒绝启动。`--diagnostic` 允许诊断运行，但结果显式标记 `identity_mode=diagnostic`
   且不作为正式 Gate 证据。
3. 真实运行默认产物根目录为 `outputs/evaluation_runs/`（盲评 `outputs/evaluation_reviews/`），
   避免二进制图片/SQLite 与 `evaluation/` 的 UTF-8 凭据扫描冲突。
   历史 `evaluation/runs/` 保留，不迁移、不覆盖。
4. 旧 Run 用 r1 重算时另写报告，不覆盖原报告；输入或标注已改变的案例不能靠重算
   成为新协议正式样本。

## 6. L4 盲评（r1 补充）

1. 配对按"用户请求已推进到同一阶段"成对；允许 A/B 不同澄清轮数，无法匹配的单元
   单独记 `missing_data`，不硬凑。
2. 盲评页必须呈现：**完整相关用户文本**（该轮及此前所有用户输入原文）、
   **必须保留内容**（该轮 `forbidden_change_paths` 对应的用户约束）与
   **目标改动**（该轮 `expected_deltas` 描述）；不暴露系统标签与内部 ID。
3. 保留 `user_preference`（自由偏好）与 `edit_success`/指令遵守两个独立评分；
   PIN 情况下偏好删除后的图片可以记录，但不能当作遵守保护约束的成功。
4. 自动确认仅是实验代理，不算验证真实用户确认体验；至少两名独立评审，分歧保留。

## 7. Gate A 判定规则状态（正式 Run 门禁）

r1 协议**尚未**冻结量化改善阈值、最小实际改善幅度、等待/澄清成本上限与
缺失结果处理方式；这些必须在正式复评（R3）开始前冻结，不得看到结果后补定。

该状态是**代码级门禁**而不是文档声明：当身份为 `formal` 且协议为 r1 时，Runner
读取配置 `metrics.gate_thresholds.status`；只要不是 `frozen`，
`EvaluationRunner.run()` 在驱动任何案例前直接拒绝启动。诊断 Run（`--diagnostic`，
`identity_mode=diagnostic`）允许运行，结果显式标记且不作为正式 Gate 证据。
本轮不新增正式阈值，也不做付费复评。

## 8. 冻结与修订

- r1 冻结物：本协议、`fixtures/core_v0_3_r1.jsonl`、`annotations/core_v0_3_r1.jsonl`、
  `configs/gate_a_v0_3_r1.json`；其 sha256 固化于 `frozen_manifest_v0_3_r1.json`。
- 旧冻结物（`protocol.md`、`core_v0_3` fixture/annotation、`gate_a_v0_3.json`、
  `frozen_manifest_v0_3.json`）继续只读保留，旧测试继续验证旧文件。
- 修订规则：任何内容变化生成新版本文件与新清单，不得覆盖。
