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

`GET /runtime/tasks/{task_id}` 保留任务的 `task_id`、`state` 等字段，并返回当前 `output` 与可空的最终 `result`。终态结果存在时优先使用其任务快照和输出；取消结果没有输出时保留已捕获输出。

`DELETE /runtime/tasks/{task_id}` 返回 `cancel_requested`、兼容字段 `cancelled` 和实际 `task` 快照。取消请求获接收不等于进程清理已确认，须检查任务状态；任务已结束时返回 `false` 和终态快照，任务不存在时返回 404。

Bash 前台等待到期后，任务仍运行，原回合的事件流不会追加后台终态。客户端可按任务 ID 查询，或重新读取会话审计。普通回合完成不会将后台任务改为成功。

```http
POST /chat/approvals/{approval_id}
{"approved":true,"comment":"approved"}

GET /runtime/tasks/{task_id}
DELETE /runtime/tasks/{task_id}
```

## Settings

`GET /settings` 返回用户级 agent profile 及 routes，不返回原始 API key。`PATCH /settings` 可以切换 workspace、agent、步数和 context window。模型在 `~/.lora/config.yaml` 中声明并由所有项目共用。
