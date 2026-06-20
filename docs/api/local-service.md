# Local API Service

The local API service is the boundary between the Electron + React desktop shell and the Python Lora runtime. It is implemented in `src/lora_api` and runs as a local FastAPI application.

## Startup

```powershell
uv run lora-api
```

Default host and port:

```text
http://127.0.0.1:8765
```

Useful options:

```powershell
uv run lora-api --host 127.0.0.1 --port 8765 --workspace-root E:\Projects\lora
uv run lora-api --config E:\Projects\lora\lora.yaml --agent dev --model deepseek-v4-flash
```

The generated OpenAPI contract is:

```text
contracts/openapi/lora-api.json
```

## Route Summary

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service liveness check |
| `GET` | `/projects` | Current workspace and Lora data root |
| `GET` | `/settings` | Resolved runtime configuration safe for UI display |
| `GET` | `/sessions` | List chat sessions |
| `POST` | `/sessions` | Create a chat session |
| `GET` | `/sessions/{session_id}` | Load session detail and history |
| `DELETE` | `/sessions/{session_id}` | Delete a session |
| `POST` | `/chat/stream` | Run one chat turn as server-sent events |
| `GET` | `/traces/{session_id}/{case_run_id}` | Read trace events for a case run |

## Common Models

### Session Record

```json
{
  "session_id": "chat-chat-20260616-172258-ba30f8",
  "session_dir": "E:\\Projects\\lora\\.lora\\sessions\\chat-chat-...",
  "case_id": "chat",
  "mode": "chat",
  "created_at": "2026-06-16T17:22:58.000000+00:00",
  "updated_at": "2026-06-16T17:23:04.000000+00:00",
  "title": "hello",
  "last_case_run_id": "run-20260616-172258-902408-4d4b23",
  "last_case_run_status": "passed"
}
```

### API Event

All streamed chat events use the same JSON shape in the `data:` line:

```json
{
  "type": "chat.completed",
  "session_id": "chat-chat-...",
  "case_run_id": "run-...",
  "payload": {}
}
```

## Endpoints

### `GET /health`

Returns service status.

Response:

```json
{
  "status": "ok",
  "service": "lora-api"
}
```

### `GET /projects`

Returns the active workspace. `projects` currently contains the active project only; it is shaped as a list for later multi-project support.

Response:

```json
{
  "active": {
    "workspace_root": "E:\\Projects\\lora",
    "lora_root": "E:\\Projects\\lora\\.lora"
  },
  "projects": [
    {
      "workspace_root": "E:\\Projects\\lora",
      "lora_root": "E:\\Projects\\lora\\.lora"
    }
  ]
}
```

### `GET /settings`

Returns resolved runtime settings that are safe for display. Raw API keys are not returned.

Response:

```json
{
  "workspace_root": "E:\\Projects\\lora",
  "lora_root": "E:\\Projects\\lora\\.lora",
  "agent": "dev",
  "model": "deepseek-v4-flash",
  "api_key_source": "env:DEEPSEEK_API_KEY",
  "base_url": "https://api.deepseek.com",
  "max_steps": -1
}
```

### `GET /sessions`

Lists chat sessions, sorted by `updated_at` descending.

Response:

```json
{
  "sessions": []
}
```

### `POST /sessions`

Creates a session.

Request:

```json
{
  "case_id": "chat",
  "mode": "chat"
}
```

Response: a session record.

### `GET /sessions/{session_id}`

Loads one session, including conversation history and session metadata.

Response:

```json
{
  "session": {
    "session_id": "chat-chat-...",
    "session_dir": "E:\\Projects\\lora\\.lora\\sessions\\chat-chat-...",
    "case_id": "chat",
    "mode": "chat",
    "created_at": "2026-06-16T17:22:58.000000+00:00",
    "updated_at": "2026-06-16T17:23:04.000000+00:00",
    "title": "hello",
    "last_case_run_id": "run-...",
    "last_case_run_status": "passed"
  },
  "history": [
    {
      "role": "user",
      "content": "hello"
    },
    {
      "role": "assistant",
      "content": "..."
    }
  ],
  "metadata": {}
}
```

### `DELETE /sessions/{session_id}`

Deletes session data from the Lora session root and compatibility session root when present.

Response:

```json
{
  "deleted": true
}
```

### `POST /chat/stream`

Runs one chat turn. The response is `text/event-stream`.

Request:

```json
{
  "message": "hello",
  "session_id": "chat-chat-...",
  "case_id": "chat",
  "turn_id": "turn-0001"
}
```

Only `message` is required. When `session_id` is omitted, the API creates a new chat session.

Stream event examples:

```text
event: chat.started
data: {"type":"chat.started","session_id":"chat-chat-...","case_run_id":"run-...","payload":{"session_id":"chat-chat-...","case_id":"chat","case_run_id":"run-...","run_dir":"..."}}

event: assistant.delta
data: {"type":"assistant.delta","session_id":"chat-chat-...","case_run_id":"run-...","payload":{"delta":"partial text"}}

event: runtime.message
data: {"type":"runtime.message","session_id":"chat-chat-...","case_run_id":"run-...","payload":{"role":"assistant","content":"...","message_type":"conversation.assistant_message","payload":{},"is_delta":false}}

event: chat.completed
data: {"type":"chat.completed","session_id":"chat-chat-...","case_run_id":"run-...","payload":{"status":"passed","final_answer":"...","error":null,"message_count":2,"turn_id":"turn-0001"}}
```

Possible event types:

- `chat.started`
- `assistant.delta`
- `runtime.message`
- `chat.completed`
- `chat.error`

On runtime failure, `chat.error` includes:

```json
{
  "error": "message",
  "error_type": "RuntimeError"
}
```

### `GET /traces/{session_id}/{case_run_id}`

Returns `events.jsonl` records for a case run.

Response:

```json
{
  "session_id": "chat-chat-...",
  "case_run_id": "run-...",
  "events": []
}
```

## Frontend Integration Notes

- Use `POST /chat/stream` for active turns; do not poll while a turn is running.
- Store `session_id` from `chat.started` or the created session response.
- Store `case_run_id` from stream events so the UI can call `/traces/{session_id}/{case_run_id}`.
- Use `/settings` for display and diagnostics only. It intentionally omits raw API keys.
- Use `contracts/openapi/lora-api.json` as the source for generated TypeScript clients.
