# 架构裁定记录 003（Rev.3）：Step 09 三项最小修订建议 + 两条已知限制确认

- 日期：2026-09-14
- 裁定方：架构设计 Agent（ARCHITECTURE.md 维护方）
- 提案方：Step 09 实现 Agent（见 `step_09_handoff.md`「对 ARCHITECTURE.md 的最小修订
  建议」1～3 与「已知限制」1/7/8）
- 结果：裁定 1 采纳方案 (i)（`submit_message` 落新 revision 后执行 `evaluate_carry`）；
  裁定 2 追认 `review.py` 兼容设计、边界测试范围维持不变；裁定 3 追认为 MVP 冻结口径；
  已知限制 7/8 维持现状（零动作）。`docs/ARCHITECTURE.md` 已按 Rev.3 更新（第 4 节
  Step 06 用例冻结、Step 09 `evaluate_carry` 行与 P3 闭环段、文末「修订记录」Rev.3）。
- 事实核验（裁定当日执行）：全量 `uv run pytest -q` → **1834 passed, 3 deselected**；
  缺口事实链逐条对照源码核实属实——`workflow/service.py::submit_message` 落新
  IntentRevision 后无任何 carry 调用；`workflow/review.py::_revise` 有
  `evaluate_carry` + 守卫式落库；`workflow/confirmation.py::derive_change_summary` 与
  `validation/reducer.py` 的 ChangeSummary 分类语义逐字段一致（SET→changed、
  CLEAR→cleared、PIN→pinned、UNPIN→unpinned，均按首次出现去重）；
  `tests/workflow/test_workflow_public_api.py` 的 `FORBIDDEN_IMPORT_PREFIXES` 不含
  `realization` / `validation`（裁定 1 的新增 import 不触发边界测试）；
  `test_submit_message_outcome_shape_is_frozen` 钉住 `SubmitMessageOutcome` 恰四字段；
  `tests/e2e/test_p1_workflow_e2e.py` 钉住 P1 全程 `realization_states == 0`；
  `tests/prompt_engine/test_prompt_engine_e2e.py::test_p1_reconfirmation_keeps_old_artifact_and_reuses_realization`
  是既有唯一"compile 后再经 `submit_message` 修改"的用例（改 `style.primary`，
  不命中 Lighting Decision 成员，裁定 1 落地后仍无失效、计数断言继续成立）。

---

## 裁定 1（需要决定）：clarify 回答经 `submit_message` 不触发 `evaluate_carry` —— 采纳 (i)

### 提案原文摘要

`ReviewService` 的 clarify 把 PendingQuestion 落库并转 `WAITING_CLARIFICATION`；用户
回答走 Step 06 的 `WorkflowService.submit_message`（Step 09 无权修改该文件）；
`submit_message` 应用 delta 产生新 IntentRevision，但不调用 `evaluate_carry` → 若回答
修改了某 delegated 路径的 dependency 路径（如 `environment.mode` 变化而
`environment.location` 已 realized），相关 Realization 不会被显式失效，下次 compile
复用语义上已过期的值。选项：(i) 修改 Step 06 的 `submit_message`，在落新
IntentRevision 后调用 `evaluate_carry`（无 RealizationState 时 no-op；有失效时落新
state）；(ii) 给 `ReviewService` 增加"回答后处理"用例（扩大 Step 09 冻结面，调用方需
区分入口）。

### 裁定：采纳 (i)，冻结细节如下

1. **调用点（唯一新增）**：`WorkflowService.submit_message` 内
   `if resolution.applied_deltas:` 块中，`append_intent_revision(...)` **之后**、
   ready/question 状态分支**之前**。无 applied delta（解析失败 / 纯提问轮）→ 不产生
   revision，也不评估 carry（没有任何变化可失效）。先落 revision 再落 state，
   `realization_states.refs["based_on_intent_revision_id"]` 的外键成立。
2. **ChangeSummary 来源（冻结）**：`derive_change_summary(list(resolution.applied_deltas))`
   ——`workflow/confirmation.py` 的既有冻结公开名，与 Reducer 的 ChangeSummary 语义
   逐字段一致（已核实两侧实现）。**不得**重新调用 `validation.reduce`：同一输入的二次
   归并是纯浪费，且把同一语义复制成两个调用源是未来的漂移点。不修改 Step 05 的
   `IntentResolution` 形状。
3. **读取**：同一块内一次 `repo.get_current_realization_state(session_id)`，命中则
   `RealizationState.model_validate_json(stored.payload)`；为 None 直接跳过评估。
4. **零写入守卫（与 `review.py::_revise` 逐字同口径）**：仅当
   `realization_state is not None and carry.has_invalidations()` 才落库。无
   RealizationState（P1 全程、首次生成前）或无失效 → **零写入、零异常**。
5. **落库形状（与 Step 09 同口径）**：`build_carry_state(carry, state, session_id=...,
   based_on_intent_revision_id=<新 revision id>)` → `append_realization_state(
   new_state.realization_id, session_id, {"based_on_intent_revision_id": <新 revision id>},
   new_state.model_dump_json())`；refs 精确一键，`rlz` 前缀，历史 state 不覆盖。
6. **返回形状不变**：`SubmitMessageOutcome` 不新增字段、carry 不进 outcome。
   依据：既有 `test_submit_message_outcome_shape_is_frozen` 钉住恰四字段集；任务书 09
   要求的是"失效必须产生显式记录"——记录落在持久层新 RealizationState（可经
   `get_current_realization_state` / 审计读回观察），任务书未要求进入用例返回；
   `ReviewService` 暴露 `carry` 是 Step 09 对 P3 反馈用例自有形状的冻结，不构成
   `submit_message` 的先例义务。
7. **Step 06 公开面零变化（向后完全兼容）**：四个用例签名、`SubmitMessageOutcome`、
   `workflow/__init__.py`、Issue code、状态迁移表、`MAX_INTERPRETATION_ATTEMPTS`
   重试策略全部不变；唯一产品改动是 `service.py` 内部一个代码块 + 模块 docstring。
   新增 import 仅限 `visual_intent_agent.realization.carry`（`evaluate_carry` /
   `build_carry_state`）与 `visual_intent_agent.realization.models`
   （`RealizationState`）——均不在 Step 06 冻结边界测试的禁止前缀内，该测试零改动。
8. **受控重复**：`service.py` 的 carry 块与 `review.py::_revise` 对应段落保持语义
   逐字一致；不抽共享 helper——跨 Step 06/09 新建共享模块属新增模块面，违反最小性，
   两处各约 8 行的编排重复是可接受代价。

### 理由（任务书原文依据）

1. **任务书 09 的失效四条件不按入口区分**。任务书「继承与失效」原文："默认继承
   active Realization，除非：用户修改同一路径；DecisionPolicy dependency 发生变化；
   新 Intent 与旧 Realization 冲突；用户 CLEAR 对应 delegated path。**失效必须产生
   显式记录，不能覆盖或删除历史 value。**"四个条件的触发主体是"用户修改/变化"，而
   `submit_message` 是任务书 06 冻结的澄清回答**唯一**入口（「Workflow 编排／用户
   消息」用例流）：clarify 回答经它过 IntentEngine、落新 revision，是与 revise 平权
   的"用户修改"通道。现状使"dependency 发生变化"在该通道上系统性不生效且无任何显式
   记录，直接违反"失效必须产生显式记录"。
2. **验收条件与必测场景预设失效判定是 Intent 变化的全局函数**。任务书 09 必测场景
   "dependency 变化导致相关 Realization 失效"未限定入口；验收条件"delegated choice
   在未失效时稳定继承"与失效规则互为表里——dependency 已变化即任务书语义上的"已
   失效"，系统却不记录、继续继承，恰好制造验收条件要排除的"已失效却被当作未失效"
   状态。且这不是边缘路径：P3 多轮剧本第 3 轮（"背景不好"→ clarify → 回答
   `environment.mode=indoor`）是必测场景"模糊反馈不会被猜成具体设计"的续半段，
   回答天然落在 Environment Decision 的成员路径上（`DECISION_POLICIES` 中
   `environment.mode` 与 `environment.location` 同属一个 Decision）。
3. **(ii) 否决**：`WAITING_CLARIFICATION` 不记录问题来源（P1 澄清与 P3 clarify 同为
   PendingQuestion），调用方无法可靠区分"该走哪个入口"；且 `ReviewService` 增加
   "回答后处理"用例等于复制整条 `submit_message` 编排（存消息 → IntentEngine → 落
   revision → 状态迁移），是同一用户动作的两个竞态入口，扩大 Step 09 冻结面并制造
   双写风险，违反最小修订。
4. **(iii) 否决**：不修则任务书 09 的失效四条件在 clarify 通道上不可达，"失效必须
   产生显式记录"被架空；Step 09 自评"不阻塞验收"仅因多轮剧本第 3 轮刻意回避了该
   失效——缺口被绕行而非不存在，任务书规则本身没有"clarify 回答豁免失效"的条文。
5. **顺带闭合确认页通道**：`WAITING_CONFIRMATION` 下经 `submit_message` 的修改
   （Rev.1 第 11 条迁移路径）同样落新 revision，本裁定使其一并触发 carry——失效
   语义与入口彻底解耦，"任何产生新 IntentRevision 的用例都先完成失效记录"成为
   全局不变量。
6. **兼容性经既有测试推演成立**：P1 全程无 RealizationState → 守卫零写入
   （`tests/e2e` 的 `realization_states: 0` 与全部 P1 用例不受影响）；唯一"compile
   后再经 `submit_message` 修改"的既有用例（Step 07 e2e 改 `style.primary`）不命中
   Lighting Decision 成员 → 无失效 → `realization_states == 1` 继续成立——即
   "未失效时稳定继承"在 `submit_message` 通道上同样被钉住，符合任务书验收条件
   "delegated choice 在未失效时稳定继承"。

## 裁定 2（追认类）：workflow 依赖边界测试范围 —— 追认兼容设计，不收窄

**裁定**：追认 `review.py` 的"payload 字典 → `FeedbackRequest.model_validate`"兼容
设计为冻结形态；`tests/workflow/test_workflow_public_api.py::
test_workflow_never_imports_later_steps_or_web_db_layers` 的扫描范围维持
`workflow/*.py` 全包，不限定 Step 06 三文件、不豁免 `review.py`。

理由：

1. 该测试以最强形态钉住 README 不变量 7（Intent / Realization / Prompt / Generation /
   Feedback 分层）：`workflow` 包在 import 层面不依赖 `prompt_engine` / `generation`。
   收窄扫描范围的唯一效果是放宽守护，零行为收益。
2. 兼容设计已验收（1834 绿）：合同校验仍由 pydantic 在 `FeedbackRequest` 边界完成，
   字段面唯一归 Step 07/08 持有，不存在第二份字段定义的一致性风险。
3. "不持有强类型变量"是可接受的 MVP 代价（`step_09_handoff` 已知限制 9 已如实记录）；
   换回直接 import 是纯人体工学改进，Step 09 自评"非阻塞"。
4. 先例一致：Rev.1/Rev.2 对"冻结测试与新增需求相碰"的处理均选择"实现适配冻结
   边界"（如 Step 09 不碰 Step 06 文件），本裁定延续同一原则——边界测试本身是
   冻结面的一部分。

## 裁定 3（追认类）：`ChangeSummary`→`evaluate_carry` 表达力边界 —— 追认为 MVP 冻结口径

**裁定**：追认"新 Intent 与旧 Realization 冲突"只以 `ChangeSummary` 可判定形态
（同路径 SET/CLEAR，含纯 resolution 的 SET）落地；语义级冲突检测（"新值与已实现
取值在语义上是否相容"）不做，属未来 Step 02/09 联合修订。零即时动作。

理由：

1. `evaluate_carry(state, change_summary, policies)` 只读 `ChangeSummary` 是架构自身
   的冻结签名（Rev.0 冻结表），本次只是如实记录其表达力边界，非行为变更。
2. 语义级冲突判定需要理解"用户新值"与"模型已实现取值"之间的语义关系，超出确定性
   代码能力；若交给 LLM 做失效判定，违反 README 不变量 1（LLM 负责理解和表达，
   确定性代码负责状态）。
3. 保守兜底已存在：同 Decision 成员路径（`path ∪ dependencies`）的任何变化即失效，
   12 条白名单路径下覆盖面足够；宁可多失效一次（重新实现）也不错失效（复用过期值），
   方向与任务书"不猜测"原则一致。
4. 未来如需（如给 `ChangeSummary` 或 `evaluate_carry` 增加显式 `new_intent` 快照），
   按"最小修订提案 → 架构裁定"流程处理，任何 Step 不得自行扩大。

## 已知限制 7/8 确认（零动作）

- **限制 7（反馈解析失败不做"最多一次"重试）——维持现状。** Step 06 的一次重试是
  `submit_message` 对 Interpreter 解析失败的内部策略（Step 05/06 冻结），任务书 09
  未要求反馈重试；反馈解析失败以可恢复 issue（`feedback.unparseable_output.*` /
  `provider.*`）暴露、状态保持 `WAITING_REVIEW`、FeedbackResult 已落库可审计，
  调用方可原样再提交——满足"不猜测修复、不静默吞"；对齐一次重试属行为增量，
  需要时走未来修订。
- **限制 8（`WAITING_REVIEW` 无重做入口）——维持现状。**
  `architecture_decision_002.md`「对 Step 09 实施 Agent 的指令」第 4 条已冻结
  "`WAITING_REVIEW` 下不提供重做、不得自行扩展迁移表"；FAILED 的重试走
  `GenerationPipeline.retry`（工单 C 已落地回退语义）；`WAITING_REVIEW` 下的
  "不满意"表达通道是 revise/clarify 反馈（语义为"修改"而非"重做"），任务书 09
  无重做入口要求。

---

## 工单 D：`submit_message` 接入 `evaluate_carry`（裁定 1 落地）

### 文件触碰范围（冻结，除此不得触碰任何其他文件）

1. **`visual_intent_agent/workflow/service.py`**（唯一产品文件）
   - 在 `submit_message` 的 `if resolution.applied_deltas:` 块内、
     `append_intent_revision(...)` 之后插入 carry 块，顺序严格为：落 revision →
     `get_current_realization_state` → `derive_change_summary(list(resolution.applied_deltas))`
     → `evaluate_carry(state, change_summary)` → 守卫（`state is not None and
     carry.has_invalidations()`）成立时 `build_carry_state(...)` +
     `append_realization_state(...)`（refs 精确 `{"based_on_intent_revision_id":
     revision.intent_revision_id}`）；随后才是既有 ready/question 状态分支；
   - 新增 import 仅限 `visual_intent_agent.realization.carry`（`evaluate_carry`、
     `build_carry_state`）与 `visual_intent_agent.realization.models`
     （`RealizationState`）；ChangeSummary 一律经既有
     `.confirmation.derive_change_summary`，**不得** import `validation.reduce` 重算；
   - 守卫条件、refs、`based_on_intent_revision_id`、ID 前缀与 `review.py::_revise`
     逐字同口径（受控重复，不抽共享 helper）；
   - 更新模块 docstring 的 `submit_message` 冻结流程段（标注 Rev.3 与
     `architecture_decision_003.md`）；docstring 与代码中**不得**出现
     `prompt_engine` / `PromptEngine` / `ImageProvider` / `WorkflowState.GENERATING`
     字面量（`test_confirm_is_the_only_gate_and_does_not_generate` 钉住）；
   - **不得**改动：四个用例签名、`SubmitMessageOutcome` 字段集、`__init__.py`、
     `questions.py`、`confirmation.py`、Issue code、状态迁移表、
     `MAX_INTERPRETATION_ATTEMPTS` 重试策略。
2. **`tests/workflow/test_workflow_submit_message_carry.py`**（新建；装配复用
   `workflow_helpers.py`；RealizationState 种子在测试内构造
   （`RealizationState`/`RealizationValue` 直接实例化）并经
   `repo.append_realization_state` 落库，refs 指向当前 revision）：
   - (a) 回答 SET dependency 路径（已有 active Realization 于同 Decision 另一路径，
     如改 `environment.mode`、`environment.location` 已 realized）→ 旧值显式
     `invalidated`（`invalidated_reason == "decision_dependency_changed"`、带
     `invalidated_at`）落**新** state；refs 精确；旧 state payload 逐字节不变；
   - (b) 回答 SET delegated 路径本身 → `user_changed_delegated_path`；
   - (c) 回答 CLEAR delegated 路径 → 失效（`user_changed_delegated_path`）；
   - (d) 会话无 RealizationState → `realization_states` 计数不变、无异常；
   - (e) 修改不命中任何 Realization 路径及其 Decision 成员（如改
     `composition.framing`）→ 无新 state（稳定继承）；
   - (f) 解析失败 / 空 delta（无新 revision）→ 零写入、不评估；
   - (g) UNPIN 命中成员路径 → `unpinned_delegated_path`；
   - (h) `derive_change_summary(deltas)` 与 `reduce(intent, deltas).change_summary`
     对同一混合 delta 批次（SET/CLEAR/PIN/UNPIN 各至少一条）逐字段相等
     （语义等价钉住；测试代码允许 import `validation`）。
3. **`tests/feedback/test_feedback_e2e.py`**（修改既有文件；**不改 fixture**）：
   - 多轮剧本第 3 轮（clarify → `submit_message` 回答 `environment.mode=indoor`）
     回答后新增断言：当前 RealizationState 中 `environment.location` 已显式失效
     （`decision_dependency_changed`）且落新 state；
   - generation #4 的 `environment.location` binding 新增断言：
     `source_kind == "delegation"`、`realization_id` 为 compile 新落的 state、
     **不**逐字复用旧文本（即验收用例"clarify 回答修改 dependency 路径后相关
     Realization 显式失效并落新 state、compile 不再复用过期值"）；
   - 终局计数按新行为更新（预期 `realization_states` 由 4 变为 6：第 3 轮回答 +1、
     generation #4 重实现 +1；以实测为准并写入 patch handoff）；
   - 第 4 轮 carry 期望（`invalidated == ["environment.location"]`）与
     `p3_multiturn_v1.json` 全部既有期望必须原样继续通过——fixture 零改动；若实施
     中发现既有期望与新行为冲突，停止并回报架构方，不得自行修改 fixture。
4. **`docs/handoffs/step_06_patch_001.md`**（新建）：记录补丁内容、变更文件、测试
   命令与实测结果、step_09_handoff 已知限制 1 的关闭说明；**不得修改**
   `step_06_handoff.md` 与 `step_09_handoff.md` 原文。

### 禁止

改 Step 09 既有文件（`feedback/**`、`realization/carry.py`、`workflow/review.py`）
与任何其他产品模块；改 MVP v0.2 任务书或任务索引 / `api.md` / 既有 handoff /
`ARCHITECTURE.md`（架构方已改）/ `pyproject.toml` / `uv.lock` / `.env`；改
fixture；新增模块 / 状态 / 依赖 / Issue code / 状态迁移；给 `SubmitMessageOutcome`
加字段；让 `submit_message` 调用 Provider / PromptEngine / GenerationPipeline。

### 验收命令（全部必须绿）

```bash
cd /home/change/projects/image_system
uv run pytest -q                                          # 全量绿（1834 + 新增用例）
uv run pytest tests/workflow -q                           # 含新 carry 用例（a）～（h）
uv run pytest tests/feedback tests/realization -q         # 含第 3 轮回答失效、generation #4 不复用过期值
uv run pytest tests/prompt_engine tests/generation tests/e2e -q
# 上游零回归：既有 realization_states 计数断言（P1=0、reconfirmation=1 等）逐一不变
```

## 对后续步骤的输入

- Step 10 评测注意：工单 D 落地后，clarify 回答路径也可能新增 RealizationState
  （多轮剧本终局 `realization_states` 计数随之变化）；评测脚本不得以"4"为硬编码
  期望，以 `step_06_patch_001.md` 记录的实测值为准。
- 已知限制 7/8 为已确认的 MVP 边界，Step 10 不得将其计入缺陷；若 Gate A 评测要求
  反馈一次重试或 WAITING_REVIEW 重做入口，按"最小修订提案 → 架构裁定"流程另行处理。
