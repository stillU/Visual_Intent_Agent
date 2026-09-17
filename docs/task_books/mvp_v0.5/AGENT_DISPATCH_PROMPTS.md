# v0.5 Agent 派发指令

默认按顺序由一个 Agent 执行。只有用户另行要求多人协作时再分派；不要让多名 Agent 同时改合同或语料。

## 完整实施入口

```text
请实施 docs/task_books/mvp_v0.5/README.md 及其 Step 01～04。
先完成 Step 00 基线核查，读取项目规则、v0.4 修复交接和 rag_resources 资源包，保护工作区既有未提交成果。
目标是开放文本可复现准备、真实知识审核和独立语料发布、最终生产语料的离线采用验证；不是重建 RAG，也不是正式效果评测。
按步骤交付 docs/handoffs/v0_5_step_XX_handoff.md。现有代码能满足要求时只新增内容与缺失测试，不为新版本而重构。
真实审核是关口：先提交 docs/handoffs/v0_5_knowledge_review.md，请用户对精确提案作决定；未获真实审核不能伪造 approved/reviewer/date，也不能把演示夹具当生产内容。等待时可准备独立工程工作，最终明确内容尚未完成。
获批后按 Step 03 新建 knowledge_base/v0.5 快照，保留 v0.4；用 Fake Provider、临时真实数据库和最终快照验证采用与追溯，不能用 --demo 冒充。
不得修改领域 Schema/PIN/确认或候选白名单，不新增向量服务、不调用真实 Provider、不跑正式评测、不改变正常数据库功能；未经要求不提交推送。
集中回归后交付 docs/handoffs/v0_5_release_handoff.md，分开报告工程、数据、知识审核、数据库兼容和正式评测状态。
```

## 审核完成后的续接入口

```text
继续 v0.5 Step 03～04。先读取 docs/handoffs/v0_5_knowledge_review.md 和用户实际审核决定，逐项核对批准对应的完整提案哈希、ID/version、条件、候选和模型范围。
只有真实批准且许可完整的内容可以发布；信息不完整先询问，不补造审核事实。未批准路径继续回退，并如实说明版本目标尚未全部达到。
依任务书发布独立语料快照，用最终语料、Fake Provider、临时真实数据库验证实际 adopted 与历史兼容，再进行集中离线回归和版本交接。
本次继续不授权正式评测、真实图片调用、修改领域合同或 Git 提交推送。
```
