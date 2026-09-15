# MVP v0.3 Agent 派发提示词

Step 06 patch 002 后使用 [更改书 002](REVISION_002_BUILD_FIRST.md) 的 A/B/C/D 文件级并行分工，具体修订依据见 [更改书 001](REVISION_001_SHORTEST_PATH.md)。优先实现、不运行真实付费评测；每个工包落地代码、局部离线测试和遗留清单。CLI 先交付工程预览版，不宣称 Gate Go。以下为历史提示词，与 002 冲突时以 002 为准。

## 通用要求

```text
你正在执行 Visual Intent Agent MVP v0.3 的一个冻结步骤。开始前阅读本步骤任务书、mvp_v0.3/README.md、docs/ARCHITECTURE.md 和相关上游交接记录。只完成本步骤范围；保留原始实验数据和失败案例；不得为提高结果临时更改数据集、规则或评分口径；不得覆盖 v0.2 任务书。完成后按 v0.3 统一交接格式提交记录。
```

## Step 01

```text
执行 MVP v0.3 Step 01：冻结 Gate A 评测协议、Core Dataset、人工标注答案和实验配置。只定义评测合同与数据，不实现 baseline、runner 或产品功能。
```

## Step 02

```text
执行 MVP v0.3 Step 02：实现最小 Direct LLM Baseline。复用与 System B 相同的 LLM/Image Provider 配置，保留原始 Prompt 和生成 Artifact，不引入结构化 Intent 能力。
```

## Step 03

```text
执行 MVP v0.3 Step 03：实现可重放的 A/B Runner、统一结果格式以及 L1～L3 自动指标。评测代码不得修改产品规则，不在本步进行图片主观评分。
```

## Step 04

```text
执行 MVP v0.3 Step 04：在冻结条件下运行真实图片 A/B，生成去系统标签的盲评材料并保存原始评分。不得挑选成功案例或用 Prompt 分数代替图片结果。
```

## Step 05

```text
执行 MVP v0.3 Step 05：汇总四层指标、失败案例和实验限制，给出 Gate A 首轮 Go、Conditional Go 或 No-Go 结论。不得修改代码或通过新增功能影响结论。
```

## Step 06

```text
仅当 Step 05 有明确失败证据时执行 MVP v0.3 Step 06：按失败归因实施最小修复并增加回归测试。不得修改冻结评测集、基线或评分规则，不顺带加入新架构。
```

## Step 07

```text
执行 MVP v0.3 Step 07：在完全相同的冻结协议下复评修正后的系统，分别报告首轮与复评结果并给出最终 Gate A 结论。禁止只报告改善样本。
```

## Step 08

```text
仅当 Gate A 最终为 Go 时执行 MVP v0.3 Step 08：提供一个最小 CLI，串联已有完整工作流并完成版本验收。CLI 只做接线和可用性处理，不复制领域规则、不绕过确认、不引入 Web UI。
```
