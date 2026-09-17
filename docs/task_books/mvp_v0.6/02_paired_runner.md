# Step 02：最小可复现 B/C 执行器

## 复用与隔离

复用项目 Repository、Workflow 确认、PromptEngine、GenerationPipeline 及现有 Fake/记录组件。另建 evaluation/v0_6/ 的 B/C 驱动，旧 evaluation/runner.py 是 Direct Baseline A 对 System B，不能照搬其标签或直接覆盖旧协议。

每个 case×重复×arm 使用独立新 SQLite、新会话，无 active Realization。在两侧分别写入等价的合法评测上下文，通过各自确认摘要及确认接口建立绑定；评测脚本代表的是事先批准的场景，不伪称真实用户交互。

不要复用一个会话先 B 后 C，不跨库复制 Prompt、Bundle 或确认记录。测试辅助逻辑需放入独立评测模块，不让正式工具运行时依赖导入 tests/。

## 关键公平性：revision ID 控制

当前 select_delegated_value 用 intent_revision_id 作确定性种子。两侧任意生成不同 revision ID 会导致未被 RAG 干预的候选也变化，形成混杂。

最短方案：在两个独立评测数据库中，用固定 case_id + repetition 的确定性映射写入相同值的合法 intent_revision_id；各自保留完整父关系和独立确认绑定。相同 ID 仅在隔离库内存在，不跨库查询；汇总主键包含 run/case/repetition/arm。禁止修改产品默认选值算法、全局 monkeypatch 或搜索“让 C 必赢”的 ID。

在 Fake 测试证明：关闭知识或无命中时，B/C 选值与 Prompt 文本相同（身份字段按各自记录合法）；有采用时，差异仅来自实际采用的授权路径。确认绑定、PIN 与其它字段保持一致。若当前公开接口无法合法建立此上下文，先提出最小评测层方案，不绕过领域校验。

图片 Provider 支持显式 seed 时，B/C 使用相同预先冻结的 seed 对；不能把 revision seed 当图片 seed。若不支持，按固定规则随机化每对调用顺序，保留重复样本并在结论披露非确定性限制，不假装像素级可复现。

## 执行与记录合同

默认 dry-run/Fake；没有 G1 授权、代码 dirty、来源许可或哈希不符、模型身份不明、预算缺失时拒绝真实启动。复用现有 formal 身份检查，不能通过 diagnostic 模式冒充正式证据。

每条记录保存 run/case/repetition/arm、数据库定位、确认/revision、最终 Prompt hash、知识指纹和实际采用、Provider 参数、请求/结果 ID、尝试序号、耗时、结果/错误以及成本来源。真实凭据不入记录；API endpoint 需去除敏感查询参数。

预算守卫必须覆盖适配器内部重试：请求发出前持久化预留尝试与最坏成本，失败/超时照实记账。若无法观察每次内部尝试，则按该次调用的最大尝试成本预留，耗尽即停止，不仅统计 GenerationPipeline 的外层次数。

运行目录只追加；同一身份/配置的续跑不覆盖已有成功结果。网络超时、进程中断后结果未知的请求标 unknown，先核对 Provider 可查询状态；不能默认未扣费并自动重发。无查询能力时保持未知并请求决定。配置、代码或知识变化必须新 run，不混用旧输出。

## 必需离线测试

- 同对控制变量/种子一致，数据库与确认各自合法，非授权字段不变。
- 默认零网络、未授权/dirty/预算缺失/哈希变动拒绝真实入口。
- 五条规则适用与回退、负对照及既有数据库兼容。
- 模拟重试、超时、单侧/双侧失败、预算边界、中断和续跑，不重复付费请求。
- 身份完整、报告分母不丢失败、Prompt/Bundle/Realization 可追溯。
- 未知图片 seed 不偷偷传参；调用顺序可复建。

完成后写 docs/handoffs/v0_6_step_02_handoff.md，提供 Fake 演练和预算演算，不输出真实质量结论。
