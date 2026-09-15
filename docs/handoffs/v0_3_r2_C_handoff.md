# v0.3 R2 工包 C 交接：r1 评测最小修订

- 日期：2026-09-16
- 范围/所有者：工包 C —— `evaluation/**` 与 `tests/evaluation/**`（独占写范围）。
  产品源码 `visual_intent_agent/**` 与其他测试目录**未改动**（工作区中的产品改动来自
  A/B/D 工包）。
- 基线快照：Git HEAD `6f7ca17ed5fbd84cdbf4927b33dd9d409035be39`（未提交，本轮不创建提交）。
- 依据：`docs/task_books/mvp_v0.3/REVISION_001_SHORTEST_PATH.md` §R1-B、
  `REVISION_002_BUILD_FIRST.md` 工包 C、A 工包语义裁定。

## 1. 交付物

### 新冻结数据/文档（旧文件只读保留）

| 文件 | 说明 |
|---|---|
| `evaluation/protocol_v0_3_r1.md` | r1 协议 `gate_a_protocol_v0_3_r1`；继承父协议未覆盖条款，只列差异 |
| `evaluation/configs/gate_a_v0_3_r1.json` | r1 配置 `gate_a_v0_3_r1`；真实产物根 `outputs/evaluation_runs`、身份门禁、输入策略、指标版本 |
| `evaluation/fixtures/core_v0_3_r1.jsonl` | 22 例；`parent_case_id`；s04 必要轮次修正；无 `answer_variants` |
| `evaluation/annotations/core_v0_3_r1.jsonl` | `parent_case_id` / `annotation_revision_reason` / `preserve_path_groups`；移除四条启发式冲突 |
| `evaluation/frozen_manifest_v0_3_r1.json` | r1 清单（roles + 全量 sha256）；只哈希 r1 文件 |
| `evaluation/reports/annotation_revision_log_v0_3_r1.md` | 逐类修订理由、受影响案例、未完成细项 |
| `evaluation/tools/build_core_v0_3_r1.py` | 确定性生成器（只读旧冻结文件，不读系统输出） |
| `evaluation/tools/freeze_manifest_v0_3_r1.py` | r1 清单生成器 |

r1 清单哈希（`frozen_manifest_v0_3_r1.json`）：

```
protocol     dcfe6b95c6f2806fec07e830bd3f8af2557573b2c1f18453321229548df15066
config       2733486c9169cf6b5bf1512f1ac74f00f860a3a1445fbacf9f05a715b290fc5d
dataset      3e1db943b96b40140d2a4d86b966bdfc27daaa48865a683dd3b05d35e7e4b7c6
annotations  a5bd3206bc39cba19508b8a3ca4526b66e8abbb18aaa92e83cb906bbb265d648
```

（权威值以 `evaluation/frozen_manifest_v0_3_r1.json` 的 `files` 为准；任何改动都会
被 `tests/evaluation/test_r1_manifest.py` 捕获。）

### 代码（`evaluation/**`，全部向后兼容）

| 文件 | 变更 |
|---|---|
| `evaluation/manifest.py`（新） | 清单解析/角色解析/全量哈希校验；旧清单无 `roles` 时按默认路径推断 |
| `evaluation/identity.py`（新） | Git commit/dirty 解析、`code_version` 合成、正式运行门禁；Git 不可用记 null 不伪造 |
| `evaluation/reporting.py` | 新增 `blocked` 轮状态与 `blocked_by_prior_failure`/`blocked_by_turn_id`；`metric_version`（记录/聚合/Run）；`code_commit`/`code_dirty`/`identity_mode`；`parent_case_id`/`annotation_revision_reason`/`preserve_path_groups`；`SystemBTurnObservation.recoverable_failure`/`failure_codes`；`L3_R1_METRICS` |
| `evaluation/metrics/prompt.py` | `evaluate_preservation_r1`（路径关联、澄清无 Prompt 记不适用、失败单独计）、`evaluate_generation_completion`、`evaluate_unauthorized_addition_r1`（关键词命中与来源防护分开）；v1 函数保留 |
| `evaluation/runner.py` | `--manifest` / `--diagnostic`；默认产物 `outputs/evaluation_runs/`；身份记录与正式门禁；r1 拒绝 `answer_variants`；首次根因→后续 `blocked`；r1 指标与 `metric_version` 装配；`protocol_version` 入 run_id |
| `evaluation/direct_baseline.py` | `DirectBaselineRunner(protocol_version=...)`，r1 嵌套 Run 不再冒充 v0_3 协议 |
| `evaluation/models.py` | `BaselineCase.parent_case_id` 可选字段（旧 fixture 兼容） |
| `evaluation/image_review.py` | `case_review_constraints` + 盲评页“必须保留 / 目标改动”区块与样式；完整用户文本沿用 `case_background_lines` |

### 测试（`tests/evaluation/**`，全离线）

| 文件 | 覆盖 |
|---|---|
| `test_r1_metrics.py` | Preservation 不误罚澄清轮、失败单独计与完成率、跨澄清合法修改、关键词/来源防护分离、v1 接口保留 |
| `test_r1_manifest.py` | r1/旧清单解析与哈希校验、篡改/缺失/角色未冻结拒绝、r1 数据修订语义（冲突停用、数量允许、s04 轮次、无 variants、保留词组） |
| `test_r1_runner.py` | 指标版本记录、v1 默认不变、`answer_variants` 拒绝、首次根因→blocked 级联、正式身份拒绝 dirty / 诊断标记、`--manifest` 解析与哈希校验、CLI `--list-cases --manifest` |
| `test_r1_blind_review.py` | 完整相关用户文本、必须保留 + 目标改动呈现、accept 轮 12 路径保留 |

## 2. 主代理纠正（已落地）

1. **来源校验不得由关键词派生**（001 明令）：
   `evaluate_unauthorized_addition_r1` 只报告 `forbidden_keyword_hit`（纯文本计数）与
   `source_guard_triggered`（真实编译 issue 计数）；`source_guard_bypassed` 仅在存在
   真实编译 source-guard 绕过证据时取值，当前记录结构不可观测 → 恒 `null` +
   `not_observable`，**禁止**用“命中且防护零触发”推导。protocol §4.2、config
   `guard_naming`、测试与错误命名同步纠正。
2. **数量语义必须保留**：容许抽取结构化 `subject.count` 或省略结构化 count 两种合法
   表达，但在明确数词案例的 case 级 `prompt_expectations.must_mention_groups` 增加
   数量语义组（原短语 / 数字形式 / 明确英文数词），由 L3 Intent Coverage 检查；
   “抽与不抽都不罚、完全丢失也不罚”被禁止。protocol §1、修订日志 §1.1、r1 数据同步。
3. **阈值未冻结 → 正式 Run 代码级拒绝**：`EvaluationRunner._enforce_formal_prerequisites`
   在 `identity_mode=formal` 且 r1 时读取 `metrics.gate_thresholds.status`，非 `frozen`
   直接抛 `FormalRunBlockedError`（`--diagnostic` 允许并标记）。本轮不新增阈值、不付费复评。
4. **Preservation 定点修正**（主代理读码反馈）：
   - 正常澄清/拒绝无 Prompt 一律计入 `no_prompt_not_applicable`，不再要求
     `preserve_path_groups` 非空；
   - `PromptSequenceTurn` 新增 `changed_paths`（来自标注 `expected_deltas` 的 SET/CLEAR），
     `evaluate_preservation_r1` 跨无 Prompt 轮累计，在下一个有效相邻 Prompt 对比较前
     排除对应路径并清空，不再依赖“最新关键词恰好在前轮不命中”碰巧跳过；
   - `build_core_v0_3_r1.derive_preserve_path_groups` 对 CLEAR 执行 `latest.pop`，
     合法删除的旧值不再跨澄清轮被要求保留；
   - 回归：`test_r1_metrics.py` / `test_r1_manifest.py` 新增 3 个定点用例；
     r1 数据未变，清单哈希不变。
5. **正式模式不可关闭冻结校验**（主代理最后一处门禁洞）：
   `EvaluationRunner.__init__` 在 `identity.mode=formal` 且 `verify_frozen=False` 时
   **构造即拒绝**（`FormalRunBlockedError`），不再只靠注释声明；
   `identity=None`/`unspecified`（合成测试）与 `diagnostic` 兼容。
   回归：`test_r1_runner.py::TestIdentity::test_formal_construction_rejects_verify_frozen_false`。

## 3. 本地验证（仅局部离线，符合工包边界）

```
# 主体交付后（preservation 定点修正前）的局部全量：
.venv/bin/python -m pytest tests/evaluation -q -p no:cacheprovider   → 284 passed
# preservation 定点修正后（按要求只跑 r1 相关新增测试）：
.venv/bin/python -m pytest tests/evaluation/test_r1_*.py -q          → 44 passed
```

- preservation 定点修正只改了 r1 指标函数、Runner 的 r1 序列装配与生成器 CLEAR 处理；
  r1 数据未变、清单哈希不变；主代理将做唯一集中全量验收。
- **未执行**任何真实 Provider、付费图片批次、全量测试或人工盲评；未创建 Git 提交。
- 额外端到端冒烟（离线、Fake Provider、临时目录）：用 `--manifest` 指向真实
  `evaluation/frozen_manifest_v0_3_r1.json`、`verify_frozen=True` 运行
  `s01-complete-001` / `s04-conflict-001`，验证清单解析、校验、r1 指标装配、
  级联 blocked 均可用。
- 全仓静态语法检查 `python -m compileall`（evaluation / tests / visual_intent_agent）
  退出码 0。

## 4. 与 A/B 版本的对应（记实）

| 组件 | 版本 |
|---|---|
| Interpreter prompt | `interpreter.v5` |
| Decision policy | `policy.v2`（四条启发式冲突规则 predicate 停用，合同保留） |
| Feedback prompt | `feedback.v4` |

r1 数据/口径只在这些版本下成立；旧 v0_3 报告与旧冻结物未改动。

## 5. 未完成细项（明确列出，不伪造完成）

1. **自由处所短语语义**："在沙发上/壁炉旁"是否直接解决 `environment.location` 决策，
   取决于 A 的 `interpreter.v5` 最终行为；r1 未据此重写对话分支（如
   `s02-missing-core-001`）。需要时另起 r1.x 标注修订并记录独立理由。
2. **核心集无真冲突案例**：四条启发式冲突停用后，`conflicting_requirements` 三例
   实测为“混合但相容”，r1 保留原 scenario 标签并记为覆盖缺口，不伪造新冲突。
3. **Gate A 判定阈值未冻结**：改善指标、最小改善幅度、等待/澄清成本上限、缺失结果
   处理必须在 R3 开始前冻结，不得看到结果后补定。该状态已由代码级门禁强制
   （`FormalRunBlockedError`），不是文档声明；本轮不新增阈值。
4. **真实 22 例 × 2 × A/B 正式 Run 未执行**：本轮不做真实评测；正式 Run 需干净
   工作区且阈值 `frozen`（`--diagnostic` 另说）。
5. **盲评阶段配对**：当前按“同案例同脚本轮序”成对（A/B 澄清轮数不同时多出侧记
   `missing_data`）。已满足“允许不同澄清轮数、无法匹配单独统计”；更细的“语义阶段”
   等价类（如把不同轮数的同一澄清阶段对齐）留待 R3 盲评规程冻结时决定。
6. **`evaluation/runs/` 历史产物**未迁移到 `outputs/evaluation_runs/`；旧记录只读保留。

## 6. 稳定收集点

工包 C 的 `evaluation/**` 与 `tests/evaluation/**` 已到达**可收集稳定点**：
`tests/evaluation` 局部全绿（284 passed），公开接口向后兼容，旧冻结文件与旧测试均未
改动。主代理可在此点开始集中验收；如 A/B/D 后续再改产品接口，需重跑
`tests/evaluation` 并核对 r1 清单哈希。
