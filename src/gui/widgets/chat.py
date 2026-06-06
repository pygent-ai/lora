from __future__ import annotations

import json
import html
import re
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFontMetrics, QKeyEvent, QTextDocument
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
        self._composer_bottom_inset = 18
        self._composer_height = 152
        self._composer_side_inset = 18

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.main_card = QFrame()
        self.main_card.setObjectName("ChatMainCard")
        self.main_card.setAttribute(Qt.WA_StyledBackground, True)
        card_layout = QVBoxLayout(self.main_card)
        card_layout.setContentsMargins(0, 0, 0, self._composer_reserved_space())
        card_layout.setSpacing(0)
        self._card_layout = card_layout
        root.addWidget(self.main_card)

        header_shell = QFrame()
        header_shell.setObjectName("ChatHeaderShell")
        header_shell.setAttribute(Qt.WA_StyledBackground, True)
        header_shell_layout = QVBoxLayout(header_shell)
        header_shell_layout.setContentsMargins(0, 0, 0, 0)
        header_shell_layout.setSpacing(0)
        header_frame = QFrame()
        header_frame.setObjectName("ChatHeader")
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
        scroll_shell_layout = QVBoxLayout(scroll_shell)
        scroll_shell_layout.setContentsMargins(0, 0, 0, 0)
        scroll_shell_layout.setSpacing(0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("ChatScroll")
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_body = QWidget()
        self.scroll_body.setObjectName("ChatScrollBody")
        self.scroll_body.setAttribute(Qt.WA_StyledBackground, True)
        self.scroll_body.setMinimumWidth(0)
        self.messages = QVBoxLayout(self.scroll_body)
        self.messages.setContentsMargins(34, 7, 34, 7)
        self.messages.setSpacing(4)
        self.messages.addStretch(1)
        self.scroll_area.setWidget(self.scroll_body)
        scroll_shell_layout.addWidget(self.scroll_area)
        card_layout.addWidget(scroll_shell, 1)

        composer = QFrame()
        composer.setObjectName("Composer")
        composer.setAttribute(Qt.WA_StyledBackground, True)
        self.input = _MessageInput()
        self.input.setPlaceholderText("Message Lora...")
        self.input.setObjectName("MessageInput")
        self.input.setParent(composer)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.setIcon(icon("send"))
        self.send_button.setToolTip("Send message")
        self.send_button.setFixedSize(132, 38)
        self.send_button.setParent(composer)
        self.send_button.clicked.connect(self._submit)
        self.input.submit_requested.connect(self._submit)
        self.composer = composer
        self.composer.setParent(self.main_card)
        self.composer.raise_()
        self.messages.setContentsMargins(34, 7, 34, 52)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        super().resizeEvent(event)
        self._position_floating_composer()
        self._update_bubble_widths()

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
        for message in history:
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")
            if role == "user":
                self.add_message("user", _clean_user_content(content))
            elif role == "assistant":
                tool_calls = _tool_calls(message)
                if content.strip():
                    self.add_message("assistant", content)
                if tool_calls:
                    self._append_tool_calls(tool_calls)
            elif role == "tool":
                self._apply_tool_result(_tool_result_state(message))

    def clear(self) -> None:
        while self.messages.count() > 1:
            item = self.messages.takeAt(0)
            _delete_layout_item(item)
        self._assistant_row = None
        self._assistant_text = ""
        self._thinking_widget = None
        self._active_tool_group = None

    def add_message(self, role: str, content: str) -> None:
        self._remove_thinking_status()
        self._active_tool_group = None
        if role == "assistant":
            row = AssistantMessageRow()
            row.render_blocks(parse_markdown_blocks(content))
            self.messages.insertWidget(self.messages.count() - 1, row)
            self._scroll_to_bottom()
            return
        row = UserMessageRow(content)
        self.messages.insertWidget(self.messages.count() - 1, row)
        self._update_bubble_widths()
        self._scroll_to_bottom()

    def start_assistant_message(self) -> None:
        self._active_tool_group = None
        self._assistant_text = ""
        self._remove_thinking_status()
        self._thinking_widget = ThinkingRow("Thinking")
        self.messages.insertWidget(self.messages.count() - 1, self._thinking_widget)
        self._scroll_to_bottom()

    def _create_assistant_message_row(self) -> None:
        row = AssistantMessageRow()
        row.hide()
        self._assistant_row = row
        self.messages.insertWidget(self.messages.count() - 1, row)
        self._active_tool_group = None

    def append_assistant_delta(self, delta: str) -> None:
        if self._assistant_row is None:
            if self._thinking_widget is None:
                self.start_assistant_message()
            if self._active_tool_group is None:
                self._remove_thinking_status()
            self._create_assistant_message_row()
        self._assistant_text += delta
        assert self._assistant_row is not None
        self._assistant_row.show()
        self._assistant_row.render_blocks(parse_markdown_blocks(self._assistant_text or " "))
        self._active_tool_group = None
        self._update_bubble_widths()
        self._scroll_to_bottom()

    def finish_assistant_message(self, final_answer: str | None = None) -> None:
        if final_answer:
            updated_row = False
            if self._assistant_row is None:
                if self._thinking_widget is not None and self._active_tool_group is None:
                    self._remove_thinking_status()
                self._create_assistant_message_row()
            if final_answer != self._assistant_text and self._assistant_row is not None:
                self._assistant_text = final_answer
                self._assistant_row.show()
                self._assistant_row.render_blocks(parse_markdown_blocks(final_answer or " "))
                updated_row = True
            if updated_row:
                self._update_bubble_widths()
                self._scroll_to_bottom()
                QTimer.singleShot(0, self._scroll_to_bottom)
        self._assistant_row = None
        self._assistant_text = ""

    def show_error(self, message: str) -> None:
        self.set_run_status("error")
        self.add_message("assistant", f"Error: {message}")

    def add_runtime_event(self, event: InspectorEvent) -> None:
        if event.kind != "tool":
            return
        if event.title.startswith("Tool call:"):
            self._close_assistant_stream()
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

    def _position_floating_composer(self) -> None:
        if not hasattr(self, "composer"):
            return
        self._card_layout.setContentsMargins(0, 0, 0, self._composer_reserved_space())
        composer_width = max(240, self.main_card.width() - (self._composer_side_inset * 2))
        composer_y = self.main_card.height() - self._composer_height - self._composer_bottom_inset
        self.composer.setGeometry(self._composer_side_inset, composer_y, composer_width, self._composer_height)
        composer_padding_x = 20
        composer_padding_top = 18
        composer_padding_bottom = 18
        button_margin_right = 20
        button_margin_bottom = 20
        button_width = self.send_button.width()
        button_height = self.send_button.height()
        button_x = composer_width - button_width - button_margin_right
        button_y = self._composer_height - button_height - button_margin_bottom
        self.send_button.move(button_x, button_y)

        input_right_margin = button_width + 40
        input_width = max(160, composer_width - (composer_padding_x * 2) - input_right_margin)
        input_height = self._composer_height - composer_padding_top - composer_padding_bottom
        self.input.setGeometry(composer_padding_x, composer_padding_top, input_width, input_height)
        self.composer.raise_()

    def _composer_reserved_space(self) -> int:
        return self._composer_height + self._composer_bottom_inset + 12

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _maximum_bubble_width(self) -> int:
        viewport_width = self.scroll_area.viewport().width()
        return max(1, viewport_width - 64)

    def _update_bubble_widths(self) -> None:
        maximum_width = self._maximum_bubble_width()
        for bubble in self.findChildren(QLabel):
            if bubble.objectName() == "UserBubble":
                bubble_limit = int(maximum_width * 0.72)
                bubble_width = min(_natural_label_width(bubble), bubble_limit)
                bubble.setFixedWidth(bubble_width)
                bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)
            elif bubble.objectName() == "AssistantBubble":
                bubble_limit = int(maximum_width * 0.82)
                bubble_width = min(_natural_label_width(bubble), bubble_limit)
                bubble.setFixedWidth(bubble_width)
                bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)
                _sync_assistant_label_height(bubble)

    def _remove_thinking_status(self) -> None:
        if self._thinking_widget is None:
            return
        self._remove_widget_from_messages(self._thinking_widget)
        self._thinking_widget.setParent(None)
        self._thinking_widget.deleteLater()
        self._thinking_widget = None

    def _remove_widget_from_messages(self, widget: QWidget) -> None:
        for index in range(self.messages.count()):
            item = self.messages.itemAt(index)
            if item is not None and item.widget() is widget:
                self.messages.takeAt(index)
                return

    def _close_assistant_stream(self) -> None:
        self._assistant_row = None
        self._assistant_text = ""

    def _append_tool_calls(self, tool_calls: list[dict[str, object] | "_ToolCallState"]) -> None:
        if self._active_tool_group is None:
            self._active_tool_group = _ToolGroupWidget()
            self.messages.insertWidget(self.messages.count() - 1, self._active_tool_group)
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


class _ToolGroupWidget(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ToolStatusGroup")
        self._calls: list[_ToolCallState] = []
        self._expanded = False
        self._blink_phase = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(520)
        self._blink_timer.timeout.connect(self._advance_blink_phase)

        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(38, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("ToolStatusRow")
        self.card.setProperty("status", "running")
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
        self.title.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.title.setWordWrap(False)
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
        _clear_layout(self.detail_holder)
        if not self._expanded:
            return
        self.detail_holder.addWidget(_tool_group_detail(self._calls))


def _tool_group_detail(calls: list[_ToolCallState]) -> QWidget:
    panel = QWidget()
    panel.setObjectName("ToolExpandedPanel")
    layout = QHBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)

    timeline = QWidget()
    timeline.setObjectName("ToolTimeline")
    timeline_layout = QVBoxLayout(timeline)
    timeline_layout.setContentsMargins(0, 6, 0, 6)
    timeline_layout.setSpacing(0)

    card = QFrame()
    card.setObjectName("ToolExpandedCard")
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(0, 0, 0, 0)
    card_layout.setSpacing(0)

    for index, call in enumerate(calls):
        timeline_layout.addWidget(_tool_timeline_marker(call.status, trailing=index < len(calls) - 1))
        card_layout.addWidget(_tool_call_line(call))

    layout.addWidget(timeline, 0)
    layout.addWidget(card, 1)
    return panel


def _tool_timeline_marker(status: str, *, trailing: bool) -> QWidget:
    marker = QWidget()
    marker.setObjectName("ToolTimelineMarker")
    marker.setFixedWidth(18)
    marker.setMinimumHeight(32)
    layout = QVBoxLayout(marker)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    dot_row = QHBoxLayout()
    dot_row.setContentsMargins(0, 0, 0, 0)
    dot_row.addStretch(1)
    dot = QLabel("")
    dot.setObjectName("ToolTimelineDot")
    dot.setProperty("status", status)
    dot.setFixedSize(10, 10)
    dot_row.addWidget(dot)
    dot_row.addStretch(1)
    layout.addLayout(dot_row)

    line = QFrame()
    line.setObjectName("ToolTimelineLine")
    line.setProperty("active", trailing)
    line.setFixedWidth(2)
    line.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    line.setVisible(False)
    line_row = QHBoxLayout()
    line_row.setContentsMargins(0, 1, 0, 0)
    line_row.addStretch(1)
    line_row.addWidget(line)
    line_row.addStretch(1)
    layout.addLayout(line_row, 1)
    return marker


def _tool_call_line(call: _ToolCallState) -> QWidget:
    row = QFrame()
    row.setObjectName("ToolCallRow")
    row.setProperty("status", call.status)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(12, 9, 12, 9)
    layout.setSpacing(8)

    text_block = QVBoxLayout()
    text_block.setContentsMargins(0, 0, 0, 0)
    text_block.setSpacing(2)

    line = QLabel(_tool_line_text(call))
    line.setObjectName("ToolCallLine")
    _configure_wrapping_label(line)

    meta_row = QHBoxLayout()
    meta_row.setContentsMargins(0, 0, 0, 0)
    meta_row.setSpacing(6)
    status = QLabel(_tool_detail_status_label(call.status))
    status.setObjectName("ToolCallStatus")
    status.setProperty("status", call.status)
    start_time = QLabel(call.started_at_text or "--:--:--")
    start_time.setObjectName("ToolCallStartTime")
    meta_row.addWidget(status, 0)
    meta_row.addWidget(start_time, 0)
    meta_row.addStretch(1)

    text_block.addWidget(line)
    text_block.addLayout(meta_row)

    duration = QLabel(_tool_duration_text(call))
    duration.setObjectName("ToolCallDuration")
    duration.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    duration.setMinimumWidth(46)

    layout.addLayout(text_block, 1)
    layout.addWidget(duration, 0, Qt.AlignTop)
    return row


def _configure_user_message_bubble(label: QLabel) -> None:
    label.setMinimumWidth(0)
    label.setWordWrap(True)
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setProperty("format", "text")
    label.setProperty("raw_text", "")
    label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)


def _configure_assistant_message_label(label: QLabel) -> None:
    label.setMinimumWidth(0)
    label.setWordWrap(True)
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setProperty("format", "text")
    label.setProperty("raw_text", "")
    label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)


def _configure_wrapping_label(label: QLabel) -> None:
    label.setMinimumWidth(0)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)


def _natural_label_width(label: QLabel) -> int:
    text = str(label.property("raw_text") or label.text() or " ")
    metrics = QFontMetrics(label.font())
    margins = label.contentsMargins()
    frame_width = label.frameWidth() * 2
    padding_width = 36
    return metrics.horizontalAdvance(text) + margins.left() + margins.right() + frame_width + padding_width


def _set_message_bubble_text(label: QLabel, text: str) -> None:
    label.setProperty("raw_text", text)
    next_format = _message_bubble_format(label, text)
    label.setTextFormat(Qt.RichText if next_format == "markdown" else Qt.PlainText)
    label.setText(_markdown_to_html(text) if next_format == "markdown" else text)
    if label.objectName() == "AssistantBubble":
        _sync_assistant_label_height(label)
    if label.property("format") != next_format:
        label.setProperty("format", next_format)
        _refresh_widget(label)


def _message_bubble_format(label: QLabel, text: str) -> str:
    if label.objectName() != "AssistantBubble":
        return "text"
    if _looks_like_json_stream(text):
        return "json"
    if _looks_like_markdown_stream(text):
        return "markdown"
    return "text"


def _sync_assistant_label_height(label: QLabel) -> None:
    if label.objectName() != "AssistantBubble":
        return
    label.setMinimumHeight(0)
    label.setMaximumHeight(16777215)
    label.updateGeometry()
    target_height = max(label.sizeHint().height(), label.minimumSizeHint().height(), 24)
    label.setFixedHeight(target_height)


def _looks_like_json_stream(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def _looks_like_markdown_stream(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return False
    if stripped.startswith(("```", "#", "> ", "- ", "* ", "+ ")):
        return True
    return bool(re.search(r"(^|\n)\s*(#{1,6}\s|[-*+]\s|>\s|\d+\.\s|```)|(\*\*|__|`[^`]*$|\[[^\]]+\]\()", text))


def _markdown_to_html(text: str) -> str:
    document = QTextDocument()
    document.setMarkdown(text)
    return _style_markdown_html(document.toHtml(), text)


def _style_markdown_html(html_text: str, markdown_text: str) -> str:
    pre_blocks: list[str] = []

    def _stash_pre_block(match: re.Match[str]) -> str:
        content = re.sub(r"</?span[^>]*>", "", match.group(1))
        token = f"@@PRE_BLOCK_{len(pre_blocks)}@@"
        pre_blocks.append(
            (
                "<table cellspacing=\"0\" cellpadding=\"0\" "
                "style=\"margin:0 0 14px 0; width:100%;\">"
                "<tr><td style=\""
                "background-color:#1c2430;"
                "border-radius:16px;"
                "padding:14px;"
                "\">"
                "<pre style=\""
                "margin:0;"
                "color:#eaf2ff;"
                "background-color:#1c2430;"
                "font-family:'Consolas','Courier New',monospace;"
                "white-space:pre-wrap;"
                "\">"
                f"{content}</pre></td></tr></table>"
            )
        )
        return token

    html_text = re.sub(r"<pre[^>]*>(.*?)</pre>", _stash_pre_block, html_text, flags=re.DOTALL)
    styled = html_text.replace(
        "<body style=\" font-family:'Sans Serif'; font-size:9pt; font-weight:400; font-style:normal;\">",
        (
            "<body style=\""
            "font-family:'Microsoft YaHei UI','Segoe UI Variable Text','Segoe UI',sans-serif;"
            "font-size:14px;"
            "line-height:1.66;"
            "font-weight:400;"
            "font-style:normal;"
            "color:#243044;"
            "\">"
        ),
    )
    styled = re.sub(
        r"<h1 style=\"[^\"]*\"><span style=\"[^\"]*\">",
        "<h1 style=\"margin:0 0 10px 0; font-size:25px; font-weight:700; color:#243044;\"><span>",
        styled,
        count=1,
    )
    styled = re.sub(
        r"<p style=\"[^\"]*\">",
        "<p style=\"margin:0 0 12px 0; color:#243044;\">",
        styled,
    )
    styled = re.sub(
        r"<ul style=\"[^\"]*\">",
        "<ul style=\"margin:2px 0 12px 18px; color:#243044;\">",
        styled,
    )
    styled = re.sub(
        r"<ol style=\"[^\"]*\">",
        "<ol style=\"margin:2px 0 12px 20px; color:#243044;\">",
        styled,
    )
    styled = re.sub(
        r"<li style=\"[^\"]*\">",
        "<li style=\"margin:0 0 5px 0;\">",
        styled,
    )
    styled = styled.replace(
        "<span style=\" font-family:'monospace';\">",
        (
            "<span style=\""
            "font-family:'Consolas','SFMono-Regular',monospace;"
            "background-color:#e9eff8;"
            "color:#1c5c98;"
            "padding:2px 6px;"
            "border-radius:7px;"
            "\">"
        ),
    )
    for index, block in enumerate(pre_blocks):
        styled = styled.replace(f"@@PRE_BLOCK_{index}@@", block)
    for quote_text in _markdown_quote_blocks(markdown_text):
        escaped_quote = html.escape(quote_text, quote=False).replace("\n", "<br />")
        default_paragraph = f"<p style=\"margin:0 0 12px 0; color:#243044;\">{escaped_quote}</p>"
        quoted_paragraph = (
            "<p style=\""
            "margin:0 0 12px 0;"
            "padding:10px 12px;"
            "background-color:#f2f6fb;"
            "border:1px solid #dce4ef;"
            "border-radius:10px;"
            "color:#5a6b82;"
            "\">"
            f"{escaped_quote}</p>"
        )
        styled = styled.replace(default_paragraph, quoted_paragraph, 1)
    return styled


def _markdown_quote_blocks(text: str) -> list[str]:
    quotes: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith(">"):
            current.append(line[1:].lstrip())
            continue
        if current:
            quotes.append("\n".join(current).strip())
            current = []
    if current:
        quotes.append("\n".join(current).strip())
    return [quote for quote in quotes if quote]


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


def _delete_layout_item(item: QLayoutItem) -> None:
    widget = item.widget()
    if widget is not None:
        widget.setParent(None)
        widget.deleteLater()
        return
    layout = item.layout()
    if layout is not None:
        _clear_layout(layout)


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        child = layout.takeAt(0)
        if child is not None:
            _delete_layout_item(child)
