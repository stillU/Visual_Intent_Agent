# Step 10：Gate A 评测与核心 MVP 验收

## 派发给 Agent 的任务

停止开发新能力，建立 Direct LLM baseline，与完成 P3 的 Visual Intent Agent 做同条件对比，给出 Gate A 的 Go / No-Go 结论。

## 前置依赖

- Step 09 已验收；
- P0～P3 的代码、测试和 Artifact 链路完整；
- 已固定唯一 LLM 和图像模型 Provider。

## Gate A 要回答的问题

> 结构化 Intent 是否在真实生成结果和多轮修改中，明显优于直接 LLM？

## 对比系统

### Baseline A

```text
User
→ 同一 LLM
→ Prompt
→ 同一 Image Model
→ Image
```

多轮时：

```text
历史对话 → LLM → 新 Prompt → Image
```

### System B

```text
User
→ Intent
→ Clarification
→ Confirmation
→ Prompt
→ 同一 Image Model
→ Image
```

多轮时：

```text
IntentDelta
→ Realization Carry
→ New Prompt
→ Image
```

除 Intent 系统本身外，模型版本、输出参数、测试输入和评价方式必须尽量一致。

## 四层评测

### Layer 1：State Correctness

目标 100%：

- 未授权字段不能改变；
- 旧确认不能复用；
- pinned 内容不能被覆盖；
- 历史不能被修改。

任一核心不变量失败，Gate A 直接判定 No-Go，先修复 P0～P3。

### Layer 2：Intent Understanding

测量：

- Intent Delta Accuracy；
- Missing Decision Recall；
- Clarification Precision；
- Conflict Detection；
- Delegation Scope Accuracy。

### Layer 3：Prompt Semantics

测量：

- Intent Coverage；
- Unauthorized Addition；
- Preservation；
- Model Compatibility。

### Layer 4：Image Result

测量：

- Intent Alignment；
- Attribute Preservation；
- User Preference；
- Edit Success。

四层必须分开报告，禁止用 State Correctness 代替图像一致性。

## 核心比较指标

- Silent Decision；
- Missing Decision；
- Clarification Cost；
- Multi-turn Attribute Drift；
- Edit Success；
- Intent Alignment；
- User Preference。

## 评测数据集

使用能够覆盖以下情况的 Core MVP Evaluation Dataset：

- 完整、明确的单轮需求；
- 缺失 Core Decision 的需求；
- 缺失 Perceptual Decision 的需求；
- 相互冲突的需求；
- 明确 delegated 的需求；
- 模糊委托范围；
- 只修改一个字段；
- PIN / UNPIN；
- 连续 3～5 轮修改；
- 对图片的模糊反馈。

数据集规模先以能够稳定复现问题为准，不在本步骤建设大规模 benchmark 平台。

## 执行步骤

1. 冻结测试集和人工标注答案。
2. 固定模型版本、参数和随机性记录方式。
3. 实现最小 Direct LLM baseline。
4. 对 A/B 执行相同单轮用例。
5. 对 A/B 执行相同多轮用例。
6. 收集四层指标。
7. 对图片结果进行盲评或至少去除系统标签。
8. 分析失败案例，不只报告总平均分。
9. 写出 Go / No-Go 结论。

## Go 条件

必须同时满足：

1. Layer 1 核心不变量 100% 通过；
2. 结构化系统在 Silent Decision、Missing Decision 或多轮 Drift 上有明确改善；
3. Edit Success 或 User Preference 至少一项有可测量优势；
4. Clarification Cost 没有高到抵消上述收益；
5. 结果可以在重复实验中复现。

不预先伪造百分比门槛；首轮实验报告实际分布，再冻结后续量化阈值。

## No-Go 后处理

如果没有明显优于 Direct LLM：

- 不进入 KnowledgeEngine；
- 不进入 Reference Image；
- 不进入多模型；
- 回到失败最多的层修正 Intent 机制；
- 不通过新增架构掩盖核心假设失败。

## 交付物

- Core MVP Evaluation Dataset；
- Direct LLM baseline；
- 自动指标脚本和人工评测表；
- A/B 结果；
- 失败案例分类；
- Gate A Go / No-Go 报告。

## 验收条件

- A/B 使用相同基础 Provider 条件；
- 单轮与多轮结果均报告；
- 四层指标分开；
- 报告包含负面结果和失败案例；
- 有明确、可执行的 Gate 结论。

## 禁止范围

- 不在评测期间修改系统规则；
- 不加入 RAG 提升 System B；
- 不只挑选成功案例；
- 不把 Prompt 评分当成图片结果评分；
- 不因为已投入开发而默认 Go。

## 给下一步的输入

只有 Gate A 明确为 Go，才向 Step 11 提供：

- 冻结的 System B；
- Gate A 数据集与指标；
- 可复用 PromptEngine 接口；
- 已识别的“知识可能改善”的失败类型。

