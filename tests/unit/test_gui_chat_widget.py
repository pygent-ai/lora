from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QSizePolicy, QWidget

from gui.workers import InspectorEvent
from gui.widgets.chat import ChatPane


class GuiChatWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_render_history_removes_existing_message_rows(self) -> None:
        pane = ChatPane()
        pane.render_history(
            [
                {"role": "user", "content": "old user"},
                {"role": "assistant", "content": "old assistant"},
            ]
        )

        pane.render_history([{"role": "user", "content": "new user"}])

        bubble_texts = [
            label.text()
            for label in pane.findChildren(QLabel)
            if label.objectName() in {"UserBubble", "AssistantBubble"}
        ]
        self.assertEqual(bubble_texts, ["new user"])

    def test_render_history_includes_tool_messages_as_status_rows(self) -> None:
        pane = ChatPane()

        pane.render_history(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "payload": {"tool_calls": [{"function": {"name": "read", "arguments": '{"path": "README.md"}'}}]},
                },
                {"role": "tool", "content": '{"status": "success", "result": "done"}', "payload": {"tool_call_id": "call_1"}},
            ]
        )

        status_texts = [label.text() for label in pane.findChildren(QLabel) if label.objectName() == "ToolStatusTitle"]
        self.assertEqual(status_texts, ["read"])

    def test_render_history_reads_persisted_top_level_tool_calls(self) -> None:
        pane = ChatPane()

        pane.render_history(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_read",
                            "function": {"name": "read", "arguments": '{"description": "Read persisted file"}'},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "content": '{"status": "success", "result": "done"}',
                    "tool_call_id": "call_read",
                },
            ]
        )

        status_texts = [label.text() for label in pane.findChildren(QLabel, "ToolStatusTitle")]
        self.assertEqual(status_texts, ["Read persisted file"])
        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        toggle.click()
        self.assertEqual([label.property("status") for label in pane.findChildren(QLabel, "ToolCallStatus")], ["success"])

    def test_history_and_runtime_tool_transcripts_render_same_order(self) -> None:
        history_pane = ChatPane()
        history_pane.render_history(
            [
                {"role": "assistant", "content": "thinking"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_read",
                            "function": {"name": "read", "arguments": '{"description": "Read files"}'},
                        }
                    ],
                },
                {"role": "tool", "content": '{"status": "success", "result": "ok"}', "tool_call_id": "call_read"},
                {"role": "assistant", "content": "first reply"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_test",
                            "function": {"name": "bash", "arguments": '{"description": "Run tests"}'},
                        }
                    ],
                },
                {"role": "assistant", "content": "second reply"},
            ]
        )

        runtime_pane = ChatPane()
        runtime_pane.start_assistant_message()
        runtime_pane.append_assistant_delta("thinking")
        runtime_pane.add_runtime_event(
            InspectorEvent(kind="tool", title="Tool call: read", detail='{"description": "Read files"}', tone="accent")
        )
        runtime_pane.add_runtime_event(InspectorEvent(kind="tool", title="Tool result: call_read success", detail="ok", tone="ok"))
        runtime_pane.append_assistant_delta("first reply")
        runtime_pane.add_runtime_event(
            InspectorEvent(kind="tool", title="Tool call: bash", detail='{"description": "Run tests"}', tone="accent")
        )
        runtime_pane.append_assistant_delta("second reply")

        expected = ["thinking", "Read files", "first reply", "Run tests", "second reply"]
        self.assertEqual(_visible_chat_flow(history_pane), expected)
        self.assertEqual(_visible_chat_flow(runtime_pane), expected)

    def test_runtime_multiple_tool_results_match_calls_by_id(self) -> None:
        pane = ChatPane()

        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: read",
                detail='{"description": "Read files"}',
                tone="accent",
                metadata={"tool_call_id": "call_read"},
            )
        )
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run tests"}',
                tone="accent",
                metadata={"tool_call_id": "call_test"},
            )
        )
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool result: call_read success",
                detail="ok",
                tone="ok",
                metadata={"tool_call_id": "call_read"},
            )
        )

        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        toggle.click()

        lines = [label.text() for label in pane.findChildren(QLabel, "ToolCallLine")]
        self.assertEqual(lines, ["Read files", "Run tests"])
        self.assertEqual([label.property("status") for label in pane.findChildren(QLabel, "ToolCallStatus")], ["success", "running"])

    def test_add_runtime_event_inserts_status_row(self) -> None:
        pane = ChatPane()

        pane.add_runtime_event(
            InspectorEvent(kind="tool", title="Tool call: bash", detail='{"description": "Run tests", "command": "pytest"}', tone="accent")
        )

        status_texts = [label.text() for label in pane.findChildren(QLabel) if label.objectName() == "ToolStatusTitle"]
        self.assertEqual(status_texts, ["Run tests"])
        self.assertEqual(pane.findChildren(QLabel, "ToolStatusDetail"), [])

    def test_tool_result_without_known_call_does_not_display_internal_id(self) -> None:
        pane = ChatPane()
        pane.add_runtime_event(
            InspectorEvent(kind="tool", title="Tool result: evt_034e2cf984014b0f9f89073a266c9554 success", detail="done", tone="ok")
        )

        self.assertEqual(pane.findChildren(QLabel, "ToolStatusTitle"), [])
        self.assertEqual(pane.findChildren(QLabel, "ToolCallLine"), [])

    def test_tool_status_line_is_created_only_after_expanding(self) -> None:
        pane = ChatPane()
        pane.add_runtime_event(
            InspectorEvent(kind="tool", title="Tool call: bash", detail='{"description": "Run diagnostics", "command": "pytest"}', tone="accent")
        )
        pane.add_runtime_event(InspectorEvent(kind="tool", title="Tool result: call_1 success", detail="done", tone="ok"))

        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        self.assertEqual(pane.findChildren(QLabel, "ToolCallLine"), [])

        toggle.click()

        detail_texts = [label.text() for label in pane.findChildren(QLabel) if label.objectName() == "ToolCallLine"]
        self.assertEqual(detail_texts, ["Run diagnostics"])

    def test_consecutive_tool_calls_collapse_into_one_expandable_summary(self) -> None:
        pane = ChatPane()
        long_result = "x" * 220

        pane.render_history(
            [
                {"role": "assistant", "content": "I will inspect this."},
                {
                    "role": "assistant",
                    "content": "",
                    "payload": {
                        "tool_calls": [
                            {
                                "id": "call_read",
                                "function": {
                                    "name": "read",
                                    "arguments": '{"description": "Read project files", "path": "README.md"}',
                                },
                            },
                            {
                                "id": "call_test",
                                "function": {
                                    "name": "bash",
                                    "arguments": '{"description": "Run unit tests", "command": "pytest"}',
                                },
                            },
                            {
                                "id": "call_fail",
                                "function": {
                                    "name": "bash",
                                    "arguments": '{"description": "Check failing command", "command": "exit 1"}',
                                },
                            },
                        ]
                    },
                },
                {
                    "role": "tool",
                    "content": '{"status": "success", "result": "readme content"}',
                    "payload": {"tool_call_id": "call_read"},
                },
                {
                    "role": "tool",
                    "content": '{"status": "success", "result": "%s"}' % long_result,
                    "payload": {"tool_call_id": "call_test"},
                },
                {
                    "role": "tool",
                    "content": '{"status": "error", "error": "bad command"}',
                    "payload": {"tool_call_id": "call_fail"},
                },
            ]
        )

        summaries = [label.text() for label in pane.findChildren(QLabel, "ToolStatusTitle")]
        self.assertEqual(summaries, ["Check failing command"])
        self.assertEqual(pane.findChildren(QLabel, "ToolStatusDetail"), [])

        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        toggle.click()

        lines = [label.text() for label in pane.findChildren(QLabel, "ToolCallLine")]
        self.assertEqual(lines, ["Read project files", "Run unit tests", "Check failing command"])
        self.assertFalse(any("..." in line for line in lines))
        self.assertFalse(any("x" in line for line in lines))
        self.assertEqual(
            [label.property("status") for label in pane.findChildren(QLabel, "ToolCallStatus")],
            ["success", "success", "error"],
        )

    def test_tool_call_without_result_is_marked_running_when_expanded(self) -> None:
        pane = ChatPane()

        pane.render_history(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "payload": {
                        "tool_calls": [
                            {
                                "id": "call_wait",
                                "function": {
                                    "name": "bash",
                                    "arguments": '{"description": "Install dependencies", "command": "uv sync"}',
                                },
                            }
                        ]
                    },
                }
            ]
        )

        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        toggle.click()

        self.assertEqual([label.property("status") for label in pane.findChildren(QLabel, "ToolCallStatus")], ["running"])
        self.assertEqual([label.text() for label in pane.findChildren(QLabel, "ToolCallLine")], ["Install dependencies"])

    def test_streaming_assistant_reply_stays_after_tool_calls_in_arrival_order(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run diagnostics", "command": "pytest"}',
                tone="accent",
            )
        )
        pane.append_assistant_delta("done")

        visible_flow = [
            label.text()
            for index in range(pane.messages.count())
            if (widget := pane.messages.itemAt(index).widget()) is not None
            for label in widget.findChildren(QLabel)
            if label.objectName() in {"ThinkingStatusTitle", "ToolStatusTitle", "AssistantBubble"}
        ]
        self.assertEqual(visible_flow, ["Thinking", "Run diagnostics", "done"])

        ordered_labels = [
            label.text()
            for index in range(pane.messages.count())
            if (widget := pane.messages.itemAt(index).widget()) is not None
            for label in widget.findChildren(QLabel)
            if label.objectName() in {"ToolStatusTitle", "AssistantBubble"}
        ]
        self.assertEqual(ordered_labels, ["Run diagnostics", "done"])

    def test_streaming_assistant_reply_without_tools_replaces_thinking_status(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.append_assistant_delta("hello")

        ordered_labels = [
            label.text()
            for index in range(pane.messages.count())
            if (widget := pane.messages.itemAt(index).widget()) is not None
            for label in widget.findChildren(QLabel)
            if label.objectName() in {"ToolStatusTitle", "AssistantBubble"}
        ]
        self.assertEqual(ordered_labels, ["hello"])

    def test_tool_call_after_streaming_reply_starts_a_new_group_below_reply(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: read",
                detail='{"description": "Read files", "path": "README.md"}',
                tone="accent",
            )
        )
        pane.append_assistant_delta("first reply")
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run tests", "command": "pytest"}',
                tone="accent",
            )
        )

        visible_flow = [
            label.text()
            for index in range(pane.messages.count())
            if (widget := pane.messages.itemAt(index).widget()) is not None
            for label in widget.findChildren(QLabel)
            if label.objectName() in {"ThinkingStatusTitle", "ToolStatusTitle", "AssistantBubble"}
        ]
        self.assertEqual(visible_flow, ["Thinking", "Read files", "first reply", "Run tests"])

    def test_user_and_assistant_messages_keep_opposite_sides_and_avatars(self) -> None:
        pane = ChatPane()

        pane.add_message("user", "hello")
        pane.add_message("assistant", "hi")

        self.assertEqual([label.text() for label in pane.findChildren(QLabel, "UserAvatar")], ["U"])
        self.assertEqual([label.text() for label in pane.findChildren(QLabel, "AssistantAvatar")], ["L"])
        self.assertEqual(len(pane.findChildren(QLabel, "UserBubble")), 1)
        self.assertEqual(len(pane.findChildren(QLabel, "AssistantBubble")), 1)

    def test_history_message_bubbles_fit_text_up_to_three_quarters_of_chat_width(self) -> None:
        pane = ChatPane()
        pane.resize(1000, 700)
        pane.show()
        self.app.processEvents()

        pane.render_history(
            [
                {"role": "user", "content": "historical user input should remain visible"},
                {"role": "assistant", "content": "a long assistant response " * 40},
            ]
        )
        self.app.processEvents()

        max_width = int((pane.scroll_area.viewport().width() - 64) * 0.75)
        user_bubble = pane.findChild(QLabel, "UserBubble")
        assistant_bubble = pane.findChild(QLabel, "AssistantBubble")
        self.assertIsNotNone(user_bubble)
        self.assertIsNotNone(assistant_bubble)
        assert user_bubble is not None
        assert assistant_bubble is not None
        user_text_width = QFontMetrics(user_bubble.font()).horizontalAdvance(user_bubble.text())

        self.assertLess(user_bubble.width(), max_width)
        self.assertGreaterEqual(user_bubble.width(), user_text_width)
        self.assertLessEqual(user_bubble.maximumWidth(), max_width)
        self.assertLessEqual(assistant_bubble.width(), max_width)
        self.assertEqual(assistant_bubble.maximumWidth(), max_width)

    def test_chat_rows_shrink_without_horizontal_scrolling(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "a long assistant response " * 40)
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run a very long diagnostic command description that should wrap instead of widening the chat"}',
                tone="accent",
            )
        )

        self.assertEqual(pane.scroll_area.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        assistant_bubbles = pane.findChildren(QLabel, "AssistantBubble")
        self.assertTrue(assistant_bubbles)
        self.assertTrue(all(bubble.minimumWidth() == bubble.maximumWidth() for bubble in assistant_bubbles))
        self.assertTrue(all(bubble.sizePolicy().horizontalPolicy() == QSizePolicy.Fixed for bubble in assistant_bubbles))
        tool_titles = pane.findChildren(QLabel, "ToolStatusTitle")
        self.assertTrue(tool_titles)
        self.assertTrue(all(title.wordWrap() for title in tool_titles))

    def test_narrow_chat_view_keeps_content_inside_viewport(self) -> None:
        pane = ChatPane()
        pane.show()
        self.app.processEvents()
        pane.scroll_area.resize(180, 400)
        pane.scroll_area.viewport().resize(180, 400)

        pane.render_history(
            [
                {"role": "user", "content": "small"},
                {"role": "assistant", "content": "a long assistant response " * 40},
            ]
        )
        self.app.processEvents()

        self.assertEqual(pane.scroll_area.horizontalScrollBar().maximum(), 0)
        viewport_width = pane.scroll_area.viewport().width()
        for label in pane.findChildren(QLabel):
            if label.objectName() in {"UserBubble", "AssistantBubble", "UserAvatar", "AssistantAvatar"}:
                left = label.mapTo(pane.scroll_area.viewport(), label.rect().topLeft()).x()
                right = left + label.width()
                self.assertGreaterEqual(left, 0)
                self.assertLessEqual(right, viewport_width)

    def test_running_state_keeps_send_button_width_and_updates_status_chip(self) -> None:
        pane = ChatPane()
        idle_width = pane.send_button.width()

        pane.set_running(True)
        running_width = pane.send_button.width()
        pane.set_running(False)

        status = pane.findChild(QLabel, "ChatRunStatus")
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(idle_width, running_width)
        self.assertEqual(pane.send_button.width(), idle_width)
        self.assertEqual(status.property("status"), "ready")

    def test_tool_group_renders_as_execution_card_with_status_property(self) -> None:
        pane = ChatPane()

        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run diagnostics", "command": "pytest"}',
                tone="accent",
                metadata={"tool_call_id": "call_test"},
            )
        )

        card = pane.findChild(QWidget, "ToolStatusRow")
        title = pane.findChild(QLabel, "ToolStatusTitle")
        self.assertIsNotNone(card)
        self.assertIsNotNone(title)
        assert card is not None
        assert title is not None
        self.assertEqual(card.property("status"), "running")
        self.assertIn("Run diagnostics", title.text())

        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool result: call_test success",
                detail="ok",
                tone="ok",
                metadata={"tool_call_id": "call_test"},
            )
        )

        self.assertEqual(card.property("status"), "success")


if __name__ == "__main__":
    unittest.main()


def _visible_chat_flow(pane: ChatPane) -> list[str]:
    return [
        label.text()
        for index in range(pane.messages.count())
        if (widget := pane.messages.itemAt(index).widget()) is not None
        for label in widget.findChildren(QLabel)
        if label.objectName() in {"ThinkingStatusTitle", "ToolStatusTitle", "AssistantBubble"}
    ]
