# 上下文压缩功能开发与测试文档

## 1. 背景

后端会话上下文会随着用户消息、assistant 回复、工具调用和工具结果不断增长。当上下文 token 使用量接近模型 `context_window` 上限时，需要自动触发上下文压缩，避免后续模型请求超出上下文窗口。

本功能只压缩后端实际传给模型的上下文，不影响 GUI 展示完整会话历史。

## 2. 目标

实现一套上下文压缩机制：

1. 在每次 `stream` 方法请求模型前检查上下文 token 使用量。
2. 当 `input_tokens + output_tokens >= context_window * 0.9` 时触发压缩。
3. 压缩后传给模型的上下文只保留一个 system message 和一个 user message。
4. 压缩后的 system message 必须与压缩前完全一致。
5. 压缩后的 user message 内包含：
  - 与压缩前完全一致的 `<system-reminder>...</system-reminder>`。
  - `<session-context>...</session-context>`。
  - `<file-read>...</file-read>`，用于恢复最近 5 个文件读取结果。
  - `<summary>...</summary>`，用于承载压缩摘要。
6. GUI 所需完整消息、工具调用、工具结果必须继续完整保存。

## 4. 核心设计原则

### 4.1 模型上下文和 GUI 历史分离

压缩只影响模型请求上下文，不影响 GUI 展示历史。

推荐拆分为四类文件：

```text
session.json         GUI 完整会话历史
model_context.json   当前实际传给模型的上下文
compactions.jsonl    压缩记录
transcript.jsonl     完整原始流水记录
```

### 4.2 System Message 完全一致

同一个会话内，压缩前后的 system message 必须完全一致。

要求：

```text
compressed_system_message.content == original_system_message.content
```

不得重新生成、裁剪、改写或补充 system prompt。

### 4.3 压缩后上下文结构固定

压缩后模型上下文只包含：

```json
[
  {
    "role": "system",
    "content": "原始 system prompt"
  },
  {
    "role": "user",
    "content": "压缩后的 continuation prompt"
  }
]
```

不再保留旧 user、assistant、tool messages 到模型上下文中。

## 5. 配置项

建议新增配置：

```text
CONTEXT_COMPRESSION_ENABLED=true
CONTEXT_COMPRESSION_TRIGGER_RATIO=0.9
CONTEXT_COMPRESSION_FILE_READ_COUNT=5
CONTEXT_COMPRESSION_FILE_READ_MAX_CHARS=5000
```


| 配置                                        | 默认值    | 说明                |
| ----------------------------------------- | ------ | ----------------- |
| `CONTEXT_COMPRESSION_ENABLED`             | `true` | 是否启用上下文压缩         |
| `CONTEXT_COMPRESSION_TRIGGER_RATIO`       | `0.9`  | 达到上下文窗口比例后触发压缩    |
| `CONTEXT_COMPRESSION_FILE_READ_COUNT`     | `5`    | 恢复最近读取文件数量        |
| `CONTEXT_COMPRESSION_FILE_READ_MAX_CHARS` | `5000` | 每个历史读取结果可注入的最大字符数 |


`context_window` 优先从模型配置读取。

## 6. 触发条件

模型返回中已有 token 使用信息。后端使用：

```text
context_tokens = input_tokens + output_tokens
```

判断是否达到压缩阈值：

```text
context_tokens >= context_window * CONTEXT_COMPRESSION_TRIGGER_RATIO
```

默认即：

```text
context_tokens >= context_window * 0.9
```

## 7. 触发位置

压缩检查发生在 `stream` 方法内，每次请求模型前。

伪代码：

```text
stream(request):
  acquire compression lock by session_id

  load session state
  load latest token usage

  if compression enabled
     and context_tokens >= context_window * 0.9:
        run context compression
        replace model context
        persist compression result

  release compression lock

  call model stream
```

第一版只考虑一轮压缩：

```text
if session.is_compacted == true:
    skip compression
```

## 8. 并发锁设计

需要 session 级别锁，避免同一会话并发触发多次压缩。

锁 key：

```text
compression:{session_id}
```

锁保护范围：

1. 读取当前 session 状态。
2. 判断是否需要压缩。
3. 调用压缩模型。
4. 写入 `model_context.json`。
5. 写入 `compactions.jsonl`。
6. 更新 session 压缩状态。

并发要求：

1. 同一 session 同时只能有一个压缩任务。
2. 其他请求等待锁释放。
3. 等待结束后重新读取 session 状态。
4. 如果已经压缩完成，不再重复压缩。

## 9. 数据结构设计

### 9.1 `session.json`

用于 GUI 展示完整历史。

压缩成功后，不删除、不裁剪、不改写历史消息。

示例：

```json
{
  "session_id": "xxx",
  "created_at": "...",
  "updated_at": "...",
  "status": "normal",
  "messages": [
    {
      "id": "msg_1",
      "role": "user",
      "content": "...",
      "created_at": "..."
    },
    {
      "id": "msg_2",
      "role": "assistant",
      "content": "...",
      "tool_calls": [],
      "created_at": "..."
    },
    {
      "id": "msg_3",
      "role": "tool",
      "tool_call_id": "call_xxx",
      "name": "read_file",
      "content": "...",
      "created_at": "..."
    }
  ],
  "token_usage": {
    "latest_input_tokens": 0,
    "latest_output_tokens": 0,
    "latest_context_tokens": 0
  }
}
```

### 9.2 `model_context.json`

保存下一次实际传给模型的上下文。

压缩后示例：

```json
{
  "session_id": "xxx",
  "is_compacted": true,
  "compaction_id": "compact_xxx",
  "messages": [
    {
      "role": "system",
      "content": "原始 system prompt"
    },
    {
      "role": "user",
      "content": "<system-reminder>...</system-reminder>\n<session-context>...</session-context>"
    }
  ]
}
```

### 9.3 `compactions.jsonl`

每次压缩追加一行记录。

```json
{
  "compaction_id": "compact_xxx",
  "session_id": "xxx",
  "created_at": "...",
  "summary": "...",
  "transcript_path": "...",
  "file_reads": [
    {
      "path": "src/a.ts",
      "mode": "partial",
      "range": "120-220",
      "included": true,
      "char_count": 1800,
      "truncated": false
    }
  ],
  "attempts_with_tools": 1,
  "attempts_without_tools": 0,
  "fallback_without_tools": false
}
```

当历史读取实际返回内容超过 `CONTEXT_COMPRESSION_FILE_READ_MAX_CHARS` 时，`file_reads` 中应记录：

```json
{
  "path": "src/example.ts",
  "mode": "partial",
  "range": "120-220",
  "included": false,
  "char_count": 7821,
  "truncated": true
}
```

### 9.4 `transcript.jsonl`

完整原始流水记录，用于恢复压缩前细节。

示例：

```json
{
  "type": "message",
  "message_id": "msg_xxx",
  "role": "assistant",
  "content": "...",
  "created_at": "..."
}
```

## 10. 文件读取恢复设计

压缩后需要恢复最近 5 个文件读取结果。

### 10.1 选择规则

1. 从历史工具调用结果或文件读取记录中获取最近文件读取。
2. 默认恢复最近 5 个。
3. 如果历史读取是片段，恢复同一个片段的历史返回结果。
4. 如果历史读取是整文件，恢复整文件的历史返回结果。
5. 不重新读取磁盘文件。
6. 不根据当前磁盘文件长度判断是否超长。
7. 不根据完整文件大小判断是否超长。

### 10.2 超长判断规则

超长判断必须基于该次历史读取的实际返回内容长度：

```text
read_result_char_count = len(read_result.content)
```

判断规则：

```text
read_result_char_count <= CONTEXT_COMPRESSION_FILE_READ_MAX_CHARS
  -> 在 <file-read> 中完整注入该次历史读取返回内容

read_result_char_count > CONTEXT_COMPRESSION_FILE_READ_MAX_CHARS
  -> 不注入任何文件内容片段，只注入固定英文说明和元数据
```

默认阈值：

```text
CONTEXT_COMPRESSION_FILE_READ_MAX_CHARS=5000
```

重要约束：

1. 大文件但实际读取片段不超过 5000 字符时，应注入该片段。
2. 小文件但历史工具结果内容超过 5000 字符时，应隐藏细节。
3. 超长时不得注入前 5000 字符，也不得注入中间片段或尾部片段。
4. GUI 完整历史仍应保留原始 tool result，隐藏策略只影响压缩后的模型上下文。

### 10.3 固定英文说明

当历史读取实际返回内容超过阈值时，使用以下固定英文说明：

```text
The previous file read result is too large to include in this compacted context, so its details are not shown here. Path: {path}. Read scope: {scope}. Returned content length: {char_count} characters. Re-read this exact file or range if you need the content.
```

字段说明：


| 字段             | 说明                                      |
| -------------- | --------------------------------------- |
| `{path}`       | 历史读取记录中的文件路径                            |
| `{scope}`      | 历史读取范围，例如 `full file` 或 `lines 120-220` |
| `{char_count}` | `len(read_result.content)` 的结果          |


### 10.4 `<file-read>` 格式

普通片段读取：

```xml
<file path="src/a.ts" mode="partial" range="120-220" truncated="false" char_count="1800">
文件片段内容
</file>
```

普通整文件读取：

```xml
<file path="src/b.ts" mode="full" truncated="false" char_count="4200">
文件完整内容
</file>
```

超长片段读取：

```xml
<file path="src/example.ts" mode="partial" range="120-220" truncated="true" char_count="7821">
The previous file read result is too large to include in this compacted context, so its details are not shown here. Path: src/example.ts. Read scope: lines 120-220. Returned content length: 7821 characters. Re-read this exact file or range if you need the content.
</file>
```

超长整文件读取：

```xml
<file path="src/large.ts" mode="full" truncated="true" char_count="12034">
The previous file read result is too large to include in this compacted context, so its details are not shown here. Path: src/large.ts. Read scope: full file. Returned content length: 12034 characters. Re-read this exact file or range if you need the content.
</file>
```

完整 `<file-read>` 示例：

```xml
<file-read>
<file path="src/a.ts" mode="partial" range="120-220" truncated="false" char_count="1800">
文件片段内容
</file>
<file path="src/example.ts" mode="partial" range="120-220" truncated="true" char_count="7821">
The previous file read result is too large to include in this compacted context, so its details are not shown here. Path: src/example.ts. Read scope: lines 120-220. Returned content length: 7821 characters. Re-read this exact file or range if you need the content.
</file>
</file-read>
```

## 11. 压缩后 User Message 格式

压缩后的 user message 必须符合以下结构：

```xml
<system-reminder>
这里必须和压缩前完全一致
</system-reminder>
<session-context>
This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

<file-read>
<file path="..." mode="partial" range="..." truncated="false" char_count="...">
...
</file>
</file-read>

<summary>
这里是模型压缩输出中 <summary>...</summary> 的内容
If you need specific details from before compaction (like exact code snippets, error messages, or content you generated), read the full transcript at: xxxxxx
</summary>
</session-context>
```

要求：

1. `<system-reminder>` 与压缩前完全一致。
2. `<session-context>` 内包含 `<file-read>` 和 `<summary>`。
3. `<summary>` 内容来自压缩模型输出中的 `<summary>...</summary>`。
4. 压缩后模型上下文不再包含旧 user、assistant、tool messages。

## 12. 压缩模型调用流程

压缩本身通过内部模型请求完成。

### 12.1 压缩触发时添加的 User Message

当 `stream` 方法内检测到需要触发压缩时，后端需要基于当前完整模型上下文构造一次内部压缩请求。该请求在当前上下文之后追加一条新的 user message，用于要求模型生成会话摘要。

这条 user message 只用于压缩请求本身，不是压缩成功后保存到 `model_context.json` 的 continuation user message。压缩成功后保存到 `model_context.json` 的 user message 仍使用第 11 节定义的 `<system-reminder> + <session-context>` 结构。

内部压缩 user message 模板如下：

```text
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.

There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating the above summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
</example>

<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
</example>

REMINDER: Do NOT call any tools. Respond with plain text only — an <analysis> block followed by a <summary> block. Tool calls will be rejected and you will fail the task.
```

实现要求：

1. `compression_messages` 应等于当前完整模型上下文追加上述 user message。
2. 默认请求仍携带 tools，用于保持 KV cache 命中。
3. 虽然请求携带 tools，但该 user message 明确要求模型不要调用工具。
4. 如果模型实际输出 tool call，本次压缩尝试失败。
5. 后端只解析并持久化 `<summary>...</summary>` 中的内容。
6. `<analysis>...</analysis>` 仅用于模型组织推理，不写入压缩后的 `model_context.json`。
7. 如果模型输出缺少 `<summary>...</summary>`，本次压缩尝试失败。

### 12.2 默认流程

默认流程：

```text
1. 使用当前上下文 + 压缩 user message 请求模型。
2. 默认携带 tools，用于保持 KV cache 命中。
3. 如果模型输出 tool call，认为本次压缩失败。
4. 如果模型正常输出文本，则解析 <summary>...</summary>。
5. 解析成功则压缩成功。
6. 带 tools 失败 5 次后，进入不带 tools 的兜底流程。
7. 不带 tools 再失败 5 次，则 session 标记为 compression_failed。
```

伪代码：

```text
for attempt in 1..5:
    result = call_model_with_tools(compression_messages)

    if result.has_tool_call:
        continue

    summary = parse_summary(result.text)

    if summary is valid:
        return success(summary)

for attempt in 1..5:
    result = call_model_without_tools(compression_messages)

    if result.has_tool_call:
        continue

    summary = parse_summary(result.text)

    if summary is valid:
        return success(summary)

mark session as compression_failed
```

## 13. Summary 解析规则

只解析模型输出中的：

```xml
<summary>
...
</summary>
```

成功条件：

1. 存在 `<summary>`。
2. 存在 `</summary>`。
3. 起止标签顺序正确。
4. 中间内容 trim 后非空。
5. 模型没有输出 tool call。

失败条件：

1. 输出了 tool call。
2. 找不到 `<summary>`。
3. 找不到 `</summary>`。
4. summary 内容为空。
5. 模型请求异常。

第一版暂不处理 XML 转义和用户输入标签污染。

## 14. Session 状态机

建议增加压缩状态：

```text
normal
compressing
compacted
compression_failed
```


| 状态                   | 含义              |
| -------------------- | --------------- |
| `normal`             | 正常会话，尚未压缩       |
| `compressing`        | 正在执行压缩          |
| `compacted`          | 已完成压缩           |
| `compression_failed` | 压缩失败，会话不能继续请求模型 |


状态流转：

```text
normal -> compressing -> compacted
normal -> compressing -> compression_failed
```

第一版中：

```text
compacted 状态不再触发第二次压缩
```

## 15. 测试范围

本轮测试只覆盖一轮上下文压缩。

覆盖内容：

1. 触发条件。
2. 压缩后上下文结构。
3. system message 保持一致。
4. `<system-reminder>` 保持一致。
5. 最近 5 个文件读取恢复。
6. 历史读取实际返回内容超长时隐藏细节。
7. summary 解析。
8. tool call 失败重试。
9. fallback without tools。
10. GUI 历史不丢失。
11. 并发锁。

## 16. 单元测试设计

### 16.1 未达到 90% 不触发压缩

输入：

```text
context_tokens = 899
context_window = 1000
trigger_ratio = 0.9
```

期望：

1. 不调用压缩模型。
2. `model_context.json` 不变化。
3. session 状态仍为 `normal`。

### 16.2 达到 90% 触发压缩

输入：

```text
context_tokens = 900
context_window = 1000
trigger_ratio = 0.9
```

期望：

1. 调用压缩模型。
2. 压缩成功后 session 状态为 `compacted`。
3. 写入一条 `compactions.jsonl`。
4. `model_context.json` 被替换为 system + user 两条消息。

### 16.3 System Message 完全一致

准备：

```text
original_system_message = "..."
```

压缩成功后检查：

```text
model_context.messages[0].role == "system"
model_context.messages[0].content == original_system_message
```

期望：

1. 内容完全一致。
2. 不新增额外 system message。
3. 不修改 system message 顺序。

### 16.4 压缩后 User Message 结构正确

检查压缩后的 user content 包含：

```text
<system-reminder>
</system-reminder>
<session-context>
<file-read>
</file-read>
<summary>
</summary>
</session-context>
```

期望：

1. 标签完整。
2. `<system-reminder>` 在 `<session-context>` 前。
3. `<file-read>` 和 `<summary>` 位于 `<session-context>` 内。

### 16.5 `<system-reminder>` 与压缩前一致

准备：

```xml
<system-reminder>
原始 reminder 内容
</system-reminder>
```

期望：

```text
compressed_system_reminder == original_system_reminder
```

### 16.6 最近 5 个文件读取恢复

准备 7 条文件读取记录：

```text
file_1
file_2
file_3
file_4
file_5
file_6
file_7
```

期望：

1. 只恢复最近 5 个：`file_3` 到 `file_7`。
2. 顺序与历史读取顺序一致。
3. 不包含更早的 `file_1`、`file_2`。

### 16.7 片段读取恢复片段

准备：

```text
path = src/a.ts
mode = partial
range = 120-220
content = lines 120-220
```

期望生成：

```xml
<file path="src/a.ts" mode="partial" range="120-220" truncated="false" char_count="...">
...
</file>
```

并且内容为原历史读取片段。

### 16.8 整文件读取恢复整文件

准备：

```text
path = src/b.ts
mode = full
content = full file content
```

期望生成：

```xml
<file path="src/b.ts" mode="full" truncated="false" char_count="...">
...
</file>
```

内容为历史读取的整文件结果。

### 16.9 历史读取实际返回内容长度为 4999 时注入原内容

准备：

```text
CONTEXT_COMPRESSION_FILE_READ_MAX_CHARS=5000
len(read_result.content) = 4999
```

期望：

1. `<file>` 中包含完整 `read_result.content`。
2. `truncated="false"`。
3. 不出现固定英文超长说明。

### 16.10 历史读取实际返回内容长度为 5000 时注入原内容

准备：

```text
CONTEXT_COMPRESSION_FILE_READ_MAX_CHARS=5000
len(read_result.content) = 5000
```

期望：

1. `<file>` 中包含完整 `read_result.content`。
2. `truncated="false"`。
3. 不出现固定英文超长说明。

### 16.11 历史读取实际返回内容长度为 5001 时隐藏细节

准备：

```text
CONTEXT_COMPRESSION_FILE_READ_MAX_CHARS=5000
len(read_result.content) = 5001
```

期望：

1. `<file>` 中不包含任何原始文件内容片段。
2. `<file>` 中只包含固定英文超长说明。
3. `truncated="true"`。
4. `char_count="5001"`。

### 16.12 大文件但读取片段不超过 5000 时注入片段

准备：

```text
actual_file_size = 100000
mode = partial
len(read_result.content) = 3200
```

期望：

1. 根据 `len(read_result.content)` 判断未超长。
2. 注入该历史读取片段。
3. 不因为完整文件很大而隐藏细节。

### 16.13 小文件但历史工具结果内容超过 5000 时隐藏细节

准备：

```text
actual_file_size = 3000
len(read_result.content) = 6200
```

期望：

1. 根据 `len(read_result.content)` 判断超长。
2. 不注入原始内容。
3. 注入固定英文超长说明。

### 16.14 `compactions.jsonl` 记录超长文件元数据

准备：

```text
path = src/example.ts
mode = partial
range = 120-220
len(read_result.content) = 7821
```

期望 `compactions.jsonl` 记录：

```json
{
  "path": "src/example.ts",
  "mode": "partial",
  "range": "120-220",
  "included": false,
  "char_count": 7821,
  "truncated": true
}
```

### 16.15 Summary 解析成功

模型返回：

```xml
<summary>
这是压缩摘要
</summary>
```

期望：

```text
parse_summary(result) == "这是压缩摘要"
```

### 16.16 Summary 缺失时解析失败

模型返回：

```text
这是普通文本
```

期望：

1. 当前尝试失败。
2. 进入下一次重试。
3. 不写入成功 compaction。

### 16.17 Summary 为空时解析失败

模型返回：

```xml
<summary>
   
</summary>
```

期望：

1. 当前尝试失败。
2. 继续重试。

## 17. 集成测试设计

### 17.1 带 tools 压缩成功

准备：

1. 当前上下文达到 90%。
2. mock 模型带 tools 返回合法 summary。
3. 无 tool call。

期望：

1. 压缩成功。
2. `attempts_with_tools = 1`。
3. `attempts_without_tools = 0`。
4. `fallback_without_tools = false`。

### 17.2 带 tools 输出 tool call 视为失败

准备：

mock 模型返回 tool call。

期望：

1. 本次尝试失败。
2. 不解析 summary。
3. 继续下一次带 tools 重试。
4. 最多重试 5 次。

### 17.3 带 tools 失败后 fallback without tools

准备：

1. 前 5 次带 tools 均返回 tool call。
2. 第 1 次 without tools 返回合法 summary。

期望：

1. 执行 5 次 with tools。
2. 执行 1 次 without tools。
3. 压缩成功。
4. `fallback_without_tools = true`。
5. `attempts_with_tools = 5`。
6. `attempts_without_tools = 1`。

### 17.4 总计失败 10 次后 session 失败

准备：

1. 5 次 with tools 全部失败。
2. 5 次 without tools 全部失败。

期望：

1. session 状态为 `compression_failed`。
2. 不替换 `model_context.json` 为半成品。
3. 记录失败原因。
4. 后续 stream 请求不再继续调用普通模型。

### 17.5 GUI 历史不丢失

准备：

session 中包含：

1. 多条 user message。
2. 多条 assistant message。
3. 多条 tool call。
4. 多条 tool result。

压缩成功后检查：

1. `session.json` 中完整历史仍存在。
2. GUI 读取 `session.json` 可以展示完整会话。
3. `model_context.json` 已被压缩为两条消息。

### 17.6 Transcript Path 注入 Summary

压缩成功后检查 `<summary>` 内包含：

```text
If you need specific details from before compaction (like exact code snippets, error messages, or content you generated), read the full transcript at:
```

并且后面带有当前 session 的 transcript 路径。

### 17.7 并发压缩只执行一次

准备：

两个请求同时进入同一个 session 的 `stream`。

期望：

1. 第一个请求获得锁并执行压缩。
2. 第二个请求等待锁。
3. 第一个请求压缩成功后释放锁。
4. 第二个请求重新读取状态，发现已 `compacted`。
5. 第二个请求不再重复压缩。
6. `compactions.jsonl` 只有一条记录。

### 17.8 超长读取在压缩上下文中只显示英文说明

准备：

1. 历史中存在一次文件读取。
2. `len(read_result.content) = 7821`。
3. `CONTEXT_COMPRESSION_FILE_READ_MAX_CHARS=5000`。
4. 触发上下文压缩。

期望：

1. 压缩后的 user message 中 `<file-read>` 包含该文件路径、读取范围和 `char_count="7821"`。
2. `<file-read>` 不包含原始文件内容的任意片段。
3. `<file-read>` 包含固定英文超长说明。
4. GUI 完整历史仍可展示原始 tool result。

## 18. 验收标准

功能验收需要满足：

1. 每次 `stream` 请求模型前都会检查是否需要压缩。
2. 使用 `input_tokens + output_tokens` 判断当前上下文长度。
3. 达到 `context_window * 0.9` 时触发压缩。
4. 压缩后模型上下文只包含一个 system message 和一个 user message。
5. 压缩后 system message 与压缩前完全一致。
6. 压缩后 `<system-reminder>` 与压缩前完全一致。
7. `<file-read>` 恢复最近 5 个文件读取结果。
8. 文件读取超长判断基于历史读取实际返回内容长度。
9. 历史读取实际返回内容长度小于等于 5000 时完整注入。
10. 历史读取实际返回内容长度超过 5000 时不注入任何文件内容片段，只注入固定英文说明。
11. 压缩输出必须成功解析 `<summary>...</summary>`。
12. 带 tools 压缩失败 5 次后，切换到 without tools。
13. without tools 再失败 5 次后，session 标记为 `compression_failed`。
14. GUI 完整历史不因压缩丢失。
15. 并发请求不会重复触发压缩。
