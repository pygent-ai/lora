# Lora API

Local FastAPI service for the Electron + React desktop shell.

This layer adapts `src/lora` core capabilities to HTTP and server-sent event boundaries. Keep business logic in `src/lora`; routers and services here should stay thin and desktop-facing.

- `container.py`: API composition root and resource lifecycle.
- `dependencies.py`: FastAPI dependency adapter only.
- `routers/`: HTTP transport and request validation.
- `services/`: use-case adapters with no dependency on FastAPI wiring.
- `models/`: API-specific event and payload contracts.

Task duration is owned by the case run's `run_metadata.json`. Session history
messages expose `run_timing` with `case_run_id`, `started_at`, `finished_at`, and
`status`; timestamps are UTC ISO 8601 strings, and unavailable values are null.
Checkpoint sequence and run identity associate this data with conversation
boundaries without adding display metadata to model-visible history. The current
checkpoint store is the authoritative source for restored message timing.

Chat start and terminal SSE events expose the same object in `data.run_timing`,
including on reconnect. Active streams wait for final run persistence before
publishing a terminal event. The desktop uses this contract for both live and
restored messages, freezes completed durations, and omits elapsed time when it
is unknown. Durations are displayed in whole seconds, rounded down.

Session detail exposes `runtime_execution_id` and `run_history_start_index` for
the running case. Reopening a session resumes the execution from its native
journal. Closing an SSE subscription does not cancel the task. Application
lifespan cleanup closes the runtime on both normal and exceptional exits.
