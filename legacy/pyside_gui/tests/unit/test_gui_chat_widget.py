from __future__ import annotations

import math
import os
import json
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QSizePolicy, QTextEdit, QWidget

from gui.theme import UI_FONT_FAMILIES, register_ui_fonts, theme_stylesheet
from gui.workers import InspectorEvent
from gui.widgets.chat import ChatPane
from gui.widgets.chat_rows import AssistantMessageRow


class GuiChatWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        register_ui_fonts()

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
            if label.objectName() in {"UserBubbleText", "AssistantBubble"}
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
        self.assertEqual(status_texts, ["Executed 1 tool"])

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
        self.assertEqual(status_texts, ["Executed 1 tool"])
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
        QTest.qWait(40)
        self.app.processEvents()

        expected = ["thinking", "Executed 1 tool", "first reply", "Executed 1 tool", "second reply"]
        runtime_expected = ["second reply"]
        self.assertEqual(_visible_chat_flow(history_pane), expected)
        self.assertEqual(_visible_chat_flow(runtime_pane), runtime_expected)
        self.assertEqual(
            _visible_chat_rows(history_pane),
            [
                ("assistant", "thinking"),
                ("tool", "Executed 1 tool"),
                ("assistant", "first reply"),
                ("tool", "Executed 1 tool"),
                ("assistant", "second reply"),
            ],
        )
        self.assertEqual(
            _visible_chat_rows(runtime_pane),
            [("assistant", "second reply")],
        )
        self.assertEqual(_activity_detail_flow(runtime_pane), ["thinking", "Executed 1 tool", "first reply", "Executed 1 tool"])
        self.assertEqual(len(history_pane.findChildren(QWidget, "ToolStatusGroup")), 2)
        self.assertEqual(len(runtime_pane.findChildren(QWidget, "ToolStatusGroup")), 2)

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

        activity_toggle = pane.findChild(QPushButton, "ActivityGroupToggle")
        self.assertIsNotNone(activity_toggle)
        assert activity_toggle is not None
        activity_toggle.click()

        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        toggle.click()

        lines = [label.text() for label in pane.findChildren(QLabel, "ToolCallLine")]
        self.assertEqual(lines, ["Read files", "Run tests"])

    def test_add_runtime_event_inserts_status_row(self) -> None:
        pane = ChatPane()

        pane.add_runtime_event(
            InspectorEvent(kind="tool", title="Tool call: bash", detail='{"description": "Run tests", "command": "pytest"}', tone="accent")
        )

        status_texts = [label.text() for label in pane.findChildren(QLabel) if label.objectName() == "ToolStatusTitle"]
        self.assertEqual(status_texts, ["Run tests", "Executed 1 tool"])
        self.assertEqual(_visible_chat_flow(pane), ["Run tests"])
        self.assertEqual(_activity_detail_flow(pane), ["Executed 1 tool"])
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

        activity_toggle = pane.findChild(QPushButton, "ActivityGroupToggle")
        self.assertIsNotNone(activity_toggle)
        assert activity_toggle is not None
        activity_toggle.click()

        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        self.assertEqual(pane.findChildren(QLabel, "ToolCallLine"), [])

        toggle.click()

        detail_texts = [label.text() for label in pane.findChildren(QLabel) if label.objectName() == "ToolCallLine"]
        self.assertEqual(detail_texts, ["Bash pytest"])
        self.assertEqual(len(pane.findChildren(QWidget, "ToolCompactPanel")), 1)

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
        self.assertEqual(summaries, ["Executed 3 tools"])
        self.assertEqual(pane.findChildren(QLabel, "ToolStatusDetail"), [])

        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        toggle.click()

        lines = [label.text() for label in pane.findChildren(QLabel, "ToolCallLine")]
        self.assertEqual(lines, ["Read README.md", "Bash pytest", "Bash exit 1"])
        self.assertFalse(any("..." in line for line in lines))
        self.assertFalse(any(len(line) > 80 for line in lines))
        self.assertEqual(len(pane.findChildren(QWidget, "ToolTimelineDot")), 3)

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

        self.assertEqual([label.text() for label in pane.findChildren(QLabel, "ToolCallLine")], ["Bash uv sync"])
        self.assertEqual([label.text() for label in pane.findChildren(QLabel, "ToolCallDuration")], ["Running"])

    def test_glob_tool_line_includes_pattern_target(self) -> None:
        pane = ChatPane()
        pane.render_history(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "payload": {
                        "tool_calls": [
                            {
                                "id": "call_glob",
                                "function": {
                                    "name": "glob",
                                    "arguments": '{"pattern": "src/**/*.py"}',
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

        self.assertEqual([label.text() for label in pane.findChildren(QLabel, "ToolCallLine")], ["Glob src/**/*.py"])

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

        visible_flow = _visible_chat_flow(pane)
        self.assertEqual(visible_flow, ["done"])

        self.assertEqual(_activity_detail_flow(pane), ["Thinking", "Executed 1 tool"])

    def test_streaming_markdown_before_tool_call_moves_into_activity(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.append_assistant_delta("first")
        original_rows = pane.findChildren(QWidget, "AssistantMessageGroup")
        self.assertEqual(len(original_rows), 1)
        original_row = original_rows[0]

        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: read",
                detail='{"description": "Read files", "path": "README.md"}',
                tone="accent",
                metadata={"tool_call_id": "call_read"},
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
        pane.append_assistant_delta("\n\n## More")
        QTest.qWait(40)
        self.app.processEvents()

        self.assertEqual(
            _visible_chat_rows(pane),
            [("assistant", "More")],
        )
        self.assertNotEqual(pane.findChildren(QWidget, "AssistantMessageGroup")[-1], original_row)
        self.assertEqual(_activity_detail_flow(pane), ["first", "Executed 1 tool"])

    def test_tool_call_freezes_existing_stream_text_inside_activity(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.append_assistant_delta("thinking")
        live_rows = pane.findChildren(QWidget, "AssistantMessageGroup")
        self.assertEqual(len(live_rows), 1)
        original_row = live_rows[0]

        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: read",
                detail='{"description": "Read files", "path": "README.md"}',
                tone="accent",
                metadata={"tool_call_id": "call_read"},
            )
        )
        pane.append_assistant_delta("final")

        self.assertEqual(
            _visible_chat_rows(pane),
            [("assistant", "final")],
        )
        self.assertEqual(_activity_detail_flow(pane), ["thinking", "Executed 1 tool"])
        self.assertNotEqual(pane.findChildren(QWidget, "AssistantMessageGroup")[-1], original_row)

    def test_streaming_assistant_reply_without_tools_replaces_thinking_status(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.append_assistant_delta("hello")

        ordered_labels = [
            label.text()
            for index in range(pane.messages.count())
            if (widget := pane.messages.itemAt(index).widget()) is not None
            for label in widget.findChildren(QLabel)
            if label.objectName() == "ToolStatusTitle" and label.isVisible()
        ]
        self.assertEqual(ordered_labels, [])
        self.assertEqual(_visible_chat_flow(pane), ["hello"])

    def test_streaming_assistant_deltas_are_coalesced_before_markdown_render(self) -> None:
        import gui.widgets.chat as chat_module

        pane = ChatPane()
        original_parse = chat_module.parse_markdown_blocks
        calls: list[str] = []

        def counting_parse(text: str):
            calls.append(text)
            return original_parse(text)

        chat_module.parse_markdown_blocks = counting_parse
        try:
            pane.start_assistant_message()
            for _ in range(20):
                pane.append_assistant_delta("x")
            pane.finish_assistant_message("x" * 20)
        finally:
            chat_module.parse_markdown_blocks = original_parse

        self.assertLessEqual(len(calls), 2)
        self.assertEqual(calls[-1], "x" * 20)
        self.assertEqual(_visible_chat_flow(pane), ["x" * 20])

    def test_streaming_json_assistant_reply_uses_live_json_format_before_complete_parse(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.append_assistant_delta('{"status": ')

        self.assertEqual(pane.findChildren(QLabel, "AssistantBubble"), [])
        self.assertEqual(_visible_chat_flow(pane), ['{"status":'])

    def _legacy_streaming_markdown_assistant_reply_renders_before_final_answer(self) -> None:
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
        self.assertEqual(status_texts, ['{"description": "Run', "Executed 1 tool"])
        self.assertEqual(_visible_chat_flow(pane), ['{"description": "Run'])
        self.assertEqual(_activity_detail_flow(pane), ["Executed 1 tool"])

    def test_activity_title_becomes_processed_when_turn_finishes(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run tests", "command": "pytest"}',
                tone="accent",
                metadata={"tool_call_id": "call_test"},
            )
        )
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool result: call_test success",
                detail="ok",
                tone="ok",
                metadata={"tool_call_id": "call_test"},
            )
        )
        pane.finish_assistant_message("done")

        activity = pane.findChild(QWidget, "ActivityGroup")
        self.assertIsNotNone(activity)
        assert activity is not None
        self.assertRegex(_activity_group_title(activity), r"^Processed for \d")

    def test_activity_without_description_shows_acting_until_processed(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"command": "pytest"}',
                tone="accent",
                metadata={"tool_call_id": "call_test"},
            )
        )

        activity_titles = [
            label.text()
            for label in pane.findChildren(QLabel, "ToolStatusTitle")
            if label.parentWidget() is not None
            and label.parentWidget().objectName() == "ActivityGroupSummary"
        ]
        self.assertEqual(activity_titles, ["Acting"])

        pane.finish_assistant_message("done")
        activity = pane.findChild(QWidget, "ActivityGroup")
        self.assertIsNotNone(activity)
        assert activity is not None
        self.assertRegex(_activity_group_title(activity), r"^Processed for \d")

    def test_tool_call_after_streaming_reply_stays_in_same_activity_group(self) -> None:
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

        visible_flow = _visible_chat_flow(pane)
        self.assertEqual(visible_flow, ["Run tests"])
        self.assertEqual(
            _visible_chat_rows(pane),
            [
                ("tool", "Run tests"),
            ],
        )
        self.assertEqual(_activity_detail_flow(pane), ["Thinking", "Executed 1 tool", "first reply", "Executed 1 tool"])
        self.assertEqual(len(pane.findChildren(QWidget, "ToolStatusGroup")), 2)

    def test_clicked_activity_keeps_later_streaming_tool_work_in_same_activity_group(self) -> None:
        pane = ChatPane()
        pane.show()
        self.app.processEvents()

        pane.start_assistant_message()
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: read",
                detail='{"description": "Read files", "path": "README.md"}',
                tone="accent",
                metadata={"tool_call_id": "call_read"},
            )
        )
        activity_toggle = pane.findChild(QPushButton, "ActivityGroupToggle")
        self.assertIsNotNone(activity_toggle)
        assert activity_toggle is not None
        activity_toggle.click()
        self.app.processEvents()

        pane.append_assistant_delta("first reply")
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run tests", "command": "pytest"}',
                tone="accent",
                metadata={"tool_call_id": "call_test"},
            )
        )
        pane.finish_assistant_message("final answer")
        self.app.processEvents()

        self.assertEqual(
            _visible_chat_rows(pane),
            [("assistant", "final answer")],
        )
        self.assertEqual(len(pane.findChildren(QWidget, "ActivityGroup")), 1)
        self.assertEqual(_activity_detail_flow(pane), ["Executed 1 tool", "first reply", "Executed 1 tool"])

    def test_finish_assistant_message_breaks_tool_group_before_later_runtime_call(self) -> None:
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
        pane.finish_assistant_message("first reply")
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run tests", "command": "pytest"}',
                tone="accent",
            )
        )

        self.assertEqual(
            _visible_chat_rows(pane),
            [
                ("assistant", "first reply"),
                ("tool", "Run tests"),
            ],
        )
        self.assertEqual(len(pane.findChildren(QWidget, "ToolStatusGroup")), 2)

    def test_assistant_turn_shows_single_avatar_for_thinking_tools_and_reply(self) -> None:
        pane = ChatPane()
        pane.show()
        self.app.processEvents()

        pane.render_history(
            [
                {"role": "user", "content": "question"},
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
                {"role": "assistant", "content": "final answer"},
            ]
        )

        activity = pane.findChild(QWidget, "ActivityGroup")
        self.assertIsNotNone(activity)
        assert activity is not None
        avatars = activity.findChildren(QLabel, "AssistantAvatar", Qt.FindDirectChildrenOnly)
        self.assertEqual(len(avatars), 1)
        self.assertIs(avatars[0], activity.findChild(QLabel, "AssistantAvatar"))

    def test_user_and_assistant_messages_keep_opposite_sides_and_avatars(self) -> None:
        pane = ChatPane()

        pane.add_message("user", "hello")
        pane.add_message("assistant", "hi")

        self.assertEqual([label.text() for label in pane.findChildren(QLabel, "UserAvatar")], ["U"])
        self.assertEqual([label.text() for label in pane.findChildren(QLabel, "AssistantAvatar")], ["L"])
        self.assertEqual(len(pane.findChildren(QLabel, "UserBubbleText")), 1)
        self.assertEqual(pane.findChildren(QLabel, "AssistantBubble"), [])
        self.assertEqual(_assistant_row_texts(pane), ["hi"])

    def test_user_bubble_recovers_after_viewport_grows(self) -> None:
        pane = ChatPane()
        pane.resize(320, 400)
        pane.show()
        self.app.processEvents()
        text = "看一下这个项目的文档和代码有什么不一致的地方"
        pane.add_message("user", text)
        _process_layout_events(self.app)

        pane.resize(1200, 800)
        _process_layout_events(self.app)

        user_bubble = pane.findChild(QFrame, "UserBubble")
        user_bubble_text = pane.findChild(QLabel, "UserBubbleText")
        self.assertIsNotNone(user_bubble)
        self.assertIsNotNone(user_bubble_text)
        assert user_bubble is not None
        assert user_bubble_text is not None

        text_advance = QFontMetrics(user_bubble_text.font()).horizontalAdvance(text)
        text_geom = user_bubble_text.geometry()

        self.assertLessEqual(text_geom.x(), 2)
        self.assertLessEqual(abs(text_geom.width() - text_advance), 2)
        self.assertLessEqual(abs(user_bubble.width() - (text_advance + 30)), 2)

    def test_short_user_message_stays_single_line_after_viewport_grows(self) -> None:
        pane = ChatPane()
        pane.resize(900, 700)
        pane.show()
        self.app.processEvents()
        text = "好的"
        pane.add_message("user", text)
        _process_layout_events(self.app)

        pane.resize(1400, 900)
        _process_layout_events(self.app)

        user_bubble_text = pane.findChild(QLabel, "UserBubbleText")
        self.assertIsNotNone(user_bubble_text)
        assert user_bubble_text is not None

        metrics = QFontMetrics(user_bubble_text.font())
        text_advance = metrics.horizontalAdvance(text)
        line_count = user_bubble_text.height() / metrics.lineSpacing()

        self.assertFalse(user_bubble_text.wordWrap())
        self.assertGreaterEqual(user_bubble_text.width(), text_advance - 1)
        self.assertLess(line_count, 1.5)

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
        user_bubble = pane.findChild(QFrame, "UserBubble")
        user_bubble_text = pane.findChild(QLabel, "UserBubbleText")
        assistant_body = _first_assistant_body(pane)
        self.assertIsNotNone(user_bubble)
        self.assertIsNotNone(user_bubble_text)
        self.assertIsNotNone(assistant_body)
        assert user_bubble is not None
        assert user_bubble_text is not None
        assert assistant_body is not None
        user_text_width = QFontMetrics(user_bubble_text.font()).horizontalAdvance(user_bubble_text.text())

        self.assertLess(user_bubble.width(), user_max_width)
        self.assertGreaterEqual(user_bubble.width(), user_text_width)
        self.assertLessEqual(user_bubble.maximumWidth(), user_max_width)
        self.assertLessEqual(assistant_body.width(), assistant_max_width)

    def test_short_chat_rows_do_not_expand_far_taller_than_their_content(self) -> None:
        pane = ChatPane()
        pane.resize(1000, 700)
        pane.show()
        self.app.processEvents()

        pane.add_message("user", "short")
        pane.add_message("assistant", "short")
        self.app.processEvents()

        user_row = pane.findChild(QWidget, "UserMessageGroup")
        assistant_row = pane.findChild(QWidget, "AssistantMessageGroup")
        self.assertIsNotNone(user_row)
        self.assertIsNotNone(assistant_row)
        assert user_row is not None
        assert assistant_row is not None

        self.assertLessEqual(user_row.height(), user_row.sizeHint().height() + 6)
        self.assertLessEqual(assistant_row.height(), assistant_row.sizeHint().height() + 6)

    def test_component_assistant_rows_fit_inside_viewport_without_horizontal_scroll(self) -> None:
        pane = ChatPane()
        pane.resize(320, 420)
        pane.show()
        self.app.processEvents()

        pane.add_message("assistant", "a long assistant response " * 40)
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run a very long diagnostic command description that should wrap instead of widening the chat"}',
                tone="accent",
            )
        )
        self.app.processEvents()

        self.assertEqual(pane.scroll_area.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        assistant_bodies = pane.findChildren(QWidget, "DocumentBlockList")
        self.assertTrue(assistant_bodies)
        tool_titles = pane.findChildren(QLabel, "ToolStatusTitle")
        self.assertTrue(tool_titles)
        viewport_width = pane.scroll_area.viewport().width()
        for body in assistant_bodies:
            left = body.mapTo(pane.scroll_area.viewport(), body.rect().topLeft()).x()
            right = left + body.width()
            self.assertGreaterEqual(left, 0)
            self.assertLessEqual(right, viewport_width)
        for title in tool_titles:
            if not title.isVisible():
                continue
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
            if label.objectName() in {"UserBubbleText", "UserAvatar", "AssistantAvatar"}:
                left = label.mapTo(pane.scroll_area.viewport(), label.rect().topLeft()).x()
                right = left + label.width()
                self.assertGreaterEqual(left, 0)
                self.assertLessEqual(right, viewport_width)
        assistant_body = _first_assistant_body(pane)
        self.assertIsNotNone(assistant_body)
        assert assistant_body is not None
        body_left = assistant_body.mapTo(pane.scroll_area.viewport(), assistant_body.rect().topLeft()).x()
        body_right = body_left + assistant_body.width()
        self.assertGreaterEqual(body_left, 0)
        self.assertLessEqual(body_right, viewport_width)

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

    def test_short_assistant_message_uses_most_of_transcript_width(self) -> None:
        pane = ChatPane()
        pane.resize(1200, 800)
        pane.show()
        self.app.processEvents()

        pane.add_message("assistant", "short text")
        self.app.processEvents()

        assistant_body = _first_assistant_body(pane)
        self.assertIsNotNone(assistant_body)
        assert assistant_body is not None
        viewport_width = pane.scroll_area.viewport().width()
        self.assertGreaterEqual(assistant_body.width(), int(viewport_width * 0.75))
        self.assertLessEqual(assistant_body.width(), int(viewport_width * 0.86))

    def test_streaming_markdown_assistant_reply_uses_wrapped_full_height_layout(self) -> None:
        pane = ChatPane()
        pane.resize(1200, 800)
        pane.show()
        self.app.processEvents()

        pane.start_assistant_message()
        pane.append_assistant_delta("# Heading\n\n" + ("markdown content " * 80))
        self.app.processEvents()

        body = _first_assistant_body(pane)
        self.assertIsNotNone(body)
        assert body is not None
        self.assertGreaterEqual(body.height(), body.sizeHint().height())

    def test_streaming_markdown_height_stays_synced_after_large_rerenders(self) -> None:
        pane = ChatPane()
        pane.resize(420, 260)
        pane.show()
        self.app.processEvents()
        pane.scroll_area.viewport().resize(420, 220)
        pane._sync_transcript_width_after_layout()
        pane._update_bubble_widths()

        pane.start_assistant_message()
        chunks = [
            "# Title\n\n",
            "paragraph " * 30,
            "\n\n## Second heading\n\n",
            "body " * 80,
            "\n\n```python\n",
            'print("x")\n' * 10,
            "```",
        ]
        heights: list[int] = []
        for chunk in chunks:
            pane.append_assistant_delta(chunk)
            QTest.qWait(45)
            self.app.processEvents()
            body = _first_assistant_body(pane)
            if body is not None:
                heights.append(body.height())

        body = _first_assistant_body(pane)
        self.assertIsNotNone(body)
        assert body is not None
        expected_height = body._content_height_for_width(body.width())
        self.assertGreaterEqual(body.height(), expected_height)
        for previous, current in zip(heights, heights[1:]):
            self.assertGreaterEqual(current + 4, previous)

    def test_markdown_paragraph_spacing_stays_tight_after_window_widens(self) -> None:
        pane = ChatPane()
        pane.resize(505, 700)
        pane.show()
        self.app.processEvents()

        content = (
            "First paragraph.\n\n"
            "Second paragraph with enough words to wrap on a narrow chat column but stay shorter once the pane is wide.\n\n"
            "Third paragraph."
        )
        pane.add_message("assistant", content)
        _process_layout_events(self.app)

        body = _first_assistant_body(pane)
        self.assertIsNotNone(body)
        assert body is not None
        narrow_blocks = pane.findChildren(QWidget, "ChatParagraphBlock")
        narrow_gaps = [
            narrow_blocks[index + 1].geometry().top() - narrow_blocks[index].geometry().bottom()
            for index in range(len(narrow_blocks) - 1)
        ]

        pane.resize(1600, 900)
        _process_layout_events(self.app)

        wide_blocks = pane.findChildren(QWidget, "ChatParagraphBlock")
        wide_gaps = [
            wide_blocks[index + 1].geometry().top() - wide_blocks[index].geometry().bottom()
            for index in range(len(wide_blocks) - 1)
        ]
        expected_height = body._content_height_for_width(body.width())

        self.assertEqual(narrow_gaps, [8, 8])
        self.assertEqual(wide_gaps, [8, 8])
        self.assertEqual(body.height(), expected_height)

    def test_chat_transcript_keeps_only_a_small_bottom_gap_above_composer(self) -> None:
        pane = ChatPane()
        self.assertLessEqual(pane.messages.contentsMargins().bottom(), 16)

    def test_real_chat_session_does_not_leave_scrollable_blank_space_after_last_reply(self) -> None:
        session_path = Path("sessions/chat-chat-20260608-104736-bfa38e/session.json")
        if not session_path.exists():
            self.skipTest("diagnostic chat session fixture is not available")
        history = json.loads(session_path.read_text(encoding="utf-8")).get("history") or []
        pane = ChatPane()
        pane.resize(507, 788)
        pane.show()
        self.app.processEvents()

        pane.render_history(history)
        _process_layout_events(self.app)
        bar = pane.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())
        _process_layout_events(self.app)

        rows = _transcript_widgets(pane)
        self.assertTrue(rows)
        last_row = rows[-1]
        body_extra = pane.scroll_body.height() - 1 - last_row.geometry().bottom()
        last_bottom = last_row.mapTo(pane.scroll_area.viewport(), last_row.rect().bottomLeft()).y()
        bottom_gap = pane.scroll_area.viewport().height() - 1 - last_bottom
        allowed_gap = pane.messages.contentsMargins().bottom() + 8
        self.assertLessEqual(body_extra, allowed_gap)
        self.assertLessEqual(bottom_gap, allowed_gap)

    def test_history_rendered_before_show_fits_assistant_text_after_initial_show(self) -> None:
        session_path = Path("sessions/chat-chat-20260608-104736-bfa38e/session.json")
        if not session_path.exists():
            self.skipTest("diagnostic chat session fixture is not available")
        history = json.loads(session_path.read_text(encoding="utf-8")).get("history") or []
        pane = ChatPane()

        pane.render_history(history)
        pane.resize(507, 788)
        pane.show()
        _process_layout_events(self.app)

        viewport_width = pane.scroll_area.viewport().width()
        for body in pane.findChildren(QWidget, "DocumentBlockList"):
            left = body.mapTo(pane.scroll_area.viewport(), body.rect().topLeft()).x()
            right = left + body.width()
            self.assertGreaterEqual(left, 0)
            self.assertLessEqual(right, viewport_width)

    def test_composer_is_bottom_layout_child_without_reserved_scroll_padding(self) -> None:
        pane = ChatPane()
        pane.resize(1000, 700)
        pane.show()
        self.app.processEvents()

        card_layout = pane.main_card.layout()
        self.assertIsNotNone(card_layout)
        assert card_layout is not None

        self.assertEqual(card_layout.contentsMargins().bottom(), 0)
        self.assertIs(pane.composer.parentWidget(), pane.composer_dock)
        self.assertIs(pane.composer_dock.parentWidget(), pane.main_card)
        self.assertGreaterEqual(card_layout.indexOf(pane.composer_dock), 0)
        composer_top = pane.composer.mapTo(pane.main_card, pane.composer.rect().topLeft()).y()
        self.assertLess(pane.scroll_shell.geometry().bottom(), composer_top)
        self.assertEqual(pane.composer_dock.geometry().bottom(), pane.main_card.rect().bottom())

    def test_chat_header_stays_fixed_while_transcript_scrolls(self) -> None:
        pane = ChatPane()
        pane.resize(507, 788)
        pane.show()
        self.app.processEvents()
        for index in range(40):
            pane.add_message("assistant", f"# Heading {index}\n\n" + ("body " * 40))
        _process_layout_events(self.app)

        header_y = pane.header_shell.mapTo(pane, pane.header_shell.rect().topLeft()).y()
        title_y = pane.header.mapTo(pane, pane.header.rect().topLeft()).y()
        scroll_top = pane.scroll_shell.mapTo(pane, pane.scroll_shell.rect().topLeft()).y()
        bar = pane.scroll_area.verticalScrollBar()
        bar.setValue(bar.minimum())
        _process_layout_events(self.app)
        bar.setValue(bar.maximum())
        _process_layout_events(self.app)

        self.assertEqual(pane.header_shell.mapTo(pane, pane.header_shell.rect().topLeft()).y(), header_y)
        self.assertEqual(pane.header.mapTo(pane, pane.header.rect().topLeft()).y(), title_y)
        self.assertGreaterEqual(scroll_top, pane.header_shell.geometry().bottom())
        self.assertEqual(pane.header_shell.sizePolicy().verticalPolicy(), QSizePolicy.Fixed)

    def test_history_collapses_tools_and_intermediate_assistant_between_user_and_final_reply(self) -> None:
        pane = ChatPane()
        pane.show()
        self.app.processEvents()

        pane.render_history(
            [
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "I will inspect"},
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
                {"role": "assistant", "content": "final answer"},
            ]
        )

        self.assertEqual(
            _visible_chat_rows(pane),
            [("user", "first question"), ("assistant", "final answer")],
        )
        self.assertEqual(len(pane.findChildren(QWidget, "ToolStatusGroup")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "AssistantMessageGroup")), 2)

        activity_toggle = pane.findChild(QPushButton, "ActivityGroupToggle")
        self.assertIsNotNone(activity_toggle)
        assert activity_toggle is not None
        self.assertFalse(pane.findChildren(QWidget, "ToolStatusSummary")[0].isVisible())
        activity_toggle.click()
        self.app.processEvents()
        self.assertTrue(pane.findChildren(QWidget, "ToolStatusSummary")[0].isVisible())

    def test_history_activity_detail_preserves_assistant_tool_interleaving(self) -> None:
        pane = ChatPane()

        pane.render_history(
            [
                {"role": "user", "content": "question"},
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
                {"role": "tool", "content": '{"status": "success", "result": "ok"}', "tool_call_id": "call_test"},
                {"role": "assistant", "content": "final answer"},
            ]
        )

        self.assertEqual(_activity_detail_flow(pane), ["thinking", "Executed 1 tool", "first reply", "Executed 1 tool"])
        self.assertEqual(len(pane.findChildren(QWidget, "ToolStatusGroup")), 2)

    def test_history_assistant_message_replays_as_componentized_row(self) -> None:
        pane = ChatPane()

        pane.render_history([{"role": "assistant", "content": "# Heading\n\nBody"}])

        self.assertEqual(len(pane.findChildren(QWidget, "AssistantMessageGroup")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatHeadingBlock")), 1)
        self.assertEqual(len(pane.findChildren(QLabel, "AssistantBubble")), 0)

    def test_streaming_assistant_message_rebuilds_active_block_list(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.append_assistant_delta("# Heading")
        pane.append_assistant_delta("\n\n```python\nprint('hi')\n```")
        QTest.qWait(40)
        self.app.processEvents()

        self.assertEqual(len(pane.findChildren(QWidget, "AssistantMessageGroup")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatCodeBlock")), 1)
        self.assertEqual(
            [label.text() for label in pane.findChildren(QLabel, "ChatCodeBlockText")],
            ["print('hi')"],
        )

    def test_finish_assistant_message_renders_final_only_reply_and_scrolls_to_bottom(self) -> None:
        pane = ChatPane()
        pane.resize(420, 220)
        pane.show()
        self.app.processEvents()

        for index in range(12):
            pane.add_message("user", f"message {index}")
        pane.start_assistant_message()
        pane.finish_assistant_message("# Heading\n\nBody")
        self.app.processEvents()

        self.assertEqual(len(pane.findChildren(QWidget, "AssistantMessageGroup")), 1)
        self.assertEqual(_visible_chat_flow(pane)[-1], "Heading\n\nBody")
        scrollbar = pane.scroll_area.verticalScrollBar()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())

    def test_streaming_markdown_assistant_reply_renders_before_final_answer(self) -> None:
        pane = ChatPane()

        pane.start_assistant_message()
        pane.append_assistant_delta("# Heading")

        heading = pane.findChild(QLabel, "ChatHeadingBlock")
        self.assertIsNotNone(heading)
        assert heading is not None
        self.assertEqual(heading.text(), "Heading")

    def test_code_block_uses_notebook_style_widget_names(self) -> None:
        pane = ChatPane()

        pane.add_message(
            "assistant",
            "# Heading\n\nParagraph with `code`.\n\n> Quote\n\n- one\n- two\n\n```python\nprint('hi')\n```",
        )

        self.assertEqual(pane.findChildren(QLabel, "AssistantBubble"), [])
        self.assertEqual(len(pane.findChildren(QWidget, "ChatCodeBlock")), 1)
        self.assertEqual(len(pane.findChildren(QLabel, "ChatCodeBlockLanguage")), 1)
        self.assertEqual(len(pane.findChildren(QLabel, "ChatCodeBlockText")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatHeadingBlock")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatQuoteBlock")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatListBlock")), 1)
        self.assertEqual(len(pane.findChildren(QTextEdit, "ChatParagraphText")), 1)

    def test_markdown_horizontal_rule_renders_as_rule_widget(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "Before\n\n---\n\nAfter")

        rules = pane.findChildren(QWidget, "ChatHorizontalRuleBlock")
        lines = pane.findChildren(QWidget, "ChatHorizontalRuleLine")
        self.assertEqual(len(rules), 1)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].height(), 1)
        self.assertEqual(_visible_chat_flow(pane), ["Before\n\nAfter"])

    def test_heading_inline_code_renders_inside_heading(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "## Update `development-guide.md`")

        heading = pane.findChild(QLabel, "ChatHeadingBlock")
        self.assertIsNotNone(heading)
        assert heading is not None
        self.assertEqual(heading.property("plain_text"), "Update development-guide.md")
        self.assertIn("development-guide.md", heading.text())

    def test_heading_uses_larger_font_than_paragraph(self) -> None:
        pane = ChatPane()
        pane.add_message("assistant", "## Lora 项目概览\n\n正文段落")

        heading = pane.findChild(QLabel, "ChatHeadingBlock")
        paragraph = pane.findChild(QLabel, "ChatParagraphText")
        self.assertIsNotNone(heading)
        self.assertIsNotNone(paragraph)
        assert heading is not None
        assert paragraph is not None
        self.assertEqual(heading.text(), "Lora 项目概览")
        self.assertGreater(heading.font().pointSizeF(), paragraph.font().pointSizeF())

    def test_indented_heading_renders_without_leading_space(self) -> None:
        pane = ChatPane()
        pane.add_message("assistant", " ## Lora 项目概览")

        heading = pane.findChild(QLabel, "ChatHeadingBlock")
        self.assertIsNotNone(heading)
        assert heading is not None
        self.assertEqual(heading.text(), "Lora 项目概览")
        self.assertFalse(heading.text().startswith(" "))

    def test_long_formatted_heading_has_no_internal_scroll_range(self) -> None:
        pane = ChatPane()
        pane.resize(360, 420)
        pane.show()
        self.app.processEvents()

        pane.add_message("assistant", "## Review `very-long-development-guide-file-name.md` before release")
        _process_layout_events(self.app)

        heading = pane.findChild(QLabel, "ChatHeadingBlock")
        self.assertIsNotNone(heading)
        assert heading is not None
        self.assertTrue(heading.wordWrap())
        self.assertEqual(heading.textFormat(), Qt.RichText)
        self.assertEqual(heading.property("plain_text"), "Review very-long-development-guide-file-name.md before release")

    def test_unordered_list_uses_marker_widget_instead_of_dash_text(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "- one\n- two")

        markers = [label.text() for label in pane.findChildren(QLabel, "ChatListMarker")]
        item_texts = [_list_item_plain_text(widget) for widget in pane.findChildren(QWidget, "ChatListItemText")]
        self.assertEqual(markers, ["", ""])
        self.assertEqual(item_texts, ["one", "two"])
        self.assertNotIn("- one", _visible_chat_flow(pane))

    def test_ordered_list_renders_bold_inline_markdown(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "1. **阅读和理解代码**\n2. 继续执行")

        item = pane.findChild(QTextEdit, "ChatListItemText")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertIn("font-weight", item.toHtml().lower())
        self.assertEqual(_visible_chat_flow(pane), ["阅读和理解代码\n继续执行"])

    def test_code_block_text_uses_ui_font_family(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "```python\nprint('hi')\n```")

        label = pane.findChild(QLabel, "ChatCodeBlockText")
        self.assertIsNotNone(label)
        assert label is not None
        self.assertFalse(label.font().fixedPitch())
        self.assertEqual(label.font().families()[0], UI_FONT_FAMILIES[0])

    def test_assistant_markdown_renders_code_block_widget_instead_of_rich_text_label(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "# Heading\n\n```python\nprint('hi')\n```")

        self.assertEqual(pane.findChildren(QLabel, "AssistantBubble"), [])
        self.assertEqual(len(pane.findChildren(QWidget, "ChatHeadingBlock")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatCodeBlock")), 1)

    def test_assistant_markdown_renders_table_block(self) -> None:
        pane = ChatPane()

        pane.add_message(
            "assistant",
            "| Name | Value |\n| --- | --- |\n| foo | 1 |\n| bar | 2 |",
        )

        self.assertEqual(len(pane.findChildren(QWidget, "ChatTableBlock")), 1)
        self.assertEqual(
            [label.text() for label in pane.findChildren(QLabel, "ChatTableCell")],
            ["Name", "Value", "foo", "1", "bar", "2"],
        )

    def test_assistant_markdown_renders_inline_markdown_inside_table_cells(self) -> None:
        pane = ChatPane()

        pane.add_message(
            "assistant",
            "| Field | Value |\n"
            "| --- | --- |\n"
            "| **环境** | `output` |",
        )

        rich_cells = pane.findChildren(QTextEdit, "ChatTableCell")
        self.assertEqual([cell.toPlainText() for cell in rich_cells], ["环境", "output"])
        self.assertTrue(any("font-weight" in cell.toHtml().lower() for cell in rich_cells))

    def test_assistant_markdown_renders_indented_list_block(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "  - one\n  - two")

        self.assertEqual(len(pane.findChildren(QWidget, "ChatListBlock")), 1)
        self.assertEqual(
            _visible_chat_flow(pane),
            ["one\ntwo"],
        )

    def test_assistant_markdown_blocks_remain_selectable(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "Paragraph with `code`")

        paragraph = pane.findChild(QTextEdit, "ChatParagraphText")
        self.assertIsNotNone(paragraph)
        assert paragraph is not None
        self.assertEqual(paragraph.toPlainText(), "Paragraph with code")
        self.assertTrue(paragraph.textInteractionFlags() & Qt.TextSelectableByMouse)

    def test_plain_assistant_paragraph_renders_as_single_selectable_label(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "Just a plain paragraph without inline code.")

        paragraph_labels = pane.findChildren(QLabel, "ChatParagraphText")
        self.assertEqual(len(paragraph_labels), 1)
        self.assertEqual(paragraph_labels[0].text(), "Just a plain paragraph without inline code.")
        self.assertTrue(paragraph_labels[0].textInteractionFlags() & Qt.TextSelectableByMouse)

    def test_chinese_paragraph_rows_keep_extra_height_to_avoid_font_clipping(self) -> None:
        pane = ChatPane()
        pane.resize(900, 600)
        pane.show()
        self.app.processEvents()

        pane.add_message("assistant", "正文段落，阅读和理解代码。")
        self.app.processEvents()

        paragraph = pane.findChild(QLabel, "ChatParagraphText")
        self.assertIsNotNone(paragraph)
        assert paragraph is not None
        metrics = QFontMetrics(paragraph.font())
        self.assertGreaterEqual(paragraph.height(), metrics.lineSpacing() + 4)

        pane.add_message("assistant", "正文段落，**阅读和理解代码**。")
        self.app.processEvents()

        rich_paragraph = pane.findChild(QTextEdit, "ChatParagraphText")
        self.assertIsNotNone(rich_paragraph)
        assert rich_paragraph is not None
        rich_height = rich_paragraph.document().documentLayout().documentSize().height()
        rich_chrome = rich_paragraph.contentsMargins().top() + rich_paragraph.contentsMargins().bottom() + (rich_paragraph.frameWidth() * 2)
        self.assertGreaterEqual(rich_paragraph.height(), math.ceil(rich_height + rich_chrome) + 4)

    def test_assistant_bold_markdown_uses_formatted_text_view(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "Paragraph with **bold** markdown.")

        paragraph = pane.findChild(QTextEdit, "ChatParagraphText")
        self.assertIsNotNone(paragraph)
        assert paragraph is not None
        self.assertIn("font-weight", paragraph.toHtml().lower())

    def test_visible_chat_flow_includes_native_non_paragraph_assistant_rows(self) -> None:
        pane = ChatPane()

        pane.add_message("assistant", "# Heading only")
        pane.add_message("assistant", "> Quote only")
        pane.add_message("assistant", "- one\n- two")
        pane.add_message("assistant", "```python\nprint('hi')\n```")

        self.assertEqual(_visible_chat_flow(pane), ["Heading only", "Quote only", "one\ntwo", "print('hi')"])

    def test_visible_chat_rows_includes_user_rows_in_order(self) -> None:
        pane = ChatPane()

        pane.add_message("user", "hello")
        pane.add_message("assistant", "hi")
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: read",
                detail='{"description": "Read files"}',
                tone="accent",
            )
        )

        self.assertEqual(
            _visible_chat_rows(pane),
            [("user", "hello"), ("assistant", "hi"), ("tool", "Read files")],
        )
        self.assertEqual(_activity_detail_flow(pane), ["Executed 1 tool"])

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
        tool_titles = [label.text() for label in pane.findChildren(QLabel, "ToolStatusTitle")]
        self.assertIn("Executed 1 tool", tool_titles)
        self.assertEqual(len(tool_titles), 2)
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
        self.assertIs(composer.parentWidget(), pane.composer_dock)
        self.assertIs(pane.composer_dock.parentWidget(), card)

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
        composer_top = composer.mapTo(pane.main_card, composer.rect().topLeft()).y()
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
        self.assertGreaterEqual(send_button.geometry().right(), composer.width() - 130)
        self.assertGreaterEqual(send_button.geometry().bottom(), composer.height() - 56)

    def test_message_input_does_not_draw_a_second_nested_box(self) -> None:
        stylesheet = theme_stylesheet("day")

        self.assertIn("#ComposerDock {", stylesheet)
        self.assertIn("#Composer {", stylesheet)
        self.assertIn("background: rgba(255,255,255,0.84);", stylesheet)
        self.assertIn("#MessageInput {", stylesheet)
        self.assertIn("border: 0;", stylesheet)

    def test_chat_theme_uses_subtle_user_surface_and_plain_assistant_text(self) -> None:
        stylesheet = theme_stylesheet("day")

        self.assertIn("#UserBubble {", stylesheet)
        self.assertIn("background: rgba(255,255,255,0.72);", stylesheet)
        self.assertIn("#ChatHeadingBlock {", stylesheet)
        self.assertIn('#ChatHeadingBlock[level="2"] {', stylesheet)
        self.assertIn("#ChatParagraphText {", stylesheet)
        self.assertIn("#ChatCodeBlock {", stylesheet)

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
        tool_card = pane.findChild(QWidget, "ActivityGroupSummary")
        self.assertIsNotNone(assistant_body)
        self.assertIsNotNone(tool_card)
        assert assistant_body is not None
        assert tool_card is not None

        bubble_left = assistant_body.mapTo(pane.scroll_area.viewport(), assistant_body.rect().topLeft()).x()
        title = tool_card.findChild(QLabel, "ToolStatusTitle")
        self.assertIsNotNone(title)
        assert title is not None
        tool_left = title.mapTo(pane.scroll_area.viewport(), title.rect().topLeft()).x()
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

        toggle = pane.findChild(QPushButton, "ActivityGroupToggle")
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
        pane.render_history(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_test",
                            "function": {
                                "name": "bash",
                                "arguments": '{"description": "Run diagnostics", "command": "pytest"}',
                            },
                        }
                    ],
                }
            ]
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

        self.assertEqual(pane.messages.spacing(), 0)
        self.assertEqual(assistant_group.layout().spacing(), 2)
        self.assertEqual(tool_group.layout().contentsMargins().left(), 0)
        self.assertEqual(tool_summary.layout().contentsMargins().top(), 1)
        self.assertEqual(tool_summary.layout().contentsMargins().bottom(), 1)

    def test_chat_rows_stack_with_small_visual_gaps_when_content_is_short(self) -> None:
        pane = ChatPane()
        pane.resize(1000, 700)
        pane.show()
        self.app.processEvents()

        pane.add_message("user", "hello")
        pane.add_message("assistant", "hi")
        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run tests"}',
                tone="accent",
            )
        )
        self.app.processEvents()

        rows: list[QWidget] = []
        for index in range(pane.messages.count()):
            item = pane.messages.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                rows.append(widget)

        self.assertEqual([row.objectName() for row in rows], ["UserMessageGroup", "AssistantMessageGroup", "ActivityGroup"])
        gaps = [_content_gap(rows[index], rows[index + 1]) for index in range(len(rows) - 1)]
        self.assertEqual(gaps, [12, 4])

    def test_chat_content_starts_near_top_when_history_is_short(self) -> None:
        pane = ChatPane()
        pane.resize(1000, 700)
        pane.show()
        self.app.processEvents()

        pane.add_message("assistant", "short")
        self.app.processEvents()

        row = pane.findChild(QWidget, "AssistantMessageGroup")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertLessEqual(row.geometry().top(), pane.messages.contentsMargins().top() + 2)

    def test_short_chat_extra_viewport_space_is_not_scrollable_bottom_padding(self) -> None:
        pane = ChatPane()
        pane.resize(1000, 700)
        pane.show()
        self.app.processEvents()

        pane.add_message("assistant", "short")
        self.app.processEvents()

        scrollbar = pane.scroll_area.verticalScrollBar()
        row = pane.findChild(QWidget, "AssistantMessageGroup")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(scrollbar.maximum(), 0)
        self.assertLessEqual(row.geometry().top(), pane.messages.contentsMargins().top() + 2)

    def test_short_chat_keeps_composer_anchored_to_bottom(self) -> None:
        pane = ChatPane()
        pane.resize(1100, 820)
        pane.show()
        self.app.processEvents()

        pane.add_message("user", "hello")
        pane.add_message("assistant", "hi")
        _process_layout_events(self.app)

        composer = pane.findChild(QWidget, "Composer")
        self.assertIsNotNone(composer)
        assert composer is not None

        expected_bottom = pane.rect().bottom()
        self.assertEqual(pane.composer_dock.geometry().bottom(), expected_bottom)
        self.assertLess(composer.geometry().width(), pane.main_card.width())

    def test_chat_scrolls_to_bottom_after_layout_updates_for_long_transcripts(self) -> None:
        pane = ChatPane()
        pane.resize(420, 220)
        pane.show()
        self.app.processEvents()

        for index in range(20):
            pane.add_message("user", f"message {index}")
        _process_layout_events(self.app)

        scrollbar = pane.scroll_area.verticalScrollBar()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())

        rows = _transcript_widgets(pane)
        self.assertTrue(rows)
        last_bottom = rows[-1].mapTo(pane.scroll_area.viewport(), rows[-1].rect().bottomLeft()).y()
        bottom_gap = pane.scroll_area.viewport().height() - 1 - last_bottom
        self.assertLessEqual(bottom_gap, pane.messages.contentsMargins().bottom())

    def test_expanded_tool_panel_is_reused_while_runtime_updates_arrive(self) -> None:
        pane = ChatPane()
        pane.show()
        self.app.processEvents()

        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run diagnostics", "command": "pytest"}',
                tone="accent",
                metadata={"tool_call_id": "call_test"},
            )
        )
        activity_toggle = pane.findChild(QPushButton, "ActivityGroupToggle")
        self.assertIsNotNone(activity_toggle)
        assert activity_toggle is not None
        activity_toggle.click()

        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        toggle.click()
        self.app.processEvents()

        original_panel = pane.findChild(QWidget, "ToolCompactPanel")
        self.assertIsNotNone(original_panel)

        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool result: call_test success",
                detail="done",
                tone="ok",
                metadata={"tool_call_id": "call_test"},
            )
        )
        self.app.processEvents()

        self.assertIs(pane.findChild(QWidget, "ToolCompactPanel"), original_panel)

    def test_reexpanded_tool_detail_rows_are_updated_in_place(self) -> None:
        pane = ChatPane()
        pane.show()
        self.app.processEvents()

        pane.add_runtime_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"description": "Run diagnostics", "command": "pytest"}',
                tone="accent",
                metadata={"tool_call_id": "call_test"},
            )
        )
        activity_toggle = pane.findChild(QPushButton, "ActivityGroupToggle")
        self.assertIsNotNone(activity_toggle)
        assert activity_toggle is not None
        activity_toggle.click()

        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        toggle.click()
        self.app.processEvents()

        original_rows = pane.findChildren(QWidget, "ToolCompactDetailRow")
        self.assertEqual(len(original_rows), 1)

        toggle.click()
        toggle.click()
        self.app.processEvents()

        refreshed_rows = pane.findChildren(QWidget, "ToolCompactDetailRow")
        self.assertEqual(len(refreshed_rows), 1)
        self.assertIs(refreshed_rows[0], original_rows[0])
        for row in refreshed_rows:
            self.assertFalse(row.isWindow())
            self.assertIsNotNone(row.parentWidget())

    def test_expanding_tools_does_not_create_extra_top_level_windows(self) -> None:
        pane = ChatPane()
        pane.show()
        self.app.processEvents()

        pane.render_history(
            [
                {"role": "assistant", "content": "I will inspect this."},
                {
                    "role": "assistant",
                    "content": "",
                    "payload": {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "read",
                                    "arguments": '{"description": "Read project files", "path": "README.md"}',
                                },
                            },
                            {
                                "id": "c2",
                                "function": {
                                    "name": "bash",
                                    "arguments": '{"description": "Run unit tests", "command": "pytest"}',
                                },
                            },
                        ]
                    },
                },
                {"role": "tool", "content": '{"status": "success"}', "payload": {"tool_call_id": "c1"}},
                {"role": "tool", "content": '{"status": "success"}', "payload": {"tool_call_id": "c2"}},
            ]
        )
        pane.resize(900, 600)
        self.app.processEvents()

        before = {id(widget) for widget in QApplication.topLevelWidgets()}
        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        toggle.click()
        self.app.processEvents()

        after = QApplication.topLevelWidgets()
        self.assertEqual(before, {id(widget) for widget in after})

    def test_expanded_tool_rows_keep_tight_internal_spacing_with_short_transcript(self) -> None:
        pane = ChatPane()
        pane.show()
        self.app.processEvents()

        pane.render_history(
            [
                {"role": "assistant", "content": "I will inspect this."},
                {
                    "role": "assistant",
                    "content": "",
                    "payload": {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "read",
                                    "arguments": '{"description": "Read project files", "path": "README.md"}',
                                },
                            },
                            {
                                "id": "c2",
                                "function": {
                                    "name": "bash",
                                    "arguments": '{"description": "Run unit tests", "command": "pytest"}',
                                },
                            },
                            {
                                "id": "c3",
                                "function": {
                                    "name": "bash",
                                    "arguments": '{"description": "Check failing command", "command": "exit 1"}',
                                },
                            },
                        ]
                    },
                },
                {"role": "tool", "content": '{"status": "success"}', "payload": {"tool_call_id": "c1"}},
                {"role": "tool", "content": '{"status": "success"}', "payload": {"tool_call_id": "c2"}},
                {"role": "tool", "content": '{"status": "error"}', "payload": {"tool_call_id": "c3"}},
            ]
        )
        pane.resize(900, 600)
        toggle = pane.findChild(QPushButton, "ToolStatusToggle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        toggle.click()
        self.app.processEvents()

        group = pane.findChild(QWidget, "ToolStatusGroup")
        self.assertIsNotNone(group)
        assert group is not None
        self.assertLessEqual(group.height(), group.sizeHint().height() + 4)

        rows = pane.findChildren(QFrame, "ToolCallRow")
        self.assertEqual(len(rows), 3)
        for row in rows:
            line = row.findChild(QLabel, "ToolCallLine")
            status = row.findChild(QLabel, "ToolCallStatus")
            self.assertIsNotNone(line)
            self.assertIsNotNone(status)
            assert line is not None
            assert status is not None
            gap = status.y() - (line.y() + line.height())
            self.assertLessEqual(gap, 6)
            self.assertLessEqual(row.height(), row.sizeHint().height() + 4)
            self.assertLessEqual(line.height(), line.sizeHint().height() + 4)


if __name__ == "__main__":
    unittest.main()


def _visible_chat_flow(pane: ChatPane) -> list[str]:
    return [text for _, text in _visible_chat_rows(pane)]


def _visible_chat_rows(pane: ChatPane) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for index in range(pane.messages.count()):
        item = pane.messages.itemAt(index)
        widget = item.widget() if item is not None else None
        if widget is None:
            continue
        row = _message_widget_row(widget)
        if row is not None:
            rows.append(row)
    return rows


def _activity_detail_flow(pane: ChatPane) -> list[str]:
    values: list[str] = []
    for activity in pane.findChildren(QWidget, "ActivityGroup"):
        detail = activity.findChild(QWidget, "ActivityGroupDetail")
        layout = detail.layout() if detail is not None else None
        if layout is None:
            continue
        for index in range(layout.count()):
            item = layout.itemAt(index)
            child = item.widget() if item is not None else None
            row = _message_widget_row(child) if child is not None else None
            if row is not None:
                values.append(row[1])
    return values


def _transcript_widgets(pane: ChatPane) -> list[QWidget]:
    rows: list[QWidget] = []
    for index in range(pane.messages.count()):
        item = pane.messages.itemAt(index)
        widget = item.widget() if item is not None else None
        if widget is not None:
            rows.append(widget)
    return rows


def _process_layout_events(app: QApplication, count: int = 5) -> None:
    for _ in range(count):
        app.processEvents()


def _message_widget_texts(widget: QWidget) -> list[str]:
    row = _message_widget_row(widget)
    return [row[1]] if row is not None else []


def _activity_footer_text(widget: QWidget) -> str:
    footer = widget.findChild(QWidget, "ActivityGroupFooter")
    layout = footer.layout() if footer is not None else None
    if layout is None:
        return ""
    for index in range(layout.count()):
        item = layout.itemAt(index)
        child = item.widget() if item is not None else None
        if isinstance(child, AssistantMessageRow):
            texts = _assistant_row_texts(child)
            if texts:
                return texts[0]
    return ""


def _activity_group_title(widget: QWidget) -> str:
    summary = widget.findChild(QWidget, "ActivityGroupSummary")
    title = summary.findChild(QLabel, "ToolStatusTitle") if summary is not None else None
    return title.text() if title is not None else "Acting"


def _message_widget_row(widget: QWidget) -> tuple[str, str] | None:
    if widget.objectName() == "ActivityGroup":
        footer_text = _activity_footer_text(widget)
        if footer_text:
            return ("assistant", footer_text)
        return ("tool", _activity_group_title(widget))
    if (thinking := widget.findChild(QLabel, "ThinkingStatusTitle")) is not None:
        return ("thinking", thinking.text())
    if (tool := widget.findChild(QLabel, "ToolStatusTitle")) is not None:
        return ("tool", tool.text())
    if (bubble := widget.findChild(QLabel, "UserBubbleText")) is not None:
        return ("user", bubble.text())
    if (bubble := widget.findChild(QLabel, "AssistantBubble")) is not None:
        return ("assistant", bubble.text())
    texts = _assistant_row_texts(widget)
    if not texts:
        return None
    return ("assistant", texts[0])


def _assistant_row_texts(widget: QWidget) -> list[str]:
    body = _first_assistant_body(widget)
    if body is None or body.layout() is None:
        return []

    block_texts: list[str] = []
    for index in range(body.layout().count()):
        item = body.layout().itemAt(index)
        block = item.widget() if item is not None else None
        if block is None:
            continue
        block_text = _assistant_block_text(block)
        if block_text:
            block_texts.append(block_text)
    return ["\n\n".join(block_texts)] if block_texts else []


def _assistant_block_text(block: QWidget) -> str:
    if isinstance(block, QLabel) and block.objectName() == "ChatHeadingBlock":
        plain_text = block.property("plain_text")
        return str(plain_text) if plain_text else block.text()
    if block.objectName() == "ChatParagraphBlock":
        plain_text = block.property("plain_text")
        return str(plain_text) if plain_text else ""
    if block.objectName() == "ChatQuoteBlock":
        label = block.findChild(QLabel)
        return label.text() if label is not None else ""
    if block.objectName() == "ChatListBlock":
        items: list[str] = []
        for row in block.findChildren(QWidget, "ChatListItem"):
            text_widget = row.findChild(QWidget, "ChatListItemText")
            if text_widget is not None:
                items.append(_list_item_plain_text(text_widget))
        return "\n".join(items)
    if block.objectName() == "ChatCodeBlock":
        label = block.findChild(QLabel, "ChatCodeBlockText")
        return label.text() if label is not None else ""
    if block.objectName() == "ChatTableBlock":
        values = [label.text() for label in block.findChildren(QLabel, "ChatTableCell")]
        return "\n".join(values)
    return ""


def _list_item_plain_text(widget: QWidget) -> str:
    plain_text = widget.property("plain_text")
    if plain_text:
        return str(plain_text)
    if isinstance(widget, QLabel):
        return widget.text()
    if isinstance(widget, QTextEdit):
        return widget.toPlainText()
    return ""


def _first_assistant_body(widget: QWidget) -> QWidget | None:
    return widget.findChild(QWidget, "DocumentBlockList")


def _content_gap(upper: QWidget, lower: QWidget) -> int:
    upper_bottom = upper.geometry().bottom()
    lower_top = lower.geometry().top()
    upper_layout = upper.layout()
    lower_layout = lower.layout()
    upper_margin = upper_layout.contentsMargins().bottom() if upper_layout is not None else 0
    lower_margin = lower_layout.contentsMargins().top() if lower_layout is not None else 0
    return lower_top - upper_bottom - 1 + upper_margin + lower_margin
