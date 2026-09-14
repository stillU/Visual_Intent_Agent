# Step 05：Gate A 首轮结论与问题归因

## 目标

汇总冻结实验的四层结果，给出首轮 Gate A 结论，并把失败定位到可执行的问题类别。本步骤只分析和裁决，不修改产品代码。

## 前置依赖

- Step 03 已生成完整 L1～L3 逐案例结果。
- Step 04 已生成 L4 盲评结果和原始评价记录。
- 所有报告能够追溯到同一数据集哈希与运行配置。

## 结论类型

- `Go`：全部 Gate 条件满足，可进入最小 CLI。
- `Conditional Go`：核心假设出现优势，但存在范围明确、可修复且必须复评的问题。
- `No-Go`：核心不变量失败，或结构化系统没有显示出足以抵消澄清成本的优势。

## 实施内容

1. 分别报告 L1、L2、L3、L4，不合并成无法解释的单一总分。
2. 比较 Silent Decision、Missing Decision、Clarification Cost、Multi-turn Drift、Edit Success、Intent Alignment 和 User Preference。
3. 报告单轮与多轮、成功与失败、均值与分布。
4. 对每类失败给出案例 ID、发生层级、可能原因、影响范围和是否值得修复。
5. 区分产品缺陷、Provider 波动、实验协议缺陷和数据不足；协议缺陷不得被包装成产品修复。
6. 给出明确结论及下一步：进入 Step 06、直接进入 Step 08，或停止 v0.3。

## Gate A Go 条件

1. L1 核心不变量 100% 通过。
2. System B 在 Silent Decision、Missing Decision 或多轮 Drift 上有明确改善。
3. Edit Success 或 User Preference 至少一项有可测量优势。
4. Clarification Cost 没有抵消上述收益。
5. 结果在协议规定的重复实验中可复现。

## 交付物

- `evaluation/reports/gate_a_initial.md`
- `evaluation/reports/gate_a_initial.json`
- `evaluation/reports/failure_catalog.jsonl`
- `docs/handoffs/v0_3_step_05_handoff.md`

## 验收条件

- 结论与原始数据一致，负面结果完整保留。
- 每项修正建议都引用具体失败证据。
- 报告明确说明是否允许进入 Step 06 或 Step 08。

## 禁止范围

- 不修改业务代码、数据集、基线或评分逻辑。
- 不因为既有投入默认判定 Go。
- 不以增加 RAG、模型或 Agent 作为问题归因的替代品。
