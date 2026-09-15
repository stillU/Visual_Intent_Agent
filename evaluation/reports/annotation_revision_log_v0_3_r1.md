# r1 标注修订日志（core_v0_3 → core_v0_3_r1）

- 依据：`docs/task_books/mvp_v0.3/REVISION_001_SHORTEST_PATH.md` §R1-A/§R1-B、
  `REVISION_002_BUILD_FIRST.md`、A 工包语义裁定。
- 生成方式：`evaluation/tools/build_core_v0_3_r1.py`（只读旧冻结文件，**不读任何系统输出**；
  逐项修订可复核）。旧 `core_v0_3` fixture/annotation 只读保留，未改动。
- 原则：只修改有独立语义理由的答案与必要对话轮次；未受影响案例逐字保留
  （含 `expected_deltas`、`paths_must_remain_unset`、`expected_carry`、L4 维度焦点等）。
  不为迎合任何被测系统或基线输出而改动期望。

## 1. 逐类修订

### 1.1 数量语义（R1-A #1）

- 旧规则：单数名词短语推出 `subject.count` 被禁止（含中文明确数词"一只/一位"）。
- 新裁定：中文明确数词**允许**抽取为整数；未陈述数量不默认（不补 1）。
- r1 落地：凡用户文本出现过明确主体量词（一只/一位/两只…）的案例，逐轮
  1. 从 `paths_must_remain_unset` 移出 `subject.count`（不再禁止提取）；
  2. 记入 `acceptable_extra_deltas`：`SET subject.count int_equals <N>`（**允许而非强制**，
     `accept` 轮不加）；
  3. 在 case 级 `prompt_expectations.must_mention_groups` 增加**数量语义组**
     （原短语 / 数字形式 / 明确英文数词，如 `["一只","1只","one "]`）。
- 两种合法结构化表达（抽取 count / 省略 count）都被容许，但**数量内容完全丢失要罚**：
  省略结构化 count 时，Prompt 仍须保留用户明确数量语义，否则 L3 Intent Coverage 扣分。
  产品侧 `interpreter.v5` 要求抽取明确数词与评测层容许省略是两回事，不冲突。
- 受影响案例（21 例，含明确数词）：s01-001/002、s02-001、s02-002（t2 起）、
  s03-001/002、s04-001/002/003、s05-001/002、s06-001/002、s07-001/002、
  s08-001/002、s09-001/002/003、s10-001/002。
- 未受影响：s02-002 的 t1（"帮我做一张图…"无主体数量；"一张图"指图片，不计主体）。

### 1.2 冲突规则停用（R1-A #3/#4，A 工包确认）

四条启发式不是可证明的硬冲突，r1 从标注移除 `expected_conflicts`：

| 案例 | 轮次 | 旧冲突 | r1 裁定 |
|---|---|---|---|
| s02-missing-core-002 | t3 | `execution_conflict.framing_aspect_mismatch` | 远景/全景不等于宽高比要求，移除 |
| s04-conflict-001 | t1 | `hard_conflict.environment_mode_location`（摄影棚+海滩） | 可能表示布景，词表不足以强制阻断，移除 |
| s04-conflict-002 | t1 | `hard_conflict.lighting_environment_source`（室内+自然光） | 该组合可以成立，移除 |
| s04-conflict-003 | t1 | `hard_conflict.style_medium_mismatch`（摄影+水彩质感） | 混合风格允许，移除 |
| s09-multiturn-001 | t3、t4 | `execution_conflict.framing_aspect_mismatch` | 同上，移除 |

### 1.3 s04 三例必要对话轮次修正

冲突停用后 t1 的全部阻塞决策已解决，不再需要"消解冲突"的澄清回答；原 t2 实为
对已生成图片的合法单字段修改，因此改为 `image_feedback` 轮（fixture kind 同步修改）：

| 案例 | t2 文本 | 旧性质 | r1 性质 |
|---|---|---|---|
| s04-conflict-001 | "改成户外环境吧。" | clarification_answer（消解冲突） | image_feedback，SET environment.mode=outdoor |
| s04-conflict-002 | "那改成温暖的壁炉火光。" | clarification_answer | image_feedback，SET lighting.character=暖/火光 |
| s04-conflict-003 | "那就改成水彩插画风格，不要写实了。" | clarification_answer | image_feedback，SET style.primary=水彩/插画 |

三例 t1 `expected_outcome` 由 `clarification_expected` 改为 `ready_and_generate`，
`must_clarify` 清空（`must_not_clarify_paths` = 全部 12 路径），`blocking_missing_paths_after_turn`
清空；t2 `forbidden_change_paths` = 全部路径减被修改路径，`expected_feedback_decision=revise`。
L4 维度焦点同步改写为"该组合不作为冲突"。

### 1.4 保留词组（R1-B #2）

为每个修改轮新增 `preserve_path_groups`：路径 → 截至该轮该路径的**最新**期望关键词组
（由旧标注 `expected_deltas.value_match` 机械派生，不新增语义）。用途：

- Preservation 只比较"仍被要求保留"的属性；
- 用户显式改掉的路径以最新值参与检查，**跨澄清轮累计合法修改路径**，
  用户明确改掉的旧值不算丢失。

### 1.5 来源校验与关键词分离（001 §R1-B #8 明令）

- 旧口径用 `guard_bypassed = 文本命中且防护零触发` 把关键词命中包装成来源校验事实，
  属于 001 明令禁止的混淆，r1 纠正：
  - `forbidden_keyword_hit`：纯文本命中计数；
  - `source_guard_triggered`：真实可观测的编译期 `prompt.unauthorized_addition`
    issue 次数；
  - `source_guard_bypassed`：仅在存在真实编译 source-guard 被绕过的可观测证据时取值；
    当前记录结构下不可观测，一律 `null` + `not_observable`。**禁止由关键词派生。**

### 1.6 正式 Run 判定阈值门禁

- r1 判定阈值未冻结的状态是**代码级门禁**：`identity_mode=formal` 的 r1 Run 在驱动
  任何案例前读取 `metrics.gate_thresholds.status`，非 `frozen` 即拒绝启动；
  `--diagnostic` 允许运行且结果标记为非正式证据。本轮不新增阈值、不付费复评。

## 2. 未完成细项（明确列出，不伪造完成）

1. **自由处所短语**："在沙发上/壁炉旁"是否**直接解决** `environment.location` 决策，
   取决于 A 工包最终实现。r1 未重写依赖该判定的对话分支（如 s02-missing-core-001），
   避免在无独立确证时改写期望。待 A 交付并在 R2 真实探针观察后，如确有语义依据，
   另起 r1.x 标注修订并记录理由。
2. **核心集无真冲突案例**：四条启发式停用后，`conflicting_requirements` 三例实测为
   "混合但相容的要求"，r1 不再存在可证明的硬冲突案例。这是**覆盖缺口/能力边界**，
   r1 保留原 scenario 标签以维持数据集连续性与场景覆盖计数，并在此显式记录；
   不伪造新冲突规则，也不据系统输出补标注。
3. **Gate A 阈值未冻结**：r1 协议尚未冻结改善指标、最小改善幅度、等待/澄清成本上限
   与缺失结果处理方式；须在 R3 正式复评开始前冻结，不得事后补定。

## 3. 产品语义修订版本（记实）

r1 标注对应以下产品侧版本（A/B 工包交付，A 已确认）：

| 组件 | 版本 | r1 相关语义 |
|---|---|---|
| Interpreter prompt | `interpreter.v5` | 中文明确数词可抽取、自由处所表达、不脑补房间 |
| Decision policy | `policy.v2` | 四条启发式冲突规则 predicate 停用（registry 合同保留） |
| Feedback prompt | `feedback.v4` | 模糊反馈不猜值、数量/处所规则与 CLEAR/PIN 边界同步 |

旧 v0_3 报告、旧 fixture/annotation/config/manifest 均不改动；r1 结论只在上表版本下成立。
正式 Run 的代码身份（commit / dirty）由 Runner 身份记录，本节不以版本号冒充 commit。

## 4. 与旧文件的关系

| 文件 | 状态 |
|---|---|
| `evaluation/fixtures/core_v0_3.jsonl` | 只读保留，未改动 |
| `evaluation/annotations/core_v0_3.jsonl` | 只读保留，未改动 |
| `evaluation/protocol.md` | 只读保留，未改动 |
| `evaluation/configs/gate_a_v0_3.json` | 只读保留，未改动 |
| `evaluation/frozen_manifest_v0_3.json` | 只读保留，未改动 |
| `evaluation/fixtures/core_v0_3_r1.jsonl` | 新增（`parent_case_id` 记录来源） |
| `evaluation/annotations/core_v0_3_r1.jsonl` | 新增（`annotation_revision_reason` 记录理由） |
| `evaluation/protocol_v0_3_r1.md`、`configs/gate_a_v0_3_r1.json` | 新增 |
| `evaluation/frozen_manifest_v0_3_r1.json` | 新增（只哈希 r1 文件） |
