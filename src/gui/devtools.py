from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget


def devtools_enabled() -> bool:
    value = os.environ.get("LORA_UI_DEBUG_HITTEST", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_widget_path(widget: QWidget) -> str:
    names: list[str] = []
    current: QObject | None = widget
    while current is not None:
        if isinstance(current, QWidget):
            segment = _widget_path_segment(current)
            if segment:
                names.append(segment)
        current = current.parent()
    return " > ".join(reversed(names))


def describe_widget_hit(widget: QWidget) -> str:
    parts = [build_widget_path(widget), type(widget).__name__]
    text_method = getattr(widget, "text", None)
    if callable(text_method):
        text = str(text_method())
        if text:
            parts.append(f'text="{_preview_text(text)}"')
    elif widget.toolTip():
        parts.append(f'tooltip="{_preview_text(widget.toolTip())}"')
    return " | ".join([parts[0], " ".join(parts[1:])])


class UiHitTestFilter(QObject):
    def __init__(self, on_hit: Callable[[str], None] | None = None):
        super().__init__()
        self._on_hit = on_hit

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override name.
        if isinstance(watched, QWidget) and isinstance(event, QMouseEvent) and event.type() == QEvent.MouseButtonPress:
            self.record_widget_hit(watched)
        return False

    def record_widget_hit(self, widget: QWidget) -> str:
        path = describe_widget_hit(widget)
        QApplication.clipboard().setText(path)
        print(f"[ui-hit] {path}", flush=True)
        if self._on_hit is not None:
            self._on_hit(path)
        return path


def install_ui_hit_test_filter(application: QApplication, *, enabled: bool | None = None) -> UiHitTestFilter | None:
    should_enable = devtools_enabled() if enabled is None else enabled
    if not should_enable:
        return None
    hit_filter = UiHitTestFilter()
    application.installEventFilter(hit_filter)
    application._lora_ui_hit_test_filter = hit_filter  # type: ignore[attr-defined]
    return hit_filter


def _preview_text(text: str, limit: int = 40) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _widget_path_segment(widget: QWidget) -> str:
    name = widget.objectName()
    if not name:
        return ""
    segment = name
    dev_id = widget.property("dev_id")
    if dev_id:
        segment = f"{segment}{{{dev_id}}}"
    index = _duplicate_sibling_index(widget)
    if index is not None:
        segment = f"{segment}[{index}]"
    return segment


def _duplicate_sibling_index(widget: QWidget) -> int | None:
    name = widget.objectName()
    parent = widget.parent()
    if not name or parent is None:
        return None
    siblings = [child for child in parent.children() if isinstance(child, QWidget) and child.objectName() == name]
    if len(siblings) <= 1:
        return None
    return siblings.index(widget) + 1
