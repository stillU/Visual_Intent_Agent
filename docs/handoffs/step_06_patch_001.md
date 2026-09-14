# Step 06 补丁交接 001：submit_message 接入 evaluate_carry（架构裁定 Rev.3 工单 D）

任务：Step 06 补丁 001 — 执行 `docs/handoffs/architecture_decision_003.md`「工单 D」：
在 `WorkflowService.submit_message` 落新 `IntentRevision`（`append_intent_revision`）之后、
ready/question 状态分支之前插入 carry 块——读取当前 `RealizationState`，用既有
`confirmation.derive_change_summary` 派生 `ChangeSummary`，调用
`realization/carry.py::evaluate_carry`；仅当 state 存在且本轮有失效时按 Step 09 既有
`build_carry_state` 模式落**新** `RealizationState`（refs 精确
`{"based_on_intent_revision_id": 新 revision}`）。`SubmitMessageOutcome` 形状与 Step 06
公开面零变化。

- 依据：`docs/handoffs/architecture_decision_003.md`（工单 D 冻结范围、用例 a～h、验收命令）、
  `docs/ARCHITECTURE.md` 第 4 节 Step 06 `submit_message` 冻结流程（Rev.3 增补 carry 步）与
  Step 09 `evaluate_carry` 行（Rev.3 冻结调用点两处）、文末「修订记录」Rev.3；
  `docs/handoffs/step_06_handoff.md` 与 `step_09_handoff.md`（现有实现结构 /
  carry / RealizationState 语义）。
- 范围纪律：只触碰工单 D 列出的 4 个文件（`workflow/service.py`、
  `tests/workflow/test_workflow_submit_message_carry.py`（新建）、
  `tests/feedback/test_feedback_e2e.py`、本文件）。未改 `workflow/questions.py`、
  `workflow/confirmation.py`、`workflow/review.py`、`realization/**`、`feedback/**`、
  `prompt_engine/**`、`generation/**`、`persistence/**`、`intent_engine/**`、
  `providers/**`、`domain/**`、`validation/**`、`policy/**`、`config.py`、
  `pyproject.toml`、`uv.lock`、`.env`、任务书、`ARCHITECTURE.md`、既有 handoff、
  `tests/fixtures/**`；未新增模块 / 状态 / 依赖 / Issue code / 状态迁移；未给
  `SubmitMessageOutcome` 加字段；`submit_message` 未调用 Provider / PromptEngine /
  GenerationPipeline。

## 完成内容

### 1. `visual_intent_agent/workflow/service.py`：唯一产品文件

- **新增 import（仅工单 D 允许的两处 + 既有 confirmation 的公开名）**：

  ```python
  from visual_intent_agent.realization.carry import build_carry_state, evaluate_carry
  from visual_intent_agent.realization.models import RealizationState
  # .confirmation import 块新增既有公开名 derive_change_summary（不 import
  # validation.reduce 重算；ChangeSummary 单一来源）
  ```

  两者均不在 `tests/workflow/test_workflow_public_api.py::FORBIDDEN_IMPORT_PREFIXES`
  内，该边界测试零改动。
- **`submit_message` 的 carry 块**（落在 `if resolution.applied_deltas:` 内、
  `append_intent_revision(revision)` 之后、ready/question 分支之前），顺序严格为
  落 revision → 读 state → `derive_change_summary(list(resolution.applied_deltas))`
  → `evaluate_carry` → 守卫：

  ```python
  realization_state = self._load_realization_state(session_id)
  change_summary = derive_change_summary(list(resolution.applied_deltas))
  carry = evaluate_carry(realization_state, change_summary)
  if realization_state is not None and carry.has_invalidations():
      new_state = build_carry_state(
          carry, realization_state, session_id=session_id,
          based_on_intent_revision_id=revision.intent_revision_id,
      )
      self._repo.append_realization_state(
          new_state.realization_id, session_id,
          {"based_on_intent_revision_id": revision.intent_revision_id},
          new_state.model_dump_json(),
      )
  ```

  守卫条件、refs、`based_on_intent_revision_id`、ID 前缀（`rlz`）与
  `review.py::_revise` 逐字同口径——**受控重复，未抽共享 helper**（裁定 1 第 8 条）。
- **新增私有读取方法** `_load_realization_state(session_id) -> RealizationState | None`
  （与 `review.py::_load_realization_state` 逐字同口径）：`get_current_realization_state`
  命中则 `RealizationState.model_validate_json(stored.payload)`，为 None 直接跳过评估。
- **模块 docstring**：`submit_message` 冻结流程第 4 步补入 Rev.3 carry 段，标注依据
  `docs/handoffs/architecture_decision_003.md` 工单 D；docstring 与代码均未出现
  `prompt_engine` / `PromptEngine` / `ImageProvider` / `WorkflowState.GENERATING`
  字面量（`test_confirm_is_the_only_gate_and_does_not_generate` /
  `test_no_generating_reference_anywhere_in_the_workflow_package` 继续绿）。
- **零行为面变化**：四个用例签名、`SubmitMessageOutcome` 恰四字段、`__init__.py`、
  Issue code、状态迁移表、`MAX_INTERPRETATION_ATTEMPTS`（仍 2）全部不变；无 applied
  delta（解析失败 / 纯提问轮）→ 不落 revision、不评估 carry（0 次调用）。

### 2. `tests/workflow/test_workflow_submit_message_carry.py`（新建，9 例）

装配复用 `workflow_helpers.py`（`make_repo` / `make_service` / `make_provider` /
`response` / `set_entry` / `empty_response` / `invalid_json_response`）；
`RealizationState` / `RealizationValue` 在测试内直接实例化并经
`repo.append_realization_state` 落库，refs 指向当前 revision。逐条覆盖工单 D 用例：

| 用例 | 场景 | 断言要点 |
|---|---|---|
| (a) | 回答 SET dependency 路径 `environment.mode`（`environment.location` 已 realized） | 旧值在新 state 显式 `status="invalidated"`、`invalidated_reason="decision_dependency_changed"`、带 `invalidated_at`；新 state 的 `refs` 精确一键 `{"based_on_intent_revision_id": 新 revision}`、`rlz_` 前缀、`based_on_intent_revision_id` == 新 revision；旧 state payload 逐字节不变 |
| (b) | 回答 SET delegated 路径本身 `environment.location` | `user_changed_delegated_path` + `invalidated_at` + 值保留 |
| (c) | 回答 CLEAR delegated 路径 | `user_changed_delegated_path`（CLEAR 经 EvidenceContext 校验仍被接受） |
| (d) | 会话无 RealizationState（P1 语义） | `realization_states == 0`、revision 仍 +1、无异常 |
| (e) | 修改不命中任何 Realization 路径及 Decision 成员（`composition.framing`） | 无新 state；当前 state 仍是被 seed 的那个、`environment.location` 仍 active（稳定继承） |
| (f) | 空 delta（合法无 delta 响应） | 不落 revision、`realization_states` 不变、monkeypatch 计数证明 `evaluate_carry` **0 次调用** |
| (f) | 解析失败（非法 JSON，恰好一次修复后仍失败） | `issues` 非空仍显式暴露；不落 revision、不评估（0 次调用） |
| (g) | UNPIN 命中 Decision 成员路径（`environment.mode` ∈ Environment `dependencies`） | `unpinned_delegated_path` |
| (h) | `derive_change_summary(deltas)` 与 `reduce(intent, deltas).change_summary` 对同一混合批次（SET/CLEAR/PIN/UNPIN 各至少一条、含重复） | `model_dump()` 逐字段相等（语义等价钉住；测试代码允许 import `validation`） |

### 3. `tests/feedback/test_feedback_e2e.py`（修改既有；fixture 零改动）

- 多轮剧本第 3 轮（clarify → `submit_message` 回答 `environment.mode=indoor`）回答后新增断言：
  - 当前 `RealizationState` 已换代（`realization_id` 变化、`based_on_intent_revision_id` ==
    回答后的新 revision）；
  - `environment.location` 显式 `status="invalidated"`、
    `invalidated_reason="decision_dependency_changed"`、带 `invalidated_at`。
- generation #4 的 `environment.location` binding 新增断言：`source_kind == "delegation"`；
  `realization_id` == generation #4 compile 新落的 state（不等于被失效的 carry state，也不等于
  generation #1 的 state）；新 `RealizationValue.first_prompt_artifact_id` == generation #4 的
  prompt artifact（不等于被失效旧值对象的 `first_prompt_artifact_id`，且不是同一对象）。
- 终局 append-only 计数改为显式字典断言：`intent_revisions=5`、`prompt_artifacts=5`、
  `generation_artifacts=5`、`feedback_results=5`、**`realization_states=6`**（实测；由 4 变为 6：
  第 3 轮回答 carry +1、generation #4 重新实现 +1）。
- `p3_multiturn_v1.json` 全部既有期望（含第 4 轮 `invalidated_realizations ==
  ["environment.location"]`、`carried_realizations == []`）原样继续通过；**fixture 与
  feedback helper 零改动**，未发现任何既有期望与新行为冲突。

### 4. 变更文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `visual_intent_agent/workflow/service.py` | 修改 | 唯一产品改动：carry 块 + 两处 import + `derive_change_summary` + 私有读取方法 + docstring |
| `tests/workflow/test_workflow_submit_message_carry.py` | 新建 | 工单 D 用例 (a)～(h)，9 例 |
| `tests/feedback/test_feedback_e2e.py` | 修改 | 第 3 轮回答失效、generation #4 binding、终局计数断言 |
| `docs/handoffs/step_06_patch_001.md` | 新建 | 本文件 |

## 公开接口

- `WorkflowService` 四个用例签名、`SubmitMessageOutcome`（恰四字段）、
  `workflow/__init__.py` 的 `__all__`、Issue code、状态迁移表：
  **零变化**（`test_frozen_use_case_signatures` /
  `test_submit_message_outcome_shape_is_frozen` / `test_expected_public_names_are_exported`
  零改动且全绿）。
- `submit_message` 的语义增量（经裁定的唯一行为变化）：落新 `IntentRevision` 后，若当前
  存在 `RealizationState` 且 `derive_change_summary(applied_deltas)` 使任一 active
  Realization 失效，则落一条新 `RealizationState`（历史不覆盖）。carry **不**进入返回值；
  失效可经 `Repository.get_current_realization_state` / 审计读回观察。
- 无新增公开名、无新增模块、无新增 Issue code、无新增状态迁移。

## 测试命令与结果

```text
$ cd /home/change/projects/image_system

# 全量（基线 1834 + 本补丁新增 9）
$ uv run pytest -q
1843 passed, 3 deselected in 4.84s

# 本补丁范围
$ uv run pytest tests/workflow -q
83 passed in 0.37s                     # 既有 74 + 新 carry 用例 9

$ uv run pytest tests/feedback tests/realization -q
122 passed in 0.37s                    # 含第 3 轮回答失效、generation #4 不复用过期值、终局计数 6

# 上游范围（未回归）
$ uv run pytest tests/prompt_engine tests/generation tests/e2e -q
163 passed in 0.79s

# 确定性复跑（新增候选表选择不引入抖动）
$ for i in $(seq 1 25); do uv run pytest tests/feedback/test_feedback_e2e.py::test_p3_five_round_loop -q; done
# -> 25/25 passed

# 依赖边界（零改动，仍绿）
$ uv run pytest tests/workflow/test_workflow_public_api.py -q
# service.py 未 import prompt_engine / generation / providers.image / httpx / sqlite3；
# docstring 与代码无 WorkflowState.GENERATING 字面量。

# 补丁有效性反证（临时把 _load_realization_state 置空模拟未打补丁）
# -> test_p3_five_round_loop 以 AssertionError 失败（新断言确实依赖 carry 块）

# 默认运行完全离线：FakeLLMProvider + FakeImageProvider + tmp_path SQLite/输出目录；
# 零网络、零真实凭据（新增/修改文件 grep 未出现任何 key；无 API key）。
```

### P3 多轮剧本终局（Rev.3 工单 D 后实测）

| 轮 | 反馈 | decision | carry（ReviewService / submit_message） | 产物 |
|---|---|---|---|---|
| 1 | keep the person, pull the camera back | revise | 继承 location+lighting / 无失效 | 新 revision + generation #2 |
| 2 | only change the lighting, dramatic | revise | 继承 location / 失效 lighting | 新 revision + state + generation #3 |
| 3 | the background is not good | clarify | — | PendingQuestion(environment.mode) |
| 3-答 | make it an indoor scene（经 `submit_message`） | — | **失效 environment.location（decision_dependency_changed）** | 新 revision + **新 state** + generation #4（**重新实现 location，落新 state**） |
| 4 | make the environment outdoor | revise | 失效 environment.location | 新 revision + state + generation #5 |
| 5 | exactly what I wanted, done | accept | — | 无新产物 |

终局 append-only 计数（实测）：`intent_revisions=5`、`prompt_artifacts=5`、
`generation_artifacts=5`、`feedback_results=5`、**`realization_states=6`**；状态 `COMPLETED`。

## 已知限制

1. **`step_09_handoff.md` 已知限制 1 已关闭**：clarify 回答（及一切经
   `WorkflowService.submit_message` 的修改，含确认页修改）在落新 `IntentRevision` 后
   同样执行 `evaluate_carry` / `build_carry_state`，失效语义与入口彻底解耦；
   "任何产生新 IntentRevision 的用例都先完成失效记录"成为全局不变量。原 handoff 原文
   未改动，本文件作为关闭记录。
2. **generation #4 的文本逐字不等断言未按字面落地（实现取舍，如实记录）**：工单 D 原文
   提到 generation #4 的 `environment.location` binding "**不**逐字复用旧文本"。但
   `select_delegated_value` 的候选表对 `environment.location` 恰有 3 个候选，新 revision
   的重新选择有 1/3 概率与旧候选逐字相同，字面文本不等断言会产生随机失败（同一行为下）。
   因此测试钉住的是**等价且确定性强**的不复用不变量：`source_kind == "delegation"`、
   binding `realization_id` 为 compile 新落 state（≠ 被失效 state、≠ generation #1 state）、
   新值 `first_prompt_artifact_id` == generation #4 的 prompt artifact（≠ 旧值对象的
   `first_prompt_artifact_id`，且 `is not` 旧对象）。这与
   `test_dependency_change_invalidates_the_realization_before_recompiling` 既有先例
   （按 realization 身份断言，而非文本）一致。若架构方要求字面文本不等，建议改为在
   确定 seed 下断言，或调整候选表选择规则——属 Step 07 范围，本补丁不自行扩大。
3. **`ChangeSummary` 表达力边界不变**：语义级冲突检测不做（裁定 3 追认），本补丁只把
   `derive_change_summary` 的既有可判定形态接到 `evaluate_carry`。
4. **受控重复**：carry 编排在 `service.py` 与 `review.py` 各约 8～12 行，语义逐字一致，
   未抽共享 helper（裁定 1 第 8 条的最小性代价）。

## 对下一步的输入

- Step 10 评测注意：clarify 回答路径现也可能新增 `RealizationState`；多轮剧本终局
  `realization_states` 实测为 **6**（原 4）。评测脚本不得硬编码 "4"，以本文件实测值为准。
- 上游既有 `realization_states` 计数断言未回归：P1 全程 `== 0`
  （`tests/e2e/test_p1_workflow_e2e.py`）、compile 后再经 `submit_message` 改
  `style.primary` 的 `== 1`（`test_p1_reconfirmation_keeps_old_artifact_and_reuses_realization`）
  逐一保持——后者不命中 Lighting Decision 成员，无失效、稳定继承。
- 若未来需要"逐字文本不复用"的确定性断言或语义级冲突检测，按"最小修订提案 → 架构裁定"
  流程处理。

## 是否满足验收条件：是

四条验收命令全绿（1843 passed / 83 / 122 / 163），工单 D 用例 (a)～(h) 全数落地，
Step 06 公开面与其余模块零改动，fixture 零改动。
