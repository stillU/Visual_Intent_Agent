# v0.4 Step 04 交接：CLI 开关、展示与工程验收

- 日期：2026-09-16
- 范围：**只完成 v0.4 Step 04 的 CLI 面**（`--rag` / `--knowledge-dir` 参数与装配、
  确认前/生成后展示、`tests/cli`、README 最小用法与真实状态、本交接）。
- 工作区基线：HEAD `b039b9b`（v0.3 后置修复之后）；工作区包含 Step 01～03 及其它
  并行 Agent 的未提交修改，全部**原样保留**，未回滚、未覆盖、未 commit/push。
- 只修改：`visual_intent_agent/cli.py`、`README.md`、`tests/cli/`、本交接。
  **未修改** knowledge 核心、PromptEngine、Repository、Realization 模型、生产语料。

## 任务：Step 04

按 `docs/task_books/mvp_v0.4/04_cli_acceptance.md`：复用现有 CLI 增加 `--rag` 显式
开启（默认关闭）与 `--knowledge-dir` 本地目录；不读取远程链接、不自动下载；装配时
注入 `KnowledgeEngine`，路径/格式错误安全可见且不伪装启用成功；`--demo --rag` 用明确
标注的演示/测试 approved 数据；确认前展示“知识仅辅助明确委托项”且不改哈希；生成后
显示采用/回退/复用、路径与 Bundle/知识来源 ID；补齐离线 CLI 测试。

## 完成内容

### 1. CLI 参数（`visual_intent_agent/cli.py`）

- `--rag`：`store_true`，默认 `False`；不开启时**完全不读取知识、不检索、不生成
  Bundle**，旧流程逐字兼容。
- `--knowledge-dir`：本地目录；默认 `DEFAULT_KNOWLEDGE_DIR = knowledge_base/v0.4`。
  提供该参数时**先做形态校验**：任何 `scheme://`（http/https/ftp/sftp/s3/gs/ssh 等）
  或已知远程 scheme 前缀一律拒绝（`cli.knowledge_dir_remote`），不联网、不下载。
  只给 `--knowledge-dir` 而未开 `--rag` 时打印“未读取任何知识目录”的提示，但不读取。
- `--demo` / `--db` / `--output-dir` / `--version` 保持兼容；`--version` 更新为 v0.4
  工程预览标识。

### 2. 装配与错误语义

- 新增 `build_knowledge_engine(knowledge_dir=..., demo=...)`：
  - 真实/本地目录：**启动时**完整 `load_corpus` 校验。不存在/畸形 manifest/哈希/版本/
    合同错误立即抛出 `KnowledgeActivationError` / `KnowledgeError`，绝不退化为无 RAG。
  - 返回只读 `KnowledgeSourceInfo`（来源标签、语料版本、全部/approved 单元数、是否 demo）。
- 新增 `build_prompt_engine(repo, knowledge_engine=...)`：仅当 RAG 开启且拿到引擎时注入
  `PromptEngine(..., knowledge_engine=...)`（keyword-only，Step 03 合同）。若当前
  `PromptEngine` 不支持注入，抛 `cli.rag_unsupported` **明确失败**，不以无 RAG 继续。
- `build_app` / `build_demo_app` 增加可选 keyword-only `rag=False, knowledge_dir=None`；
  默认行为与旧调用逐字兼容。`SessionApp._assemble` / `from_settings` 同样只加可选参数。
- `main` 对知识/RAG 错误统一 `_render_knowledge_error`：稳定 code + “--rag 未生效，本次
  不会以无 RAG 静默继续” + 退出码 2。
- `_print_rag_status` 打印真实来源、语料版本、approved 计数；`approved == 0` 时明确提示
  “将按无命中透明回退，不会采用任何知识”。

### 3. `--demo --rag` 的演示/测试语料

- 新增 `build_demo_knowledge_corpus()`：CLI **内存内置** 3 条 approved 夹具（每知识路径
  1 条），带 `source_revision=v0.4-demo-1`、原创声明，`reviewer` 明确标注
  `cli-demo-fixture-not-a-human-review`。语料指纹由与真实 JSONL 同构的序列化内容 sha256
  计算，确定性、可复现。
- `--demo --rag` **拒绝**同时指定 `--knowledge-dir`（`cli.demo_knowledge_conflict`），避免
  拿未标注的外部目录冒充演示 approved 数据；界面明确打印“内置演示/测试 approved 夹具
  不是生产人工审核结果，也不代表真实检索或图片效果”。
- 演示夹具**不修改** `knowledge_base/v0.4/` 的 9 条生产 draft。

### 4. 展示（只读、薄接线、不改哈希）

- `render_summary(..., rag_enabled=...)`：RAG 开启时在摘要哈希之后追加一行
  “知识说明: 知识仅辅助明确委托项；……”。**确认哈希算法与摘要内容不变**，用户确认的仍是
  已展示的 Intent 与委托范围。
- `SessionApp.knowledge_report(session_id, *, prompt_artifact_id=None)`：只读解析最新
  `PromptArtifact` → `knowledge_bundle_refs` → Repository 中的 `KnowledgeBundle`，并读取
  当前 active Realization；按 `first_prompt_artifact_id == 当前 prompt` 区分“本次采用”与
  “历史复用”。RAG 关闭时返回 `None`（不读取任何知识记录）。缺失 Bundle 引用如实跳过。
- `render_knowledge_report(console, report)`：显示采用 / 回退 / 复用、路径、`knowledge_unit_id`
  + 版本、`bundle_id`、语料版本与指纹（截断）、推荐候选与分数；固定声明“不代表用户确认过
  任何知识取值”。`render_generation(..., knowledge_report=...)` 在采用/复用/重试三处接入。

### 5. 测试与文档

- 新增 `tests/cli/test_cli_rag.py`（19 项）：参数默认/组合、`--knowledge-dir` 默认目录、
  5 类远程 scheme 拒绝、无凭据下远程路径拒绝、未开 RAG 不读目录、缺失目录、损坏 manifest、
  真实模式损坏语料退出码 2、`--demo --rag --knowledge-dir` 冲突、RAG 关闭旧流程无知识块、
  `build_app` 默认 RAG 关闭、`--demo --rag` 演示标识与采用/来源展示、生产 draft 语料
  approved 0 透明回退、纯回退渲染不冒充用户确认。
- README 增加 v0.4 最小用法与**真实状态**：默认关闭、远程拒绝、不伪装成功、确认哈希不变、
  采用/回退/复用展示、演示语料标注、生产 9 条 draft 0 approved、三质量结论分离。

## 变更文件

- 修改：`visual_intent_agent/cli.py`、`README.md`
- 新增：`tests/cli/test_cli_rag.py`、`docs/handoffs/v0_4_step_04_handoff.md`

未触碰：`visual_intent_agent/knowledge/**`、`prompt_engine/**`、`persistence/**`、
`realization/models.py`、`knowledge_base/**`、其它 `tests/**`、既有交接/冻结物。未 commit/push。

## 测试命令与结果

```bash
uv run pytest tests/cli -q
# 33 passed（含既有 14 项 + 新增 19 项）

uv run pytest -q
# 2500 passed, 3 deselected（最终 Step 01～04 合并态；离线、未选 smoke）

git diff --check
# 无输出（通过）
```

同时人工（非测试断言）跑通 `--demo --rag` 完整离线闭环：确认前显示安全提示，
生成后显示 `本次采用 (adopted): lighting.character = 'soft' ← demo.lighting.character.soft`、
`Bundle / 来源 ID: kbu_... status=ok corpus=v0.4-demo-1 fingerprint=...`，并声明不代表用户
确认知识值。生产语料 `--rag`（无 `--knowledge-dir`）加载成功但 approved 0，界面提示透明回退。

未运行：真实 Provider、图片批次、正式评测、`smoke`。未声明知识已人工审核或 RAG 有收益。

## 公开接口（CLI 增量，向后兼容）

```python
from visual_intent_agent.cli import (
    DEFAULT_KNOWLEDGE_DIR, CLI_KNOWLEDGE_DIR_REMOTE, CLI_KNOWLEDGE_DIR_INVALID,
    CLI_KNOWLEDGE_RAG_UNSUPPORTED, CLI_KNOWLEDGE_DEMO_CONFLICT,
    KnowledgeActivationError,        # .code / .message
    KnowledgeSourceInfo,             # label/path/corpus_version/all_unit_count/approved_unit_count/is_demo
    KnowledgeReport,                 # prompt_artifact_id/source/bundles/adopted/reused/fallback_notes/note
    resolve_knowledge_dir, build_knowledge_engine, build_demo_knowledge_corpus,
    build_prompt_engine, render_knowledge_report,
)
# 兼容扩展（旧调用不变）：
build_app(settings, *, db_path=None, output_dir=None, rag=False, knowledge_dir=None)
build_demo_app(*, db_path=None, output_dir=None, rag=False, knowledge_dir=None)
SessionApp.from_settings(..., knowledge_engine=None, knowledge_source=None)
render_summary(console, summary, *, rag_enabled=False)
render_generation(console, artifact, *, knowledge_report=None)
```

## 已知限制与未完成项

1. **生产知识库仍未人工审核**：`knowledge_base/v0.4/` 9 条全 draft、0 approved；默认
   `--rag` 在本机只会透明回退，不采用任何知识。演示夹具的 `reviewer` 明确标注非人工审核。
2. **全量回归已跑但非本步独占**：当前工作区还含其它 Agent 的未提交修改，`2500 passed`
   只代表这一合并态，不等于正式可复现版本；正式评测前仍需固化干净版本。
3. **7 项 Fake 验收由跨模块测试共同覆盖**：Step 03 的 PromptEngine 注入在本步进行期间
   已合并，`--demo --rag` 的采用链路已实测。跨 Step 组合行为由
   `tests/prompt_engine/test_prompt_engine_knowledge.py`、既有反馈/carry 测试以及
   `tests/generation/test_generation_knowledge_retry.py` 共同覆盖，本步不复制其断言：
   - active 复用零新检索与历史 Bundle 引用：
     `test_active_reuse_does_zero_retrieval_and_keeps_historical_refs`；
   - 图片 Provider 首次失败后 retry 复用原 Prompt、知识查询数不增加：
     `test_retry_reuses_the_original_prompt_without_extra_knowledge_retrieval`；
   - 跨 revision carry 保留知识来源（版本更换后新选择产生新来源）：
     `test_carry_preserves_knowledge_provenance`；
   - RAG 确实改变新授权路径的选值与 Prompt、默认 None/无命中与旧流程等价：
     `test_rag_changes_the_selected_value_and_the_prompt` /
     `test_default_none_and_no_hit_are_equivalent_to_legacy`。
   本步**未伪造**这些通过，也未把它们写入 `tests/cli` 作为已通过断言。
4. **`--rag` 与真实模式联调未跑真实 Provider**：仅构造 Provider 后立即 `exit` 验证装配与
   状态打印，未执行任何图片/LLM 调用。
5. **CLI 不做知识内容判定**：只读展示 Bundle 与 Realization 追溯字段；采用/回退的真实
   判定在 `PromptEngine`（Step 03），CLI 不复制该规则。

## 对下一步的输入

- 集中验收：01～04 合并后运行 `uv run pytest -q` 与 `git diff --check`；若 Step 03 接口有
  变动，本步 `_knowledge_engine_kwarg("knowledge_engine"/"knowledge")` 探测需同步核对。
- README 只保留最小用法与真实状态；详细计划留在 `docs/task_books/mvp_v0.4/`。
- 正式 B/C 对照必须用独立初始会话，不能对同一会话切 `--rag` 假装控制变量相同（任务 03/05）。
- 未经额外评测启动指令，不自动花费图片/API 预算。

## 是否满足验收条件：CLI 面满足；知识审核与正式评测未完成

Step 04 的 CLI 开关、装配、错误可见性、确认前/生成后展示、演示标识与 `tests/cli`
局部测试已完成并通过，当前合并态全量回归 `2499 passed`、`git diff --check` 干净。
但生产知识库 9 条仍全为 draft、0 approved，7 项完整跨 Step Fake E2E 由集中验收覆盖，
本轮**未**声称知识已审核、RAG 优于无 RAG 或正式发布成功。