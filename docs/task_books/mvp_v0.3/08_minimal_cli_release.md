# Step 08：最小 CLI 与 v0.3 版本验收

## 目标

在 Gate A 最终为 Go 后，为现有完整闭环提供一个最小、可操作的命令行入口，并完成 v0.3 版本验收。CLI 只负责接线和交互，不复制领域规则。

## 启动条件

- Step 05 或 Step 07 的最终 Gate A 结论为 `Go`。
- Go 报告、运行 Artifact 和对应代码 commit 已固定。

## 最小用户流程

```text
启动会话
→ 输入图像需求
→ 回答必要澄清
→ 查看 Intent 变更摘要并确认
→ 生成图片并显示输出位置
→ 接受结果或提交修改反馈
→ 重新确认并生成下一版
```

## 实施内容

1. 提供 `uv run python -m visual_intent_agent` 入口。
2. 复用现有 Settings、Repository、WorkflowService、PromptEngine、GenerationPipeline 和 ReviewService。
3. 根据状态机显示下一步允许的操作，不在 CLI 中复制状态判断。
4. 确认前显示 diff-first 摘要；任何重要修改必须重新确认。
5. 展示生成文件路径和 Artifact ID，不在终端输出密钥或完整 Provider 凭据。
6. 对配置缺失、Provider 失败和非法状态给出简洁、可恢复的提示。
7. 增加全 Fake CLI 端到端测试，不让默认测试调用真实 Provider。
8. 更新根 README 的最小运行说明和当前版本状态。

## 交付物

- `visual_intent_agent/__main__.py`
- 必要的轻量 CLI 模块
- `tests/cli/` 离线端到端测试
- 根 README 最小使用说明
- `docs/handoffs/v0_3_step_08_handoff.md`
- `docs/releases/mvp_v0.3.md`

## 验收条件

- 新用户按 README 可启动并完成最小完整流程。
- CLI 不绕过确认、Revision、Artifact 或状态机。
- 多轮反馈后能够重新确认并生成新 Artifact。
- 默认全量测试通过，真实 smoke 由显式命令触发。
- 发布记录引用最终 Gate A 报告，并明确保留限制。

## 禁止范围

- 不实现 Web UI、账户、权限、队列、云存储或部署平台。
- 不把 CLI 交互逻辑下沉为新的业务规则。
- 不在本版本实现 KnowledgeEngine。
