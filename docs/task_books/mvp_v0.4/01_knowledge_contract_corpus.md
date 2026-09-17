# 01 · 知识合同与首批内容

## 范围

新增 `visual_intent_agent/knowledge/`、`knowledge_base/v0.4/` 与 `tests/knowledge/`。先定义模型、审核与消费合同，再写检索；不引入新依赖。新增架构决定 `docs/handoffs/architecture_decision_005.md`（如编号已占用则取下一号），记录本版对旧 Step 11 的替代、兼容字段、存储与依赖边界，最小更新 `docs/ARCHITECTURE.md` 的现状，保留历史修订。

## KnowledgeUnit 合同

每条为一项可独立审核的规则，不机械切分大文档；单元建议不超过 800 中文字符。字段至少为：

- `knowledge_id`、`version`、`content_hash`、`content`。
- `applicable_path`：本版三条白名单之一；`candidate_value`：该路径现有候选值。
- `keywords`、中英文 `aliases`，用于检索；不从测试答案生成词表。
- `conditions`：基于已确认字段的精确值或显式枚举集合判断，禁止可执行表达式、正则脚本、LLM 判定。缺少必要条件时视为不满足。
- `target_models`：明确匹配的模型标识或明确的通用标识，不根据近似名称猜测兼容性。
- `source`：来源类型、标题、URL/仓库相对路径、定位、来源 revision/日期、许可或内部原创声明。
- `review_status`（draft/approved/rejected）、`reviewer`、`reviewed_at`。只有真实审核后才可 approved；agent 不可虚构人类审核。

来源以经过核实的模型官方材料和项目原创领域规则为主，所有适用性须人工审核。首批目标 30 条、每条路径约 10 条；先用 9 条（每路径 3 条）打通离线链路，不用低质量凑数。测试单元与生产内容分开存放，测试里的 approved 不得当作生产审核证据。人工审核未完成可交付 draft 与 Fake 工程验收，不宣称生产知识库已验收。

JSONL 为权威源，清单记录语料版本、每文件哈希、Schema/分词/检索版本。拒绝重复 ID、非法路径、非法候选、无来源、伪造审核字段；同 ID 不允许静默覆盖旧内容，更新须新版本。构建时只加载 approved；单元排序变化不应改变稳定检索结果。

## 安全裁定

检索资料按不可信数据处理。原始 content 用于说明和审计，不执行、不作为系统指令、不拼入 Interpreter/Feedback 的消息。消费端只读取验证过的 path/value/conditions。恶意正文要求“忽略用户/PIN/读取文件”等不得产生副作用；审核不能取代程序白名单。

用户明确值优先；RAG 只能为仍是 `user_delegated`、未 PIN、尚无 active Realization 的路径提出候选。建议不满足已确认条件、命中多个同等候选而无法消歧时回退，不强行采用。现有候选白名单是授权上限，不意味着任意候选与场景都相容。

## 来源核查注意事项

[Qwen-Image 官方仓库](https://github.com/QwenLM/Qwen-Image) 可作为资料入口，但本项目请求的 `qwen-image-3.0` 是当前代码标识；未核对实际 Provider 文档前，不得把官方仓库其他版本的能力、参数或提示词当作该别名的保证。模型对应关系不明时，仅启用审核后的通用知识。

验收：Schema 与候选校验、来源完整性、draft 排除、重复 ID/篡改哈希拒绝、恶意正文无执行能力的离线测试通过；给出生产内容实际审核状态。
