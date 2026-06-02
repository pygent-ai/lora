from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
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
from lora.trace import DESIGN_EVENT_TYPES, EventStore


class TraceInspector(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TraceInspector")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 24, 22, 24)
        layout.setSpacing(14)

        header = QWidget()
        header.setObjectName("InspectorHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        header_text = QVBoxLayout()
        header_text.setContentsMargins(0, 0, 0, 0)
        header_text.setSpacing(3)
        self.status = QLabel("Ready")
        self.status.setObjectName("InspectorStatus")
        self.ids = QLabel("")
        self.ids.setObjectName("InspectorMeta")
        self.ids.setWordWrap(True)
        self.status_indicator = QLabel("Ready")
        self.status_indicator.setObjectName("InspectorStatusIndicator")
        self.status_indicator.setProperty("status", "ready")
        self.status_indicator.setAlignment(Qt.AlignCenter)
        self.status_indicator.setFixedWidth(76)
        header_text.addWidget(self.status)
        header_text.addWidget(self.ids)
        header_layout.addLayout(header_text, 1)
        header_layout.addWidget(self.status_indicator)

        self.tabs = QTabWidget()
        self.events = QTreeWidget()
        self.events.setHeaderLabels(["Event type", "Detail"])
        self.events.setRootIsDecorated(True)
        self.events.setAlternatingRowColors(True)
        self.events.setColumnWidth(0, 190)
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
        self._set_empty_states()

    def reset_context(self, *, session_id: str | None = None, case_run_id: str | None = None) -> None:
        self.clear_trace_events()
        self.tools.clear()
        self.files.clear()
        self.set_status("Ready")
        parts = []
        if session_id:
            parts.append(f"Session: {session_id}")
        if case_run_id:
            parts.append(f"Run: {case_run_id}")
        self.ids.setText("\n".join(parts))
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
            self._add_tool_item(*_live_tool_summary(event), _event_detail({"title": event.title, "detail": event.detail, "tone": event.tone}))
        if event.kind == "file":
            self._add_file_item(event.title, event.kind, _event_detail({"title": event.title, "detail": event.detail, "tone": event.tone}))

    def clear_trace_events(self) -> None:
        self.events.clear()
        self._event_type_items = {}
        for event_type in sorted(DESIGN_EVENT_TYPES):
            item = QTreeWidgetItem([event_type, ""])
            item.setData(0, 256, event_type)
            item.setExpanded(False)
            self.events.addTopLevelItem(item)
            self._event_type_items[event_type] = item

    def add_trace_event(self, event: ContextEvent) -> None:
        group = self.event_type_item(event.type)
        if group is None:
            return
        child = QTreeWidgetItem([_event_summary(event), _event_detail(event.to_dict())])
        child.setData(0, 256, event.type)
        child.setData(0, 257, event.id)
        group.addChild(child)
        group.setExpanded(False)
        if event.type == "tool.call" or event.type == "tool.result":
            self._add_tool_item(*_tool_summary(event), _event_detail(event.to_dict()))
        if event.type.startswith("file.") or event.type == "diff.created":
            self._add_file_item(*_file_summary(event), _event_detail(event.to_dict()))

    def load_trace_events_from_run_dir(self, run_dir: str | Path) -> None:
        self.clear_trace_events()
        self.tools.clear()
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

    def _add_tool_item(self, title: str, context: str, detail: str) -> None:
        _add_collapsed_detail_item(self.tools, title, context, detail)

    def _add_file_item(self, title: str, context: str, detail: str) -> None:
        _add_collapsed_detail_item(self.files, title, context, detail)

    def _add_live_event(self, event: InspectorEvent) -> None:
        event_type = _live_event_type(event)
        group = self.event_type_item(event_type)
        if group is None:
            return
        child = QTreeWidgetItem([event.title, _event_detail({"title": event.title, "detail": event.detail, "tone": event.tone})])
        child.setData(0, 256, event_type)
        group.addChild(child)


def _trace_tree(headers: list[str]) -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderLabels(headers)
    tree.setRootIsDecorated(True)
    tree.setAlternatingRowColors(True)
    tree.setColumnWidth(0, 170)
    return tree


def _set_tree_placeholder(tree: QTreeWidget, text: str) -> None:
    if tree.topLevelItemCount():
        return
    item = QTreeWidgetItem([text, ""])
    item.setData(0, 257, "placeholder")
    item.setDisabled(True)
    tree.addTopLevelItem(item)


def _clear_tree_placeholder(tree: QTreeWidget) -> None:
    if tree.topLevelItemCount() == 1 and tree.topLevelItem(0).data(0, 257) == "placeholder":
        tree.clear()


def _add_collapsed_detail_item(tree: QTreeWidget, title: str, context: str, detail: str) -> None:
    _clear_tree_placeholder(tree)
    item = QTreeWidgetItem([title, context])
    item.setExpanded(False)
    item.addChild(QTreeWidgetItem(["Detail", detail]))
    tree.addTopLevelItem(item)


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


def _live_tool_summary(event: InspectorEvent) -> tuple[str, str]:
    if event.title.startswith("Tool call:"):
        return event.title.removeprefix("Tool call:").strip() or "tool", "tool.call"
    if event.title.startswith("Tool result:"):
        return event.title.removeprefix("Tool result:").strip() or "tool", "tool.result"
    return event.title or "tool", event.kind


def _tool_summary(event: ContextEvent) -> tuple[str, str]:
    payload = event.payload
    if event.type == "tool.call":
        title = str(payload.get("tool_name") or payload.get("name") or "tool")
    else:
        status = str(payload.get("status") or "result")
        tool_call_id = str(payload.get("tool_call_id") or "tool")
        title = f"{tool_call_id} {status}"
    return title, _event_context(event)


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


def _refresh_widget(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
