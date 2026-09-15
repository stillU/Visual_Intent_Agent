# MVP v0.3 Step 02 交接记录：Direct LLM Baseline

任务：MVP v0.3 Step 02 —— 实现最小 Baseline A（单轮/多轮：用户文本 → LLM → 最终 Prompt →
同一 Image Provider），冻结多轮输入格式，产出可确定性重放的逐轮 Artifact 记录、
稳定关联 ID 方案与 Fake Provider 离线测试。不实现评测指标、Runner、报告；
不引入 VisualIntent / 澄清 / 确认 / IntentDelta / Realization Carry 等任何结构化能力。

- 依据：`docs/task_books/mvp_v0.3/README.md`、`docs/task_books/mvp_v0.3/02_direct_llm_baseline.md`、
  `evaluation/protocol.md`（Step 01 冻结）、`evaluation/configs/gate_a_v0_3.json`、
  `docs/handoffs/v0_3_step_01_handoff.md`、`docs/ARCHITECTURE.md` 第 3/4/6 节与
  `visual_intent_agent/providers/*`、`visual_intent_agent/config.py`。
- 范围纪律：**未修改** `visual_intent_agent/` 下任何文件、Step 01 任何冻结物
  （`protocol.md`、fixtures、annotations、configs、`frozen_manifest_v0_3.json`，sha256 复核一致）、
  `tests/` 下既有测试、`pyproject.toml`、`uv.lock`、`.env`；全部新增文件见下。

---

## 完成内容

### 1. `evaluation/models.py` — Baseline A 合同模型（pydantic v2）

全部记录模型 `frozen=True, extra="forbid"`，风格对齐仓库既有合同模型（tz-aware
`created_at`、`Literal` 状态枚举、`model_validator` 一致性校验、`__all__`）：

- 输入：`BaselineRunConfig`（config 的非密子集：版本/尺寸/张数）、`BaselineTurn`
  （`turn_id` / `kind` / `user_text` / 只读入不使用的 `answer_variants`）、`BaselineCase`；
- 快照：`BaselineProviderSnapshot`（**只记** LLM/图像模型名、两个超时、最大重试、
  尺寸、张数；`temperature_sent` / `max_tokens_sent` / `response_format_sent` 用
  `None` 字面量类型把"不显式设置"固化成合同；**无 base_url、无 key**）；
- 调用：`BaselineLLMCallRecord`（完整请求消息、原始输出、实际返回模型、provider_request_id、
  usage、耗时、重试次数、错误）、`BaselineImageCallRecord`（最终 Prompt、size、seed、
  provider_request_id、Artifact 列表、耗时、重试、错误）；
- Artifact：`BaselinePromptRecord`（`prompt_id` + 最终 Prompt + sha256）、
  `BaselineImageArtifactRecord`（`artifact_id`、相对输出目录路径、MIME、字节数、sha256）；
- 逐轮/逐案例/Run：`BaselineTurnRecord`（`skipped` 只用于 accept）、`BaselineCaseRecord`、
  `BaselineRunRecord`、`BaselineRunResult`。

稳定关联 ID 方案 `baseline_ids_sha256_v1`：全部 ID = `sha256(parts 以 "|" 连接)[:16]`，
前缀 `brun` / `bcase` / `bturn` / `bllm` / `bprompt` / `bimg` / `bart`，不含随机数与时钟；
`run → case → turn → LLM/Prompt/Image/Artifact` 层层可追溯。

### 2. `evaluation/direct_baseline.py` — 基线执行器

- 单轮链路：用户文本 → LLM（冻结系统提示，自由文本）→ 最终 Prompt（仅剥离首尾空白）→ Image Provider。
- 多轮链路：完整历史 + 前轮 Prompt 摘要 → LLM → 新 Prompt → Image Provider。
- 依赖注入：`llm: LLMProvider` / `image: ImageProvider` / `Settings` / `output_dir`
  全部构造参数注入；只 import `providers`（Protocol/错误分类）与 `config`，不 import
  任何具体 adapter、不读环境变量。
- 逐轮落盘（`output_dir` 内）：`run.json`、`cases/<case_id>/case.json`、
  `cases/<case_id>/turns/turn_<NN>.json`、`cases/<case_id>/images/turn_<NN>_image_<i>.<ext>`；
  JSON 用 `sort_keys=True` 固定序列化，保证同输入逐字节一致。
- Provider 失败：只 `except ProviderError`，按 `provider.*` code / retryable / status_code
  原样记录；不换模型、不换样本、基线层不重试（`retry_count` 恒 0）；LLM 返回空白文本记为
  `baseline.empty_llm_output`（`stage=baseline`），不调用图像 Provider；失败轮保留在记录与
  分母中，后续轮继续执行。
- `accept` 轮不执行：`skipped=True`、`skip_reason="accept_turn_not_executed"`，
  不进入后续历史。

### 3. 多轮输入格式冻结：`baseline_multiturn_v1`

1. 历史 = 本案例**此前已执行**的非 `accept` 轮，按数据集顺序；`accept` 轮不进入历史。
2. 每轮用户文本逐字作为一条 `role="user"` 消息（无任何前后缀）；当前轮为最后一条 user。
3. 该轮成功产生最终 Prompt 时，紧随一条 `role="assistant"` 摘要消息；LLM 失败/空白输出
   的轮次没有 Prompt，只保留用户消息（历史中不出现空摘要）。
4. 摘要构造完全规则化（无 LLM）：空白折叠为单空格 → 超过 240 字符按码位截断并追加 `…`。
5. 摘要模板：`[第 N 轮 Prompt 摘要] <摘要>`；截断时写成
   `[第 N 轮 Prompt 摘要（已截断）] <摘要>`。
6. 系统提示恒为第一条（模板唯一变量是目标图像模型名，版本 `baseline_prompt_v1`）。

版本号写入 Run 记录、每轮记录、Prompt 记录、图像调用记录与图像 Artifact 记录
（`multiturn_format_version` 字段）。

### 4. `evaluation/__init__.py` — 包初始化（任务书允许，仅 docstring + 缓存自愈）

新增原因：任务书允许"加入 .py 模块后如需包初始化文件可新增 `evaluation/__init__.py`
（仅 docstring，不改冻结数据文件）"。实现时发现一个必须处理的交互：Step 01 冻结的凭据卫生测试
`test_no_plaintext_keys_in_evaluation_tree` 会把 `evaluation/` 树下**所有文件**按 UTF-8
文本读取，一旦本包被 import 产生 `__pycache__/*.pyc`，该冻结测试即以 `UnicodeDecodeError`
失败（已实测复现）。本 `__init__.py` 因此在导入时关闭 `sys.dont_write_bytecode` 并清理本包
`__pycache__`，使 `evaluation/` 始终只含源码与冻结数据；该自愈对"已存在的陈旧 .pyc"同样有效
（实测：`compileall evaluation` 后运行 `uv run pytest tests/evaluation -q` 仍全绿且缓存被清除）。
未修改该冻结测试的任何一行。

### 5. `tests/evaluation/test_direct_baseline.py` — 42 条离线确定性测试

自足（无 conftest、不跨测试目录 import、只用 Fake Provider、输出写 `tmp_path`），覆盖：

- **依赖边界（静态 ast 扫描）**：`evaluation/models.py` 与 `direct_baseline.py` 未 import
  `domain`/`validation`/`policy`/`workflow`/`prompt_engine`/`realization`；产品包只 import
  `config` 与 `providers`；不 import 任何具体 adapter（`fake_llm`/`fake_image`/`openai_llm`/
  `openai_image`）；无 `import os` / `os.environ` / `getenv` / `dotenv` / `load_settings`；
- **同一公开接口**：Fake LLM/Image、自定义 Fake、真实 adapter 均满足 `LLMProvider`/`ImageProvider`
  Protocol（真实 adapter 只用 Mock client 做结构检查，不发请求）；
- 单轮全字段链路（消息面、参数恒 None、最终 Prompt、尺寸/模型、seed、Artifact 字节+sha256+路径）；
- 多轮历史格式（逐字用户文本、摘要模板、失败轮无摘要、accept 轮跳过且不入历史、摘要截断规则）；
- Provider 失败分类（auth/server/timeout/rate_limited）、空白输出、失败保留与不重试；
- 稳定关联 ID（前缀、关联闭包、唯一性、dataset 哈希驱动 run_id、nonce 敏感）；
- 确定性重放（注入固定时钟/计时器 → 两次运行**所有落盘文件逐字节一致**、内存记录相等）；
- 数据集驱动全量运行（22 案例、非 accept 轮数 = LLM 调用数 = 图像调用数、accept 全 skipped）；
- 落盘布局与 JSON 回读、run.json 无任何凭据字段、不写 `data/` 与 `outputs/`；
- 合同模型不变量（frozen/extra=forbid、拒绝 key 字段、拒绝非法采样参数、状态一致性）。

---

## 变更文件

新增（5 个文件，无修改任何既有文件）：

- `evaluation/models.py`
- `evaluation/direct_baseline.py`
- `evaluation/__init__.py`（仅 docstring + 字节码缓存自愈，任务书允许）
- `tests/evaluation/test_direct_baseline.py`
- `docs/handoffs/v0_3_step_02_handoff.md`（本文件）

## 公开接口

### `evaluation/models.py`

```text
BASELINE_SCHEMA_VERSION = "baseline_v1"

输入        BaselineRunConfig{config_version, image_size, images_per_generation}
            BaselineTurn{turn_id, kind, user_text, answer_variants|None}
            BaselineCase{case_id, dataset_version, scenario, turn_type, description, tags, turns}
快照        BaselineProviderSnapshot{llm_model, image_model, llm_timeout_seconds,
              image_timeout_seconds, http_max_retries, temperature_sent=None,
              max_tokens_sent=None, response_format_sent=None, image_size,
              images_per_generation}
调用        BaselineLLMCallRecord{call_id, run_id, case_id, turn_id, turn_index, call_index,
              messages[LLMMessage], model_requested=None, model_returned, temperature_sent=None,
              max_tokens_sent=None, response_format_sent=None, raw_output, provider_request_id,
              usage|None, latency_ms, retry_count, status: ok|failed, error|None}
            BaselineImageCallRecord{call_id, run_id, case_id, turn_id, turn_index, prompt_id,
              prompt_text, size, multiturn_format_version, model_requested, model_returned,
              provider_request_id, seed|None, images[BaselineImageArtifactRecord], latency_ms,
              retry_count, status, error|None}
Artifact    BaselinePromptRecord{prompt_id, run_id, case_id, turn_id, turn_index, text,
              text_sha256, source="llm_output", system_prompt_version, multiturn_format_version}
            BaselineImageArtifactRecord{artifact_id, run_id, case_id, turn_id, image_call_id,
              image_index, relative_path, mime_type, byte_size, sha256, multiturn_format_version}
逐轮/案例   BaselineTurnRecord{record_id, run_id, case_id, turn_id, turn_index, turn_kind,
              input_text, skipped, skip_reason, multiturn_format_version, history_user_turns,
              request_messages[LLMMessage], llm_call|None, prompt|None, image_call|None,
              status: completed|skipped|failed, error|None, created_at}
            BaselineCaseRecord{record_id, run_id, case_id, dataset_version, scenario, turn_type,
              turns[BaselineTurnRecord], status: completed|failed, created_at}
Run         BaselineRunRecord{run_id, baseline_version, baseline_prompt_version,
              multiturn_format_version, id_scheme_version, code_version, run_nonce,
              dataset_version, dataset_path, dataset_sha256, config_version, config_path,
              config_sha256, protocol_version, protocol_path, protocol_sha256, provider,
              system_prompt, system_prompt_template, case_count, case_ids, created_at}
            BaselineRunResult{run: BaselineRunRecord, cases: list[BaselineCaseRecord]}
错误        BaselineErrorRecord{code, message, retryable, status_code|None,
              provider_request_id|None, stage: llm|image|baseline}
```

### `evaluation/direct_baseline.py`

```text
版本常量    BASELINE_VERSION="direct_llm_baseline_v1"
            BASELINE_PROMPT_VERSION="baseline_prompt_v1"
            BASELINE_MULTITURN_FORMAT_VERSION="baseline_multiturn_v1"
            BASELINE_ID_SCHEME_VERSION="baseline_ids_sha256_v1"
            GATE_A_PROTOCOL_VERSION="gate_a_protocol_v0_3"
            PROMPT_SUMMARY_MAX_CHARS=240; SKIP_REASON_ACCEPT="accept_turn_not_executed"
            BASELINE_EMPTY_LLM_OUTPUT="baseline.empty_llm_output"

纯函数      sha256_bytes/sha256_text/sha256_file(data|text|path) -> str
            digest(*parts) -> str; make_record_id(prefix, *parts) -> str
            build_run_id(*, code_version, dataset_version, dataset_sha256, config_version,
                         config_sha256, protocol_version=GATE_A_PROTOCOL_VERSION,
                         run_nonce="") -> str
            default_code_version() -> str
            render_system_prompt(image_model) -> str
            collapse_whitespace(text) -> str
            summarize_prompt(prompt, *, max_chars=240) -> tuple[str, bool]
            format_history_summary(turn_index, prompt) -> str
            build_request_messages(system_prompt, history: Sequence[BaselineHistoryEntry],
                                   current_user_text) -> list[LLMMessage]
            load_run_config(path=DEFAULT_CONFIG_PATH) -> BaselineRunConfig
            load_dataset(path=DEFAULT_DATASET_PATH) -> list[BaselineCase]

执行器      class DirectBaselineRunner:
                def __init__(self, *, llm: LLMProvider, image: ImageProvider,
                             settings: Settings, output_dir: Path,
                             config_path=DEFAULT_CONFIG_PATH,
                             dataset_path=DEFAULT_DATASET_PATH,
                             protocol_path=DEFAULT_PROTOCOL_PATH,
                             code_version: str | None = None, run_nonce: str = "",
                             clock: Callable[[], datetime] | None = None,
                             monotonic: Callable[[], float] | None = None) -> None
                def run_dataset(self) -> BaselineRunResult
                def run_cases(self, cases: Iterable[BaselineCase]) -> BaselineRunResult
                def run_case(self, case: BaselineCase) -> BaselineCaseRecord
                # 只读属性：run_id / output_dir / run_config / system_prompt /
                #           code_version / dataset_sha256
```

（基线不新增产品 Issue code；`baseline.empty_llm_output` 只在评测层使用。）

## 测试与实验结果

```text
$ uv run pytest tests/evaluation -q
84 passed in 0.24s          # Step 01 冻结 42 条 + 本步 42 条

$ uv run pytest -q
1927 passed, 3 deselected in 4.37s   # 基线 1885 + 本步 42；既有测试零回归
```

补充实测（非测试命令）：

- 冻结物 sha256 与 `evaluation/frozen_manifest_v0_3.json` 及 Step 01 内嵌常量逐条一致；
- `uv run python -m compileall evaluation` 制造陈旧 `.pyc` 后，`uv run pytest tests/evaluation -q`
  仍全绿，且运行后 `evaluation/` 无任何 `.pyc` 残留；
- Fake 下全量 22 案例 run 产物落 `tmp_path`，未触碰 `data/`、`outputs/`。

## 原始 Artifact 位置

- 本步不产生真实模型 Artifact（无 A/B 运行、无真实图片）。
- 测试运行产物均在 pytest `tmp_path`；真实运行产物布局为
  `evaluation/runs/<run_id>/baseline/{run.json,cases/<case_id>/...}`（由 Step 03 决定根目录）。

## 已知限制

1. **`retry_count` 只覆盖基线层（恒 0）**：adapter 内部冻结重试不通过 `LLMProvider` /
   `ImageProvider` Protocol 暴露，无法观测；记录保持诚实（不虚构），协议第 7 节的"重试次数"
   目前只能表达为"基线未重试"。建议 Step 03 在报告中把该字段口径写清，或后续经架构修订让
   adapter 返回重试计数。
2. **`evaluation/` 字节码缓存**：Step 01 冻结的凭据扫描不容忍 `.pyc`；本包 `__init__` 自愈只
   在**通过包导入** `evaluation.*` 时触发。Step 03 必须保持 `import evaluation.xxx` 的导入方式
   （不要用 `importlib` 直接按文件路径加载绕过包），不要在 `evaluation/` 内运行 `compileall`，
   也不要把运行产物 `.pyc` 落进该目录。单独运行 `pytest tests/evaluation/test_dataset_contract.py`
   且目录内已存在陈旧 `.pyc` 时仍会失败——属于 Step 01 扫描口径的已知边界，不是本步引入。
3. **`run_id` 不含重复序次**：协议第 7 节 L1~L3 每案例 2 次；Step 03 需为每次重复传
   `run_nonce`（如 `"rep1"`/`"rep2"`）以区分 run_id，或按 rep 分目录并自行登记。
4. **摘要截断是冻结启发式**：240 字符、按码位截断、不切词；这是确定性设计（可复现优先），
   不代表语义最优。修改摘要规则必须升 `baseline_multiturn_v1` 版本号。
5. **`images_per_generation` 记录但不强制**：runner 落盘 Provider 实际返回的全部图片（保持顺序、
   逐张 sha256），不因数量不等于 1 而失败或丢图；协议期望值为 1，非 1 属可归因异常数据。
6. **未实现任何指标/报告/盲评**：L1/L2 对 Baseline A 应记 `not_applicable`（协议第 3 节），
   本步只产出可被 Step 03 聚合的原始 Turn/Artifact 记录。
7. **真实 Provider 只在测试中做 Protocol 结构检查**：默认测试全离线；真实调用路径的 smoke
   仍属 `tests/smoke/`（本步未新增，也不在验收条件内）。

## 对下一步的输入

- **Step 03（Runner 与 L1~L3）**：
  - 每次重复构造一个 `DirectBaselineRunner`（`run_nonce` 区分；`output_dir` 建议
    `evaluation/runs/<run_id>/baseline/`）。需要先知道 run_id 时，用本模块公开的
    `build_run_id(...)` + `sha256_file(...)` + `default_code_version()` 预计算（与执行器内部
    算法一致），或直接读执行器 `runner.run_id` 后再决定最终目录。
  - `run_dataset()` 返回 `BaselineRunResult`（`run` 元数据 + `cases` 记录）；`accept` 轮为
    `status="skipped"`、`skip_reason="accept_turn_not_executed"`，**不得**记成 `missing_data`。
  - 失败轮为 `status="failed"` + `error.code`（`provider.*` 或 `baseline.empty_llm_output`），
    保留在分母；图像失败轮仍有 `prompt`、无 Artifact。
  - L3 关联：`BaselineTurnRecord.prompt.text`（Prompt 文本）；L4 图片：从
    `image_call.images[].relative_path` 还原到 `output_dir` 下取字节，`sha256` 可校验。
  - L1/L2 对 Baseline A 一律 `not_applicable`（协议第 3 节）。
  - **导入 `evaluation.*` 前请确保走包导入**（见已知限制 2）。
- **Step 04（图片 A/B 盲评）**：Baseline 图片落盘文件名含 `turn_NN_image_i`，去标签时需改为
  随机化呈现名；不要改动 `run.json` / `case.json` / `turn_*.json` 原始记录。
- **Step 05/06/07（结论与复评）**：Baseline 口径不可在评测中途修改；任何格式/提示变更必须
  升版本号（`BASELINE_PROMPT_VERSION` / `BASELINE_MULTITURN_FORMAT_VERSION`）并生成新 Run。

## 关键设计决定（≤8 条）

1. **ID 全哈希化、无随机数与时钟**：`baseline_ids_sha256_v1` 让重放产生完全相同的
   run/case/turn/call/artifact ID，便于 Step 03 逐轮对齐与 Step 04 图片配对。
2. **时间与计时器依赖注入**：`clock` / `monotonic` 可注入，测试可实现"所有落盘文件逐字节一致"
   的强重放断言；真实运行用系统时钟。
3. **用 `None` 字面量类型固化"不设采样参数"**：`temperature_sent` / `max_tokens_sent` /
   `response_format_sent` 在合同层只能为 `None`，把协议第 1 节的冻结条件变成不可违反的类型约束。
4. **多轮历史用标准 chat 消息而非单条拼接文本**：用户文本逐字为 `user` 消息、前轮 Prompt 摘要为
   `assistant` 消息；轮次边界天然清晰、无分隔符歧义，且用户文本零改写。
5. **摘要规则化、可版本化**：折叠空白 + 240 字符截断 + 固定模板，LLM 完全不参与摘要；
   版本号 `baseline_multiturn_v1` 写入每条记录。
6. **失败即记录、不重试不换模型**：只捕获 `ProviderError`，把 code/retryable/status_code/
   provider_request_id 原样落盘；`baseline.empty_llm_output` 显式区分"Provider 成功但输出不可用"。
7. **只用 Protocol、绝不 import 具体 adapter**：执行器与 Fake/真实 adapter 解耦，依赖边界测试
   静态强制；真实与 Fake 走同一 `complete` / `generate` 公开接口。
8. **`evaluation/__init__.py` 做字节码缓存自愈**：在不改动 Step 01 冻结测试的前提下，让
   `evaluation/` 目录始终只含源码与冻结数据（凭据扫描按 UTF-8 读取全部文件）。

## 是否满足验收条件

**是。** 任务书验收条件逐条核对：

| # | 条件 | 核对 |
|---|---|---|
| 1 | Fake Provider 下可确定性重放单轮和多轮 | 注入固定时钟/计时器后两次运行所有落盘文件逐字节一致、内存记录相等（`TestDeterministicReplay`） |
| 2 | 真实与 Fake 路径使用同一公开接口 | 执行器只依赖 `LLMProvider`/`ImageProvider` Protocol；Fake、自定义 Fake、真实 adapter 均通过 `isinstance` 结构检查（`TestDependencyBoundary`） |
| 3 | 所有输入、Prompt 和图片结果均有稳定关联 ID | `brun/bcase/bturn/bllm/bprompt/bimg/bart` 哈希 ID 全链闭包 + 唯一性测试（`TestStableAssociationIds`） |
| 4 | 未 import `domain`/`validation`/`policy`/`workflow`/`prompt_engine`/`realization` | ast 静态扫描双文件 + 仅允许 `config`/`providers`（`TestDependencyBoundary`）；另禁止具体 adapter 与环境变量访问 |
| 5 | `uv run pytest tests/evaluation -q` 全绿（含 Step 01 的 42 条） | 84 passed（42 + 42 新增） |
| 6 | 全量测试保持全绿 | `uv run pytest -q` → 1927 passed, 3 deselected（1885 + 42） |
