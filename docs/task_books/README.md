# 版本任务书

最新交付整理：[v1.0 最小交付收敛](v1.0/README.md)（2026-09-17 编制任务书；Step 01～04 已静态实施）。按用户要求只做静态检查与整理，不运行测试或验证；[v0.6 静态状态](v1.0/00_v0_6_static_review.md)仍为离线准备交付，真实评测尚未完成。候选交付整理说明见 [v1.0.0rc1](../releases/v1.0.0rc1.md)，交接见 [v1_0_handoff.md](../handoffs/v1_0_handoff.md)。

下一版本计划：[MVP v0.6：RAG 对照验证与结果闭环](mvp_v0.6/README.md)（仅编制任务书，尚未实施）。v0.5 收尾独立复查与两环境回归见 [准备状态](mvp_v0.6/00_readiness_review.md)；真实评测须经过单独的预算、身份与评审授权关口。

最新收尾入口：[v0.5 后置收尾策略](post_v0.5_fixes/README.md)（**F1/F2 已完成并通过离线验证；F3 COCO-CN 归档精确许可仍暂不确认**）。最新复查除许可待确认外，还发现审核材料可移交性、派生样例与验收衔接两项缺口，这两项已由 F1/F2 修复，归档入口与当前 30-case 清单可交付；不含 `data/` 的隔离副本集中回归已完成，最终交接 [post_v0_5_fixes_001_handoff.md](../handoffs/post_v0_5_fixes_001_handoff.md)（已完成），v0.5 整体继续部分完成。下方原版本交接的“唯一不完整原因”属于此前结论，应结合本次策略书阅读。

本目录按项目版本归档实施任务书，避免任务规划与项目介绍、架构文档混排。

当前版本：[MVP v0.5：知识落地与离线验收](mvp_v0.5/README.md)（**已实施**，v0.5 整体为**部分完成**，唯一不完整原因是 Step 01 的 COCO-CN 许可暂不确认）。实施状态入口：[docs/mvp_v0.5/README.md](../mvp_v0.5/README.md)（版本实施 / 审核 / 评测状态摘要）；版本交接：[v0_5_release_handoff.md](../handoffs/v0_5_release_handoff.md)。人工知识审核与正式评测仍分别管理，正式评测本版不执行。

当前修复入口：[v0.4 后置修复（独立批次）](post_v0.4_fixes/README.md)。v0.4 RAG 工程链路已接通，审查发现知识写库失败状态与消费端条件校验两项缺口；生产知识审核已由 v0.5 闭环（`knowledge_base/v0.5/` 5 条真实 `approved`，`knowledge_base/v0.4/` 仍 9 draft / 0 approved）。本轮仅修复与离线回归，正式评测仍待独立启动。原建设计划保留在 [MVP v0.4](mvp_v0.4/README.md)。

- [MVP v0.2](mvp_v0.2/README.md)：Step 01～11 任务书及 Agent 派发提示词。
- [MVP v0.3](mvp_v0.3/README.md)：验证与可用化版本，覆盖 Gate A、定向修正和最小运行入口。
- [v0.3 后置修复](post_v0.3_fixes/README.md)：独立修复书，包含实施范围、验收用例与 Agent 派发指令。
- [MVP v0.4](mvp_v0.4/README.md)：RAG 知识合同、检索与编译接入、CLI 验收及后续评测准备。
- [MVP v0.5](mvp_v0.5/README.md)：开放文本准备、知识证据与真实审核、独立语料发布、生产语料离线采用验证。
- [MVP v0.6](mvp_v0.6/README.md)：RAG 开关 B/C 对照、可复现执行器、预算门禁、获授权后的盲评与结果分析。
- [v1.0 最小交付收敛](v1.0/README.md)：统一版本展示、整理配置与现有启动方式、精简文档、静态收敛与交接；Step 01～04 已静态实施。候选交付整理说明见 [`docs/releases/v1.0.0rc1.md`](../releases/v1.0.0rc1.md)，交接见 [`docs/handoffs/v1_0_handoff.md`](../handoffs/v1_0_handoff.md)。
- [v0.5 后置收尾策略](post_v0.5_fixes/README.md)：审核材料归档（F1 完成，归档入口可交付）、样例与实际验收对齐（F2 完成，30/30）、许可待定项隔离（F3 仍暂不确认）及无暂存环境回归（已完成）。
- [v0.4 后置修复](post_v0.4_fixes/README.md)：失败状态处理、知识适用性二次校验、生产知识审核交接与 Agent 派发指令。
- [RAG 知识与开放测试数据资源包](rag_resources/README.md)：独立于版本实施任务，提供已核实来源、固定版本下载清单、许可边界及后续 Agent 操作指引；不启动正式评测。

后续版本统一新增独立目录，例如 `mvp_v0.3/`、`v1.0/`。每个版本目录自行维护任务索引、执行顺序和派发提示词，不覆盖其他版本的任务书。
