# Lora API

Local FastAPI service for the Electron + React desktop shell.

This layer adapts `src/lora` core capabilities to HTTP and server-sent event boundaries. Keep business logic in `src/lora`; routers and services here should stay thin and desktop-facing.
