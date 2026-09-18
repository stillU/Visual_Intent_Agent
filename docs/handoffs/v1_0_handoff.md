# v1.0 最小交付收敛交接（v1.0.0rc1 候选交付）

- 日期：2026-09-18
- 依据：[docs/task_books/v1.0/README.md](../task_books/v1.0/README.md)（Step 04）、
  [AGENT_DISPATCH_PROMPTS.md](../task_books/v1.0/AGENT_DISPATCH_PROMPTS.md)。
- 前置静态基线：[v0.6 静态检查记录](../task_books/v1.0/00_v0_6_static_review.md)。
- 候选交付说明：[docs/releases/v1.0.0rc1.md](../releases/v1.0.0rc1.md)。
- 基线：HEAD `0929b506e054d05f3acc558ed23f818f13599363`（`0929b50`），分支 `main`。
- 工作区：**dirty 且未提交**；未执行 commit / push / tag / stash。
- 方法：只读代码与文档，加上必要的元数据与文档编辑；**未运行测试、校验器、CLI、Fake 演练、
  安装、构建、哈希复算或任何真实服务**；**未读取 `.env` / `api.md`**。

## 0. 结论速览（必须分开陈述）

| 结论项 | 结果 |
|---|---|
| 版本一致性（Step 01） | 已整理：应用版本统一为 `1.0.0rc1`，CLI 版本展示引用包 `__version__`（**静态阅读结论**） |
| 配置与启动说明（Step 02） | 已整理：新增 `.env.example`，`docs/USAGE.md` 覆盖配置、源码运行与全部现有参数 |
| 文档整理（Step 03） | 已整理：根 README 精简并加交付状态；新增候选交付说明；更新任务索引；ARCHITECTURE 当前状态补充 |
| 静态收敛与交接（Step 04） | 已整理：本交接 + Step 04 状态同步；描述经静态核对未超出实现 |
| 测试 / 运行验证 | **未执行**（本轮范围外） |
| 真实评测 / 人工盲评 / RAG 收益结论 | **未执行、无结论**：v0.6 真实入口不可实际运行 |
| RAG 默认行为 | **未改**（默认关闭；默认目录仍为 `knowledge_base/v0.4`） |
| 已批准语料 / 冻结评测文件 / 用户数据库 | **未改动** |
| 真实调用 / 费用 / 凭据读写 / 提交推送 | **均无** |
| **v1.0 当前交付状态** | **v1 候选交付整理完成，未测试 / 未运行验证** |
| 正式发布身份 | 无：无 Git tag、无 pip 发行包、无容器镜像、无独立安装程序 |

## 1. 实际修改范围（完整文件清单）

本轮共 **9 个已跟踪文件被修改 + 3 个新增未跟踪文件**（`.env.example`、`docs/releases/v1.0.0rc1.md`、
本文件）。完整清单如下（改动行数取自 `git diff --numstat`；新增文件为未跟踪、未纳入 diff）：

| # | 文件 | 状态 | 改动规模 | 步骤 | 内容 |
|---|---|---|---|---|---|
| 1 | `pyproject.toml` | 修改 | +2 / −2 | 01 | `version = "1.0.0rc1"`；description 中过期的 “MVP v0.2” 改为候选版本表述 |
| 2 | `uv.lock` | 修改 | +1 / −1 | 01 | 仅本项目 `[[package]] visual-intent-agent` 的版本项；第三方锁项未动 |
| 3 | `visual_intent_agent/__init__.py` | 修改 | +2 / −2 | 01 | 包 docstring 与 `__version__ = "1.0.0rc1"` |
| 4 | `visual_intent_agent/cli.py` | 修改 | +13 / −8 | 01 | 新增 `PACKAGE_VERSION` 导入；`CLI_VERSION`、启动 banner、argparse description 与模块 docstring 引用包版本 |
| 5 | `.env.example` | **新增**（未跟踪） | 58 行 | 02 | 无凭据配置模板，只列 `ENV_VAR_NAMES` 的 9 个变量 |
| 6 | `docs/USAGE.md` | 修改 | +65 / −12 | 02 | 配置变量表、源码运行方式、全部 CLI 参数表、费用/RAG/PIN/重试边界 |
| 7 | `README.md` | 修改 | +11 / −1 | 03 | 新增“交付状态”一节；文档索引加入候选交付说明并标注 USAGE 内容 |
| 8 | `docs/releases/v1.0.0rc1.md` | **新增**（未跟踪） | 76 行 | 03 / 04 | 候选交付说明：范围、未验证、知识/DB 语义、v0.6 边界、许可、升级注意事项、发布身份 |
| 9 | `docs/task_books/README.md` | 修改 | +2 / −1 | 03 / 04 | v1.0 状态更新为 Step 01～04 已静态实施；链接候选交付说明与交接 |
| 10 | `docs/ARCHITECTURE.md` | 修改 | +5 / −0 | 03 / 04 | 仅在“当前状态”后追加候选交付整理段落，指向说明与交接 |
| 11 | `docs/task_books/v1.0/README.md` | 修改 | +2 / −0 | 04 | 任务书头部追加一行“实施进展（2026-09-18）”，避免与索引矛盾 |
| 12 | `docs/handoffs/v1_0_handoff.md` | **新增**（未跟踪） | 本文件 | 04 | 本交接 |

**本轮未改动的受保护范围**（`git status` 与 `git diff --name-only` 核验）：

- 语料与知识：`knowledge_base/v0.4/`、`knowledge_base/v0.5/`（含 manifest、JSONL、审核归档）全部未改。
- 评测：`evaluation/**`（含 `evaluation/v0_6/` 协议、样例、标注、`run_config.json` 与冻结清单）全部未改。
- 测试与工具：`tests/**`、`tools/**` 全部未改。
- 运行数据：`data/**`（含用户 SQLite 库）、`outputs/**` 全部未改、未读、未迁移。
- 其他文档：`docs/handoffs/` 既有报告、`docs/task_books/mvp_v0.2|v0.3|v0.4|v0.5|v0.6/`、
  `docs/releases/mvp_v0.3_preview.md`、`docs/mvp_v0.5/`、架构图产物等全部未改。
- 运行代码：除 `visual_intent_agent/__init__.py`（docstring + `__version__`）与
  `visual_intent_agent/cli.py`（版本展示字符串）外，`visual_intent_agent/**` 未改。
- 凭据：未读取或改写 `.env`、`api.md`。

## 2. 保持不变的合同

本轮唯一改动的运行时代码是 `visual_intent_agent/__init__.py`（docstring + `__version__`）与
`visual_intent_agent/cli.py`（版本展示字符串，改为引用包版本）；两者都不触及下列合同：

- **领域合同**：`VisualIntent` 的 Facet 与路径白名单、`IntentDelta` 操作（SET/CLEAR/PIN/UNPIN）、
  不可变模型与 `schema_version = "v1"` 语义未改。
- **数据库 Schema**：SQLite `PRAGMA user_version = 2`（当前 10 张表）与既有最小迁移逻辑未改；
  本轮**未执行任何迁移**。默认业务库仍为 `data/visual_intent.db`（`VIA_DB_PATH` 可覆盖）。
- **工作流与门禁**：逐轮单问题澄清、生成前硬确认、确认绑定展示的 revision 与摘要哈希、
  重要修改使旧确认失效、**不自动确认**、`PIN` 语义（不改值、只加保持标记）均未改。
- **Provider 与重试边界**：`http_max_retries` 仍是 Provider 适配器**单次调用内部**的传输重试；
  FAILED 重试仍复用同一已落库 Prompt；本轮**未新增**任何自动重试承诺。
- **知识/RAG 行为**：RAG 仍默认关闭；未指定时默认目录仍为 `knowledge_base/v0.4`；
  知识只辅助仍被明确委托的路径，不能覆盖用户指定值或 PIN。已批准快照
  `knowledge_base/v0.5/`（`v0.5-approved-1`，5 条 `approved`）字节未改。
- **两个入口分开**：产品入口是本地源码 CLI（`python -m visual_intent_agent`）；
  评测/校验入口是 `evaluation/**` 与 `tools/**`。日常 CLI **不包含** v0.6 评测层的预算守卫，
  也没有全局消费限额（预算只在 `evaluation/v0_6/budget.py`，服务评测入口）。
- **普通业务数据库与知识库职责分开**：SQLite Repository 保存会话/意图/确认/Prompt/生成/反馈/知识审计；
  知识库只是可选的候选建议来源，不替代业务库、不替用户作主。

## 3. 静态发现

1. **版本曾不一致**：改动前 `pyproject.toml` 与 `visual_intent_agent.__version__` 均为 `0.1.0`，
   而 CLI 展示的是 “MVP v0.4 engineering preview”。现统一为 `1.0.0rc1`，且 CLI 的
   `CLI_VERSION`、banner 与 argparse description 都由导入的包版本派生，不再多处硬编码。
2. **仓库没有 `AGENTS.md`**（也没有 `CLAUDE.md` / `.cursorrules` / `CONTRIBUTING.md`，全仓搜索无结果），
   因此本轮适用规则只来自 `docs/task_books/v1.0/` 任务书与派发指令。
3. **`.env.example` 原本不存在**；`.gitignore` 忽略 `.env` 与 `api.md`，但不忽略 `.env.example`，
   所以新增模板会以未跟踪状态出现。
4. **项目是非安装包**：`pyproject.toml` 无 `[build-system]` 且有 `[tool.uv] package = false`，
   `uv sync` 只装依赖。因此不能依赖 `importlib.metadata` 取版本，包 `__version__` 是唯一可用来源
   （与 `evaluation/direct_baseline.py`、`evaluation/runner.py` 既有写法一致）。
5. **日常 CLI 没有预算守卫**：`visual_intent_agent/` 内没有任何 budget 模块；预算逻辑只在
   `evaluation/v0_6/budget.py`。真实模式会调用真实 Provider，**可能产生费用**。
6. **v0.6 真实入口是双重 fail-closed**：`run_config.json` 的 `real_run_authorized = false` 且
   `authorization.status = "not_granted"`（gate G1）；`paired_runner.py` 的 `--provider real`
   工厂直接抛 `PairedRunBlockedError`，并且 `assert_real_run_allowed()` 即使在门禁项全过时
   也仍抛错（要求 Step 03 接入 G1 批准的工厂）。**只改授权布尔值无法运行真实评测。**
7. **知识快照现状**（只读核对）：`knowledge_base/v0.4/manifest.json` 为 `v0.4-draft-1`，
   9 条 `draft` / 0 `approved`（CLI 默认目录）；`knowledge_base/v0.5/manifest.json` 为
   `v0.5-approved-1`，5 条 `approved`。
8. **SQLite 备份细节**：`persistence` 只设置 `PRAGMA foreign_keys = ON`，**未显式启用 WAL**；
   因此通常不存在 `*.db-wal` / `*.db-shm`，但若环境或工具启用了 WAL，备份时应一并保存存在的旁文件。
9. **USAGE 参数表为静态推导**：`docs/USAGE.md` 的参数表由阅读 `cli.py` 的 `parse_args` 得出，
   **未用 `--help` 实际输出抓取核对**；其中 `--knowledge-dir` 在未开 `--rag` 时仍会做本地路径
   形态校验并提示不读取知识，`--demo --rag` 会拒绝外部 `--knowledge-dir`（`cli.demo_knowledge_conflict`）。
10. **根 README 在 Step 03 之前已无操作步骤**（上一提交已把操作说明迁入 `docs/USAGE.md`），
    本轮只新增“交付状态”与索引条目，未把任务步骤塞回 README。
11. **保留但已过时的历史陈述**（按要求不批量替换历史字样，切勿误读为当前状态）：
    `docs/releases/mvp_v0.3_preview.md` 仍写“`visual_intent_agent.__version__` 保持 `0.1.0`”；
    `docs/handoffs/v0_3_step_04_handoff.md` 与 `docs/handoffs/post_v0_3_fixes_001_handoff.md` 的
    `code_version` 仍含 `0.1.0`；`docs/task_books/v1.0/00_v0_6_static_review.md` 记录“应用版本仍 0.1.0”。
    这些是其写作时点的记录，**以本交接与 `docs/releases/v1.0.0rc1.md` 为准**。
12. **历史测试数字**：旧发布/交接记录中的通过数量（例如 `docs/releases/mvp_v0.3_preview.md` 内的数字）
    均属**前序报告记载，本轮未复跑**；本交接不把它们作为本轮证据，也不填写任何新数字。

## 4. 未执行项（本轮）

- 未运行 `pytest`、`tests/**` 任何用例，也未用任何方式执行项目代码做导入级验证。
- 未运行 `tools/` 下的校验脚本（如 `validate_v0_6_cases.py`、`run_v0_5_acceptance_cases.py`、
  `validate_knowledge_review_proposals.py`）。
- 未运行 CLI：`--help`、`--version`、`--demo`、`--rag`、`--knowledge-dir`、`--db`、`--output-dir` 均未执行。
- 未做 Fake 演练，未安装依赖（未 `uv sync`），未构建，未复算任何哈希，未运行 ruff 或其他静态工具。
- 未启动任何真实服务，未调用任何真实 Provider，**未产生费用**。
- 未读取或写入凭据（`.env`、`api.md` 未读），未执行数据库迁移。
- 未提交、未推送、未打 tag、未 stash。

因此：`docs/USAGE.md`、`docs/releases/v1.0.0rc1.md` 与本交接中所有“存在 / 默认值 / 未改动”的描述
都是**静态阅读结论**，不是运行证据。

## 5. 配置 / 数据 / 评测遗留

**配置**

- 真实模式的 Provider 凭据尚未提供、也未验证；`.env` 未被读取。
- `.env.example` 目前是**未跟踪**文件，是否纳入版本库由用户决定。
- RAG 默认目录仍是全 draft 的 `knowledge_base/v0.4`；使用已批准语料必须显式
  `--knowledge-dir knowledge_base/v0.5`。
- 日常 CLI 无预算守卫，真实调用可能收费；费用控制只能靠 Provider 侧自设限额。
- 未启用 WAL；升级/迁移前的备份策略见候选交付说明第 5 节，本记录不替用户执行备份或迁移。

**数据**

- 用户业务数据库（默认 `data/visual_intent.db`，被 `.gitignore` 忽略）未被读取、修改或迁移。
- COCO-CN 精确许可**仍待确认**，不纳入新增内容；v0.5 整体仍为**部分完成**（F3 未决），
  历史审核决定与结论保持原时间与原样。

**评测**

- v0.6 只交付到 Step 01/02；Step 03（执行与评审）与 Step 04（分析）**没有交接产物**，
  真实执行入口不可用。
- `real_run_authorized = false`、G1 未授权；**人工盲评、B/C 对照收益与知识质量结论全部缺失**。
- 不得通过只修改授权布尔值绕过门禁；正式评测需要单独的预算、模型身份与评审授权决定。

## 6. 当前交付状态

**v1 候选交付整理完成，未测试 / 未运行验证。**

- 该结论只表示版本、配置、文档与交接的**静态整理**已落地，可读可查。
- **不能**称“已验证稳定版”，**不能**称 v0.6 已完成，**不能**声称 RAG 更优或任何效果结论。
- 尚未提交、无 Git tag、无 pip 包 / 容器 / 独立安装程序，因此也**没有正式发布身份**。

## 7. 索引与旧报告保全

- 候选交付说明：[docs/releases/v1.0.0rc1.md](../releases/v1.0.0rc1.md)。
- 任务书与索引：[docs/task_books/v1.0/README.md](../task_books/v1.0/README.md)、
  [docs/task_books/README.md](../task_books/README.md)。
- 前置静态基线：[v0.6 静态检查记录](../task_books/v1.0/00_v0_6_static_review.md)。
- 使用说明：[docs/USAGE.md](../USAGE.md)；架构：[docs/ARCHITECTURE.md](../ARCHITECTURE.md)。
- 旧报告（`docs/handoffs/**`、`docs/releases/mvp_v0.3_preview.md`、`docs/mvp_v0.5/**`）**保留原时间与结论**，
  本交接不改写、不重跑其中的任何数字或裁定。

## 8. 后续独立任务（不在本轮范围，需用户决定）

1. 若需要稳定发布：先单独决定验证/评测范围，再执行测试与真实 smoke（本轮未做）。
2. 若要运行 v0.6 B/C 评测：先实现 Step 03 的真实 Provider 工厂，并按 `v0_6_launch_request.md`
   逐项确认授权；仅改布尔值无效。
3. 确认 COCO-CN 精确许可后再决定是否纳入内容。
4. 是否提交当前工作区改动、是否打 tag，由用户决定；本 Agent 不自动执行。
