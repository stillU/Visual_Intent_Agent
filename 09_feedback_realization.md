# Step 09：FeedbackEngine、RealizationState 与多轮修改

## 派发给 Agent 的任务

实现针对真实 GenerationArtifact 的用户反馈解释、Realization 的继承与失效，以及连续 3～5 轮局部修改闭环。

## 前置依赖

- Step 08 已验收；
- Feedback 能精确关联 PromptArtifact、GenerationArtifact、VisualIntent 和当前 RealizationState。

## 目标

实现 P3 完整链路：

```text
User Feedback
    ↓
FeedbackEngine
    ↓
FeedbackResult / Candidate IntentDelta
    ↓
Validator
    ↓
Reducer
    ↓
DecisionPolicy
    ↓
Confirmation
    ↓
PromptEngine + Realization Carry
    ↓
New GenerationArtifact
```

## FeedbackEngine 输入输出

输入：

```text
feedback_text
GenerationArtifact
PromptArtifact
VisualIntent
RealizationState
```

输出：

```text
FeedbackResult {
    decision: accept | revise | clarify
    candidate_delta
    preserve_paths
    compile_feedback
    issues
    evidence_refs
}
```

FeedbackEngine 只提出 Candidate Delta 和保持范围，不能直接修改状态。

## 反馈处理规则

### accept

用户明确接受当前图片或结束任务，Workflow 可进入 `COMPLETED`。

### revise

用户提出明确可映射修改，例如“人物不变，只把镜头拉远”：

```text
preserve_paths: subject.*
candidate_delta: SET composition.framing
```

候选仍必须经过 Validator、Reducer、DecisionPolicy 和重新确认。

### clarify

用户反馈不足以形成明确 Delta，例如“背景不好”。系统不得猜“换成海边”，而应针对 environment 提问。

## RealizationState

只记录 delegated path 上系统要求模型实现的具体选择：

```text
RealizationState {
    realization_id
    based_on_intent_revision
    values: [
        {
            path
            value
            source: user_delegated
            first_prompt_artifact_id
            carry_policy: preserve_until_invalidated
            status: active | invalidated
        }
    ]
}
```

Realization 不是图片事实。

## 继承与失效

默认继承 active Realization，除非：

- 用户修改同一路径；
- DecisionPolicy dependency 发生变化；
- 新 Intent 与旧 Realization 冲突；
- 用户 CLEAR 对应 delegated path。

失效必须产生显式记录，不能覆盖或删除历史 value。

## 两层 Preservation

### Intent Preservation

用户没有授权修改时，Intent 不改变。该不变量由代码保证。

### Image Preservation

新图片中的人物或属性是否真的没变化，需要比较实际 GenerationArtifact。MVP 只测量，不承诺严格视觉一致性。

## 实现步骤

1. 定义 FeedbackResult 和 Feedback Provider interface。
2. 实现反馈结构化解释。
3. 把 preserve 表达转换成合法 PIN 或 preserve_paths。
4. 复用 Step 02～03 完成状态更新。
5. 实现 Realization carry evaluator。
6. 实现 Realization invalidation 记录。
7. 把修改流程接回 Confirmation 和 PromptEngine。
8. 建立 3～5 轮场景测试。

## 必测场景

- “人物不变，只把镜头拉远”只修改 framing。
- “只改光线”保持其他 Intent 路径不变。
- “背景不好”进入 clarify，不自动 SET location。
- delegated clothing 在只改镜头时继续复用原 Realization。
- 用户修改 clothing 后旧 clothing Realization 失效。
- dependency 变化导致相关 Realization 失效。
- 每次重要修改都要求重新确认。
- 新一轮 Feedback 绑定新的 GenerationArtifact。
- 历史 Prompt、Generation 和 Realization 均不覆盖。
- 3～5 轮后未修改路径仍保持。

## 交付物

- FeedbackEngine；
- FeedbackResult；
- Realization carry / invalidation；
- 修改 Workflow；
- 多轮场景 fixture；
- P3 端到端测试和结果记录。

## 验收条件

- 连续 3～5 轮局部修改闭环可运行；
- 状态层未授权字段变化为零；
- delegated choice 在未失效时稳定继承；
- 模糊反馈不会被猜成具体设计；
- 所有新图片和反馈均可追溯。

## 禁止范围

- 不实现图片编辑或身份锁定；
- 不声称图片视觉上保证不变；
- 不实现 Reference Image；
- 不跳过重新确认；
- 不接 KnowledgeEngine。

## 给下一步的输入

Step 10 需要获得：

- 完整 P0～P3 系统；
- 多轮场景 fixture；
- 每层 Artifact 与指标采集点；
- Direct LLM baseline 可使用的同一 Provider 配置。

