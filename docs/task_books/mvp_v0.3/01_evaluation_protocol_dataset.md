# Step 01：评测协议与数据集冻结

## 目标

建立 Gate A 的唯一评测口径。在任何 A/B 运行前冻结输入、人工标注、模型条件和结果记录方式，防止后续根据结果调整题目或评分规则。

## 输入

- MVP v0.2 Step 10 任务书。
- `tests/fixtures/multiturn/` 的既有多轮案例。
- v0.2 已冻结的 VisualIntent、IntentDelta、DecisionPolicy 与 Artifact 合同。

## 实施内容

1. 建立 `evaluation/protocol.md`，明确 Baseline A、System B、四层指标和 Go 条件。
2. 建立版本化 Core Dataset，覆盖：
   - 完整明确的单轮请求；
   - 缺失 Core / Perceptual Decision；
   - 冲突、明确委托和模糊委托；
   - 单字段修改、PIN / UNPIN；
   - 连续 3～5 轮修改；
   - 对图片的模糊反馈。
3. 为每个案例记录稳定 ID、输入轮次、预期 Delta、必须澄清项、禁止变化路径和图片评价维度。
4. 冻结 LLM/Image Provider、模型名、参数、超时、随机性记录和失败重试口径。
5. 明确哪些指标自动计算、哪些需要人工盲评，以及缺失数据如何报告。
6. 生成数据集校验测试，保证 ID 唯一、字段完整、路径合法且多轮引用闭合。

## 交付物

- `evaluation/protocol.md`
- `evaluation/fixtures/core_v0_3.jsonl`
- `evaluation/annotations/core_v0_3.jsonl`
- `evaluation/configs/gate_a_v0_3.json`
- `tests/evaluation/test_dataset_contract.py`
- `docs/handoffs/v0_3_step_01_handoff.md`

## 验收条件

- 覆盖全部规定场景，单轮和多轮均有案例。
- 标注不依赖待评系统的实际输出。
- Provider 条件和评分口径可被后续 Agent 直接读取。
- 数据校验测试离线通过。
- 数据集冻结后以内容哈希标识；后续任何修改必须生成新版本，不得覆盖。

## 禁止范围

- 不实现 Direct LLM baseline。
- 不运行正式 A/B。
- 不修改产品规则或 v0.2 fixture。
- 不预先填写有利于 System B 的结果。
