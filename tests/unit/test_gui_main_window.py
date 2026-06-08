from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QWidget

from gui.main_window import MainWindow
from gui.session_model import ChatSessionStore
from gui.workers import InspectorEvent
from lora.session import SessionManager
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

            self.assertEqual(_visible_chat_flow(window.chat), [])
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
                label.text() for label in window.chat.findChildren(QLabel) if label.objectName() == "UserBubble"
            ]
            status_texts = [
                label.text()
                for label in window.chat.findChildren(QLabel)
                if label.objectName() == "ToolStatusTitle"
            ]
            self.assertEqual(bubble_texts, ["live prompt"])
            self.assertEqual(_visible_chat_flow(window.chat), ["live prompt", "Activity"])
            self.assertEqual(_activity_detail_flow(window.chat), ["live answer", "Read files"])
            self.assertEqual(status_texts, ["Activity", "Read files"])
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
            self.assertEqual(_visible_chat_flow(window.chat), [])
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
            first_header = window.sidebar._group_sections[0].header
            self.assertEqual(first_header.findChild(QLabel, "SessionGroupTitle").text(), "对话")
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
            labels = []
            for section in window.sidebar._group_sections:
                labels.append(section.header.findChild(QLabel, "SessionGroupTitle").text())
            self.assertIn("repo", labels)

    def test_main_layout_uses_tighter_three_pane_spacing_and_margins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)

            layout = window.centralWidget().layout()
            margins = layout.contentsMargins()

            self.assertEqual(layout.spacing(), 14)
            self.assertEqual((margins.left(), margins.top(), margins.right(), margins.bottom()), (16, 16, 16, 16))

    def test_debug_message_box_exposes_named_buttons_for_hit_testing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)

            dialog = window._build_debug_message_box(
                "warning",
                "Runs in progress",
                "Wait for running sessions to finish before changing projects.",
                buttons=("ok",),
                default="ok",
            )

            self.assertEqual(dialog.objectName(), "WarningDialog")
            self.assertEqual(dialog.text(), "Wait for running sessions to finish before changing projects.")
            self.assertEqual(dialog.informativeText(), "")
            self.assertEqual(dialog.button(QMessageBox.Ok).objectName(), "WarningDialogOkButton")

    def test_question_message_box_uses_named_yes_no_buttons_for_hit_testing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)

            dialog = window._build_debug_message_box(
                "question",
                "Delete session",
                "Delete session chat-123?\n\nThis removes the saved chat and local run artifacts.",
                buttons=("yes", "no"),
                default="no",
            )

            self.assertEqual(dialog.objectName(), "QuestionDialog")
            self.assertEqual(dialog.button(QMessageBox.Yes).objectName(), "QuestionDialogYesButton")
            self.assertEqual(dialog.button(QMessageBox.No).objectName(), "QuestionDialogNoButton")

    def test_sidebar_initial_render_includes_conversation_and_project_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            project.mkdir()
            state = json.loads("{}")
            state["default_project_path"] = str(project.resolve())
            state["recent_project_paths"] = [str(project.resolve())]
            state_path.write_text(json.dumps(state), encoding="utf-8")

            window = MainWindow(workspace_root=None, state_path=state_path)

            labels: list[str] = []
            for section in window.sidebar._group_sections:
                labels.append(section.header.findChild(QLabel, "SessionGroupTitle").text())
            self.assertEqual(labels, ["repo", "对话"])

    def test_select_project_session_switches_active_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            project.mkdir()
            window = MainWindow(workspace_root=None, state_path=state_path)
            window.set_project_path(str(project))
            project_session = window.store.create_chat_session().session_id
            window.select_scope("conversation")

            window.select_session(f"project:{project.resolve()}", project_session)

            self.assertEqual(window._active_scope_id, f"project:{project.resolve()}")
            self.assertEqual(window._active_session_id, project_session)
            self.assertEqual(window.config.workspace_root, str(project.resolve()))

    def test_select_session_in_other_scope_allowed_while_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            project.mkdir()
            window = MainWindow(workspace_root=None, state_path=state_path)
            window.set_project_path(str(project))
            conversation_scope = next(scope for scope in window.scopes if scope.scope_id == "conversation")
            conversation_store = ChatSessionStore(SessionManager(window._config_for_scope(conversation_scope)))
            conversation_session = conversation_store.create_chat_session().session_id
            window.select_scope("conversation")
            window._running_sessions[conversation_session] = window._new_active_run(
                session_id=conversation_session,
                user_input="running",
                turn_index=1,
                thread=None,
                worker=None,
            )
            project_scope = next(scope for scope in window.scopes if scope.scope_id == f"project:{project.resolve()}")
            project_store = ChatSessionStore(SessionManager(window._config_for_scope(project_scope)))
            project_session = project_store.create_chat_session().session_id

            window.select_session(f"project:{project.resolve()}", project_session)

            self.assertEqual(window._active_scope_id, f"project:{project.resolve()}")
            self.assertEqual(window._active_session_id, project_session)
            self.assertIn(conversation_session, window._running_sessions)

    def test_group_order_and_collapse_state_are_saved_from_sidebar_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            project.mkdir()
            window = MainWindow(workspace_root=None, state_path=state_path)
            window.set_project_path(str(project))

            window._remember_group_order([f"project:{project.resolve()}", "conversation"])
            window._remember_group_collapsed("conversation", True)
            window._project_state_save_timer.stop()
            window.project_state.save()
            restored = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(restored["scope_order"], [f"project:{project.resolve()}", "conversation"])
            self.assertEqual(restored["collapsed_scope_ids"], ["conversation"])

    def test_remove_project_clears_it_from_sidebar_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            project.mkdir()
            window = MainWindow(workspace_root=None, state_path=state_path)
            window.set_project_path(str(project))
            window.remove_project(f"project:{project.resolve()}", confirm=False)

            restored = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(restored["recent_project_paths"], [])
            self.assertIsNone(restored["default_project_path"])
            labels = []
            for section in window.sidebar._group_sections:
                labels.append(section.header.findChild(QLabel, "SessionGroupTitle").text())
            self.assertEqual(labels, ["对话"])
            self.assertEqual(window._active_scope_id, "conversation")


def _visible_chat_flow(widget: QWidget) -> list[str]:
    texts: list[str] = []
    for index in range(widget.messages.count()):
        item = widget.messages.itemAt(index)
        row = item.widget() if item is not None else None
        if row is None:
            continue
        if row.objectName() == "ActivityGroup":
            texts.append("Activity")
            continue
        if (thinking := row.findChild(QLabel, "ThinkingStatusTitle")) is not None:
            texts.append(thinking.text())
            continue
        if (user := row.findChild(QLabel, "UserBubble")) is not None:
            texts.append(user.text())
            continue
        if (tool := row.findChild(QLabel, "ToolStatusTitle")) is not None:
            texts.append(tool.text())
            continue
        body = row.findChild(QWidget, "DocumentBlockList")
        if body is None or body.layout() is None:
            continue
        block_texts: list[str] = []
        for block_index in range(body.layout().count()):
            block_item = body.layout().itemAt(block_index)
            block = block_item.widget() if block_item is not None else None
            if block is None:
                continue
            if isinstance(block, QLabel) and block.objectName() == "ChatHeadingBlock":
                plain_text = block.property("plain_text")
                block_texts.append(str(plain_text) if plain_text else block.text())
            elif block.objectName() == "ChatParagraphBlock":
                plain_text = block.property("plain_text")
                if plain_text:
                    block_texts.append(str(plain_text))
            elif block.objectName() == "ChatCodeBlock":
                code = block.findChild(QLabel, "ChatCodeBlockText")
                if code is not None:
                    block_texts.append(code.text())
        if block_texts:
            texts.append("\n\n".join(block_texts))
    return texts


def _activity_detail_flow(widget: QWidget) -> list[str]:
    values: list[str] = []
    for activity in widget.findChildren(QWidget, "ActivityGroup"):
        detail = activity.findChild(QWidget, "ActivityGroupDetail")
        layout = detail.layout() if detail is not None else None
        if layout is None:
            continue
        for index in range(layout.count()):
            item = layout.itemAt(index)
            row = item.widget() if item is not None else None
            if row is None:
                continue
            if (thinking := row.findChild(QLabel, "ThinkingStatusTitle")) is not None:
                values.append(thinking.text())
                continue
            if (tool := row.findChild(QLabel, "ToolStatusTitle")) is not None:
                values.append(tool.text())
                continue
            body = row.findChild(QWidget, "DocumentBlockList")
            if body is None or body.layout() is None:
                continue
            block_texts: list[str] = []
            for block_index in range(body.layout().count()):
                block_item = body.layout().itemAt(block_index)
                block = block_item.widget() if block_item is not None else None
                if block is not None and block.objectName() == "ChatParagraphBlock":
                    plain_text = block.property("plain_text")
                    if plain_text:
                        block_texts.append(str(plain_text))
            if block_texts:
                values.append("\n\n".join(block_texts))
    return values


if __name__ == "__main__":
    unittest.main()
