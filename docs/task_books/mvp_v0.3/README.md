# Visual Intent Agent MVP v0.3：任务书索引

MVP v0.3 是“验证与可用化版本”。本版本不以增加架构层次为目标，而是验证 v0.2 的核心产品假设、修复评测暴露的问题，并在验证通过后提供一个最小可运行入口。

## 版本目标

1. 用同模型、同输入、同生成条件完成 Direct LLM 与 Visual Intent Agent 的 Gate A 对照评测。
2. 分开报告状态正确性、意图理解、Prompt 语义和图片结果，不用单层指标代替最终效果。
3. 只修复有评测证据支持的核心问题，并通过相同数据集复评。
4. Gate A 最终为 Go 后，提供可实际运行完整闭环的最小 CLI。
5. 本版本不实现 KnowledgeEngine、RAG、Web UI、多 Agent 框架、多模型路由或参考图编辑。

## 执行顺序

```text
01 评测协议与数据集冻结
  ↓
02 Direct LLM Baseline
  ↓
03 自动评测框架与 L1～L3 指标
  ↓
04 真实图片 A/B 与盲评
  ↓
05 Gate A 首轮结论与问题归因
  ├─ Go ───────────────────────────────┐
  └─ No-Go / Conditional Go → 06 定向修正 → 07 最终复评
                                        ↓
                          Gate A 最终为 Go
                                        ↓
                              08 最小 CLI 与版本验收
```

若 Step 05 已直接满足全部 Go 条件，可跳过 Step 06～07，直接进入 Step 08。若最终仍为 No-Go，则 v0.3 以证据充分的 No-Go 报告结束，不通过新增功能掩盖失败。

## 任务清单

| Step | 任务书 | 主要交付 | 前置条件 |
|---|---|---|---|
| 01 | [评测协议与数据集](01_evaluation_protocol_dataset.md) | 冻结数据集、标注与实验协议 | v0.2 Step 01～09 |
| 02 | [Direct LLM Baseline](02_direct_llm_baseline.md) | 最小基线及确定性测试 | 01 |
| 03 | [自动评测框架](03_evaluation_harness.md) | A/B Runner、L1～L3 指标与结果格式 | 01、02 |
| 04 | [图片 A/B 与盲评](04_image_ab_blind_review.md) | L4 结果、盲评表与原始记录 | 03 |
| 05 | [Gate A 首轮结论](05_gate_a_initial_decision.md) | Go / Conditional Go / No-Go 与问题归因 | 03、04 |
| 06 | [证据驱动的定向修正](06_evidence_driven_fixes.md) | 最小修复与回归测试 | 05 判定需要修正 |
| 07 | [Gate A 最终复评](07_gate_a_final_reevaluation.md) | 固定协议下的最终结论 | 06，或需重复验证 |
| 08 | [最小 CLI 与版本验收](08_minimal_cli_release.md) | 可运行入口、使用说明与 v0.3 验收记录 | Gate A 最终 Go |

## 全局约束

- v0.2 任务书与历史 Artifact 只读，不覆盖、不改写。
- 评测开始后不得为提高分数临时修改数据集、规则或评分口径。
- Baseline A 与 System B 必须复用相同 Provider、模型、参数和输入。
- L1 核心不变量必须 100% 通过，否则不能判定 Go。
- 修复必须对应已记录的失败案例，不建设“以后可能需要”的能力。
- KnowledgeEngine 只保留为 Gate A 之后的独立决策，不属于 v0.3 实现范围。
- Step 08 不绕过现有确认、Revision、Artifact 和状态机边界。

## 统一交接格式

```text
任务：MVP v0.3 Step XX
完成内容：
变更文件：
公开接口：
测试与实验结果：
原始 Artifact 位置：
已知限制：
对下一步的输入：
是否满足验收条件：是 / 否
```

派发 Agent 时同时提供本索引、对应任务书和 [派发提示词](AGENT_DISPATCH_PROMPTS.md)。
