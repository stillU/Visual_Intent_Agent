# MVP v0.3 Step 08 交接记录：最小 CLI（工程预览版）

- 工包：D「最小 CLI」（`docs/task_books/mvp_v0.3/REVISION_002_BUILD_FIRST.md` 文件级分工）。
- 任务书：`docs/task_books/mvp_v0.3/08_minimal_cli_release.md`。
- 发布记录：`docs/releases/mvp_v0.3_preview.md`（**预览，非正式发布**）。
- 状态：CLI 真实落地、可启动；离线 Fake 端到端通过。**本轮/r1 修订后的真实 smoke、正式复评与人工盲评未执行**（Step 01～06 的真实历史数据与首轮结论仍然有效，不在本轮范围）。

## 0. 边界与未触碰文件

独占新增 / 修改（本工包写入范围）：

| 文件 | 类型 | 说明 |
|---|---|---|
| `visual_intent_agent/__main__.py` | 新增 | 模块入口，只转发 `cli.main` |
| `visual_intent_agent/cli.py` | 新增 | 装配、交互循环、错误渲染、`--demo` |
| `tests/cli/cli_helpers.py` | 新增 | 自包含离线测试工具（脚本化 Console / LLM、装配） |
| `tests/cli/test_cli_e2e.py` | 新增 | 离线端到端测试 |
| 根 `README.md` | 修改 | 最小使用说明 + 预览状态 |
| `docs/releases/mvp_v0.3_preview.md` | 新增 | 预览发布记录 |
| `docs/handoffs/v0_3_step_08_handoff.md` | 新增 | 本文件 |

**未修改**：`workflow/**`、`policy/**`、`evaluation/**`、`providers/**`、`generation/**`、
`prompt_engine/**`、`persistence/**`、`feedback/**`、`intent_engine/**`、`validation/**`、
`realization/**`、`config.py`、`pyproject.toml`、`.env`、`api.md`。跨范围需求见 §6。

## 1. 使用的既有接口（未新增业务规则）

```python
Settings                       # config.load_settings；CLI 不直接读环境变量
SQLiteRepository               # db_path；CLI 只做读取（list_generation_artifacts）与关闭
WorkflowService                # create_session / submit_message / get_session /
                               # get_confirmation_summary / confirm_current_intent
ReviewService.submit_feedback  # 从 workflow.review 导入（包根不出导出）
GenerationPipeline             # generate / retry
PromptEngine(QwenImageRenderer, repo)
Interpreter(llm) → IntentEngine(llm)
FeedbackEngine(llm)、QuestionBuilder()
compute_summary_hash           # 客户端展示摘要后交给 confirm_current_intent 的冻结算法
```

`SessionApp.confirmation_view` + `confirm_and_generate` 绑定的是**用户实际看到的那份摘要**：

```text
snapshot, summary = confirmation_view(sid)      # REPL 展示 summary
  → compute_summary_hash(summary)               # 用展示时捕获的同一对象
  → confirm_current_intent(sid, snapshot.irev, snapshot.erev, hash)
        # 服务按**当前**快照重算并比对：展示后 revision 变了 → stale_revision / hash_mismatch
  → GenerationPipeline.generate(sid)
```

CLI 不缓存确认、不重算 revision、不替服务判断状态；状态只读 `snapshot.workflow_state`。
关键点：`confirm_and_generate` **不**重新读取"最新摘要"来静默授权——绑定失败就拒绝，
由用户重新查看摘要后再确认。

## 2. 交互闭环

```text
UNDERSTANDING / WAITING_CLARIFICATION
    输入需求或澄清回答 → submit_message
WAITING_CONFIRMATION
    展示 diff-first 摘要（六要素 + 完整 Intent + 摘要哈希前缀）
    输入确认词 → confirm_and_generate(展示时的 summary + irev/erev)（显示路径与 generation_id）
    输入其它文本 → 当作修改消息 → submit_message（落新 revision，再次要求确认）
WAITING_REVIEW
    显示当前 GenerationArtifact
    输入修改反馈 → submit_feedback
    输入 accept / 接受 → 以规范接受原文提交 FeedbackEngine（仍由 LLM 裁决 accept）
FAILED
    有已落库 Prompt → 输入 retry → GenerationPipeline.retry(sid)（省略 id，复用最新 Prompt）
    无已落库 Prompt（编译失败/从未编译）→ 明确提示只能 exit 后新建会话，不提示可重新确认
COMPLETED
    结束
```

- 确认词：`y` / `yes` / `confirm` / `ok` / `确认` / `是` / `生成`。
- 退出词：`exit` / `quit` / `q` / `退出` / `结束`。
- 显式重试：`retry` / `重试` 重发**上一条**消息/反馈；CLI 不自动重试（无自动重试层）。

## 3. 不变量核对（任务书 08「验收条件」）

| 不变量 | CLI 做法 |
|---|---|
| 不绕过确认 | 只有显式确认词才调用 `confirm_current_intent`；确认页其它输入一律当修改 |
| 确认绑定可见版本 | 提交展示时捕获的 summary + irev/erev；展示后变化被 stale/hash 门禁拒绝 |
| 不绕过 Revision | 修改走 `submit_message` / `submit_feedback`，由服务落新 `IntentRevision`；CLI 不写库 |
| 不绕过 Artifact | 生成只经 `GenerationPipeline`；历史 Artifact 只读、不覆盖 |
| 不自动确认 | 无任何自动确认路径；`_outcome_failed` 也不用 `pending_question is None` 推断成功 |
| 不复制状态规则 | 下一步动作由 `snapshot.workflow_state` 驱动，迁移全部由既有服务执行 |
| 不输出凭据 | 只显示模型名、ID、路径；Provider 异常只显示安全 code + retryable/status |
| 可恢复失败 | 显示"本轮未处理成功 / 状态未推进"，提供显式 `retry`；`workflow.stale_revision` 时提示已刷新需重新确认 |
| FAILED 重试不取旧 Prompt | 一律 `retry(sid)`（省略 id）取本会话最新已落库 Prompt，不按历史 GenerationArtifact 挑 |
| 编译失败可恢复 | `PromptCompilationError` 已接入 `_HANDLED_ERRORS`；无 Prompt 时明确只能退出/新会话 |

## 4. 与 B 工包（可恢复失败）的接口对接

B **已全部落地**：`SubmitMessageOutcome.recoverable_failure: bool = False`、
`failure_codes: tuple[str, ...] = ()`；`FeedbackOutcome` 已有 `recoverable_failure`，并新增
`failure_codes`。CLI 采用**前向兼容读取**：

```python
_outcome_failed(outcome)   # 显式 recoverable_failure 优先；缺失时回退 severity=ERROR 的 issue
_failure_codes(outcome)    # 显式 failure_codes 优先；缺失时从 ERROR issue 收集 code
```

因此：

- CLI 不依赖未定义字段，也不会把"无待答问题"误判为成功；
- `failure_codes` 含 `workflow.stale_revision` 时，界面明确提示"确认绑定已过期，已按最新状态
  刷新；请重新查看摘要后再确认"；确认本身绑定的是**用户实际看到的那份摘要**（展示时捕获的
  summary + irev/erev），**不会**在提交时重新读取"最新摘要"静默授权；
- 不做任何自动重试或自动状态修复。

## 5. 测试

```bash
uv run pytest tests/cli -q
```

`tests/cli/` 自包含、全离线（`tmp_path` SQLite、`FakeImageProvider`、脚本化 LLM），
不读取项目 `.env`、不调用真实 Provider：

1. `test_cli_normal_flow_confirm_generate_and_accept`
2. `test_cli_partial_feedback_requires_reconfirmation_and_new_artifact`
3. `test_cli_provider_timeout_is_recoverable_only_by_explicit_retry`
4. `test_cli_failed_generation_retry_reuses_prompt_without_fabricated_id`
5. `test_cli_retry_after_second_generation_failure_uses_second_prompt`
6. `test_cli_confirm_rejects_revision_changed_after_summary_display`
7. `test_cli_prompt_compilation_failure_is_handled_and_cannot_retry`
8. `test_cli_never_confirms_without_the_explicit_confirmation_token`
9. `test_real_mode_missing_credentials_returns_clear_error`
10. `test_demo_mode_runs_offline_without_credentials`
11. `test_module_entrypoint_help_works_without_credentials`

结果见本文件末尾「执行记录」（协调者 E 负责集中全量离线验收；本工包只跑局部测试）。

## 6. 跨模块需求 / 待裁定（交协调者 E）

1. **FAILED 重试的 Prompt 选择（已落地，CLI 侧已修正）**：`GenerationPipeline` 纯 Provider
   失败时不写 `GenerationArtifact`，也不把内部 `generation_id` 返回给调用方。generation 侧已
   提供 `retry(session_id, generation_id: str | None = None)`：省略 id → 本会话最新**已落库**
   PromptArtifact（保留 FAILED + 有效确认门禁；空字符串仍拒绝）。CLI **一律省略 id**，因为
   "会话最新历史 GenerationArtifact"可能属于上一版（修改后第二次生成失败时会取到旧 Prompt）；
   用例 `test_cli_retry_after_second_generation_failure_uses_second_prompt` 钉住该行为。
2. **确认绑定（CLI 侧已修正）**：`confirm_and_generate` 改为接收 REPL **实际展示**的 summary
   与捕获的 irev/erev，绑定失败即拒绝；用例
   `test_cli_confirm_rejects_revision_changed_after_summary_display` 覆盖"展示后 revision 改变"。
3. **Prompt 编译失败已接线**：`PromptCompilationError` 加入 `_HANDLED_ERRORS`；无已落库 Prompt
   时明确提示只能 exit / 新建会话，不提示当前 FAILED 可直接重新确认；用例
   `test_cli_prompt_compilation_failure_is_handled_and_cannot_retry` 覆盖。
4. **B 工包回归（已修复）**：`visual_intent_agent/workflow/models.py::engine_failure_codes` 曾在
   进行中改动里出现 `NameError`（生成器表达式里应为 `issue.code`），只在"有引擎失败 issue"
   （如 `provider.timeout`）时触发；已同步协调者并由 B 修复。该文件属 B 范围，D 未修改。
5. **本轮正式发布门禁**：Step 08 启动条件（Step 05/07 的 Gate A=Go）在**本轮/r1 修订后**
   尚无结论；本轮按 REVISION_002 提前实现，发布记录只能标注"工程预览 / 非正式发布"，正式
   `docs/releases/mvp_v0.3.md` 需待本轮/r1 修订后的 Gate A。Step 01～06 的真实历史数据与
   首轮结论仍然有效，本交接不重跑、也不否定它们。

## 7. 如何运行

```bash
# 离线演示（无需凭据）
uv run python -m visual_intent_agent --demo

# 真实模式（需 .env 配置 VIA_PROVIDER_BASE_URL / VIA_PROVIDER_API_KEY）
uv run python -m visual_intent_agent

# 覆盖路径
uv run python -m visual_intent_agent --demo --db data/demo.db --output-dir outputs/demo
```

## 8. 执行记录

- 本工包只跑 `uv run pytest tests/cli -q`（局部离线）：**11 passed**。
- 另手工验证 `python -m visual_intent_agent --demo` 以管道输入跑通完整闭环（退出码 0、
  生成文件落盘）、`--help` / `--version` 正常。
- 未跑全量、未跑 smoke、未做真实调用；全量离线验收由协调者 E 集中执行一次；
  **本轮/r1 修订后**的真实 smoke、正式复评、人工盲评与 Gate A 均**未执行**（Step 01～06
  的真实历史调用不在本轮范围）。
