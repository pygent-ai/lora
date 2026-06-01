# PySide6 Conversation Workbench Design

Date: 2026-06-01

## Goal

Build Lora into a usable PySide6 desktop application where users can chat with Lora, configure the active agent/runtime, and manage prior sessions from one place.

The first version focuses on the Conversation Workbench: a three-pane desktop shell that makes the core loop obvious and useful immediately:

- choose or create a chat session,
- send messages to Lora,
- stream assistant output,
- inspect tool calls, file effects, trace events, and run status while the turn executes,
- preserve sessions and run artifacts through the existing `.lora` storage model.

## Product Direction

The app should feel like a calm engineering workbench, not a marketing page or a decorative chatbot. The visual language is compact, legible, and focused:

- dark left navigation for identity and session selection,
- light central chat surface for readability,
- right-side inspector with dense cards, tabs, and status colors,
- restrained accent colors for active run state, tool calls, file effects, and errors.

The first memorable quality should be observability: users are not only chatting with Lora, they can see what Lora is doing.

## First-Version Scope

In scope:

- PySide6 desktop app package under `gui`, kept as a sibling of the core `lora` package.
- A GUI script entry point such as `lora-gui`.
- Main window with session sidebar, chat pane, and trace inspector.
- New chat session creation.
- Resume existing chat sessions from `.lora/sessions`.
- Send one user message at a time.
- Stream assistant text into the current assistant message.
- Display runtime messages for assistant tool calls and tool results.
- Display current turn status, session id, case run id, and errors.
- Basic runtime settings dialog for workspace root, config path, agent alias, model override, and max steps.
- Reuse existing Lora APIs instead of shelling out to the CLI.

Out of scope for the first version:

- Full `lora.yaml` profile CRUD.
- Case authoring UI.
- Regression suite dashboards.
- Repair workflow UI.
- Rich diff viewer.
- Packaging as an installer.

These can be added later without changing the main shell.

## Architecture

Add a GUI package next to the core `lora` package:

```text
src/gui/
  __init__.py
  __main__.py
  app.py
  main_window.py
  workers.py
  session_model.py
  widgets/
    chat.py
    sessions.py
    inspector.py
    settings.py
```

This keeps `lora` focused on agent/runtime behavior and makes `gui` the application layer. `gui` may import from `lora`, but `lora` must not import from `gui`.

`app.py` owns Qt application startup and applies the stylesheet.

`main_window.py` composes the three main panes and coordinates user actions.

`workers.py` contains Qt worker objects that run async Lora turns in a background thread. Workers call `AgentRuntimeAdapter.run_turn()` and emit Qt signals for assistant deltas, runtime messages, errors, and completion.

`session_model.py` adapts `SessionManager` and `.lora/sessions` into view-friendly records. It should be responsible for listing, loading, and creating chat sessions.

The GUI reuses existing core APIs from `lora`:

- `load_run_config()` for runtime configuration.
- `SessionManager` for session lifecycle.
- `AgentRuntimeAdapter.run_turn()` for chat execution.
- `EventStore` and run artifact files for inspector data.

The GUI must not duplicate session persistence logic. Packaging should include both `src/lora` and `src/gui`, while the existing `lora` CLI remains owned by the core package.

## Main UI

### Session Sidebar

The left pane shows:

- app name and current workspace,
- `New Chat` command,
- a scrollable list of chat sessions,
- active agent/model status,
- settings command.

Selecting a session loads its stored history into the chat pane. Creating a session calls `SessionManager.create("chat", mode="chat")`.

### Chat Pane

The center pane shows:

- conversation history,
- user and assistant message bubbles,
- streaming assistant text,
- input field,
- send button,
- disabled/sending state while a turn is running,
- error banner when a turn fails.

For the first version, turns run serially. If a turn is active, the send button is disabled. A stop button can be visually present but may be disabled until cancellation is implemented.

### Trace Inspector

The right pane shows:

- current session id,
- current case run id,
- current status,
- tabbed views for `Events`, `Tools`, `Files`, and `Config`.

`Events` displays chronological trace events for the active run. `Tools` highlights assistant tool calls and results. `Files` summarizes file impact events when available. `Config` shows effective runtime metadata such as agent alias, model name, API key source, base URL, and max steps.

The inspector should update live from worker callbacks and can refresh from files after a turn completes.

### Settings Dialog

The settings dialog supports temporary runtime configuration:

- workspace root,
- config path,
- agent alias,
- model override,
- max steps.

Applying settings reloads `RunConfig` and recreates the `SessionManager`. Persisting these values back to `lora.yaml` is explicitly deferred.

## Data Flow

1. App starts and loads `RunConfig`.
2. App creates `SessionManager`.
3. Sidebar lists existing chat sessions.
4. User creates or selects a chat session.
5. User sends a message.
6. Main window starts a chat case run through `SessionManager.start_case_run(session_id, "chat", run_config=config)`.
7. Worker creates `AgentRuntimeAdapter` and calls `run_turn()`.
8. Worker emits assistant deltas and runtime messages as Qt signals.
9. Main window updates chat and inspector.
10. Worker finishes the case run with `SessionManager.finish_case_run()`.
11. Sidebar and inspector refresh from persisted artifacts.

## Error Handling

Runtime exceptions are surfaced in the chat pane and inspector. The worker should preserve partial output and mark the run as `error`.

Configuration errors should appear before a run starts, with enough detail for the user to fix workspace, config path, agent alias, or missing API key state.

Missing API keys are not treated as GUI failures because current Lora behavior can return a deterministic fallback response.

## Testing

Automated coverage should focus on the non-visual parts:

- session listing filters chat sessions correctly,
- chat worker emits expected signals using a fake agent,
- settings reload produces the expected `RunConfig`,
- main window can be instantiated in offscreen mode.

Manual verification:

- launch the app,
- create a new chat,
- send a message without a real API key and verify fallback behavior,
- confirm session persistence under `.lora/sessions`,
- confirm right inspector shows status and runtime messages.

## Implementation Notes

PySide6 should be added as a project dependency only when implementation begins. The design assumes Python 3.13 compatibility.

Implementation should update packaging metadata so both packages are included:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/lora", "src/gui"]
```

It should also add a desktop app command:

```toml
[project.scripts]
lora = "lora.cli:main"
lora-gui = "gui.__main__:main"
```

Qt styling should be local to the GUI package. Core Lora modules should remain UI-agnostic.

The implementation should keep the first version narrow. A polished chat workbench is more valuable than a broad but shallow dashboard.
