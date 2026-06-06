from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow
from gui.workers import InspectorEvent
from lora.trace import EventStore


class GuiMainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_delete_current_session_clears_chat_and_active_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)
            window.create_chat()
            session_id = window._active_session_id
            self.assertIsNotNone(session_id)
            window.chat.add_message("user", "old message")

            window.delete_session(str(session_id), confirm=False)

            self.assertIsNone(window._active_session_id)
            self.assertEqual(window.chat.header.text(), "Select or create a chat session")
            self.assertEqual(window.chat.findChildren(type(window.chat.header), "UserBubble"), [])

    def test_select_session_loads_last_run_tool_and_file_trace_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)
            window.create_chat()
            session_id = window._active_session_id
            assert session_id is not None
            run_ref = window.manager.start_case_run(session_id, "chat", run_config=window.config)
            store = EventStore(run_ref)
            store.append("tool.call", actor="assistant", payload={"tool_name": "read", "args": {"path": "README.md"}})
            store.append(
                "file.read",
                actor="tool",
                payload={"path": f"{tmp}/README.md", "tool_call_id": "call_1", "tool_name": "read"},
            )
            window.manager.finish_case_run(run_ref, "passed")

            window.select_session(session_id)

            self.assertGreater(window.inspector.tools.topLevelItemCount(), 0)
            self.assertGreater(window.inspector.files.topLevelItemCount(), 0)

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
            window._on_runtime_event(
                session_id,
                InspectorEvent(
                    kind="tool",
                    title="Tool call: read",
                    detail='{"description": "Read files"}',
                    tone="accent",
                    metadata={"tool_call_id": "call_read"},
                ),
            )

            window.select_session(session_id)

            bubble_texts = [
                label.text()
                for label in window.chat.findChildren(type(window.chat.header))
                if label.objectName() in {"UserBubble", "AssistantBubble"}
            ]
            status_texts = [
                label.text()
                for label in window.chat.findChildren(type(window.chat.header))
                if label.objectName() == "ToolStatusTitle"
            ]
            self.assertEqual(bubble_texts, ["live prompt", "live answer"])
            self.assertEqual(status_texts, ["Read files"])
            self.assertFalse(window.chat.input.isEnabled())

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

    def test_delete_running_session_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)
            window._show_warning = lambda *_args: None
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
            warnings: list[tuple[str, str]] = []
            window._show_warning = lambda title, message: warnings.append((title, message))
            session_id = window.store.create_chat_session().session_id
            window._running_sessions[session_id] = window._new_active_run(
                session_id=session_id,
                user_input="running",
                turn_index=1,
                thread=None,
                worker=None,
            )

            window.open_settings()

            self.assertTrue(window._has_running_sessions())
            self.assertEqual(warnings[0][0], "Runs in progress")

    def test_without_project_path_starts_in_conversation_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"

            window = MainWindow(workspace_root=None, state_path=state_path)

            self.assertEqual(window._active_scope_id, "conversation")
            self.assertEqual(window.sidebar.scope_tabs.tabText(0), "对话")
            self.assertIsNone(window.current_scope.workspace_root)

    def test_set_project_path_remembers_default_and_switches_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            project.mkdir()
            window = MainWindow(workspace_root=None, state_path=state_path)

            window.set_project_path(str(project))

            restored = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(restored["default_project_path"], str(project.resolve()))
            self.assertEqual(window._active_scope_id, f"project:{project.resolve()}")
            self.assertEqual(window.config.workspace_root, str(project.resolve()))
            self.assertEqual(window.sidebar.scope_tabs.tabText(1), "repo")

    def test_main_layout_uses_tighter_three_pane_spacing_and_margins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)

            layout = window.centralWidget().layout()
            margins = layout.contentsMargins()

            self.assertEqual(layout.spacing(), 14)
            self.assertEqual((margins.left(), margins.top(), margins.right(), margins.bottom()), (16, 16, 16, 16))


if __name__ == "__main__":
    unittest.main()
