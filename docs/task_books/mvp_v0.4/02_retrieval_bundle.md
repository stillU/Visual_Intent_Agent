# 02 · 本地检索与 KnowledgeBundle

前置：01 合同确定。建议文件 `knowledge/models.py`、`corpus.py`、`retrieval.py`；可按规模合并，避免空接口与框架化。

## 检索路径

1. QueryBuilder 只取已确认 Intent、目标模型、当前待实现路径；不取未确认聊天、任意文件或秘密配置。调用前由 PromptEngine 完成确认门禁。
2. 先按 approved、模型、路径、conditions 过滤，再进行词法排序。原文与 aliases 使用同一规范化：Unicode NFKC、英文小写与字母数字词、中文连续字符的二元组，并保留单字输入。将规则与版本固定；不要悄悄下载分词模型。
3. 首版采用可解释的规范化 token 重合评分：查询 token 集与单元检索 token 集交集数 / 查询 token 数；查询为空得 0。按分数降序、knowledge_id/version 稳定排序，每路径 Top-3。零分不命中，不把位置排在前面当作推荐理由。
4. 最高分单元给出唯一候选才可采用；最高分并列且候选不同则记录 ambiguous 并回退。低质量召回以后用诊断记录改进，不自动加入 reranker/embedding。
5. 无命中、语料缺失/校验失败、已批准单元为空时走现有固定候选回退，记录具体原因。只捕获明确的知识读取/校验错误，不以 `except Exception` 吞掉编程错误或确认失败。

索引是 JSONL 的内存派生物，启动时构建一次并固定语料哈希；不热更新。重启或显式重建加载新版本。对首批小语料无需独立数据库服务。若未来改用 FTS5，必须另测中文短词：SQLite 官方文档说明 trigram 全文查询不匹配少于三个 Unicode 字符的子串，不能直接把它当作中文短词方案。[SQLite FTS5 文档](https://www.sqlite.org/fts5.html)

## KnowledgeBundle

记录 `bundle_id`、session/intent/execution/confirmation IDs、语料哈希和版本、retriever 版本、查询与路径、实际检索单元快照（ID/version/hash/来源/内容）、分数、采用与拒绝原因、最终推荐、时间。

不同路径分别检索，合并一个编译级 Bundle；先限定最多 3 路径 × Top-3。身份绑定不能仅是自然语言 query。知识失效/无命中也可有诊断 Bundle，必须与实际采用的单元区分。禁用 RAG 时不查询、不生成 Bundle。

## 验收

- 中英文、同义词、短词、空输入、无命中、跨模型、未审核和条件缺失场景。
- 相同语料与查询的候选、排序、分数一致；文件行序变化不改变选择。
- 哈希或语料版本变化可观测；不得拿旧索引冒充新语料。
- 检索接口与 Fake 使用同一合同；无网络；查询与日志不含凭据。
- 至少两个不同确认场景导致不同的合法推荐，证明不是固定返回一条知识。

检索召回检查仅作为工程诊断，不构成正式收益评测。
