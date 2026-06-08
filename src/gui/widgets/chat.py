from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

import shiboken6
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFontMetrics, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.icons import icon
from gui.workers import InspectorEvent
from gui.widgets.chat_markdown import parse_markdown_blocks
from gui.widgets.chat_rows import AssistantMessageRow, ThinkingRow, UserMessageRow


class ChatPane(QWidget):
    message_submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ChatPane")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._assistant_row: AssistantMessageRow | None = None
        self._assistant_text = ""
        self._thinking_widget: ThinkingRow | None = None
        self._active_tool_group: _ToolGroupWidget | None = None
        self._activity_group: _ActivityGroupWidget | None = None
        self._assistant_rendered_text = ""
        self._assistant_render_pending = False
        self._stick_to_bottom = True
        self._scrollbar_update_in_progress = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.main_card = QFrame()
        self.main_card.setObjectName("ChatMainCard")
        self.main_card.setAttribute(Qt.WA_StyledBackground, True)
        card_layout = QVBoxLayout(self.main_card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        self._card_layout = card_layout
        root.addWidget(self.main_card)

        header_shell = QFrame()
        header_shell.setObjectName("ChatHeaderShell")
        header_shell.setAttribute(Qt.WA_StyledBackground, True)
        header_shell.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.header_shell = header_shell
        header_shell_layout = QVBoxLayout(header_shell)
        header_shell_layout.setContentsMargins(0, 0, 0, 0)
        header_shell_layout.setSpacing(0)
        header_frame = QFrame()
        header_frame.setObjectName("ChatHeader")
        header_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(24, 14, 24, 12)
        header_layout.setSpacing(12)
        header_text = QVBoxLayout()
        header_text.setContentsMargins(0, 0, 0, 0)
        header_text.setSpacing(3)
        self.header = QLabel("Select or create a chat session")
        self.header.setObjectName("ChatHeaderTitle")
        self.header_meta = QLabel("Execution canvas")
        self.header_meta.setObjectName("ChatHeaderMeta")
        self.run_status = QLabel("Ready")
        self.run_status.setObjectName("ChatRunStatus")
        self.run_status.setProperty("status", "ready")
        self.run_status.setAlignment(Qt.AlignCenter)
        self.run_status.setFixedWidth(86)
        header_text.addWidget(self.header)
        header_text.addWidget(self.header_meta)
        header_layout.addLayout(header_text, 1)
        header_layout.addWidget(self.run_status)
        header_shell_layout.addWidget(header_frame)
        card_layout.addWidget(header_shell)

        scroll_shell = QFrame()
        scroll_shell.setObjectName("ChatScrollShell")
        scroll_shell.setAttribute(Qt.WA_StyledBackground, True)
        self.scroll_shell = scroll_shell
        scroll_shell_layout = QVBoxLayout(scroll_shell)
        scroll_shell_layout.setContentsMargins(0, 0, 0, 0)
        scroll_shell_layout.setSpacing(0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("ChatScroll")
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.verticalScrollBar().rangeChanged.connect(self._handle_scroll_range_changed)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._handle_scroll_value_changed)
        self.scroll_body = QWidget()
        self.scroll_body.setObjectName("ChatScrollBody")
        self.scroll_body.setAttribute(Qt.WA_StyledBackground, True)
        self.scroll_body.setMinimumWidth(0)
        self.messages = QVBoxLayout(self.scroll_body)
        self.messages.setContentsMargins(34, 7, 34, 12)
        self.messages.setSpacing(0)
        self.messages.setAlignment(Qt.AlignTop)
        self.scroll_area.setWidget(self.scroll_body)
        scroll_shell_layout.addWidget(self.scroll_area)
        card_layout.addWidget(scroll_shell, 1)

        composer = QFrame()
        composer.setObjectName("Composer")
        composer.setAttribute(Qt.WA_StyledBackground, True)
        composer.setFixedHeight(152)
        composer_layout = QHBoxLayout(composer)
        composer_layout.setContentsMargins(20, 18, 20, 18)
        composer_layout.setSpacing(18)
        self.input = _MessageInput()
        self.input.setPlaceholderText("Message Lora...")
        self.input.setObjectName("MessageInput")
        composer_layout.addWidget(self.input, 1)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.setIcon(icon("send"))
        self.send_button.setToolTip("Send message")
        self.send_button.setFixedSize(132, 38)
        composer_layout.addWidget(self.send_button, 0, Qt.AlignBottom)
        self.send_button.clicked.connect(self._submit)
        self.input.submit_requested.connect(self._submit)
        self.composer = composer
        card_layout.addWidget(composer, 0)

        self._assistant_render_timer = QTimer(self)
        self._assistant_render_timer.setSingleShot(True)
        self._assistant_render_timer.setInterval(33)
        self._assistant_render_timer.timeout.connect(self._flush_assistant_render)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        super().resizeEvent(event)
        self._update_bubble_widths()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override.
        super().showEvent(event)
        self._sync_transcript_width_after_layout()
        QTimer.singleShot(0, self._sync_transcript_width_after_layout)

    def set_session_title(self, title: str) -> None:
        metrics = QFontMetrics(self.header.font())
        self.header.setText(metrics.elidedText(title, Qt.ElideRight, 520))
        self.header.setToolTip(title)
        self.header_meta.setText(_short_session_meta(title))

    def set_session_context(self, title: str, *, agent: str | None = None, model: str | None = None) -> None:
        metrics = QFontMetrics(self.header.font())
        self.header.setText(metrics.elidedText(title, Qt.ElideRight, 520))
        self.header.setToolTip(title)
        chips = [value for value in (agent, model) if value]
        self.header_meta.setText(" / ".join(chips) if chips else _short_session_meta(title))

    def set_run_status(self, status: str) -> None:
        normalized = _status_key(status)
        self.run_status.setText(_status_label(normalized))
        self.run_status.setProperty("status", normalized)
        _refresh_widget(self.run_status)

    def set_running(self, running: bool) -> None:
        self.input.setEnabled(not running)
        self.send_button.setEnabled(not running)
        self.send_button.setText("Running" if running else "Send")
        self.send_button.setIcon(icon("running" if running else "send"))
        self.set_run_status("running" if running else "ready")

    def render_history(self, history: list[dict[str, object]]) -> None:
        self.clear()
        index = 0
        while index < len(history):
            message = history[index]
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")
            if role == "user":
                self.add_message("user", _clean_user_content(content))
                turn_messages: list[dict[str, object]] = []
                index += 1
                while index < len(history) and str(history[index].get("role") or "") != "user":
                    turn_messages.append(history[index])
                    index += 1
                self._render_turn_segment(turn_messages)
                continue
            elif role == "assistant":
                tool_calls = _tool_calls(message)
                if content.strip():
                    self.add_message("assistant", content)
                if tool_calls:
                    self._append_tool_calls(tool_calls)
            elif role == "tool":
                self._apply_tool_result(_tool_result_state(message))
            index += 1

    def clear(self) -> None:
        while self.messages.count():
            item = self.messages.takeAt(0)
            _delete_layout_item(item)
        self._assistant_row = None
        self._assistant_text = ""
        self._assistant_rendered_text = ""
        self._assistant_render_pending = False
        if hasattr(self, "_assistant_render_timer"):
            self._assistant_render_timer.stop()
        self._thinking_widget = None
        self._break_tool_group()
        self._activity_group = None

    def add_message(self, role: str, content: str) -> None:
        self._remove_thinking_status()
        self._break_tool_group()
        self._break_activity_group()
        if role == "assistant":
            row = AssistantMessageRow()
            row.render_blocks(parse_markdown_blocks(content))
            self._insert_message_widget(row)
            self._update_bubble_widths()
            self._scroll_to_bottom()
            return
        row = UserMessageRow(content)
        self._insert_message_widget(row)
        self._update_bubble_widths()
        self._scroll_to_bottom()

    def start_assistant_message(self) -> None:
        self._break_tool_group()
        self._break_activity_group()
        self._assistant_text = ""
        self._assistant_rendered_text = ""
        self._assistant_render_pending = False
        self._assistant_render_timer.stop()
        self._remove_thinking_status()
        self._thinking_widget = ThinkingRow("Thinking")
        activity = self._ensure_activity_group()
        activity.add_row(self._thinking_widget)
        self._scroll_to_bottom()

    def _create_assistant_message_row(self) -> None:
        row = AssistantMessageRow()
        row.hide()
        row.set_viewport_width(self._maximum_bubble_width())
        self._assistant_row = row
        self._insert_message_widget(row)
        self._break_tool_group()

    def append_assistant_delta(self, delta: str) -> None:
        if self._assistant_row is None:
            if self._thinking_widget is None and self._activity_group is None:
                self.start_assistant_message()
            if self._active_tool_group is None:
                self._remove_thinking_status()
                self._remove_empty_activity_group()
            self._create_assistant_message_row()
        self._assistant_text += delta
        assert self._assistant_row is not None
        self._assistant_row.show()
        self._break_tool_group()
        if not self._assistant_rendered_text:
            self._flush_assistant_render()
            return
        self._schedule_assistant_render()

    def finish_assistant_message(self, final_answer: str | None = None) -> None:
        if final_answer:
            updated_row = False
            if self._assistant_row is None:
                if self._thinking_widget is not None and self._active_tool_group is None:
                    self._remove_thinking_status()
                self._break_activity_group()
                self._create_assistant_message_row()
            if final_answer != self._assistant_text and self._assistant_row is not None:
                self._assistant_text = final_answer
                self._assistant_row.show()
                self._flush_assistant_render()
                updated_row = True
            elif self._assistant_render_pending:
                self._flush_assistant_render()
                updated_row = True
            if updated_row:
                QTimer.singleShot(0, self._scroll_to_bottom)
        else:
            self._flush_assistant_render()
        self._assistant_row = None
        self._assistant_text = ""
        self._assistant_rendered_text = ""
        self._assistant_render_pending = False
        self._assistant_render_timer.stop()

    def show_error(self, message: str) -> None:
        self.set_run_status("error")
        self.add_message("assistant", f"Error: {message}")

    def add_runtime_event(self, event: InspectorEvent) -> None:
        if event.kind != "tool":
            return
        if event.title.startswith("Tool call:"):
            self._move_active_assistant_to_activity()
            self._close_assistant_stream()
            self._ensure_activity_group()
            self._append_tool_calls([_tool_call_from_event(event)])
        elif event.title.startswith("Tool result:"):
            self._apply_tool_result(_tool_result_state_from_event(event))
        else:
            self._close_assistant_stream()
            self._append_tool_calls([_ToolCallState(id="", name=event.title, description=event.title, arguments=event.detail)])
        self._scroll_to_bottom()

    def _submit(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.message_submitted.emit(text)

    def _has_transcript_widgets(self) -> bool:
        return self._last_transcript_widget() is not None

    def _schedule_transcript_layout_update(self) -> None:
        self.scroll_body.updateGeometry()

    def _schedule_assistant_render(self) -> None:
        self._assistant_render_pending = True
        if not self._assistant_render_timer.isActive():
            self._assistant_render_timer.start()

    def _flush_assistant_render(self) -> None:
        self._assistant_render_timer.stop()
        self._assistant_render_pending = False
        if self._assistant_row is None:
            return
        text = self._assistant_text or " "
        if text == self._assistant_rendered_text:
            return
        self._assistant_row.render_blocks(parse_markdown_blocks(text), preserve_height=True)
        self._assistant_rendered_text = text
        self._update_bubble_widths()
        self._scroll_to_bottom()

    def _sync_transcript_width_after_layout(self) -> None:
        if not shiboken6.isValid(self):
            return
        self._update_bubble_widths()
        self.scroll_body.updateGeometry()

    def _scroll_to_bottom(self) -> None:
        self._stick_to_bottom = True
        self._schedule_transcript_layout_update()
        self._scroll_to_bottom_now()
        QTimer.singleShot(0, self._scroll_to_bottom_now)

    def _scroll_to_bottom_now(self) -> None:
        if not shiboken6.isValid(self) or not hasattr(self, "scroll_area") or not shiboken6.isValid(self.scroll_area):
            return
        bar = self.scroll_area.verticalScrollBar()
        self._scrollbar_update_in_progress = True
        try:
            bar.setValue(bar.maximum())
        finally:
            self._scrollbar_update_in_progress = False

    def _handle_scroll_range_changed(self, _minimum: int, _maximum: int) -> None:
        if not self._stick_to_bottom:
            return
        self._scroll_to_bottom_now()
        QTimer.singleShot(0, self._scroll_to_bottom_now)

    def _handle_scroll_value_changed(self, value: int) -> None:
        if self._scrollbar_update_in_progress:
            return
        bar = self.scroll_area.verticalScrollBar()
        self._stick_to_bottom = value >= bar.maximum()

    def _maximum_bubble_width(self) -> int:
        viewport_width = self.scroll_area.viewport().width()
        return max(1, viewport_width - 64)

    def _update_bubble_widths(self) -> None:
        maximum_width = self._maximum_bubble_width()
        for bubble in self.findChildren(QLabel):
            if bubble.objectName() != "UserBubble":
                continue
            bubble_limit = int(maximum_width * 0.72)
            bubble_width = min(_natural_label_width(bubble), bubble_limit)
            bubble.setFixedWidth(bubble_width)
            bubble.setMaximumWidth(bubble_limit)
            bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
        viewport_width = self._maximum_bubble_width()
        for row in self.findChildren(AssistantMessageRow):
            row.set_viewport_width(viewport_width)

    def _remove_thinking_status(self) -> None:
        if self._thinking_widget is None:
            return
        self._remove_widget_from_messages(self._thinking_widget)
        _destroy_widget(self._thinking_widget)
        self._thinking_widget = None
        self._remove_empty_activity_group()

    def _remove_widget_from_messages(self, widget: QWidget) -> None:
        for index in range(self.messages.count()):
            item = self.messages.itemAt(index)
            if item is not None and item.widget() is widget:
                if index > 0:
                    previous = self.messages.itemAt(index - 1)
                    if previous is not None and previous.spacerItem() is not None:
                        self.messages.takeAt(index - 1)
                        index -= 1
                self.messages.takeAt(index)
                return
        if self._activity_group is not None:
            self._activity_group.remove_row(widget)

    def _close_assistant_stream(self) -> None:
        # Intentionally preserve the current tool group so runtime tool call/result
        # rows stay in arrival order; assistant prose owns the group boundary.
        self._flush_assistant_render()
        self._assistant_row = None
        self._assistant_text = ""
        self._assistant_rendered_text = ""
        self._assistant_render_pending = False
        self._assistant_render_timer.stop()

    def _break_tool_group(self) -> None:
        self._active_tool_group = None

    def _break_activity_group(self) -> None:
        self._activity_group = None

    def _message_insert_index(self) -> int:
        return self.messages.count()

    def _insert_message_widget(self, widget: QWidget, *, before: QWidget | None = None) -> None:
        if before is not None:
            for index in range(self.messages.count()):
                item = self.messages.itemAt(index)
                if item is not None and item.widget() is before:
                    self.messages.insertWidget(index, widget)
                    self.messages.insertSpacing(index + 1, self._spacing_before(widget, before))
                    return
        previous = self._last_transcript_widget()
        if previous is not None:
            self.messages.addSpacing(self._spacing_before(previous, widget))
        self.messages.insertWidget(self._message_insert_index(), widget)

    def _last_transcript_widget(self) -> QWidget | None:
        for index in range(self.messages.count() - 1, -1, -1):
            item = self.messages.itemAt(index)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                return widget
        return None

    def _spacing_before(self, previous: QWidget, current: QWidget) -> int:
        previous_name = previous.objectName()
        current_name = current.objectName()
        if previous_name == "UserMessageGroup" or current_name == "UserMessageGroup":
            return 12
        return 4

    def _append_tool_calls(self, tool_calls: list[dict[str, object] | "_ToolCallState"]) -> None:
        if self._active_tool_group is None:
            self._active_tool_group = _ToolGroupWidget()
            if self._activity_group is not None:
                self._activity_group.add_row(self._active_tool_group)
            else:
                self._insert_message_widget(self._active_tool_group)
        for tool_call in tool_calls:
            if isinstance(tool_call, _ToolCallState):
                state = tool_call
            else:
                state = _tool_call_state(tool_call)
            self._active_tool_group.add_call(state)
        self._scroll_to_bottom()

    def _apply_tool_result(self, result: "_ToolResultState") -> None:
        if self._active_tool_group is None:
            return
        self._active_tool_group.apply_result(result)
        self._scroll_to_bottom()

    def _render_turn_segment(self, messages: list[dict[str, object]]) -> None:
        final_assistant_index = -1
        for index, message in enumerate(messages):
            if str(message.get("role") or "") == "assistant" and str(message.get("content") or "").strip():
                final_assistant_index = index
        for index, message in enumerate(messages):
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")
            if role == "assistant":
                tool_calls = _tool_calls(message)
                if index == final_assistant_index and content.strip():
                    self._activity_group = None
                    self._break_tool_group()
                    self.add_message("assistant", content)
                    if tool_calls:
                        self._append_tool_calls(tool_calls)
                    continue
                if content.strip():
                    self._active_tool_group = None
                    row = AssistantMessageRow()
                    row.render_blocks(parse_markdown_blocks(content))
                    self._ensure_activity_group().add_row(row)
                if tool_calls:
                    self._ensure_activity_group()
                    self._append_tool_calls(tool_calls)
            elif role == "tool":
                self._apply_tool_result(_tool_result_state(message))
        self._activity_group = None
        self._break_tool_group()
        self._update_bubble_widths()
        self._scroll_to_bottom()

    def _ensure_activity_group(self) -> "_ActivityGroupWidget":
        if self._activity_group is None:
            self._activity_group = _ActivityGroupWidget()
            self._insert_message_widget(self._activity_group, before=self._assistant_row)
        return self._activity_group

    def _move_active_assistant_to_activity(self) -> None:
        if self._assistant_row is None or not self._assistant_text.strip():
            return
        row = self._assistant_row
        self._remove_widget_from_messages(row)
        self._ensure_activity_group().add_row(row)

    def _remove_empty_activity_group(self) -> None:
        if self._activity_group is None or not self._activity_group.is_empty():
            return
        activity = self._activity_group
        self._activity_group = None
        self._remove_widget_from_messages(activity)
        _destroy_widget(activity)


class _MessageInput(QPlainTextEdit):
    submit_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override.
        key = event.key()
        modifiers = event.modifiers()
        submit_key = key in {Qt.Key_Return, Qt.Key_Enter}
        has_text_modifier = bool(
            modifiers
            & (
                Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
        )
        if submit_key and not has_text_modifier:
            self.submit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _message_avatar(role: str) -> QLabel:
    avatar = QLabel("U" if role == "user" else "L")
    avatar.setAlignment(Qt.AlignCenter)
    avatar.setObjectName("UserAvatar" if role == "user" else "AssistantAvatar")
    avatar.setFixedSize(36, 36)
    return avatar


@dataclass(slots=True)
class _ToolCallState:
    id: str
    name: str
    description: str
    arguments: str
    result: str = ""
    status: str = "running"
    started_at_text: str = "--:--:--"
    finished_at_text: str = ""
    started_monotonic: float | None = None
    finished_monotonic: float | None = None


@dataclass(frozen=True, slots=True)
class _ToolResultState:
    id: str
    result: str
    status: str
    finished_at_text: str = ""
    finished_monotonic: float | None = None


class _ActivityGroupWidget(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ActivityGroup")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._expanded = False
        self._row_widgets: list[QWidget] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(38, 0, 0, 0)
        layout.setSpacing(2)

        self.summary = QFrame()
        self.summary.setObjectName("ActivityGroupSummary")
        summary_layout = QHBoxLayout(self.summary)
        summary_layout.setContentsMargins(2, 1, 2, 1)
        summary_layout.setSpacing(1)
        self.title = QLabel("Activity")
        self.title.setObjectName("ToolStatusTitle")
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.toggle = QPushButton(">")
        self.toggle.setObjectName("ActivityGroupToggle")
        self.toggle.setFixedSize(16, 16)
        self.toggle.clicked.connect(self._toggle_detail)
        summary_layout.addWidget(self.title, 0)
        summary_layout.addWidget(self.toggle, 0)
        summary_layout.addStretch(1)
        layout.addWidget(self.summary)

        self.detail = QWidget()
        self.detail.setObjectName("ActivityGroupDetail")
        self.detail.setVisible(False)
        self.detail_layout = QVBoxLayout(self.detail)
        self.detail_layout.setContentsMargins(0, 2, 0, 0)
        self.detail_layout.setSpacing(4)
        layout.addWidget(self.detail)

    def add_row(self, widget: QWidget) -> None:
        if widget in self._row_widgets:
            return
        self._row_widgets.append(widget)
        self.detail_layout.addWidget(widget)
        widget.setVisible(self._expanded)
        self._refresh_title()
        self.updateGeometry()

    def is_empty(self) -> bool:
        return not self._row_widgets

    def remove_row(self, widget: QWidget) -> None:
        if widget not in self._row_widgets:
            return
        self._row_widgets.remove(widget)
        for index in range(self.detail_layout.count()):
            item = self.detail_layout.itemAt(index)
            if item is not None and item.widget() is widget:
                self.detail_layout.takeAt(index)
                break
        self._refresh_title()
        self.updateGeometry()

    def _toggle_detail(self) -> None:
        self._expanded = not self._expanded
        self.detail.setVisible(self._expanded)
        for widget in self._row_widgets:
            widget.setVisible(self._expanded)
        self.toggle.setText("v" if self._expanded else ">")
        self.updateGeometry()

    def _refresh_title(self) -> None:
        self.title.setText("Activity")


class _ToolGroupWidget(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ToolStatusGroup")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._calls: list[_ToolCallState] = []
        self._expanded = False
        self._blink_phase = False
        self._detail_panel: QWidget | None = None
        self._detail_rows_layout: QVBoxLayout | None = None
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(520)
        self._blink_timer.timeout.connect(self._advance_blink_phase)

        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(38, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("ToolStatusRow")
        self.card.setProperty("status", "running")
        self.card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        outer_layout.addWidget(self.card, 1)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.summary_frame = QFrame()
        self.summary_frame.setObjectName("ToolStatusSummary")
        self.summary_frame.setProperty("status", "running")
        layout.addWidget(self.summary_frame)

        summary = QHBoxLayout(self.summary_frame)
        summary.setContentsMargins(2, 1, 2, 1)
        summary.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(1)
        self.toggle = QPushButton("+")
        self.toggle.setObjectName("ToolStatusToggle")
        self.toggle.setFixedSize(16, 16)
        self.toggle.clicked.connect(self._toggle_detail)
        self.title = QLabel("")
        self.title.setObjectName("ToolStatusTitle")
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.title.setWordWrap(True)
        title_row.addWidget(self.title, 0)
        title_row.addWidget(self.toggle, 0)
        summary.addLayout(title_row, 0)
        summary.setAlignment(title_row, Qt.AlignLeft | Qt.AlignVCenter)

        self.detail_holder = QVBoxLayout()
        self.detail_holder.setContentsMargins(0, 1, 0, 0)
        self.detail_holder.setSpacing(1)
        layout.addLayout(self.detail_holder)

    def add_call(self, call: _ToolCallState) -> None:
        self._calls.append(call)
        self._refresh()

    def apply_result(self, result: _ToolResultState) -> None:
        target = self._find_call(result.id)
        if target is None:
            target = self._find_last_running_call()
        if target is None:
            target = _ToolCallState(id=result.id, name=result.id or "tool", description=result.id or "tool", arguments="")
            self._calls.append(target)
        target.result = result.result
        target.status = "error" if result.status == "error" else "success"
        target.finished_at_text = result.finished_at_text
        target.finished_monotonic = result.finished_monotonic
        self._refresh()

    def _find_call(self, call_id: str) -> _ToolCallState | None:
        if not call_id:
            return None
        for call in self._calls:
            if call.id == call_id:
                return call
        return None

    def _find_last_running_call(self) -> _ToolCallState | None:
        for call in reversed(self._calls):
            if call.status == "running":
                return call
        return None

    def _toggle_detail(self) -> None:
        self._expanded = not self._expanded
        self._refresh()

    def _advance_blink_phase(self) -> None:
        self._blink_phase = not self._blink_phase
        self.card.setProperty("blinkPhase", "alt" if self._blink_phase else "base")
        self.summary_frame.setProperty("blinkPhase", "alt" if self._blink_phase else "base")
        self.title.setProperty("blinkPhase", "alt" if self._blink_phase else "base")
        self.toggle.setProperty("blinkPhase", "alt" if self._blink_phase else "base")
        _refresh_widget(self.card)
        _refresh_widget(self.summary_frame)
        _refresh_widget(self.title)
        _refresh_widget(self.toggle)

    def _set_blinking(self, enabled: bool) -> None:
        self.card.setProperty("blink", enabled)
        self.summary_frame.setProperty("blink", enabled)
        self.title.setProperty("blink", enabled)
        self.toggle.setProperty("blink", enabled)
        if enabled:
            self._blink_phase = False
            self.card.setProperty("blinkPhase", "base")
            self.summary_frame.setProperty("blinkPhase", "base")
            self.title.setProperty("blinkPhase", "base")
            self.toggle.setProperty("blinkPhase", "base")
            if not self._blink_timer.isActive():
                self._blink_timer.start()
        else:
            if self._blink_timer.isActive():
                self._blink_timer.stop()
            self._blink_phase = False
            self.card.setProperty("blinkPhase", "steady")
            self.summary_frame.setProperty("blinkPhase", "steady")
            self.title.setProperty("blinkPhase", "steady")
            self.toggle.setProperty("blinkPhase", "steady")
        _refresh_widget(self.card)
        _refresh_widget(self.summary_frame)
        _refresh_widget(self.title)
        _refresh_widget(self.toggle)

    def _refresh(self) -> None:
        if not self._calls:
            self.title.setText("")
            self.toggle.setEnabled(False)
            return
        self.title.setText(self._calls[-1].description or self._calls[-1].name)
        status = _group_status(self._calls)
        self.card.setProperty("status", status)
        self.card.setProperty("expanded", self._expanded)
        self.summary_frame.setProperty("status", status)
        self.title.setProperty("status", status)
        self.toggle.setProperty("status", status)
        self._set_blinking(status == "running")
        _refresh_widget(self.card)
        _refresh_widget(self.summary_frame)
        self.toggle.setEnabled(True)
        self.toggle.setText("v" if self._expanded else ">")
        if self._detail_panel is not None:
            self._detail_panel.setVisible(self._expanded)
        if not self._expanded:
            return
        self._render_detail_rows()

    def _render_detail_rows(self) -> None:
        rows_layout = self._ensure_detail_panel()
        card = rows_layout.parentWidget()
        last_index = len(self._calls) - 1

        while rows_layout.count() > len(self._calls):
            item = rows_layout.takeAt(rows_layout.count() - 1)
            if item is not None:
                _delete_layout_item(item)

        for index, call in enumerate(self._calls):
            if index < rows_layout.count():
                item = rows_layout.itemAt(index)
                row_widget = item.widget() if item is not None else None
                if row_widget is not None:
                    _update_tool_detail_row(row_widget, call, trailing=index < last_index)
                    continue
            rows_layout.addWidget(_tool_detail_row(call, trailing=index < last_index, parent=card))

    def _ensure_detail_panel(self) -> QVBoxLayout:
        if self._detail_panel is not None and self._detail_rows_layout is not None:
            self._detail_panel.setVisible(True)
            return self._detail_rows_layout

        panel = QWidget()
        panel.setObjectName("ToolExpandedPanel")
        panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        card = QFrame()
        card.setObjectName("ToolExpandedCard")
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        rows_layout = QVBoxLayout(card)
        rows_layout.setContentsMargins(0, 6, 0, 6)
        rows_layout.setSpacing(0)

        panel_layout.addWidget(card)
        self.detail_holder.addWidget(panel)

        self._detail_panel = panel
        self._detail_rows_layout = rows_layout
        return rows_layout


def _update_tool_detail_row(row: QWidget, call: _ToolCallState, *, trailing: bool) -> None:
    marker = row.findChild(QWidget, "ToolTimelineMarker")
    if marker is not None:
        dot = marker.findChild(QLabel, "ToolTimelineDot")
        if dot is not None:
            dot.setProperty("status", call.status)
            _refresh_widget(dot)
        connector = marker.findChild(QFrame, "ToolTimelineLine")
        if connector is not None:
            connector.setProperty("active", trailing)
            connector.setVisible(trailing)
            _refresh_widget(connector)

    call_row = row.findChild(QFrame, "ToolCallRow")
    if call_row is not None:
        _update_tool_call_line(call_row, call)


def _update_tool_call_line(row: QFrame, call: _ToolCallState) -> None:
    row.setProperty("status", call.status)
    line = row.findChild(QLabel, "ToolCallLine")
    status = row.findChild(QLabel, "ToolCallStatus")
    start_time = row.findChild(QLabel, "ToolCallStartTime")
    duration = row.findChild(QLabel, "ToolCallDuration")
    if line is not None:
        line.setText(_tool_line_text(call))
    if status is not None:
        status.setText(_tool_detail_status_label(call.status))
        status.setProperty("status", call.status)
        _refresh_widget(status)
    if start_time is not None:
        start_time.setText(call.started_at_text or "--:--:--")
    if duration is not None:
        duration.setText(_tool_duration_text(call))
    _refresh_widget(row)


def _tool_detail_row(call: _ToolCallState, *, trailing: bool, parent: QWidget | None = None) -> QWidget:
    row = QWidget(parent)
    row.setObjectName("ToolDetailRow")
    row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(_tool_timeline_marker(call.status, trailing=trailing, parent=row), 0, Qt.AlignTop)
    layout.addWidget(_tool_call_line(call, parent=row), 1)
    return row


def _tool_timeline_marker(status: str, *, trailing: bool, parent: QWidget | None = None) -> QWidget:
    marker = QWidget(parent)
    marker.setObjectName("ToolTimelineMarker")
    marker.setFixedWidth(18)
    marker.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    layout = QVBoxLayout(marker)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    dot_row = QHBoxLayout()
    dot_row.setContentsMargins(0, 0, 0, 0)
    dot_row.addStretch(1)
    dot = QLabel("", marker)
    dot.setObjectName("ToolTimelineDot")
    dot.setProperty("status", status)
    dot.setFixedSize(10, 10)
    dot_row.addWidget(dot)
    dot_row.addStretch(1)
    layout.addLayout(dot_row)

    line = QFrame(marker)
    line.setObjectName("ToolTimelineLine")
    line.setProperty("active", trailing)
    line.setFixedWidth(2)
    line.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    line.setVisible(trailing)
    line_row = QHBoxLayout()
    line_row.setContentsMargins(0, 1, 0, 0)
    line_row.addStretch(1)
    line_row.addWidget(line)
    line_row.addStretch(1)
    layout.addLayout(line_row, 1)
    return marker


def _tool_call_line(call: _ToolCallState, parent: QWidget | None = None) -> QWidget:
    row = QFrame(parent)
    row.setObjectName("ToolCallRow")
    row.setProperty("status", call.status)
    row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(12, 9, 12, 9)
    layout.setSpacing(8)

    text_block = QVBoxLayout()
    text_block.setContentsMargins(0, 0, 0, 0)
    text_block.setSpacing(2)

    line = QLabel(_tool_line_text(call), row)
    line.setObjectName("ToolCallLine")
    _configure_wrapping_label(line)

    meta_row = QHBoxLayout()
    meta_row.setContentsMargins(0, 0, 0, 0)
    meta_row.setSpacing(6)
    status = QLabel(_tool_detail_status_label(call.status), row)
    status.setObjectName("ToolCallStatus")
    status.setProperty("status", call.status)
    start_time = QLabel(call.started_at_text or "--:--:--", row)
    start_time.setObjectName("ToolCallStartTime")
    meta_row.addWidget(status, 0)
    meta_row.addWidget(start_time, 0)
    meta_row.addStretch(1)

    text_block.addWidget(line)
    text_block.addLayout(meta_row)

    duration = QLabel(_tool_duration_text(call), row)
    duration.setObjectName("ToolCallDuration")
    duration.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    duration.setMinimumWidth(46)

    layout.addLayout(text_block, 1)
    layout.addWidget(duration, 0, Qt.AlignTop)
    return row


def _configure_wrapping_label(label: QLabel) -> None:
    label.setMinimumWidth(0)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)


def _natural_label_width(label: QLabel) -> int:
    text = str(label.property("raw_text") or label.text() or " ")
    metrics = QFontMetrics(label.font())
    margins = label.contentsMargins()
    frame_width = label.frameWidth() * 2
    padding_width = 36
    return metrics.horizontalAdvance(text) + margins.left() + margins.right() + frame_width + padding_width


def _looks_like_json_stream(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def _tool_status_label(status: str) -> str:
    if status == "success":
        return "Done"
    if status == "error":
        return "Fail"
    return "Live"


def _tool_detail_status_label(status: str) -> str:
    if status == "success":
        return "Completed"
    if status == "error":
        return "Failed"
    return "Running"


def _tool_line_text(call: _ToolCallState) -> str:
    return call.description or call.name


def _clean_user_content(content: str) -> str:
    match = re.search(r"<user-message>(.*?)</user-message>", content, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return content.strip()


def _tool_calls(message: dict[str, object]) -> list[dict[str, object]]:
    payload = message.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    raw_tool_calls = message.get("tool_calls") or payload.get("tool_calls") or []
    return [tool_call for tool_call in raw_tool_calls if isinstance(tool_call, dict)] if isinstance(raw_tool_calls, list) else []


def _tool_call_state(tool_call: dict[str, object]) -> _ToolCallState:
    name = _tool_call_name(tool_call)
    arguments = _tool_call_arguments(tool_call)
    description = _tool_call_description(arguments) or name
    started_at_text = _tool_call_started_at_text(tool_call)
    return _ToolCallState(
        id=_tool_call_id(tool_call),
        name=name,
        description=description,
        arguments=arguments,
        started_at_text=started_at_text,
    )


def _tool_call_from_event(event: InspectorEvent) -> _ToolCallState:
    name = event.title.removeprefix("Tool call:").strip() or "tool"
    description = _tool_call_description(event.detail) or name
    return _ToolCallState(
        id=str(event.metadata.get("tool_call_id") or ""),
        name=name,
        description=description,
        arguments=event.detail,
        started_at_text=_display_time_now(),
        started_monotonic=monotonic(),
    )


def _tool_result_state(message: dict[str, object]) -> _ToolResultState:
    content = str(message.get("content") or "")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {}
    payload = message.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    status = str(parsed.get("status") or "result") if isinstance(parsed, dict) else "result"
    tool_call_id = str(
        message.get("tool_call_id")
        or payload.get("tool_call_id")
        or (parsed.get("tool_call_id") if isinstance(parsed, dict) else "")
        or "tool"
    )
    detail_source = parsed.get("result") if isinstance(parsed, dict) and "result" in parsed else content
    if isinstance(parsed, dict) and parsed.get("error"):
        detail_source = parsed["error"]
    state = "error" if status == "error" or (isinstance(parsed, dict) and parsed.get("error")) else "success"
    return _ToolResultState(id=tool_call_id, result=str(detail_source), status=state, finished_at_text="")


def _tool_result_state_from_event(event: InspectorEvent) -> _ToolResultState:
    match = re.match(r"Tool result:\s+(\S+)(?:\s+(\S+))?", event.title)
    call_id = str(event.metadata.get("tool_call_id") or (match.group(1) if match else ""))
    status = "error" if event.tone == "error" or (match and match.group(2) == "error") else "success"
    return _ToolResultState(
        id=call_id,
        result=event.detail,
        status=status,
        finished_at_text=_display_time_now(),
        finished_monotonic=monotonic(),
    )


def _tool_call_id(tool_call: dict[str, object]) -> str:
    return str(tool_call.get("id") or tool_call.get("tool_call_id") or "")


def _tool_call_name(tool_call: dict[str, object]) -> str:
    function = tool_call.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"])
    return str(tool_call.get("name") or tool_call.get("tool_name") or "tool")


def _tool_call_arguments(tool_call: dict[str, object]) -> str:
    function = tool_call.get("function")
    if isinstance(function, dict) and function.get("arguments") is not None:
        return str(function["arguments"])
    arguments = tool_call.get("arguments")
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)


def _tool_call_description(arguments: str) -> str:
    parsed = _parse_json_dict(arguments)
    if not parsed:
        if _looks_like_json_stream(arguments):
            return arguments.strip()
        return ""
    return str(parsed.get("description") or "").strip()


def _tool_call_started_at_text(tool_call: dict[str, object]) -> str:
    for key in ("started_at", "start_time", "created_at", "timestamp"):
        value = tool_call.get(key)
        if isinstance(value, str) and value.strip():
            return _coerce_time_text(value)
    function = tool_call.get("function")
    if isinstance(function, dict):
        for key in ("started_at", "start_time", "created_at", "timestamp"):
            value = function.get(key)
            if isinstance(value, str) and value.strip():
                return _coerce_time_text(value)
    return _display_time_now()


def _coerce_time_text(value: str) -> str:
    stripped = value.strip()
    match = re.search(r"(\d{2}:\d{2}:\d{2})", stripped)
    if match:
        return match.group(1)
    return stripped or "--:--:--"


def _parse_json_dict(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _group_status(calls: list[_ToolCallState]) -> str:
    if any(call.status == "error" for call in calls):
        return "error"
    if any(call.status == "running" for call in calls):
        return "running"
    return "success"


def _tool_duration_text(call: _ToolCallState) -> str:
    if call.status == "running":
        return "Running"
    if call.started_monotonic is None or call.finished_monotonic is None:
        return "--"
    elapsed_ms = max(0, int(round((call.finished_monotonic - call.started_monotonic) * 1000)))
    if elapsed_ms < 1000:
        return f"{elapsed_ms}ms"
    if elapsed_ms < 10_000:
        return f"{elapsed_ms / 1000:.2f}s"
    return f"{elapsed_ms / 1000:.1f}s"


def _display_time_now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _status_key(status: str) -> str:
    value = status.lower()
    if "run" in value:
        return "running"
    if "error" in value or "fail" in value:
        return "error"
    if "finish" in value or "complete" in value or "success" in value or "pass" in value:
        return "success"
    return "ready"


def _status_label(status: str) -> str:
    return {
        "running": "Running",
        "success": "Done",
        "error": "Error",
        "ready": "Ready",
    }.get(status, "Ready")


def _short_session_meta(title: str) -> str:
    if title.startswith("Select or create"):
        return "Execution canvas"
    return f"Session {title[:12]}"


def _refresh_widget(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _destroy_widget(widget: QWidget) -> None:
    widget.hide()
    if shiboken6.isValid(widget):
        shiboken6.delete(widget)


def _delete_layout_item(item: QLayoutItem) -> None:
    widget = item.widget()
    if widget is not None:
        _destroy_widget(widget)
        return
    layout = item.layout()
    if layout is not None:
        _clear_layout(layout)


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        child = layout.takeAt(0)
        if child is not None:
            _delete_layout_item(child)
