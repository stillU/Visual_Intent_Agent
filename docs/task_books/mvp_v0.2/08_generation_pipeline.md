# Step 08：图像生成闭环与 GenerationArtifact

## 派发给 Agent 的任务

实现唯一目标图像模型的 Adapter，把已确认且已编译的 PromptArtifact 变成真实图片，并保存 GenerationArtifact。

## 前置依赖

- Step 07 已验收；
- PromptArtifact、目标模型与参数已固定；
- Repository 可以保存 Artifact；
- Workflow 已具备 `GENERATING / WAITING_REVIEW / FAILED` 状态。

## 目标

补齐真实生成闭环：

```text
PromptArtifact
      ↓
ImageGenerationAdapter
      ↓
GenerationArtifact
      ↓
WAITING_REVIEW
```

## ImageGenerationAdapter 边界

Adapter 只负责：

- 把 PromptArtifact 转成 Provider 请求；
- 调用唯一图像 Provider；
- 规范化响应；
- 分类 Provider 错误；
- 返回输出引用和可审计元数据。

Adapter 不修改 Intent、不重新编译 Prompt、不自行重写用户要求。

## GenerationArtifact

```text
GenerationArtifact {
    generation_id
    prompt_artifact_id
    target_model
    model_version
    parameters
    seed?
    output_refs[]
    provider_request_id?
    created_at
}
```

如果 Provider 不返回 seed 或 request ID，字段可为空；不得伪造。

## Workflow 规则

1. 进入生成前再次验证 Confirmation 与 PromptArtifact 的 revision 链。
2. 状态从 `WAITING_CONFIRMATION` 经有效确认进入 `GENERATING`。
3. Provider 成功后先保存 GenerationArtifact，再进入 `WAITING_REVIEW`。
4. Provider 失败时保存错误记录并进入 `FAILED`。
5. Retry 必须引用原 PromptArtifact，不能偷偷重新编译不同 Prompt。
6. 如果用户修改 Intent，则回到 `UNDERSTANDING`，不能继续复用旧确认。

## 输出保存

`output_refs` 使用项目选择的稳定引用方式。MVP 只要求能重新定位生成结果，不建设媒体资产平台。

必须保存足够信息以回答：

- 哪个 Prompt 生成了这张图；
- 使用哪个模型版本和参数；
- 哪次 Provider 请求产生；
- 用户后续反馈针对哪次生成。

## 实现步骤

1. 定义唯一 Image Provider interface。
2. 实现一个实际 Provider adapter。
3. 实现请求 / 响应映射。
4. 实现 timeout、限流、Provider 错误分类。
5. 实现 GenerationArtifact 持久化。
6. 集成状态迁移和最小 retry。
7. 用 Fake Provider 完成默认集成测试。
8. 用实际 Provider 完成受控 smoke test。

## 必测场景

- 未确认时无法调用 Provider。
- PromptArtifact 与当前 Confirmation 不匹配时拒绝生成。
- 成功调用后 Artifact 完整且状态为 `WAITING_REVIEW`。
- Provider 失败后状态为 `FAILED`，没有伪造 Artifact。
- Retry 使用同一 PromptArtifact。
- 多次生成产生不同 generation ID，不覆盖历史。
- Feedback 外键可精确引用某个 GenerationArtifact。

## 交付物

- Image Provider interface 和一个实际 Adapter；
- Fake Provider；
- GenerationArtifact 模型与持久化；
- 生成和 retry 用例；
- P2 端到端测试。

## 验收条件

- 从确认到真实图片的链路可运行；
- 每次成功调用都有可追踪 GenerationArtifact；
- 失败和重试行为可观察；
- 生成调用不修改 Intent / Prompt；
- 仅支持一个目标图像模型。

## 禁止范围

- 不实现多模型路由；
- 不实现图片理解或图片质量自动判断；
- 不引入队列集群；
- 不实现 Reference Image；
- 不把 output 当作 RealizationState 的事实来源。

## 给下一步的输入

Step 09 需要获得：

- GenerationArtifact；
- 精确关联生成结果的反馈入口；
- retry 和失败状态；
- Fake Image Provider 测试夹具。
