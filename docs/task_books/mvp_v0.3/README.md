# Visual Intent Agent MVP v0.3：任务书索引

当前修订入口：[更改书 002：从 Step 06 断点并行构建](REVISION_002_BUILD_FIRST.md)。当前从 Step 06 patch 002 继续，优先完成工程实现与最小 CLI，集中离线验收；真实复评和正式发布分开处理。002 优先于 [更改书 001](REVISION_001_SHORTEST_PATH.md) 与下列原规划。

MVP v0.3 是“验证与可用化版本”。本版本不以增加架构层次为目标，而是修复现有问题、交付可运行的工程入口，并独立完成产品假设的正式评测。

## 当前实施看板（优先于下文原顺序）

| 阶段 | 当前事实 | 接下来 |
|---|---|---|
| Step 01～05 | 历史实现、冻结物与首轮证据已存在 | 保留，不重复实施 |
| Step 06 patch 002 | 本轮起始断点，成果未全部提交 | 保留既有 dirty 工作区 |
| Step 06 / A 语义 | interpreter.v5、policy.v2 已实现 | 集中离线验收通过 |
| Step 06 / B 恢复 | feedback.v4、显式失败、所有反馈裁决过期保护已实现 | 集中离线验收通过 |
| Step 06 / C r1评测修订 | 新协议、数据、清单与 Runner 工程修订完成 | 集中验收通过；阈值冻结/语义阶段配对留待后续 |
| Step 08 / D CLI 工程预览 | 模块入口、Fake 演示、人工确认与失败恢复已实现 | 11 项 CLI 测试通过，不冒称正式发布 |
| Step 07 正式复评 | 本轮未执行 | 后续冻结正式判定规则与干净快照，再独立执行 |

稳定集成后的集中验收：`uv run pytest -q` → **2215 passed, 3 deselected in 6.73s**；本轮未运行真实 Provider 或图片评测。

工包分配与验收条件见 [更改书 002](REVISION_002_BUILD_FIRST.md)，跨模块决策及实际验收记录见 [集成交接](../../handoffs/v0_3_r2_integration_handoff.md)。

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
