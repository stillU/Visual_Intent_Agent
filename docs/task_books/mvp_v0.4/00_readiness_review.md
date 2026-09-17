# 00 · v0.4 前置工程检查

检查日期：2026-09-16。范围：上一份独立修复书的 F1/F2/F3、相关代码与回归，以及 RAG 可接入位置；不是全仓无缺陷证明。

## 结论

**达到三项后置修复的工程预期，可开始 v0.4。** 当前仍是工程预览，不代表模型质量或用户收益已经验证。

| 项目 | 实际核查 | 结论 |
|---|---|---|
| F1 Preservation | `evaluation/metrics/prompt.py` 每个有效 Prompt 更新基准后无条件清空累计路径；测试覆盖初始 SET、跨澄清与无保留标签中间轮 | 已落实 |
| F2 完成率 | `evaluation/runner.py` 的预期生成标记不再排除 blocked；测试覆盖 0/2、1/3、先澄清失败再阻断、A/B 同分母 | 已落实 |
| F3 CLI 重试 | `SessionApp.retry_status` 读取 Prompt 并复用确认有效性校验；测试覆盖第二轮编译失败、无 Prompt、有效重试与检查后失效 | 已落实 |
| 版本身份 | `EVALUATION_HARNESS_VERSION` 升为 `evaluation_harness_v2`；旧指标与冻结输入未在本次差异中修改 | 已落实 |
| 集中回归 | 本次实际运行 `uv run pytest -q`：**2232 passed, 3 deselected in 7.30s** | 通过 |
| 差异检查 | `git diff --check` 无错误 | 通过 |

HEAD 为 `7417243`；上述修复及文档仍在未提交工作区，不能用 HEAD 单独代表已验证代码。下一 agent 保留这些改动，先核对状态；正式实验前另行固化可复现版本。本次未调用真实 Provider，未修改业务实现。

## RAG 接入依据

- `PromptEngine._plan_realizations` 已有 active 值复用与新委托值选择分支，适合最小接入。
- `select_delegated_value` 当前按 revision ID 摘要选择固定候选，并不理解场景；可以以检索提供的受限建议替换新值选择，保留原函数作回退。
- `SourceBinding` 当前仅允许 intent/delegation/realization/runtime。KnowledgeBundle 只能补充推导来源，不能成为新的用户授权类型。
- `PromptArtifact`、`RealizationValue` 尚无知识引用；需要显式、向后兼容地增加可选追溯字段，并更新合同文档与结构断言。
- `QwenImageRenderer` 是确定性 clause 拼接器，不是 LLM 重写器。本版保持这个边界，避免为了接知识库重构整条生成链路。

## 未被本次检查证明的事项

真实模型的语义理解、实际图片质量、RAG 收益、词法召回泛化能力均未验证。现有四条粗糙冲突启发式已停用，不代表系统获得通用冲突推理能力；本版也不以 RAG 偷换该能力。正式评测按用户要求暂缓到 RAG 接入后。
