# Repository Layout

This repository is moving toward a local desktop architecture with three main layers:

- `src/lora`: Python core domain logic. It should not import desktop, React, Electron, or FastAPI code.
- `src/lora_api`: local FastAPI service layer. It adapts core Lora capabilities to HTTP and event streams.
- `apps/desktop`: Electron and React desktop shell. It talks to `lora_api` through typed contracts.

The previous PySide6 GUI has been moved to `legacy/pyside_gui`. It is kept as an archive/reference while the Electron + React shell is built, and it is no longer part of the active package layout.

Project-level documentation is organized under `docs/`:

- `docs/api`: local FastAPI service and generated-contract notes.
- `docs/cli`: command-line usage.
- `docs/guides`: development and operations guides.
- `docs/design`: subsystem design notes.
- `docs/planning`: historical implementation plans and specs.
- `docs/backlog`: deferred or unimplemented work.
