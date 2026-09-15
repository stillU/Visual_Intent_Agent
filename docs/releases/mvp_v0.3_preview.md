# MVP v0.3 工程预览版发布记录（非正式发布）

- 记录类型：**工程预览（engineering preview）**，**不是** MVP v0.3 正式发布记录。
- 依据：`docs/task_books/mvp_v0.3/REVISION_002_BUILD_FIRST.md`、`docs/task_books/mvp_v0.3/08_minimal_cli_release.md`。
- 对应交接：`docs/handoffs/v0_3_step_08_handoff.md`。
- 本轮不自动创建 Git 提交；保留工作区既有 dirty 状态（见 REVISION_002「当前断点与优先级」）。

> **发布状态（针对本轮 / r1 修订后）：未执行 / 证据不足 —— 既不是 Go，也不是 No-Go。**
> 本轮按 REVISION_002 提前实现 CLI，**未执行**本轮/r1 修订后的真实 smoke、固定真实探针、
> 同协议正式 A/B、人工盲评与 Gate A 裁定。
> Step 01～06 已有的真实历史数据与首轮结论仍然有效；本记录不重跑、不否定、也不改写它们。
> 本记录只声明"代码真实落地、CLI 可启动、离线 Fake 闭环通过"，不声称任何产品假设成立。

---

## 1. 本预览版交付内容

| 交付物 | 说明 |
|---|---|
| `visual_intent_agent/__main__.py` | `python -m visual_intent_agent` 模块入口 |
| `visual_intent_agent/cli.py` | 最小 CLI：`SessionApp` 薄门面 + 状态驱动交互循环 + `--demo` 离线演示装配 |
| `tests/cli/` | 全 Fake、全离线端到端测试（正常闭环 / 局部反馈 / timeout 恢复 / 不自动确认 / 配置错误 / demo / 模块入口） |
| 根 `README.md` | 最小安装、运行与交互说明；明确预览状态与 demo 局限 |
| 本文件 | 预览发布记录 |
| `docs/handoffs/v0_3_step_08_handoff.md` | 交接记录 |

CLI 复用既有模块，不新增业务规则、不改任何工作流 / 策略 / 评测 / Provider 模块：

```text
Settings + SQLiteRepository
  → WorkflowService（需求 → 澄清 → WAITING_CONFIRMATION + diff-first 摘要）
  → 人工显式确认（CLI 不自动确认）
  → confirm_current_intent + GenerationPipeline（→ PromptArtifact / GenerationArtifact / 本地图片）
  → ReviewService.submit_feedback（接受 / 修改 / 澄清）
  → 修改必须重新确认 → 再生成下一版
```

## 2. 已验证的内容（离线）

验证命令与范围仅限本工包（`REVISION_002`：子代理只做所需局部测试；全量离线验收由协调者 E 集中执行一次）：

```bash
uv run pytest tests/cli -q
```

覆盖（`tests/cli/` 共 11 条）：

1. **正常闭环**：需求 → 7 轮单问题澄清 → diff-first 摘要 → 显式确认 → 生成并显示输出路径 / `generation_id` → 接受 → `COMPLETED`；确认 1 次、生成 1 次、输出文件存在；终端不出现凭据 / Provider 地址。
2. **局部反馈**：修改 `composition.framing` → `WAITING_CONFIRMATION`（**必须重新确认**）→ 生成第 2 个 Artifact；两次生成 id 不同、历史 Artifact 仍可读取。
3. **timeout 恢复**：回答澄清问题时 Interpreter 连续两次 `provider.timeout` → 状态不推进、同一待答问题保留、无生成、无确认；界面显示"本轮未处理成功"，输入 `retry` 显式重发后才继续。
4. **FAILED 生成重试（首次失败）**：图像 Provider 首次超时 → `FAILED` 且无 `GenerationArtifact`；输入 `retry` 省略 id，复用同一 `PromptArtifact`（不重新 compile）成功生成。
5. **FAILED 生成重试（第二版失败）**：第一版成功 → 修改再确认 → 第二次生成失败；`retry` 必须复用**第二版**编译的 Prompt（不得挑上一版历史 Artifact 的旧 Prompt）。
6. **确认绑定**：展示摘要后当前 revision 被外部改变 → 该次确认被 `workflow.stale_revision` / `summary_hash_mismatch` 拒绝，不静默确认用户没看到的版本；重新展示后再确认才生效。
7. **Prompt 编译失败**：`prompt.unsupported_requirement` 被 CLI 捕获（不再未捕获抛出）；无已落库 Prompt 时明确提示"只能 exit 后新建会话"，不暗示当前 `FAILED` 可直接重新确认。
8. **不自动确认**：确认页输入修改消息 → 只落新 revision，确认数仍为 0；只有显式 `y` 才确认并生成（最终确认 1 次 / 生成 1 次）。
9. **配置缺失**：真实模式无凭据 → 可读提示 + 退出码 2，不调用任何 Provider。
10. **`--demo`**：无凭据、`env={}`、`env_file=None` 下可完成闭环。
11. **模块入口**：`python -m visual_intent_agent --help` 在无凭据环境下退出码 0。

协调者 E 在全部工包停止写入后完成一次集中全量离线验收：`uv run pytest -q` → **2215 passed, 3 deselected in 6.73s**（含上述 11 项 CLI 测试、新旧清单哈希与 r1 回归）。详见 `docs/handoffs/v0_3_r2_integration_handoff.md`。

本轮**未执行**：r1 修订后的真实 Provider smoke（`-m smoke`）、付费图片批次、人工盲评、r1 正式复评与 Gate A 裁定。Step 01～06 的真实历史数据与首轮结论不属于本轮范围，也不被本记录改写。

## 3. 已知限制与明确未完成项

1. **本轮正式发布条件未满足**：`08_minimal_cli_release.md` 的启动条件是"Step 05/07 Gate A 为 Go"；本轮按 REVISION_002 提前实现 CLI，**没有本轮/r1 修订后的 Gate A 结论**，因此本版本只能标注为工程预览（不是对整个项目"从未有真实数据"的判断）。
2. **`--demo` 是脚本式演示，不是自然语言理解**：`_DemoLLM` 只识别预设选项与少量中英文关键词，用于演示闭环；它不代表真实模型能力，也不进入任何产品指标。
3. **真实模式未做端到端真实运行**：CLI 的真实装配复用既有 adapter，但本轮未使用真实凭据执行该入口，没有新增真实调用证据；**本轮/r1 修订后的**真实 smoke 仍未执行（Step 01～06 的历史真实调用记录不在本记录范围）。
4. **FAILED 重试一律复用"当前失败尝试"的已落库 Prompt（已落地）**：`GenerationPipeline` 在纯 Provider 失败时按契约不写 `GenerationArtifact`，也不把内部 `generation_id` 暴露给调用方。generation 侧已提供 `retry(session_id, generation_id: str | None = None)`：省略 id → 本会话最新已落库 PromptArtifact（保留 FAILED + 有效确认门禁；空字符串仍拒绝）。CLI 在 `FAILED` 时**一律省略 id**，因为"会话最新历史 GenerationArtifact"可能属于上一版（修改后第二次生成失败时会给旧 Prompt）；不按历史 Artifact 挑 Prompt，也不伪造 id。
5. **可恢复失败字段（B 工包已落地）**：CLI 前向兼容读取 `SubmitMessageOutcome.recoverable_failure` / `failure_codes`（`FeedbackOutcome` 同理）；字段缺失时回退为 `severity=ERROR` 的 issue 判定（绝不用 `pending_question is None` 推断成功）。失败时显式显示"本轮未处理成功"，不做自动重试。
6. **Provider 错误不显示原始响应**：CLI 只显示安全 code 与 `retryable` / `status_code`，避免泄露上游响应或凭据。
7. **包版本号未改**：`visual_intent_agent.__version__` 保持 `0.1.0`；未用版本号冒充 v0.3 正式发布。

## 4. 后续独立任务（不在本预览版内）

```text
冻结干净代码快照
  → 一次固定真实探针
  → 同协议正式 A/B
  → 人工盲评
  → Gate A 裁定
  → 决定是否正式发布（届时另立 docs/releases/mvp_v0.3.md）
```

在**本轮/r1 修订后**的真实覆盖与人工结论缺失时，状态保持"未执行 / 证据不足"。
