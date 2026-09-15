# v0.3 Build-first 集成交接

## 断点

- Git HEAD：`6f7ca17ed5fbd84cdbf4927b33dd9d409035be39`（docs: plan MVP v0.3 validation and usability）。
- 本轮开始时工作区已 dirty：Step 01～06 源码修改、evaluation、测试与交接尚未全部提交。未回滚、未自动提交；HEAD 不代表本轮实现快照。
- 旧真实探针、2151 项离线测试是历史记录，不作为本轮验收。
- 当前计划：[更改书 002](../task_books/mvp_v0.3/REVISION_002_BUILD_FIRST.md)。

## 调度记录

| 工包 | 子代理 | 写入范围 |
|---|---|---|
| A | `d333b30e-bbee-443a-9ba2-fc830c7a4374` | Interpreter prompt、Policy、对应测试；额外授权单个 policy golden fixture |
| B | `844e46bf-b3d9-4bb3-bf3c-e962ab8ac2d5` | Workflow/Review 可恢复失败、Feedback、对应测试 |
| C | `6868d2a5-f7c5-46ae-b709-c3b1b248ab1a` | evaluation 与对应测试，r1 新冻结输入 |
| D | `6219141e-3141-40bd-8216-0f211a811d6e` | CLI 入口、Fake 端到端、使用说明、预览发布记录 |

模型均为 `deepseek-official/deepseek-flash`（DeepSeek-V41-Flash）。主代理负责计划和集成；共享文件修改提前协调。

## 跨工包决策

1. 景别不是输出比例：A 停用相关 policy 规则，B 更新 workflow 执行上下文测试但保留接线覆盖，C 只修改新 r1 标注。
2. A 原获授权修改 `tests/fixtures/policy_cases/execution_conflict_non_blocking.json`；另修改 `hard_conflict_over_missing.json` 后由主代理读取并接受：studio 与 outdoor street 可以表达布景，词表不足以证明互斥；应无该冲突，缺失 style.primary 仍阻断。两者记录独立语义理由，不按模型实际输出生成答案、不跳过测试。
3. CLI 工程预览先行，正式 Gate 和真实 smoke 不在本轮执行。不存在通过 Fake 测试推出真实语义正确或用户偏好改善的结论。

## 当前验收状态

**工程实现与集中离线验收完成，非正式发布，Gate A 不作新结论。** 所有子代理停止写入后，主代理运行一次：

```text
$ uv run pytest -q
2215 passed, 3 deselected in 6.73s
```

无失败，无需第二次全量或针对失败补测。默认排除真实 smoke；本轮零真实 Provider/图片调用。测试包含新旧冻结清单哈希验证、旧接口兼容与新增工程闭环。

| 工包 | 完成内容 | 交接 |
|---|---|---|
| A | interpreter.v5 数量/处所纠错，policy.v2 停用四条不成立启发式，registry 合同保留 | `v0_3_r2_A_handoff.md` |
| B | feedback.v4；Submit/Feedback 显式失败 codes；保留原 Intent/PIN/问题；accept/clarify/revise 全部分支过期保护 | `v0_3_r2_B_handoff.md` |
| C | r1 协议/配置/22例/标注/清单，A/B同输入，级联blocked，路径关联Preservation，关键词与来源防护分离，身份/阈值/哈希正式门禁，盲评约束展示 | `v0_3_r2_C_handoff.md` |
| D | CLI、11项Fake端到端；绑定实际显示的摘要；第二版生成失败重试；编译失败安全提示 | `v0_3_step_08_handoff.md` |

r1 Preservation 集成修正：正常无 Prompt 均计不适用，不依赖保留标签；CLEAR 移除旧值；合法 SET/CLEAR 跨无 Prompt 轮累计排除，失败仍留在完成率分母。来源防护绕过当前不可观测→null，不用关键词命中推导。正式模式不能关闭冻结哈希验证；r1 阈值未冻结时拒绝正式 Run。

主代理完成 `GenerationPipeline.retry(session_id, generation_id=None)`：不再要求 CLI 伪造不存在的 generation ID。省略时选本会话最新已落库 PromptArtifact，仍检查 FAILED 状态与有效确认、不重新 compile；已有 ID 接口不变，空字符串仍拒绝。`uv run pytest tests/generation -q` 实测 62 passed。参数化回归覆盖无 Prompt 拒绝、纯 Provider 失败后省略 ID 恢复、过期确认拒绝。

A 曾在 C 尚在编辑期间额外启动全仓检查，报 runner 暂态语法错误；该次不作为集成验收。已要求停止此类跨工包反复检查；最终稳定点由主代理完成上述一次集中验收。

## 下一断点与未完成事项（不阻塞工程预览）

1. 当前断点从 Step06 patch002 前进到 **A/B/C/D 工程集成已验收，待正式评测准备**；无需重新派发已完成工包。
2. 正式评测前先用新版本冻结改善幅度、等待/澄清成本与缺失结果判定，补充语义阶段盲评配对规程（现为同脚本轮序配对，不能冒充不同阶段的语义等价）。再固定干净代码与产物快照。不要仅把配置 status 改成 frozen 来绕过准备工作。
3. 自由处所是否直接解决地点决策尚未扩展标注；四条启发式停用后无当前有效硬冲突规则/真冲突核心案例，需独立语义设计，不把关闭误报等同于通用冲突理解完成。
4. 历史 evaluation/runs、outputs 与 Git dirty 成果保留，未自动提交、搬迁或覆盖。正式 Run 不能用本轮 dirty 工作区冒充干净版本。
5. 后续再执行一次固定真实语义探针、同 r1 协议正式 A/B、双人盲评与 CLI 真实 smoke；据证据裁定 Gate A。缺少证据时不宣称 Go，也不自动 No-Go。

## 使用入口

```bash
uv run python -m visual_intent_agent --demo
```

无需凭据的脚本式演示（非真实 NLU）；真实模式与配置见根 `README.md`。本轮仅交付工程预览，发布说明在 `docs/releases/mvp_v0.3_preview.md`。
