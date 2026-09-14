# Step 04：真实图片 A/B 与盲评

## 目标

在冻结实验条件下生成 A/B 图片结果，建立去除系统身份标签的盲评材料，收集 L4 Image Result 指标及原始评价记录。

## 前置依赖

- Step 03 Runner 与 L1～L3 结果格式已验收。
- 真实 Provider 配置可用。
- Step 01 已规定随机性、失败重试和人工评分口径。

## 实施内容

1. 对冻结数据集执行真实 A/B；两套系统使用相同 Image Provider、模型和参数。
2. 为每个案例保存 Prompt、图片、Provider 响应、耗时、失败信息和运行版本。
3. 随机化 A/B 展示顺序，生成不含系统名称、Prompt 来源和内部 ID 的盲评包。
4. 采集 Intent Alignment、Attribute Preservation、Edit Success 和 User Preference。
5. 多轮案例逐轮保留结果，不只评价最终一张图片。
6. 将评分与匿名样本 ID 关联；解盲映射单独保存，报告生成前不得人工挑选。
7. 输出原始分布、平局、缺失评价和失败案例，不只输出平均分。

## 交付物

- `evaluation/image_review.py`
- `evaluation/runs/<run_id>/` 原始 A/B Artifact
- `evaluation/reviews/<run_id>/` 盲评包、评分与解盲映射
- `evaluation/reports/<run_id>_layer4.json`
- `tests/evaluation/test_image_review.py`
- `docs/handoffs/v0_3_step_04_handoff.md`

## 验收条件

- 盲评材料无法从文件名或展示文本识别系统身份。
- 每项评分可追溯至冻结案例和原始图片，同时不污染盲评界面。
- A/B Provider 条件一致；差异均被显式记录。
- 失败图片和无效样本按协议报告，不被静默替换。

## 禁止范围

- 不修改图片或人工筛选“更好看”的样本。
- 不让评审者看到系统标签。
- 不在本步骤下 Gate A 结论。
