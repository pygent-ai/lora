# Agent 流式输出时间线展示方案

## 1. 设计目标

将一次 Agent 回复展示为按实际发生顺序不断追加的执行时间线，让用户同时看到：

- Agent 当前处于思考、回复还是工具执行阶段；
- 每一轮思考持续了多久；
- 工具调用前后 Agent 给出的说明；
- 工具的运行中、成功和失败状态；
- Agent 最终回复；
- 整个任务的累计处理时间。

展示层不提前判断一段 Assistant 文本是“过程说明”还是“最终回复”。文本先按事件顺序流式展示；如果后面发生工具调用，它自然成为工具调用前的过程说明；如果任务结束，最后一段 Assistant 文本就是最终回复。

## 2. 整体视觉结构

Agent 的一次回复使用一个左对齐的消息气泡，内部包含任务状态标题、分隔线和有序时间线。

```text
L  ┌─────────────────────────────────────────────┐
   │ Processed for 15s                          │
   │ ─────────────────────────────────────────── │
   │                                             │
   │ Thinking for 2s  >                         │
   │                                             │
   │ 好的，我先浏览一下当前项目结构……             │
   │                                             │
   │ 已执行：浏览项目文件  >                      │
   │                                             │
   │ Thinking for 2s  >                         │
   │                                             │
   │ 接下来检查流式事件的处理逻辑……               │
   │                                             │
   │ 已执行：读取 App.jsx  >                     │
   │                                             │
   │ Thinking for 1s  >                         │
   │                                             │
   │ 最终回复正文……                              │
   └─────────────────────────────────────────────┘
```

视觉原则：

- 时间线内容保持轻量，不为每一步嵌套厚重卡片；
- 思考和工具详情默认可折叠；
- Assistant 文本直接使用 Markdown 渲染；
- 运行中使用中性色，成功使用绿色，失败使用红色；
- 不额外显示原始事件名、事件 ID 等技术信息。

## 3. 各阶段展示效果

### 3.1 任务刚开始

发送请求后立即创建 Agent 消息，并显示运行状态：

```text
Processing for 0s
────────────────────

Thinking
```

规则：

- 顶部计时器从请求开始时持续递增；
- 在模型尚未输出 reasoning 时，仅显示 `Thinking`；
- 不再显示 `Waiting for model output...`。

### 3.2 流式输出 reasoning

模型返回 reasoning 字段后，在当前 Thinking 节点下流式拼接：

```text
Processing for 2s
────────────────────

Thinking
正在分析项目结构和前端事件处理逻辑……
```

规则：

- 第一段 reasoning 到达时复用已有的 Thinking 节点；
- 后续 reasoning delta 追加到该节点；
- Thinking 运行期间保持展开；
- reasoning 内容使用普通文本展示，不使用 Markdown 强格式。

### 3.3 开始输出 Assistant 文本

第一段 Assistant 正文到达时，结束当前 Thinking 阶段：

```text
Processing for 3s
────────────────────

Thinking for 2s  >

好的，我先浏览一下当前项目结构……
```

规则：

- Thinking 记录结束时间并展示耗时；
- 已结束的 Thinking 默认折叠；
- 点击 `Thinking for 2s` 可以查看完整 reasoning；
- Assistant 文本在 Thinking 后方按 Markdown 流式拼接；
- 连续的文本 delta 合并到同一个文本节点。

### 3.4 调用工具

如果 Assistant 文本之后出现工具调用，则在当前文本后追加工具节点：

```text
Processing for 5s
────────────────────

Thinking for 2s  >

好的，我先浏览一下当前项目结构……

正在执行：浏览项目文件
```

规则：

- 工具运行期间显示 `正在执行：{工具描述}`；
- 工具描述优先使用面向用户的语义描述，不直接显示原始 JSON 参数；
- 多个并行或连续发起的工具调用按实际调用顺序分别展示；
- 工具运行时可显示轻量的运行状态动画。

### 3.5 工具执行完成

工具完成后，在原位置更新状态，不额外插入结果消息：

```text
Processing for 6s
────────────────────

Thinking for 2s  >

好的，我先浏览一下当前项目结构……

已执行：浏览项目文件  >
```

成功、失败状态分别为：

```text
已执行：浏览项目文件  >
执行失败：运行测试  >
```

规则：

- 成功状态使用绿色；
- 失败状态使用红色；
- 工具详情默认折叠；
- 点击后展示工具参数、结果预览和完整结果入口；
- 大结果按需通过 `result_ref` 加载，避免阻塞流式界面。

### 3.6 工具后的新一轮思考

工具完成后如果再次收到 reasoning，则创建新的 Thinking 节点：

```text
Processing for 9s
────────────────────

Thinking for 2s  >

好的，我先浏览一下当前项目结构……

已执行：浏览项目文件  >

Thinking
继续分析组件之间的关系……
```

每一轮 Thinking 都独立计时，不能与前一轮合并。

### 3.7 最终完成

收到任务完成事件后：

```text
Processed for 15s
────────────────────

Thinking for 2s  >

好的，我先浏览一下当前项目结构……

已执行：浏览项目文件  >

Thinking for 2s  >

接下来检查流式事件的处理逻辑……

已执行：读取 App.jsx  >

Thinking for 1s  >

最终回复正文……
```

规则：

- 顶部标题由 `Processing for x s` 变为 `Processed for x s`；
- 停止总耗时计时；
- 正在运行的 Thinking 节点结束并记录耗时；
- 已结束的 Thinking 和工具详情默认折叠；
- 保留过程说明、工具记录和最终回复的完整顺序；
- 不再把整个执行过程自动折叠成一个不可见区域；
- 最后一段 Assistant 文本作为最终回复直接展示。

## 4. 时间线数据结构

```ts
type AgentTimelineItem =
  | {
      id: string;
      type: "thinking";
      status: "running" | "completed";
      content: string;
      startedAt: number;
      endedAt?: number;
      expanded: boolean;
    }
  | {
      id: string;
      type: "assistant_text";
      status: "streaming" | "completed";
      content: string;
    }
  | {
      id: string;
      type: "tool";
      toolCallId: string;
      name: string;
      description: string;
      status: "running" | "success" | "error";
      arguments?: unknown;
      preview?: string;
      resultRef?: string;
      startedAt: number;
      endedAt?: number;
      expanded: boolean;
    };

interface AgentTimelineMessage {
  id: string;
  role: "assistant";
  status: "running" | "success" | "error" | "cancelled";
  startedAt: number;
  endedAt?: number;
  items: AgentTimelineItem[];
}
```

时间线必须以 `items` 的数组顺序作为唯一展示顺序，不能在渲染阶段把 Thinking、工具和文本重新分组。

## 5. 流事件到界面状态的映射

| 流事件 | 展示操作 |
| --- | --- |
| `chat.started` | 创建运行中的 Agent 时间线并启动总计时 |
| `assistant.reasoning_delta` | 找到当前运行中的 Thinking 节点并追加内容；不存在则创建 |
| `assistant.delta` | 结束当前 Thinking；找到当前流式文本节点并追加内容，不存在则创建 |
| 带 `tool_calls` 的 `runtime.message` | 结束当前文本节点，并按调用顺序追加工具节点 |
| `tool.result` | 按 `tool_call_id` 更新原工具节点的状态和结果信息 |
| 新一轮 `assistant.reasoning_delta` | 在时间线末尾创建新的 Thinking 节点 |
| `chat.completed` | 结束所有运行中节点，记录总耗时并进入成功状态 |
| `chat.error` | 结束运行中节点，在时间线末尾展示错误信息 |
| `chat.cancelled` | 结束运行中节点并标记为已取消 |

## 6. 状态转换规则

### Thinking 节点

```text
不存在
  └─ reasoning_delta → running
                         ├─ reasoning_delta → 追加内容
                         ├─ assistant.delta → completed
                         ├─ tool call → completed
                         └─ chat completed/error/cancelled → completed
```

### Assistant 文本节点

```text
不存在
  └─ assistant.delta → streaming
                        ├─ assistant.delta → 追加内容
                        ├─ tool call → completed
                        ├─ 新 reasoning → completed
                        └─ chat completed → completed，并成为最终回复
```

### 工具节点

```text
tool call → running
              ├─ tool.result success → success
              ├─ tool.result error → error
              └─ chat error/cancelled → error 或 cancelled
```

## 7. 关键判断原则

### 不提前判断最终回复

前端无法在文本刚开始输出时确定它是否为最终回复，因此统一按普通 Assistant 文本展示：

- 后续出现工具调用：该文本成为工具调用前的过程说明；
- 后续出现新 reasoning：该文本成为上一阶段的阶段性说明；
- 直接收到 `chat.completed`：该文本成为最终回复。

### 不合并不同 Thinking 阶段

一次工具调用前后的 reasoning 必须展示为两个独立节点，并分别计算耗时。

### 不重新排序

以下顺序必须原样保留：

```text
Thinking
→ Assistant text
→ Tool
→ Thinking
→ Assistant text
→ Tool
→ Final Assistant text
```

### 最终答案一致性

如果 `chat.completed.payload.final_answer` 与流式累积的最后一个文本节点不同：

- 使用 `final_answer` 修正最后一个 Assistant 文本节点；
- 不创建重复的最终回复；
- 不修改此前已经形成的过程说明节点。

## 8. 异常与边界情况

- **没有 reasoning：** 第一段 Assistant 文本到达时结束空 Thinking，空 Thinking 可以隐藏，避免展示 `Thinking for 0s`。
- **只有 reasoning、没有正文：** 完成时保留 Thinking 记录，并根据完成事件决定是否展示空回复提示。
- **工具没有返回结果：** 任务结束时将仍在运行的工具标记为异常中断，不能自动标记成功。
- **工具失败后继续回答：** 保留失败工具节点，后续 Thinking 和文本继续追加。
- **重复流事件：** 使用事件 `sequence` 和 `tool_call_id` 去重，避免断线恢复后重复追加。
- **长 reasoning：** 运行时限制可视高度并自动滚动；结束后默认折叠。
- **长工具结果：** 仅展示预览，完整结果按需加载。
- **用户切换会话：** 时间线状态继续保存在对应会话缓存中，切回后恢复。

## 9. 验收标准

1. 请求发出后立即展示 `Processing for 0s` 和 `Thinking`。
2. reasoning 字段能够在当前 Thinking 节点中流式拼接。
3. Assistant 正文开始时，上一 Thinking 正确结束并显示独立耗时。
4. 多轮 Thinking 分别生成独立节点，不发生内容或耗时合并。
5. Assistant 文本、工具调用和 Thinking 严格按照事件顺序展示。
6. 工具调用在原位置完成状态更新，不产生重复工具记录。
7. 工具详情和已完成 Thinking 默认折叠，并可以手动展开。
8. 最后一段 Assistant 文本在任务完成后作为最终回复保留。
9. `final_answer` 只修正最后一个文本节点，不重复插入回复。
10. 运行时显示 `Processing for x s`，完成后显示 `Processed for x s`。
11. 工具无结果时不能被自动标记为成功。
12. 断线恢复后不会重复展示已经处理过的时间线节点。
