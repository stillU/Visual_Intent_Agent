# Gate A 首轮报告（MVP v0.3 · Step 05）

- 结论类型：**No-Go（可修复型）** —— 见第 8 节五条件逐条裁决。
- 运行：`erun_d35a33ffc18d6c1f`（`--nonce gate-a-initial`），
  `dataset_sha256=05195347…c63a64b`、`annotations_sha256=d1712aab…6b57`、
  `config_sha256=3b6159f8…905b`、`protocol_sha256=70cc0b3c…6d93`、
  `protocol_version=gate_a_protocol_v0_3`、`repetitions=2`、`l4_repetition=1`。
- 数据来源（全部只读）：`outputs/evaluation_runs/erun_d35a33ffc18d6c1f/{run.json,summary.json,cases/*.jsonl}`、
  `evaluation/reports/erun_d35a33ffc18d6c1f_layer4.json`、
  `evaluation/reviews/erun_d35a33ffc18d6c1f/{review_manifest.json,unblinding_map.json,scores/scores_user.json}`。
- 本步零写入产品代码 / 评测代码 / 数据集 / 标注 / 协议 / 评分；仅新增本报告与
  `gate_a_initial.json`、`failure_catalog.jsonl`、`docs/handoffs/v0_3_step_05_handoff.md`。
  结束时 `uv run pytest -q` → **2086 passed, 3 deselected**（未越界）。

**四层分开报告，不合并为单一总分。** L1/L2 仅 System B（Baseline `not_applicable`），
L3 双系统同口径，L4 人工盲评。

---

## 1. 运行完整性（先看分母）

| 系统 | 案例运行 | completed | failed | 图片数 |
|---|---|---|---|---|
| Baseline A | 44（22×2 重复） | 44 | 0 | 120 |
| System B | 44（22×2 重复） | 33 | **11** | 59 |

- System B 失败轮共 **13 个**（11 个案例运行失败）：`workflow.invalid_state` ×11、
  `evaluation.no_generation_under_review` ×2；13 轮全部 `flow_deviation=true` 且**保留在分母**。
- LLM 调用失败共 **12 次**：`provider.timeout` ×12（interpreter ×9、feedback_engine ×2；
  其中 s05-delegate-explicit-002 与 s09-multiturn-002 的 t1 各连续两次超时）。
  Baseline A 的 112 次调用**零失败**。
- 图像调用 59 次全部 `status=ok`、`retry_count=0`、`seed=null`（Provider 未返回，未伪造）。
- L4：29 对成对样本、覆盖 19/22 案例；31 条 `counterpart_image_missing`、1 个 null 槽位、
  3 个案例无任何成对样本，全部显式进入分母。

---

## 2. Layer 1 — State Correctness（仅 System B；目标 100%）

| 不变量 | 通过 | 违例 | 结论 |
|---|---|---|---|
| `history_append_only` | 44/44 | 0 | 通过 |
| `no_stale_confirmation` | 44/44 | 0 | 通过 |
| `pinned_not_overwritten` | 44/44 | 0 | 通过 |
| `unauthorized_fields_unchanged` | **42/44** | **8** | **失败 → `gate_a_blocked=true`** |

`summary.json.l1_blocking`：`s08-pin-unpin-001` rep1（`ecase_db61e38e69d0a5c1`）与
rep2（`ecase_6c155a6afdc7a49c`），`failed_invariants=[unauthorized_fields_unchanged]`，
metric record ids `emet_8aebc69dc10e4d11`（rep1）/ `emet_8859330254893a75`（rep2）。

### 2.1 8 次违例的完整路径（逐条落到轮记录）

s08-pin-unpin-001 的标注语义（`annotations/core_v0_3.jsonl`）：t2 PIN `subject.description`；
t3 pinned 被 CLEAR 必须被拒、本轮 `expected_outcome=no_state_change`；
t4 只允许 UNPIN（同批次 CLEAR 仍被拒）；**t5 只允许 CLEAR `subject.description`**，
`forbidden_change_paths=[subject.count, subject.pose_action, composition.framing, environment.mode,
environment.location, style.primary, style.description, lighting.character, camera.angle,
camera.depth_of_field, color.palette]`，`expected_outcome=clarification_expected`。

**rep1（`emet_8aebc69dc10e4d11`，turn t5 = `eturn_24807db8aadfa204`）**
- 系统 `applied_deltas` = `CLEAR subject.description` + **`CLEAR subject.count`** + **`CLEAR subject.pose_action`**
- `intent_values_before.subject.count = 1` → `after = null`；`subject.pose_action = "端坐在钢琴上"` → `null`
- `resolutions_before` 两者均为 `user_specified` → `after` 记录被移除
- 4 条违例（2 路径 × 值/Resolution），`kind ∈ {forbidden_value_changed, forbidden_resolution_changed}`

**rep2（`emet_8859330254893a75`，turn t3 = `eturn_12a7525d937d679a`）**：同样 4 条，
路径与前后值完全相同（`turn_id=t3`）。rep2 的 t4/t5 因状态机拒绝而失败
（`eturn_9205c6cf4358f185`/`eturn_b59160f482797ec8`），所以违例提前发生在 t3。

### 2.2 定性：真实产品缺陷；指标计算与标注口径均无缺陷

- **不是指标 bug**：`evaluation/metrics/state.py:126-189` 的
  `check_unauthorized_fields_unchanged` 逐个比对 `forbidden_change_paths` 的
  值与 Resolution，并另查"变化路径 ⊆ applied_deltas"。本次 8 条都由第一段
  （forbidden 路径逐值变化）产生；第二段（`change_outside_applied_deltas`）零命中，
  说明变化**确实来自被接受的 Delta**，不是记账串扰。
- **不是标注口径问题**：t5 的目标路径只有 `subject.description` 一条；
  `subject.pose_action` 是 `PERCEPTUAL` 决策且 `required_if=when_subject_specified`
  （`policy/decision_policy.py:411-419`），CLEAR 它直接抹掉了 "subject 在做什么" 这一
  已解决决策。上游 Step 01 已冻结"未提及路径必须保持原值"与"Reducer 保留既有
  ResolutionRecord"，标注与之一致。
- **产品侧根因**：`intent_engine/prompts.py:52-59`（`CLEAR removes the value and the
  authorization record` / `Never touch a path the user did not mention`）没有约束
  "删主体"这类语义只应命中原路径；Interpreter 在 s08 t5/t3 把"把猫去掉"扩展为
  `subject` 面三个路径的 CLEAR。Validator 只拦 pinned 路径的 CLEAR
  （`validation.clear_requires_unpinned_path`），因此 `subject.count` /
  `subject.pose_action` 被放行并落库。**归因：intent_engine（Interpreter 语义边界）**。
- **附带观察（未计分）**：rep1 t3（`eturn_7bf48920dfdb9c2e`）同一次扩展被 PIN 规则
  拦住 `subject.description` 的 CLEAR 后，`subject.count`/`subject.pose_action` 的 CLEAR
  也在同批次被拒（`applied_deltas=[]`，符合 `expected_outcome=no_state_change`）——
  说明批量拒绝是"整批"语义，而 t5 在 UNPIN 之后同一批 CLEAR 便整批放行。
- **影响范围**：L1 阻断（Go 条件 1 直接不满足）；同一根因贡献 L2
  `out_of_scope` 中的 `('s08-pin-unpin-001', t3/t5, CLEAR, subject.count/subject.pose_action)`
  4 次；并使 rep2 的 `missing_decision_recall` 掉到 0/1。
- **值得修复**：是。修复目标模块 `intent_engine`（Interpreter 提示词/输出约束），
  不动 `validation`/`policy`/`reducer`（后两者行为是冻结且正确的）。

---

## 3. Layer 2 — Intent Understanding（仅 System B）

| 指标 | 值 | 计数明细 |
|---|---|---|
| Intent Delta Accuracy（主 score = recall） | **0.884**（304/346；`details.precision`=318/374=0.850） | out_of_scope **56**、must_remain_unset_violations **58**、rejection_failures 1/26、rejections_satisfied 7 |
| Missing Decision Recall | **0.736**（59/72） | 13 次未覆盖 |
| Clarification Precision | **0.852**（46 问 / 9 坏问） | ready 轮提问 **3**；must_clarify 满足 **37/52** |
| Conflict Detection | **0.0（0/12）** | blocking 冲突未检出且进入 ready 的**严重失败 6** |
| Delegation Scope Accuracy | **0.864** | under 8 / over 0 |

### 3.1 out_of_scope 56 / must_remain_unset_violations 58 —— 与 L1 违例、与流程错位的关系

逐案例明细（`delta_accuracy.details`）：

- **`subject.count` 是最大单项（但不是"几乎全部"）：56 个 out_of_scope 中 35 个与 `subject.count` 有关
  （SET 30、PIN 3、CLEAR 2）；58 个 must_remain_unset 违例中 50 个是 `subject.count`**，
  覆盖 s01/s02/s03/s04/s05/s06/s07/s08/s09/s10 共 19 个案例运行。标注把 `subject.count` 列入
  `paths_must_remain_unset`（用户从未给出数量），Interpreter 从"一只/一位/一只…"一律
  推出 `value=1, resolution=user_specified`。
  **定性：产品缺陷（intent_engine：无依据的具体化），但与 L1 违例不同根因**——
  L1 违例是"越权 CLEAR 已授权路径"，这里是"凭空 SET 未提及路径"。
  **影响有限**：`subject.count` 不是 `blocking` 决策（补充路径），
  不阻塞 ready，也不改变 L1 结论（该路径不在任何 `forbidden_change_paths` 中）。
- 其余 out_of_scope 构成：`PIN` 扩到 `lighting.character`/`style.primary`/`composition.framing`/
  `environment.location`/`environment.mode`/`subject.pose_action` 共 13 次（主要来自
  s07-single-field-002 t2 的 PIN 范围过宽，其次 s09-multiturn-001 t3、s05-delegate-explicit-001 t2），
  `SET environment.location` 3 次（s02-missing-core-001 t1 ×2、s09-multiturn-002 t1 ×1）、
  `SET composition.framing` 2 次（s01-complete-002 t1、s09-multiturn-002 t1）、
  `SET style.description` 1 次（s03-missing-perceptual-001 t1）、
  `CLEAR subject.pose_action` 2 次（s08-pin-unpin-001 t3/t5，即 L1 违例的同一根因）。
  `must_remain_unset` 违例按路径：`subject.count` 50、`environment.location` 5、`style.description` 3。
- **其中 `environment.location` / `composition.framing` 这一族同时是流程错位的上游原因**：
  - **s02-missing-core-001**（`ecase_fdff98fed948a0d7`/`ecase_562e5220c23ebc21`）：
    Interpreter 把"趴在壁炉边"具体化为 `environment.location="壁炉边"`（标注要求保持 unset），
    于是 t1 只报 `style.primary` 一个 blocking（标注要求 `style.primary` + `environment.location`），
    t2 回答风格后系统直接 ready 并生成 → 会话进入 `WAITING_REVIEW`，
    t3 的脚本化 `clarification_answer` 被状态机拒绝（`eturn_bb409ce262360ce9`/
    `eturn_655ec3b1d12bf440`，`workflow.invalid_state`）。
  - **s09-multiturn-002** rep2 t1（`ecase_4b9281d8215d6ae2`）同类：`environment.location="沙发"`，
    跳过 `must_clarify environment.location`，t2 被拒（失败轮记录见第 5 节）。
- 结论：**out_of_scope/unset 违例不是单一根因**：`subject.count` 一族是"无依据具体化"（35/56）；
  PIN 一族是"保护范围过宽"（13/56）；s02/s09 的 `environment.location`/`composition.framing`
  一族（7/56）是"把地点/构图描述吞进字段"，后者同时造成流程错位与失败运行。

### 3.2 Conflict Detection 0/12 —— 三种候选根因的逐条判定

`expected_conflicts` 共 12 条（6 个轮次 ×2 重复）：3 个 hard（`environment_mode_location`、
`lighting_environment_source`、`style_medium_mismatch`，各 2 次）+
`execution_conflict.framing_aspect_mismatch` 6 次。检出 0，**严重失败 6 次全部是 3 个 hard
冲突在 t1 未检出且系统进入 ready**：

| 案例 | 轮 | rule_id | 系统存的值 | 谓词词表 | 结果 |
|---|---|---|---|---|---|
| s04-conflict-001 | t1 | `hard_conflict.environment_mode_location` | `environment.mode="摄影棚环境"`、`environment.location="海边沙滩"` | `_ENCLOSED_ENVIRONMENT_MODES={studio,indoor,indoors,interior}`、`_OPEN_AIR_LOCATION_TERMS={outdoor,street,…,beach,…}` | 中文值两边都不命中 → 未检出 → `ready=True` → 生成 |
| s04-conflict-002 | t1 | `hard_conflict.lighting_environment_source` | `environment.mode="室内环境"`、`lighting.character="自然光照明"` | `_NATURAL_LIGHT_TERMS={natural light,sunlight,daylight,golden hour}` | 同上 |
| s04-conflict-003 | t1 | `hard_conflict.style_medium_mismatch` | `style.primary="写实摄影"`、`style.description="画面整体要水彩画的质感"` | `_PHOTO_STYLE_TERMS` 命中"写实摄影"？否（词表为英文）；`_NON_PHOTO_STYLE_TERMS` 亦不命中中文 | 双向都不命中 → 未检出 |
| s02-missing-core-002 t3 / s09-multiturn-001 t3,t4 | — | `execution_conflict.framing_aspect_mismatch` | `composition.framing="远景构图"` / `"全景"`、`"wide_shot"` | `_WIDE_FRAMING_TERMS`（英文） | 中文值不命中；且 `assess` 未收到 `execution_context` |

三条候选根因判定：

1. **Interpreter 存中文值导致冻结英文词表谓词不命中 → 成立，且是主根因。**
   证据：`policy/decision_policy.py:142-195`（词表）、`248-299`（谓词）、`674-699`
   （`_detect_conflicts`）。s04 三个案例的 `conflict_rule_ids=[]`、`issues=[]`，
   即 `assess` 确实跑了但谓词返回 False。
2. **`assess` 未携带 1024x1024 `ExecutionRevision` → 成立，但是"第二重保险缺失"
   而非主根因。** `assess()` 在 `intent_engine/engine.py:62,100` 与
   `workflow/review.py:322,563` 四处调用点**全部未传 `execution_context`**，
   因此 `_framing_aspect_mismatch_conflict` 永远在第 291 行 `return False`（死规则）。
   但即使把 execution revision 传进去，中文 `composition.framing` 仍不命中
   `_WIDE_FRAMING_TERMS`（s09 的 `"wide_shot"` 那次恰好是英文，只有它可能被救回）。
   Step 01 的静态复核（`v0_3_step_01_handoff.md` 第 124-128 行）"含 1024x1024 执行上下文"
   是标注推导侧的假设，产品实际从未传入——**这是产品集成缺口，不是 Runner 缺口**
   （Runner 不需要给产品补执行上下文）。
3. **Runner 没传执行上下文 → 不成立。** Runner 的职责是驱动冻结产品入口；
   执行上下文由 `WorkflowService.create_session` 建立（`workflow/service.py:150`，
   `DEFAULT_OUTPUT_SIZE="1024x1024"`）并已落库，只是没被传给 `assess`。

**严重失败 6 例（逐例）**：s04-conflict-001 r1/r2 t1、s04-conflict-002 r1/r2 t1、
s04-conflict-003 r1/r2 t1（metric records `emet_6646ffe53586c7bb`/`emet_59a42cd91a8a8d3e`、
`emet_5faa370f19c5260b`/`emet_df2a1a9aa0e08d66`、`emet_a4db88a9d5f87ee8`/`emet_34f2465667eae094`）。
每个都同时导致：① 未按标注提问 → 后续 `clarification_answer` 被状态机拒绝
（`workflow.invalid_state`）；② 带冲突值的 Prompt 直接送给图像模型
（`unauthorized_addition` 命中"摄影棚"/"自然光"/"写实"）。

**归因：产品缺陷（policy 词表覆盖 + 调用点未传执行上下文），两个模块内聚在 policy/intent_engine。
另有 1 条指标口径观察（见 6.1）。**

### 3.3 Missing Decision Recall 0.736（13 次未覆盖）逐条归因

| 案例 | 轮 | 未覆盖路径 | 根因 |
|---|---|---|---|
| s02-missing-core-001 | t1（rep1/2）、t2（rep1/2） | `environment.location` | 产品：把"壁炉边"具体化，路径不再"缺失" |
| s05-delegate-explicit-002 | t1、t2（各 rep1/2） | `composition.framing`、`lighting.character` | **Provider 超时**：t1 interpreter 连续 2 次 60s 超时 → 无任何 Delta → 系统只问 `subject.description`，首轮委托意图完全丢失 |
| s08-pin-unpin-001 rep2 | t5 | `subject.description` | t5 未执行（t4 已被状态机拒绝） |
| s09-multiturn-002 r1/r2 | t1 | `environment.location` | r1：interpreter 双超时；r2：被具体化为"沙发" |

即：**4 次由 Provider 超时直接造成，8 次由"无依据/错误具体化"造成。**
对照 Baseline A：协议规定 L1/L2 对 Baseline 一律 `not_applicable`（结构性缺席），
因此 Go 条件 2 的 Missing Decision 分支只能按"结构性缺席 + 绝对召回"评估（第 8 节）。

### 3.4 Clarification Cost 明细

- 22 案例 × 2 重复共 **46 次提问**（≈2.09 问/案例运行；`questions_asked` 合计）。
- **9 次坏问**，但只来自 3 个真实缺陷（同一缺陷跨轮/跨重复重复计数）：
  - s05-delegate-explicit-002（6 次）：t1 unexpected_path、t2 unexpected_path、t3 ready 轮提问
    ——全部是 `subject.description`；上游起因是 t1 双超时。
  - s09-multiturn-002 r1（2 次）：t1 unexpected_path、t2 ready 轮提问 —— `subject.description`。
  - s08-pin-unpin-001 r2 t3（1 次）：unexpected_path `subject.pose_action`
    —— Interpreter 把"把猫去掉"扩成 pose_action 的 CLEAR，被 PIN 规则拒后重新就该路径提问。
- `must_clarify` 满足 **37/52**；未满足的 15 次即上表（含 s06-delegate-vague-002 的
  14/14 全部满足——该案例是数据集中表现最好的一例：4 问全中、delta_accuracy 1.0、
  但按冻结单问题策略永远无法批量授权，因此没有图片，L4 记 missing_data）。
- 用户为完成一个案例需要的轮次/字数：System B 平均每案例运行 **1.05 次提问**（46/44），
  但 t2 之后才首次出图的案例运行显著更多；且 **44 次案例运行中 11 次（25%）以状态机
  拒绝中断**（其中 12 轮未产出任何 Prompt）。
- 与收益同表：直接收益只有 `edit_success` 的 +0.4（见第 8 节条件 3/4）。

### 3.5 Delegation Scope 0.864（under 8 / over 0）

`under_delegated` 集中在 3 个案例（各 2 重复）：`s05-delegate-explicit-002`
（`composition.framing`、`lighting.character`，Provider 超时）、
`s09-multiturn-001`（`lighting.character`，t1 超时）、`s09-multiturn-002`
（`environment.location`，超时 + 具体化）。**over=0**：系统从不越权委托。
即该指标的全部损失同样可归因到 Provider 超时 + t1 语义覆盖，
而不是"委托规则被误用"。

---

## 4. Layer 3 — Prompt Semantics（Baseline A 与 System B 同口径；文本层代理）

| 指标 | Baseline A | System B |
|---|---|---|
| Intent Coverage | 0.978（165/168，miss 2） | 0.967（150/156，miss 5） |
| Model Compatibility | **1.0**（112/112） | **1.0**（59/59） |
| Preservation | **1.0**（87 kept / 0 lost，34 对） | **0.691**（47 kept / 35 lost，33 对） |
| Unauthorized Addition hits | 2（`guard_bypassed` 2） | **6**（`guard_bypassed` 6）；`guard_triggered` 双 0 |

### 4.1 Preservation B 0.691 的逐案例分解 —— 判定为**指标口径缺陷（失真），不是语义丢失**

我把全部 33 对（system_b）与 34 对（baseline_a）重建，逐个读上一轮/本轮实际 Prompt 文本，
结论是**决定性的**：

| 分类 | 对数 | 损失关键词组 | 说明 |
|---|---|---|---|
| 两侧都有 Prompt（可比较） | **23**（B）/ 34（A） | **B 的 35 次 "groups_lost" 中 0 次来自这里** | 23 对全部 kept，无一丢失 |
| 本轮修改轮未产出 Prompt | **10**（B）/ 0（A） | **35（全部）** | 评测器按"保留 0"保守计 |

未产出 Prompt 的 10 对（各含 2~3 个关键词组）：
s08-pin-unpin-001 r1 t2→t3、t4→t5；r2 t2→t3、t2→t4、t2→t5；
s08-pin-unpin-002 r1/r2 t1→t2；s09-multiturn-003 r1/r2 t1→t2；
s10-vague-feedback-001 r1/r2 t1→t2；s10-vague-feedback-002 r1/r2 t1→t2。
**这些轮不生成图片是"期望行为"**：s08 t3/t5 是 pinned-CLEAR 被拒后的澄清/拒绝、
s09-003 与 s10-001/002 t2 是系统按标注 `must_clarify` 正确地提出了问题
（标注 `expected_outcome=clarification_expected`）。

抽查实例（语义是否真丢失）：
- s08-pin-unpin-002 r1：t1 Prompt = `subject: 一只法斗犬, subject count: 1, pose: 趴在地毯上,
  framing: 中景, environment: 室内, location: 儿童房, lighting: 明亮光线, style: 卡通插画`；
  t3 Prompt = 同文仅 `framing: 特写`。"法斗/卡通/儿童房"三组 100% 保留。
- s09-multiturn-003 r1：t1 与 t3 的 Prompt 中"三色猫/窗台"均在，t3 追加 camera angle/depth of field。
- s10-vague-feedback-002 r1：t1 与 t3 的"兔/水彩/森林"三组全在，t3 仅 framing 中景→特写。
- 唯一的 B 侧"内容减少"是从未被比较的口径：s08-pin-unpin-001 的 PIN 保护使图片保持含猫，
  语义上**保留了**关键词（Prompt 在 t2/t4 仍含"猫/钢琴/戏剧"）。人工 L4 的
  `attribute_preservation` B=A=3.5 与此一致。

**修正后该指标在"可比较对"上为 47 kept / 0 lost = 1.000（与 A 相同），
33 对中的 10 对（30%）是不应进入"丢失"分子的无 Prompt 对。**
因此 L3 Preservation 的 0.691 **不能**作为"B 多轮漂移更严重"的证据；
Go 条件 2 的"多轮 Drift"分支在 L3 上无有效证据（L4 打平）。

指标实现位置：`evaluation/metrics/prompt.py:190-210`（`current.prompt_text is None` → `kept=0`
且把上一轮全部命中组记为 `lost_groups`）。协议第 5 节本身允许 `missing_data` 三态，
但此处选择了"保守计 0"；**这是冻结口径内的实现选择，是否正确须由协议所有者裁决**
（见第 9 节修正路径），我不单方面改判。

### 4.2 Unauthorized Addition B 6 vs A 2、guard_triggered 0

| 侧 | 案例 | 命中关键词 | 实际最终 Prompt 片段 |
|---|---|---|---|
| B r1/r2 | s04-conflict-001 | `摄影棚` | `environment: 摄影棚环境, location: 海边沙滩` |
| B r1/r2 | s04-conflict-002 | `自然光` | `environment: 室内环境, lighting: 自然光照明` |
| B r1/r2 | s04-conflict-003 | `写实` | `style: 写实摄影, style detail: 画面整体要水彩画的质感` |
| A r1/r2 | s04-conflict-003 | `写实` | `…写实摄影风格，画面整体具有水彩画质感…` |

- **6 次 B 侧命中全部是 §3.2 冲突未检出的下游后果**：用户确实说了冲突的两面，
  正确行为应是提问而不是把两面都写进 Prompt。命中发生在同一批案例、同一轮次。
- **`prompt.unauthorized_addition` 防护为何 0 触发？** 因为它测的是**另一件事**：
  `prompt_engine/engine.py:293-307` 的 `check_source_bindings`/`check_clause_coverage`
  只在"clause 无合法 source binding"或"已解决路径没有绑定 clause"时抛错。
  这里的每个 clause 都能回溯到已确认 Intent（`摄影棚环境` 就是用户原文），
  所以防护正确地没有触发。**"guard_bypassed" 这个字段名会把"编译期授权防护未触发"
  误读成"防护被绕过"，实际是防护与 must_not_mention 语义不同层次。**
- 定性：命中本身=**产品缺陷（冲突未检出）**；`guard_bypassed` 字段语义=**报告措辞/口径问题**
  （低优先级，不影响分数）。

### 4.3 Intent Coverage 差异

B 0.967 vs A 0.978，差距来自 5 个案例运行 `missing_data`（无最终 Prompt：
s02-missing-core-001 r1（t3 未执行）、s06-delegate-vague-002 r1/r2（设计上无图）、
s09-multiturn-002 r1/r2（无图））与 1 个真实漏组。A 的 2 个 `missing_data`
同样来自 s06-delegate-vague-002。**方向一致、量级相近，不构成差异证据。**

---

## 5. 11 次运行失败 / 13 个失败轮的因果链裁定

`workflow.invalid_state` ×11、`evaluation.no_generation_under_review` ×2。

| 组 | 案例（重复） | 失败轮 | 会话状态 | 上游根因 | 层级裁定 |
|---|---|---|---|---|---|
| A | s02-missing-core-001 r1/r2 | t3 `clarification_answer` | `WAITING_REVIEW` | Interpreter 把"壁炉边"具体化为 `environment.location`，缺失决策消失 → t2 即 ready | **产品缺陷**（脚本映射正确） |
| B | s04-conflict-001/002/003 r1/r2（6 轮） | t2 `clarification_answer` | `WAITING_REVIEW` | 硬冲突未检出 → t1 即 ready | **产品缺陷** |
| C | s08-pin-unpin-001 r2 | t4/t5 `image_feedback` | `WAITING_CLARIFICATION` | t3 的越权 CLEAR 被拒后就 `subject.pose_action` 提问（标注要求 `no_state_change` 且不问） | **产品缺陷**（与 L1 同根因） |
| D | s09-multiturn-002 r1 | t3 `image_feedback`、t4 `accept` | `WAITING_CLARIFICATION` | t1 interpreter 连续 2 次超时 → 无 Delta → 只问 `subject.description`；t2 "背景你来安排"因无待答问题被拒为 `validation.unauthorized_resolution_claim` | **Provider 波动触发 + 产品无降级路径**；协议映射次之 |
| E | s09-multiturn-002 r2 | t2 `clarification_answer` | `WAITING_REVIEW` | 同 A 组（`environment.location="沙发"` 具体化） | **产品缺陷** |

**"数据集/协议缺陷还是产品缺陷"的裁定：**

- 协议 §3 的映射确实是"按脚本 `kind` 直送对应入口"，因此当产品把会话带进
  `WAITING_REVIEW` 时，脚本化的 `clarification_answer` 必然被拒。但——
  **这 11 次拒绝全部是产品先偏离了标注 `expected_outcome` 的下游症状**：
  每一组的 t(n) 失败轮之前，都有一个"应该提问/不该 ready 却 ready 了"的产品偏差。
  反过来看 A/C/E 组的用户文本（"地点在客厅吧"、"现在把猫去掉"）
  **都是该状态下合法的用户在正常操作**，v0.2 状态机拒绝它们是正确的冻结行为。
  因此**不能**把这 11 次记为协议/数据集缺陷。
- 协议确实存在一处**健壮性缺口**（非本次失败的直接成因）：`Runner` 按脚本 `kind`
  而非**当前会话状态**路由；当出现上述偏差时，没有任何"用户文本 → 该状态允许的入口"
  的兜底。本次它只造成"失败原样保留"，没有造成任何分数被隐藏或美化——
  影响是让失败模式变多、让"用户流程中断"的代价被计入（这是真实代价，应保留）。
- **D 组的特殊性（必须记入 Provider 波动）**：s09-multiturn-002 r1 t1 与
  s05-delegate-explicit-002 t1（r1+r2）各有**连续两次 60s interpreter 超时**
  （`provider.timeout`，`retryable=true`，adapter 已用尽 2 次重试），
  这是 Provider 侧长尾，不是产品逻辑错误。但产品在 interpreter 超时后
  **没有编排层降级/重试**，只记 issue 并继续用空 Intent 走澄清，
  导致 t1 的用户信息永久丢失、整个案例连锁失败。这属于
  **"Provider 波动 + 产品健壮性缺口"**，与"冲突/具体化"类产品缺陷分开计数。
- 12 次 LLM 失败全部是 `provider.timeout`，**Baseline A 零失败**——
  这一侧差异无法用产品逻辑解释，必须按 Provider 波动保留（协议 §7 不允许外部重试）。

---

## 6. Layer 4 — Image Result（人工盲评；n 与分布）

评审人 `change`，29/29 对完成，`completed_at=2026-09-15T02:37:11Z`。
解盲映射 `evaluation/reviews/erun_d35a33ffc18d6c1f/unblinding_map.json`（盲评结束后可读）。
**全部样本含 1 个 null 槽位与 31 条缺图，未剔除。**

| 维度（范围） | Baseline A | System B | 配对裁决 |
|---|---|---|---|
| User Preference（29 对） | **15 胜** | 12 胜 / 2 平 | **A 领先**（multi：A 14 / B 11 / 2 平；single：1:1） |
| Intent Alignment（1–5，全部） | 3.138（n=29；dist 1:2, 2:9, 3:3, 4:13, 5:2） | **3.321**（n=28，1 missing；dist 1:2, 2:5, 3:5, 4:14, 5:2） | 配对 n=28：A 胜 10 / B 胜 11 / 平 7，均值差 B−A = **+0.214** |
| Intent Alignment（multi） | 3.185（n=27） | **3.346**（n=26） | — |
| Intent Alignment（single） | 2.5（n=2） | 3.0（n=2） | 样本 2，不足以支撑 |
| Attribute Preservation（multi） | 3.5（n=16；dist 1:1, 3:6, 4:8, 5:1） | 3.5（n=16；dist 1:1, 3:7, 4:6, 5:2） | 配对 n=16：A 胜 3 / B 胜 3 / 平 10，均值差 **0.000** |
| Edit Success（multi） | 3.2（n=10；dist 1:1, 2:4, 4:2, 5:3） | **3.6**（n=10；dist 2:3, 3:1, 4:3, 5:3） | 配对 n=10：A 胜 3 / B 胜 4 / 平 3，均值差 **+0.400** |

**逐对证据（Edit Success 的全部 10 对）**：
B 胜 —— s08-pin-unpin-001 t2（2:1）、s09-multiturn-001 t3（4:2）、s09-multiturn-003 t3（5:2）、
s10-vague-feedback-002 t3（5:2）；A 胜 —— s08-pin-unpin-001 t4（2:5）、
s09-multiturn-001 t4（3:4）、s10-vague-feedback-001 t3（4:5）；平 ——
s07-single-field-001 t2（5:5）、s07-single-field-002 t2（4:4）、s08-pin-unpin-002 t3（2:2）。

**逐对证据（Attribute Preservation 的全部差异对）**：B 胜 s07-single-field-001 t1（4:3）、
s08-pin-unpin-001 t4（3:1，即 PIN 保猫）、s09-multiturn-001 t3（5:3）；
A 胜 s09-multiturn-001 t4（1:4）、s09-multiturn-003 t1（3:4）、s10-vague-feedback-001 t1（3:4）；
其余 10 对完全相同。

### 6.1 一处必须记录的 L4 有效性问题（不是产品缺陷）

`pair_018`（s08-pin-unpin-001 **t4**）：评审人给 System B `intent_alignment=5`
（理由"猫被删了"——**指 Baseline 侧被删**），却在 `user_preference` 选 `right`
（= Baseline A），并给 Baseline `edit_success=5`、System B `edit_success=2`。
而该案例的标注 `image_evaluation_dimensions[edit_success].focus` 与
`notes` 明确写："t3/t4 的『去猫』按 PIN 保护语义**不落实**（这是期望行为而非失败）"，
且协议 §4 规定 `edit_success` **以标注为准**。
→ 这一对的 `edit_success`/`preference` 与标注直接冲突：
**Baseline 的"删猫"是违反会话内 PIN 承诺的行为，却在偏好上被判为胜、在 edit_success 上得 5。**
它不是产品缺陷（产品在 t4 正确保护了 pinned 内容），而是**人工 L4 与评审须知不一致**
造成的测量偏差（影响：Edit Success B/A 差被这一对拉低了 5 分，偏好记 A 一胜）。
其余 28 对未发现同类直接冲突。

---

## 7. 核心比较指标同表（协议 §6 口径）

| 准则 | 指标 | Baseline A | System B | 方向 |
|---|---|---|---|---|
| 静默决策（L3 代理） | Unauthorized Addition hits | 2 | 6 | **B 差**（且 A 的 2 次与 B 的 6 次同为 s04 冲突下游） |
| 静默决策（L3 代理） | Intent Coverage | 0.978 | 0.967 | 无实质差异 |
| 缺失决策（L2 / 结构性缺席） | Missing Decision Recall | `not_applicable`（无此能力） | 0.736（59/72） | 绝对水平**未达可用** |
| 多轮漂移（L4） | Attribute Preservation | 3.5 | 3.5 | 平 |
| 多轮漂移（L3 代理） | Preservation | 1.0 | 0.691（**口径失真**；可比较对为 1.000） | 无有效证据 |
| 编辑成功（L4） | Edit Success | 3.2 | **3.6** | B 微弱领先（配对 4:3:3） |
| 意图对齐（L4） | Intent Alignment | 3.138 | 3.321 | B 微弱领先（配对 11:10:7） |
| 用户偏好（L4） | User Preference | **15 胜** | 12 胜 / 2 平 | **A 领先** |
| 澄清成本（L2） | 提问 46 / 坏问 9 / ready 轮提问 3 / must_clarify 37-52 / 11 次运行中断 | 无（不提问） | — | 见条件 4 |

---

## 8. 五条 Go 条件逐条裁决

### 条件 1 — L1 核心不变量 100% 通过 → **不满足**
`unauthorized_fields_unchanged` 42/44、8 次违例（s08-pin-unpin-001 r1/r2，§2），
`summary.json.gate_a_blocked=true`。四条中三条 44/44，但**任一条失败即不满足**。
→ 按任务书与协议 §6 明文，**L1 不 100% 通过不得判 Go**。

### 条件 2 — Silent Decision / Missing Decision / 多轮 Drift 至少一项明确改善 → **不满足**
- Silent Decision：L3 Unauthorized Addition **B 6 > A 2**（B 更差）；"未说明维度被静默具体化"
  的案例数：B 侧 `subject.count` 具体化覆盖 19 个案例运行，A 侧同类仅 2 次 Keyword 命中。
  两项都不支持 B 改善。
- Missing Decision：按协议 §6 的操作化，Baseline 侧为"结构性缺席"
  （L2 一律 `not_applicable`，不伪装 0/1 分），因此只能以 B 的绝对水平衡量：
  **0.736（59/72）**，13 次未覆盖中 8 次是产品把"缺失决策"错误具体化、4 次是 Provider 超时。
  一个 26% 的缺失漏报率不构成"明确改善"。
- 多轮 Drift：L4 Attribute Preservation **完全打平（3.5/3.5，配对 3:3:10）**；
  L3 Preservation 的 B 0.691 经逐对复核为**口径失真**（§4.1，可比较对 23/23 全保留），
  **不能**作为"B 漂移更严重"的证据，也不能反过来当作"B 更好"的证据。
- → 三个分支全部不成立。

### 条件 3 — Edit Success 或 User Preference 至少一项有可测量优势 → **弱成立（但证据薄）**
- Edit Success：B 3.6 vs A 3.2（n=10 每侧，配对 4 胜 3 负 3 平，均值差 +0.4）；
  分布上 B 无 1 分、A 有 1 个 1 分。**是"可测量"的差异（同案例同轮次配对）**，
  但 ① 样本仅 10 对，② 其中最大的一组差异（s08 t4）源自 §6.1 的评审须知偏差，
  ③ 另一组（s10-002 t3）B 明显更好、s09-003 t3 B 明显更好。
  综合判断：**方向为 B 微弱领先，但不足以单独支撑 Go。**
- User Preference：A 15 : B 12（2 平），**A 领先**，明确不满足。
- → 条件 3 在字面上被 Edit Success 满足，但属于"弱满足"。

### 条件 4 — Clarification Cost 没有抵消上述收益 → **不满足**
同表评估（收益 = 条件 3 的 +0.4 edit_success；成本 = 下列全部）：

| 成本项 | 数值 |
|---|---|
| 提问总数 | 46（22 案例 ×2 重复），≈1.05 次/案例运行 |
| 坏问 | 9（3 个真实缺陷跨轮/跨重复重复计数） |
| ready 轮提问 | 3（s05-002 t3 各重复、s09-002 r1 t2） |
| must_clarify 未满足 | 15/52（含 Provider 超时的 6 次） |
| **运行中断** | **11/44 案例运行失败、13 个失败轮、25% 的案例运行被状态机拒绝** |
| 未产出 Prompt 的轮 | **23 轮**（System B 59 张图 / 66 次生成机会） |
| Time-to-ready | s02/s04 组在 t2 就被迫终止；s09-002 需要 4 轮仍未完成 |

**判定：澄清机制目前既没有换来缺失决策召回（0.736）、也没有换来冲突检出（0.0），
却带来了 25% 的运行中断率与 3 次 ready 轮误问。成本明确抵消并超过微弱收益。**

### 条件 5 — 重复实验可复现 → **部分满足，有一项方向不一致**
逐重复对照（`rep1` vs `rep2`，`counts` 层面）：

| 指标 | rep1 | rep2 | 方向 |
|---|---|---|---|
| L1 `history/no_stale/pinned` | 22/22 通过 | 22/22 通过 | **一致（不变量结论不变）** |
| L1 `unauthorized_fields_unchanged` | 21/22 通过、4 违例 | 21/22 通过、4 违例 | **一致** |
| delta_accuracy（pooled / 每案例均值） | 150/173=0.867 / 0.874 | 154/173=0.890 / 0.894 | 轻微不一致（rep2 略好） |
| missing_decision_recall | 30/36=**0.833** | 29/36=**0.806** | **rep2 变差（方向相反）** |
| clarification_precision | 19/24=0.792 | 18/22=0.818 | 轻微不一致 |
| conflict_detection | 0/6=0.0 | 0/6=0.0 | 一致（都是 0） |
| delegation_scope_accuracy | 0.864 | 0.864 | 一致 |
| intent_coverage（B） | 0.966 | 0.968 | 一致 |
| preservation（B） | 0.688 | 0.694 | 一致（都落在失真口径上） |
| unauthorized_addition hits（B） | 3 | 3 | 一致 |
| model_compatibility | 1.0 | 1.0 | 一致 |

**关键指标方向一致性检查未通过**：`missing_decision_recall` 在两次重复中反向
（rep2 更差），delta_accuracy 出现轻微反向；其余指标一致或稳定在 0/失真值上。
考虑到单次重复仅 36 个 blocking 标注点、且 12 次 Provider 超时在两次重复间
分布不对称（如 s09-multiturn-002 t1 只在 r1 双超时、r2 单超时后成功），
**该不一致更可能是 Provider 波动而非产品不确定性**，但按协议 §6 条件 5 的字面口径，
"L2/L3 关键指标方向一致"**未完全成立**。

### 结论
条件 1 **不满足**（L1 阻断，硬门槛）；条件 2 **不满足**；条件 3 弱满足；
条件 4 **不满足**；条件 5 部分满足。→ **No-Go**。

这是**可修复型 No-Go**：失败集中在 4 个可定位的模块（intent_engine、policy、
review 的 L4 执行、以及 1 处 L3 口径），不是架构性失败，也不是"没有优势"的全盘否定
（Edit Success / Intent Alignment 的方向确实偏向 B）。

---

## 9. 归因总表与下一步建议

### 9.1 产品缺陷（可修复，须在 Step 06 修并复评）

| # | 缺陷 | 模块 | 证据 | 影响 |
|---|---|---|---|---|
| P1 | 主体删除类语义把 CLEAR 扩到 `subject.count`/`subject.pose_action` | `intent_engine` | `emet_8aebc69dc10e4d11`、`emet_8859330254893a75`；`eturn_24807db8aadfa204`、`eturn_12a7525d937d679a` | **L1 阻断**（Go 条件 1） |
| P2 | 冲突谓词词表只认英文，Interpreter 存中文 → 3 条 hard 冲突全漏 | `policy`（`decision_policy.py:142-195,248-299`）+ `intent_engine` 值规范 | 6 例严重失败（§3.2）+ 6 次 `unauthorized_addition` | Conflict Detection 0/12；6 次冲突直接进图 |
| P3 | `assess()` 四处调用点未传 `ExecutionRevision` → `framing_aspect_mismatch` 是死规则 | `intent_engine/engine.py:62,100`、`workflow/review.py:322,563` | `emet_99619029b4aefa74`/`emet_2d9a6f824666d975`（s09-multiturn-001 t3/t4）、`emet_58f72f7ec1585b26`/`emet_6662dfd26d2e07c2`（s02-missing-core-002 t3） | 潜在（即使词表修好也仍漏） |
| P4 | 无依据具体化：`subject.count` 一律 SET 1（35/56 out_of_scope、50/58 unset 违例）；`environment.location` 吞掉"壁炉边/沙发" | `intent_engine` | `emet_084e77929579b45c`（s02-missing-core-001）、`emet_6bb1e99d2e0cb4ab`（s09-multiturn-002 r2）、`emet_d9c80c0d061db21d` | 缺失决策召回 0.736（8/13 漏报）、8 次流程错位 |
| P5 | PIN 授权范围过宽（一次 PIN 覆盖 5~6 路径） | `intent_engine` | `emet_78618550965e1762`、`emet_8d04c6d11fdeb3e0`（s07-single-field-002 t2，13/56 out_of_scope） | L2 精确率；rep2 的 UNPIN 触发 `validation.unpin_requires_pinned_path` |
| P6 | interpreter/feedback_engine 超时后无编排层降级，t1 失败即整案连锁 | `workflow` / `intent_engine` | 12 次 `provider.timeout`（§5-D 组） | 4 次缺失决策漏报、2 次整案失败 |

### 9.2 Provider 波动（不修产品逻辑，但影响分母）

12 次 `provider.timeout`（interpreter 9 / feedback_engine 2 / 重试内 3），
Baseline A 0 次。影响：s05-delegate-explicit-002 两个重复全废、
s09-multiturn-002 r1 整案失败、s01-complete-001 r2 与 s09-multiturn-001 r1 的 t1 部分退化。
**不是产品缺陷**；协议 §7 禁止外部重试，因此只能如实保留。

### 9.3 实验协议 / 数据集 / 指标口径问题（不得包装成产品修复）

| # | 问题 | 类别 | 证据 | 对 Step 07 复评可行性的影响 | 建议修正路径 |
|---|---|---|---|---|---|
| Q1 | Preservation 把"修改轮未产出 Prompt"按保留 0 计入丢失分子 | 指标口径（实现遵循协议字面，但协议未区分"未生成"与"丢失"） | §4.1：35/35 lost 全部来自 10 个无 Prompt 对；可比较对 23/23 全保留 | **当前 L3 Preservation 0.691 不可用于任何结论**；Step 07 若沿用同口径，该指标仍不可解读 | 新版本协议（`gate_a_protocol_v0_3_1`）+ 新指标版本：无 Prompt 的修改轮改记 `missing_data`（协议 §5 已有该三态），**不改变任何已有分数**；须由协议所有者书面批准，不得为提分改口径 |
| Q2 | Runner 按脚本 `kind` 而非当前会话状态路由；产品偏差后脚本输入必然被拒 | 协议健壮性缺口（本次**未**造成分数失真，13 个失败轮全部留在分母） | §5 全部 13 轮 | 不影响复评可行性；影响失败分类与"用户流程中断"代价的可比性 | 可选新版本协议：保留 kind 映射，但显式规定"若会话状态与脚本 kind 不符，原样记 `flow_deviation` 并以该状态允许的入口提交"；**不得**借此隐藏失败 |
| Q3 | L4 评审与标注 `edit_success` 须知冲突（s08 t4） | 人工评测执行偏差 | §6.1（`pair_018`） | Edit Success 的 +0.4 中有一对被系统性拉低；Step 07 应复核该对 | 不改评分实现；在 Step 07 复评前把 `image_evaluation_dimensions.notes` 的"期望行为"提示**显式**放进盲评界面（Step 04 只放了 focus，未强制 notes） |
| Q4 | `guard_bypassed` 字段把"授权防护未触发"称为"被绕过" | 报告措辞 / 语义混淆 | §4.2；`evaluation/metrics/prompt.py:112-139` | 不影响数值与复评 | 新版本报告字段注释或改名为 `guard_not_triggered_with_hits`；属文档级修正 |

**未被证实为缺陷的三项**（明确排除）：
- `pinned_not_overwritten` 44/44 通过 → 对 pinned 路径的 SET 未被计为违例（Step 01 已知限制 5 口径正确执行）。
- `missing_decision_recall` 的 `not_applicable` 对 Baseline → 协议 §3 明文，非伪装。
- `s06-delegate-vague-002` 无图 → 冻结单问题策略的必然结果（14/14 召回、4/4 提问全中），
  不是失败。

### 9.4 下一步指令

**No-Go → 建议进入 Step 06（证据驱动的定向修正），并在 Step 07 用同一冻结协议复评。**
Step 06 可验证修正目标清单（每条都有本报告的失败案例与记录 ID 作为依据）：

1. **P1（P0，阻断级）**：`intent_engine` —— 回归用例 s08-pin-unpin-001 r1 t5 & r2 t3；
   期望 `applied_deltas` 只含 `CLEAR subject.description`，`subject.count`/`subject.pose_action`
   值与 Resolution 不变；验证 L1 `unauthorized_fields_unchanged` 44/44。
2. **P2（P0）**：`policy` —— 回归用例 s04-conflict-001/002/003 t1；
   期望 `conflict_rule_ids` 命中 3 个 hard rule；验证 Conflict Detection ≥ 3/3（hard 部分）
   且 `severe_failures=0`。
3. **P3（P1）**：`intent_engine`/`workflow` —— 回归用例 s02-missing-core-002 t3、
   s09-multiturn-001 t3/t4；期望 `execution_conflict.framing_aspect_mismatch` 命中。
4. **P4（P1）**：`intent_engine` —— 回归用例 s02-missing-core-001 t1/t2、
   s09-multiturn-002 t1；期望 `environment.location` 保持 unset 并成为 blocking 缺失；
   Baseline 侧对比 `subject.count` 具体化在 A 不适用，故以标注 `paths_must_remain_unset`
   为回归真值；验证 Missing Decision Recall ≥ 15/18（s02/s09 相关点）。
5. **P5（P2）**：`intent_engine` —— 回归用例 s07-single-field-002 t2；
   期望 PIN 只在标注 `expected/acceptable` 路径上。
6. **Q1（P1，需协议所有者批准）**：先出新版本协议 + 指标版本，再改
   `evaluation/metrics/prompt.py:evaluate_preservation` 的无 Prompt 分支。
7. **Q3（P2）**：Step 07 复评前修盲评界面，把 `notes` 的期望行为提示展示给评审人
   （不重算已有 29 对分数，仅在复评时生效）。

**不进入 Step 08**（L1 阻断 + 4/5 条件不满足）。
**不建议停止 v0.3**：Edit Success/Intent Alignment 的方向性优势、以及全部失败都能落到
具体模块，说明核心假设未被否定，只是尚未被正确地测量与实现。

---

## 10. 复核与可信度自查

**强证据（可直接作为裁决依据）**
1. L1 8 次违例的完整路径、前后值与 Resolution：来自逐轮 `intent_values_before/after` 与
   `resolutions_before/after` 原始记录 + `applied_deltas`，且与指标实现逻辑逐行核对
   （`metrics/state.py:126-189`）。**结论确定：产品缺陷，非指标/标注问题。**
2. Preservation 的 35 次 "lost" 全部来自无 Prompt 的修改轮：由我重建 33/34 对配对
   （脚本读 `turn.prompt_text` 与实际 Prompt 文本）得出，且与本轮新算的
   `counts.groups_kept/lost` 完全一致（47/35、87/0）。**结论确定：指标口径失真。**
3. 10 次 `workflow.invalid_state` 的前置状态与上游偏差：来自逐轮 `state_before/after`、
   `unresolved_decisions`、`question`、`applied_deltas` 与失败 `error` 原文字段。
   **结论确定：5 组中 4 组为产品缺陷、1 组为 Provider 超时触发。**
4. 冲突 0/12 的机理：`assess` 调用点 `grep` 全量核对（4 处均未传 `execution_context`）
   + 三案例实际存储值 + 词表常量逐条比对。**结论确定：产品缺陷（词表 + 集成）。**
5. 测试与范围：`uv run pytest -q` → 2086 passed, 3 deselected（与 Step 04 基线一致），
   证明本步未改任何代码。

**中等证据（结论方向可靠，数值需 Step 06/07 复核）**
6. `subject.count` 具体化的计数（35/56 out_of_scope、50/58 unset 违例）依赖
   `details.out_of_scope_deltas` 与 `must_remain_unset_violations` 的逐条列表（已逐条核对并分类），
   但"它是否是缺陷"依赖
   "用户未给数量"这一语义判断——我按标注判为缺陷，Step 06 可用真实 Prompt 的
   `subject count: 1` 渲染结果作为旁证。
7. Provider 超时的"决定性影响"（4 次缺失决策漏报 + 2 次整案失败）：来自失败调用链，
   但单次重跑可能不出现，因此**波动幅度不可从本次单 run 估计**。
8. 条件 5 的"方向不一致"：基于 pooled counts（36 个标注点），
   **样本极小**，我只声明"未完全成立"，不声明"不可复现"。

**弱证据 / 纯假设（必须由 Step 06 验证，不得作为修复依据）**
9. L4 `pair_018` 的评审偏差：我推断评审人未看到 `notes` 的 PIN 提示（Step 04 已知
   "HTML 只展示 focus + 用户文本"），但**没有直接证据**证明评审人当时看到了什么；
   Step 06 需核对盲评 HTML/模板内容。
10. P6（无编排层降级）是否"值得修"：取决于 Step 06/07 是否把 Provider 重试策略
    纳入修订范围（协议 §7 目前禁止）。**当前只作记录，不建议在 v0.3 内扩大重试口径。**

**未覆盖的盲区**
11. LLM 原始响应文本未落盘（只落 `issues` 与结构化结果），因此
    "Interpreter 是否曾自报冲突/委托"只能从 `issues` 推断；
    `detected_conflicts` 的 code 恒被覆盖为 `interpreter.conflict_detected`
    （Step 03 已知限制 4），所以**无法**从本次 Artifact 证明 LLM 是否识别了冲突。
12. 单评审人（`change`）、n=29 对、L4 每案例 1 次成对：L4 的 +0.4/+0.2 差异**不构成
    统计功效**，只能按"方向性证据"使用。
13. 本报告未重跑任何真实 Provider（任务书硬约束），所有结论均来自既有 Artifact。
