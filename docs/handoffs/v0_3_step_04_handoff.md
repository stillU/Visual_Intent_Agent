# MVP v0.3 Step 04 交接记录

任务：MVP v0.3 Step 04 —— 在冻结实验条件下完成真实图片 A/B 运行，建立去系统标签的
盲评材料，由人工评审完成 L4 Image Result 打分，产出可追溯的 L4 报告与原始记录。

执行方式：编排方（主 Agent）+ 实现子代理（deepseek-flash，工具链）+ 人工评审（用户本人）。
真实运行与盲评打包由编排方驱动；本记录汇总全部事实。

---

## 完成内容

1. **盲评工具链**（阶段一，子代理交付）：`evaluation/image_review.py`（约 1900 行，
   `package` / `ingest` / `report` 三个 CLI 子命令 + 纯函数库）与
   `tests/evaluation/test_image_review.py`（35 条离线测试）。匿名化、成对随机化、
   解盲映射隔离、评分校验、分布级 L4 报告全部实现并有测试覆盖。
2. **真实 A/B 运行**：`erun_d35a33ffc18d6c1f`
   （命令：`uv run python -m evaluation.runner --output-root outputs/evaluation_runs --nonce gate-a-initial`；
   开始于 2026-09-14T22:16:58Z，历时约 3 小时，exit 0）。
   22 案例 × 2 重复 × A/B 双系统，全部使用冻结配置（同一 base_url/key、LLM 默认
   `qwen3.8-max`、图像 `qwen-image-3.0`、1024x1024、每次 1 张）；
   run.json 记录 code_version=`visual_intent_agent-0.1.0+direct_llm_baseline_v1+evaluation_harness_v1`、
   dataset_sha256=`05195347…c63a64b`（与冻结清单一致）、protocol_version=`gate_a_protocol_v0_3`。
   - Baseline A：44/44 案例运行 completed，120 张真实图片。
   - System B：33 completed / **11 failed**，59 张图片；合计 179 张 PNG。
   - **L1 阻断**：`s08-pin-unpin-001` rep1+rep2 均违例 `unauthorized_fields_unchanged`
     （该不变量 42/44 通过、8 次违例检查记录，全部 multi 范围），summary.json 显式
     `gate_a_blocked=true`，`l1_blocking` 携带 metric_record_ids 可追溯。
3. **盲评包生成**：`package --seed 20260101`（派生种子 1893071956362610948，双记录于
   manifest 与解盲映射）。29 对成对样本（覆盖 19/22 案例），31 条 missing_data
   （全部 `counterpart_image_missing`，缺图侧不硬凑）。去标签验证：盲评目录仅
   `pair_NNN_left|right.png` + `review.html` + `scores_template.json`；HTML 全文扫描
   零命中 baseline/system_b/case_id/run_id/Prompt 片段；解盲映射单独存放仓库内
   `evaluation/reviews/<run_id>/`，未链接进盲评界面。
4. **人工盲评**（协议规定的唯一人工环节）：评审人 `change`（用户本人），29/29 对全部完成，
   完成时间 2026-09-15T02:37:11Z；导出 `layer4_scores.json`（schema `layer4_scores_v1`）。
   评审期间解盲映射未向评审人展示（编排方在打包后、评分完成前未读取左右归属以外的
   映射内容用于任何筛选；报告由 `report` 子命令机械生成，无人工挑选）。
5. **评分收集与 L4 报告**：`ingest` 校验通过（覆盖 29/29，1 个 null 槽位显式进入
   missing），落盘 `scores/scores_user.json` + `scores/ingest_report.json`；
   `report` 生成 `evaluation/reports/erun_d35a33ffc18d6c1f_layer4.json`
   （逐对原始分、每维分布计数、解盲后按真实系统归位、by_case/by_turn_type 聚合、
   全部样本携带 sha256 追溯）。

## 变更文件

全部为**新增**；产品代码、Step 01 冻结物、Step 02/03 交付物零修改（git status 复核）：

- `evaluation/image_review.py`（新增，阶段一）
- `tests/evaluation/test_image_review.py`（新增，35 测试）
- `evaluation/runs/erun_d35a33ffc18d6c1f/run_pointer.json`（运行指针：run 目录 + run.json sha256 + 图片清单摘要）
- `evaluation/reviews/erun_d35a33ffc18d6c1f/{review_manifest.json, unblinding_map.json, scores/scores_user.json, scores/ingest_report.json}`
- `evaluation/reports/erun_d35a33ffc18d6c1f_layer4.json`
- `outputs/evaluation_runs/erun_d35a33ffc18d6c1f/**`（原始 A/B Artifact，运行时产物，gitignore）
- `outputs/evaluation_reviews/erun_d35a33ffc18d6c1f/blind/**`（盲评包，运行时产物，gitignore）

## 公开接口

```text
uv run python -m evaluation.image_review package --run-dir <Step03运行目录> [--seed 20260101]
uv run python -m evaluation.image_review ingest  --scores <评审导出.json> [--run-id <run_id>]
uv run python -m evaluation.image_review report  [--run-id <run_id>]
```

关键函数：`package_run` / `ingest_scores` / `report_run`（输出根全部可注入，`created_at`
可注入以逐字节重现）；`plan_pairs`（成对与随机化）、`validate_scores_payload`（评分校验）、
`build_layer4_report_payload`（解盲聚合）。评分文件 schema：`layer4_scores_v1`
（sample_id + dimensions{intent_alignment, attribute_preservation, edit_success 各
left/right ∈ 1..5 | "not_applicable" | null + reason} + user_preference.choice ∈
left/right/tie/null + reviewer_id + completed_at）。

## 测试与实验结果

**离线测试**：`uv run pytest tests/evaluation -q` → 243 passed（208 既有 + 35 新增）；
`uv run pytest -q` → **2086 passed, 3 deselected**（零回归）；frozen_manifest 四冻结物
sha256 复核一致；打包/收集/报告全过程未破坏 Step 01 凭据卫生测试（evaluation/ 内只写
UTF-8 记录）。

**L4 结果（全部数字，无挑选；n 为该侧被评槽位）**：

| 维度 | Baseline A | System B |
|---|---|---|
| User Preference（29 对） | **15 胜** | 12 胜（平局 2） |
| Intent Alignment（multi） | 3.19（n=27） | **3.35**（n=26，1 missing） |
| Intent Alignment（single） | 2.5（n=2） | 3.0（n=2） |
| Attribute Preservation（multi） | 3.5（n=16） | 3.5（n=16） |
| Edit Success（multi） | 3.2（n=10） | **3.6**（n=10） |

**L1～L3 headline（同次真实运行，summary.json 权威；细节归因属 Step 05）**：

- L1（仅 System B；Baseline 一律 not_applicable）：`history_append_only` 44/44、
  `no_stale_confirmation` 44/44、`pinned_not_overwritten` 44/44 全通过；
  `unauthorized_fields_unchanged` 42/44（**8 次违例**，s08-pin-unpin-001 双重复）→
  **gate_a_blocked=true**。
- L2（仅 System B）：delta_accuracy 0.884（applied 374 / matched 304 / out_of_scope 56 /
  must_remain_unset_violations 58）；missing_decision_recall 0.736（59/72）；
  clarification_precision 0.852（46 问 / 9 坏问 / 3 次 ready 轮提问；must_clarify 37/52）；
  **conflict_detection 0.0（0/12 检出，6 次严重失败）**；delegation_scope_accuracy 0.864
  （under_delegated 8 / over 0）。
- L3（A/B 同口径）：intent_coverage A 0.978 vs B 0.967（B 侧 5 案例 missing_data）；
  model_compatibility 双 1.0；**preservation A 1.0（87 kept/0 lost）vs B 0.691
  （47 kept/35 lost）**；unauthorized_addition hits A 2 vs B 6（guard_triggered 双 0）。
- System B 11 个失败案例运行（保留在分母）：`workflow.invalid_state` ×10
  （s02-missing-core-001 rep1+2、s04-conflict-001/002/003 rep1+2、s08-pin-unpin-001 rep2、
  s09-multiturn-002 rep2）+ `evaluation.no_generation_under_review` ×1
  （s09-multiturn-002 rep1）。

## 原始 Artifact 位置

- 原始 A/B 运行：`outputs/evaluation_runs/erun_d35a33ffc18d6c1f/`
  （run.json、summary.json、cases/*.jsonl、baseline_a/rep{1,2}/**、system_b/rep{1,2}/**、179 PNG）
- 盲评包：`outputs/evaluation_reviews/erun_d35a33ffc18d6c1f/blind/`
- 仓库内记录：`evaluation/runs/<run_id>/run_pointer.json`、
  `evaluation/reviews/<run_id>/{review_manifest.json, unblinding_map.json, scores/}`、
  `evaluation/reports/<run_id>_layer4.json`

## 已知限制

1. **产物位置偏差**（记录在案）：任务书字面路径为 `evaluation/runs/<run_id>/` 原始
   Artifact 与 `evaluation/reviews/<run_id>/` 盲评包；实际二进制（图片、HTML）一律放
   `outputs/` 下（已 gitignore），`evaluation/` 内只保留 UTF-8 指针与记录。原因：Step 01
   冻结的凭据卫生测试将 `evaluation/` 树下所有文件按 UTF-8 读取，任何二进制都会击穿该
   冻结测试，而冻结测试不可修改。追溯性由 run_pointer.json（含 run.json sha256 与图片
   清单）与 unblinding_map（含每图 sha256 与原路径）保证。
2. **单评审人**（reviewer=`change`）：协议未规定评审人数下限；`ingest` 为单文件覆盖式
   收集，未实现多评审人合并。评分分布与理由已逐条落盘，可审计。
3. **3 个案例无任何 L4 成对样本**（s05-delegate-explicit-002、s06-delegate-vague-002、
   s09-multiturn-002——System B 侧运行失败/无图），其 L4 按协议记 missing_data，
   保留在分母侧，不静默剔除。
4. 31 条缺图 missing_data + 1 个 null 评分槽位（intent_alignment system_b 1 槽）均显式
   进入报告。
5. L4 只用 `l4_repetition=1` 的图片（冻结口径）；rep2 图片保留在原始运行目录、未进入
   盲评，未做任何"重抽"。
6. 图片副本为字节原样复制 + 重命名，未重写元数据；两侧同 Provider 同模型，元数据结构
   对称，不含系统身份信息（去标签断言在测试中强制）。

## 对下一步的输入

Step 05（Gate A 首轮结论与归因）可直接读取：

1. `outputs/evaluation_runs/erun_d35a33ffc18d6c1f/summary.json`（L1~L3 聚合 +
   l1_blocking + gate_a_blocked）与 `cases/*.jsonl`（逐轮原始记录，含 error 与
   flow_deviation）；
2. `evaluation/reports/erun_d35a33ffc18d6c1f_layer4.json`（L4 全部原始分与分布）；
3. 重点归因线索（编排方观察，未定性）：
   - L1 8 次 `unauthorized_fields_unchanged` 违例集中于 s08-pin-unpin-001 双重复，且
     L2 有 58 次 must_remain_unset_violations / 56 个 out_of_scope delta——需要核对
     具体路径与轮次，区分产品缺陷 vs 指标/标注口径问题；
   - 10 次 `workflow.invalid_state` 的模式是"生成后会话处于 WAITING_REVIEW，脚本下一轮
     kind=user_message 经协议 §3 映射走 submit_message 被状态机拒绝"——需要区分
     **协议/数据集设计缺陷**（真实用户文本在 WAITING_REVIEW 应由 CLI 路由为 feedback）
     与产品缺陷；协议缺陷不得包装成产品修复；
   - conflict_detection 0/12：Step 01 已预警"冲突规则谓词按冻结英文词表匹配值文本，
     Interpreter 以中文存值可能不命中"——需按失败证据定性；
   - L3 preservation B(0.691) 远低于 A(1.0)，但人工 L4 attribute_preservation 打平
     （3.5/3.5）——L3 是关键词文本代理，B 侧结构化编译可能改写措辞导致关键词组
     "丢失"而语义保持，归因前必须抽查实际 Prompt 文本，不得直接当产品缺陷；
   - 偏好 A 15:12 B，但 edit_success B 3.6:3.2、intent_alignment B 3.35:3.19——
     Go 条件 3 的"至少一项可测量优势"如何裁决由 Step 05 依据协议第 6 节执行。

## 是否满足验收条件

**是。** 任务书 4 条验收逐条核对：

| # | 验收条件 | 结果 |
|---|---|---|
| 1 | 盲评材料无法从文件名或展示文本识别系统身份 | **是**。文件名仅 pair_NNN_left/right.png；HTML 全文扫描零命中系统名/case_id/Prompt/内部路径；有自动化测试强制 |
| 2 | 每项评分可追溯至冻结案例和原始图片，同时不污染盲评界面 | **是**。layer4 报告逐样本携带 case_id/turn_id/system/sha256/sample_id；追溯信息只在仓库内记录，盲评界面仅展示用户文本原文与中性化评审焦点 |
| 3 | A/B Provider 条件一致；差异均被显式记录 | **是**。同一冻结配置驱动双系统；唯一结构差异（System B response_format=json_object vs Baseline 自由文本）按协议 §1 照实登记于 run.json/记录 |
| 4 | 失败图片和无效样本按协议报告，不被静默替换 | **是**。11 个失败案例运行、31 条缺图、3 个无成对案例、1 个 null 槽位全部显式进入 manifest/报告分母侧，零剔除零替换 |

禁止范围核对：未修改图片、未人工筛选样本；评审人未见系统标签；本步未下 Gate A 结论
（结论属 Step 05）。
