# v0.3 更改书 001 · R1-A（工包 A 语义纠错）交接

状态：**已完成并通过本工包局部离线验收**；无真实 Provider 调用、无 Git 提交、未覆盖任何既有工作区成果。
范围所有者：Flash A。写权限内改动：`visual_intent_agent/intent_engine/prompts.py`、
`visual_intent_agent/policy/decision_policy.py`、`tests/intent_engine/**`、`tests/policy/**`；
经协调者（主代理）明确授权追加：`tests/fixtures/policy_cases/execution_conflict_non_blocking.json`、
`tests/fixtures/policy_cases/hard_conflict_over_missing.json`。

## 1. 依据

- `docs/task_books/mvp_v0.3/REVISION_002_BUILD_FIRST.md`（快速构建优先、文件级分工 A）；
- `docs/task_books/mvp_v0.3/REVISION_001_SHORTEST_PATH.md` §R1-A（本工包唯一语义依据）；
- 协调者 2026-09-15 追加裁定：**停用四条不成立的冲突启发式，含"摄影棚 × 海滩"**；保留确凿冲突。

## 2. 实际改动

### 2.1 `intent_engine/prompts.py` —— 版本 v5（`interpreter.v4` → `interpreter.v5`）

| 项 | 旧（v4） | 新（v5） |
|---|---|---|
| rule 12 COUNTING | 把"一只猫 / 一位老人"整体当反例，明确中文数词也不敢提取 | **明确数词必须提取**：「一只猫/两只狗/三位老人/两个」→ 1/2/3/2，英文 `one/two` → 1/2；**未说数量**（"猫在睡觉"）或英文不定冠词 `a/an` 才保持未设，绝不补 1 |
| rule 13 处所 | 标题 `LOCATIVE-FREE TEXT`，只写"自由处所不得吞成 location" | 标题 `FREE LOCATIVE PHRASES`；自由处所（"趴在壁炉边/坐在窗边/趴在沙发上"）保留在 `subject.description` / `subject.pose_action`，并**明确不得由此推断用户没说的房间**；用户明示地点（"地点是客厅/海边沙滩"、`in a park`、`location: the beach`）必须提取 |

未变：rule 1–11、14（CLEAR IS PER-PATH）、15（MINIMAL）、输出 Schema、证据合同、操作语义、
`INTERPRETER_OUTPUT_SCHEMA` 与 `interpreter._LLMOutput` 的一一对应。系统提示词 4590 → 4877 字符
（仍低于 v3 精简前的 5660）。

### 2.2 `policy/decision_policy.py` —— 版本 v2（`policy.v1` → `policy.v2`）

- **停用全部四条冲突启发式**（判定标准从"两个词能否同时出现"改为"该组合能否被证明互斥"）：
  `hard_conflict.lighting_environment_source`（室内可有自然光）、
  `hard_conflict.style_medium_mismatch`（摄影与水彩质感可混合）、
  `execution_conflict.framing_aspect_mismatch`（景别不等于宽高比要求）、
  `hard_conflict.environment_mode_location`（摄影棚可搭海滩布景，协调者裁定）。
- **保留 registry 合同**（协调者要求，避免跨工包 import 暂态失败）：四条 rule id 仍在
  `CONFLICT_RULES`，`kind` / `path` / `message` 元数据不变；规则表 `conflict_rules` 声明照旧；
  新增 `DISABLED_CONFLICT_RULES` + `_is_disabled()`，谓词恒为 `False` →
  `assess().conflicts` 恒为 `[]`，不阻断、不改写字段，用户表达原样保留。
- 保留 `is_hard_conflict` / `is_execution_conflict` 合同、`_ConflictRule`、`_detect_conflicts`、
  `_select_question` 的优先级分支与全部触发词表（未来有证据可重新注册）。
- 未新增任何冲突推理引擎；未改动 Decision 规则表（仍恰好 9 条）、Resolution 合法性、
  `ready_for_confirmation` 判定与任何安全不变量。

### 2.3 测试（同步修正固化错误语义，未放宽安全测试）

| 文件 | 处理 |
|---|---|
| `tests/intent_engine/test_interpreter_prompts.py` | 版本断言 → v5；注明 R1-A 理由 |
| `tests/intent_engine/test_interpreter_boundaries.py` | 数量边界改名并改反例（"一只猫"从反例改为正例）；`LOCATIVE_MARKER` → `FREE LOCATIVE PHRASES`；新增"不得推断房间"断言；执行上下文接线测试改为 **spy**（不再用已停用规则的结果间接证明） |
| `tests/intent_engine/test_interpreter_v3_boundaries.py` | 版本 → v5；数量正反例改写；rule 13 标题与新示例；长度阈值 5000 → 5200（不钉死字符数）；端到端 7 路径用例改为"全部生效 + 冲突为空 + 值原样保留" |
| `tests/intent_engine/test_interpreter_count_semantics.py` | **新增**：明确中文/英文数量必须提取、未说数量不得补 1、rule 12 不得导致少提取其它路径 |
| `tests/policy/test_policy_conflicts.py` | **重写**为 v2 语义：registry 四条 id 完整、谓词对"已知会命中"的输入也返回 False、四类组合不再冲突/不阻断/不改写、前缀判定仍随 `kind` |
| `tests/policy/test_policy_conflict_cjk.py` | **重写**：四条中文组合由"必须冲突"改为"保留且不冲突"；保留 `_words`/`_contains_phrase` 的 CJK 匹配回归 |
| `tests/policy/test_policy_rules.py` | `POLICY_VERSION == "policy.v2"`；registry 接线断言不变 |
| `tests/policy/test_policy_question_selection.py` | 两条冲突优先用例改为"停用后不再有冲突问题、回归缺失决策问题" |
| `tests/policy/test_policy_scenarios.py` | 场景 7 改为"停用组合不覆盖缺失决策、问题回到 `style.primary`" |
| `tests/fixtures/policy_cases/execution_conflict_non_blocking.json` | 先独立判定（景别不构成宽高比冲突 → `conflicts=[]`、全 block 已解决 → `ready=True`、`question=None`），再用 `assess` 实际输出核对；新增 `r1a_reason` 记录理由 |
| `tests/fixtures/policy_cases/hard_conflict_over_missing.json` | 同上（摄影棚×海滩停用 → `conflicts=[]`；`style.primary` 缺失 → `ready=False`、`question.target_path=style.primary`） |

**未放宽**：PIN、来源校验、确认绑定、Revision 校验、越权 CLEAR 等安全不变量测试全部保留原样。

## 3. 实际测试结果（本工包局部、离线、一次集中）

- `python -m pytest tests/policy tests/intent_engine -q` → **347 passed**（改前同目录 354 passed，
  差值来自新增/改写用例的净变化，无失败）；
- 全仓离线（`--continue-on-collection-errors`）→ **2137 passed, 3 deselected, 1 error**；
  唯一 error 是 `tests/evaluation/test_runner.py` 收集期 `evaluation/runner.py:606`
  语法错误（工包 C 在建的**暂态**，与本工包无 import 关系）：
  `self._hashes = self._compute_hashes(...)        self._run_id = ...`；
- 未做真实调用、未跑图片批次、未做全量反复重跑。

## 4. 跨范围影响与已完成协调

1. **已解决**：`tests/workflow/test_workflow_execution_context.py` 原先固化"全景/wide_shot/远景构图
   必报 execution conflict"；协调者转工包 B 处理后，该文件现为 6 passed（改为"停用后不产生该冲突 +
   spy 证明执行上下文仍接进 `assess`"）。
2. **归工包 C**：`evaluation/fixtures/core_v0_3.jsonl`、`evaluation/annotations/core_v0_3.jsonl`
   中 `s04-conflict-001/002/003` 与 `s02-missing-core-002`/`s09-multiturn-001` 的
   `expected_conflicts` 仍是旧规则；`evaluation/fixtures` / `annotations` 不在本工包写权限内，
   未改动（R1-B 修订时同步）。
3. `tests/evaluation/test_dataset_contract.py` 的 `CONFLICT_RULE_IDS` 只是标注静态校验，不受影响。
4. 本工包新增的 `r1a_reason` 字段仅出现在两个 golden fixture 顶层，golden 测试只读 `intent` /
   `execution_context` / `expected` / `case_id` / `description`，不影响既有断言。

## 5. 剩余事项（未完成，不冒充完成）

- R2 的**真实语义探针**才能验证"明确数词/自由处所/停用冲突"在真实模型输出上的表现；
  离线 Fake 只证明确定性链路，不证明真实语言理解。
- `policy.v2` 下当前**没有任何**已注册的可确证冲突：后续若要恢复冲突检测，必须有新证据并
  在测试中说明"为什么两个要求不能同时成立"（不得回到关键词启发式）。
- R1-B（工包 C）需按 v2 同步评测标注与协议；R1-C（工包 B）负责恢复行为。
- 本工包未创建任何 Git 提交；工作区 dirty 状态与历史成果保持原样。

## 6. 结论

R1-A 的语义纠错已真实落地（v5 / v2）、固化错误语义的测试已同步修正且未放宽安全测试；
可进入 R1-B / R1-C 并行收尾与 R2 最小真实探针。
