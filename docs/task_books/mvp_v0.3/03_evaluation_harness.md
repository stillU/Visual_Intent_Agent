# Step 03：自动评测框架与 L1～L3 指标

## 目标

建立可重放的 A/B Runner，以相同数据和 Provider 条件运行 Baseline A 与 System B，并生成独立的 L1～L3 指标和逐案例记录。

## 前置依赖

- Step 01 评测协议已冻结。
- Step 02 Baseline A 已通过验收。
- v0.2 System B 全链路与 Fake Provider 测试保持通过。

## 实施内容

1. 定义统一 Run、Turn、Metric、Failure 和 Artifact 引用合同。
2. Runner 读取冻结数据集和配置，记录代码版本、数据哈希、Provider 配置与随机性信息。
3. 对 A/B 执行相同案例；不适用于 Baseline 的结构化指标必须标为 `not_applicable`，不能伪装成零分。
4. 实现 L1 State Correctness：未授权变化、旧确认复用、PIN 覆盖、历史覆盖等核心不变量。
5. 实现 L2 Intent Understanding：Delta Accuracy、Missing Decision Recall、Clarification Precision、Conflict Detection、Delegation Scope Accuracy。
6. 实现 L3 Prompt Semantics：Intent Coverage、Unauthorized Addition、Preservation、Model Compatibility。
7. 输出逐案例 JSONL 与聚合摘要；聚合结果必须能够追溯到原始 Turn 和 Artifact。
8. 用 Fake Provider 建立确定性回归测试，再提供显式命令运行真实 LLM 层评测。

## 交付物

- `evaluation/runner.py`
- `evaluation/metrics/state.py`
- `evaluation/metrics/intent.py`
- `evaluation/metrics/prompt.py`
- `evaluation/reporting.py`
- `tests/evaluation/` 对应测试
- `docs/handoffs/v0_3_step_03_handoff.md`

## 验收条件

- 同一配置可重放并产生结构一致的结果。
- L1 核心不变量任一失败会在摘要中显式标记 Gate A 阻断。
- 单轮与多轮指标分开保留，并可继续聚合。
- 失败、超时和缺失结果不从分母中静默删除。
- 默认测试离线运行，不触发真实 Provider。

## 禁止范围

- 不实现图片主观评分。
- 不根据运行结果修改产品规则或冻结标注。
- 不用 L3 Prompt 分数代替 L4 图片结果。
