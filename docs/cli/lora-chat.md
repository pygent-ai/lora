# lora chat 使用说明

`lora chat` 用于启动一个可以和 Lora Agent 对话的命令行入口。它复用当前项目里的 `SessionManager`、`AgentRuntimeAdapter`、`LoraAgent` 和 `EventStore`，因此每次对话都会落到 `.lora/sessions/{session_id}` 下，后续可以继续使用同一个 session。

## 基本用法

单轮对话：

```powershell
lora chat --message "你好，帮我总结一下这个项目"
```

如果在源码目录内直接用本地代码运行：

```powershell
.\.venv\Scripts\python.exe -m lora chat --message "你好，帮我总结一下这个项目"
```

交互式对话：

```powershell
lora chat
```

进入交互模式后，直接输入消息并回车。输入 `/exit` 或 `/quit` 结束对话。

## Session 续接

`lora chat` 会把对话历史保存到 session 中。你可以用 `--session` 续接已有会话：

```powershell
lora chat --session <session_id>
```

也可以在续接 session 时直接发送单轮消息：

```powershell
lora chat --session <session_id> --message "继续刚才的话题"
```

如果配置文件或环境变量中已经提供了 `session_id`，但你希望强制新建一个 chat session，可以使用：

```powershell
lora chat --new
```

## 常用参数

```text
--message, -m       发送单轮消息并以 JSON 输出结果
--session           续接已有 session
--new               强制新建 chat session
--workspace-root    指定工作区根目录，默认是当前目录或 LORA_WORKSPACE_ROOT
--config            指定 lora.yaml
--model             覆盖模型配置
--max-steps         覆盖 agent 最大执行步数；-1 表示无限循环，直到模型不再请求工具
```

## Agent 管理配置

当前实现支持通过全局 `--agent` 选择 `lora.yaml` 中的 agent profile。`AgentRuntimeAdapter` 仍默认创建 `LoraAgent`，但模型请求配置已经可以从 `agents[].model_request`、环境变量和 CLI 覆盖中解析，并以脱敏元数据写入运行产物。

推荐配置结构：

```yaml
agent:
  default_alias: dev

agents:
  - alias: dev
    model_request:
      api_key_env: DEEPSEEK_API_KEY
      api_key: ""
      model_name: deepseek-v4-flash
  - alias: fast-check
    model_request:
      api_key_env: DEEPSEEK_API_KEY
      model_name: deepseek-v4-flash
```

字段含义：

- `alias`：agent 别名，用于在 chat、case run 或后续 agent 管理命令中选择配置。
- `model_request.api_key_env`：读取 API key 的环境变量名，推荐优先使用，避免把密钥写入仓库。
- `model_request.api_key`：已废弃的明文 API key 字段，仅保留兼容；请改用 `~/.lora/credentials.env` 或 `lora credentials set`。
- 完整凭证管理说明见 [../guides/api-key-management.md](../guides/api-key-management.md)。
- `model_request.model_name`：模型名称，例如 `deepseek-v4-flash`。

解析优先级：

1. CLI 显式选择的 agent alias。
2. `agent.default_alias`。
3. 内置默认 alias，例如 `default`。

模型请求字段优先级：

1. `--model` 覆盖选中 agent 的 `model_request.model_name`。
2. `model_request.api_key_env` 指向的环境变量优先于明文 `model_request.api_key`。
3. 兼容现有 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_BASE_URL`。

运行事件和 `run_config.json` 会记录 `agent_alias`、`model_name`、`api_key_source` 和 `base_url`，但不会记录原始 API key。

## 单轮输出

`--message` 模式会输出 JSON。`final_answer` 是子 Agent 的答复内容，其余字段只作为追踪元数据：

```json
{
  "case_run_id": "run-...",
  "final_answer": "Lora agent is wired into chat, but API key is not configured for agent alias 'default'.",
  "run_dir": "...\\.lora\\sessions\\...\\cases\\chat\\runs\\...",
  "session_id": "chat-chat-..."
}
```

如果运行报错，会额外返回 `error`：

```json
{
  "case_run_id": "run-...",
  "error": "agent error message",
  "final_answer": "partial answer if any",
  "run_dir": "...\\.lora\\sessions\\...\\cases\\chat\\runs\\...",
  "session_id": "chat-chat-..."
}
```

其中：

- `session_id` 是后续续接对话需要使用的 id。
- `final_answer` 是 agent 的最终回复。
- `run_dir` 是本轮对话事件、消息和元数据的落盘目录。
- `error` 是可选字段，仅在 agent 运行报错时返回。

## 数据落盘

每次 chat 会创建一个 `chat` 类型的 case run，并写入类似下面的文件：

```text
.lora/
  sessions/
    {session_id}/
      session.json
      logs/session-events.jsonl
      cases/chat/runs/{case_run_id}/
        events.jsonl
        messages.jsonl
        run_config.json
        run_metadata.json
        diffs/
          diff_events.jsonl
          patches/
          snapshots/
```

`events.jsonl` 会记录用户消息、模型请求边界、提示词渲染、assistant 回复、模型响应边界和上下文 checkpoint。实际调用工具时，还会写入 `tool_calls.jsonl`、`tool_results.jsonl` 和 `file_events.jsonl` 等投影文件。写入、编辑或删除 workspace 文件时，Lora 还会在 `diffs/` 下保存文本快照、patch 文件和 `diff.created` 索引，后续 turn 可以通过 `diff` 工具查询这些持久化改动。

## 当前 agent 行为

`AgentRuntimeAdapter` 默认会创建 `LoraAgent`。`LoraAgent` 会加载工作区 `.env`，读取已解析的 agent profile，并兼容 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_BASE_URL`。默认工具从 `pygent.toolkits` 中按白名单注册：

- `bash`
- `read`
- `write`
- `edit`
- `glob`
- `grep`
- `diff`

`diff` 工具读取 Lora 已记录的文件影响和快照产物，可以按当前 turn、当前 run 或整个 session 返回变更摘要、结构化 JSON 或 unified patch；它用于查看历史运行证据，不替代实时仓库状态下的 `git diff`。

如果选中的 agent profile 解析到了可用 API key，`LoraAgent` 会通过 DeepSeek 兼容接口进行流式模型调用；当模型实际调用已注册工具时，调用会通过 `ToolInterceptor` 记录。API key 可以来自 `model_request.api_key_env`、`model_request.api_key` 或兼容的 `DEEPSEEK_API_KEY`。

如果选中的 agent profile 没有可用 API key，它不会调用外部模型，而是返回包含 alias 的固定提示：

```text
Lora agent is wired into chat, but API key is not configured for agent alias 'default'.
```

`EchoAgent` 仍保留在 `runtime.py` 中用于测试和自定义适配示例，但它不是 `lora chat` 的默认 agent。
