from __future__ import annotations

import json
import re
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
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


class ChatPane(QWidget):
    message_submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ChatPane")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._assistant_label: QLabel | None = None
        self._assistant_row: QFrame | None = None
        self._assistant_text = ""
        self._thinking_widget: QWidget | None = None
        self._active_tool_group: _ToolGroupWidget | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header_frame = QFrame()
        header_frame.setObjectName("ChatHeader")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(22, 16, 22, 14)
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
        self.run_status.setFixedWidth(82)
        header_text.addWidget(self.header)
        header_text.addWidget(self.header_meta)
        header_layout.addLayout(header_text, 1)
        header_layout.addWidget(self.run_status)
        root.addWidget(header_frame)

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
        self.messages.setContentsMargins(32, 28, 32, 28)
        self.messages.setSpacing(20)
        self.messages.addStretch(1)
        self.scroll_area.setWidget(self.scroll_body)
        root.addWidget(self.scroll_area, 1)

        composer = QFrame()
        composer.setObjectName("Composer")
        composer_layout = QHBoxLayout(composer)
        composer_layout.setContentsMargins(24, 18, 24, 18)
        composer_layout.setSpacing(14)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Message Lora...")
        self.input.setFixedHeight(74)
        self.input.setObjectName("MessageInput")
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.setIcon(icon("send"))
        self.send_button.setToolTip("Send message")
        self.send_button.setFixedSize(104, 54)
        self.send_button.clicked.connect(self._submit)
        composer_layout.addWidget(self.input, 1)
        composer_layout.addWidget(self.send_button)
        root.addWidget(composer)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        super().resizeEvent(event)
        self._update_bubble_widths()

    def set_session_title(self, title: str) -> None:
        self.header.setText(title)
        self.header_meta.setText(_short_session_meta(title))

    def set_session_context(self, title: str, *, agent: str | None = None, model: str | None = None) -> None:
        self.header.setText(title)
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
        self._assistant_label = None
        self._assistant_row = None
        self._assistant_text = ""
        self._thinking_widget = None
        self._active_tool_group = None

    def add_message(self, role: str, content: str) -> None:
        self._remove_thinking_status()
        self._active_tool_group = None
        row = QFrame()
        row.setObjectName("UserMessageGroup" if role == "user" else "AssistantMessageGroup")
        row.setMinimumWidth(0)
        row.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.MinimumExpanding)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        bubble = QLabel(content or " ")
        bubble.setObjectName("UserBubble" if role == "user" else "AssistantBubble")
        _configure_message_bubble(bubble)
        avatar = _message_avatar(role)
        if role == "user":
            layout.addStretch(1)
            layout.addWidget(bubble, 0, Qt.AlignRight)
            layout.addWidget(avatar)
        else:
            layout.addWidget(avatar)
            layout.addWidget(bubble, 1, Qt.AlignLeft)
            layout.addStretch(1)
        self.messages.insertWidget(self.messages.count() - 1, row)
        self._update_bubble_widths()
        self._scroll_to_bottom()

    def start_assistant_message(self) -> None:
        self._active_tool_group = None
        self._assistant_text = ""
        self._remove_thinking_status()
        self._thinking_widget = self._thinking_status_widget("Thinking")
        self.messages.insertWidget(self.messages.count() - 1, self._thinking_widget)
        self._scroll_to_bottom()

    def _create_assistant_message_row(self) -> None:
        row = QFrame()
        row.setObjectName("AssistantMessageGroup")
        row.setMinimumWidth(0)
        row.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.MinimumExpanding)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        avatar = _message_avatar("assistant")
        self._assistant_label = QLabel(" ")
        self._assistant_label.setObjectName("AssistantBubble")
        _configure_message_bubble(self._assistant_label)
        self._assistant_label.hide()
        layout.addWidget(avatar)
        layout.addWidget(self._assistant_label, 1, Qt.AlignLeft)
        layout.addStretch(1)
        row.hide()
        self._assistant_row = row
        self.messages.insertWidget(self.messages.count() - 1, row)
        self._update_bubble_widths()
        self._active_tool_group = None

    def append_assistant_delta(self, delta: str) -> None:
        if self._assistant_label is None:
            if self._thinking_widget is None:
                self.start_assistant_message()
            if self._active_tool_group is None:
                self._remove_thinking_status()
            self._create_assistant_message_row()
        self._assistant_text += delta
        assert self._assistant_label is not None
        if self._assistant_row is not None:
            self._assistant_row.show()
        self._assistant_label.show()
        self._assistant_label.setText(self._assistant_text or " ")
        self._active_tool_group = None
        self._update_bubble_widths()
        self._scroll_to_bottom()

    def finish_assistant_message(self, final_answer: str | None = None) -> None:
        if final_answer and self._assistant_label is not None and final_answer != self._assistant_text:
            self._remove_thinking_status()
            self._assistant_text = final_answer
            if self._assistant_row is not None:
                self._assistant_row.show()
            self._assistant_label.show()
            self._assistant_label.setText(final_answer)
        self._assistant_label = None
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

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _maximum_bubble_width(self) -> int:
        margins = self.messages.contentsMargins()
        available_width = self.scroll_area.viewport().width() - margins.left() - margins.right()
        row_chrome_width = 36 + 12
        return max(1, min(int(max(available_width, 1) * 0.75), max(1, available_width - row_chrome_width)))

    def _update_bubble_widths(self) -> None:
        maximum_width = self._maximum_bubble_width()
        for bubble in self.findChildren(QLabel):
            if bubble.objectName() in {"UserBubble", "AssistantBubble"}:
                bubble.setFixedWidth(min(_natural_label_width(bubble), maximum_width))
                bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)

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
        self._assistant_label = None
        self._assistant_row = None
        self._assistant_text = ""

    def _thinking_status_widget(self, title: str) -> QWidget:
        row = QFrame()
        row.setObjectName("ToolStatusRow")
        row.setProperty("status", "running")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        status_icon = QLabel("RUN")
        status_icon.setObjectName("ToolStatusIcon")
        status_icon.setProperty("status", "running")
        status_icon.setAlignment(Qt.AlignCenter)
        status_icon.setFixedSize(34, 22)
        title_label = QLabel(title)
        title_label.setObjectName("ThinkingStatusTitle")
        layout.addWidget(status_icon)
        layout.addWidget(title_label, 1)
        return row

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


@dataclass(frozen=True, slots=True)
class _ToolResultState:
    id: str
    result: str
    status: str


class _ToolGroupWidget(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ToolStatusRow")
        self.setProperty("status", "running")
        self._calls: list[_ToolCallState] = []
        self._expanded = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        summary = QHBoxLayout()
        summary.setContentsMargins(0, 0, 0, 0)
        summary.setSpacing(10)
        self.status_icon = QLabel("RUN")
        self.status_icon.setObjectName("ToolStatusIcon")
        self.status_icon.setProperty("status", "running")
        self.status_icon.setAlignment(Qt.AlignCenter)
        self.status_icon.setFixedSize(34, 22)
        self.toggle = QPushButton(">")
        self.toggle.setObjectName("ToolStatusToggle")
        self.toggle.setFixedSize(22, 22)
        self.toggle.clicked.connect(self._toggle_detail)
        self.title = QLabel("")
        self.title.setObjectName("ToolStatusTitle")
        _configure_wrapping_label(self.title)
        summary.addWidget(self.status_icon)
        summary.addWidget(self.toggle)
        summary.addWidget(self.title, 1)
        layout.addLayout(summary)

        self.detail_holder = QVBoxLayout()
        self.detail_holder.setContentsMargins(46, 0, 0, 0)
        self.detail_holder.setSpacing(6)
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

    def _refresh(self) -> None:
        if not self._calls:
            self.title.setText("")
            self.toggle.setEnabled(False)
            return
        self.title.setText(self._calls[-1].description or self._calls[-1].name)
        status = _group_status(self._calls)
        self.setProperty("status", status)
        self.status_icon.setText(_tool_status_label(status))
        self.status_icon.setProperty("status", status)
        _refresh_widget(self)
        _refresh_widget(self.status_icon)
        self.toggle.setEnabled(True)
        self.toggle.setText("v" if self._expanded else ">")
        _clear_layout(self.detail_holder)
        if not self._expanded:
            return
        for call in self._calls:
            self.detail_holder.addWidget(_tool_call_line(call))


def _tool_call_line(call: _ToolCallState) -> QWidget:
    row = QFrame()
    row.setObjectName("ToolCallRow")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(8, 6, 8, 6)
    layout.setSpacing(8)
    status = QLabel(_tool_status_label(call.status))
    status.setObjectName("ToolCallStatus")
    status.setProperty("status", call.status)
    status.setAlignment(Qt.AlignCenter)
    status.setFixedWidth(44)
    line = QLabel(_tool_line_text(call))
    line.setObjectName("ToolCallLine")
    _configure_wrapping_label(line)
    layout.addWidget(status)
    layout.addWidget(line, 1)
    return row


def _configure_message_bubble(label: QLabel) -> None:
    label.setMinimumWidth(0)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)


def _configure_wrapping_label(label: QLabel) -> None:
    label.setMinimumWidth(0)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)


def _natural_label_width(label: QLabel) -> int:
    text = label.text() or " "
    metrics = QFontMetrics(label.font())
    margins = label.contentsMargins()
    frame_width = label.frameWidth() * 2
    padding_width = 36
    return metrics.horizontalAdvance(text) + margins.left() + margins.right() + frame_width + padding_width


def _tool_status_label(status: str) -> str:
    if status == "success":
        return "OK"
    if status == "error":
        return "ERR"
    return "RUN"


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
    return _ToolCallState(id=_tool_call_id(tool_call), name=name, description=description, arguments=arguments)


def _tool_call_from_event(event: InspectorEvent) -> _ToolCallState:
    name = event.title.removeprefix("Tool call:").strip() or "tool"
    description = _tool_call_description(event.detail) or name
    return _ToolCallState(id=str(event.metadata.get("tool_call_id") or ""), name=name, description=description, arguments=event.detail)


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
    return _ToolResultState(id=tool_call_id, result=str(detail_source), status=state)


def _tool_result_state_from_event(event: InspectorEvent) -> _ToolResultState:
    match = re.match(r"Tool result:\s+(\S+)(?:\s+(\S+))?", event.title)
    call_id = str(event.metadata.get("tool_call_id") or (match.group(1) if match else ""))
    status = "error" if event.tone == "error" or (match and match.group(2) == "error") else "success"
    return _ToolResultState(id=call_id, result=event.detail, status=status)


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
        return ""
    return str(parsed.get("description") or "").strip()


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
