# Visual Intent Agent 使用说明

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

## 最小 CLI（v1.0.0rc1 候选交付）

> **状态：v1.0.0rc1 候选交付；本轮仅静态整理，未测试、未运行验证，非正式稳定发布。**
> v0.4 在 v0.3 最小闭环上增加**可选、默认关闭**的
> 本地 RAG 工程链路。生产知识库 `knowledge_base/v0.4/` 的 9 条**全部为 draft、0 条
> approved**，仍未人工审核；`knowledge_base/v0.5/`（`corpus_version = v0.5-approved-1`）
> 是本轮经真实人工审核（审核人 `change`，2026-09-16）批准的**独立快照**，含 5 条
> `approved`，需用 `--knowledge-dir` 显式选择。当前离线安全测试只允许声称“工程链路可用”，
> **不允许**声称优于无 RAG、知识质量达标或正式发布成功。
> v0.3 的 r1 真实 smoke、正式复评与人工盲评仍未执行。详见
> [历史预览记录](releases/mvp_v0.3_preview.md)。

环境：Python 3.12+（`pyproject.toml` 的 `requires-python = ">=3.12"`）与
[`uv`](https://docs.astral.sh/uv/)。在**仓库根目录**安装依赖：

```bash
uv sync
```

本项目是**非安装包**（`pyproject.toml` 中 `[tool.uv] package = false`）：`uv sync` 只把依赖
装进 `.venv`，不安装 `visual-intent-agent` 发行包，也没有 console script。所有命令都在仓库
根目录通过源码模块入口 `python -m visual_intent_agent` 运行（下文统一写作
`uv run python -m visual_intent_agent`）。当前没有 pip 发行包、容器或独立安装程序。

路径解析：`Settings` 的默认值与默认语料目录由仓库根（`config.PROJECT_ROOT`）推导为绝对路径；
`--db` / `--output-dir` 以及 `VIA_DB_PATH` / `VIA_OUTPUT_DIR` 中的相对路径按**当前工作目录**
解析，因此建议始终在仓库根运行。

### 离线演示（无需任何凭据，不访问网络）

```bash
uv run python -m visual_intent_agent --demo
```

`--demo` 使用确定性 Fake Provider（`_DemoLLM` + `FakeImageProvider`），用于演示完整交互
闭环。**它只识别预设选项与少量关键词（脚本式演示），不是真实自然语言理解**，也不代表
产品能力。`--demo` 不读取 `.env`，不需要任何 `VIA_*` 配置。默认数据库
`data/cli_demo.db`（**与真实模式默认库 `data/visual_intent.db` 不同**）、输出目录
`outputs/generations/`，可用 `--db` / `--output-dir` 覆盖。

### 真实模式（需要 Provider 配置）

默认真实模式会调用真实 Provider 并**可能产生费用**。日常 CLI **不包含** v0.6 评测层的预算
守卫，也**没有全局消费限额**（`evaluation/v0_6/budget.py` 只服务评测入口，不是日常 CLI 的
门禁），费用只能由你在 Provider 侧自行控制。

把仓库根的 `.env.example` 复制为 `.env` 后填写，或直接使用进程环境变量：

```bash
cp .env.example .env
```

配置优先级：进程环境变量 > 仓库根 `.env` > `Settings` 字段默认值。`.env` 支持 `#` 注释行、
可选的 `export ` 前缀与可选引号。变量名以下表为准，唯一来源是
`visual_intent_agent/config.py` 的 `ENV_VAR_NAMES`：

| 变量 | 必填 | `Settings` 默认值 | 说明 |
|---|---|---|---|
| `VIA_PROVIDER_BASE_URL` | 是 | — | Provider endpoint；必须以 `http://` 或 `https://` 开头 |
| `VIA_PROVIDER_API_KEY` | 是 | — | Provider 凭据；只以 `SecretStr` 持有，不打印 |
| `VIA_LLM_MODEL` | 否 | `qwen3.8-max` | 语言模型别名；默认值只是字段默认，不是已核实的官方模型版本声明 |
| `VIA_IMAGE_MODEL` | 否 | `qwen-image-3.0` | 图片模型别名；同上 |
| `VIA_LLM_TIMEOUT_SECONDS` | 否 | `60.0` | 语言模型单次调用超时（秒） |
| `VIA_IMAGE_TIMEOUT_SECONDS` | 否 | `120.0` | 图片模型单次调用超时（秒） |
| `VIA_HTTP_MAX_RETRIES` | 否 | `2` | Provider 适配器传输层重试上限（指数退避 `0.5s × 2^n`） |
| `VIA_DB_PATH` | 否 | `data/visual_intent.db`（由仓库根推导） | 业务 SQLite 数据库路径；与 `--demo` 默认库不同 |
| `VIA_OUTPUT_DIR` | 否 | `outputs/generations`（由仓库根推导） | 生成图片输出目录 |

然后运行：

```bash
uv run python -m visual_intent_agent
```

CLI 只显示模型名、Revision / Artifact ID 与本地输出路径；不打印 API key、Authorization
或 Provider 原始响应。配置缺失时会给出可读提示并退出（退出码 2）。

### 本地知识检索 RAG（v0.4，可选、默认关闭）

RAG **默认关闭**；不开启时完全不读取知识、不检索、不生成 `KnowledgeBundle`，旧流程不变。

```bash
# 关闭（默认）：旧流程
uv run python -m visual_intent_agent

# 显式开启：读取本地 JSONL 语料（默认 knowledge_base/v0.4，当前全 draft）
uv run python -m visual_intent_agent --rag

# 指定本地语料目录（只接受本地路径；远程 URL 一律拒绝）
uv run python -m visual_intent_agent --rag --knowledge-dir knowledge_base/v0.4

# 选择已获真实审核的 v0.5 发布快照（5 条 approved；仍不启动正式评测）
uv run python -m visual_intent_agent --rag --knowledge-dir knowledge_base/v0.5

# 离线演示 + 内置演示/测试 approved 夹具（不是生产审核，不代表真实效果）
uv run python -m visual_intent_agent --demo --rag
```

约定与真实状态：

- `--rag` 只接受本地目录，**不读取任意远程链接、不自动下载模型或语料**；路径/格式/
  版本错误在启动时给出可读提示并以退出码 2 结束，**不会伪装成启用成功**，也不会静默
  退化成“无 RAG 继续运行”。
- 仅当 `--rag` 开启时才创建 `LocalKnowledgeEngine` 并注入 `PromptEngine`；知识只能辅助
  **仍被明确委托、且尚未有 active Realization** 的白名单路径，不能覆盖用户指定值与 PIN。
- 确认前的摘要会显示“知识仅辅助明确委托项”，但**确认哈希算法不变**：用户确认的是已
  展示的 Intent 与委托范围，不是任何知识取值。
- 生成后会显示本次**采用 / 回退 / 复用**、受影响路径与 `Bundle`/知识单元来源 ID，可按
  `bundle_id` 从 Repository 查回完整来源；界面明确说明这不代表用户确认过具体知识取值。
- `--demo --rag` 只使用 CLI 内置、明确标注为**演示/测试**的 approved 夹具，绝不冒充生产
  人工审核或真实检索/图片效果，也不修改生产 draft。该组合**不能与外部 `--knowledge-dir`
  混用**：同时给出会以 `cli.demo_knowledge_conflict` 报错并以退出码 2 结束。
- 只给 `--knowledge-dir` 而**不给** `--rag` 时，CLI 只做本地路径形态校验（仍拒绝远程 URL）
  并提示“本次不读取任何知识目录”，不会启用 RAG。
- **知识库质量、检索质量、最终图片质量是三个不同结论。** `knowledge_base/v0.4/` 的 9 条
  仍为 draft、0 条 approved；`knowledge_base/v0.5/` 的 5 条已获真实人工审核批准，但默认
  路径仍是 v0.4，本机默认 RAG 只会透明回退到既有固定候选。工程测试通过不等于 RAG 有效。

### 命令行参数

当前 CLI 的全部参数（以 `uv run python -m visual_intent_agent --help` 的实际输出为准）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--demo` | 关闭 | 离线演示模式：确定性 Fake Provider，不需要凭据、不访问网络；只做预设选项与少量关键词映射（脚本式演示），不是真实自然语言理解。默认库 `data/cli_demo.db`。 |
| `--rag` | 关闭 | 显式开启本地知识检索；未开启时完全不读取知识、不检索、不生成 `KnowledgeBundle`。 |
| `--knowledge-dir` | 仅 `--rag` 时生效，默认 `knowledge_base/v0.4` | 本地 `JSONL + manifest.json` 语料目录；只接受本地路径，远程 URL 一律拒绝。选择已批准快照用 `knowledge_base/v0.5`。 |
| `--db` | `--demo`：`data/cli_demo.db`；真实模式：`Settings.db_path`（默认 `data/visual_intent.db`） | SQLite 数据库路径。 |
| `--output-dir` | `outputs/generations/`（`--demo` 与真实模式默认相同） | 生成图片输出目录。 |
| `--version` | — | 打印版本字符串后退出；版本号引用包 `visual_intent_agent.__version__`，不在 CLI 内重复硬编码。 |

`--demo` 与真实模式的**默认数据库不同**（`data/cli_demo.db` vs `data/visual_intent.db`），
默认输出目录相同；两者都可用 `--db` / `--output-dir` 覆盖。

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
- **不绕过 PIN**：CLI 不提供自动确认或自动解除 `PIN`（保持标记）的开关；知识辅助只作用于
  仍被明确委托且无 active Realization 的白名单路径，不能覆盖用户指定值或 PIN。
- **没有自动重试承诺**：`VIA_HTTP_MAX_RETRIES` 只是 Provider 适配器在**单次调用内部**的传输
  重试；流程层不会自动重试生成、确认或反馈，是否 `retry` 完全由你按上面两条条件决定。
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
恢复、确认页不自动确认、`--demo` 入口，以及 v0.4 的 `--rag` / `--knowledge-dir`
参数默认与组合、非法远程路径、损坏语料、安全提示、RAG 关闭旧流程与演示 RAG 标识。

## 文档

- [架构设计](ARCHITECTURE.md)
- [版本任务书](task_books/README.md)
- [实施交接记录](handoffs/)
- [发布记录（预览）](releases/mvp_v0.3_preview.md)

项目使用 Python 3.12+、Pydantic、SQLite、HTTPX 与 pytest。
