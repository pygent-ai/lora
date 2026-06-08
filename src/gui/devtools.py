from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLayoutItem, QWidget


def devtools_enabled() -> bool:
    value = os.environ.get("LORA_UI_DEBUG_HITTEST", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def devtools_clipboard_enabled() -> bool:
    value = os.environ.get("LORA_UI_DEBUG_HITTEST_CLIPBOARD", "")
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
        if not isinstance(watched, QWidget) or not isinstance(event, QMouseEvent):
            return False
        if event.type() != QEvent.Type.MouseButtonPress:
            return False
        target = QApplication.widgetAt(event.globalPosition().toPoint())
        if target is None or watched is not target:
            return False
        self.record_widget_hit(target)
        return False

    def record_widget_hit(self, widget: QWidget) -> str:
        path = describe_widget_hit(widget)
        if devtools_clipboard_enabled():
            QApplication.clipboard().setText(path)
        print(f"[ui-hit] {path}", flush=True)
        for line in chat_layout_diagnostics(widget):
            print(line, flush=True)
        if self._on_hit is not None:
            self._on_hit(path)
        return path


def chat_layout_diagnostics(widget: QWidget) -> list[str]:
    chat = _find_chat_pane(widget)
    if chat is None:
        return []

    scroll_area = getattr(chat, "scroll_area", None)
    scroll_body = getattr(chat, "scroll_body", None)
    messages = getattr(chat, "messages", None)
    scroll_shell = getattr(chat, "scroll_shell", None)
    composer = getattr(chat, "composer", None)
    if scroll_area is None or scroll_body is None or messages is None:
        return []

    viewport = scroll_area.viewport()
    vertical_bar = scroll_area.verticalScrollBar()
    rows = _layout_items(messages)
    widget_rows = [(index, item.widget()) for index, item in rows if item.widget() is not None]
    spacer_rows = [(index, item) for index, item in rows if item.widget() is None and item.spacerItem() is not None]
    visible_widgets = [row for _, row in widget_rows if row.isVisible()]
    hidden_widgets = [row for _, row in widget_rows if not row.isVisible()]
    last_visible = visible_widgets[-1] if visible_widgets else None

    lines = [
        f"[ui-chat-layout] root=ChatPane hit={widget.objectName() or type(widget).__name__}",
        "[ui-chat-layout] "
        f"viewport={_size_text(viewport)} "
        f"scroll_body={_geom_text(scroll_body)} hint={_size_hint_text(scroll_body)} "
        f"messages_hint={_layout_size_hint_text(messages)} margins={_margins_text(messages)} "
        f"scrollbar=min:{vertical_bar.minimum()} max:{vertical_bar.maximum()} value:{vertical_bar.value()} page:{vertical_bar.pageStep()}",
        "[ui-chat-layout] "
        f"scroll_shell={_optional_geom_text(scroll_shell)} "
        f"composer={_optional_geom_text(composer)} "
        f"items={len(rows)} widgets={len(widget_rows)} visible={len(visible_widgets)} hidden={len(hidden_widgets)} "
        f"spacers={len(spacer_rows)} spacer_total={sum(item.sizeHint().height() for _, item in spacer_rows)}",
    ]
    if last_visible is not None:
        bottom = last_visible.mapTo(viewport, last_visible.rect().bottomLeft()).y()
        gap = viewport.height() - 1 - bottom
        local_bottom = last_visible.geometry().bottom()
        body_extra = scroll_body.height() - 1 - local_bottom
        lines.append(
            "[ui-chat-layout] "
            f"last_row={_row_summary(last_visible)} "
            f"local_bottom={local_bottom} body_extra_after_last={body_extra} "
            f"bottom_in_viewport={bottom} bottom_gap={gap}"
        )

    for index, item in _interesting_rows(widget_rows, spacer_rows):
        row = item.widget()
        if row is not None:
            lines.append(
                f"[ui-chat-row] index={index} item_geom={_rect_text(item.geometry())} "
                f"item_hint={_item_size_hint_text(item)} {_row_summary(row)}"
            )
        elif item.spacerItem() is not None:
            hint = item.sizeHint()
            lines.append(
                f"[ui-chat-row] index={index} spacer item_geom={_rect_text(item.geometry())} "
                f"hint={hint.width()}x{hint.height()}"
            )
    return lines


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


def _find_chat_pane(widget: QWidget) -> QWidget | None:
    current: QObject | None = widget
    while current is not None:
        if isinstance(current, QWidget) and current.objectName() == "ChatPane":
            return current
        current = current.parent()
    return None


def _layout_items(layout) -> list[tuple[int, QLayoutItem]]:
    return [(index, layout.itemAt(index)) for index in range(layout.count()) if layout.itemAt(index) is not None]


def _interesting_rows(
    widget_rows: list[tuple[int, QWidget]],
    spacer_rows: list[tuple[int, QLayoutItem]],
    *,
    limit: int = 14,
) -> list[tuple[int, QLayoutItem]]:
    combined: list[tuple[int, QLayoutItem]] = []
    for index, row in widget_rows:
        item = row.parentWidget().layout().itemAt(index) if row.parentWidget() is not None and row.parentWidget().layout() is not None else None
        if item is not None:
            combined.append((index, item))
    combined.extend(spacer_rows)
    combined.sort(key=lambda pair: pair[0])
    if len(combined) <= limit:
        return combined
    return combined[:4] + combined[-10:]


def _row_summary(row: QWidget) -> str:
    return (
        f"name={row.objectName() or type(row).__name__} "
        f"visible={row.isVisible()} "
        f"geom={_geom_text(row)} "
        f"hint={_size_hint_text(row)} "
        f"min={row.minimumWidth()}x{row.minimumHeight()} "
        f"max={row.maximumWidth()}x{row.maximumHeight()}"
    )


def _geom_text(widget: QWidget) -> str:
    geometry = widget.geometry()
    return _rect_text(geometry)


def _rect_text(rect) -> str:
    return f"{rect.x()},{rect.y()},{rect.width()}x{rect.height()}"


def _optional_geom_text(widget: QWidget | None) -> str:
    return _geom_text(widget) if widget is not None else "missing"


def _size_text(widget: QWidget) -> str:
    size = widget.size()
    return f"{size.width()}x{size.height()}"


def _size_hint_text(widget: QWidget) -> str:
    hint = widget.sizeHint()
    return f"{hint.width()}x{hint.height()}"


def _layout_size_hint_text(layout) -> str:
    hint = layout.sizeHint()
    return f"{hint.width()}x{hint.height()}"


def _item_size_hint_text(item: QLayoutItem) -> str:
    hint = item.sizeHint()
    return f"{hint.width()}x{hint.height()}"


def _margins_text(layout) -> str:
    margins = layout.contentsMargins()
    return f"{margins.left()},{margins.top()},{margins.right()},{margins.bottom()}"


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
