# Lora 前台 ReAct Agent 能力基线与 Pygent 基础框架诉求

## 文档目标

本文只钉死前台 Agent 的上下文结构和最终组合样式，并据此提出最小的 Pygent 框架诉求。

- 前台 Agent 统一使用 Pygent 标准 `ReActLayer`，不维护第二套 ReAct 循环。
- 所有模型可见输入只分为 `Pre-Model Request Context` 和 `Event-Injected Context` 两类。

## 1. Pre-Model Request Context

每次模型请求发出前准备，只包含 System Prompt 和 System Tools。

### 1.1 System Prompt

- 落点：`Context.system_prompt`。
- 时机：每次模型请求前生成最终有效 System Prompt。
- 可以包含稳定部分和请求级动态部分，但对模型表现为一个 System Prompt。
- 压缩后需要重新生成，不能把旧请求的动态 System Prompt 固化进 continuation。
- 具体内容、模块、顺序和缓存策略由 Lora 管理。

### 1.2 System Tools

- 落点：`Context.tools`。
- 时机：每次模型请求前确定本次真正可见的 Tool Definitions。
- Tool name、description、schema 和启用策略由 Lora 管理。
- Pygent 负责把有效工具发送给模型，并执行合法 tool calls。
- System Tools 不进入 conversation messages。

## 2. Event-Injected Context

由 Agent 运行期间的事件产生，并以 conversation message 进入上下文。

### 2.1 Initial User Input

- 落点：本轮初始 `UserMessage`。
- 时机：用户开始一个新 turn。
- 它是用户的真实输入，不属于 system reminder。
- 原始用户文本需要独立保留用于审计。

### 2.2 Mid-run User Steering

- 落点：新的独立 `UserMessage`。
- 时机：Agent execution 运行过程中随时到达。
- 它是用户追加的真实要求，不属于 System Prompt 或 User-side System Reminder。
- 多条 steering 按接收顺序进入当前 execution，不能丢失或重复。
- 如果 execution 已结束，则作为下一个 turn 的 Initial User Input。

### 2.3 User-side System Reminder

系统生成、通过 user role 交给模型的运行时提醒。Lora 使用 `UserMessage.kind / metadata` 标记类别，不要求 Pygent 增加 Reminder 消息类型。

#### Initial CLI/Skill

- 时机：Initial User Input 入场时。
- 位置：追加到当前 Initial User Input 的 `UserMessage`。
- 内容：初始可用的 CLI 和 Skill。

#### Compression Request

- 时机：开始上下文压缩时。
- 位置：压缩模型请求中的独立 `UserMessage`。
- 作用：要求压缩任务生成 continuation；不直接写进前台正常对话历史。

#### Context Continuation

- 时机：上下文压缩成功后。
- 位置：压缩后模型投影中的 replacement `UserMessage`。
- 作用：替换旧消息投影，但不删除完整历史，也不替代重新生成的 System Prompt 和 System Tools。

### 2.4 Tool-side System Reminder

工具执行后产生，并附在当前 `ToolMessage`。Lora 负责生成和消费，Pygent 不需要理解 Reminder 语义。

#### New CLI

- 时机：工具执行后发现新的 CLI。
- 位置：产生该发现的当前 `ToolMessage`。
- 内容：只包含本次新发现的 CLI。

#### New Skill

- 时机：工具执行后发现新建或变更的 Skill。
- 位置：产生该发现的当前 `ToolMessage`。
- 内容：只包含本次新发现的 Skill，并消费一次。

## 上下文组装不变量

1. 每次模型调用前重新准备 System Prompt 和 System Tools。
2. Initial User Input 和 Mid-run User Steering 始终是真实 `UserMessage`。
3. Initial CLI/Skill 追加到当前初始 UserMessage。
4. Compression Request 是独立 UserMessage；Context Continuation 是 replacement UserMessage。
5. New CLI 和 New Skill reminder 附在产生它们的 ToolMessage。
6. User-side 和 Tool-side Reminder 不重复进入 System Prompt。
7. 压缩只替换模型消息投影，不删除完整历史。

## 最小 ReAct 流程

```text
Initial User Input
  -> 准备 System Prompt / System Tools
  -> 模型调用
  -> tool calls
  -> 工具执行
  -> ToolMessage / Tool-side System Reminder
  -> 注入待处理 Mid-run User Steering
  -> 重新准备下一次模型请求
  -> 直到模型返回最终答案
```

## Pygent 基础框架诉求

Pygent 0.2.19 现有的 `Message`、`Context`、`Module`、`ModelCallLayer`、`ToolCallLayer` 和 `ReActLayer` 继续作为主体，不需要增加 Prompt Framework。

只需要补充三个基础能力：

### 1. Execution Inbox

- 通过 `ExecutionHandle` 向运行中的 execution 投递 Mid-run User Steering。
- 输入在 Agent 尚未等待时也能排队，不会丢失。
- 同一 execution 内有序、可去重、可恢复，且只消费一次。

### 2. Durable `select()`

允许 Model Module 等待“模型响应”和“新的 execution input”中的第一个完成者，使 Lora 能在模型边界把 steering 转成新的 `UserMessage`，不需要修改标准 `ReActLayer`。

### 3. Final Model Request Snapshot

在调用 provider 前暴露最终有效请求，至少包含：

- System Prompt
- current message 和历史 messages
- effective tools
- model/generation settings
- request id/digest

Pygent 只暴露最终请求，不理解其中的 Prompt 或 Reminder 分类。

可选增加 `pipe(...)` 或 `.then(...)` 来简化 Module 顺序组合，但这不是核心能力。

## Pygent 不需要提供的抽象

- Prompt Registry / Prompt Profile。
- System Reminder、User-side Reminder、Tool-side Reminder 类型。
- CLI、Skill、Memory、Compression 抽象。
- Lora 专用 ReAct hooks 或另一套 Agent 状态机。

这些业务概念继续由 Lora 使用现有 `Context` 和 `Message.kind / data / metadata` 表达。

## 验收标准

1. 所有模型可见输入都能唯一归入本文规定的两大类和对应子类。
2. 前台 Agent 只使用标准 Pygent `ReActLayer`。
3. Mid-run User Steering 作为真实 `UserMessage` 进入当前 execution，并且不丢失、不重复。
4. Reminder 只出现在规定的 UserMessage 或 ToolMessage 位置。
5. 压缩后重新准备 System Prompt 和 System Tools，同时保留完整历史。
6. 每次模型调用都能观察最终实际使用的 System Prompt、messages 和 tools。
7. 任何提示词正文或注入语义变化继续单独确认。
