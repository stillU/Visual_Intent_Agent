# Step 07：Gate A 最终复评

## 目标

在与首轮完全相同的冻结协议下复评修正后的 System B，比较首轮与复评结果并给出最终 Gate A 结论。

## 前置依赖

- Step 06 已验收并提供修复追踪记录；或 Step 05 明确要求额外重复实验。
- Step 01 的数据集、配置和评分口径保持不变。
- Baseline A 的正式结果不得重新挑选或调参。

## 实施内容

1. 记录待测 System B 的 commit、依赖锁和配置。
2. 使用同一 Runner、数据集哈希、Provider 条件和重复次数执行复评。
3. 分别展示 Baseline、首轮 System B 和修正后 System B 的 L1～L4 结果。
4. 检查修正是否改善目标失败，同时引入新的回归或澄清成本。
5. 对仍失败的案例进行分类，不隐藏负向变化。
6. 按 Step 05 的同一 Go 条件给出最终 `Go` 或 `No-Go`。

## 交付物

- `evaluation/reports/gate_a_final.md`
- `evaluation/reports/gate_a_final.json`
- 完整复评 Run Artifact
- `docs/handoffs/v0_3_step_07_handoff.md`

## 验收条件

- 首轮和复评使用相同协议，可逐案例比较。
- 报告同时包含改善、无变化和退化案例。
- 最终结论明确且可执行：Go 才允许 Step 08；No-Go 则停止功能扩展。
- 原始实验数据和环境信息可审计。

## 禁止范围

- 不修改任何业务代码或评测代码。
- 不重跑并挑选最有利的随机结果。
- 不因修复投入而降低 Gate 条件。
