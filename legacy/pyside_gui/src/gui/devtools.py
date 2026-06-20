from __future__ import annotations

import os
from collections.abc import Callable

import shiboken6
from PySide6.QtCore import QObject, QEvent, QTimer
from PySide6.QtGui import QFontMetrics, QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QLayoutItem, QWidget


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
    lines.extend(chat_bubble_diagnostics(chat))
    return lines


def chat_bubble_diagnostics(chat: QWidget) -> list[str]:
    maximum_bubble_width = _chat_maximum_bubble_width(chat)
    if maximum_bubble_width <= 0:
        return []

    from gui.widgets.chat import _natural_user_bubble_width, _user_bubble_chrome_width
    from gui.widgets.chat_rows import AssistantMessageRow

    bubble_limit = int(maximum_bubble_width * 0.72)
    chrome_width = _user_bubble_chrome_width()
    lines = [
        "[ui-chat-bubble] "
        f"viewport_max={maximum_bubble_width} "
        f"user_limit_72pct={bubble_limit} "
        f"chrome_width={chrome_width} "
        f"messages_margins={_margins_text(getattr(chat, 'messages', None))}",
    ]

    for index, bubble in enumerate(chat.findChildren(QFrame, "UserBubble")):
        text_label = bubble.findChild(QLabel, "UserBubbleText")
        if text_label is None:
            continue
        raw_text = str(text_label.property("raw_text") or text_label.text() or " ")
        text_advance = QFontMetrics(text_label.font()).horizontalAdvance(raw_text)
        natural_width = _natural_user_bubble_width(text_label)
        bubble_width = min(natural_width, bubble_limit)
        inner_width = max(1, bubble_width - chrome_width)
        text_geom = text_label.geometry()
        bubble_geom = bubble.geometry()
        row = bubble.parentWidget()
        row_geom = _optional_geom_text(row)
        left_pad = text_geom.x()
        right_pad = bubble_geom.width() - (text_geom.x() + text_geom.width())
        top_pad = text_geom.y()
        bottom_pad = bubble_geom.height() - (text_geom.y() + text_geom.height())
        text_hint = text_label.sizeHint()
        lines.append(
            "[ui-chat-bubble] "
            f"user[{index}] "
            f"text_preview={_preview_text(raw_text)!r} "
            f"text_advance={text_advance} "
            f"natural={natural_width} "
            f"computed_bubble={bubble_width} "
            f"inner_width={inner_width} "
            f"label_min_max={text_label.minimumWidth()}x{text_label.maximumWidth()} "
            f"label_hint={text_hint.width()}x{text_hint.height()}"
        )
        lines.append(
            "[ui-chat-bubble] "
            f"user[{index}] "
            f"row_geom={row_geom} "
            f"bubble_geom={_geom_text(bubble)} "
            f"bubble_fixed={bubble.width()} "
            f"bubble_max={bubble.maximumWidth()} "
            f"text_geom={_rect_text(text_geom)} "
            f"inside_pad=L{left_pad} R{right_pad} T{top_pad} B{bottom_pad} "
            f"bubble_extra_w={bubble_geom.width() - text_geom.width()} "
            f"bubble_extra_h={bubble_geom.height() - text_geom.height()}"
        )

    for index, row in enumerate(chat.findChildren(AssistantMessageRow)):
        layout = row._layout
        avatar = row._avatar
        document_blocks = row.document_blocks
        chrome_width = (
            layout.contentsMargins().left()
            + layout.contentsMargins().right()
            + layout.spacing()
            + avatar.width()
        )
        available_width = max(1, maximum_bubble_width - chrome_width)
        target_width = max(180, int(available_width * 0.82))
        body_width = min(available_width, target_width)
        content_height = document_blocks._content_height_for_width(document_blocks.width())
        lines.append(
            "[ui-chat-bubble] "
            f"assistant[{index}] "
            f"chrome_width={chrome_width} "
            f"available={available_width} "
            f"target_82pct={target_width} "
            f"body_width={body_width} "
            f"content_height_for_width={content_height}"
        )
        lines.append(
            "[ui-chat-bubble] "
            f"assistant[{index}] "
            f"row_geom={_geom_text(row)} "
            f"row_fixed_h={row.height()} "
            f"avatar_geom={_geom_text(avatar)} "
            f"body_geom={_geom_text(document_blocks)} "
            f"body_fixed_w={document_blocks.width()} "
            f"body_fixed_h={document_blocks.height()} "
            f"row_extra_h={row.height() - max(avatar.height(), document_blocks.height())}"
        )
    return lines


def emit_chat_layout_diagnostics(chat: QWidget, *, reason: str = "probe") -> None:
    if not devtools_enabled() or not shiboken6.isValid(chat):
        return
    print(f"[ui-chat-layout] reason={reason}", flush=True)
    for line in chat_layout_diagnostics(chat):
        print(line, flush=True)


def attach_chat_layout_probe(chat: QWidget) -> None:
    if not devtools_enabled():
        return
    timer = QTimer(chat)
    timer.setSingleShot(True)
    timer.setInterval(0)
    timer.timeout.connect(lambda: emit_chat_layout_diagnostics(chat, reason=getattr(timer, "_reason", "probe")))
    chat._lora_layout_diag_timer = timer  # type: ignore[attr-defined]

    def schedule(reason: str) -> None:
        if not shiboken6.isValid(chat):
            return
        timer._reason = reason
        timer.start(0)

    chat._schedule_layout_diagnostics = schedule  # type: ignore[attr-defined]
    schedule("startup")


def schedule_chat_layout_diagnostics(chat: QWidget, *, reason: str = "layout") -> None:
    if not devtools_enabled():
        return
    schedule = getattr(chat, "_schedule_layout_diagnostics", None)
    if callable(schedule):
        schedule(reason)


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
    if layout is None:
        return "missing"
    margins = layout.contentsMargins()
    return f"{margins.left()},{margins.top()},{margins.right()},{margins.bottom()}"


def _chat_maximum_bubble_width(chat: QWidget) -> int:
    maximum_bubble_width = getattr(chat, "_maximum_bubble_width", None)
    if callable(maximum_bubble_width):
        return int(maximum_bubble_width())
    scroll_area = getattr(chat, "scroll_area", None)
    if scroll_area is None:
        return 0
    viewport = scroll_area.viewport()
    return max(1, viewport.width() - 64)


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
