from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolButton

from gui.session_model import ChatSessionRecord
from gui.widgets.sessions import SessionSidebar


class GuiSessionSidebarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_each_session_row_has_own_delete_action_in_menu(self) -> None:
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
                ),
                ChatSessionRecord(
                    session_id="chat-two",
                    session_dir="sessions/chat-two",
                    case_id="chat",
                    mode="chat",
                    created_at="2",
                    updated_at="2",
                    title="Chat two",
                ),
            ]
        )
        emitted: list[str] = []
        sidebar.session_delete_requested.connect(emitted.append)

        row = sidebar.sessions.itemWidget(sidebar.sessions.item(1))
        assert row is not None
        menu_button = row.findChild(QToolButton, "SessionRowMenuButton")
        assert menu_button is not None
        delete_action = next(
            (action for action in menu_button.actions() if action.objectName() == "SessionRowDeleteAction"),
            None,
        )
        assert delete_action is not None
        delete_action.trigger()

        self.assertEqual(emitted, ["chat-two"])

    def test_session_row_controls_have_session_dev_ids(self) -> None:
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
                )
            ]
        )

        row = sidebar.sessions.itemWidget(sidebar.sessions.item(0))
        assert row is not None
        menu_button = row.findChild(QToolButton, "SessionRowMenuButton")
        assert menu_button is not None
        delete_action = next(
            (action for action in menu_button.actions() if action.objectName() == "SessionRowDeleteAction"),
            None,
        )
        assert delete_action is not None

        self.assertEqual(row.property("dev_id"), "chat-one")
        self.assertEqual(menu_button.property("dev_id"), "menu:chat-one")
        self.assertEqual(delete_action.property("dev_id"), "delete:chat-one")

    def test_settings_button_has_stable_object_name(self) -> None:
        sidebar = SessionSidebar()

        self.assertEqual(sidebar.settings_button.objectName(), "SettingsButton")

    def test_session_rows_have_substantial_height(self) -> None:
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
                )
            ]
        )

        self.assertGreaterEqual(sidebar.sessions.item(0).sizeHint().height(), 48)

    def test_session_row_shows_metadata_and_selected_rail(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_sessions(
            [
                ChatSessionRecord(
                    session_id="chat-one",
                    session_dir="sessions/chat-one",
                    case_id="chat",
                    mode="chat",
                    created_at="2026-06-01T00:00:00Z",
                    updated_at="2026-06-02T09:15:00Z",
                    title="Chat one",
                )
            ]
        )

        sidebar.select_session("chat-one")
        row = sidebar.sessions.itemWidget(sidebar.sessions.item(0))
        assert row is not None
        metadata = row.findChild(QLabel, "SessionRowMeta")
        rail = row.findChild(QLabel, "SessionRowRail")

        self.assertIsNotNone(metadata)
        self.assertIsNotNone(rail)
        assert metadata is not None
        assert rail is not None
        self.assertIn("2026-06-02", metadata.text())
        self.assertTrue(row.property("selected"))
        self.assertEqual(rail.property("selected"), True)

    def test_session_row_has_chat_icon_and_overflow_menu(self) -> None:
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
                )
            ]
        )

        row = sidebar.sessions.itemWidget(sidebar.sessions.item(0))
        assert row is not None
        icon_label = row.findChild(QLabel, "SessionRowIcon")
        menu_button = row.findChild(QToolButton, "SessionRowMenuButton")

        self.assertIsNotNone(icon_label)
        self.assertIsNotNone(menu_button)

    def test_primary_sidebar_actions_have_icons_and_tooltips(self) -> None:
        sidebar = SessionSidebar()

        self.assertFalse(sidebar.new_button.icon().isNull())
        self.assertFalse(sidebar.settings_button.icon().isNull())
        self.assertEqual(sidebar.new_button.toolTip(), "Create a new chat session")
        self.assertEqual(sidebar.settings_button.toolTip(), "Open settings")

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

    def test_sidebar_shows_scope_tabs_and_emits_selected_scope(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_scopes(
            [
                {"scope_id": "conversation", "label": "对话", "tooltip": "General chats"},
                {"scope_id": "project:E:/Projects/lora", "label": "lora", "tooltip": "E:/Projects/lora"},
            ],
            active_scope_id="conversation",
        )
        emitted: list[str] = []
        sidebar.scope_selected.connect(emitted.append)

        sidebar.scope_tabs.setCurrentIndex(1)

        self.assertEqual(sidebar.scope_tabs.tabText(0), "对话")
        self.assertEqual(sidebar.scope_tabs.tabText(1), "lora")
        self.assertEqual(sidebar.scope_tabs.tabToolTip(1), "E:/Projects/lora")
        self.assertEqual(emitted, ["project:E:/Projects/lora"])

    def test_project_picker_button_has_stable_name_and_signal(self) -> None:
        sidebar = SessionSidebar()
        emitted: list[bool] = []
        sidebar.project_select_requested.connect(lambda: emitted.append(True))

        sidebar.project_button.click()

        self.assertEqual(sidebar.project_button.objectName(), "ProjectPickerButton")
        self.assertEqual(emitted, [True])


if __name__ == "__main__":
    unittest.main()
