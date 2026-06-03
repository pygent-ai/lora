# Concurrent GUI Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow multiple chat sessions to keep running concurrently while the user freely opens other historical sessions in the GUI.

**Architecture:** Keep `ChatPane` and `TraceInspector` as stateless renderers for the currently visible session. Add a per-session runtime registry in `MainWindow` that owns each running worker, records live updates in arrival order, and only paints updates when their `session_id` matches the visible session. The GUI supports one active turn per session, but different sessions may run at the same time.

**Tech Stack:** Python 3.13, PySide6 `QObject`/`Signal`/`QThread`, existing `SessionManager`, existing `ChatPane`, existing `TraceInspector`, `unittest` offscreen Qt tests.

---

## Scope Rules

- Multiple sessions may run at the same time.
- The same session may not start a second turn while its current turn is running.
- Switching sessions never stops any running worker.
- Hidden running sessions cache live assistant deltas and runtime events, then replay them when selected.
- Completed hidden sessions do not need an in-memory transcript after completion because `AgentRuntimeAdapter.run_turn()` persists final history.
- Deleting, changing settings, or closing the window while runs are active must not silently destroy active workers.

## File Structure

- Modify `src/gui/workers.py`: include `session_id` in every worker signal payload.
- Modify `src/gui/main_window.py`: replace the single `_thread/_worker` pair with a `_running_sessions` registry and session-aware callback routing.
- Modify `src/gui/session_model.py`: add a lightweight `runtime_status` field to `ChatSessionRecord`.
- Modify `src/gui/widgets/sessions.py`: render each session row's runtime status.
- Modify `src/gui/theme.py`: add restrained styling for running/error/done session status labels.
- Modify `tests/unit/test_gui_worker.py`: verify worker runtime events carry the originating session id.
- Modify `tests/unit/test_gui_main_window.py`: verify switching sessions does not clear running state and hidden updates do not paint the visible chat.
- Modify `tests/unit/test_gui_session_sidebar.py`: verify session rows show runtime status.

---

### Task 1: Make Worker Signals Session-Aware

**Files:**
- Modify: `src/gui/workers.py`
- Test: `tests/unit/test_gui_worker.py`

- [ ] **Step 1: Write the failing worker signal test**

Add this test to `GuiWorkerTests` in `tests/unit/test_gui_worker.py`:

```python
    def test_worker_runtime_event_signal_includes_session_id(self) -> None:
        worker = ChatTurnWorker(
            config=object(),
            manager=object(),
            session_id="chat-alpha",
            user_input="hello",
            turn_index=1,
        )
        emitted: list[tuple[str, InspectorEvent]] = []
        worker.runtime_event.connect(lambda session_id, event: emitted.append((session_id, event)))

        worker._emit_runtime_message(
            RuntimeMessage(
                role="assistant",
                content="",
                payload={"tool_calls": [{"id": "call_1", "function": {"name": "read", "arguments": "{}"}}]},
            )
        )

        self.assertEqual(emitted[0][0], "chat-alpha")
        self.assertEqual(emitted[0][1].title, "Tool call: read")
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```powershell
uv run pytest tests/unit/test_gui_worker.py::GuiWorkerTests::test_worker_runtime_event_signal_includes_session_id -q
```

Expected: FAIL because `runtime_event` currently emits only the event object.

- [ ] **Step 3: Update worker signal definitions and emits**

In `src/gui/workers.py`, change `ChatTurnWorker` signals to:

```python
class ChatTurnWorker(QObject):
    assistant_delta = Signal(str, str)
    runtime_event = Signal(str, object)
    run_started = Signal(str, object)
    completed = Signal(str, object)
    failed = Signal(str, str)
```

Update `run()`:

```python
self.run_started.emit(self.session_id, run_ref)
```

Update the `adapter.run_turn()` call:

```python
on_assistant_delta=self._emit_assistant_delta,
on_runtime_message=self._emit_runtime_message,
```

Update success and failure emits:

```python
self.completed.emit(self.session_id, {**run_ref.to_dict(), **result})
```

```python
self.failed.emit(self.session_id, str(exc))
```

Add this method to `ChatTurnWorker`:

```python
    def _emit_assistant_delta(self, delta: str) -> None:
        self.assistant_delta.emit(self.session_id, delta)
```

Update `_emit_runtime_message()`:

```python
    def _emit_runtime_message(self, message: RuntimeMessage) -> None:
        for event in runtime_message_to_inspector_events(message):
            self.runtime_event.emit(self.session_id, event)
```

- [ ] **Step 4: Run worker tests**

Run:

```powershell
uv run pytest tests/unit/test_gui_worker.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/workers.py tests/unit/test_gui_worker.py
git commit -m "feat(gui): tag worker signals with session id"
```

---

### Task 2: Add Per-Session Runtime Registry

**Files:**
- Modify: `src/gui/main_window.py`
- Test: `tests/unit/test_gui_main_window.py`

- [ ] **Step 1: Write failing routing tests**

Add these tests to `GuiMainWindowTests` in `tests/unit/test_gui_main_window.py`:

```python
    def test_hidden_running_session_updates_are_cached_not_painted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)
            first = window.store.create_chat_session().session_id
            second = window.store.create_chat_session().session_id
            window.select_session(first)
            window._running_sessions[first] = window._new_active_run(
                session_id=first,
                user_input="first prompt",
                turn_index=1,
                thread=None,
                worker=None,
            )

            window.select_session(second)
            window._on_assistant_delta(first, "hidden answer")

            visible_texts = [
                label.text()
                for label in window.chat.findChildren(type(window.chat.header))
                if label.objectName() == "AssistantBubble"
            ]
            self.assertEqual(visible_texts, [])
            self.assertEqual(window._running_sessions[first].updates[0].kind, "assistant_delta")

    def test_select_running_session_replays_cached_live_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)
            session_id = window.store.create_chat_session().session_id
            window._running_sessions[session_id] = window._new_active_run(
                session_id=session_id,
                user_input="live prompt",
                turn_index=1,
                thread=None,
                worker=None,
            )
            window._on_assistant_delta(session_id, "live answer")

            window.select_session(session_id)

            bubble_texts = [
                label.text()
                for label in window.chat.findChildren(type(window.chat.header))
                if label.objectName() in {"UserBubble", "AssistantBubble"}
            ]
            self.assertEqual(bubble_texts, ["live prompt", "live answer"])
            self.assertFalse(window.chat.input.isEnabled())
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_hidden_running_session_updates_are_cached_not_painted tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_select_running_session_replays_cached_live_updates -q
```

Expected: FAIL because `_running_sessions`, `_new_active_run`, and session-aware callbacks do not exist.

- [ ] **Step 3: Add runtime dataclasses**

At the top of `src/gui/main_window.py`, add:

```python
from dataclasses import dataclass, field
from typing import Literal
```

Below imports, add:

```python
@dataclass(slots=True)
class _LiveUpdate:
    kind: Literal["assistant_delta", "runtime_event"]
    payload: str | InspectorEvent


@dataclass(slots=True)
class _ActiveRun:
    session_id: str
    user_input: str
    turn_index: int
    thread: QThread | None
    worker: ChatTurnWorker | None
    updates: list[_LiveUpdate] = field(default_factory=list)
    run_ref: object | None = None
    status: str = "running"
    error: str | None = None
```

- [ ] **Step 4: Replace single worker fields with registry**

In `MainWindow.__init__`, replace:

```python
self._thread: QThread | None = None
self._worker: ChatTurnWorker | None = None
self._active_session_id: str | None = None
```

with:

```python
self._running_sessions: dict[str, _ActiveRun] = {}
self._active_session_id: str | None = None
```

Add this helper method to `MainWindow`:

```python
    def _new_active_run(
        self,
        *,
        session_id: str,
        user_input: str,
        turn_index: int,
        thread: QThread | None,
        worker: ChatTurnWorker | None,
    ) -> _ActiveRun:
        return _ActiveRun(
            session_id=session_id,
            user_input=user_input,
            turn_index=turn_index,
            thread=thread,
            worker=worker,
        )
```

- [ ] **Step 5: Add replay helper**

Add this method to `MainWindow`:

```python
    def _render_running_session(self, active_run: _ActiveRun) -> None:
        case_run_id = getattr(active_run.run_ref, "case_run_id", "") if active_run.run_ref is not None else ""
        self.inspector.reset_context(session_id=active_run.session_id, case_run_id=case_run_id)
        self.chat.add_message("user", active_run.user_input)
        self.chat.start_assistant_message()
        for update in active_run.updates:
            if update.kind == "assistant_delta":
                self.chat.append_assistant_delta(str(update.payload))
            elif isinstance(update.payload, InspectorEvent):
                self.inspector.add_event(update.payload)
                self.chat.add_runtime_event(update.payload)
        self.chat.set_running(True)
        self.chat.set_run_status(active_run.status)
        self.inspector.set_status(active_run.status)
```

- [ ] **Step 6: Update `select_session()`**

At the end of `select_session()`, after `_turn_index = _next_turn_index(session.history)`, add:

```python
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            self._render_running_session(active_run)
        else:
            self.chat.set_running(False)
```

Ensure this is placed after `self._load_last_run_trace(...)` so running replay wins over persisted trace when the session is live.

- [ ] **Step 7: Add session-aware delta callback**

Replace `_on_runtime_event`, `_on_run_started`, `_on_turn_completed`, and `_on_turn_failed` signatures in later tasks. For this task, add:

```python
    def _on_assistant_delta(self, session_id: str, delta: str) -> None:
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            active_run.updates.append(_LiveUpdate("assistant_delta", delta))
        if session_id == self._active_session_id:
            self.chat.append_assistant_delta(delta)
```

- [ ] **Step 8: Run routing tests**

Run:

```powershell
uv run pytest tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_hidden_running_session_updates_are_cached_not_painted tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_select_running_session_replays_cached_live_updates -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add src/gui/main_window.py tests/unit/test_gui_main_window.py
git commit -m "feat(gui): cache live updates per session"
```

---

### Task 3: Start Multiple Session Workers Concurrently

**Files:**
- Modify: `src/gui/main_window.py`
- Test: `tests/unit/test_gui_main_window.py`

- [ ] **Step 1: Write failing send tests**

Add this test to `GuiMainWindowTests`:

```python
    def test_different_sessions_can_start_independent_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)
            first = window.store.create_chat_session().session_id
            second = window.store.create_chat_session().session_id
            started: list[tuple[str, str]] = []

            def fake_start(session_id: str, message: str, turn_index: int):
                started.append((session_id, message))
                active_run = window._new_active_run(
                    session_id=session_id,
                    user_input=message,
                    turn_index=turn_index,
                    thread=None,
                    worker=None,
                )
                window._running_sessions[session_id] = active_run
                return active_run

            window._start_chat_worker = fake_start

            window.select_session(first)
            window.send_message("first message")
            window.select_session(second)
            window.send_message("second message")

            self.assertEqual(started, [(first, "first message"), (second, "second message")])
            self.assertIn(first, window._running_sessions)
            self.assertIn(second, window._running_sessions)
```

Add this same-session guard test:

```python
    def test_same_session_cannot_start_second_run_while_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)
            session_id = window.store.create_chat_session().session_id
            window.select_session(session_id)
            window._running_sessions[session_id] = window._new_active_run(
                session_id=session_id,
                user_input="already running",
                turn_index=1,
                thread=None,
                worker=None,
            )

            window.send_message("second message")

            user_bubbles = [
                label.text()
                for label in window.chat.findChildren(type(window.chat.header))
                if label.objectName() == "UserBubble"
            ]
            self.assertNotIn("second message", user_bubbles)
```

- [ ] **Step 2: Run the new send tests and verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_different_sessions_can_start_independent_runs tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_same_session_cannot_start_second_run_while_running -q
```

Expected: FAIL because `_start_chat_worker` is not extracted and same-session guard does not exist.

- [ ] **Step 3: Extract worker startup**

In `send_message()`, after ensuring there is an active session, add:

```python
        session_id = self._active_session_id
        if session_id in self._running_sessions:
            self.chat.set_run_status("running")
            self.inspector.set_status("Running")
            return
```

Replace inline thread/worker creation with:

```python
        self._start_chat_worker(session_id, message, self._turn_index)
```

Add this method:

```python
    def _start_chat_worker(self, session_id: str, message: str, turn_index: int) -> _ActiveRun:
        thread = QThread(self)
        worker = ChatTurnWorker(
            config=self.config,
            manager=self.manager,
            session_id=session_id,
            user_input=message,
            turn_index=turn_index,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.run_started.connect(self._on_run_started)
        worker.assistant_delta.connect(self._on_assistant_delta)
        worker.runtime_event.connect(self._on_runtime_event)
        worker.completed.connect(self._on_turn_completed)
        worker.failed.connect(self._on_turn_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda session_id=session_id: self._clear_worker_refs(session_id))
        active_run = self._new_active_run(
            session_id=session_id,
            user_input=message,
            turn_index=turn_index,
            thread=thread,
            worker=worker,
        )
        self._running_sessions[session_id] = active_run
        thread.start()
        return active_run
```

- [ ] **Step 4: Keep visible UI startup behavior**

Keep these lines in `send_message()` before `_start_chat_worker(...)`:

```python
        self.chat.add_message("user", message)
        self.chat.start_assistant_message()
        self.chat.set_running(True)
        self.inspector.set_status("Running")
```

Only run them after the same-session guard.

- [ ] **Step 5: Run send tests**

Run:

```powershell
uv run pytest tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_different_sessions_can_start_independent_runs tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_same_session_cannot_start_second_run_while_running -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/gui/main_window.py tests/unit/test_gui_main_window.py
git commit -m "feat(gui): run independent sessions concurrently"
```

---

### Task 4: Route Run Start, Runtime Events, Completion, and Failure

**Files:**
- Modify: `src/gui/main_window.py`
- Test: `tests/unit/test_gui_main_window.py`

- [ ] **Step 1: Write failing completion routing test**

Add this test:

```python
    def test_hidden_completion_does_not_overwrite_visible_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)
            first = window.store.create_chat_session().session_id
            second = window.store.create_chat_session().session_id
            window._running_sessions[first] = window._new_active_run(
                session_id=first,
                user_input="first prompt",
                turn_index=1,
                thread=None,
                worker=None,
            )
            window.select_session(second)

            window._on_turn_completed(first, {"status": "passed", "final_answer": "hidden final"})

            self.assertEqual(window._active_session_id, second)
            visible_texts = [
                label.text()
                for label in window.chat.findChildren(type(window.chat.header))
                if label.objectName() == "AssistantBubble"
            ]
            self.assertEqual(visible_texts, [])
            self.assertNotIn(first, window._running_sessions)
```

- [ ] **Step 2: Run completion test and verify it fails**

Run:

```powershell
uv run pytest tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_hidden_completion_does_not_overwrite_visible_session -q
```

Expected: FAIL because `_on_turn_completed` currently assumes the active visible session.

- [ ] **Step 3: Update run-start callback**

Replace `_on_run_started` with:

```python
    def _on_run_started(self, session_id: str, run_ref: object) -> None:
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            active_run.run_ref = run_ref
            active_run.status = "Running"
        if session_id == self._active_session_id:
            case_run_id = getattr(run_ref, "case_run_id", "")
            self.inspector.reset_context(session_id=session_id, case_run_id=case_run_id)
            self.inspector.set_status("Running")
```

- [ ] **Step 4: Update runtime event callback**

Replace `_on_runtime_event` with:

```python
    def _on_runtime_event(self, session_id: str, event: InspectorEvent) -> None:
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            active_run.updates.append(_LiveUpdate("runtime_event", event))
        if session_id == self._active_session_id:
            self.inspector.add_event(event)
            self.chat.add_runtime_event(event)
```

- [ ] **Step 5: Update completion callback**

Replace `_on_turn_completed` with:

```python
    def _on_turn_completed(self, session_id: str, result: object) -> None:
        payload = result if isinstance(result, dict) else {}
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            active_run.status = str(payload.get("status") or "Finished")
        if session_id == self._active_session_id:
            self.chat.finish_assistant_message(str(payload.get("final_answer") or ""))
            self.chat.set_running(False)
            self.chat.set_run_status(str(payload.get("status") or "Finished"))
            self.inspector.set_status(str(payload.get("status") or "Finished"))
            run_dir = payload.get("run_dir")
            if run_dir:
                self.inspector.load_trace_events_from_run_dir(str(run_dir))
            self._turn_index += 1
        self._running_sessions.pop(session_id, None)
        self.refresh_sessions()
```

- [ ] **Step 6: Update failure callback**

Replace `_on_turn_failed` with:

```python
    def _on_turn_failed(self, session_id: str, error: str) -> None:
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            active_run.status = "Error"
            active_run.error = error
        if session_id == self._active_session_id:
            self.chat.finish_assistant_message()
            self.chat.show_error(error)
            self.chat.set_running(False)
            self.inspector.set_status("Error")
            self.inspector.add_event(InspectorEvent(kind="event", title="Runtime error", detail=error, tone="error"))
        self._running_sessions.pop(session_id, None)
        self.refresh_sessions()
```

- [ ] **Step 7: Update cleanup callback**

Replace `_clear_worker_refs` with:

```python
    def _clear_worker_refs(self, session_id: str) -> None:
        active_run = self._running_sessions.get(session_id)
        if active_run is None:
            return
        active_run.thread = None
        active_run.worker = None
```

This is intentionally harmless after completion because completion already removes the registry entry.

- [ ] **Step 8: Run main window tests**

Run:

```powershell
uv run pytest tests/unit/test_gui_main_window.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add src/gui/main_window.py tests/unit/test_gui_main_window.py
git commit -m "fix(gui): route run callbacks by session"
```

---

### Task 5: Show Per-Session Runtime Status in Sidebar

**Files:**
- Modify: `src/gui/session_model.py`
- Modify: `src/gui/widgets/sessions.py`
- Modify: `src/gui/theme.py`
- Modify: `src/gui/main_window.py`
- Test: `tests/unit/test_gui_session_sidebar.py`

- [ ] **Step 1: Write failing sidebar status test**

Add this test to `GuiSessionSidebarTests`:

```python
    def test_session_row_shows_runtime_status(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_sessions(
            [
                ChatSessionRecord(
                    session_id="chat-one",
                    session_dir="sessions/chat-one",
                    case_id="chat",
                    mode="chat",
                    created_at="1",
                    updated_at="1",
                    title="Chat one",
                    runtime_status="running",
                )
            ]
        )

        row = sidebar.sessions.itemWidget(sidebar.sessions.item(0))
        assert row is not None
        status = row.findChild(QLabel, "SessionRowStatus")

        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.text(), "Running")
        self.assertEqual(status.property("status"), "running")
```

- [ ] **Step 2: Run sidebar test and verify it fails**

Run:

```powershell
uv run pytest tests/unit/test_gui_session_sidebar.py::GuiSessionSidebarTests::test_session_row_shows_runtime_status -q
```

Expected: FAIL because `runtime_status` does not exist.

- [ ] **Step 3: Extend `ChatSessionRecord`**

In `src/gui/session_model.py`, add a default field:

```python
    runtime_status: str = "ready"
```

No existing tests need constructor changes because the field has a default.

- [ ] **Step 4: Add sidebar status label**

In `_SessionRow.__init__` in `src/gui/widgets/sessions.py`, after `metadata` is created, add:

```python
        status = QLabel(_runtime_status_label(record.runtime_status))
        status.setObjectName("SessionRowStatus")
        status.setProperty("status", _runtime_status_key(record.runtime_status))
        status.setAlignment(Qt.AlignCenter)
        status.setFixedWidth(74)
```

Add `status` to the row layout before the delete button:

```python
        layout.addWidget(status)
```

Add helper functions:

```python
def _runtime_status_key(status: str) -> str:
    value = status.lower()
    if "run" in value:
        return "running"
    if "error" in value or "fail" in value:
        return "error"
    if "done" in value or "finish" in value or "pass" in value or "success" in value:
        return "success"
    return "ready"


def _runtime_status_label(status: str) -> str:
    return {
        "running": "Running",
        "error": "Error",
        "success": "Done",
        "ready": "Ready",
    }[_runtime_status_key(status)]
```

- [ ] **Step 5: Add theme styling**

In `src/gui/theme.py`, add a compact status style near the session row styles:

```python
#SessionRowStatus {
    border-radius: 8px;
    padding: 3px 6px;
    font-size: 11px;
    font-weight: 700;
    color: {c.muted};
    background: transparent;
}
#SessionRowStatus[status="running"] {
    color: {state.running};
    background: {c.selection};
}
#SessionRowStatus[status="error"] {
    color: {state.error};
    background: {c.selection};
}
#SessionRowStatus[status="success"] {
    color: {state.success};
    background: {c.selection};
}
```

Use the same string interpolation style already used in `theme_stylesheet()`.

- [ ] **Step 6: Merge live statuses in `refresh_sessions()`**

In `src/gui/main_window.py`, import:

```python
from dataclasses import replace
```

If `replace` conflicts with the existing import from Step 3, combine the import:

```python
from dataclasses import dataclass, field, replace
```

Replace `refresh_sessions()` with:

```python
    def refresh_sessions(self) -> None:
        records = []
        for record in self.store.list_chat_sessions():
            active_run = self._running_sessions.get(record.session_id)
            if active_run is not None:
                records.append(replace(record, runtime_status=active_run.status))
            else:
                records.append(record)
        self.sidebar.set_sessions(records)
        if self._active_session_id is not None:
            self.sidebar.select_session(self._active_session_id)
```

- [ ] **Step 7: Run sidebar and main window tests**

Run:

```powershell
uv run pytest tests/unit/test_gui_session_sidebar.py tests/unit/test_gui_main_window.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add src/gui/session_model.py src/gui/widgets/sessions.py src/gui/theme.py src/gui/main_window.py tests/unit/test_gui_session_sidebar.py
git commit -m "feat(gui): show per-session run status"
```

---

### Task 6: Protect Running Sessions From Destructive Actions

**Files:**
- Modify: `src/gui/main_window.py`
- Test: `tests/unit/test_gui_main_window.py`

- [ ] **Step 1: Write failing destructive-action tests**

Add these tests:

```python
    def test_delete_running_session_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)
            session_id = window.store.create_chat_session().session_id
            window._running_sessions[session_id] = window._new_active_run(
                session_id=session_id,
                user_input="running",
                turn_index=1,
                thread=None,
                worker=None,
            )

            window.delete_session(session_id, confirm=False)

            self.assertIsNotNone(window.manager.load(session_id))
            self.assertIn(session_id, window._running_sessions)

    def test_settings_reload_is_blocked_while_any_session_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)
            session_id = window.store.create_chat_session().session_id
            window._running_sessions[session_id] = window._new_active_run(
                session_id=session_id,
                user_input="running",
                turn_index=1,
                thread=None,
                worker=None,
            )

            self.assertTrue(window._has_running_sessions())
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_delete_running_session_is_blocked tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_settings_reload_is_blocked_while_any_session_runs -q
```

Expected: FAIL because the helper and delete guard do not exist.

- [ ] **Step 3: Add running helper**

Add to `MainWindow`:

```python
    def _has_running_sessions(self) -> bool:
        return bool(self._running_sessions)
```

- [ ] **Step 4: Guard deletion**

At the start of `delete_session()`, before confirmation:

```python
        if session_id in self._running_sessions:
            QMessageBox.warning(self, "Session is running", "This session is still running. Wait for it to finish before deleting it.")
            return
```

- [ ] **Step 5: Guard settings reload**

At the start of `open_settings()`:

```python
        if self._has_running_sessions():
            QMessageBox.warning(self, "Runs in progress", "Wait for running sessions to finish before changing runtime settings.")
            return
```

- [ ] **Step 6: Add close guard**

Add this Qt override to `MainWindow`:

```python
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        if not self._has_running_sessions():
            super().closeEvent(event)
            return
        result = QMessageBox.question(
            self,
            "Runs in progress",
            "There are running chat sessions. Closing now will stop them. Keep Lora open?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if result == QMessageBox.Yes:
            event.ignore()
            return
        super().closeEvent(event)
```

- [ ] **Step 7: Run destructive-action tests**

Run:

```powershell
uv run pytest tests/unit/test_gui_main_window.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add src/gui/main_window.py tests/unit/test_gui_main_window.py
git commit -m "fix(gui): protect active session workers"
```

---

### Task 7: Full Regression and Manual Verification

**Files:**
- No code changes expected unless tests reveal a defect.

- [ ] **Step 1: Run focused GUI tests**

Run:

```powershell
uv run pytest tests/unit/test_gui_worker.py tests/unit/test_gui_main_window.py tests/unit/test_gui_session_sidebar.py tests/unit/test_gui_session_model.py tests/unit/test_gui_chat_widget.py tests/unit/test_gui_inspector.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the full test suite**

Run:

```powershell
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 3: Launch the GUI**

Run:

```powershell
uv run lora-gui
```

Manual checks:

- Create chat A and send a message.
- Before chat A finishes, create or select chat B.
- Confirm chat A remains marked `Running` in the sidebar.
- Send a message in chat B while chat A is still running.
- Switch between chat A and chat B and confirm each session shows its own live transcript.
- Wait for both runs to finish and confirm each saved history reloads correctly.
- Try deleting a running session and confirm the GUI blocks it.
- Try opening settings during a run and confirm the GUI blocks it.

- [ ] **Step 4: Commit any verification fixes**

Only if Step 1, 2, or 3 exposed a defect:

```powershell
git add src/gui tests
git commit -m "fix(gui): polish concurrent session behavior"
```

---

## Self-Review

- Spec coverage: The plan covers concurrent runs across sessions, one active turn per session, hidden-session update caching, replay on selection, completion routing, sidebar status, and destructive-action protection.
- Content scan: No implementation step relies on incomplete markers. Each code-changing task includes concrete snippets and test commands.
- Type consistency: `_LiveUpdate`, `_ActiveRun`, `_running_sessions`, `_new_active_run`, `_on_assistant_delta`, and session-aware worker signal signatures are used consistently across tasks.
