from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QConicalGradient, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.workers import InspectorEvent
from lora.schema import ContextEvent
from lora.tracing import DESIGN_EVENT_TYPES, EventStore

_EVENT_TYPE_ROLE = 256
_ITEM_KIND_ROLE = 257
_TOOL_CALL_ID_ROLE = 258
_TOOL_STATUS_ROLE = 259
_TOOL_CHILD_KIND_ROLE = 260


class TraceInspector(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TraceInspector")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(12)

        header = QFrame()
        header.setObjectName("InspectorStatusCard")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(10)
        header_text = QVBoxLayout()
        header_text.setContentsMargins(0, 0, 0, 0)
        header_text.setSpacing(4)
        self.status = QLabel("Ready")
        self.status.setObjectName("InspectorStatus")
        self.ids = QLabel("")
        self.ids.setObjectName("InspectorMeta")
        self.ids.setWordWrap(True)
        self.status_indicator = QLabel("Ready")
        self.status_indicator.setObjectName("InspectorStatusIndicator")
        self.status_indicator.setProperty("status", "ready")
        self.status_indicator.setAlignment(Qt.AlignCenter)
        self.status_indicator.setFixedWidth(72)
        header_text.addWidget(self.status)
        header_text.addWidget(self.ids)
        header_layout.addLayout(header_text, 1)
        header_layout.addWidget(self.status_indicator)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("InspectorTabs")
        self.events = QTreeWidget()
        self.events.setHeaderLabels(["Event type", "Detail"])
        self.events.setRootIsDecorated(True)
        self.events.setAlternatingRowColors(True)
        self.events.setColumnWidth(0, 168)
        self.tools = _trace_tree(["Tool", "Event"])
        self.files = _trace_tree(["File", "Event"])
        self.config = QListWidget()
        self.tabs.addTab(self.events, "Events")
        self.tabs.addTab(self.tools, "Tools")
        self.tabs.addTab(self.files, "Files")
        self.tabs.addTab(self.config, "Config")

        layout.addWidget(header)
        layout.addWidget(self.tabs, 1)
        self._event_type_items: dict[str, QTreeWidgetItem] = {}
        self._tool_items_by_call_id: dict[str, QTreeWidgetItem] = {}
        self._tool_icon_phase = 0
        self._tool_icon_timer = QTimer(self)
        self._tool_icon_timer.setInterval(180)
        self._tool_icon_timer.timeout.connect(self._advance_running_tool_icons)
        self._set_empty_states()

    def reset_context(self, *, session_id: str | None = None, case_run_id: str | None = None) -> None:
        self.clear_trace_events()
        self._clear_tool_items()
        self.files.clear()
        self.set_status("Ready")
        parts = []
        if session_id:
            parts.append(f"Session: {session_id}")
        if case_run_id:
            parts.append(f"Run: {case_run_id}")
        self.ids.setText(_format_meta_lines(parts))
        self._set_empty_states()

    def set_status(self, status: str) -> None:
        self.status.setText(status)
        normalized = _status_key(status)
        self.status_indicator.setText(_status_label(normalized))
        self.status_indicator.setProperty("status", normalized)
        _refresh_widget(self.status_indicator)

    def set_config(self, entries: dict[str, object]) -> None:
        self.config.clear()
        for key, value in entries.items():
            row = _config_row(str(key), str(value))
            item = QListWidgetItem()
            item.setSizeHint(QSize(row.sizeHint().width(), 42))
            self.config.addItem(item)
            self.config.setItemWidget(item, row)

    def add_event(self, event: InspectorEvent) -> None:
        self._add_live_event(event)
        if event.kind == "tool":
            self._add_live_tool_event(event)
        if event.kind == "file":
            self._add_file_item(event.title, event.kind, _event_detail({"title": event.title, "detail": event.detail, "tone": event.tone}))

    def clear_trace_events(self) -> None:
        self.events.clear()
        self._event_type_items = {}
        for event_type in sorted(DESIGN_EVENT_TYPES):
            item = QTreeWidgetItem([event_type, ""])
            item.setData(0, _EVENT_TYPE_ROLE, event_type)
            item.setExpanded(False)
            self.events.addTopLevelItem(item)
            self._event_type_items[event_type] = item

    def add_trace_event(self, event: ContextEvent) -> None:
        group = self.event_type_item(event.type)
        if group is None:
            return
        child = QTreeWidgetItem([_event_summary(event), _event_detail(event.to_dict())])
        child.setData(0, _EVENT_TYPE_ROLE, event.type)
        child.setData(0, _ITEM_KIND_ROLE, event.id)
        group.addChild(child)
        group.setExpanded(False)
        if event.type == "tool.call" or event.type == "tool.result":
            self._add_trace_tool_event(event)
        if event.type.startswith("file.") or event.type == "diff.created":
            self._add_file_item(*_file_summary(event), _event_detail(event.to_dict()))

    def load_trace_events_from_run_dir(self, run_dir: str | Path) -> None:
        self.clear_trace_events()
        self._clear_tool_items()
        self.files.clear()
        self._set_empty_states()
        for row in EventStore.iter_jsonl(Path(run_dir) / "events.jsonl"):
            self.add_trace_event(ContextEvent.from_dict(row))

    def event_type_item(self, event_type: str) -> QTreeWidgetItem | None:
        return self._event_type_items.get(event_type)

    def _set_empty_states(self) -> None:
        if self.events.topLevelItemCount() == 0:
            self.clear_trace_events()
        _set_tree_placeholder(self.tools, "No tool calls yet")
        _set_tree_placeholder(self.files, "No file activity yet")

    def _add_live_tool_event(self, event: InspectorEvent) -> None:
        detail = _event_detail(
            {
                "title": event.title,
                "detail": event.detail,
                "tone": event.tone,
                "metadata": event.metadata,
            }
        )
        if event.title.startswith("Tool call:"):
            title, context, call_id = _live_tool_call_summary(event)
            self._add_tool_call(title=title, context=context, detail=detail, call_id=call_id)
            return
        if event.title.startswith("Tool result:"):
            title, context, call_id, status = _live_tool_result_summary(event)
            self._apply_tool_result(title=title, context=context, detail=detail, call_id=call_id, status=status)
            return
        self._add_tool_call(title=event.title or "tool", context=event.kind, detail=detail, call_id="")

    def _add_trace_tool_event(self, event: ContextEvent) -> None:
        detail = _event_detail(event.to_dict())
        if event.type == "tool.call":
            title, context, call_id = _trace_tool_call_summary(event)
            self._add_tool_call(title=title, context=context, detail=detail, call_id=call_id)
            return
        title, context, call_id, status = _trace_tool_result_summary(event)
        self._apply_tool_result(title=title, context=context, detail=detail, call_id=call_id, status=status)

    def _add_tool_call(self, *, title: str, context: str, detail: str, call_id: str) -> None:
        item = self._tool_item_for_call(call_id)
        if item is None:
            item = self._new_tool_item(title, _tool_context("running", context), call_id=call_id, status="running")
        else:
            if _tool_item_title_is_placeholder(item, call_id):
                item.setText(0, title)
            if item.data(0, _TOOL_STATUS_ROLE) not in {"success", "error"}:
                self._set_tool_item_status(item, "running", _tool_context("running", context))
        _upsert_tool_child(item, "Call", detail)
        self._sync_tool_icon_timer()

    def _apply_tool_result(self, *, title: str, context: str, detail: str, call_id: str, status: str) -> None:
        item = self._tool_item_for_call(call_id)
        if item is None:
            item = self._new_tool_item(title, _tool_context(status, context), call_id=call_id, status=status)
        else:
            self._set_tool_item_status(item, status, _tool_context(status, context))
        _upsert_tool_child(item, "Result", detail)
        self._sync_tool_icon_timer()

    def _tool_item_for_call(self, call_id: str) -> QTreeWidgetItem | None:
        return self._tool_items_by_call_id.get(call_id) if call_id else None

    def _new_tool_item(self, title: str, context: str, *, call_id: str, status: str) -> QTreeWidgetItem:
        _clear_tree_placeholder(self.tools)
        item = QTreeWidgetItem([title, context])
        item.setExpanded(False)
        item.setData(0, _TOOL_CALL_ID_ROLE, call_id)
        item.setData(0, _TOOL_STATUS_ROLE, status)
        item.setIcon(0, _tool_status_icon(status, self._tool_icon_phase))
        if call_id:
            self._tool_items_by_call_id[call_id] = item
        self.tools.addTopLevelItem(item)
        return item

    def _set_tool_item_status(self, item: QTreeWidgetItem, status: str, context: str) -> None:
        item.setText(1, context)
        item.setData(0, _TOOL_STATUS_ROLE, status)
        item.setIcon(0, _tool_status_icon(status, self._tool_icon_phase))

    def _clear_tool_items(self) -> None:
        self.tools.clear()
        self._tool_items_by_call_id = {}
        self._tool_icon_timer.stop()

    def _advance_running_tool_icons(self) -> None:
        self._tool_icon_phase = (self._tool_icon_phase + 1) % 24
        has_running = False
        for index in range(self.tools.topLevelItemCount()):
            item = self.tools.topLevelItem(index)
            if item.data(0, _TOOL_STATUS_ROLE) == "running":
                item.setIcon(0, _tool_status_icon("running", self._tool_icon_phase))
                has_running = True
        if not has_running:
            self._tool_icon_timer.stop()

    def _sync_tool_icon_timer(self) -> None:
        has_running = any(
            self.tools.topLevelItem(index).data(0, _TOOL_STATUS_ROLE) == "running"
            for index in range(self.tools.topLevelItemCount())
        )
        if has_running and not self._tool_icon_timer.isActive():
            self._tool_icon_timer.start()
        elif not has_running and self._tool_icon_timer.isActive():
            self._tool_icon_timer.stop()

    def _add_file_item(self, title: str, context: str, detail: str) -> None:
        _add_collapsed_detail_item(self.files, title, context, detail)

    def _add_live_event(self, event: InspectorEvent) -> None:
        event_type = _live_event_type(event)
        group = self.event_type_item(event_type)
        if group is None:
            return
        child = QTreeWidgetItem([event.title, _event_detail({"title": event.title, "detail": event.detail, "tone": event.tone})])
        child.setData(0, _EVENT_TYPE_ROLE, event_type)
        group.addChild(child)


def _trace_tree(headers: list[str]) -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderLabels(headers)
    tree.setRootIsDecorated(True)
    tree.setAlternatingRowColors(True)
    tree.setColumnWidth(0, 150)
    return tree


def _set_tree_placeholder(tree: QTreeWidget, text: str) -> None:
    if tree.topLevelItemCount():
        return
    item = QTreeWidgetItem([text, ""])
    item.setData(0, _ITEM_KIND_ROLE, "placeholder")
    item.setDisabled(True)
    tree.addTopLevelItem(item)


def _clear_tree_placeholder(tree: QTreeWidget) -> None:
    if tree.topLevelItemCount() == 1 and tree.topLevelItem(0).data(0, _ITEM_KIND_ROLE) == "placeholder":
        tree.clear()


def _add_collapsed_detail_item(tree: QTreeWidget, title: str, context: str, detail: str) -> None:
    _clear_tree_placeholder(tree)
    item = QTreeWidgetItem([title, context])
    item.setExpanded(False)
    item.addChild(QTreeWidgetItem(["Detail", detail]))
    tree.addTopLevelItem(item)


def _upsert_tool_child(item: QTreeWidgetItem, kind: str, detail: str) -> None:
    for index in range(item.childCount()):
        child = item.child(index)
        if child.data(0, _TOOL_CHILD_KIND_ROLE) == kind:
            child.setText(1, detail)
            return
    child = QTreeWidgetItem([kind, detail])
    child.setData(0, _TOOL_CHILD_KIND_ROLE, kind)
    if kind == "Call":
        item.insertChild(0, child)
        return
    item.addChild(child)


def _event_summary(event: ContextEvent) -> str:
    return f"{event.timestamp}  {event.actor}  {event.id}"


def _event_detail(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def _live_event_type(event: InspectorEvent) -> str:
    if event.title.startswith("Tool call:"):
        return "tool.call"
    if event.title.startswith("Tool result:"):
        return "tool.result"
    if event.kind == "file":
        return "file.edit"
    return "conversation.assistant_message"


def _live_tool_call_summary(event: InspectorEvent) -> tuple[str, str, str]:
    return event.title.removeprefix("Tool call:").strip() or "tool", "tool.call", str(event.metadata.get("tool_call_id") or "")


def _live_tool_result_summary(event: InspectorEvent) -> tuple[str, str, str, str]:
    match = event.title.removeprefix("Tool result:").strip().split()
    call_id = str(event.metadata.get("tool_call_id") or (match[0] if match else ""))
    status_text = match[1] if len(match) > 1 else event.tone
    status = "error" if event.tone == "error" or status_text == "error" else "success"
    return call_id or "tool", "tool.result", call_id, status


def _trace_tool_call_summary(event: ContextEvent) -> tuple[str, str, str]:
    payload = event.payload
    title = str(payload.get("tool_name") or payload.get("name") or "tool")
    return title, _event_context(event), _trace_tool_call_id(event)


def _trace_tool_result_summary(event: ContextEvent) -> tuple[str, str, str, str]:
    payload = event.payload
    status = str(payload.get("status") or "success")
    tool_call_id = str(payload.get("tool_call_id") or "")
    return tool_call_id or "tool", _event_context(event), tool_call_id, "error" if status == "error" else "success"


def _trace_tool_call_id(event: ContextEvent) -> str:
    payload = event.payload
    return str(payload.get("tool_call_id") or payload.get("id") or event.id or "")


def _tool_context(status: str, context: str) -> str:
    return f"{status}  {context}" if context else status


def _tool_item_title_is_placeholder(item: QTreeWidgetItem, call_id: str) -> bool:
    title = item.text(0).strip()
    return not title or title == "tool" or bool(call_id and title == call_id)


def _file_summary(event: ContextEvent) -> tuple[str, str]:
    raw_path = event.payload.get("path") or event.payload.get("relative_path") or event.payload.get("patch_path")
    title = Path(str(raw_path)).name if raw_path else event.type
    return title, _event_context(event)


def _event_context(event: ContextEvent) -> str:
    return f"{event.type}  {event.timestamp}"


def _config_row(key: str, value: str) -> QWidget:
    row = QWidget()
    row.setObjectName("InspectorConfigRow")
    row.setAttribute(Qt.WA_StyledBackground, True)
    layout = QVBoxLayout(row)
    layout.setContentsMargins(10, 7, 10, 7)
    layout.setSpacing(2)
    key_label = QLabel(key)
    key_label.setObjectName("InspectorConfigKey")
    value_label = QLabel(value)
    value_label.setObjectName("InspectorConfigValue")
    value_label.setWordWrap(True)
    layout.addWidget(key_label)
    layout.addWidget(value_label)
    return row


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
        "running": "Live",
        "success": "Done",
        "error": "Error",
        "ready": "Ready",
    }.get(status, "Ready")


def _tool_status_icon(status: str, phase: int = 0) -> QIcon:
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if status == "running":
        gradient = QConicalGradient(9, 9, phase * 15)
        gradient.setColorAt(0.00, QColor("#147efb"))
        gradient.setColorAt(0.35, QColor("#31d5c8"))
        gradient.setColorAt(0.70, QColor("#8b5cf6"))
        gradient.setColorAt(1.00, QColor("#147efb"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(3, 3, 12, 12)
        painter.setBrush(QColor(255, 255, 255, 210))
        painter.drawEllipse(7, 7, 4, 4)
    elif status == "error":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#e35d58"))
        painter.drawEllipse(3, 3, 12, 12)
        painter.setPen(QPen(QColor("#ffffff"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(7, 7, 11, 11)
        painter.drawLine(11, 7, 7, 11)
    else:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#3eb37b"))
        painter.drawEllipse(3, 3, 12, 12)
        painter.setPen(QPen(QColor("#ffffff"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(6, 9, 8, 11)
        painter.drawLine(8, 11, 12, 7)
    painter.end()
    return QIcon(pixmap)


def _refresh_widget(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _format_meta_lines(parts: list[str]) -> str:
    lines: list[str] = []
    for entry in parts:
        if ": " not in entry:
            lines.append(entry)
            continue
        label, value = entry.split(": ", 1)
        shortened = value if len(value) <= 34 else f"{value[:18]}...{value[-10:]}"
        lines.append(f"{label}: {shortened}")
    return "\n".join(lines)
