# Lora API

Local FastAPI service for the Electron + React desktop shell.

This layer adapts `src/lora` core capabilities to HTTP and server-sent event boundaries. Keep business logic in `src/lora`; routers and services here should stay thin and desktop-facing.

- `container.py`: API composition root and resource lifecycle.
- `dependencies.py`: FastAPI dependency adapter only.
- `routers/`: HTTP transport and request validation.
- `services/`: use-case adapters with no dependency on FastAPI wiring.
- `models/`: API-specific event and payload contracts.
