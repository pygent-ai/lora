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

## 审批与后台任务

```http
POST /chat/approvals/{approval_id}
{"approved":true,"comment":"approved"}

GET /runtime/tasks/{task_id}
DELETE /runtime/tasks/{task_id}
```

## Settings

`GET /settings` 返回用户级 agent profile 及 routes，不返回原始 API key。`PATCH /settings` 可以切换 workspace、agent、步数和 context window。模型在 `~/.lora/config.yaml` 中声明并由所有项目共用。
