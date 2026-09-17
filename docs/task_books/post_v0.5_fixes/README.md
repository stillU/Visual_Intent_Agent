# v0.5 后置收尾策略书 001

日期：2026-09-16。状态：**F1/F2 已完成并通过离线验证（归档入口与当前 30-case 清单可交付）；F3 COCO-CN 归档精确许可仍暂不确认、许可待定项保持隔离；正式评测未执行且属范围边界；v0.5 整体继续部分完成。** 独立收尾批次，不覆盖 v0.5 原建设任务，不代表新版本发布。以下第 1～5 节为当初编制的策略原文，保留作为执行依据与历史记录。

## 0. 当前执行状态（2026-09-16 收尾执行后）

| 项 | 状态 |
|---|---|
| F1 审核依据随项目交付 | **已完成并通过离线验证**：5 份获批 v2 提案原字节归档至 `docs/knowledge_reviews/v0.5-approved-1/proposals/`，附 `README.md` 与 `archive_manifest.json` 历史路径映射；发布守卫与 `tools/validate_knowledge_review_proposals.py` 改读可交付归档，归档缺失、字节篡改、条件或候选变化仍被拒绝；`knowledge_base/v0.4/`、`knowledge_base/v0.5/` 的 JSONL 与 manifest 字节未变 |
| F2 历史准备材料与当前验收分开 | **已完成并通过离线验证**：Step 01 旧派生材料明确为**审核前历史准备，不计当前已执行覆盖**；当前清单 `tests/fixtures/knowledge/v0_5_acceptance_cases.json`（3 路径 × 10 场景，绑定 `v0.5-approved-1` 及 manifest sha256 `0bbeea9bbf7438bd8480e4cc56b1797368e2aa6b47c1601b0f276b44697b37d7`）由 `tests/generation/test_generation_v0_5_acceptance_matrix.py` 与复用用例执行，`tools/run_v0_5_acceptance_cases.py` 逐 case 结果 **30/30 pass** |
| F3 COCO-CN 许可待定 | **仍暂不确认**：保持隔离与用途待定，未将原文提交/发布/灌库/用于新验收集，未缩减验收范围，未改变许可裁定；正式评测未执行且属本版范围边界 |
| 集中离线验收（两环境） | **已完成**：原工作区全量 `2665 passed, 3 deselected`、`git diff --check` 通过；初始严格副本白名单遗漏 `evaluation/`，必测子集通过但全量收集失败；补齐代码白名单的无 `data/` 增强副本全量通过，命令、数量与边界见最终交接 |
| v0.5 整体 | **部分完成，不宣称完整完成**；正式评测未执行是范围边界，不是不完整原因或阻塞 |

- 本批最终交接（已完成）：[post_v0_5_fixes_001_handoff.md](../../handoffs/post_v0_5_fixes_001_handoff.md)。
- 可交付入口：审核依据归档 `docs/knowledge_reviews/v0.5-approved-1/`；当前 30 场景清单 `tests/fixtures/knowledge/v0_5_acceptance_cases.json`。
- 历史报告（如 [v0.5 发布交接](../../handoffs/v0_5_release_handoff.md)）中的旧测试数字与当时结论不改写，本批只追加补充说明与链接。

## 1. 目标与复查依据

不重做 RAG，不改变已批准知识；以最小修改补齐可移交性与测试材料一致性。顺序：基线核查 → F1 审核材料归档 → F2 样例与验收对齐 → F3 许可状态隔离 → 集中离线验收。

复查实测：全量离线测试 2583 passed、3 deselected；v0.5 可加载 5 条 approved，三路径采用测试通过；七个锁定文本哈希一致；70 条样本、67 组，现有分组未跨开发/保留池。这些是修复前结果，不能作为本批次验收结果。

| 编号 | 问题 | 最短处理 |
|---|---|---|
| F1 / P1 | 发布守卫与知识来源依赖被 Git 忽略的暂存提案，缺失时已复现失败 | 原字节归档五份获批提案，测试改读可交付归档，保留历史来源映射 |
| F2 / P2 | 成功样例仍标 draft_only / BLOCKED，三十条派生记录没有转成可执行验收 | 旧材料标为历史准备；补版本绑定的当前验收清单与测试映射 |
| F3 / 待定 | COCO-CN 归档许可仍暂不确认 | 保持隔离与部分完成状态，不借修复默许使用或自行删减验收范围 |

上游：[原任务书](../mvp_v0.5/README.md)、[发布交接](../../handoffs/v0_5_release_handoff.md)、[审核记录](../../handoffs/v0_5_knowledge_review.md)、[资源包](../rag_resources/README.md)。先读取适用 AGENTS.md 和实际代码，保护已有未提交成果；已修复问题验证后跳过，不重复改写。

## 2. F1：审核依据随项目交付

### 证据

tests/knowledge/test_knowledge_v0_5_release.py 的 STAGING_PROPOSALS_DIR 指向 data/staging/rag_resources_20260916/knowledge_review/draft_proposals/，发布守卫要求文件存在且匹配批准哈希；.gitignore 排除整个 data/。五个发布单元的 source.repository_path 也指向暂存位置。

拒绝无法核验的发布是正确的；错误是权威材料未随版本交付。不要改成 skip，不要吞掉失败，不要提交整个 data 目录。

### 最小实施

1. 将审核记录绑定的五份 v2 提案原字节归档至新的 docs/knowledge_reviews/v0.5-approved-1/proposals/。先验证原始 SHA-256，保留 draft/null/null，不格式化或重序列化，不将提案改为 approved。
2. 新建同级 README.md 与 archive_manifest.json，记录审核入口、语料版本、knowledge_id/version、历史暂存路径、归档相对路径、原文件 SHA-256 和对应发布单元。归档应能随项目交付且不被忽略；本任务不授权实际 Git 提交。
3. 不修改 knowledge_base/v0.4/ 和 knowledge_base/v0.5/ 的 JSONL/manifest，不更改原审核哈希或补造审批。旧 source.repository_path 保留为历史定位，通过归档索引明确映射到现存副本。审计工具使用明确映射，不改运行时加载器猜路径。
4. 发布守卫改读归档，继续检查原始哈希及发布相对提案仅改变三个审核字段。哈希锚定既有批准记录，不能让“文件和新清单同时改”变成合法发布。tools/validate_knowledge_review_proposals.py 默认校验归档，保留显式 --dir 支持待审草稿。
5. 在新归档说明及当前状态文档补充历史路径映射；原审核决定不重写。原文件缺失或哈希不符时停止该项并报告，不能从发布 JSON 反向猜造相同字节，也不能用新审批掩盖证据丢失。

### 验收

- 五份原始文件哈希与既有批准记录逐一相符；发布与提案仍只差审核三字段。
- 无 data/ 的代码副本能运行发布守卫和提案验证工具。
- 对临时归档副本注入缺失、字节篡改、条件或候选变化，测试必须拒绝；不改真实归档做破坏性测试。
- v0.4/v0.5 所有 JSONL 和 manifest 的修复前后字节哈希一致，普通数据库无需迁移。

## 3. F2：历史准备材料与当前可执行验收分开

### 证据

tools/prepare_v0_5_resources.py 的 _project_annotation 将 successful_adoption 固定为 draft_only，并写“9 draft / 0 approved、BLOCKED”。当前派生文件仍如此；工具测试验证记录形状，不代表三十条已经驱动实际采用。

现有 test_generation_v0_5_release_adoption.py 已证明五条生产规则可采用，不推倒重写。缺口是派生材料和已执行验收之间缺少准确关系。

### 裁定：历史归档 + 当前验收清单

1. 保留 Step 01 旧派生材料为审核前历史准备。工具和产物说明明确阶段、依据语料及“不是当前发布验收输入”。工具继续生成此类记录时，必须带明确历史/计划状态，不能无限定地声称当前生产知识为零。
2. 新建小型可交付清单，例如 tests/fixtures/knowledge/v0_5_acceptance_cases.json，绑定 v0.5-approved-1 及 manifest 哈希。三个路径各十类场景：委托、明确值、PIN、未确认条件、条件不符、模型不符、未审核知识、冲突、缺失知识、成功采用。
3. 清单属于项目原创工程用例，不是官方 benchmark，也不自动继承旧样本覆盖。不要为凑来源关系复制 COCO-CN 待确认文本或保留池原文。仅在许可明确且关系真实时关联旧开发样本 ID；未使用原文的明确标“非数据集派生”。
4. 每个 case_id 映射确切测试 node ID/参数 ID、准备条件、目标路径、语料/fixture 类型及预期状态。复用已有测试，缺哪项补哪项。新增检查保证三路径×十类完整、映射目标实际存在，不能仅填自然语言“应成功”。实际运行映射测试并生成逐 case 结果，未执行项不得标 pass。
5. 三条成功场景必须加载最终生产快照，满足真实条件、明确委托且已确认；断言 adopted、实际候选、Prompt/Realization 及数据库 Bundle 追溯。临时 approved 夹具仅用于合成拒绝/冲突场景，不能替代生产正例。
6. 负例隔离原因：条件不符时保留合法委托、审核通过和模型匹配，只改变条件；模型不符不能因 draft 先被过滤；冲突场景用可消费的测试单元，不是两个 draft。未确认/受保护路径可在检索前被阻断，按真实合同断言，不虚构拒绝 Bundle。

### 验收

历史准备材料不计为当前已执行覆盖；当前清单绑定最终语料，三十个 case 均有实际结果；生产正例、负例原因和数据库追溯准确。无需联网或原始数据下载即可运行工程用例。

## 4. F3：许可待定项独立管理

本批不裁定 COCO-CN 许可，不扩大抓取范围。保留“归档精确许可暂不确认”，不将相关原文提交、发布、灌入知识库或用于新的当前验收集。既有暂存保留，不删除或覆盖；元数据标明用途待定。

用户后续可选择补充可核实的许可依据，或明确将该来源从 v0.5 完成范围延期。未经明确裁定，Agent 不能通过减少样本数或改变任务定义宣布完整完成。F1/F2 不依赖此决定，可以先完成。

报告分开写“工程收尾完成”和“数据许可待定”。正式评测未执行是本版本的范围约束，不是应补跑的缺陷。

## 5. 修改边界

允许：审核归档与索引、相关验证工具、历史材料阶段说明、当前验收清单、缺失测试、当前状态文档。没有必要不改 visual_intent_agent/；若必须扩大运行时修改范围，先报告证据并请求裁定。

禁止：改变已批准内容/来源/条件/候选，重写生产快照，伪造审核，放宽哈希/PIN/确认，删测试避错，新增数据库迁移/向量库/网络检索/LLM，读取真实凭据或调用 Provider，启动正式评测或下一版本，未经要求提交推送。

验收见 [验收与交接](ACCEPTANCE.md)，执行入口见 [Agent 派发指令](AGENT_DISPATCH_PROMPTS.md)。
