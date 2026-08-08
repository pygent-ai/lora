# Pygent Agent Runtime Protocol 需求

## 背景

Lora 当前有三种面向用户的对话入口：

- CLI: `lora chat` 和 `lora chat --message "..."`
- 本地 API: `POST /chat/stream`
- 桌面端 Electron/React UI

这三种入口的外层形态不同，但最终都会进入 Lora 的 `AgentRuntimeAdapter.run_turn(...)`。`AgentRuntimeAdapter` 再创建或接收一个 agent，并消费 agent 的流式输出。

当前默认 agent 是 `LoraAgent`。`LoraAgent` 基于 pygent 的模型、消息和工具能力实现 ReAct 风格执行循环，包括：

- 组合系统提示词。
- 调用 `llm.stream_forward(...)`。
- 读取 assistant delta。
- 识别 assistant message 中的 tool calls。
- 调用 pygent 工具。
- 将 tool result 追加回 pygent context。
- 继续下一轮模型请求，直到没有 tool calls。

`AgentRuntimeAdapter` 则把这个 agent 执行过程接入 Lora 自己的 session、case run 和 trace 体系。它的职责已经超过“调用 agent”本身，承担了大量运行时边界工作。

因此，Lora 希望向 pygent 提出一个更稳定的 Agent Runtime Protocol 诉求：pygent 不需要接管 Lora 的 session/case/trace，但需要提供标准化的 agent 上下文、事件、工具生命周期和运行结果协议，减少 Lora 对 pygent 内部消息形态的适配代码。

## 当前实现中的职责拆分

### `AgentRuntimeAdapter` 当前职责

`AgentRuntimeAdapter` 位于 Lora 工程边界，主要承担这些职责：

1. 创建 `RuntimeContext`，将 `AgentSession.history` 暴露给 agent。
2. 生成或接收 `turn_id`。
3. 调用 agent 的 `start_run(case_run_ref, turn_id)` 钩子。
4. 包装用户消息，例如追加 `<user-message>`、`user_identity` 和 initial reminder。
5. 将用户消息写入 `EventStore`，事件类型为 `conversation.user_message`。
6. 写入 `model.request` 事件，记录 agent、模型、最大步数、历史消息数等元数据。
7. 调用 agent 的 `stream(...)` 或 `run(...)`。
8. 将 agent 输出标准化为 `RuntimeMessage`。
9. 区分 assistant delta、assistant message、tool message。
10. 合并 assistant delta，避免把 token 级片段写入 session history。
11. 将 assistant/tool/runtime message 写入 session history 和 trace。
12. 将 assistant delta 和 runtime message 回调给 CLI/API/UI。
13. 捕获异常，写入 `runtime.error`。
14. 更新并保存 session history 和 metadata。
15. 写入 `model.response` 和 `context.checkpoint`。
16. 返回统一结果：`session_id`、`case_id`、`case_run_id`、`turn_id`、`status`、`final_answer`、`error`、`message_count`。
17. 支持 `run_turn(...)` 和 `run_case(...)` 两种 Lora 执行场景。

其中 1、2、4、5、6、10、11、12、13、14、15、16、17 都是 Lora 的 runtime 编排和证据链职责，不适合直接上提给 pygent。

### `LoraAgent` 当前职责

`LoraAgent` 更接近实际 agent 执行器，主要承担这些职责：

1. 持有 pygent LLM client 和 tool manager。
2. 注册默认工具。
3. 构建模型请求 prompt。
4. 进行 context compression。
5. 调用 `llm.stream_forward(pygent_context, tools=...)`。
6. 将模型 chunk 转换为 assistant delta。
7. 从 assistant message 中读取 tool calls。
8. 通过 `ToolInterceptor` 调用工具。
9. 将 tool result 转为 `ToolMessage` 并追加进 pygent context。
10. 在工具后续轮次继续请求模型。
11. 在没有 tool calls 时结束执行。

其中 5、6、7、9、10、11 属于通用 agent runtime 协议能力，适合 pygent 标准化。

8 比较特殊：Lora 需要 `ToolInterceptor` 记录 trace、文件影响、diff 和安全元数据。因此工具实际执行可以仍由 Lora 包装，但 pygent 应提供清晰的工具调用生命周期事件，使 Lora 可以插入自己的 interceptor。

## 问题定义

当前 Lora 与 pygent 的边界存在以下问题：

1. **消息形态不稳定**

   Lora 需要兼容 dict、`RuntimeMessage`、pygent `BaseMessage`、assistant chunk、tool message 等多种输出形态。`AgentRuntimeAdapter._normalize_output(...)` 负责把这些对象转成 Lora 自己的 `RuntimeMessage`。

2. **delta 与最终消息语义不够明确**

   当前模型流式输出产生 assistant delta，随后 pygent context 中会出现最终 assistant message。Lora 需要自己判断：

   - delta 是否应该只回调给 UI。
   - delta 是否需要合并成最终 assistant message。
   - 最终 assistant message 是否已经包含了 delta 内容。
   - 什么时候应该写入 history。

3. **tool calls 生命周期需要上层自行推断**

   Lora 需要从 assistant message 中提取 tool calls，然后调用工具，再构造 `ToolMessage` 追加到 pygent context。工具调用开始、工具调用完成、工具调用失败、tool_call_id 映射等事件没有统一的 agent runtime event 协议。

4. **工具执行扩展点不够明确**

   Lora 希望保留 `ToolInterceptor`，用于记录 `tool.call`、`tool.result`、文件读写影响、snapshot 和 diff。但如果 pygent agent runtime 自己直接执行工具，上层难以插入完整观测和安全策略。

5. **运行结果缺少统一协议**

   当前 `AgentRuntimeAdapter` 自己拼装 `final_answer`、`status`、`error`、`message_count`。pygent 没有一个标准 `AgentRunResult`，用于表达一次 agent turn 的最终状态和统计信息。

6. **不同上层入口重复处理流式事件**

   CLI、API 和 UI 都需要理解 assistant delta、runtime message、tool result 等事件。如果 pygent 的 agent runtime event 形态稳定，Lora 只需要做一次协议映射。

## 需求目标

pygent 需要提供稳定的 Agent Runtime Protocol，使 Lora 这类上层框架可以：

1. 用统一上下文对象启动一次 agent turn。
2. 以统一事件流消费 agent 执行过程。
3. 明确区分 assistant delta、assistant final message、reasoning delta、tool call、tool result、error、usage。
4. 在工具执行前后插入上层自定义 interceptor。
5. 不依赖 pygent 内部 message/chunk 对象结构。
6. 获取结构化运行结果。
7. 继续保留上层自己的 session、trace、case run、evaluation 和 UI 协议。

最低目标：

- pygent agent runtime 对外只暴露稳定事件对象，不要求 Lora 解析多种内部 message 形态。
- 每个事件都有明确类型、run id、turn id、sequence、payload。
- tool call 和 tool result 有稳定 ID，能够跨模型消息、工具执行和 trace 串联。
- Lora 可以选择由 pygent 默认执行工具，也可以注入自定义 tool executor。

## 非目标

- 不要求 pygent 管理 Lora 的 `.lora/sessions/...` 目录。
- 不要求 pygent 写 Lora 的 `EventStore`。
- 不要求 pygent 理解 Lora 的 case、regression、repair、diff 产物。
- 不要求 pygent 接管 Lora 的 user message wrapper、initial reminder、prompt module、context checkpoint。
- 不要求一次性重写 Lora 的 `AgentRuntimeAdapter`。
- 不要求改变 pygent 现有 `BaseAgent`、`BaseMessage` 的内部实现，只要求提供稳定的外部 protocol。

## 推荐协议设计

### 1. Agent runtime interface

建议 pygent 提供一个高层 runtime interface：

```python
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


class AgentRuntimeProtocol(Protocol):
    async def stream_turn(
        self,
        context: "AgentRuntimeContext",
        *,
        options: "AgentRunOptions | None" = None,
        tool_executor: "ToolExecutor | None" = None,
    ) -> AsyncIterator["AgentRuntimeEvent"]:
        ...
```

说明：

- `stream_turn(...)` 只表示一次用户 turn 的执行。
- `context` 由上层传入，包含模型可见 history、system prompt 和 metadata。
- `options` 包含 `max_steps`、`request_type`、模型配置覆盖等运行参数。
- `tool_executor` 是可选扩展点。未传入时 pygent 使用默认工具执行；传入时由上层执行工具。
- 返回值是稳定的 `AgentRuntimeEvent` 流。

### 2. Agent runtime context

```python
@dataclass(slots=True)
class AgentRuntimeContext:
    session_id: str
    turn_id: str
    messages: list["AgentMessage"]
    system_prompt: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

```python
@dataclass(slots=True)
class AgentMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list["AgentToolCall"] = field(default_factory=list)
    reasoning_content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

要求：

- `messages` 是模型上下文的稳定表示。
- pygent 可以内部转换成自己的 `BaseContext` 和 `BaseMessage`，但上层不需要知道内部结构。
- `metadata` 可携带 Lora 的 `case_id`、`case_run_id`、`workspace_root`、`request_type` 等信息。
- `system_prompt` 可以为 `None`，由 agent 自行生成；也可以由上层完全指定。
- 如果 pygent 需要写回上下文，应通过 event 表达，而不是直接要求上层读取内部 context。

### 3. Agent run options

```python
@dataclass(slots=True)
class AgentRunOptions:
    max_steps: int = -1
    stream_reasoning: bool = True
    stream_assistant_delta: bool = True
    emit_tool_events: bool = True
    emit_usage_events: bool = True
    request_type: Literal["agent_turn", "case_run", "summary", "evaluation"] = "agent_turn"
```

要求：

- `max_steps=-1` 表示无限循环，直到模型不再请求工具。
- `max_steps <= 0` 且不是 `-1` 应抛出结构化参数错误。
- `request_type` 只是运行语义标记，pygent 不必理解 Lora case，但应透传到 metadata 或 request hooks。

### 4. Agent runtime event

建议事件对象：

```python
@dataclass(slots=True)
class AgentRuntimeEvent:
    type: str
    sequence: int
    session_id: str
    turn_id: str
    payload: dict[str, Any] = field(default_factory=dict)
```

必须支持的事件类型：

| type | 时机 | payload 要求 |
| --- | --- | --- |
| `run.started` | turn 开始 | `agent_name`, `model_name`, `max_steps` |
| `model.request` | 每次模型请求前 | `message_count`, `tool_names`, `step_index` |
| `assistant.reasoning_delta` | reasoning 流式片段 | `delta` |
| `assistant.delta` | assistant 正文流式片段 | `delta` |
| `assistant.message` | 完整 assistant message | `message`, `content`, `tool_calls` |
| `tool.call` | 准备执行工具 | `tool_call_id`, `name`, `arguments` |
| `tool.result` | 工具执行完成 | `tool_call_id`, `status`, `result`, `error`, `error_type` |
| `model.response` | 每次模型响应完成 | `step_index`, `usage`, `finish_reason` |
| `run.completed` | turn 成功结束 | `final_answer`, `message_count`, `step_count`, `usage` |
| `run.error` | turn 异常结束 | `error`, `error_type`, `partial_final_answer` |
| `run.cancelled` | turn 被取消 | `reason`, `partial_final_answer` |

可选事件类型：

| type | 时机 | payload 要求 |
| --- | --- | --- |
| `context.compaction_started` | 上下文压缩开始 | `message_count`, `reason` |
| `context.compaction_completed` | 上下文压缩完成 | `before_count`, `after_count`, `summary_message` |
| `context.compaction_failed` | 上下文压缩失败 | `error`, `error_type` |
| `usage.updated` | token usage 增量可用 | `usage` |

事件要求：

- `sequence` 从 1 开始递增。
- 同一个 turn 内 `sequence` 必须稳定、有序、无重复。
- `assistant.delta` 不应自动等价于可持久化 message。
- `assistant.message` 表示可以写入 conversation history 的完整 assistant message。
- 如果完整 assistant message 的 content 等于所有 delta 拼接结果，payload 仍应包含完整 content，便于上层去重。
- `tool.result.status` 必须为 `"success"` 或 `"error"`。
- 工具级失败不得只作为普通字符串 result 返回。

### 5. Tool executor 协议

Lora 需要保留 `ToolInterceptor`，因此 pygent 应支持上层注入工具执行器。

```python
class ToolExecutor(Protocol):
    async def execute_tool(
        self,
        call: "AgentToolCall",
        context: "ToolExecutionContext",
    ) -> "AgentToolResult":
        ...
```

```python
@dataclass(slots=True)
class AgentToolCall:
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    provider_call_id: str | None = None
    raw: dict[str, Any] | None = None
```

```python
@dataclass(slots=True)
class ToolExecutionContext:
    session_id: str
    turn_id: str
    step_index: int
    workspace_root: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

```python
@dataclass(slots=True)
class AgentToolResult:
    tool_call_id: str
    status: Literal["success", "error"]
    content: str
    result: Any = None
    error: str | None = None
    error_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

要求：

- pygent 负责识别模型返回的 tool calls，并为每个 call 发出 `tool.call`。
- 如果上层传入 `tool_executor`，pygent 调用该 executor。
- 如果未传入，pygent 使用自己的默认工具执行路径。
- 工具结果必须通过 `AgentToolResult` 表达。
- pygent 将工具结果转换为模型可见 tool message，但转换规则应稳定。
- `tool_call_id` 必须从 assistant tool call 贯穿到 tool result 和 tool message。
- 当 tool executor 抛出异常时，pygent 应转换为 `tool.result(status="error")`，并继续由 agent 策略决定是否把错误 tool message 送回模型。

### 6. Tool message 转换规则

pygent 应提供一个固定的工具结果到模型消息的转换函数：

```python
def tool_result_to_message(result: AgentToolResult) -> AgentMessage:
    ...
```

建议规则：

- `role="tool"`
- `tool_call_id=result.tool_call_id`
- `content` 为 JSON 字符串或稳定文本协议。
- 成功时包含：

```json
{
  "status": "success",
  "result": "...",
  "metadata": {}
}
```

- 失败时包含：

```json
{
  "status": "error",
  "error": "File does not exist",
  "error_type": "FileNotFoundError",
  "metadata": {}
}
```

要求：

- 失败不能只靠 `"错误：..."` 或 `"error: ..."` 这样的自然语言字符串表达。
- 如果 result 本身是字符串，也应包进结构化对象，避免调用方无法判断状态。
- 为兼容旧模型上下文，可以允许 `content` 是字符串，但必须有可解析的结构化字段。

### 7. Final result 协议

虽然主通道是事件流，pygent 仍应提供从事件流归约出来的最终结果形态：

```python
@dataclass(slots=True)
class AgentRunResult:
    session_id: str
    turn_id: str
    status: Literal["passed", "error", "cancelled"]
    final_answer: str
    messages: list[AgentMessage]
    error: str | None = None
    error_type: str | None = None
    message_count: int = 0
    step_count: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
```

建议同时提供 helper：

```python
async def run_turn(...) -> AgentRunResult:
    ...
```

这个 helper 可以内部消费 `stream_turn(...)`。Lora 的 API/桌面端仍可以直接使用事件流；CLI 单轮或测试可以使用 `run_turn(...)`。

### 8. Message normalization helper

为迁移现有生态，pygent 应提供标准转换函数：

```python
def to_agent_message(value: Any) -> AgentMessage:
    ...

def to_agent_event(value: Any) -> AgentRuntimeEvent:
    ...
```

必须支持：

- pygent `BaseMessage`
- pygent assistant chunk
- pygent tool message
- OpenAI compatible message dict
- OpenAI compatible stream chunk

Lora 的目标是逐步删除或收缩自己的 `_normalize_output(...)`，只保留 Lora 事件映射。

## Lora 侧期望迁移方案

### 阶段 1：pygent 增加协议对象，不改变现有 agent 行为

pygent 先新增：

- `AgentRuntimeContext`
- `AgentMessage`
- `AgentRuntimeEvent`
- `AgentRunOptions`
- `AgentToolCall`
- `AgentToolResult`
- `ToolExecutor`
- message/event normalization helper

现有 `BaseAgent` 和 `llm.stream_forward(...)` 保持兼容。

### 阶段 2：pygent 提供默认 ReAct runtime

pygent 提供一个默认 ReAct runtime，实现：

1. 读取 `AgentRuntimeContext.messages`。
2. 构造内部 pygent context。
3. 调用模型 stream。
4. 发出 assistant delta。
5. 读取完整 assistant message。
6. 发出 assistant message。
7. 读取 tool calls。
8. 发出 tool call。
9. 调用 `ToolExecutor` 或默认工具执行器。
10. 发出 tool result。
11. 追加 tool message。
12. 继续下一步，直到没有 tool calls 或达到 `max_steps`。

### 阶段 3：LoraAgent 收缩为 Lora 特化配置层

Lora 保留：

- prompt composition
- context compression 策略
- tool registry 白名单
- `ToolInterceptor`
- Lora-specific reminders
- EventStore 映射
- session/case/run 持久化

LoraAgent 可以把通用 ReAct 循环委托给 pygent runtime，只保留 Lora 需要注入的 hooks。

### 阶段 4：AgentRuntimeAdapter 只做 Lora 边界编排

`AgentRuntimeAdapter.run_turn(...)` 迁移后大致变成：

```python
async def run_turn(...):
    store = EventStore(case_run_ref)
    context = build_pygent_runtime_context(session, user_input, turn_id)
    tool_executor = LoraToolExecutor(interceptor=ToolInterceptor(...))

    final = AgentRunAccumulator()
    async for event in agent_runtime.stream_turn(
        context,
        options=AgentRunOptions(max_steps=config.max_steps),
        tool_executor=tool_executor,
    ):
        lora_event = map_pygent_event_to_lora_event(event)
        store.append(...)
        update_session_history_if_persistable(event, session)
        emit_cli_or_api_callbacks(event)
        final.apply(event)

    session_manager.save(session)
    return final.to_lora_result()
```

迁移目标：

- `AgentRuntimeAdapter` 不再识别 pygent chunk 内部结构。
- `AgentRuntimeAdapter` 不再自己推断 tool calls。
- `AgentRuntimeAdapter` 不再自己构造 tool message。
- `AgentRuntimeAdapter` 仍负责写 Lora trace 和保存 session。

## Lora 侧事件映射建议

| pygent event | Lora event |
| --- | --- |
| `run.started` | `model.request` 前置元数据或新的 `runtime.started` |
| `model.request` | `model.request` |
| `assistant.reasoning_delta` | API: `assistant.reasoning_delta`; trace 可选 |
| `assistant.delta` | API/CLI streaming callback；trace 可选 |
| `assistant.message` | `conversation.assistant_message` |
| `tool.call` | `tool.call` |
| `tool.result` | `tool.result` 或 API `tool.result` |
| `model.response` | `model.response` |
| `run.completed` | `context.checkpoint` + caller result |
| `run.error` | `runtime.error` + `model.response(status="error")` |
| `run.cancelled` | `runtime.cancelled` |

注意：

- Lora 可以继续选择不把 token delta 写入 `messages.jsonl`。
- Lora 应只把 `assistant.message` 和 `tool.result` 对应的模型可见消息写入 session history。
- `assistant.reasoning_delta` 默认不进入 session history，除非配置要求保留。

## 与现有 `AgentRuntimeAdapter` 的边界

迁移后，以下逻辑仍留在 Lora：

1. `wrap_user_message(...)`
2. `_render_initial_user_reminder(...)`
3. `EventStore.append(...)`
4. `SessionManager.save(...)`
5. `case.session.carry_context` 逻辑
6. `CaseRunResult` 生成
7. `Evaluator` 和 `FailureAnalyzer`
8. `ToolInterceptor` 的文件影响记录
9. `.lora/sessions/...` 目录结构
10. API SSE resume 和桌面端消息渲染

以下逻辑希望沉到 pygent：

1. 标准 agent context object。
2. 标准 agent event object。
3. assistant delta 和 assistant final message 的稳定语义。
4. tool call 提取。
5. tool result 到 tool message 的转换。
6. ReAct max steps 循环。
7. usage/finish_reason 标准化。
8. pygent 内部 message/chunk 到外部协议对象的转换。

## 具体实现细节

### 1. 事件序号生成

pygent runtime 内部维护递增 sequence：

```python
class AgentEventEmitter:
    def __init__(self, session_id: str, turn_id: str):
        self.session_id = session_id
        self.turn_id = turn_id
        self.sequence = 0

    def event(self, type: str, payload: dict[str, Any]) -> AgentRuntimeEvent:
        self.sequence += 1
        return AgentRuntimeEvent(
            type=type,
            sequence=self.sequence,
            session_id=self.session_id,
            turn_id=self.turn_id,
            payload=payload,
        )
```

要求：

- 同一个 `stream_turn(...)` 调用内 sequence 只能由一个 emitter 生成。
- resume 不是 pygent 的必需能力；Lora API 可以基于自己收到的 sequence 实现 SSE resume。

### 2. Assistant delta 处理

pygent runtime 在模型 stream 中：

```python
assistant_parts: list[str] = []
reasoning_parts: list[str] = []

async for chunk in llm.stream_forward(...):
    if chunk.reasoning_content:
        reasoning_parts.append(chunk.reasoning_content)
        yield event("assistant.reasoning_delta", {"delta": chunk.reasoning_content})

    if chunk.content:
        assistant_parts.append(chunk.content)
        yield event("assistant.delta", {"delta": chunk.content})
```

模型 stream 结束后，从 context 或 provider response 中读取完整 assistant message：

```python
assistant_message = normalize_assistant_message(...)
if not assistant_message.content and assistant_parts:
    assistant_message.content = "".join(assistant_parts)
```

然后发出：

```python
yield event(
    "assistant.message",
    {
        "message": assistant_message.to_dict(),
        "content": assistant_message.content,
        "tool_calls": [call.to_dict() for call in assistant_message.tool_calls],
    },
)
```

要求：

- `assistant.delta` 表示流式展示。
- `assistant.message` 表示可持久化的完整 assistant message。
- 如果 provider 没有最终 assistant message，pygent 可以用 delta 拼接结果构造。
- 如果 provider 最终 message 与 delta 不一致，应以最终 message 为准，并在 metadata 中标记 `delta_content_mismatch=true`。

### 3. Tool call 提取

pygent runtime 统一提取 tool calls：

```python
tool_calls = extract_tool_calls(assistant_message)
```

每个 tool call 标准化为：

```python
AgentToolCall(
    tool_call_id=stable_tool_call_id,
    provider_call_id=provider_call_id,
    name=tool_name,
    arguments=arguments,
    raw=raw_call,
)
```

要求：

- 如果 provider 没有提供 tool call id，pygent 应生成稳定 id，例如 `call_{step_index}_{index}`。
- `arguments` 必须是 dict；JSON 解析失败应成为 `tool.result(status="error", error_type="ToolArgumentsParseError")` 或 `run.error`，具体策略需要文档化。
- `raw` 可选保留 provider 原始结构，供上层调试。

### 4. Tool execution

伪代码：

```python
for call in tool_calls:
    yield event("tool.call", call.to_dict())

    try:
        if tool_executor is not None:
            result = await tool_executor.execute_tool(call, tool_context)
        else:
            result = await default_tool_executor.execute_tool(call, tool_context)
    except Exception as exc:
        result = AgentToolResult(
            tool_call_id=call.tool_call_id,
            status="error",
            content="",
            error=str(exc),
            error_type=type(exc).__name__,
        )

    yield event("tool.result", result.to_dict())

    tool_message = tool_result_to_message(result)
    model_context.add_message(tool_message)
```

要求：

- `tool.call` 必须在执行前发出。
- `tool.result` 必须在执行后发出，包括成功和失败。
- 如果工具失败但错误已经结构化，agent runtime 可以继续把 error tool message 送回模型，让模型决定下一步。
- 如果工具失败不可恢复，例如 executor 自身崩溃，也应先发出 `tool.result(status="error")`，再按配置决定是否继续。

### 5. Max steps 语义

建议 step 定义为“一次模型请求和其后工具处理”。

流程：

```text
step 1:
  model.request
  assistant.delta*
  assistant.message
  tool.call*
  tool.result*

step 2:
  model.request
  assistant.delta*
  assistant.message
  no tool calls => completed
```

规则：

- `max_steps=-1`：无限，直到没有 tool calls。
- `max_steps=1`：最多一次模型请求。如果这次请求产生 tool calls，可以执行这些工具，但不再发起第二次模型请求。
- 当达到 max steps 且仍需要工具 follow-up 时，返回 `run.error`，`error_type="MaxStepsExceeded"`。
- 错误消息应包含 `max_steps`、`step_count`、是否仍有 pending tool calls。

### 6. Usage 汇总

pygent runtime 应尽量从 provider response 中提取 usage：

```json
{
  "prompt_tokens": 123,
  "completion_tokens": 45,
  "total_tokens": 168
}
```

要求：

- 每次模型响应的 usage 放在 `model.response.payload.usage`。
- turn 总 usage 放在 `run.completed.payload.usage`。
- 如果 provider 不提供 usage，字段可以为空 dict。
- usage 字段不应阻塞 agent 执行。

### 7. Error 分类

建议定义标准错误类型：

| error_type | 场景 |
| --- | --- |
| `InvalidRunOptionsError` | `max_steps` 等参数非法 |
| `ModelRequestError` | 模型请求失败 |
| `ModelStreamError` | stream 中途失败 |
| `ToolArgumentsParseError` | tool arguments 无法解析 |
| `UnknownToolError` | 模型请求未注册工具 |
| `ToolExecutionError` | 工具执行失败且未提供更具体类型 |
| `MaxStepsExceeded` | 达到最大步数 |
| `ContextCompressionError` | 上下文压缩失败 |
| `AgentCancelled` | 调用方取消 |

要求：

- `run.error.payload.error_type` 使用稳定字符串。
- Python exception type 可以放在 `metadata.exception_type`。
- 工具错误优先使用 `tool.result(status="error")`，不应全部升级成 `run.error`。

### 8. Backward compatibility adapter

pygent 可以提供兼容现有 agent 的包装器：

```python
class LegacyAgentRuntimeAdapter:
    def __init__(self, agent: Any):
        self.agent = agent

    async def stream_turn(...):
        if hasattr(agent, "stream"):
            async for value in agent.stream(...):
                yield normalize_legacy_output(value)
        elif hasattr(agent, "run"):
            value = await maybe_await(agent.run(...))
            yield assistant_message_event(value)
        else:
            raise InvalidAgentError(...)
```

这能让 Lora 分阶段迁移，不必一次性修改所有测试 agent。

## 验收用例

### 1. 简单 assistant 回复

输入：

```python
messages = [{"role": "user", "content": "hello"}]
```

agent 输出普通 assistant 文本。

期望事件：

```text
run.started
model.request
assistant.delta*
assistant.message
model.response
run.completed
```

验收：

- `assistant.message.payload.content` 是完整回复。
- `run.completed.payload.final_answer` 等于完整回复。
- 不需要上层解析 pygent chunk。

### 2. 流式 delta 与最终消息一致

模型 chunk：

```text
hel
lo
```

最终 assistant message：

```text
hello
```

验收：

- 发出两个 `assistant.delta`。
- 发出一个 `assistant.message(content="hello")`。
- `assistant.message.metadata.delta_content_mismatch` 不存在或为 false。

### 3. provider 没有最终 assistant message

模型只提供 delta：

```text
hello
```

验收：

- pygent 用 delta 拼接构造 `assistant.message(content="hello")`。
- `assistant.message.metadata.constructed_from_delta=true`。

### 4. 工具调用成功

assistant message 包含：

```json
{
  "tool_calls": [
    {
      "id": "call_1",
      "name": "read",
      "arguments": {"file_path": "E:\\Projects\\lora\\README.md"}
    }
  ]
}
```

验收：

- 发出 `assistant.message`，payload 中包含 tool call。
- 发出 `tool.call(tool_call_id="call_1", name="read")`。
- 调用注入的 `ToolExecutor`。
- 发出 `tool.result(tool_call_id="call_1", status="success")`。
- tool result 被转换成模型可见 tool message。
- 如果后续模型给出最终回答，`run.completed.final_answer` 是后续 assistant 文本。

### 5. 工具调用失败

`ToolExecutor` 返回：

```python
AgentToolResult(
    tool_call_id="call_1",
    status="error",
    content="",
    error="File does not exist",
    error_type="FileNotFoundError",
)
```

验收：

- 发出 `tool.result(status="error")`。
- tool message content 中包含结构化 `status="error"`。
- agent runtime 可以继续请求模型，让模型看到工具失败。
- Lora 不需要通过字符串匹配判断错误。

### 6. 未知工具

模型请求未注册工具 `unknown_tool`。

验收：

- 发出 `tool.call(name="unknown_tool")`。
- 发出 `tool.result(status="error", error_type="UnknownToolError")`。
- payload 包含 `available_tools`。
- 是否继续下一轮由 runtime 策略决定，但状态必须结构化。

### 7. max steps 超限

配置：

```python
AgentRunOptions(max_steps=1)
```

模型第一步产生 tool calls，工具执行后仍需要模型 follow-up。

验收：

- 已执行第一步 tool calls。
- 不发起第二次模型请求。
- 发出 `run.error(error_type="MaxStepsExceeded")`。
- `partial_final_answer` 包含截至当前可用 assistant 文本。

### 8. Lora 注入 ToolInterceptor

Lora 传入自定义 `ToolExecutor`，内部调用 `ToolInterceptor.call_tool(...)`。

验收：

- pygent 不绕过该 executor。
- Lora 能记录 `tool.call`、`tool.result`、`file_events`、`diffs`。
- pygent 仍负责把 `AgentToolResult` 转成 tool message 继续 agent loop。

### 9. API/UI SSE 映射

Lora 收到 pygent events 后映射为现有 SSE：

| pygent | API |
| --- | --- |
| `assistant.delta` | `assistant.delta` |
| `assistant.reasoning_delta` | `assistant.reasoning_delta` |
| `assistant.message` | `runtime.message` |
| `tool.result` | `tool.result` |
| `run.completed` | `chat.completed` |
| `run.error` | `chat.error` |

验收：

- 桌面端无需理解 pygent 内部 message。
- 断线恢复仍由 Lora API 的 event sequence 实现。

## 测试建议

### pygent 单元测试

1. `AgentRuntimeEvent` sequence 递增。
2. assistant delta 拼接成最终 message。
3. provider final message 优先于 delta 拼接。
4. tool call id 贯穿 `assistant.message`、`tool.call`、`tool.result`、tool message。
5. injected `ToolExecutor` 被调用。
6. default tool executor fallback 可用。
7. unknown tool 生成结构化错误。
8. tool executor exception 生成 `tool.result(status="error")`。
9. `max_steps` 超限生成 `run.error(MaxStepsExceeded)`。
10. usage 在 step 和 run 层聚合。

### Lora 集成测试

1. `AgentRuntimeAdapter.run_turn(...)` 使用 pygent runtime event 流后，仍写入现有 `conversation.user_message`、`conversation.assistant_message`、`tool.result`、`model.request`、`model.response`、`context.checkpoint`。
2. CLI 单轮仍返回原有 JSON 字段。
3. CLI 交互式仍能实时打印 assistant delta 和 tool/runtime message。
4. API `/chat/stream` 仍返回现有 SSE event names。
5. 桌面端现有消息渲染不需要迁移。
6. `run_case(...)` 的 evaluator 输入不变。
7. agent 异常时 partial assistant output 仍被保存。
8. tool result 的文件影响和 diff 仍由 Lora 记录。

## 推荐实现顺序

1. 在 pygent 新增 protocol dataclasses 和 Protocol 类型，不接入运行链。
2. 增加 message/chunk normalization helper。
3. 增加 `ToolExecutor`、`AgentToolCall`、`AgentToolResult`。
4. 实现 `tool_result_to_message(...)`。
5. 实现默认 ReAct `stream_turn(...)`，先覆盖无工具和单工具成功路径。
6. 增加工具失败、未知工具、max steps、usage 的测试。
7. 提供 legacy adapter，兼容现有 `agent.stream(...)` 和 `agent.run(...)`。
8. Lora 新增实验性 pygent runtime adapter，不替换默认路径。
9. 用 Lora 的 `AgentRuntimeAdapter` 测试集验证行为一致。
10. 再逐步将 `LoraAgent.stream(...)` 中的通用 ReAct loop 委托给 pygent。

## 成功标准

完成后，Lora 与 pygent 的边界应变成：

- pygent 负责稳定的 agent runtime protocol。
- pygent 负责模型流、assistant message、tool call、tool result、ReAct loop 的通用语义。
- Lora 负责 session、case run、trace、evaluation、repair、文件影响和 UI/API 映射。

具体可观察结果：

1. Lora 不再需要解析 pygent 内部 chunk/message 的多种形态。
2. Lora 不再需要自己从 assistant message 中提取 tool calls。
3. Lora 不再需要自己构造 pygent `ToolMessage`。
4. 工具调用成功/失败在 event 层结构化可见。
5. CLI、API、桌面端继续复用同一条 Lora runtime 边界。
6. `AgentRuntimeAdapter` 从“协议补丁 + Lora 编排”收缩为“Lora 编排 + 事件映射”。

最终目标不是把 Lora 的工程证据链上提给 pygent，而是让 pygent 提供足够稳定的 agent runtime event protocol，使 Lora 可以专注于本地 agent 运行证据、评测和自优化闭环。
