# PySide6 Conversation Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first working PySide6 desktop application for chatting with Lora, managing chat sessions, and inspecting runtime events.

**Architecture:** Add a top-level `gui` package beside `lora`. Keep testable data/session helpers separate from Qt widgets, then compose them in `gui.main_window`. `gui` imports Lora core APIs; `lora` does not import `gui`.

**Tech Stack:** Python 3.13, PySide6, unittest, existing Lora runtime/session/config APIs.

---

## File Structure

- Create `src/gui/__init__.py`: GUI package marker.
- Create `src/gui/__main__.py`: console script entry point.
- Create `src/gui/app.py`: Qt application startup and stylesheet.
- Create `src/gui/main_window.py`: three-pane shell and chat orchestration.
- Create `src/gui/session_model.py`: list/create/load chat sessions with `SessionManager`.
- Create `src/gui/workers.py`: Qt background worker for `AgentRuntimeAdapter.run_turn()`.
- Create `src/gui/widgets/chat.py`: chat transcript and input widgets.
- Create `src/gui/widgets/sessions.py`: session sidebar widget.
- Create `src/gui/widgets/inspector.py`: run trace inspector widget.
- Create `src/gui/widgets/settings.py`: runtime settings dialog.
- Modify `pyproject.toml`: add PySide6 dependency, `lora-gui` script, and `src/gui` package.
- Create `tests/unit/test_gui_session_model.py`: non-visual session model coverage.
- Create `tests/unit/test_gui_worker.py`: worker helper coverage without launching a real agent.

## Task 1: Session Model

**Files:**
- Create: `tests/unit/test_gui_session_model.py`
- Create: `src/gui/session_model.py`
- Create: `src/gui/__init__.py`

- [ ] **Step 1: Write failing session model tests**

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.config import load_run_config
from lora.session import SessionManager

from gui.session_model import ChatSessionRecord, ChatSessionStore


class GuiSessionModelTests(unittest.TestCase):
    def test_list_chat_sessions_returns_only_chat_mode_sorted_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_run_config(workspace_root=tmp)
            manager = SessionManager(config)
            older = manager.create("chat", mode="chat").session_id
            manager.create("demo", mode="e2e")
            newer = manager.create("chat", mode="chat").session_id

            records = ChatSessionStore(manager).list_chat_sessions()

            self.assertEqual([record.session_id for record in records], [newer, older])
            self.assertTrue(all(isinstance(record, ChatSessionRecord) for record in records))
            self.assertTrue(all(record.mode == "chat" for record in records))

    def test_create_chat_session_uses_chat_case_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_run_config(workspace_root=tmp)
            manager = SessionManager(config)

            record = ChatSessionStore(manager).create_chat_session()

            self.assertEqual(record.case_id, "chat")
            self.assertEqual(record.mode, "chat")
            self.assertTrue(Path(record.session_dir).exists())
```

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run python -m unittest tests.unit.test_gui_session_model -v`

Expected: fail because `gui.session_model` does not exist.

- [ ] **Step 3: Implement session model**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lora.io import read_json
from lora.session import SessionManager


@dataclass(frozen=True, slots=True)
class ChatSessionRecord:
    session_id: str
    session_dir: str
    case_id: str
    mode: str
    created_at: str
    updated_at: str
    title: str


class ChatSessionStore:
    def __init__(self, manager: SessionManager):
        self.manager = manager

    def list_chat_sessions(self) -> list[ChatSessionRecord]:
        sessions_root = Path(self.manager.sessions_root)
        if not sessions_root.exists():
            return []
        records: list[ChatSessionRecord] = []
        for metadata_path in sessions_root.glob("*/metadata.json"):
            metadata = read_json(metadata_path)
            if metadata.get("mode") != "chat":
                continue
            records.append(_record_from_metadata(metadata_path.parent, metadata))
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def create_chat_session(self) -> ChatSessionRecord:
        ref = self.manager.create("chat", mode="chat")
        metadata = read_json(Path(ref.session_dir) / "metadata.json")
        return _record_from_metadata(Path(ref.session_dir), metadata)


def _record_from_metadata(session_dir: Path, metadata: dict[str, object]) -> ChatSessionRecord:
    session_id = str(metadata["session_id"])
    case_id = str(metadata.get("case_id") or "chat")
    mode = str(metadata.get("mode") or "chat")
    created_at = str(metadata.get("created_at") or "")
    updated_at = str(metadata.get("updated_at") or created_at)
    return ChatSessionRecord(
        session_id=session_id,
        session_dir=str(session_dir),
        case_id=case_id,
        mode=mode,
        created_at=created_at,
        updated_at=updated_at,
        title=f"Chat {session_id}",
    )
```

- [ ] **Step 4: Run tests to verify GREEN**

Run: `uv run python -m unittest tests.unit.test_gui_session_model -v`

Expected: pass.

## Task 2: Worker Data Helpers

**Files:**
- Create: `tests/unit/test_gui_worker.py`
- Create: `src/gui/workers.py`

- [ ] **Step 1: Write failing worker helper tests**

```python
from __future__ import annotations

import unittest

from lora.runtime import RuntimeMessage

from gui.workers import InspectorEvent, runtime_message_to_inspector_event


class GuiWorkerTests(unittest.TestCase):
    def test_runtime_message_to_inspector_event_formats_tool_call(self) -> None:
        message = RuntimeMessage(
            role="assistant",
            content="",
            payload={
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "read", "arguments": '{"path": "README.md"}'},
                    }
                ]
            },
        )

        event = runtime_message_to_inspector_event(message)

        self.assertEqual(
            event,
            InspectorEvent(kind="tool", title="Tool call: read", detail='{"path": "README.md"}', tone="accent"),
        )

    def test_runtime_message_to_inspector_event_formats_tool_result(self) -> None:
        message = RuntimeMessage(
            role="tool",
            content='{"status": "success", "result": "done"}',
            payload={"tool_call_id": "call_1"},
        )

        event = runtime_message_to_inspector_event(message)

        self.assertEqual(event.kind, "tool")
        self.assertEqual(event.title, "Tool result: call_1 success")
        self.assertEqual(event.detail, "done")
        self.assertEqual(event.tone, "ok")
```

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run python -m unittest tests.unit.test_gui_worker -v`

Expected: fail because `gui.workers` does not exist.

- [ ] **Step 3: Implement worker helper and Qt worker**

Implement `InspectorEvent`, `runtime_message_to_inspector_event()`, and `ChatTurnWorker`. The worker should use Qt signals when PySide6 is available and run one chat turn using `asyncio.run()`.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `uv run python -m unittest tests.unit.test_gui_worker -v`

Expected: pass.

## Task 3: PySide6 Application Shell

**Files:**
- Create: `src/gui/app.py`
- Create: `src/gui/__main__.py`
- Create: `src/gui/main_window.py`
- Create: `src/gui/widgets/chat.py`
- Create: `src/gui/widgets/sessions.py`
- Create: `src/gui/widgets/inspector.py`
- Create: `src/gui/widgets/settings.py`
- Create: `src/gui/widgets/__init__.py`

- [ ] **Step 1: Add import smoke test manually**

Run: `uv run python -c "import gui.app, gui.main_window; print('ok')"`

Expected before implementation: fail because modules do not exist.

- [ ] **Step 2: Implement widgets and shell**

Build the three-pane UI:

- `SessionSidebar` emits `new_chat_requested`, `session_selected`, and `settings_requested`.
- `ChatPane` emits `message_submitted` and exposes methods to render history, append user messages, start/append/finish assistant messages, and show errors.
- `TraceInspector` exposes methods to reset context, set status, add events, and show config.
- `SettingsDialog` returns workspace/config/agent/model/max-steps values.
- `MainWindow` composes these widgets, loads config, creates sessions, sends chat turns, and updates widgets from worker signals.

- [ ] **Step 3: Run smoke test to verify GREEN**

Run: `uv run python -c "import gui.app, gui.main_window; print('ok')"`

Expected: prints `ok`.

## Task 4: Packaging

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add package/script/dependency expectations**

Update `pyproject.toml` so:

```toml
dependencies = [
    "pygent-ai==0.1.11",
    "PySide6>=6.10.1",
]

[project.scripts]
lora = "lora.cli:main"
lora-gui = "gui.__main__:main"

[tool.hatch.build.targets.wheel]
packages = ["src/lora", "src/gui"]
```

- [ ] **Step 2: Verify entry point help path**

Run: `uv run python -m gui --help`

Expected: exits 0 and prints usage including `--workspace-root`.

## Task 5: Verification

**Files:**
- No new files unless test fixes are needed.

- [ ] **Step 1: Run focused GUI tests**

Run: `uv run python -m unittest tests.unit.test_gui_session_model tests.unit.test_gui_worker -v`

Expected: pass.

- [ ] **Step 2: Run full test suite**

Run: `uv run python -m unittest discover -s tests`

Expected: pass.

- [ ] **Step 3: Launch GUI in offscreen smoke mode**

Run: `$env:QT_QPA_PLATFORM='offscreen'; uv run python -m gui --smoke`

Expected: exits 0 after constructing the application window.
