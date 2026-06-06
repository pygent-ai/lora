from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QSizePolicy, QWidget

from gui.theme import theme_stylesheet
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
        self.assertRegex(pane.findChildren(QLabel, "ToolCallStartTime")[0].text(), r"^\d{2}:\d{2}:\d{2}$")
        self.assertEqual([label.text() for label in pane.findChildren(QLabel, "ToolCallDuration")], ["--"])

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
        self.assertEqual(len(pane.findChildren(QWidget, "ToolTimelineDot")), 2)
        self.assertRegex(pane.findChildren(QLabel, "ToolCallStartTime")[0].text(), r"^\d{2}:\d{2}:\d{2}$")
        self.assertEqual(pane.findChildren(QLabel, "ToolCallDuration")[1].text(), "Running")

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
        self.assertEqual(len(pane.findChildren(QWidget, "ToolExpandedCard")), 1)

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
        self.assertEqual(len(pane.findChildren(QWidget, "ToolTimelineDot")), 3)
        self.assertEqual(pane.findChildren(QLabel, "ToolCallDuration")[-1].text(), "--")

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
        self.assertEqual([label.text() for label in pane.findChildren(QLabel, "ToolCallDuration")], ["Running"])

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

    def test_streaming_json_assistant_reply_uses_live_json_format_before_complete_parse(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.append_assistant_delta('{"status": ')

        bubble = pane.findChild(QLabel, "AssistantBubble")
        self.assertIsNotNone(bubble)
        assert bubble is not None
        self.assertEqual(bubble.text(), '{"status": ')
        self.assertEqual(bubble.property("format"), "json")

    def test_streaming_markdown_assistant_reply_renders_before_final_answer(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.append_assistant_delta("# 标")

        bubble = pane.findChild(QLabel, "AssistantBubble")
        self.assertIsNotNone(bubble)
        assert bubble is not None
        self.assertEqual(bubble.property("format"), "markdown")
        self.assertEqual(bubble.property("raw_text"), "# 标")
        self.assertIn("<h1", bubble.text())

    def test_partial_tool_call_json_displays_streaming_json_fragment(self) -> None:
        pane = ChatPane()

        pane.add_runtime_event(
            InspectorEvent(kind="tool", title="Tool call: bash", detail='{"description": "Run', tone="accent")
        )

        status_texts = [label.text() for label in pane.findChildren(QLabel, "ToolStatusTitle")]
        self.assertEqual(status_texts, ['{"description": "Run'])

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
        self.assertEqual(pane.findChildren(QLabel, "AssistantBubble"), [])
        self.assertEqual(_assistant_row_texts(pane), ["hi"])

    def test_history_messages_keep_user_surface_compact_and_assistant_text_bounded(self) -> None:
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

        viewport_max_width = pane.scroll_area.viewport().width() - 64
        user_max_width = int(viewport_max_width * 0.75)
        assistant_max_width = int(viewport_max_width * 0.82)
        user_bubble = pane.findChild(QLabel, "UserBubble")
        assistant_body = _first_assistant_body(pane)
        self.assertIsNotNone(user_bubble)
        self.assertIsNotNone(assistant_body)
        assert user_bubble is not None
        assert assistant_body is not None
        user_text_width = QFontMetrics(user_bubble.font()).horizontalAdvance(user_bubble.text())

        self.assertLess(user_bubble.width(), user_max_width)
        self.assertGreaterEqual(user_bubble.width(), user_text_width)
        self.assertLessEqual(user_bubble.maximumWidth(), user_max_width)
        self.assertLessEqual(assistant_body.width(), assistant_max_width)

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
        assistant_bodies = pane.findChildren(QWidget, "DocumentBlockList")
        self.assertTrue(assistant_bodies)
        tool_titles = pane.findChildren(QLabel, "ToolStatusTitle")
        self.assertTrue(tool_titles)
        viewport_width = pane.scroll_area.viewport().width()
        for title in tool_titles:
            left = title.mapTo(pane.scroll_area.viewport(), title.rect().topLeft()).x()
            right = left + title.width()
            self.assertGreaterEqual(left, 0)
            self.assertLessEqual(right, viewport_width + 4)

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

    def test_assistant_stream_uses_flexible_full_width_text_layout(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "assistant transcript should blend into the pane without a bubble")

        assistant_body = _first_assistant_body(pane)
        self.assertIsNotNone(assistant_body)
        assert assistant_body is not None
        self.assertEqual(_assistant_row_texts(pane), ["assistant transcript should blend into the pane without a bubble"])

    def test_long_assistant_message_can_expand_close_to_chat_width(self) -> None:
        pane = ChatPane()
        pane.resize(1200, 800)
        pane.show()
        self.app.processEvents()

        pane.add_message("assistant", "a long assistant response " * 40)
        self.app.processEvents()

        assistant_body = _first_assistant_body(pane)
        self.assertIsNotNone(assistant_body)
        assert assistant_body is not None
        viewport_width = pane.scroll_area.viewport().width()
        self.assertGreater(assistant_body.width(), 0)
        self.assertLessEqual(assistant_body.width(), viewport_width)

    def test_streaming_markdown_assistant_reply_uses_wrapped_full_height_layout(self) -> None:
        pane = ChatPane()
        pane.resize(1200, 800)
        pane.show()
        self.app.processEvents()

        pane.start_assistant_message()
        pane.append_assistant_delta("# Heading\n\n" + ("markdown content " * 80))
        self.app.processEvents()

        bubble = pane.findChild(QLabel, "AssistantBubble")
        self.assertIsNotNone(bubble)
        assert bubble is not None
        self.assertEqual(bubble.property("format"), "markdown")
        self.assertGreaterEqual(bubble.height(), bubble.sizeHint().height())

    def test_markdown_assistant_reply_uses_native_notebook_block_widgets(self) -> None:
        pane = ChatPane()

        pane.add_message(
            "assistant",
            "# Heading\n\nParagraph with `code`.\n\n> Quote\n\n- one\n- two\n\n```python\nprint('hi')\n```",
        )

        self.assertEqual(pane.findChildren(QLabel, "AssistantBubble"), [])
        self.assertEqual(len(pane.findChildren(QWidget, "ChatHeadingBlock")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatQuoteBlock")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatListBlock")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatCodeBlock")), 1)
        self.assertEqual(len(pane.findChildren(QLabel, "ChatCodeBlockLanguage")), 1)
        self.assertEqual(len(pane.findChildren(QLabel, "ChatInlineCodeSpan")), 1)
        self.assertEqual(len(pane.findChildren(QLabel, "ChatCodeBlockText")), 1)

    def test_assistant_markdown_renders_code_block_widget_instead_of_rich_text_label(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "# Heading\n\n```python\nprint('hi')\n```")

        self.assertEqual(pane.findChildren(QLabel, "AssistantBubble"), [])
        self.assertEqual(len(pane.findChildren(QWidget, "ChatHeadingBlock")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatCodeBlock")), 1)

    def test_assistant_markdown_blocks_remain_selectable(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "Paragraph with `code`")

        paragraph_labels = pane.findChildren(QLabel, "ChatParagraphText")
        code_labels = pane.findChildren(QLabel, "ChatInlineCodeSpan")
        self.assertTrue(paragraph_labels)
        self.assertTrue(code_labels)
        self.assertTrue(
            all(label.textInteractionFlags() & Qt.TextSelectableByMouse for label in paragraph_labels + code_labels)
        )

    def test_plain_assistant_paragraph_renders_as_single_selectable_label(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "Just a plain paragraph without inline code.")

        paragraph_labels = pane.findChildren(QLabel, "ChatParagraphText")
        self.assertEqual(len(paragraph_labels), 1)
        self.assertEqual(paragraph_labels[0].text(), "Just a plain paragraph without inline code.")
        self.assertTrue(paragraph_labels[0].textInteractionFlags() & Qt.TextSelectableByMouse)

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

    def test_enter_submits_message_from_composer(self) -> None:
        pane = ChatPane()
        submitted: list[str] = []
        pane.message_submitted.connect(submitted.append)
        pane.input.setPlainText("hello from keyboard")
        pane.input.setFocus()

        QTest.keyClick(pane.input, Qt.Key_Return)

        self.assertEqual(submitted, ["hello from keyboard"])
        self.assertEqual(pane.input.toPlainText(), "")

    def test_shift_enter_keeps_newline_in_composer(self) -> None:
        pane = ChatPane()
        submitted: list[str] = []
        pane.message_submitted.connect(submitted.append)
        pane.input.setPlainText("first")
        pane.input.moveCursor(pane.input.textCursor().MoveOperation.End)
        pane.input.setFocus()

        QTest.keyClick(pane.input, Qt.Key_Return, Qt.KeyboardModifier.ShiftModifier)

        self.assertEqual(submitted, [])
        self.assertEqual(pane.input.toPlainText(), "first\n")

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
        summary = pane.findChild(QWidget, "ToolStatusSummary")
        title = pane.findChild(QLabel, "ToolStatusTitle")
        self.assertIsNotNone(card)
        self.assertIsNotNone(summary)
        self.assertIsNotNone(title)
        assert card is not None
        assert summary is not None
        assert title is not None
        self.assertEqual(card.property("status"), "running")
        self.assertEqual(summary.property("status"), "running")
        self.assertIn("Run diagnostics", title.text())
        self.assertEqual(card.property("blink"), True)

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
        self.assertEqual(summary.property("status"), "success")
        self.assertEqual(card.property("blink"), False)

    def test_chat_pane_wraps_header_scroll_and_single_floating_composer_inside_main_card(self) -> None:
        pane = ChatPane()

        card = pane.findChild(QWidget, "ChatMainCard")
        header_shell = pane.findChild(QWidget, "ChatHeaderShell")
        scroll_shell = pane.findChild(QWidget, "ChatScrollShell")
        composer = pane.findChild(QWidget, "Composer")
        composer_surface = pane.findChild(QWidget, "ComposerSurface")

        self.assertIsNotNone(card)
        self.assertIsNotNone(header_shell)
        self.assertIsNotNone(scroll_shell)
        self.assertIsNotNone(composer)
        self.assertIsNone(composer_surface)
        assert card is not None
        assert header_shell is not None
        assert scroll_shell is not None
        assert composer is not None
        self.assertIs(header_shell.parentWidget(), card)
        self.assertIs(scroll_shell.parentWidget(), card)
        self.assertIs(composer.parentWidget(), card)

    def test_floating_composer_sits_below_scroll_content_area(self) -> None:
        pane = ChatPane()
        pane.resize(1000, 700)
        pane.show()
        self.app.processEvents()

        scroll_shell = pane.findChild(QWidget, "ChatScrollShell")
        composer = pane.findChild(QWidget, "Composer")
        self.assertIsNotNone(scroll_shell)
        self.assertIsNotNone(composer)
        assert scroll_shell is not None
        assert composer is not None

        scroll_bottom = scroll_shell.geometry().bottom()
        composer_top = composer.geometry().top()
        self.assertGreaterEqual(composer_top, scroll_bottom)

    def test_send_button_sits_inside_composer_lower_right_corner(self) -> None:
        pane = ChatPane()
        pane.resize(1000, 700)
        pane.show()
        self.app.processEvents()

        composer = pane.findChild(QWidget, "Composer")
        send_button = pane.send_button
        self.assertIsNotNone(composer)
        assert composer is not None

        self.assertIs(send_button.parentWidget(), composer)
        self.assertGreaterEqual(send_button.geometry().right(), composer.width() - 180)
        self.assertGreaterEqual(send_button.geometry().bottom(), composer.height() - 96)

    def test_message_input_does_not_draw_a_second_nested_box(self) -> None:
        stylesheet = theme_stylesheet("day")

        self.assertIn("#Composer {", stylesheet)
        self.assertIn("background: #ffffff;", stylesheet)
        self.assertIn("#MessageInput {", stylesheet)
        self.assertIn("border: 0;", stylesheet)

    def test_chat_theme_uses_subtle_user_surface_and_plain_assistant_text(self) -> None:
        stylesheet = theme_stylesheet("day")

        self.assertIn("#UserBubble {", stylesheet)
        self.assertIn("background: rgba(255,255,255,0.72);", stylesheet)
        self.assertIn("#AssistantBubble {", stylesheet)
        self.assertIn("background: transparent;", stylesheet)
        self.assertIn("border: 0;", stylesheet)

    def test_chat_theme_makes_tool_transcript_fully_blend_into_background(self) -> None:
        stylesheet = theme_stylesheet("day")

        self.assertIn("#ToolStatusSummary {", stylesheet)
        self.assertIn("#ToolExpandedCard {", stylesheet)
        self.assertIn("#ToolCallRow {", stylesheet)
        self.assertIn("#ToolTimelineLine {", stylesheet)
        self.assertIn("border-bottom: 0;", stylesheet)
        self.assertIn("background: transparent;", stylesheet)

    def test_chat_main_card_has_no_extra_shadow_or_outer_inset(self) -> None:
        pane = ChatPane()
        pane.resize(1000, 700)
        pane.show()
        self.app.processEvents()

        card = pane.findChild(QWidget, "ChatMainCard")
        self.assertIsNotNone(card)
        assert card is not None
        self.assertIsNone(card.graphicsEffect())
        self.assertEqual(card.geometry(), pane.rect())

    def test_tool_status_row_aligns_with_assistant_bubble_not_avatar(self) -> None:
        pane = ChatPane()
        pane.resize(1000, 700)
        pane.show()
        self.app.processEvents()

        pane.add_message("assistant", "alignment check")
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run diagnostics", "command": "pytest"}',
                tone="accent",
            )
        )
        self.app.processEvents()

        assistant_body = _first_assistant_body(pane)
        tool_card = pane.findChild(QWidget, "ToolStatusRow")
        self.assertIsNotNone(assistant_body)
        self.assertIsNotNone(tool_card)
        assert assistant_body is not None
        assert tool_card is not None

        bubble_left = assistant_body.mapTo(pane.scroll_area.viewport(), assistant_body.rect().topLeft()).x()
        tool_left = tool_card.mapTo(pane.scroll_area.viewport(), tool_card.rect().topLeft()).x()
        self.assertEqual(tool_left, bubble_left)

    def test_tool_status_toggle_uses_trailing_chevron(self) -> None:
        pane = ChatPane()
        pane.show()
        self.app.processEvents()
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run diagnostics", "command": "pytest"}',
                tone="accent",
            )
        )

        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        title = pane.findChild(QLabel, "ToolStatusTitle")
        self.assertIsNotNone(toggle)
        self.assertIsNotNone(title)
        assert toggle is not None
        assert title is not None
        self.assertEqual(toggle.text(), ">")
        self.app.processEvents()
        self.assertGreater(toggle.pos().x(), title.pos().x())

    def test_expanded_tool_card_shows_status_and_start_time_on_second_line(self) -> None:
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
        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        toggle.click()

        status = pane.findChild(QLabel, "ToolCallStatus")
        start_time = pane.findChild(QLabel, "ToolCallStartTime")
        duration = pane.findChild(QLabel, "ToolCallDuration")
        self.assertIsNotNone(status)
        self.assertIsNotNone(start_time)
        self.assertIsNotNone(duration)
        assert status is not None
        assert start_time is not None
        assert duration is not None
        self.assertEqual(status.text(), "Running")
        self.assertRegex(start_time.text(), r"^\d{2}:\d{2}:\d{2}$")
        self.assertEqual(duration.text(), "Running")

    def test_chat_layout_uses_tighter_message_and_tool_spacing(self) -> None:
        pane = ChatPane()
        pane.add_message("assistant", "spacing check")
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run diagnostics", "command": "pytest"}',
                tone="accent",
            )
        )

        assistant_group = pane.findChild(QWidget, "AssistantMessageGroup")
        tool_group = pane.findChild(QWidget, "ToolStatusGroup")
        tool_summary = pane.findChild(QWidget, "ToolStatusSummary")
        self.assertIsNotNone(assistant_group)
        self.assertIsNotNone(tool_group)
        self.assertIsNotNone(tool_summary)
        assert assistant_group is not None
        assert tool_group is not None
        assert tool_summary is not None

        self.assertEqual(pane.messages.spacing(), 4)
        self.assertEqual(assistant_group.layout().spacing(), 2)
        self.assertEqual(tool_group.layout().contentsMargins().left(), 38)
        self.assertEqual(tool_summary.layout().contentsMargins().top(), 1)
        self.assertEqual(tool_summary.layout().contentsMargins().bottom(), 1)


if __name__ == "__main__":
    unittest.main()


def _visible_chat_flow(pane: ChatPane) -> list[str]:
    texts: list[str] = []
    for index in range(pane.messages.count()):
        item = pane.messages.itemAt(index)
        widget = item.widget() if item is not None else None
        if widget is None:
            continue
        texts.extend(_message_widget_texts(widget))
    return texts


def _message_widget_texts(widget: QWidget) -> list[str]:
    if (thinking := widget.findChild(QLabel, "ThinkingStatusTitle")) is not None:
        return [thinking.text()]
    if (tool := widget.findChild(QLabel, "ToolStatusTitle")) is not None:
        return [tool.text()]
    if (bubble := widget.findChild(QLabel, "AssistantBubble")) is not None:
        return [bubble.text()]
    return _assistant_row_texts(widget)


def _assistant_row_texts(widget: QWidget) -> list[str]:
    texts: list[str] = []
    for paragraph in widget.findChildren(QWidget, "ChatParagraphBlock"):
        plain_text = paragraph.property("plain_text")
        if plain_text:
            texts.append(str(plain_text))
    return texts


def _first_assistant_body(widget: QWidget) -> QWidget | None:
    return widget.findChild(QWidget, "DocumentBlockList")
