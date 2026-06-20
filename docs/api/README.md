# API Docs

The local FastAPI layer lives in `src/lora_api` and is exposed through the `lora-api` console script.

Start here:

- [Local API Service](local-service.md): route-level API documentation for the desktop shell.
- `contracts/openapi/lora-api.json`: generated OpenAPI contract for typed clients.

Implemented route groups:

- Health: `GET /health`
- Projects and settings: `GET /projects`, `GET /settings`
- Sessions: `GET /sessions`, `POST /sessions`, `GET /sessions/{session_id}`, `DELETE /sessions/{session_id}`
- Chat stream: `POST /chat/stream`
- Traces: `GET /traces/{session_id}/{case_run_id}`
