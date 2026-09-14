# Step 08 补丁交接 001：retry 回退微修复（架构裁定 Rev.2 工单 C）

任务：Step 08 补丁 001 — 执行 `docs/handoffs/architecture_decision_002.md` 工单 C
（裁定 1「retry 与"失败不写 Artifact"的缺口」采纳方案 (a) 的落地）：Repository 增加
additive 只读 `get_latest_prompt_artifact(session_id) -> StoredArtifact | None`，
`GenerationPipeline.retry` 在原 GenerationArtifact 不存在时回退到本会话最新
PromptArtifact，两条路径都校验该 PromptArtifact 的确认仍有效，**绝不重新 compile**。

- 依据：`docs/handoffs/architecture_decision_002.md`（工单 C 冻结范围与验收命令）、
  `docs/ARCHITECTURE.md` 第 4 节 Step 04 Repository 方法表（Rev.2 后总数 24，含
  `get_latest_prompt_artifact`）与 Step 08 `retry` 注释（Rev.2 回退规则）、
  `docs/handoffs/step_08_handoff.md`（现有 pipeline/retry 实现结构）。
- 范围纪律：只触碰工单 C 列出的 4 个文件 + 本文件；未改 `schema.sql`、
  `state_machine.py`、`records.py`、`generation/models.py`、`providers/**`、
  `pyproject.toml`、`uv.lock`、`.env`、任务书、`ARCHITECTURE.md`、`step_08_handoff.md`
  原文；未新增依赖、表/列/索引、Issue code 或状态迁移。

## 完成内容

### 1. `persistence/repository.py`：唯一新增只读 getter（纯 additive）

- `Repository(Protocol)` 与 `SQLiteRepository` 各新增恰好一个方法，签名逐字冻结：

  ```python
  def get_latest_prompt_artifact(self, session_id: str) -> StoredArtifact | None: ...
  ```

- 实现**逐行对照**既有 `get_current_realization_state`：`_require_non_empty(session_id,
  "session_id")` → `_require_session(conn, session_id)` → `SELECT prompt_artifact_id,
  session_id, refs_json, payload, created_at FROM prompt_artifacts WHERE session_id = ?
  ORDER BY created_at DESC, rowid DESC LIMIT 1` → 无行返回 `None`，有行
  `self._row_to_artifact(row, "prompt_artifact_id")`。
- 语义：本会话最新一条 prompt_artifacts 信封（`created_at` 相同则 rowid DESC 定序）；
  会话不存在 → `persistence.session_not_found`（与既有 getter 一致）；空会话 → `None`。
- `schema.sql` 零改动（`idx_prompt_artifacts_session` 已存在，9 张表不变）；
  既有方法签名与语义零改动；`persistence/__init__.py` 未改（方法经 Protocol/类暴露）。

### 2. `generation/pipeline.py`：仅 retry 回退分支

- 模块 docstring 的 `retry` 段补充回退规则（只读已落库 PromptArtifact、两条路径的确认校验）。
- `retry(session_id, generation_id)`：门禁（仅 `FAILED`）与后续 `transition GENERATING` →
  `_generate_from_prompt_artifact` 流程不变；PromptArtifact 解析改为经新的
  `_load_retry_prompt_artifact`，`is_confirmation_valid(based_on_confirmation_id)` 校验
  对**两条路径共用**，失败仍然 `generation.no_valid_confirmation`、状态不变、不调用 Provider。
- 原 `_load_generation_artifact(session_id, generation_id) -> StoredArtifact` 改写为
  `_load_retry_prompt_artifact(session_id, generation_id) -> PromptArtifact`：
  - `generation_id` 空/空白 → `generation.artifact_not_found`（不变）；
  - `get_generation_artifact` 命中 → 跨会话 → `generation.session_mismatch`（不变）；
    否则取 `refs["prompt_artifact_id"]`，经既有 `_load_prompt_artifact` 读回同一
    PromptArtifact（缺失 → `generation.artifact_not_found`；跨会话 →
    `generation.session_mismatch`，均不变）；
  - `get_generation_artifact` 抛 `RepositoryError`（原 Artifact 不存在）→ **回退**：
    `get_latest_prompt_artifact(session_id)`；返回 `None` → 仍抛
    `generation.artifact_not_found`；命中 → `PromptArtifact.model_validate_json(
    latest.payload)`（查询按 `session_id` 过滤，无需再校验跨会话）。
- `generate` 路径零改动；`pipeline.py` 仍只有 1 处 `PromptEngine.compile` 调用
  （在 `generate` 内）；无新 Issue code（仍只用现有 5 个 `generation.*`）；
  `_generate_from_prompt_artifact`、落盘、失败记录与 `WAITING_REVIEW` 迁移逻辑不变。

### 3. `tests/persistence/test_persistence_prompt_artifact_queries.py`（新建，10 例）

| 用例 | 覆盖 |
|---|---|
| `test_latest_prompt_artifact_is_none_until_the_first_artifact` | 空会话 → `None` |
| `test_latest_prompt_artifact_returns_the_single_envelope` | 单条信封逐字段（含 tz-aware `created_at`），等于 `get_prompt_artifact` |
| `test_latest_prompt_artifact_picks_the_newest_created_at` | 多条取最新（固定时钟推进），历史两行都在 |
| `test_latest_prompt_artifact_uses_rowid_to_break_created_at_ties` | `created_at` 并列（monkeypatch `utc_now` 冻结）→ rowid DESC 定序 |
| `test_latest_prompt_artifact_is_isolated_per_session` | 跨会话隔离 + 另一空会话 → `None` |
| `test_latest_prompt_artifact_for_missing_session_is_rejected` | 不存在会话 → `persistence.session_not_found` |
| `test_latest_prompt_artifact_requires_a_non_empty_session_id`（2 参数） | 空串 / `None` → `persistence.invalid_field` |
| `test_prompt_artifact_foreign_key_constraints_still_apply` | FK 约束仍生效（缺失 confirmation / intent revision → `persistence.foreign_key_violation`），拒绝的写入零残留 |
| `test_latest_prompt_artifact_is_a_read_only_query` | 只读：两次调用结果一致、行数不变 |

### 4. `tests/generation/`：retry 新路径（+2 新用例，1 例改断言，1 例 e2e 改断言）

- **新增必测** `test_retry_after_a_pure_provider_failure_reuses_the_latest_prompt_artifact`：
  `FakeImageProvider` 回调先抛 `ProviderError.server`、后成功；`generation_id` 取自
  `generation.failed` 日志事件（失败不写 Artifact）。断言：retry 成功、
  `prompt_artifact_id` 与失败前**同一**、`prompt_artifacts` 计数不变（=1，不重新 compile）、
  Provider 两次入参逐字段相同、产生新 generation_id、状态
  `FAILED → GENERATING → WAITING_REVIEW`、新 Artifact 可读回、历史不覆盖。
- **新增必测** `test_retry_fallback_rejects_a_prompt_artifact_whose_confirmation_is_stale`：
  Provider 失败 → 追加新 IntentRevision 使确认失效 → retry 抛
  `generation.no_valid_confirmation`，状态保持 `FAILED`，Provider 区零调用，
  `prompt_artifacts` 计数不变。
- **改断言（旧行为已冻结变更）**：原 `test_retry_without_an_original_artifact_is_rejected_with_a_clear_code`
  改为 `test_retry_without_any_prompt_artifact_is_rejected_with_a_clear_code` —— 会话
  从未编译过 Prompt（无任何 PromptArtifact）时 retry 仍抛 `generation.artifact_not_found`
  且状态保持 `FAILED`。
- **e2e 改断言**：`test_p2_provider_failure_is_observable_and_writes_no_artifact` 保留
  "Provider 失败 → `FAILED` + 零 Artifact"，把旧"retry 抛 artifact_not_found"的钉住改为
  断言 Rev.2 回退语义（retry 成功、复用失败现场 PromptArtifact、不重新 compile）。
- 原 Artifact 存在时的既有 retry 用例（复核迁移失败现场、跨会话、确认失效、retry 再失败）
  **断言逻辑零改动、原样通过**。`generation_helpers.py` 未改（无需新增 helper）。

### 5. 文档

- 新建本文件 `docs/handoffs/step_08_patch_001.md`；`step_08_handoff.md` 原文未改。

## 变更文件

修改（产品代码，2 个）：

- `visual_intent_agent/persistence/repository.py`（Protocol + 实现各 +1 additive 只读 getter）
- `visual_intent_agent/generation/pipeline.py`（模块 docstring retry 段 + `retry` 回退分支 +
  `_load_generation_artifact` → `_load_retry_prompt_artifact` 改写）

新增/修改（测试，3 个）：

- 新建 `tests/persistence/test_persistence_prompt_artifact_queries.py`
- 修改 `tests/generation/test_generation_pipeline.py`（+2 新用例，1 例更新旧断言）
- 修改 `tests/generation/test_generation_e2e.py`（1 例更新旧断言 + import 调整）

交接文档：

- 新建 `docs/handoffs/step_08_patch_001.md`（本文件）

未触碰：`visual_intent_agent/persistence/schema.sql`、`state_machine.py`、`records.py`、
`__init__.py`、`visual_intent_agent/generation/models.py`、`__init__.py`、
`visual_intent_agent/providers/**`、其他一切产品模块、其他测试目录、`tests/conftest.py`、
`pyproject.toml`、`uv.lock`、`.env`、`README.md`、`api.md`、`docs/ARCHITECTURE.md`、
`docs/handoffs/step_08_handoff.md` 及其他既有 md。

## 公开接口

### 产品接口变化（唯一一处，纯 additive 只读）

```python
# visual_intent_agent/persistence/repository.py
class Repository(Protocol):
    def get_latest_prompt_artifact(self, session_id: str) -> StoredArtifact | None: ...
# SQLiteRepository 同签名实现；ARCHITECTURE 第 4 节 Step 04 方法表总数 23 -> 24（Rev.2）
```

- 无新增模块/符号导出（`persistence.__all__` 不变）；无新增 Issue code；
  无新增状态迁移；无 schema 变更。
- `GenerationPipeline.retry(session_id, generation_id) -> GenerationArtifact` 签名不变，
  仅"原 Artifact 不存在"分支从"抛 `generation.artifact_not_found`"变为
  "回退本会话最新 PromptArtifact，仍校验确认有效，仍不 compile"。

Rev.2 冻结的 retry 语义（落地后）：

```text
retry(session_id, generation_id):
  仅 FAILED（否则 generation.invalid_state，状态不变）
  原 GenerationArtifact 存在 → refs["prompt_artifact_id"]（跨会话 → generation.session_mismatch）
  原 GenerationArtifact 不存在 → get_latest_prompt_artifact(session_id)
      None（会话从未编译过 Prompt）→ generation.artifact_not_found
  两条路径 → is_confirmation_valid(based_on_confirmation_id)
      为假 → generation.no_valid_confirmation（状态不变、Provider 零调用）
  → transition GENERATING → 同一 PromptArtifact 调 Provider → 新 generation_id
  → 落盘 → 新 GenerationArtifact（同一 prompt_artifact_id）→ WAITING_REVIEW
```

### 测试侧接口变化（非产品面）

- 新增测试模块 `tests.persistence.test_persistence_prompt_artifact_queries`
  （目录内 import `persistence_helpers`，遵守唯一命名 helper 规则；未新增 helper）。

## 测试命令与结果

```text
$ cd /home/change/projects/image_system

# 验收命令 1：全量（基线 1721 passed, 3 deselected + 本补丁 12 条新用例）
$ uv run pytest -q
1733 passed, 3 deselected in 4.26s

# 验收命令 2：persistence 范围（175 -> 185，+10）
$ uv run pytest tests/persistence -q
185 passed in 0.68s

# 验收命令 3：generation 范围（56 -> 58，+2 新用例；2 条既有用例更新断言）
$ uv run pytest tests/generation -q
58 passed in 0.27s
```

- 三条验收命令全部通过，零失败、零错误、零跳过；零网络、零真实凭据
  （FakeImageProvider + `tmp_path` SQLite/输出目录）；默认 3 deselected 为
  `@pytest.mark.smoke`（与基线一致，未触发真实 Provider）。
- 用例数核对：`tests/persistence` 原 175 + 10 新 = 185；`tests/generation` 原 56 +
  2 新 = 58；全量 1721 + 12 = 1733。
- 关键断言摘录（必测场景）：纯 Provider 失败后 retry 成功且
  `retried.prompt_artifact_id == expected_prompt.prompt_artifact_id`、
  `artifact_count(repo, "prompt_artifacts") == 1`（不重新 compile）、
  `provider.requests[-1] == provider.requests[0]`、`retried.generation_id !=
  failed_generation_id`、状态 `FAILED → WAITING_REVIEW`；回退确认失效时
  `excinfo.value.code == generation.no_valid_confirmation` 且 Provider 区零调用。

## 已知限制

1. **`docs/handoffs/step_08_handoff.md` 的「已知限制 1/2」与「公开接口」第 1 条现已过时**
   （其中"Provider 失败后 retry 抛 `generation.artifact_not_found`、retry 可达失败现场只有
   复核迁移失败一类"已被本补丁与 Rev.2 取代）。按工单 C 禁改该文件原文，故以本补丁记录为准。
2. **回退路径下 `generation_id` 参数不参与持久比对**（Rev.2 裁定 1.6 已记录在案的 MVP 折衷）：
   纯 Provider 失败不写 Artifact，调用方必须传失败日志 `generation.failed` 事件中的
   generation_id；传未知 id 时系统用本会话最新且确认有效的 PromptArtifact 重试。
   匹配校验（确认仍有效）已排除过期 Prompt，语义保证不变。
3. **回退路径的 PromptArtifact payload 解析失败**（理论边界）：若最新信封 payload 不是合法
   `PromptArtifact` JSON，`model_validate_json` 会抛 pydantic `ValidationError`（非
   `generation.*`）。生产路径只由 `PromptEngine.compile` 写该表，属不可达边界；与原 Artifact
   路径的既有行为一致，本补丁未改动该语义。
4. **错误记录仍不可经 Repository 查询**（Rev.2 裁定 1.5 的已知边界）：`generation.failed`
   日志 + sessions 表 FAILED 状态是唯一观察入口；本补丁未新增错误记录表/字段/查询方法。
5. **`WAITING_REVIEW` 下仍不提供"重做"**（`WAITING_REVIEW → FAILED` 不是合法迁移）：
   本补丁只让 `FAILED` 状态的 retry 在纯 Provider 失败场景真实可用，未扩展迁移表。

## 对下一步的输入

### Step 09（FeedbackEngine / RealizationState / ReviewService）

- **"失败后一键重试"入口就绪**：对 FAILED 会话调用
  `GenerationPipeline.retry(session_id, generation_id)`，`generation_id` 取自
  `visual_intent_agent.generation.pipeline` logger 的 `generation.failed` 结构化日志事件；
  落地后纯 Provider 失败场景按 Rev.2 回退语义工作。**Step 09 不得**自行实现回退查询、
  不得绕过 Repository 直读 SQLite、不得用 `generate` 代替 `retry`（会重新 compile）。
- `get_latest_prompt_artifact(session_id)` 是只读 getter，可直接用于展示"会话最近 Prompt"；
  反馈关联仍必须以 `GenerationArtifact.prompt_artifact_id` 为准。
- 反馈外键、失败观察入口（FAILED 状态 + 日志）与既有交付一致，无变化。

### 通用

- `generation/` 依赖方向不变（`generation → prompt_engine / providers.image /
  persistence`）；`persistence` 仍只依赖 `domain`（依赖边界测试通过）。
- 上游问题继续走"最小修订提案 → 架构方裁定"；本补丁不再扩大架构。

## 是否满足验收条件

是。逐条核对工单 C：

| # | 工单 C 要求 | 结果 |
|---|---|---|
| 1 | `Repository(Protocol)` 与 `SQLiteRepository` 各新增 `get_latest_prompt_artifact`，逐行对照 `get_current_realization_state`，无则 `None`，不改 schema / 不新增表列索引 | **是**（唯一产品面变化，纯 additive 只读；`schema.sql` 零改动） |
| 2 | retry 仅 `FAILED`；原 Artifact 路径与跨会话校验不变；仅原 Artifact 不存在时回退 `get_latest_prompt_artifact`，返回 `None` 仍抛 `generation.artifact_not_found` | **是** |
| 3 | 两条路径都校验 `based_on_confirmation_id` 满足 `is_confirmation_valid`，否则 `generation.no_valid_confirmation`、状态不变、不调用 Provider | **是**（两条路径共用同一校验点） |
| 4 | 绝不重新 compile；`prompt_artifacts` 行数不变 | **是**（`pipeline.py` 仍只有 generate 内 1 处 compile；新用例钉住计数不变） |
| 5 | 其余 retry 语义不变（新 generation_id、历史不覆盖） | **是**（既有用例原样通过） |
| 6 | tests/persistence 覆盖最新语义 / `None` / 跨会话隔离 / FK 约束 | **是**（新文件 10 例） |
| 7 | tests/generation 覆盖必测场景（纯 Provider 失败后 retry 复用同一 PromptArtifact 且不重新 compile；回退确认失效时拒绝） | **是**（2 新用例 + 2 既有用例更新断言） |
| 8 | 新建 `docs/handoffs/step_08_patch_001.md`，不改 `step_08_handoff.md` | **是** |
| 9 | 验收命令 1/2/3 全绿 | **是**（1733 passed, 3 deselected / 185 passed / 58 passed） |
| 10 | 禁改文件零触碰、无新增依赖、无 API key | **是**（改动仅工单 C 列出的文件；源码/测试/本文件均无 `sk-` 形态字符串） |
