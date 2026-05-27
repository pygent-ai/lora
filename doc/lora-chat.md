# lora chat 使用说明

`lora chat` 用于启动一个可以和 agent 对话的命令行入口。它复用当前项目里的 session、runtime adapter、event store 和消息历史保存机制，因此每次对话都会落到 `.lora/sessions/{session_id}` 下，后续可以继续使用同一个 session。

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
--max-steps         覆盖 agent 最大执行步数
```

## 单轮输出

`--message` 模式会输出 JSON，包含本次 chat run 和 turn 的关键信息：

```json
{
  "case_id": "chat",
  "case_run_id": "run-...",
  "error": null,
  "final_answer": "Echo: ping",
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

这和现有 `case run` 流程保持一致，便于后续 replay、分析和调试。

## 当前 agent 行为

当前项目的默认 agent 是 `EchoAgent`，所以默认回复会是：

```text
Echo: <你的输入>
```

`lora chat` 已经接在通用 `AgentRuntimeAdapter` 上。后续接入真实 pygent、DeepSeek 或其他 agent 时，可以继续复用这个 CLI 入口和 session/event 保存链路。
