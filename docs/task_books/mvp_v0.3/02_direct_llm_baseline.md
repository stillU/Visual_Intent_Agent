# Step 02：Direct LLM Baseline

## 目标

实现最小 Baseline A，使同一用户输入能够经同一 LLM 直接生成 Prompt，再调用与 System B 相同的图片 Provider。Baseline 不得借用 VisualIntent、澄清、确认或 Realization Carry。

## 前置依赖

- Step 01 的数据集、实验配置和失败处理口径已冻结。
- v0.2 Provider Adapter 可正常运行。

## 实施内容

1. 在 `evaluation/` 内实现基线合同与执行器，不加入产品业务包。
2. 单轮链路：用户文本 → LLM → Prompt → Image Provider。
3. 多轮链路：完整历史对话与前轮 Prompt 摘要 → LLM → 新 Prompt → Image Provider。
4. 每次运行保存输入、模型配置、原始 LLM 输出、最终 Prompt、图片 Artifact、耗时和错误。
5. 复用现有 LLM/Image Provider 和错误分类；不得为基线单独选择更弱或更强模型。
6. 提供 Fake Provider 测试，验证单轮、多轮、失败和重放行为。

## 冻结边界

- Baseline 可以通过系统提示要求输出适配目标图像模型的 Prompt。
- Baseline 不提问、不维护字段状态、不输出 IntentDelta、不使用 System B 的确认信息。
- 多轮输入格式必须在正式实验前冻结，并在 Artifact 中记录版本。
- Provider 失败按 Step 01 口径记录，不静默换模型或样本。

## 交付物

- `evaluation/models.py`
- `evaluation/direct_baseline.py`
- `tests/evaluation/test_direct_baseline.py`
- `docs/handoffs/v0_3_step_02_handoff.md`

## 验收条件

- Fake Provider 下可确定性重放单轮和多轮。
- 真实与 Fake 路径使用同一公开接口。
- 所有输入、Prompt 和图片结果均有稳定关联 ID。
- 未 import `domain`、`validation`、`policy`、`workflow`、`prompt_engine` 或 `realization`。

## 禁止范围

- 不实现评测指标或最终报告。
- 不在基线中复制结构化 Intent 能力。
- 不修改现有 Provider 行为来迁就基线。
