# MVP v0.3 Step 03 交接记录：自动评测框架与 L1～L3 指标

任务：MVP v0.3 Step 03 —— 建立可重放的 A/B Runner，以相同数据与 Provider 条件运行
Baseline A（Step 02 执行器）与 System B（v0.2 冻结实现，零修改），产出统一合同记录、
逐案例 JSONL、聚合摘要与 L1～L3 自动指标；用 Fake Provider 建立确定性回归测试，
并提供显式命令运行真实 LLM 层评测。不实现 L4 图片盲评、不下 Gate A 结论、不做图片主观评分。

- 依据：`docs/task_books/mvp_v0.3/README.md`、`docs/task_books/mvp_v0.3/03_evaluation_harness.md`、
  `evaluation/protocol.md`（Step 01 冻结）、`evaluation/configs/gate_a_v0_3.json`、
  `docs/handoffs/v0_3_step_01_handoff.md`、`docs/handoffs/v0_3_step_02_handoff.md`、
  `docs/handoffs/step_09_handoff.md`、`evaluation/models.py`、`evaluation/direct_baseline.py`。
- 范围纪律：**未修改** `visual_intent_agent/` 下任何文件、Step 01 四个冻结物与
  `frozen_manifest_v0_3.json`/`test_dataset_contract.py`（sha256 复核一致）、Step 02 交付物
  （`models.py` / `direct_baseline.py` / `evaluation/__init__.py` /
  `test_direct_baseline.py` / `v0_3_step_02_handoff.md`）、`v0_3_step_01_handoff.md`、
  `docs/task_books/`、`pyproject.toml`；全部改动只落在 Step 03 自有文件与新增文件。

---

## 0. 接手断点说明（前任已完成 vs 本次审计/修复）

本步由前一个 Agent 执行到大部分交付物后中断；本次任务是**审计、修复、补全、验收**，
不是重写。断点状态与处理：

| 前任留下的状态 | 本次处理 |
|---|---|
| `evaluation/runner.py`（2031 行，含 argparse CLI）、`reporting.py`、`metrics/{state,intent,prompt}.py`、6 个 Step 03 测试文件、`evaluation_harness_helpers.py` 均已存在 | 逐文件通读审计（结论见第 1 节）；未推倒重写，只做最小修复（第 2 节） |
| `uv run pytest -q` → 2 failed, 2048 passed, 3 deselected；两条失败都在 `tests/evaluation/test_runner.py` | 两条均已修复（第 2 节），修复后全量 2051 passed |
| `docs/handoffs/v0_3_step_03_handoff.md` 缺失 | 本次补写（本文件） |
| `evaluation/runs/` 为空目录（仅 `.gitignore`） | 保持为空；运行产物运行期生成，不入冻结清单 |
| Step 01 冻结物、Step 02 交付物 | 未改动；90 个受保护文件 sha256 改动前后逐字节一致（第 8 节） |

---

## 1. 审计结论（对照任务书 8 条实施内容与协议第 3～8 节）

### 1.1 已正确实现（审计通过，未改动）

1. **统一 Run/Turn/Metric/Failure/Artifact 引用合同**（`evaluation/reporting.py`）：
   `EvalRunRecord` / `EvalCaseRecord` / `EvalTurnRecord` / `MetricRecord` /
   `EvalFailureRecord` / `ArtifactRef`，外加 `LLMCallObservation` /
   `ImageCallObservation` / `SystemBTurnObservation`；全部
   `frozen=True, extra="forbid"`，`created_at` tz-aware UTC；A/B 同形状。
2. **Run 级元数据**：`code_version`（含 `EVALUATION_HARNESS_VERSION` 与
   `EVAL_ID_SCHEME_VERSION`）、`protocol_version/dataset_version/config_version/
   manifest_version`、四个冻结输入 + manifest 的 sha256、`run_nonce`、
   `repetitions/l4_repetition/systems/case_ids/case_count`。
   `EvalProviderSnapshot` 只记模型名/超时/重试/尺寸/张数与**环境变量名**，
   无 base_url/无 key（测试断言 `run.json` 无凭据值）。
3. **随机性记录**（协议第 7 节逐字段）：`LLMCallObservation` =
   model_requested + model_returned + `temperature_sent`/`max_tokens_sent`
   （合同层恒 `None`，固化"不显式设置采样参数"）+ response_format_sent +
   provider_request_id + latency_ms + retry_count；`ImageCallObservation` =
   model_requested/returned + size + `seed`（Fake/未返回记 `None`，禁止伪造）+
   provider_request_id + latency_ms + retry_count。Baseline 的同等字段在
   Step 02 记录中，本合同以 ID + 路径**引用而非复制**。
4. **Baseline 不适用指标**：`_baseline_metrics` 对 L1 四不变量与 L2 五指标一律
   构造 `status="not_applicable"`、`score=None`、`passed=None`（合同校验器禁止
   非 ok 记录携带 score/passed，杜绝"伪装零分/满分"）；L3 四指标 A/B 同口径计算。
5. **L1 四不变量**（`evaluation/metrics/state.py`）：未授权字段不变（含
   `forbidden_change_paths` 逐值 + 变化路径 ⊆ `applied_deltas` 双重取证）、
   旧确认不复用（生成绑定本轮新确认 + 当前 revision；过期 revision / 篡改 hash
   两个负向探针）、pinned 不被覆盖（值不变 + PIN/UNPIN 必须来自被接受的显式
   Delta；对 pinned 的 SET 记 `set_on_pinned_events` 归因）、历史 append-only
   （8 张表计数单调 + 逐行列投影哈希，`sessions` 指针表除外）。四条各有正/反例
   单元测试。
6. **L2 五指标**（`metrics/intent.py`）与 **L3 四指标**（`metrics/prompt.py`）
   逐条对齐协议第 4 节口径（详见第 4 节）。
7. **逐案例 JSONL + 聚合摘要可追溯**：`cases/<case_id>.jsonl`（每行
   `{"record_type": case|turn|metric, "record": ...}`，确定性行序）+ `run.json` +
   `summary.json`；`MetricRecord.evidence_refs` 指向逐轮记录，
   `MetricAggregate.metric_record_ids` 指向原始指标记录，`ArtifactRef` 指向
   原始 Prompt/图片/Step 02 记录/SQLite。
8. **单轮与多轮分开且可继续聚合**：聚合键为
   `(layer, metric, system, turn_scope∈{single,multi,all})`；`all` 由两分组合并
   （样本量相加、`counts_sum` 逐键求和），`score_mean` 只在 ok 且带 score 的记录上取均值。
9. **协议第 3 节驱动规则**：`user_message`/`clarification_answer` →
   `WorkflowService.submit_message`；`image_feedback`/`accept` →
   `ReviewService.submit_feedback`；进入 `WAITING_CONFIRMATION` 时重算
   `summary_hash` 自动确认并立即 `GenerationPipeline.generate`（确认与生成计入该轮）；
   `WAITING_CLARIFICATION` 不即兴作答、按脚本提交并记 `flow_deviation`；
   `answer_variants` 按实际待答问题 `target_path` 选择、无匹配回退 `user_text`；
   Baseline 恒用 `user_text` 且 `accept` 轮 `skipped`（`not_executed`，不记
   `missing_data`）；与 `expected_outcome` 的偏离记 `actual_outcome`/`outcome_matches`。
10. **重复 2 次与图片层**：每案例跑 `repetitions`（冻结配置 = 2）次，
    `run_nonce=f"rep{N}"`；图片每次重复都真实生成（多轮反馈轮必须以真实生成为前提），
    仅第 `l4_repetition`（=1）次的图片 `designated_for_l4=True`，A/B 对称。
11. **隔离与零修改**：每案例全新 SQLite（`system_b/rep<N>/cases/<case_id>/session.db`）
    与新会话；`RecordingLLMProvider`/`RecordingImageProvider` 只观察不改行为，
    不重试、不改写；产品代码零修改。
12. **显式真实运行入口**：`evaluation/runner.py` 的 argparse CLI（`build_arg_parser`/
    `main`），真实 adapter 只在 `_real_provider_factories` 内**延迟 import**；
    默认测试全程 Fake + `tmp_path`，零网络、零真实凭据。
13. **冻结输入强校验**：`verify_frozen=True`（默认）在构造期按
    `frozen_manifest_v0_3.json` 复核四个冻结文件 sha256，篡改即 `ValueError`。

### 1.2 有偏差需修（本次已修）

| # | 偏差 | 影响 | 修复 |
|---|---|---|---|
| D1 | `EvalFailureRecord.stage` 的 `Literal` 缺 `"baseline"`，而 Runner 把 Step 02 `BaselineErrorRecord.stage` 原样透传（该枚举含 `llm`/`image`/`baseline`） | **真实可达崩溃**：Baseline LLM 返回空白文本时 Step 02 记 `baseline.empty_llm_output`（`stage="baseline"`），Runner 在记录装配期抛 pydantic `ValidationError`，整次 Run 崩溃 | `reporting.py` 的 `stage` 增加 `"baseline"`（与 Step 02 `BaselineErrorStage` 完全兼容，不做有损映射）；新增 `TestFailureHandling::test_baseline_empty_llm_output_is_recorded_without_crashing` 端到端回归 |
| D2 | Baseline 逐轮 `ArtifactRef` 只引用 Prompt 与图片，Step 02 逐轮原始 JSON（含 seed/provider_request_id/latency/retry 等随机性字段）无路径引用 | 协议「聚合可追溯到原始 Turn 与 Artifact」对 Baseline 随机性字段只能靠约定拼路径，Step 04/05 定位不便 | `_map_baseline_turn` 增加 `kind="baseline_turn_record"` 的 `ArtifactRef`，`path` 指向 `baseline_a/rep<N>/cases/<case_id>/turns/turn_NN.json`；`test_structure_two_systems_two_repetitions` 断言该路径在 run 目录内可解析（含 `skipped` 轮） |
| D3 | `TestFrozenManifestEnforcement::test_modified_frozen_input_is_refused` 在 `pytest.raises` 块**外**构造 Runner，而冻结校验在构造期即抛错，异常逃逸导致测试失败（测试与实现的校验时机不一致） | 该测试无法反映"篡改必被拒"的真实语义 | 见第 2 节（测试修复，不弱化语义） |
| D4 | `TestOfflineProof::test_tests_directory_has_no_real_adapter_or_network_imports` 用 `ast.walk` 扫描**所有** import，把 Step 02 测试函数体内的延迟 import（真实/Fake 同接口验证，不触网）也判违规，与测试自己声明的"真实 adapter 只允许函数内延迟 import"规则自相矛盾 | 离线证明测试误报，且规则与扫描逻辑不一致 | 见第 2 节（扫描只认模块顶层 import + 反例断言证明扫描有效） |

### 1.3 缺失需补（本次已补）

| # | 缺失 | 补充 |
|---|---|---|
| M1 | `docs/handoffs/v0_3_step_03_handoff.md` | 本文件 |
| M2 | Baseline `stage="baseline"` 失败的端到端回归用例 | `test_baseline_empty_llm_output_is_recorded_without_crashing`（Step 03 自有测试文件） |
| M3 | 离线 import 扫描的"反例断言"（证明扫描规则非空转） | `_forbidden_top_level_imports` 纯函数 + 顶层 `httpx`/`socket`/`openai_image` 反例 + 函数体内延迟 import 放行断言 |
| M4 | Baseline 逐轮原始记录路径引用的回归断言 | 已并入 M2/D2 的测试与结构测试断言 |

未发现任务书 8 条实施内容的其它缺失项；L1～L3 指标函数、三态口径、聚合与落盘均已实现。

---

## 2. 两条失败测试的修复方式（以及为何不弱化语义）

### 2.1 `TestFrozenManifestEnforcement::test_modified_frozen_input_is_refused`

- **根因**：Runner 的冻结校验在**构造期**执行（`EvaluationRunner.__init__` →
  `_compute_hashes(verify_frozen=True)`，fail fast，runner.py「verify_frozen」文档即
  "构造时按清单复核"），而测试把 `pytest.raises` 只套在 `runner.run()` 上，
  构造语句先抛 `ValueError` 逃逸。
- **设计意图核对**：构造期校验是有意为之（尽早失败、不读数据集、不落任何产物），
  文档一致，因此**不改 Runner**。
- **修复**：把构造与运行一并放进 `pytest.raises`：
  ```python
  def build() -> EvaluationRunner: ...
  with pytest.raises(ValueError, match="frozen file hash mismatch"):
      build().run()
  assert not (tmp_path / "runs").exists()   # 被拒的运行不落任何产物
  ```
  这样断言对"构造期校验 / run() 期校验"两种实现都成立，且新增"不落盘"断言，
  "篡改冻结输入必被拒绝"的语义**不弱化反而加强**（不存在任何"已构造即可运行"的窗口）。

### 2.2 `TestOfflineProof::test_tests_directory_has_no_real_adapter_or_network_imports`

- **根因**：`ast.walk` 会把函数体内的延迟 import 也算违规；Step 02
  `test_direct_baseline.py:262–263` 在测试函数内 import `openai_image`/`openai_llm`
  做"真实与 Fake 同一公开接口"的结构检查（不构造、不发请求、不触网），
  而 `test_runner.py` 自己已声明"真实 adapter 只允许在函数内延迟 import"。
- **修复**：新增模块级纯函数
  `_forbidden_top_level_imports(tree, forbidden)`，**只扫 `tree.body`（模块顶层）**；
  扫描逻辑与声明规则一致：顶层 import 真实 adapter / `httpx`/`socket`/`requests`
  仍必须被捕获，函数体内延迟 import 放行。
- **不弱化证明**：测试内新增反例断言——对合成源码
  `import httpx` / `import socket` /
  `from visual_intent_agent.providers.openai_image import ...` 断言扫描器返回三个违规名，
  并对 `def f(): import httpx` 断言返回空。即扫描规则被证明"能抓顶层违规、放行延迟 import"，
  不是空转。未修改 `test_direct_baseline.py` 的任何一行。

---

## 3. 统一合同与 Runner 公开接口摘要（供 Step 04 直接调用）

### 3.1 `evaluation/reporting.py`（统一合同）

```text
版本常量   EVAL_RECORDS_SCHEMA_VERSION="eval_records_v1"
           EVALUATION_HARNESS_VERSION="evaluation_harness_v1"
           EVAL_ID_SCHEME_VERSION="eval_ids_sha256_v1"
           SYSTEM_IDS=("baseline_a","system_b")
           L1_INVARIANTS=(unauthorized_fields_unchanged, no_stale_confirmation,
                          pinned_not_overwritten, history_append_only)
           L2_METRICS=(delta_accuracy, missing_decision_recall, clarification_precision,
                       conflict_detection, delegation_scope_accuracy)
           L3_METRICS=(intent_coverage, unauthorized_addition, preservation,
                       model_compatibility)
输入合同   ValueMatchSpec{mode, keywords, value}
           ExpectedDeltaSpec{operation, path, value_match, resolution}
           ExpectedRejectionSpec{operation, path, expected_issue_codes, only_if_emitted, resolution}
           MustClarifySpec{target_path, reason, allow_delegate}
           ExpectedConflictSpec{rule_id, kind, blocking}
           ExpectedCarrySpec{carried, invalidated}
           TurnAnnotation{turn_id, expected_deltas, acceptable_extra_deltas,
             expected_rejections, forbidden_change_paths, paths_must_remain_unset,
             expected_resolution_records, blocking_missing_paths_after_turn,
             must_clarify, must_not_clarify_paths, acceptable_clarify_paths,
             expected_conflicts, expected_feedback_decision, expected_carry,
             expected_outcome, notes}
           ImageEvalDimension{dimension, focus, applies_to_turns}      # Step 04 读
           PromptExpectations{must_mention_groups, must_not_mention}
           CaseAnnotation{case_id, dataset_version, scenario, annotation_basis,
             turn_annotations, image_evaluation_dimensions, prompt_expectations, notes}
             .turn_annotation(turn_id) -> TurnAnnotation | None
           load_annotations(path) -> list[CaseAnnotation]
输出合同   EvalFailureRecord{code, message, stage∈{llm,image,baseline,workflow,
               generation,prompt,runner}, retryable, status_code, provider_request_id}
           ArtifactRef{artifact_ref_id, kind∈{baseline_case_record, baseline_turn_record,
               baseline_prompt, baseline_image, prompt_artifact, generation_artifact,
               image_file, sqlite_db, case_jsonl}, ref_id, path, sha256,
               designated_for_l4, note}
           LLMCallObservation / ImageCallObservation                 # 随机性记录
           ObservedDelta / ObservedIssue / ObservedQuestion /
           ObservedConfirmation / ObservedGeneration / SystemBTurnObservation
           EvalTurnRecord / EvalCaseRecord / MetricRecord
           EvalProviderSnapshot / EvalRunRecord / EvalRunResult
             EvalRunResult{run, cases, turns, metrics, summary}
           MetricPayload{status∈{ok,not_applicable,missing_data}, score, passed,
               counts, details, evidence_refs}
           MetricAggregate / L1BlockingEntry / RunSummary
             RunSummary{run_id, case_status_counts, metrics, gate_a_blocked,
                        l1_blocking, created_at}
           make_eval_record_id(prefix, *parts) -> str
           aggregate_run(run, cases, metrics, *, created_at) -> RunSummary
           write_json(path, payload) -> None
           write_case_jsonl(path, case_records, turn_records, metric_records) -> None
           read_case_jsonl(path) -> list[dict]
```

合同不变量（测试强制）：非 `ok` 指标不得携带 `score/passed`；`failed` 轮/指标必须携带
`error/failure`；Baseline 轮不得携带 `system_b` 观察、Baseline 案例不得携带 `l1_failed`；
`flow_deviation=True` 必须带原因；`score` 必须在 `[0,1]`；全部模型 frozen + `extra=forbid`。

### 3.2 `evaluation/runner.py`（Runner 与 CLI）

```text
build_eval_run_id(*, code_version, dataset_sha256, annotations_sha256, config_sha256,
                  protocol_sha256, repetitions, run_nonce="") -> str   # 内容寻址
class EvaluationRunner:
  def __init__(self, *, settings: Settings,
               llm_factory: Callable[[], LLMProvider],
               image_factory: Callable[[], ImageProvider],
               output_root: Path = evaluation/runs,
               config_path=DEFAULT_CONFIG_PATH, dataset_path=DEFAULT_DATASET_PATH,
               annotations_path=DEFAULT_ANNOTATIONS_PATH,
               protocol_path=DEFAULT_PROTOCOL_PATH, manifest_path=DEFAULT_MANIFEST_PATH,
               repetitions: int | None = None,        # None → 冻结配置 = 2
               l4_repetition: int = 1,
               run_nonce: str = "",
               code_version: str | None = None,
               verify_frozen: bool = True,
               clock: Callable[[], datetime] | None = None,
               monotonic: Callable[[], float] | None = None) -> None
  # 只读属性：run_id / run_dir / repetitions
  def run(self, case_ids: Sequence[str] | None = None) -> EvalRunResult
build_arg_parser() -> argparse.ArgumentParser
main(argv: Sequence[str] | None = None) -> int
```

落盘布局（协议第 8 节；`<run_id>` 由冻结输入 + 代码版本 + 重复次数 + nonce 内容寻址）：

```text
evaluation/runs/<run_id>/
├── run.json                              # EvalRunRecord
├── summary.json                          # RunSummary（gate_a_blocked / l1_blocking）
├── cases/<case_id>.jsonl                 # case/turn/metric 三类行
├── baseline_a/rep<N>/                    # Step 02 原始产物（run.json/cases/<id>/...）
│   └── cases/<case_id>/{case.json,turns/turn_NN.json,images/...}
└── system_b/rep<N>/cases/<case_id>/
    ├── session.db                        # System B 全量原始状态（L1 历史证据）
    └── images/<gen_id>/image_i.png
```

---

## 4. L1/L2/L3 指标函数清单与口径

### L1 `evaluation/metrics/state.py`（仅 System B）

```text
check_unauthorized_fields_unchanged(evidence) -> list[L1Violation]
check_no_stale_confirmation(evidence) -> list[L1Violation]
check_pinned_not_overwritten(evidence) -> tuple[list[L1Violation], list[set_on_pinned_event]]
check_history_append_only(turn_id, before, after) -> list[L1Violation]
evaluate_case_l1(case_id, turns) -> L1CaseEvaluation{l1_failed, outcomes, set_on_pinned_events}
HISTORY_TABLES = messages/intent_revisions/execution_revisions/confirmations/
                 prompt_artifacts/generation_artifacts/feedback_results/realization_states
```
任一不变量失败 → 案例 `l1_failed=true` → `summary.gate_a_blocked=true` +
`l1_blocking[case_id, repetition, failed_invariants, metric_record_ids]`（显式 Gate A 阻断）。

### L2 `evaluation/metrics/intent.py`（仅 System B；输入 `TurnObservationInput`）

```text
evaluate_delta_accuracy(turns)            # 主 score=recall(expected 按 operation/path/
                                          #   value_match/resolution 贪婪一对一匹配)；
                                          #   details.precision=in_scope/applied；counts 含
                                          #   out_of_scope / rejection_failures /
                                          #   must_remain_unset_violations
evaluate_missing_decision_recall(turns)   # blocking_missing_paths_after_turn 被
                                          #   unresolved_decisions(action=block) 覆盖比例
evaluate_clarification_precision(turns)   # 提问 target_path ∈ must_clarify ∪
                                          #   acceptable_clarify_paths 且 ∉ must_not_clarify_paths；
                                          #   ready_and_generate 轮提问必失败
evaluate_conflict_detection(turns)        # expected_conflicts 同 rule_id 检出率；
                                          #   blocking 未检出且进入 ready → severe_failures
evaluate_delegation_scope(turns)          # 终态 user_delegated 集合 vs 标注逐轮并集；
                                          #   over/under 分计；Reducer 保留旧 delegated 记录
                                          #   按 Step 01 已知限制 4 口径不判异常
辅助: normalize_token / value_matches / delta_matches / normalize_conflict_rule_id
```

### L3 `evaluation/metrics/prompt.py`（A/B 同口径；文本层代理，不得代替 L4）

```text
evaluate_intent_coverage(must_mention_groups, final_prompt)     # 最终就绪态 Prompt 组命中率
evaluate_unauthorized_addition(must_not_mention, final_prompt, *, guard_triggered=0)
                                                                # 命中计数；guard_bypassed
evaluate_preservation(must_mention_groups, prompt_sequence)     # 相邻生成对关键词组保留率
evaluate_model_compatibility(requests, *, expected_size, expected_model)
                                                                # size 形态+冻结值+模型名
辅助: keyword_hit（大小写不敏感子串，宽松下界）
```

三态口径（协议第 5 节）：`not_applicable`（Baseline 的 L1/L2、单轮 Preservation）、
`missing_data`（应产生而未产生，如无 Prompt/无请求/无 `prompt_expectations`，
`s06-delegate-vague-002`）、`failed`（指标计算异常由 `_guarded_metric_record` 记
`evaluation.metric_error`）。三类均进 `n_cases` 分母，`counts_sum` 只在 ok 记录上求和。

---

## 5. 重复 2 次与图片层口径的实现方式

- `repetitions` 默认读冻结配置 `repetitions.llm_layer_runs_per_case`（=2），
  CLI `--repetitions` 可覆盖；每次重复 `run_nonce=f"rep{N}"`，基线 side 与系统 B side
  对称（A/B 各 2 次）。
- **图片每次重复都真实生成**：多轮案例的 `image_feedback`/`accept` 轮必须以真实生成为前提，
  协议第 7 节 L4 要求"每系统每案例 1 次成对"，两者通过**指定重复序次**调和——
  仅 `l4_repetition`（默认 1）的图片 `ArtifactRef.designated_for_l4=True`，
  其余重复的图片保留在记录中但**不进入 L4**、不丢弃。Step 04 只需取
  `designated_for_l4=True` 的 image refs 成对即可，无需重跑。
- 禁止单图重抽：Runner 不做任何"重抽"；复评必须整案重跑（换 `--nonce` 另起 Run，
  两份原始记录均保留）。

---

## 6. 真实运行命令完整用法（Step 04 用它跑真实 A/B）

```bash
cd /home/change/projects/image_system

# 0) 只列案例（不需要凭据；离线；输出 22 行 case_id/scenario/turn_type）
uv run python -m evaluation.runner --list-cases

# 1) 全量真实 A/B（22 案例 × 2 重复 × A/B；凭据取 VIA_* 环境变量/.env）
#    产物写仓库外，避免二进制污染 evaluation/ 的凭据卫生扫描
uv run python -m evaluation.runner --output-root /home/change/via_runs

# 2) 冒烟：先跑 1 个案例 × 1 重复
uv run python -m evaluation.runner --case-id s01-complete-001 --repetitions 1 \
    --output-root /home/change/via_runs --nonce smoke-001

# 3) 指定子集/重复/命名
uv run python -m evaluation.runner \
    --case-id s04-conflict-001 --case-id s08-pin-unpin-001 \
    --repetitions 2 --output-root /home/change/via_runs --nonce gate-a-001 --list-cases
```

- 参数：`--output-root`（默认 `evaluation/runs`）、`--case-id`（可重复，缺省全量 22）、
  `--repetitions`（缺省读冻结配置 = 2）、`--nonce`（同配置多次运行的区分串）、
  `--list-cases`（只列案例并退出，不需要凭据）。
- 凭据只从 `VIA_PROVIDER_BASE_URL` / `VIA_PROVIDER_API_KEY` / `VIA_LLM_MODEL` /
  `VIA_IMAGE_MODEL` 等环境变量或 `.env` 读取；CLI **不接受**明文 key 参数；
  缺凭据时退出码 2 且 stderr 提示，不发起任何请求。
- 产物落 `<output_root>/<run_id>/`（布局见 3.2）；标准输出打印
  `run_id` / 产物目录 / 案例数 / 案例状态计数 / `gate_a_blocked` / 逐条阻断证据。
- **重要**：若把产物写到默认的 `evaluation/runs/`，真实运行后的图片与 SQLite 是二进制，
  Step 01 的凭据卫生测试会按 UTF-8 读取 `evaluation/` 全部文件而失败——真实运行请用
  `--output-root` 指到仓库外，或运行默认测试前清空 `evaluation/runs/`
  （该目录已 gitignore，且 `evaluation/__init__.py` 会清理本包 `__pycache__`）。

### 默认测试零真实网络

- Runner 顶层只 import 产品公开面与 `evaluation.*`；真实 adapter 在
  `_real_provider_factories` 内**延迟 import**（`test_real_adapters_are_only_lazy_imports_in_runner`）。
- `tests/evaluation/*.py` 模块顶层禁止 `openai_llm`/`openai_image`/`httpx`/`socket`/`requests`
  （修复后的顶层扫描 + 反例断言）；Fake Provider + `tmp_path`，零网络、零真实凭据。

---

## 7. 测试与实验结果

```text
$ uv run pytest tests/evaluation -q
208 passed in 1.46s

$ uv run pytest -q
2051 passed, 3 deselected in 5.63s      # 断点基线 2048 passed + 修复 2 条 + 新增 1 条

# 两条原失败用例 + 新增回归 + 重放一致性（定向）
$ uv run pytest tests/evaluation/test_runner.py::TestFrozenManifestEnforcement \
                tests/evaluation/test_runner.py::TestOfflineProof \
                tests/evaluation/test_runner.py::TestFailureHandling \
                tests/evaluation/test_runner.py::TestSingleTurnRun -q
全部通过（含 test_deterministic_replay_produces_structurally_identical_results）

$ uv run python -m evaluation.runner --list-cases | wc -l
22
```

补充实测（非测试命令）：

- 冻结物 sha256 与 `frozen_manifest_v0_3.json`、Step 01 内嵌常量逐条一致（下表）；
- 90 个受保护文件（`visual_intent_agent/**` + Step 01/02 交付物 + task books + pyproject）
  改动前后 sha256 完全一致；
- 用全量冻结数据集 + 启发式 Fake 离线跑通 System B：L1 阻断链路实测生效
  （8 个案例因未授权 `forbidden_change_paths` 变化被正确标记 `gate_a_blocked=true`，
  证明 L1 失败→摘要阻断端到端可用；启发式 Fake 只为机械完备，不代表真实分数）；
- Baseline 空白 LLM 输出（`baseline.empty_llm_output`）端到端跑通并保留在分母（D1 回归）；
- Baseline 逐轮原始 JSON 路径引用在 completed 与 skipped 轮均可解析（D2）。

冻结哈希（本次复核，未改动）：

| 文件 | sha256 |
|---|---|
| `evaluation/protocol.md` | `70cc0b3c4bde970a91172191b398cf3869df539fa0971ae85cfd849c52c26d93` |
| `evaluation/fixtures/core_v0_3.jsonl` | `0519534702225949bede2782516cd1543c936d2fd298834ad36dd253fc63a64b` |
| `evaluation/annotations/core_v0_3.jsonl` | `d1712aab45c42251d920d22f6a5a6a1fe5bef964f577e2b45aead851dc6a6b57` |
| `evaluation/configs/gate_a_v0_3.json` | `3b6159f8bc620f6c31fc85fb40a937a7ced1a71296a8c6ab9d0514c0f329905b` |

---

## 8. 验收自检 8 项逐项结果

| # | 自检项 | 结果 |
|---|---|---|
| 1 | `uv run pytest tests/evaluation -q` 全绿 | ✅ `208 passed`（原 207，含 2 条失败修复 + 1 条新增回归） |
| 2 | `uv run pytest -q` 全量零回归 | ✅ `2051 passed, 3 deselected`（断点基线 2048 + 2 修复 + 1 新增） |
| 3 | 复核 `frozen_manifest_v0_3.json` 四文件 sha256 | ✅ 四个哈希与清单/Step 01 内嵌常量一致（见第 7 节表） |
| 4 | `git status --short` 范围纪律 | ✅ `visual_intent_agent/` 无任何修改（git 无变更）；Step 01/02 交付物、task books、pyproject 未修改——90 个受保护文件 sha256 前后逐字节一致；改动只在 Step 03 自有文件（`evaluation/reporting.py`、`evaluation/runner.py`、`tests/evaluation/test_runner.py`）与新增本文件 |
| 5 | CLI 离线自检 `--list-cases` | ✅ 无凭据可运行，输出 22 案例（`22` 行，含 `s01-complete-001`） |
| 6 | 默认测试零真实网络（顶层 import 扫描修复后仍有效） | ✅ 顶层扫描零违规 + 反例断言证明扫描能抓顶层 `httpx`/`socket`/`openai_image`；函数体内延迟 import 放行 |
| 7 | Fake 重放一致性（同配置两次运行结构一致） | ✅ `test_deterministic_replay_produces_structurally_identical_results`（结构投影掩码产品 uuid4，`run_id` 内容寻址一致） |
| 8 | 默认测试不触发真实 Provider | ✅ 全量测试 Fake + `tmp_path`，零网络；真实 adapter 仅延迟 import 且未被调用 |

---

## 9. 变更文件

```text
新增：
  docs/handoffs/v0_3_step_03_handoff.md            （本文件）

修改（Step 03 自有，最小变更）：
  evaluation/reporting.py        # EvalFailureRecord.stage 增加 "baseline"（D1）+ 文档
  evaluation/runner.py           # Baseline 逐轮原始记录 ArtifactRef 路径引用（D2）
  tests/evaluation/test_runner.py# 两条失败测试修复（D3/D4）+ 3 条新断言/回归（M2/M3/M4）

前任留下、本次未改动（仅审计）：
  evaluation/metrics/{__init__,state,intent,prompt}.py、
  tests/evaluation/{test_metrics_state,test_metrics_intent,test_metrics_prompt,
                    test_reporting}.py、tests/evaluation/evaluation_harness_helpers.py、
  evaluation/runs/（空，仅 .gitignore）
```

## 公开接口

见第 3 节（`evaluation.reporting` 合同、`EvaluationRunner` 构造参数与方法签名、
`build_arg_parser`/`main`）与第 4 节（L1～L3 指标函数清单）。

## 原始 Artifact 位置

- 本步不产生真实模型 Artifact（无真实 A/B 运行、无真实图片）；测试产物均在 pytest
  `tmp_path`。
- 真实运行产物布局为 `evaluation/runs/<run_id>/`（或 `--output-root` 指定目录），
  运行期生成、不入冻结清单、不计入默认测试。

## 已知限制

1. **Baseline 随机性字段以引用而非复制呈现**：`EvalTurnRecord.baseline_turn_record_id`
   + `ArtifactRef(kind="baseline_turn_record", path=...)` 指向 Step 02 逐轮 JSON；
   `EvalTurnRecord.llm_calls/image_calls` 对 Baseline 恒空（不重复记录，避免两处不一致）。
2. **`retry_count` 恒 0 = "评测层未重试"**：adapter 内部冻结重试不经 Provider Protocol
   暴露（Step 02 已知限制 1），报告不得把 0 解读为"未发生重试"。
3. **L1 pinned 不变量按"值不变"严格测量**：对 pinned 路径的被接受 SET 若改变了值即计违例，
   同时逐例记 `set_on_pinned_events` 供归因（Step 01 已知限制 5 的口径；协议第 4 节
   L1.3 的"SET 为冻结允许行为"通过归因记录呈现，不放松不变量结论）。
4. **Conflict Detection 主要经 `assess` 的 `policy.hard_conflict.*` /
   `policy.execution_conflict.*` 归一化命中**；Interpreter 的 `detected_conflicts`
   issue code 恒为 `interpreter.conflict_detected`（rule_id 只在 message 里），
   因此不直接贡献命中——这是被测系统结构，不是标注缺陷。
5. **启发式 Fake 的离线全量跑会因不真实对话流产生大量 `flow_deviation`/`failed`
   （`workflow.invalid_state`）与 L1 阻断**：这是"失败不从分母剔除"的实证，
   不代表真实分数；真实评测必须跑第 6 节命令。
6. **真实运行与默认测试的目录交互**：真实产物若落在 `evaluation/runs/`，二进制文件会让
   Step 01 凭据卫生扫描（按 UTF-8 读 `evaluation/` 全部文件）失败；用 `--output-root`
   或运行前清空该目录。
7. **`evaluation/` 字节码缓存**：保持 `import evaluation.*` 包导入方式；
   不要在 `evaluation/` 内运行 `compileall`（Step 02 已知限制 2 的边界，本次未改）。
8. **22 案例规模**不构成统计功效证明；扩展必须新数据集版本（协议第 9 节）。
9. 未实现 L4 图片盲评、未下 Gate A 结论、未做图片主观评分（属 Step 04/05）。

## 对下一步的输入

- **Step 04（图片 A/B 与盲评）**：
  - 直接用第 6 节命令跑真实 A/B；产物在 `<output_root>/<run_id>/`。
  - 取 L4 图片：遍历 `cases/<case_id>.jsonl` 的 `turn` 行，筛
    `artifact_refs[].kind ∈ {image_file, baseline_image}` 且 `designated_for_l4=True`；
    同案例 System B 与 Baseline 成对。路径相对 run 根目录，`sha256` 可校验字节。
  - 去标签：Baseline 图片文件名含 `turn_NN_image_i`，System B 在
    `system_b/rep1/cases/<case_id>/images/<gen_id>/image_i.png`；呈现时改为随机化名称，
    不要改动原始记录文件。
  - L4 维度与逐案评审焦点见标注 `image_evaluation_dimensions`（含 `applies_to_turns`）；
    无图案例（`s06-delegate-vague-002` 等）按 `missing_data` 报告并注明原因。
  - 盲评原始评分（评审人 ID、时间戳、分数、理由）逐案例逐维度落盘到
    `evaluation/runs/<run_id>/blind/`（Step 04 自定），聚合保留分布而非只报均值。
- **Step 05/06/07（结论与复评）**：
  - `summary.json` 已给 `gate_a_blocked`/`l1_blocking` 与逐指标三态计数、单轮/多轮分开视图；
    `MetricAggregate.counts_sum` 可跨 Run 直接相加做复评聚合。
  - 评测口径已冻结；任何修改数据集/标注/口径必须新版本文件（协议第 9 节）。
  - 复评整案重跑：同配置换 `--nonce`，两份原始记录均保留。
- **通用**：`tests/evaluation/` 无 `conftest.py`、不跨测试目录 import、共享工具在
  `evaluation_harness_helpers.py`；默认全离线。

## 是否满足验收条件

**是。** 任务书 8 条实施内容与 5 条验收条件逐条核对：

| # | 验收条件 | 核对 |
|---|---|---|
| 1 | 同一配置可重放并产生结构一致的结果 | ✅ 结构投影重放测试通过；`run_id` 内容寻址（含 nonce 区分） |
| 2 | L1 核心不变量任一失败在摘要显式标记 Gate A 阻断 | ✅ 指标级注入测试 + 全量数据集实测（8 案例阻断，`gate_a_blocked=true`）+ `l1_blocking` 可追溯 |
| 3 | 单轮与多轮指标分开保留并可继续聚合 | ✅ `turn_scope∈{single,multi,all}`，`counts_sum` 求和、样本量相加（报告测试） |
| 4 | 失败、超时和缺失结果不从分母静默删除 | ✅ 三态 + `failed` 均进 `n_cases`；Provider/解析失败轮保留；Baseline `accept` 记 `skipped` 而非 `missing_data` |
| 5 | 默认测试离线运行，不触发真实 Provider | ✅ 全量 2051 passed，零网络；顶层 import 扫描（含反例）证明真实 adapter/网络库仅函数内延迟 import |

**是。** 本步未修改任何产品代码或 Step 01/02 冻结交付物，未实现 L4 图片盲评，
未下 Gate A 结论，未做图片主观评分；源码/测试/本文件无明文 API key。
