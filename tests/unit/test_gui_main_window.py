from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow
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


if __name__ == "__main__":
    unittest.main()
