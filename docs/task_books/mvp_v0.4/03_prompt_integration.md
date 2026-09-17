# 03 · PromptEngine 接入、来源与持久化

前置：01、02 通过。修改集中在 `prompt_engine/`、`realization/models.py`、`persistence/` 与相应测试；不改 Interpreter、Feedback、Policy 的业务语义。

## 接入位置与授权

`PromptEngine` 构造函数增加可选 keyword-only 的 KnowledgeEngine 依赖，默认 None，现有构造调用兼容。`PromptCompileRequest` 继续仅接受 session/confirmation IDs，不接受调用方任意提交的 KnowledgeBundle 或 Intent 草稿。

在确认校验及模型支持检查之后，读取同一组已确认 revisions，在 `_plan_realizations` 中：

1. 先复用 active Realization；不重新检索改选已有实现。
2. 对本版白名单中缺少实现的委托路径，检索并验证建议。消费端再次检查 path、候选、委托、PIN、conditions、目标模型与 Bundle 绑定；不能仅相信检索接口已过滤。
3. 采用合法建议，否则调用现有 `select_delegated_value`。非白名单路径原逻辑不变。
4. 继续用原 clause 构造、source binding、coverage 校验、renderer 与保存前再次确认校验；知识正文不直接生成 clause，不改变 VisualIntent。

来源分两层：授权来源仍为 `user_delegated`/delegation/realization；知识只说明为何选这个具体实现。禁止新增 `source_kind=knowledge` 以绕过授权。

## 最小追溯扩展

- `RealizationValue` 可选新增 `knowledge_bundle_id`、`knowledge_unit_id`、`knowledge_unit_version`，旧 payload 缺省 None；知识三字段必须同时有效或全空。跨 revision carry 原样保留，失效后新选择产生新来源。
- `PromptArtifact` 可选新增 `knowledge_bundle_refs`，默认空列表。既包含当前检索诊断，也包含本次复用 Realization 实际使用的历史 Bundle；不得只记录最新一次查询导致旧选择断链。
- 复用来源链：Prompt → source_binding.realization_id → RealizationValue → Bundle → 单元快照；不用给每种模型都另加一套授权逻辑。
- 新增 append-only `knowledge_bundles` 存储与必要 Repository 读写方法。旧表不改 payload、不修改旧 refs 的精确集合；采用幂等加表升级，不删除数据库。必须用旧数据库 fixture 验证升级与旧 Artifact 读取。
- 序列化合同需明示这是兼容扩展，更新字段枚举/精确字段集测试与架构决定；旧 payload 可读，新字段有默认值。不机械放松 `extra=forbid` 或删掉安全断言。

## 保存与失败语义

新的 Bundle 是不可变快照，保存时验证同会话及 revision 引用，随后 Realization/Prompt 可引用它；安全顺序保证不产生指向未落库 Bundle 的已保存 Prompt。写库失败硬失败，不能落一个伪称可追溯的 Prompt。允许留下可辨认的未被 Prompt 引用的诊断 Bundle，不伪称跨多个现有 Repository 调用已原子提交。

知识读取错误可在编译前回退；确认失效、来源校验、持久化失败不可伪装成“知识无命中”后继续。复用当前生成管线的编译错误处理，必要时增加明确的知识持久化编译错误 code 及其 CLI 提示，但不另加重试层。

已有 Prompt 的 `GenerationPipeline.retry` 不重新检索、不重新 compile；知识库变更不能影响它。关闭 RAG 只停止新检索，不清除已产生的 active Realization；纯 B/C 对照要用独立初始会话，不能对同一会话切开关假装控制变量相同。

## 必须通过的测试

检索确实改变新授权路径的选值与 Prompt；明确值与 PIN 不变；missing 不自动补值；伪造跨会话/过期 Bundle 被拒；越界候选和恶意正文不起作用；confirm 在检索期间过期不生成；禁用与空命中回退的业务 Prompt/状态等价；跨轮 carry/失效来源正确；重试零新检索；旧数据库和旧 payload 可用；存储失败不产生悬空引用。

“等价”排除随机 ID、时间及允许增加的诊断字段；用户状态、具体实现、Prompt 内容与生成参数必须一致。至少一个场景须证明 RAG 真正被消费，只有检索日志不算接入完成。
