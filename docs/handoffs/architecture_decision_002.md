# 架构裁定记录 002（Rev.2）：Step 08 四项最小修订建议 + 一项行为追认

- 日期：2026-09-14
- 裁定方：架构设计 Agent（ARCHITECTURE.md 维护方）
- 提案方：Step 08 实现 Agent（见 `step_08_handoff.md`「公开接口」第 1～4 条最小修订建议、
  「已知限制」1～8）
- 结果：裁定 1 采纳方案 (a)；裁定 2/3/4 追认实现现状；裁定 5 追认为冻结行为。
  `docs/ARCHITECTURE.md` 已按 Rev.2 更新（第 4 节 Step 04 方法表、Step 08 冻结表与
  generate/retry 注释、第 1 节目录树、第 7 节测试策略、文末「修订记录」Rev.2）。
- 事实核验（裁定当日执行）：Step 08 handoff 记录全量 `uv run pytest -q` →
  1721 passed, 3 deselected；真实出图 smoke 通过；缺口事实（Provider 失败后无原
  Artifact、Repository 无 prompt artifact 按会话查询方法）经对照
  `visual_intent_agent/generation/pipeline.py` 与
  `visual_intent_agent/persistence/repository.py` 源码核实属实。

---

## 裁定 1（最重要）：retry 与"失败不写 Artifact"的缺口 —— 采纳 (a)

### 提案原文摘要

`generate` 在 `ProviderError` 时按契约不写 GenerationArtifact（"不伪造 Artifact"），
而 `retry(session_id, generation_id)` 被冻结为"加载原 GenerationArtifact 的
`prompt_artifact_id`"。纯 Provider 失败后没有原 Artifact，retry 抛
`generation.artifact_not_found`，最常见失败场景无法重试；Repository 也没有
prompt artifact 的按会话查询方法。选项：(a) Repository 增加只读
`list_prompt_artifacts(session_id)`（或 `get_latest_prompt_artifact(session_id)`），
retry 回退到"当前确认绑定的最近 PromptArtifact"，仍不得重新 compile；
(b) retry 增加可选 `prompt_artifact_id` 参数；(c) 明确"失败即不可 retry"。

### 裁定：采纳 (a)，取其最窄形态 `get_latest_prompt_artifact`

1. **Repository 冻结新增恰好一个只读方法**（不写 `list_prompt_artifacts`——retry
   只需要"最新一条"，list 是更大的公开面，违反最小修订）：

   ```python
   def get_latest_prompt_artifact(self, session_id: str) -> StoredArtifact | None: ...
   # 按 created_at, rowid 取本会话最新一条 prompt_artifacts；无则 None；
   # 会话不存在抛 persistence.session_not_found（与既有 getter 一致）
   ```

   实现模板即既有 `get_current_realization_state`（同一查询形态：
   `WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1`）。
   **schema.sql 不动**：`idx_prompt_artifacts_session` 索引已存在，9 张表零变更。

2. **retry 回退语义（冻结）**：`retry(session_id, generation_id)` 仍为仅 FAILED
   可用；读原 GenerationArtifact 的路径与校验（跨会话 → `generation.session_mismatch`）
   完全不变。仅当原 Artifact **不存在**（`persistence.artifact_not_found`）时回退：
   `get_latest_prompt_artifact(session_id)`；返回 None（会话从未成功编译）仍抛
   `generation.artifact_not_found`。
3. **PromptArtifact 与当前有效 Confirmation 的匹配校验（冻结）**：无论原 Artifact
   路径还是回退路径，进入 GENERATING 之前必须
   `is_confirmation_valid(prompt_artifact.based_on_confirmation_id)` 为真，否则
   `generation.no_valid_confirmation`、状态不变、不调用 Provider。由于
   `is_confirmation_valid` 绑定的是**当前** intent/execution revision + summary_hash，
   该校验即"PromptArtifact 与当前有效确认匹配"的判定；确认已失效时唯一出路是
   重新确认（`FAILED → WAITING_CONFIRMATION` 为既有合法迁移），retry 不兜底。
4. **"不重新 compile"不变**：回退只读取**已落库**的 PromptArtifact，不调用
   `PromptEngine.compile`；retry 前后 `prompt_artifacts` 行数不变（测试钉住）。
5. **"错误记录"落点（冻结）**：任务书"Provider 失败时保存错误记录并进入 FAILED"
   由 **结构化日志（`generation.failed` 事件：session_id / generation_id /
   prompt_artifact_id / reason_code / retryable / status_code / provider_request_id）
   + sessions 表持久化的 FAILED 状态**共同满足。不新增错误记录表/字段。理由：
   任务书验收条件对应的是"失败和重试行为**可观察**"（可观察 ≠ 可经 Repository
   查询）；9 张表 schema 已冻结，新增表/字段属较大修订而 MVP 无查询需求
   （Step 09 读取的是 FAILED 状态与日志事件）；generation_id 已随失败日志发出，
   调用方可据此发起 retry。已知边界（记录在案，非缺陷）：错误记录不可经
   Repository 查询，仅可经日志与状态观察。

### 理由（任务书原文依据）

1. **任务书 Workflow 规则 4 + 5 必须同时成立**：规则 4"Provider 失败时保存错误记录
   并进入 FAILED"与规则 5"Retry 必须引用原 PromptArtifact，不能偷偷重新编译不同
   Prompt"。状态机冻结表含 `FAILED → GENERATING` 迁移——该迁移的唯一消费者就是
   retry；若选 (c)，纯 Provider 失败（最常见的失败类别）永远无法 retry，
   `FAILED → GENERATING` 在现实路径上成为死迁移，必测场景 5"Retry 使用同一
   PromptArtifact"只剩"复核迁移失败"一类人工构造现场可达。规则 4 与规则 5 并列，
   任务书意图显然是"失败后**可以**用原 Prompt 重试"。
2. **回退到的就是"原 PromptArtifact"，不是近似**：`generate` 的冻结顺序是
   compile（PromptArtifact 落库）→ Provider 调用。纯 Provider 失败时，本次
   generate 刚编译的 PromptArtifact **已经落库**且就是"原 PromptArtifact"；
   `get_latest_prompt_artifact(session_id)` 在 FAILED 状态下取到的正是它
   （FAILED 中无其他路径会产生新 PromptArtifact：generate 要求
   WAITING_CONFIRMATION）。因此 (a) 不违反规则 5 的"同一 PromptArtifact"。
3. **(b) 否决**：给 retry 加可选参数要求调用方在失败响应中带回
   prompt_artifact_id，而失败路径的异常面（ProviderError）不承载该字段，调用方
   仍须另查；改动的是用例签名（比 additive getter 更大的公开面变更），且把
   "找原 Prompt"的责任推给调用方，与"Repository 是业务层唯一依赖面"的边界相比
   没有任何收益。
4. **(c) 否决**：见理由 1；且 (c) 需要把必测场景 5 限定为人造现场，削弱任务书
   验收条件"失败和重试行为可观察"的实际含义。
5. **先例成立**：Rev.1 已追认 additive 只读 getter 模式
   （`get_execution_revision` / `get_confirmation`），任务书 04 的 Repository
   接口原文为"**至少提供**："——additive 只读方法在任务书授权之内，不扩大架构；
   不新增模块、状态、依赖、表、字段。
6. **接受的 MVP 折衷（记录在案）**：回退路径下 `generation_id` 参数无法与任何
   持久记录比对（失败本就不写 Artifact）；调用方必须传失败日志中的
   generation_id。传入未知 id 时系统将用本会话最新且确认有效的 PromptArtifact
   重试——考虑到匹配校验（裁定 1.3）已排除过期 Prompt，该折衷不改变
   "retry 复用原 Prompt"的语义保证。

## 裁定 2：`model_version` 语义 —— 追认

追认 Step 08 实现并写入冻结表：**`target_model` = 请求的目标模型**（来自
PromptArtifact/ExecutionRevision）；**`model_version` = Provider 响应报告的模型
标识**（`ImageGenerationResult.model`）；Provider 未报告时 adapter 回填请求/配置
模型名，**不编造版本号**。

理由：任务书 08 的 GenerationArtifact 形状含 `model_version` 且注明"如果 Provider
不返回 seed 或 request ID，字段可为空；**不得伪造**"——"不伪造"原则同样适用于
model_version；该 Provider（ARCHITECTURE 6.2 已探测事实）响应不携带独立版本号，
回填请求模型名是诚实的最小语义。Artifact 定位四问之"使用哪个模型版本和参数"
由 `target_model + model_version + parameters` 联合回答，语义完整。

## 裁定 3：`OutputRef.path` 相对基准 —— 追认

追认并补入冻结表：`path` 相对项目根，用 **`PROJECT_ROOT / path`** 还原；当
`output_dir` 在项目根之外（测试 `tmp_path`）时允许 `../..` 形态的相对路径。

理由：任务书 08"输出保存"只要求"项目选择的稳定引用方式……MVP 只要求能重新定位
生成结果"。相对项目根 + 可还原满足"可重新定位"；拒绝绝对路径与 URL 保证 Artifact
可移植、不泄露机器布局；`../..` 形态仅出现在测试注入的外部 `output_dir` 下，
默认配置（`<项目根>/outputs/generations`）下就是冻结表给出的
`outputs/generations/<gen>/image_1.png` 形态。零行为变更，纯文档补全。

## 裁定 4：P2 端到端测试位置 —— 追认现位置，修订第 1/7 节

追认 `tests/generation/test_generation_e2e.py`，不归位 `tests/e2e/`。规则修订为：
`tests/e2e/` 保留 Step 06 已交付的 P1 端到端；自 Step 07 起端到端用例随本步放在
对应 `tests/<package>/`（Step 09 的 P3 端到端放 `tests/feedback/`）。

理由：
1. **先例已成立**：Step 07 已把编译端到端放 `tests/prompt_engine/`，P2 放
   `tests/generation/` 与之同构；架构第 7 节的 helper 唯一命名模块规则要求测试
   目录自包含、禁止跨目录 import，端到端与本步 helper（`generation_helpers.py`）
   同目录是与之最一致的布局。
2. **移动是纯成本无收益**：归位 `tests/e2e/` 需要移动文件 + 调整 import +
   处理 helper 共享，产品代码零变化、测试语义零变化；而"端到端随本步"的定位
   让每步的范围验收命令（`uv run pytest tests/<package> -q`）天然覆盖本步
   端到端，验证粒度更好。
3. ARCHITECTURE 第 1 节是架构自身描述而非任务书约束（任务书 08 只要求"P2 端到端
   测试"这一交付物，未指定路径），修订描述使其与实施一致属正当程序。

## 裁定 5（行为追认）：FAILED 转换范围 —— 追认为冻结行为

追认：**GENERATING 阶段内任何失败**——`ProviderError`、`PromptCompilationError`
（含 `prompt.no_valid_confirmation`）、字节落盘 `OSError`、
`append_generation_artifact` 落库失败、复核（WAITING_REVIEW）迁移失败——一律转
`FAILED` 并向上抛原异常；**门禁失败**（状态不对 / 无有效确认）仍在 transition
之前拒绝、状态保持不变。未新增任何迁移（`GENERATING → FAILED` 是既有冻结迁移）。

理由：
1. **唯一不自相矛盾的选择**：会话进入 GENERATING 后若失败而不转 FAILED，将永远
   卡在 GENERATING——冻结迁移表中 GENERATING 的出口只有 `→ WAITING_REVIEW` 与
   `→ FAILED`，前者以成功为前提。任务书规则 4 的精神是"失败即入 FAILED"。
2. **与"不伪造 Artifact"正交且兼容**：失败转 FAILED 不要求写任何成功 Artifact；
   ProviderError / 编译失败 / 落盘失败 / 落库失败均不写 Artifact（复核迁移失败
   时 Artifact 已保存属成功后的迁移失败，如实保留，历史可审计）。
3. **编译失败不回 WAITING_CONFIRMATION**：`PromptCompilationError` 发生在
   transition GENERATING 之后，回退需要新增 `GENERATING → WAITING_CONFIRMATION`
   迁移（更大修订）；且确认此刻已实际失效（`prompt.no_valid_confirmation`），
   回到 WAITING_CONFIRMATION 也无法生成，FAILED 配合既有
   `FAILED → WAITING_CONFIRMATION`（重新确认）即可恢复，语义完备。
4. 行为已被测试钉住并经验收（1721 全绿），追认零代码变更。

---

## 工单 C：Step 08 retry 回退微修复（裁定 1 落地；Step 09 的"失败后一键重试"前置）

### 文件触碰范围（冻结，除此不得触碰任何其他文件）

1. **`visual_intent_agent/persistence/repository.py`**
   - `Repository(Protocol)` 与 `SQLiteRepository` 各增加
     `get_latest_prompt_artifact(session_id: str) -> StoredArtifact | None`；
   - 实现**逐行对照**既有 `get_current_realization_state`：`_require_non_empty` →
     `_require_session` → `SELECT prompt_artifact_id, session_id, refs_json, payload,
     created_at FROM prompt_artifacts WHERE session_id = ? ORDER BY created_at DESC,
     rowid DESC LIMIT 1` → 无行返回 None，有行 `_row_to_artifact(row,
     "prompt_artifact_id")`；
   - **不得**改 `schema.sql`（`idx_prompt_artifacts_session` 已存在）、不得改任何
     既有方法签名与语义；`persistence/__init__.py` 无需改动（方法经 Protocol/类
     暴露，不新增符号）。
2. **`visual_intent_agent/generation/pipeline.py`**（仅 retry 路径）
   - `retry` 中读原 GenerationArtifact 抛 `persistence.artifact_not_found` 时回退到
     `repo.get_latest_prompt_artifact(session_id)`：返回 None → 仍抛
     `GenerationError(generation.artifact_not_found)`；命中 → 反序列化为
     PromptArtifact（session 由查询条件保证，无需再校验跨会话），与其余路径共用
     既有的 `is_confirmation_valid(based_on_confirmation_id)` 校验
     （失败 → `generation.no_valid_confirmation`，状态不变、不调用 Provider）；
   - 只改写 `_load_generation_artifact` / `retry` 的回退分支与模块 docstring 的
     retry 段；**不得**改 `generate` 路径、不得新增 compile 调用、不得新增
     Issue code（复用现有 5 个 `generation.*` code）。
3. **`tests/persistence/`**（新增测试文件，如
   `test_persistence_prompt_artifact_queries.py`）：覆盖空会话 → None、单条、
   多条取最新（created_at 相同则 rowid 定序）、会话隔离、不存在会话 →
   `persistence.session_not_found`。
4. **`tests/generation/`**（`test_generation_pipeline.py`，必要时
   `generation_helpers.py` 加自包含装配 helper）：
   - **必测新用例**：纯 Provider 失败后 `retry` 成功且复用同一 PromptArtifact、
     不重新 compile（断言：`prompt_artifacts` 行数不变、Provider 收到与首次相同的
     `prompt`/`size`/`model`、产生**新** generation_id、状态
     FAILED → GENERATING → WAITING_REVIEW）；
   - 回退路径下确认已失效 → `generation.no_valid_confirmation`、状态保持 FAILED、
     Provider 零调用；
   - 会话从未编译过 Prompt（无任何 PromptArtifact）→ 仍
     `generation.artifact_not_found`；
   - 更新钉住旧行为的既有用例（原"Provider 失败后 retry 抛
     artifact_not_found"改为断言新回退语义）；原 Artifact 存在时的既有 retry
     用例必须原样通过。
5. **`docs/handoffs/step_08_patch_001.md`**（新建）：记录补丁内容、变更文件、
   测试命令与结果；**不得修改** `step_08_handoff.md` 原文。

### 禁止

改 `schema.sql` / `state_machine.py` / `records.py` / 其他任何产品模块；改任务书
md / `README.md` / `api.md` / 既有 handoff / `ARCHITECTURE.md`（架构方已改）/
`pyproject.toml` / `uv.lock` / `.env`；新增依赖、新增表/字段、新增 Issue code、
新增状态迁移；让 retry 以任何形式重新 compile。

### 验收命令（全部必须绿）

```bash
cd /home/change/projects/image_system
uv run pytest -q                      # 全量绿（1721 + 新增用例）
uv run pytest tests/persistence -q    # 含 get_latest_prompt_artifact 新用例
uv run pytest tests/generation -q     # 含"纯 Provider 失败后 retry 复用同一
                                      # PromptArtifact、不重新 compile"新用例
```

## 对 Step 09 实施 Agent 的指令

1. **Step 09 主流程不依赖本裁定**：revise 路径是 `WAITING_REVIEW → UNDERSTANDING`
   （既有迁移）→ 重新确认（`WAITING_CONFIRMATION`）→ `generate`（重新 compile，
   这是新一次生成而非 retry），P3 闭环的全部依赖在 Step 08 已交付范围内。
2. **"失败后一键重试"能力依赖工单 C**：若 Step 09 的 ReviewService/应用层要对
   FAILED 会话提供重试入口，调用 `GenerationPipeline.retry(session_id,
   generation_id)`（generation_id 取自 `generation.failed` 结构化日志事件）；
   工单 C 落地前该入口在纯 Provider 失败场景会抛
   `generation.artifact_not_found`，落地后按裁定 1 回退语义工作。**Step 09 不得**
   自行实现回退查询、不得绕过 Repository 直读 SQLite、不得用 `generate` 代替
   retry（会重新 compile，违反任务书规则 5）。
3. `get_latest_prompt_artifact` 是只读 getter，Step 09 如需"会话最近 Prompt"
   也可直接使用（如展示当前 Prompt 来源），但反馈关联仍必须以
   `GenerationArtifact.prompt_artifact_id` 为准。
4. `WAITING_REVIEW` 下不提供"重做"：`WAITING_REVIEW → FAILED` 不是合法迁移，
   Step 09 不得自行扩展迁移表（与 step_08_handoff「对下一步的输入」第 3 条一致）。
5. 错误观察入口：FAILED 状态（`session.workflow_state`）+ logger
   `visual_intent_agent.generation.pipeline` 的 `generation.failed` 结构化事件；
   MVP 不提供经 Repository 查询错误记录的接口（裁定 1.5 的已知边界），Step 09
   不得为此新增表或字段。
