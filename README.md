# Visual Intent Agent

Visual Intent Agent 是一个面向图像生成工作流的结构化意图系统。它把用户的自然语言需求转换为可验证、可确认、可追踪的 `VisualIntent`，再据此编译提示词、调用图像模型，并在多轮反馈中精确修改指定内容。

项目目标不是单纯生成更长的 Prompt，而是减少隐式决策、避免多轮修改中的属性漂移，让每次生成都能说明“用户要求了什么、系统代为决定了什么、最终生成依据是什么”。

## 核心架构

```text
用户输入
  → IntentEngine（理解意图并提出变更）
  → Validator / Reducer（校验并更新结构化状态）
  → DecisionPolicy（识别缺失、冲突与委托项）
  → Workflow（澄清与确认）
  → PromptEngine（从已确认意图编译 Prompt）
  → GenerationPipeline（生成并保存图片与 Artifact）
  → FeedbackEngine（将反馈转换为下一轮精确变更）
```

SQLite Repository 保存 Intent、确认记录、Prompt、生成结果和反馈的版本链。LLM 负责语言理解与表达，确定性代码负责状态变更、权限边界和流程约束。

## 项目原则

- 用户未明确表达的重要视觉内容，不由系统静默决定。
- LLM 只能提出候选变更，不能直接修改状态。
- 生成前必须确认当前 Intent，重要修改会使旧确认失效。
- 历史 Revision 与 Artifact 保持可追踪且不可覆盖。

## 最小 CLI（MVP v0.3 工程预览版）

> **状态：工程预览版，非正式发布。** 本轮（r1 修订后）的真实 smoke、正式复评与
> 人工盲评**尚未执行**；本预览版对应用户流程的结论是“未执行 / 证据不足”，既不是
> Go，也不是 No-Go。Step 01～06 的真实历史数据与首轮结论仍然有效，本预览版不重跑、
> 也不改写它们。
> 详见 [docs/releases/mvp_v0.3_preview.md](docs/releases/mvp_v0.3_preview.md)。

环境：Python 3.12 + [`uv`](https://docs.astral.sh/uv/)。安装依赖：

```bash
uv sync
```

### 离线演示（无需任何凭据，不访问网络）

```bash
uv run python -m visual_intent_agent --demo
```

`--demo` 使用确定性 Fake Provider（`_DemoLLM` + `FakeImageProvider`），用于演示完整交互
闭环。**它只识别预设选项与少量关键词（脚本式演示），不是真实自然语言理解**，也不代表
产品能力。默认数据库 `data/cli_demo.db`、输出目录 `outputs/generations/`，可用
`--db` / `--output-dir` 覆盖。

### 真实模式（需要 Provider 配置）

在项目根 `.env`（或环境变量）中设置：

```ini
VIA_PROVIDER_BASE_URL=https://<your-provider-endpoint>
VIA_PROVIDER_API_KEY=<your-key>
# 可选：VIA_LLM_MODEL / VIA_IMAGE_MODEL / VIA_DB_PATH / VIA_OUTPUT_DIR 等
```

然后运行：

```bash
uv run python -m visual_intent_agent
```

CLI 只显示模型名、Revision / Artifact ID 与本地输出路径；不打印 API key、Authorization
或 Provider 原始响应。配置缺失时会给出可读提示并退出（退出码 2）。

### 交互流程

```text
需求 → 澄清（每轮一个问题）→ 展示 diff-first 摘要 → 用户显式确认
     → 生成并显示输出文件路径 / generation_id → 接受或提交修改反馈
     → 重新确认 → 生成下一版 → 退出
```

约定：

- **不会自动确认**：只有显式输入 `y` / `yes` / `confirm` / `确认` 才会确认并生成；
  在确认页输入其它文本会被当作修改消息，产生新 revision 并再次要求确认。
- **确认绑定实际展示的摘要**：提交确认时绑定你刚看到的那份摘要与 revision；若展示后
  版本已变化，会被拒绝并要求重新查看摘要，不会静默确认你没看到的版本。
- **失败可显式重试**：Provider 超时 / 解析失败等可恢复失败**不推进状态、不生成、
  不确认**；界面会显示“本轮未处理成功”，输入 `retry` 可重发上一条消息。
- **生成失败（FAILED）**：有可复用的 Prompt 时可输入 `retry`（复用同一 Prompt，不重新编译）；
  若编译就已失败、没有可复用 Prompt，则只能 `exit` 后新建会话。
- `exit` / `quit` / `退出` 可随时结束会话。

## 测试

默认测试完全离线（不读取 `.env`、不调用真实 Provider）：

```bash
uv run pytest -q
```

真实 Provider smoke 测试需显式选择并自行提供凭据：

```bash
uv run pytest -m smoke
```

CLI 的离线端到端测试位于 `tests/cli/`，覆盖正常闭环、局部反馈后重新确认、timeout
恢复、确认页不自动确认与 `--demo` 入口。

## 文档

- [架构设计](docs/ARCHITECTURE.md)
- [版本任务书](docs/task_books/README.md)
- [实施交接记录](docs/handoffs/)
- [发布记录（预览）](docs/releases/mvp_v0.3_preview.md)

项目使用 Python 3.12、Pydantic、SQLite、HTTPX 与 pytest。
