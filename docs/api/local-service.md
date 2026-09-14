# Lora Local API

## 启动

```powershell
uv run lora-api --workspace-root E:\Projects\lora --agent dev
```

## Chat execution

启动 execution：

```http
POST /chat/stream
Content-Type: application/json

{"message":"hello","session_id":null,"case_id":"chat"}
```

恢复 execution：

```http
POST /chat/stream
Content-Type: application/json

{"execution_id":"exec-...","after_sequence":12}
```

`message` 与 `execution_id` 必须且只能提供一个；请求中的未知字段会被拒绝。

所有非 keepalive 数据使用同一个 SSE 名称：

```text
event: execution.event
data: {"execution_id":"exec-...","sequence":13,"kind":"model.text.delta","module_path":"lora.react.model","trace_id":"trace-...","data":{"text":"hello"}}
```

客户端应按 `kind` 消费 Pygent 与 Lora Module 发出的事件，并保存最新 `execution_id/sequence` 用于重连。

运行中追加用户指令使用 `POST /chat/executions/{execution_id}/steering`，请求体为 `{"session_id":"...","input_id":"客户端生成的唯一 ID","message":"追加指令"}`。网络重试必须复用同一 `input_id`；响应 `status` 为 `accepted` 或 `duplicate`。会话不匹配或执行不可用返回 404，execution 输入窗口已经关闭返回 409，空消息返回 422。

该接口向 Pygent `send_input` 投递 `StandaloneUserMessage`，在 ReAct 下一处理边界生效，不中断当前模型或工具调用。接收后的指令通过 conversation checkpoint 保存到会话历史。前端在运行中使用原输入框追加指令；失败时保留草稿。

## 审批与后台任务

```http
POST /chat/approvals/{approval_id}
{"approved":true,"comment":"approved"}

GET /runtime/tasks/{task_id}
DELETE /runtime/tasks/{task_id}
```

## Settings

`GET /settings` 返回用户级 agent profile 及 routes，不返回原始 API key。`PATCH /settings` 可以切换 workspace、agent、步数和 context window。模型在 `~/.lora/config.yaml` 中声明并由所有项目共用。
