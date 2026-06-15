from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QToolButton

from gui.session_model import ChatSessionRecord, SessionGroupRecord, SessionScope
from gui.widgets.sessions import SessionSidebar


def _scope(scope_id: str, label: str, tooltip: str | None = None) -> SessionScope:
    return SessionScope(
        scope_id=scope_id,
        label=label,
        tooltip=tooltip or label,
        workspace_root=None if scope_id == "conversation" else tooltip or label,
        lora_root=f"/tmp/{label}/.lora",
        runtime_workspace_root=f"/tmp/{label}",
    )


def _record(session_id: str, title: str, updated_at: str = "2", status: str = "ready") -> ChatSessionRecord:
    return ChatSessionRecord(
        session_id=session_id,
        session_dir=f"sessions/{session_id}",
        case_id="chat",
        mode="chat",
        created_at="1",
        updated_at=updated_at,
        title=title,
        runtime_status=status,
    )


def _groups() -> list[SessionGroupRecord]:
    return [
        SessionGroupRecord(scope=_scope("conversation", "对话"), records=[_record("chat-one", "Chat one")]),
        SessionGroupRecord(
            scope=_scope("project:E:/Projects/lora", "lora", "E:/Projects/lora"),
            records=[_record("chat-two", "Chat two")],
        ),
    ]


class GuiSessionSidebarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_each_session_row_has_own_delete_action_in_menu(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_session_groups(
            [SessionGroupRecord(scope=_scope("conversation", "对话"), records=[_record("chat-one", "Chat one"), _record("chat-two", "Chat two")])],
            active_scope_id="conversation",
            active_session_id="chat-one",
        )
        emitted: list[tuple[str, str]] = []
        sidebar.session_delete_requested.connect(lambda scope_id, session_id: emitted.append((scope_id, session_id)))

        section = sidebar._group_sections[0]
        row = section.session_row(1)
        assert row is not None
        menu_button = row.findChild(QToolButton, "SessionRowMenuButton")
        assert menu_button is not None
        delete_action = next(
            (action for action in menu_button.actions() if action.objectName() == "SessionRowDeleteAction"),
            None,
        )
        assert delete_action is not None
        delete_action.trigger()

        self.assertEqual(emitted, [("conversation", "chat-two")])

    def test_session_row_controls_have_session_dev_ids(self) -> None:
        sidebar = SessionSidebar()
        record = _record("chat-one", "Chat one", updated_at="1")
        sidebar.set_session_groups(
            [SessionGroupRecord(scope=_scope("conversation", "对话"), records=[record])],
            active_scope_id="conversation",
            active_session_id=record.session_id,
        )

        section = sidebar._group_sections[0]
        row = section.session_row(0)
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
        sidebar.set_session_groups(
            [SessionGroupRecord(scope=_scope("conversation", "对话"), records=[_record("chat-one", "Chat one", updated_at="1")])],
            active_scope_id="conversation",
            active_session_id="chat-one",
        )

        section = sidebar._group_sections[0]
        row = section.session_row(0)
        assert row is not None
        self.assertGreaterEqual(row.height(), 48)

    def test_session_row_shows_metadata_and_selected_rail(self) -> None:
        sidebar = SessionSidebar()
        record = ChatSessionRecord(
            session_id="chat-one",
            session_dir="sessions/chat-one",
            case_id="chat",
            mode="chat",
            created_at="2026-06-01T00:00:00Z",
            updated_at="2026-06-02T09:15:00Z",
            title="Chat one",
        )
        sidebar.set_session_groups(
            [SessionGroupRecord(scope=_scope("conversation", "对话"), records=[record])],
            active_scope_id="conversation",
            active_session_id=record.session_id,
        )

        sidebar.select_session("conversation", "chat-one")
        section = sidebar._group_sections[0]
        row = section.session_row(0)
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
        sidebar.set_session_groups(
            [SessionGroupRecord(scope=_scope("conversation", "对话"), records=[_record("chat-one", "Chat one", updated_at="1")])],
            active_scope_id="conversation",
            active_session_id="chat-one",
        )

        section = sidebar._group_sections[0]
        row = section.session_row(0)
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
        sidebar.set_session_groups(
            [
                SessionGroupRecord(
                    scope=_scope("conversation", "对话"),
                    records=[_record("chat-one", "Chat one", updated_at="1", status="running")],
                )
            ],
            active_scope_id="conversation",
            active_session_id="chat-one",
        )

        section = sidebar._group_sections[0]
        row = section.session_row(0)
        assert row is not None
        status = row.findChild(QLabel, "SessionRowStatus")

        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.text(), "Running")
        self.assertEqual(status.property("status"), "running")

    def test_sidebar_renders_group_headers_and_session_children(self) -> None:
        sidebar = SessionSidebar()

        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id="chat-one")

        self.assertEqual(len(sidebar._group_sections), 2)
        first_header = sidebar._group_sections[0].header
        second_header = sidebar._group_sections[1].header
        self.assertIsNotNone(first_header.findChild(QLabel, "SessionGroupTitle"))
        self.assertEqual(first_header.findChild(QLabel, "SessionGroupTitle").text(), "对话")
        self.assertEqual(second_header.findChild(QLabel, "SessionGroupTitle").text(), "lora")
        self.assertEqual(second_header.toolTip(), "E:/Projects/lora")
        self.assertEqual(sidebar._group_sections[0].session_count(), 1)
        self.assertEqual(sidebar._group_sections[1].session_count(), 1)

    def test_collapsed_group_starts_collapsed(self) -> None:
        sidebar = SessionSidebar()
        group = SessionGroupRecord(scope=_scope("conversation", "对话"), records=[_record("chat-one", "Chat one")], collapsed=True)

        sidebar.set_session_groups([group], active_scope_id="conversation", active_session_id=None)

        self.assertTrue(sidebar._group_sections[0]._collapsed)
        self.assertFalse(sidebar._group_sections[0].sessions_body.isVisible())

    def test_clicking_session_emits_scope_and_session_id(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id=None)
        emitted: list[tuple[str, str]] = []
        sidebar.session_selected.connect(lambda scope_id, session_id: emitted.append((scope_id, session_id)))

        sidebar._group_sections[1]._on_row_selected("chat-two")

        self.assertEqual(emitted, [("project:E:/Projects/lora", "chat-two")])

    def test_group_order_changed_emits_top_level_scope_order(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id=None)
        emitted: list[list[str]] = []
        sidebar.group_order_changed.connect(emitted.append)

        sidebar._reorder_groups("project:E:/Projects/lora", "conversation")

        self.assertEqual(emitted, [["project:E:/Projects/lora", "conversation"]])

    def test_group_order_reads_only_top_level_groups(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id=None)

        self.assertEqual(sidebar.group_order(), ["conversation", "project:E:/Projects/lora"])

    def test_project_group_header_shows_delete_button(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id=None)

        project_header = sidebar._group_sections[1].header
        conversation_header = sidebar._group_sections[0].header

        project_delete = project_header.findChild(QToolButton, "SessionGroupDeleteButton")
        conversation_delete = conversation_header.findChild(QToolButton, "SessionGroupDeleteButton")

        self.assertIsNotNone(project_delete)
        assert project_delete is not None
        self.assertFalse(project_delete.icon().isNull())
        self.assertIsNone(conversation_delete)

    def test_project_group_header_shows_directory_icon_only_for_directory_scopes(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id=None)

        conversation_header = sidebar._group_sections[0].header
        project_header = sidebar._group_sections[1].header

        conversation_icon = conversation_header.findChild(QLabel, "SessionGroupDirIcon")
        project_icon = project_header.findChild(QLabel, "SessionGroupDirIcon")

        self.assertIsNone(conversation_icon)
        self.assertIsNotNone(project_icon)
        assert project_icon is not None
        pixmap = project_icon.pixmap()
        self.assertIsNotNone(pixmap)
        assert pixmap is not None
        self.assertFalse(pixmap.isNull())

    def test_project_delete_button_emits_scope_id(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id=None)
        emitted: list[str] = []
        sidebar.project_remove_requested.connect(emitted.append)

        delete_button = sidebar._group_sections[1].header.findChild(QToolButton, "SessionGroupDeleteButton")
        assert delete_button is not None
        delete_button.click()

        self.assertEqual(emitted, ["project:E:/Projects/lora"])

    def test_session_list_stays_within_viewport_width(self) -> None:
        sidebar = SessionSidebar()
        sidebar.resize(360, 720)
        sidebar.show()
        self.app.processEvents()
        sidebar.set_session_groups(
            [
                SessionGroupRecord(
                    scope=_scope("conversation", "对话"),
                    records=[_record("chat-one", "A very long session title that should elide instead of overflowing")],
                ),
                SessionGroupRecord(
                    scope=_scope("project:E:/Projects/lora", "lora", "E:/Projects/lora"),
                    records=[_record("chat-two", "Another long title for overflow checks")],
                ),
            ],
            active_scope_id="conversation",
            active_session_id="chat-one",
        )
        self.app.processEvents()

        viewport_width = sidebar.session_tree.viewport().width()
        container_width = sidebar._groups_container.width()
        self.assertGreater(viewport_width, 0)
        self.assertLessEqual(container_width, viewport_width)
        self.assertEqual(sidebar.session_tree.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        self.assertEqual(sidebar.session_tree.horizontalScrollBar().maximum(), 0)
        for section in sidebar._group_sections:
            self.assertLessEqual(section.width(), container_width)
            row = section.session_row(0)
            assert row is not None
            self.assertLessEqual(row.width(), section.width())

    def test_project_picker_button_has_stable_name_and_signal(self) -> None:
        sidebar = SessionSidebar()
        emitted: list[bool] = []
        sidebar.project_select_requested.connect(lambda: emitted.append(True))

        sidebar.project_button.click()

        self.assertEqual(sidebar.project_button.objectName(), "ProjectPickerButton")
        self.assertEqual(emitted, [True])


if __name__ == "__main__":
    unittest.main()
