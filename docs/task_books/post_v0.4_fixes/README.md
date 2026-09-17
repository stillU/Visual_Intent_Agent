# v0.4 后置修复书 001

日期：2026-09-16。状态：F1/F2 已离线实施并通过，F3 已交接且生产知识待审核。独立修复批次，不属于 `mvp_v0.4/` 原任务书，不代表新版本正式发布。

## 1. 目标、基线与执行顺序

以最小修改补齐两处已确认的代码缺口：

1. **F1 / P1：知识记录写入失败后，会话停留在 GENERATING。**
2. **F2 / P2：编译消费端缺少知识适用条件的二次校验。**

另设 **F3：生产知识审核交接**。这是内容交付缺口，不是允许 agent 自动批准知识的工单。

顺序：核对工作区 → F1 → F2 → 集中离线回归 → F3 状态核查与交接。生产知识尚未审核不阻止代码修复；代码完成和生产内容完成必须分开报告。

审查时 HEAD 为 `b039b9b`，v0.4 实现在未提交工作区；实际回归为 **2500 passed, 3 deselected in 7.87s**。该数字是修复前记录，不能作为本批次验收。实施前核对实际状态，保留用户和其他 agent 的成果；若问题已被修复，验证后跳过重复改动。

本轮只修复和做离线验证。不启动真实 Provider、付费图片、正式 A/B、人工图片盲评或 Gate 判定。知识内容审核与图片盲评不是同一事项。正式评测继续等待独立启动。

## 2. F1：知识持久化失败必须进入受控失败路径

### 证据与影响

`visual_intent_agent/prompt_engine/engine.py::_append_knowledge_bundle` 直接调用 Repository，可能抛出 `RepositoryError`。`visual_intent_agent/generation/pipeline.py::generate` 已先转入 GENERATING，但 compile 异常处理只捕获 `PromptCompilationError`。

审查用临时数据库和 Fake Image 注入一次 `append_knowledge_bundle` 写入失败，实际结果：

```text
异常：RepositoryError / persistence.database_error
会话状态：GENERATING
retry：generation.invalid_state
图片调用：0
```

现有 `test_bundle_persistence_failure_does_not_leave_a_prompt` 直接调用 compile，只检查未写入 Prompt，没有覆盖生成管线状态。

### 最小修复路径

优先在 `GenerationPipeline.generate` 的 **compile 调用边界**显式处理 `RepositoryError`，复用现有失败记录与 FAILED 迁移机制；保留原错误 code 和异常因果，不把写库失败变成知识无命中。PromptEngine 独立调用仍可抛原 RepositoryError，避免无必要地改动其公开异常合同。

若实际结构要求转换为明确的 `PromptCompilationError`，必须先说明理由，并同步错误 code 合同、CLI 与测试；二选一即可，不叠加多套失败处理。

- 不添加 `except Exception` 吞掉编程错误，不新增自动重试层，不自动重新确认或重新 compile。
- 失败不得调用图片 Provider，不得落一个引用未保存 Bundle 的 Prompt。
- 保持 Bundle → Realization → Prompt 保存顺序；不为本修复重构多表事务。允许已落库的未被 Prompt 引用的审计记录保留，不能删除历史以掩盖失败。
- 进入 FAILED 不等于可以生成重试：没有当前有效 Prompt 时，CLI 明确提示不可 retry；历史 Prompt 过期时沿用既有过期提示，不退回旧图。
- 全库不可写时，FAILED 迁移本身也可能失败。不得承诺在存储彻底故障时仍能持久化状态；保留原始失败与迁移失败的可诊断信息，不伪称状态已恢复。正常可写数据库中的单次 Bundle 写入失败必须转 FAILED。

### 必须新增的离线回归

1. 在真实 GenerationPipeline 中注入一次 Bundle 写入失败，其他数据库操作正常：状态 FAILED、图片调用 0、无新 Prompt/Generation，无悬空引用。
2. CLI 同场景给出安全错误提示，不崩溃、不停在 GENERATING 的瞬时状态分支；无有效 Prompt 时不诱导用户 retry。
3. 第二轮修改后写 Bundle 失败：旧图和旧 Prompt 保留，但不能用旧确认重试。
4. 若统一捕获 compile 内的 RepositoryError，同样覆盖 Realization/Prompt 写入失败，不只覆盖 Bundle 表。
5. 原有 Provider 失败重试仍复用原 Prompt，知识检索次数不增加；RAG 关闭流程不变。
6. 失败处理自身遇到数据库不可写时，不丢失原始错误，不发生无限重试，不产生成功响应。

允许修改：`generation/pipeline.py`、必要的 `prompt_engine` 错误边界、CLI 错误提示与对应测试。不得修改状态机合法迁移规则来绕开错误。

## 3. F2：保存适用条件快照并在消费端复核

### 证据与影响

`knowledge/bundle.py::KnowledgeUnitHit` 只保留命中内容等字段，缺少 conditions、target_models 与审核状态快照。`PromptEngine._recommendation_is_usable` 明确把 conditions 判断交给检索器，未达到原计划要求的消费端纵深校验。

审查模拟检索器误用了上下文：当前确认的 environment.mode=indoor，但检索器用 outdoor 上下文返回仅在 outdoor 条件下适用的单元，仍携带正确 session/revision/confirmation IDs。编译器采用了该建议。

这里的问题是**违反单元自己的适用条件**，不是“室内不能用自然光”。当前 LocalKnowledgeEngine 正常路径已有条件过滤；本修复补齐检索器错误、缓存错误等情况下的第二道校验，不宣称当前所有正常检索都有误。

### 最小合同扩展

为命中单元添加可验证的适用性快照，优先用一个可选嵌套字段，例如 `eligibility_snapshot`，包含：

- `conditions`：复用现有 KnowledgeCondition 类型和封闭算子。
- `target_models`：明确模型集合或现有通用标识。
- `review_status`、`reviewer`、`reviewed_at`：与权威 KnowledgeUnit 一致。

字段名称可依据现有模型调整，但不得丢掉上述语义。新 LocalKnowledgeEngine 从已经校验的 KnowledgeUnit 原样构建快照，不从 query 猜、不从正文重新解析。

**旧数据兼容：**整个快照缺省为 None，表示“旧记录未提供条件证据”；不能默认 `conditions=[]`、`target_models=any`、`approved` 而把证据缺失当成无限适用。旧 Bundle/Prompt/Realization 必须可读；已有 active Realization 和原 Prompt 重试保持原行为，不重写历史、不重新选值。只有当前编译准备采纳的新推荐必须具有完整快照。

新快照进入不可变 Bundle payload，通常不需要新增业务表或数据库迁移。更新 Bundle 合同/实现版本的现有标识与架构决定，保留旧版本读取能力；不改无关冻结评测协议，不批量重写历史 Bundle。若新增字段影响严格序列化断言，按兼容语义更新测试，不关闭 `extra=forbid`。

### 消费端验证顺序

1. 继续检查 session、intent/execution revision、confirmation、目标模型和 pending 路径身份；原硬拒绝行为不放宽。
2. 继续检查该路径明确委托、无用户显式值、未 PIN，以及候选白名单。
3. 要求完整适用性快照、approved 且审核字段有效；复核精确模型匹配或显式通用适用。
4. 复用现有纯函数 `evaluate_conditions`，用 **PromptEngine 自己读取的已确认 Intent** 判断全部条件，不用 Bundle 的查询文本替代事实，不复制出另一套条件解释器。
5. 验证推荐与命中单元的 ID/version/hash/path/value 一致；对实际内容重新核对现有 content_hash，避免仅检查哈希字符串形状。
6. 缺失快照、条件不满足、审核或模型不适用的单条建议安全回退到原候选选择，并记录拒绝原因；不能同时把它显示成 adopted。复用现有 reason 字段或做最小兼容扩展，区分“检索器推荐”和“编译器实际采用”。

该快照用于复核和追溯，不是数字签名；不得宣称它能证明任意恶意替换的检索服务可信。信任边界仍是经审核、哈希校验的本地权威语料。原始 content 仍不可执行、不可直接进入 clause，不能成为用户授权。

### 必须新增的离线回归

- 返回正确身份 IDs、但只适用于 outdoor 的推荐给 indoor 会话：消费端拒绝并回退；原 Intent/PIN 不变。
- 条件字段缺失、equals/in 不满足、subject.count 类型边界：与现有检索条件规则一致。
- 单元模型不匹配，即使 Bundle 的 target_model 正确也不能采用。
- 单元为 draft/rejected、审核字段缺失、旧快照 None：新推荐不得采用；原因可查询、CLI 不误报 adopted。
- 合法完整快照仍被采用，实际影响授权实现值；零新 LLM/网络调用。
- 内容与哈希不符被拒，不能仅靠推荐和命中项填写同一错误哈希过关。
- 旧 Bundle 可反序列化、旧 active Realization 可复用、原 Prompt 重试零新检索，旧库升级与历史读取测试继续通过。

允许修改：`knowledge/bundle.py`、`knowledge/retrieval.py`、必要的知识合同导出、`prompt_engine/engine.py`、最小 CLI 采用/拒绝展示、相关测试与架构记录。不改 Intent、Policy、确认算法与候选白名单。

## 4. F3：知识内容审核与真实交付状态

审查实际加载 `knowledge_base/v0.4/`：9 条单元，全部 draft，0 条 approved。它允许工程链路透明回退，但不能称为“生产 RAG 已在使用审核知识”。

本批次只需：

1. 重新核查实际条数、来源、适用路径/条件、审核字段，并列出逐条待审核清单；优先复用已有知识目录说明，避免重复内容。
2. 将待审核建议整理到 `docs/handoffs/post_v0_4_knowledge_review_checklist.md`，列明 ID/version、来源、候选、适用条件、疑点与待审核状态，便于用户或真实审核人决定。
3. 用户没有实际批准时保持 draft；禁止 agent 填造 reviewer/date，禁止把演示或测试夹具升格为生产 approved。此时明确报告“代码验收完成，生产知识待审核”。
4. 若后续获得真实审核，单独记录决定；按语料版本与哈希流程发布新快照，不仅修改 review_status 后跳过 manifest 校验。再做离线采用验证，不自动启动真实评测。

不为满足数量目标扩充到 30 条，不引入新知识来源爬取、向量服务或知识管理界面。本轮不把内容审批当作模型质量证据。

## 5. 验收、范围与交接

- 先加入能在修复前失败的定向回归，再修改实现；使用 Fake Provider、临时 SQLite、临时语料，默认完全离线。
- 开发中只运行相关局部测试，集成完成后运行一次 `uv run pytest -q` 与 `git diff --check`；失败针对根因修复并做必要验证，如实记录，不能通过删测试或放宽安全断言过关。
- 保留现有业务表、旧 payload 和历史 Artifact；不删除用户数据库，不改 `.env`、Provider 重试预算或旧评测冻结文件。
- 编写 `docs/handoffs/post_v0_4_fixes_001_handoff.md`，包含实际 HEAD/dirty 状态、F1/F2 修复前后证据、变更文件、兼容合同和版本、测试命令/结果、F3 审核状态与剩余风险。
- 若必须扩大到状态机重构、全库事务或新持久化架构，先给出最小必要范围与理由，请用户裁定，不自行扩展。
- 未获明确要求，不提交、推送、stash 或覆盖现有工作。

最终分别报告：**代码修复是否通过、旧数据库兼容是否通过、生产知识是否已审核、正式评测未执行。** 不以全量测试通过推导知识质量或 RAG 收益。

## 6. 可直接派发给其他 agent 的指令

```text
请执行 docs/task_books/post_v0.4_fixes/README.md。

先核对当前 HEAD、工作区、v0.4 实现与交接，保留所有已有成果。按 F1→F2 顺序最小修复：知识 Bundle 写库失败接入生成管线受控失败状态；为新推荐保存完整适用性快照，并由 PromptEngine 使用已确认 Intent 复核条件、审核与模型适用性。旧 Bundle 可读，旧 Realization/Prompt 复用与 retry 不变，不能通过放宽 PIN、确认、Revision 或来源校验让测试通过。

补齐修复书列出的离线故障注入、错误上下文、旧数据兼容与端到端测试，集成后运行全量离线回归，交付 docs/handoffs/post_v0_4_fixes_001_handoff.md。

F3 只核查并整理生产知识待审核清单到 docs/handoffs/post_v0_4_knowledge_review_checklist.md；没有真实审批就保持 draft，不能伪造 approved。代码完成和知识审核完成分别报告。

不启动真实 Provider、图片批次、正式评测或 Gate 判定；不扩建 RAG、不改冻结评测物、不删除历史数据库。未经用户要求，不提交或推送 Git。
```
