# v0.3 后置修复批次交接记录（post_v0_3_fixes_001）

任务依据：`docs/task_books/post_v0.3_fixes/README.md`（v0.3 后置修复任务书，独立批次，
不属于 v0.3 原任务书，也不代表新功能版本发布）。

批次范围：只修复审查已复现的 F1（Preservation 比较基准）、F2（级联阻断轮完成率分母）、
F3（CLI 历史 Prompt 过期重试提示），并完成离线回归与 harness 实现版本升级。
不开展正式评测、真实探针、付费图片生成、人工盲评或 Gate A 判定；不实现 RAG。

执行方式：编排方 + 实现子代理（前序实现）+ 本审查子代理（终审、最小修正、harness 版本
升级、交接）。本记录汇总实际事实。

---

## 1. 实际 HEAD 与工作区状态

- 仓库：`/home/change/projects/image_system`；分支 `feature`。
- 实际 HEAD：`74172435d82ecf8bbc741246f266dd4d5d28433a`（短号 `7417243`，
  `feat: deliver v0.3 engineering preview and r1 evaluation`）。
- 审查基线（任务书记录）：同一提交；修复前离线测试为 `2215 passed, 3 deselected`，
  该数字是修复前记录，不是本批次验收结果。
- 工作区：未提交、未推送、未 stash；`git diff --check` 通过（exit 0，无空白/格式问题）。
- 未修改冻结物：`evaluation/annotations/`、`evaluation/configs/`、`evaluation/fixtures/`、
  `evaluation/protocol*.md`、`evaluation/frozen_manifest_*.json`、`evaluation/runs/`、
  `evaluation/reports/`、`outputs/` 全部零改动；历史 Run（`outputs/evaluation_runs/*`
  的 `run.json` 仍为 `evaluation_harness_v1`）只读保留，未重算、未覆盖。

### 1.1 初始工作区用户文档改动（本批次开始前既存，非修复实现）

以下为用户在初始工作区已有的文档改动，本批次原样保留、未改写：

- `docs/task_books/README.md`：新增“当前执行入口”段落与 `post_v0.3_fixes` 索引项。
- `docs/task_books/post_v0.3_fixes/`（新增目录，untracked）：本批次任务书
  `README.md`，含实施范围、验收用例、变更边界与 Agent 派发指令。

### 1.2 本批次代码/测试改动（F1/F2/F3 + harness）

- 实现：`evaluation/metrics/prompt.py`（F1）、`evaluation/runner.py`（F2）、
  `visual_intent_agent/cli.py`（F3）、`evaluation/reporting.py`（harness 版本升级）。
- 测试：`tests/evaluation/test_r1_metrics.py`、`tests/evaluation/test_r1_runner.py`、
  `tests/cli/test_cli_e2e.py`、`tests/cli/cli_helpers.py`、
  `tests/evaluation/test_reporting.py`、`tests/evaluation/test_image_review.py`。

---

## 2. F1 · Preservation 比较基准

### 2.1 复现（以 HEAD 实现为准，确定性，非模型运行）

用 HEAD 的 `evaluation/metrics/prompt.py` 源码对任务书最小场景直接调用：

- t1 生成 `cat watercolor`，`changed_paths=["subject.description","style.primary"]`；
- t2 只合法改光，要求保留 `cat`/`watercolor`，实际生成 `dog watercolor`。

实测修复前：`evaluate_preservation_r1` 返回 `status=not_applicable, score=None`（零比较对）。
根因与任务书一致：`pending_changed` 仅在成功构造比较对时清空，t1 的初始 SET 长期滞留，
t2 的两个保留路径被当作“本轮合法修改”排除，`groups` 变为空 → 不构成比较对。

### 2.2 修复

`evaluation/metrics/prompt.py::evaluate_preservation_r1`：每个有效 Prompt 一律
“先完成可用比较，再无条件设为新基准并清空 `pending_changed`”；

```python
if groups and previous_with_prompt is not None:
    pairs.append((previous_with_prompt, turn, groups, sorted(pending_changed)))
previous_with_prompt = turn
pending_changed.clear()          # 无条件，是否存在保留标签/是否成对都不阻止
```

累计窗口仍为“上一个有效 Prompt 之后（含当前轮）的合法 SET/CLEAR”；无 Prompt 轮继续累计，
正常澄清不计内容丢失；失败计数与未知值语义未变。旧 v1 `evaluate_preservation` 未改动。

修复后同一场景：`status=ok, score=0.5, groups_kept=1, groups_lost=1`，
`excluded_changed_paths=["lighting.character"]`；完整保留场景 `score=1.0`。

### 2.3 回归（`tests/evaluation/test_r1_metrics.py` + `test_r1_runner.py`）

- `test_first_prompt_initial_changed_paths_do_not_pollute_next_comparison`：cat→dog 得 0.5；
- `test_first_prompt_initial_changed_paths_allow_full_preservation_when_kept`：完整保留得 1.0；
- `test_initial_changed_paths_survive_clarifications_but_not_the_first_prompt`：
  首次生成前多澄清轮，初始字段仍进入后续保留评分；
- `test_no_prompt_change_is_excluded_only_from_that_comparison`：两个有效 Prompt 之间的
  无 Prompt 合法 SET 仅本次排除，下一对不沿用；
- `test_prompt_without_preserve_labels_still_resets_baseline_and_exclusions`：
  中间有效 Prompt 无保留标签仍更新基准并清空；
- `TestPreservationBaselineIntegration::test_initial_set_does_not_pollute_next_comparison`：
  用 Runner 真实标注（含首轮初始 SET + 保留标签）端到端验证，不再依赖手工省略初始化输入。

五类必需回归全部具备，断言覆盖 pair 数、得分、逐路径排除项与基准轮 ID。

---

## 3. F2 · 级联阻断轮的完成率分母

### 3.1 复现

任务书：预期生成两轮，首轮失败、第二轮 blocked，完成率分母应为 2。修复前 Runner 对
`blocked` 轮把 `expected_prompt` 置为 `None`，而 `evaluate_generation_completion` 只统计
`expected_prompt is True`，级联阻断轮从分母消失。

确定性验证（同一指标函数，仅改 blocked 轮派生值）：
`expected_prompt=None` → 分母 1；`expected_prompt=True` → 分母 2。

### 3.2 修复

`evaluation/runner.py::_l3_metrics`：r1 的 `expected_prompt` 始终由冻结标注
`expected_outcome == "ready_and_generate"` 派生，不再因 `blocked` 改成 `None`；
`not r1` 分支仍为 `None`（旧 v1 口径不动）。`evaluation/metrics/prompt.py::`
`evaluate_generation_completion` 的口径与实现不变：分母包含全部标注应生成的轮，
实际未产出 Prompt 的阻断轮计未完成；正常澄清/终局接受（非 True）不入分母。

同时确认（未改动、本次核查通过）：

- `blocked` 轮不驱动、不执行调用，`input_source="not_executed"`，`llm_calls`/`image_calls` 空；
- 不为阻断轮伪造独立 `error`；`blocked_by_prior_failure=True` 且
  `blocked_by_turn_id` 指向首个根因轮；`expected_outcome` 照实记录；
- L2 根因统计继续排除 `blocked` 轮（`record.status != "blocked"`），根因去重不缩减完成率分母；
- A/B 用同一 `_l3_metrics` 与同一标注，预期生成口径一致。

### 3.3 回归（`tests/evaluation/test_r1_runner.py`）

- `test_two_expected_turns_first_failed_second_blocked`：分母 2、产出 0、未完成 2，
  独立根因 1（`t1`），阻断轮无 error、有 `blocked_by_turn_id`；
- `test_three_expected_turns_success_failure_blocked_is_one_third`：完成率 `1/3`（非 `1/2`）；
- `test_clarification_failure_then_blocked_is_zero_not_not_applicable`：
  首轮预期澄清却失败、后续预期生成 blocked → 完成率 0，不是 `not_applicable`；
- `test_clarification_and_accept_excluded_and_ab_share_denominator`：
  澄清/接受不入分母，A/B 同口径；
- `test_legacy_v1_run_unchanged_for_blocked_cascade`：旧 v1 无 `generation_completion`，
  blocked 记录不变。

### 3.4 关于“汇总输出区分根因失败与级联阻断”的核查结论

逐轮 `EvalTurnRecord` 已携带可区分信息：根因轮 `status="failed"` 且带 `error`；
级联轮 `status="blocked"`、`error=None`、`blocked_by_prior_failure=True`、
`blocked_by_turn_id` 指向根因；`generation_completion.details.failed_turn_ids` 可回溯。
`RunSummary` 的 `case_status_counts` 与指标 `counts_sum` 保持原语义。
**结论：区分能力已存在，无需新增计数。** 不向 `generation_completion.counts` 添加
根因/阻断拆分，以避免改变指标含义、污染冻结口径（任务书要求 4/5）；
旧报告与历史批次未覆盖、未重算。

---

## 4. F3 · CLI 历史 Prompt 过期重试提示

### 4.1 复现

任务书场景：第一轮成功生成 → 反馈修改 Intent → 重新确认 → 第二轮编译失败。旧
`SessionApp.can_retry_generation` 只判断“本会话是否存在已落库 PromptArtifact”，
因此旧 Prompt 仍在时返回 True，FAILED 分支持续提示“可用同一 PromptArtifact 重新生成”；
实际 `retry` 被 `generation.no_valid_confirmation` 拒绝。HEAD 源码确认该方法体仅
`return self._repo.get_latest_prompt_artifact(session_id) is not None`。

### 4.2 修复

`visual_intent_agent/cli.py`：

- 新增只读 `SessionApp.retry_status(session_id) -> str`，取
  `Repository.get_latest_prompt_artifact`，用 `PromptArtifact.model_validate_json`
  读 `based_on_confirmation_id`，并**复用** `Repository.is_confirmation_valid(...)`
  （不复制确认判定规则），返回 `RETRY_READY` / `RETRY_NO_PROMPT` / `RETRY_PROMPT_EXPIRED`
  （常量已加入 `__all__`）；`can_retry_generation` 委托 `retry_status == RETRY_READY`。
- FAILED 分支按结果给两类准确提示：“没有可复用的已落库 Prompt（编译失败或从未编译）”
  与“最新已落库 Prompt 绑定的确认已失效（历史 Prompt 已过期）”，不再把过期描述成从未编译。
- 底层门禁未动：`GenerationPipeline.retry` 仍是 FAILED + 确认校验的唯一授权；
  预检查后状态变化仍由底层以 `generation.no_valid_confirmation` 拒绝。
- 不重新确认、不重新 compile、不伪造 generation ID、不放宽 PIN/Revision/Artifact 约束、
  不新增会话恢复机制。

### 4.3 回归（`tests/cli/test_cli_e2e.py`，配合 `tests/cli/cli_helpers.py`）

- `test_cli_second_round_compile_failure_reports_expired_prompt`：
  第一轮成功、修改后第二轮编译失败；旧 Prompt 存在但 `can_retry_generation=False`，
  显示“历史 Prompt 已过期”，`retry` 不触发图片 Provider，仍可 exit；
- `test_cli_no_reusable_prompt_reports_not_retryable`：首轮编译失败无 Prompt，
  准确提示不可重试，不崩溃，`flaky.requests == []`；
- `test_cli_retry_after_second_generation_failure_uses_second_prompt`（增强）：
  第二轮成功编译、图片 Provider 失败 → 预检查为可重试，重试复用第二轮 Prompt；
- `test_cli_retry_rejected_when_confirmation_expires_after_precheck`：
  TOCTOU——预检查通过（`can_retry=True`）后确认失效，底层仍拒绝，
  断言 `generation.no_valid_confirmation` 且不新增图片调用、不落 Generation Artifact。

辅助：`tests/cli/cli_helpers.py` 新增
`append_execution_revision_with_model(...)`（只增 ExecutionRevision，模拟会话中途执行
上下文变化，离线构造第二轮编译失败），`ScriptedConsole` 的 `hook` 用于在提示点注入
状态变化以覆盖 TOCTOU。均为测试侧工具，未改产品状态机。

---

## 5. harness 实现版本：新旧对照

任务书允许“修改 `evaluation/reporting.py` 中的 harness 实现版本标识，使后续 Run 能区分
修复前后的评分实现；同步更新相关版本断言；不改 metric/protocol 冻结版本”。

**决定：升级 harness 实现版本（本批次 F1/F2 改变了评分实现）。**

| 版本标识 | 字段 | 旧值 | 新值 |
| --- | --- | --- | --- |
| 评测框架实现版本 | `EVALUATION_HARNESS_VERSION` / `EvalRunRecord.harness_version` | `evaluation_harness_v1` | **`evaluation_harness_v2`** |
| 指标口径版本（**未改**） | `METRIC_VERSION_R1` / `MetricRecord.metric_version` | `v0_3_r1` | `v0_3_r1` |
| 指标口径版本（**未改**） | `METRIC_VERSION_V1` | `v1` | `v1` |
| 协议版本（**未改**） | `gate_a_protocol_v0_3_r1` | 同左 | 同左 |
| 记录信封版本（**未改**） | `EVAL_RECORDS_SCHEMA_VERSION` | `eval_records_v1` | `eval_records_v1` |

影响：`harness_version` 写入 `run.json`；默认 `code_version` 变为
`visual_intent_agent-0.1.0+direct_llm_baseline_v1+evaluation_harness_v2`；
`build_eval_run_id` 与 `identity.component_version` 均含该常量，故修复前后 Run 不共享
同一身份，可区分、不可直接比较结论。冻结清单与协议文件不含该标识，无需改冻结物；
`outputs/evaluation_runs/*` 历史 Run 仍为 `v1`，只读保留。

同步的测试断言/夹具：

- `tests/evaluation/test_r1_runner.py` 新增
  `TestR1RunRecording::test_harness_version_marks_post_v0_3_scoring_fix`：
  断言常量为 `evaluation_harness_v2`、`result.run.harness_version` 一致、
  默认 `code_version` 以 `+evaluation_harness_v2` 结尾，且 `metric_version` 仍为 `v0_3_r1`；
- `tests/evaluation/test_reporting.py`、`tests/evaluation/test_image_review.py` 的
  合成 `EvalRunRecord` 夹具 `harness_version` 同步为 `evaluation_harness_v2`。

---

## 6. 变更文件清单（相对 HEAD）

实现：

- `evaluation/metrics/prompt.py`（F1：`evaluate_preservation_r1` 清空时机 + 注释）
- `evaluation/runner.py`（F2：`_l3_metrics` 的 `expected_prompt` 派生）
- `visual_intent_agent/cli.py`（F3：`retry_status`、两类提示、`__all__`）
- `evaluation/reporting.py`（harness v1 → v2，注释说明）

测试：

- `tests/evaluation/test_r1_metrics.py`（F1 单元 + F2 指标单元，新增 7 条）
- `tests/evaluation/test_r1_runner.py`（F1/F2 Runner 集成 + harness 版本断言，新增 7 条）
- `tests/cli/test_cli_e2e.py`（F3，新增 3 条 + 1 条增强）
- `tests/cli/cli_helpers.py`（测试侧辅助）
- `tests/evaluation/test_reporting.py`、`tests/evaluation/test_image_review.py`（夹具同步）

文档（用户既有，本批次只读保留）：`docs/task_books/README.md`、
`docs/task_books/post_v0.3_fixes/README.md`。

`git diff --stat`：11 个已跟踪文件变更，`847 insertions(+), 30 deletions(-)`；
另有 1 个 untracked 任务书目录。

---

## 7. 测试命令与实际结果（本审查会话；末条为全量集中回归）

均为离线 Fake Provider / 临时 SQLite / 临时输出目录；未读取真实凭据、未执行真实 smoke、
未做真实 Provider、正式评测或 RAG。

```text
git diff --check
  → exit 0（无输出，无空白/格式问题）

uv run python - <<'PY'  # 修复前/后确定性对照（HEAD 源码 vs 新实现）
  F1 OLD status/score: not_applicable None
  F1 NEW status/score: ok 0.5 1 1
  F2 OLD blocked expected_prompt=None -> {'expected_prompt_turns': 1, 'produced': 0, 'failed': 1}
  F2 NEW blocked expected_prompt=True  -> {'expected_prompt_turns': 2, 'produced': 0, 'failed': 2}

uv run pytest -q tests/evaluation/test_r1_metrics.py tests/evaluation/test_r1_runner.py tests/cli/test_cli_e2e.py
  → 50 passed in 0.76s

uv run pytest -q tests/evaluation/test_reporting.py tests/evaluation/test_runner.py tests/evaluation/test_image_review.py
  → 77 passed in 1.62s

uv run pytest -q tests/evaluation tests/cli
  → 316 passed in 2.56s

uv run pytest -q "tests/evaluation/test_r1_runner.py::TestR1RunRecording::test_harness_version_marks_post_v0_3_scoring_fix"
  → 1 passed in 0.19s

uv run pytest -q   # 全部最终修改完成后，由主代理集中执行一次的全量命令
  → 2232 passed, 3 deselected in 7.24s
```

以上 `uv run pytest -q` 为本批次唯一一次集中全量回归，执行时点在本批次全部最终修改
（含 harness v1→v2 及断言/夹具同步）完成之后，覆盖全部离线用例并通过；
3 条 deselected 为仓库既有的默认取消选择项，与本批次修复无关。

---

## 8. 审查结论

- F1：清空时机正确（每个有效 Prompt 无条件成为新基准并清空），累计窗口正确，旧 v1 未动；
  最小 cat→dog 复现修复前 `not_applicable`、修复后 0.5，完整保留 1.0；五类必需回归齐备。
- F2：r1 `expected_prompt` 始终由冻结标注派生，`blocked` 仍留在分母；v1 分支保持 `None`；
  阻断轮不执行、不伪造异常、根因与级联可逐轮区分；分母/完成率断言与 A/B 同口径均覆盖。
  “汇总输出区分根因与级联”经核查由逐轮记录承载，无需新增计数（不改变指标含义）。
- F3：`retry_status` 只读、复用 `Repository.is_confirmation_valid`，两类提示准确，
  TOCTOU 由底层 `GenerationPipeline.retry` 继续拒绝；预检查不是授权。
- 测试审查：未发现过度测试；断言覆盖任务书要求的全部必需回归，关键断言为
  pair/分母/得分/排除路径/Provider 调用次数等可观察事实，而非仅测试总数。
- 本次最小修正：仅 harness 实现版本升级（v1→v2）及相应断言/夹具同步；未改
  metric/protocol 冻结版本，未改冻结数据或历史结果。

---

## 9. 未完成事项与边界

- 全量 `uv run pytest -q` 已在全部最终修改完成后由主代理集中执行一次，结果为
  `2232 passed, 3 deselected`；离线回归无未完成项。
- 正式评测、真实探针、A/B、人工盲评与 Gate A 判定均未开展，按任务书推迟到 RAG
  知识库辅助接入后另行制定方案；本批次不实现 RAG。
- `outputs/evaluation_runs/*` 等历史 Run 仍标识 `evaluation_harness_v1`，未重算、未覆盖；
  新旧 harness 结果禁止直接合并比较。
- 未提交、未推送 Git；工作区保持未提交状态。

---

## 10. 完成状态

**“v0.3 后置三项修复完成，离线回归通过；正式评测暂缓，待 RAG 辅助接入后另行安排。”**

不写 Gate A Go/No-Go；离线测试结果不代表模型语义或生成质量验证。