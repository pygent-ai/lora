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

当前最小实现只有默认 `LoraAgent`。Agent 管理功能落地后，`lora chat` 应支持通过别名选择 agent，并从 `lora.yaml` 或环境变量读取模型请求配置。

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
- `model_request.api_key`：本地明文 API key，仅适合个人未提交的本地配置；写入 run 产物时必须脱敏。
- `model_request.model_name`：模型名称，例如 `deepseek-v4-flash`。

解析优先级建议：

1. CLI 显式选择的 agent alias。
2. `agent.default_alias`。
3. 内置默认 alias，例如 `default`。

模型请求字段优先级建议：

1. `--model` 覆盖选中 agent 的 `model_request.model_name`。
2. `model_request.api_key_env` 指向的环境变量优先于明文 `model_request.api_key`。
3. 兼容现有 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_BASE_URL`。

运行事件和 `run_config.json` 可以记录 `agent_alias`、`model_name` 和 `api_key_source`，但不能记录原始 API key。

## 单轮输出

`--message` 模式会输出 JSON，包含本次 chat run 和 turn 的关键信息：

```json
{
  "case_id": "chat",
  "case_run_id": "run-...",
  "error": null,
  "final_answer": "Lora agent is wired into chat, but DEEPSEEK_API_KEY is not configured for a model call.",
  "message_count": 2,
  "run_dir": "...\\.lora\\sessions\\...\\cases\\chat\\runs\\...",
  "session_id": "chat-chat-...",
  "status": "passed",
  "turn_id": "turn-0001"
}
```

其中：

- `session_id` 是后续续接对话需要使用的 id。
- `final_answer` 是 agent 的最终回复。
- `run_dir` 是本轮对话事件、消息和元数据的落盘目录。
- `status` 为 `passed`、`error` 等运行状态。

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
```

`events.jsonl` 会记录用户消息、模型请求边界、提示词渲染、assistant 回复、模型响应边界和上下文 checkpoint。实际调用工具时，还会写入 `tool_calls.jsonl`、`tool_results.jsonl` 和 `file_events.jsonl` 等投影文件。

## 当前 agent 行为

`AgentRuntimeAdapter` 默认会创建 `LoraAgent`。在 agent 管理功能落地前，`LoraAgent` 会加载工作区 `.env`，读取 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_BASE_URL`，并从 `pygent.toolkits` 中注册一组白名单工具：

- `bash`
- `read`
- `write`
- `edit`
- `glob`
- `grep`

如果配置了 `DEEPSEEK_API_KEY`，`LoraAgent` 会通过 DeepSeek 兼容接口进行流式模型调用；当模型实际调用已注册工具时，调用会通过 `ToolInterceptor` 记录。

如果没有配置 `DEEPSEEK_API_KEY`，它不会调用外部模型，而是返回固定提示：

```text
Lora agent is wired into chat, but DEEPSEEK_API_KEY is not configured for a model call.
```

`EchoAgent` 仍保留在 `runtime.py` 中用于测试和自定义适配示例，但它不是 `lora chat` 的默认 agent。
