# MVP v0.3 Step 05 交接记录

任务：MVP v0.3 Step 05 —— Gate A 首轮结论与问题归因。汇总冻结实验（`erun_d35a33ffc18d6c1f`）
的四层结果，给出 Go / Conditional Go / No-Go 结论，并把每类失败定位到案例、层级、模块与记录。
本步**只分析裁决**：未修改任何产品代码、评测代码、数据集、标注、协议或评分。

---

## 完成内容

1. **四层分开报告**（`evaluation/reports/gate_a_initial.md`）：L1/L2 仅 System B、
   L3 双系统同口径、L4 人工盲评；单轮/多轮分开；每项给出均值 + 分布 + 样本量 +
   三态计数；成功与失败并列；不合并为单一总分。
2. **十条证据线逐条落地**（每条都追到文件 + record_id）：
   - L1 8 次违例 → s08-pin-unpin-001 rep1 t5 / rep2 t3，`CLEAR subject.count` +
     `CLEAR subject.pose_action` 被接受，值与 Resolution 被移除；**产品缺陷**（intent_engine），
     指标实现（`metrics/state.py:126-189`）与标注口径均正确。
   - 10 次 `workflow.invalid_state` + 1 次 `no_generation_under_review` → 13 个失败轮分 5 组；
     A/B/C/E 组（11 轮）**根因在产品**（提前 ready / 错误具体化 / 越权 CLEAR），
     D 组（s09-multiturn-002 r1 两轮）由 **Provider 连续两次超时**触发；
     **协议 §3 的 kind→入口映射是被动的下游症状，不是根因**（用户文本本身合法，
     v0.2 状态机拒绝是冻结的正确行为），但有健壮性缺口（记入 Q2）。
   - `conflict_detection 0/12` → **产品缺陷双根因**：① 冻结英文词表 vs Interpreter 中文值
     （`decision_policy.py:142-195,248-299`）；② `assess()` 四处调用点均未传
     `ExecutionRevision`，`framing_aspect_mismatch` 是死规则（`engine.py:62,100`、
     `review.py:322,563`）。**Runner 集成无缺陷**（执行上下文由产品自建并已落库，只是没传）。
     6 次严重失败逐例列出（三案例 ×2 重复）。
   - `preservation B 0.691` → **指标口径失真**：重建 33/34 对配对并逐条读 Prompt 文本，
     B 侧 35 次 lost **全部**来自 10 个"修改轮未产出 Prompt"的对（合法澄清或 PIN 拒绝），
     可比较的 23 对 47/47 全保留，与 A 的 87/87 相同。L4 `attribute_preservation` 打平
     与此一致；**既不用 L3 判 B 漂移，也不用 L4 打平掩盖**——本步判定 L3 该指标当前不可解读。
   - `out_of_scope 56 / unset 58` → 35/56 与 50/58 属 `subject.count` 无依据具体化；
     13/56 属 PIN 范围过宽；7/56 属 `environment.location`/`composition.framing` 吞字段，
     且这一族正是 s02/s09 流程错位的上游。
   - `unauthorized_addition B 6 vs A 2、guard_triggered 0` → 6 次命中全部是 s04 三案例
     冲突未检出的下游；guard 是"来源绑定"防护（每条 clause 都能回溯到已确认 Intent，
     0 触发是正确行为），`guard_bypassed` 字段名把两件事混为一谈（记入 Q4）。
   - Go 条件 2/3/4/5 逐条按协议 §6 操作化裁决（见下）。
3. **五条件逐条裁决**：条件 1 不满足（L1 阻断）、条件 2 不满足、条件 3 弱满足
   （仅 Edit Success）、条件 4 不满足、条件 5 部分满足 → **No-Go（可修复型）**。
4. **失败目录**（`evaluation/reports/failure_catalog.jsonl`，11 条）：
   5 条产品缺陷（P1~P5）、1 条 Provider 波动（P6 的根因是 Provider 超时 + 无降级路径）、
   2 条协议/口径缺陷（Q1 preservation 配对、Q2 Runner kind 映射）、
   1 条 L4 测量有效性（Q3）、1 条报告字段命名（Q4）、1 条 Baseline 无缺陷确认条目。
   每条含 `classification / root_cause_hypothesis / evidence_refs / impact /
   worth_fixing / suggested_fix_target`。
5. **机器可读结论**（`evaluation/reports/gate_a_initial.json`）：`verdict=no_go`、
   五条件 status + evidence refs、四层关键数值、失败分类计数、下一步与修正目标、
   协议/数据集缺陷的合规修正路径。

## 变更文件

全部**新增**，四个文件，此外零写入：

- `evaluation/reports/gate_a_initial.md`
- `evaluation/reports/gate_a_initial.json`
- `evaluation/reports/failure_catalog.jsonl`
- `docs/handoffs/v0_3_step_05_handoff.md`（本文件）

未修改：`visual_intent_agent/**`、`evaluation/**` 既有文件（含 runner/metrics/protocol/
fixtures/annotations/configs/image_review）、`tests/**`、`docs/task_books/**`、
既有 handoffs、`outputs/**`、`pyproject.toml`、`uv.lock`。

## 公开接口

本步不新增产品/评测接口。可被 Step 06/07 直接消费：

- `evaluation/reports/gate_a_initial.json`：`verdict`、`go_conditions[]`（status + evidence_refs）、
  `layers.{L1,L2,L3,L4}`（关键数值与记录 ID）、`failure_classification_counts[_by_occurrence]`、
  `next_step.fix_targets[]`、`protocol_dataset_defects[]`、`traceability`。
- `evaluation/reports/failure_catalog.jsonl`：每行一个失败类别/案例条目，schema
  `{catalog_id, layer, case_ids, repetitions, classification, root_cause_hypothesis,
  evidence_refs[], impact, worth_fixing, suggested_fix_target}`。

## 测试与实验结果

```text
$ uv run pytest -q
2086 passed, 3 deselected in 6.48s      # 与 Step 04 基线一致 → 本步零代码改动
```

关键裁决数值（原始文件为权威，全部经本步独立复核）：

| 项 | 值 |
|---|---|
| `gate_a_blocked` | true（s08-pin-unpin-001 r1/r2） |
| L1 四不变量 | 44/44、44/44、44/44、**42/44（8 违例）** |
| L2 | delta 0.884（out_of_scope 56 / unset 58）、missing_decision 0.736（59/72）、clarification 0.852（46 问/9 坏/3 ready 轮）、conflict **0.0（0/12、6 严重）**、delegation 0.864（under 8/over 0） |
| L3 | coverage A 0.978/B 0.967；compat 1.0/1.0；preservation A 1.0(87/0)/B 0.691(47/35，**其中 35 全为无 Prompt 对**)；unauthorized_addition A 2/B 6（guard_triggered 0/0） |
| L4 | preference A 15/B 12/2 平；intent_alignment A 3.138/B 3.321（配对 10:11:7）；attribute_preservation 3.5/3.5（配对 3:3:10）；edit_success A 3.2/B 3.6（配对 3:4:3） |
| 运行 | B 33 completed / 11 failed（13 失败轮）；A 44/44 completed；12 次 `provider.timeout` |
| 复现 | L1 结论一致；missing_decision_recall 反向（rep1 30/36 vs rep2 29/36）→ 条件 5 部分满足 |

## 原始 Artifact 位置

- 原始 A/B 运行：`outputs/evaluation_runs/erun_d35a33ffc18d6c1f/`
  （`summary.json`、`cases/*.jsonl`、`baseline_a/rep{1,2}/**`、`system_b/rep{1,2}/**`、179 PNG）
- L4 报告与原始评分：`evaluation/reports/erun_d35a33ffc18d6c1f_layer4.json`、
  `evaluation/reviews/erun_d35a33ffc18d6c1f/{review_manifest.json,unblinding_map.json,scores/}`
- 本步交付：`evaluation/reports/gate_a_initial.{md,json}`、
  `evaluation/reports/failure_catalog.jsonl`、本文件

## 已知限制

1. **未重跑任何真实 Provider**（任务书硬约束）：所有结论来自既有 Artifact 的离线只读分析；
   Provider 波动的幅度无法从单次 run 估计。
2. **LLM 原始响应未落盘**：只有结构化结果与 `issues`；`detected_conflicts` 的 code 被系统
   统一覆盖为 `interpreter.conflict_detected`（Step 03 已知限制 4），因此**无法**从本次
   Artifact 证明 LLM 是否自报过冲突。
3. **L4 单评审人、29 对、每案例一次成对**：Edit Success/Intent Alignment 的 +0.4/+0.2
   只能作方向性证据，不构成统计功效。
4. **Q1 的"修正后分数"仅作分解陈述**，不写入任何指标记录：本步未改 `metrics/prompt.py`，
   也不主张 0.691 应被替换为其他数字；只声明"可比较对上 47/47 全保留"这一事实。
5. **Q3（L4 与 PIN 须知冲突）的证据是间接的**：只能证明 `scores_user.json#pair_018` 的
   评分与标注 `notes`/`focus` 冲突，无法证明评审人当时看到了什么。
6. 22 案例、2 重复的规模不构成统计功效；任何新结论都应在新版本协议下整案重跑。

## 对下一步的输入

**结论：No-Go（可修复型）→ 建议进入 Step 06；不要进入 Step 08；不建议停止 v0.3。**
Step 06 的启动条件（任务书：Step 05 为可修复的 No-Go 且报告给出可验证修正目标）已满足。

可验证修正目标清单（每条都有失败案例 + record_id + 期望验证口径）：

| 优先级 | 目标 | 模块 | 回归案例 | 成功判据 |
|---|---|---|---|---|
| P0 | 主体删除语义不得把 CLEAR 扩到 `subject.count`/`subject.pose_action` | `intent_engine` | s08-pin-unpin-001 r1 t5、r2 t3 | L1 `unauthorized_fields_unchanged` 44/44 |
| P0 | 冲突谓词能识别系统实际存储的值（中英并列）；`assess` 携带 `ExecutionRevision` | `policy` + `intent_engine`/`workflow` | s04-conflict-001/002/003 t1、s02-missing-core-002 t3、s09-multiturn-001 t3/t4 | 3/3 hard 冲突命中、`severe_failures=0`、execution 冲突可达 |
| P1 | 无依据具体化：`subject.count` 不臆测、`environment.location` 不吞地点描述 | `intent_engine` | s02-missing-core-001 t1/t2、s09-multiturn-002 t1 | 相关 `paths_must_remain_unset` 不再违例；missing_decision_recall 在受影响点上 ≥15/18 |
| P2 | PIN 授权范围与用户表述一致 | `intent_engine` | s07-single-field-002 t2 | out_of_scope 显著下降，UNPIN 不再报 `unpin_requires_pinned_path` |
| P1（需协议所有者批准） | Preservation 不再把"修改轮未产出 Prompt"计为丢失 | 新协议版本 + `evaluation/metrics/prompt.py` | s08-pin-unpin-002 t1→t2、s10-vague-feedback-001 t1→t2 | 无 Prompt 的修改轮记 `missing_data`；已有分数不变 |
| P2 | 复评前把案例 `notes` 的期望行为提示展示给评审人 | `evaluation/image_review.py` | s08-pin-unpin-001 t4 | Step 07 盲评界面含 PIN 语境提示（不重算已有 29 对） |

协议/数据集缺陷的合规修正路径（本步不修改任何冻结物）：

- **Q1**：新建 `evaluation/protocol.md` 新版本 + 新指标版本，明确"修改轮未产出 Prompt
  → `missing_data`（协议 §5 已有该态），不计入丢失分子"；附书面理由，
  **不得为提高分数改口径**，旧版本只读保留。
- **Q2**：可选新版本协议明确"脚本 kind 与当前会话状态不一致时，按该状态允许的入口提交并
  记 `flow_deviation`"；**不得**用于隐藏失败（本次 13 个失败轮全部保留在分母）。
- **Q3**：不改评分实现，只在复评界面补足评审须知。
- **Q4**：报告字段改名/加注（文档级）。

## 是否满足验收条件

**是。** 任务书验收条件逐条核对：

| # | 验收条件 | 核对 |
|---|---|---|
| 1 | 结论与原始数据一致，负面结果完整保留 | 是。所有数字取自 `summary.json` / `cases/*.jsonl` / layer4 报告，经独立重算复核；11 次失败、13 个失败轮、31 条缺图、1 个 null 槽位、0/12 冲突检测全部原样保留并单列，无剔除无美化 |
| 2 | 每项修正建议都引用具体失败证据 | 是。`failure_catalog.jsonl` 11 条与报告 §9 每条修正目标都带案例 ID + `record_id`/文件路径；已脚本校验全部 ID 在原始 Artifact 中真实存在 |
| 3 | 报告明确说明是否允许进入 Step 06 或 Step 08 | 是。§9.4 与本节：建议进入 Step 06，不进入 Step 08，不停止 v0.3 |
| 4 | 只写四个交付文件、零越界 | 是。`uv run pytest -q` → 2086 passed（与 Step 04 基线一致）；未改任何代码/数据/冻结物/既有 handoff；分析脚本全在 `/tmp` |
