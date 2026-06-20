from __future__ import annotations

import math
from html import escape

import shiboken6
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QTextCharFormat, QTextCursor, QWheelEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.theme import UI_FONT_FAMILIES, point_size_for_pixel_size
from gui.widgets.chat_markdown import (
    BlockData,
    CodeBlockData,
    HeadingBlockData,
    HorizontalRuleBlockData,
    InlineSpan,
    ListBlockData,
    ListItemData,
    ParagraphBlockData,
    QuoteBlockData,
    TableBlockData,
    parse_inline_spans,
)

__all__ = [
    "UserMessageRow",
    "AssistantMessageRow",
    "ThinkingRow",
    "DocumentBlockList",
]


class UserMessageRow(QFrame):
    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("UserMessageGroup")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        bubble = QFrame()
        bubble.setObjectName("UserBubble")
        bubble.setFrameShape(QFrame.NoFrame)
        bubble.setMinimumWidth(0)
        bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)

        bubble_layout = QHBoxLayout(bubble)
        bubble_layout.setContentsMargins(0, 0, 0, 0)
        bubble_layout.setSpacing(0)

        text_label = QLabel(text or " ")
        text_label.setObjectName("UserBubbleText")
        text_label.setWordWrap(True)
        text_label.setTextFormat(Qt.PlainText)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        text_label.setProperty("format", "text")
        text_label.setProperty("raw_text", text)
        text_label.setMinimumWidth(0)
        text_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        bubble_layout.addWidget(text_label, 0, Qt.AlignLeft | Qt.AlignVCenter)

        self._bubble = bubble
        self._bubble_text = text_label

        layout.addStretch(1)
        layout.addWidget(bubble, 0, Qt.AlignRight)
        layout.addWidget(_message_avatar("user"))


class AssistantMessageRow(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AssistantMessageGroup")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        self._nested_in_activity = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._layout = layout
        self._avatar = _message_avatar("assistant")
        self.document_blocks = DocumentBlockList()
        layout.addWidget(self._avatar, 0, Qt.AlignTop)
        layout.addWidget(self.document_blocks, 0, Qt.AlignLeft | Qt.AlignTop)
        layout.addStretch(1)

    def render_blocks(self, blocks: list[BlockData], *, preserve_height: bool = False) -> None:
        self.document_blocks.render_blocks(blocks, preserve_height=preserve_height)
        if self._nested_in_activity:
            self._sync_nested_layout(self.document_blocks.width())
        QTimer.singleShot(0, self._sync_document_height)

    def set_nested_in_activity(self, nested: bool) -> None:
        if self._nested_in_activity == nested:
            return
        self._nested_in_activity = nested
        self.setProperty("nestedInActivity", nested)
        if nested:
            self._avatar.hide()
            self._layout.setContentsMargins(0, 0, 2, 0)
            while self._layout.count() > 2:
                item = self._layout.takeAt(self._layout.count() - 1)
                if item is not None:
                    del item
            self.document_blocks.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            self.document_blocks._preserve_height = False
            self.document_blocks._minimum_render_height = 0
            self._sync_nested_layout()
            return
        self._avatar.show()
        self._layout.setContentsMargins(0, 0, 0, 0)
        if self._layout.count() == 2:
            self._layout.addStretch(1)
        self.document_blocks.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        self.setMaximumHeight(16777215)
        self.setMinimumHeight(0)

    def set_viewport_width(self, viewport_width: int) -> None:
        if self._nested_in_activity:
            self._sync_nested_layout(viewport_width)
            return
        chrome_width = (
            self._layout.contentsMargins().left()
            + self._layout.contentsMargins().right()
            + self._layout.spacing()
            + self._avatar.width()
        )
        available_width = max(1, viewport_width - chrome_width)
        target_width = max(180, int(available_width * 0.82))
        body_width = min(available_width, target_width)
        self.document_blocks.setFixedWidth(body_width)
        self._sync_document_height()
        QTimer.singleShot(0, self._sync_document_height)

    def _sync_nested_layout(self, viewport_width: int | None = None) -> None:
        if not shiboken6.isValid(self) or not shiboken6.isValid(self.document_blocks):
            return
        if not self.document_blocks._preserve_height:
            self.document_blocks._minimum_render_height = 0
        margins = self._layout.contentsMargins().left() + self._layout.contentsMargins().right()
        max_width = max(1, (viewport_width if viewport_width is not None else 16777215) - margins)
        body_width = min(max_width, max(180, int(max_width * 0.82)))
        self.document_blocks.setMaximumWidth(max_width)
        self.document_blocks.setFixedWidth(body_width)
        self.document_blocks.sync_height_for_width(body_width)
        content_height = max(1, self.document_blocks.height())
        self.document_blocks.setFixedHeight(content_height)
        self.setFixedHeight(content_height)
        self.updateGeometry()

    def _sync_document_height(self) -> None:
        if not shiboken6.isValid(self) or not shiboken6.isValid(self.document_blocks):
            return
        self.document_blocks.sync_height_for_width(self.document_blocks.width())
        if self._nested_in_activity:
            content_height = max(1, self.document_blocks._content_height_for_width(self.document_blocks.width()))
            self.document_blocks.setFixedHeight(content_height)
            self.setFixedHeight(content_height)
        else:
            row_height = max(self._avatar.height(), self.document_blocks.height())
            self.setFixedHeight(row_height)
        self.updateGeometry()


class ThinkingRow(QFrame):
    def __init__(self, title: str = "Thinking", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ToolStatusRow")
        self.setProperty("status", "running")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(2)

        status_icon = QLabel("Live")
        status_icon.setObjectName("ToolStatusIcon")
        status_icon.setProperty("status", "running")
        status_icon.setAlignment(Qt.AlignCenter)
        status_icon.setFixedSize(42, 22)

        title_label = QLabel(title)
        title_label.setObjectName("ThinkingStatusTitle")
        title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout.addWidget(status_icon)
        layout.addWidget(title_label, 1)


class DocumentBlockList(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DocumentBlockList")
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self._layout = layout
        self._minimum_render_height = 0
        self._preserve_height = False
        self._last_sync_width = 0

    def render_blocks(self, blocks: list[BlockData], *, preserve_height: bool = False) -> None:
        self._preserve_height = preserve_height
        if preserve_height:
            self._minimum_render_height = max(self._minimum_render_height, self.height())
        else:
            self._minimum_render_height = 0
        _clear_layout(self._layout)
        for block in blocks:
            self._layout.addWidget(_build_block_widget(block))
        if self.width() > 0:
            self.sync_height_for_width(self.width())
        self.updateGeometry()
        if self.parentWidget() is not None:
            self.parentWidget().updateGeometry()

    def sync_height_for_width(self, width: int) -> None:
        effective_width = max(1, width)
        if effective_width != self._last_sync_width and not self._preserve_height:
            self._minimum_render_height = 0
        self._last_sync_width = effective_width

        self._layout.activate()
        self._sync_inline_paragraph_heights(effective_width)
        height = self._content_height_for_width(effective_width)
        if self._preserve_height:
            effective_height = max(1, height, self._minimum_render_height)
            self._minimum_render_height = max(self._minimum_render_height, effective_height)
        else:
            effective_height = max(1, height)
        self.setFixedHeight(effective_height)
        self.updateGeometry()

    def _sync_inline_paragraph_heights(self, width: int) -> None:
        for view in self.findChildren(_InlineParagraphView):
            view.sync_height(width)

    def _content_height_for_width(self, width: int) -> int:
        margins = self._layout.contentsMargins()
        height = margins.top() + margins.bottom()
        visible_count = 0
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            if item is None:
                continue
            if visible_count:
                height += max(0, self._layout.spacing())
            if item.hasHeightForWidth():
                item_height = item.heightForWidth(width)
            else:
                item_height = item.sizeHint().height()
            height += max(1, item_height)
            visible_count += 1
        return height


class _FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, margin: int = 0, h_spacing: int = 4, v_spacing: int = 4):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        margins = self.contentsMargins()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width()
            if line_height > 0 and next_x > effective.right() + 1:
                x = effective.x()
                y += line_height + self._v_spacing
                next_x = x + hint.width()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x + self._h_spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


def _build_block_widget(block: BlockData) -> QWidget:
    if isinstance(block, HeadingBlockData):
        return _heading_block(block)
    if isinstance(block, HorizontalRuleBlockData):
        return _horizontal_rule_block()
    if isinstance(block, ParagraphBlockData):
        return _paragraph_block(block)
    if isinstance(block, QuoteBlockData):
        return _quote_block(block)
    if isinstance(block, ListBlockData):
        return _list_block(block)
    if isinstance(block, TableBlockData):
        return _table_block(block)
    if isinstance(block, CodeBlockData):
        return _code_block(block)
    raise TypeError(f"Unsupported block type: {type(block)!r}")


def _heading_block(block: HeadingBlockData) -> QWidget:
    if all(span.kind == "text" for span in block.spans):
        label = QLabel(block.text)
        label.setObjectName("ChatHeadingBlock")
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("level", block.level)
        label.setProperty("plain_text", block.text)
        label.setFont(_heading_font(label.font(), block.level))
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        return label
    label = QLabel(_rich_inline_html(block.spans))
    label.setObjectName("ChatHeadingBlock")
    label.setWordWrap(True)
    label.setTextFormat(Qt.RichText)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setProperty("level", block.level)
    label.setProperty("plain_text", "".join(span.text for span in block.spans))
    label.setFont(_heading_font(label.font(), block.level))
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    return label


def _horizontal_rule_block() -> QFrame:
    frame = QFrame()
    frame.setObjectName("ChatHorizontalRuleBlock")
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    frame.setFixedHeight(12)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 5, 0, 6)
    layout.setSpacing(0)
    line = QFrame(frame)
    line.setObjectName("ChatHorizontalRuleLine")
    line.setFrameShape(QFrame.NoFrame)
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    layout.addWidget(line)
    return frame


def _paragraph_block(block: ParagraphBlockData) -> QWidget:
    container = QWidget()
    container.setObjectName("ChatParagraphBlock")
    container.setProperty("plain_text", block.plain_text)
    container.setMinimumWidth(0)
    container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)

    if all(span.kind == "text" for span in block.spans):
        label = QLabel(block.plain_text)
        label.setObjectName("ChatParagraphText")
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout.addWidget(label, 0, Qt.AlignTop)
        return container

    layout.addWidget(_formatted_paragraph_view(block), 0, Qt.AlignTop)
    return container


def _quote_block(block: QuoteBlockData) -> QFrame:
    frame = QFrame()
    frame.setObjectName("ChatQuoteBlock")
    frame.setFrameShape(QFrame.NoFrame)
    frame.setMinimumWidth(0)
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.setSpacing(0)

    label = QLabel(block.text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setMinimumWidth(0)
    layout.addWidget(label)
    return frame


def _list_block(block: ListBlockData) -> QWidget:
    container = QWidget()
    container.setObjectName("ChatListBlock")
    container.setMinimumWidth(0)
    container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)

    for index, item in enumerate(block.items, start=1):
        row = QWidget()
        row.setObjectName("ChatListItem")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(7)

        bullet = QLabel(f"{index}." if block.ordered else "")
        bullet.setObjectName("ChatListMarker")
        bullet.setProperty("ordered", block.ordered)
        bullet.setFixedWidth(18)
        bullet.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        bullet.setTextInteractionFlags(Qt.NoTextInteraction)

        text = _list_item_text_widget(item)
        text.setMinimumWidth(0)

        row_layout.addWidget(bullet, 0, Qt.AlignTop)
        row_layout.addWidget(text, 1)
        layout.addWidget(row)
    return container


def _code_block(block: CodeBlockData) -> QFrame:
    frame = QFrame()
    frame.setObjectName("ChatCodeBlock")
    frame.setFrameShape(QFrame.NoFrame)
    frame.setMinimumWidth(0)
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(5)

    if block.language:
        language = QLabel(block.language)
        language.setObjectName("ChatCodeBlockLanguage")
        language.setTextInteractionFlags(Qt.TextSelectableByMouse)
        language.setFont(_code_block_font(language.font(), bold=True))
        layout.addWidget(language, 0, Qt.AlignLeft)

    text = QLabel(block.code)
    text.setObjectName("ChatCodeBlockText")
    text.setTextFormat(Qt.PlainText)
    text.setTextInteractionFlags(Qt.TextSelectableByMouse)
    text.setWordWrap(True)
    text.setMinimumWidth(0)
    text.setFont(_code_block_font(text.font()))
    layout.addWidget(text)
    return frame


def _table_block(block: TableBlockData) -> QFrame:
    frame = QFrame()
    frame.setObjectName("ChatTableBlock")
    frame.setFrameShape(QFrame.NoFrame)
    frame.setMinimumWidth(0)
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)

    layout.addWidget(_table_row(block.headers, header=True))
    for row_cells in block.rows:
        layout.addWidget(_table_row(row_cells, header=False))
    return frame


def _table_row(cells: list[str], *, header: bool) -> QWidget:
    row = QWidget()
    row.setObjectName("ChatTableHeaderRow" if header else "ChatTableDataRow")
    row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)

    for cell_text in cells:
        layout.addWidget(_table_cell_widget(cell_text, header=header), 1)
    return row


def _table_cell_widget(cell_text: str, *, header: bool) -> QWidget:
    spans = parse_inline_spans(cell_text)
    display_text = _inline_display_text(spans, fallback=cell_text)
    if all(span.kind == "text" for span in spans):
        label = QLabel(display_text)
        label.setObjectName("ChatTableCell")
        label.setProperty("header", header)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        return label

    cell = _formatted_text_view(spans, plain_text=display_text, object_name="ChatTableCell")
    cell.setProperty("header", header)
    cell.setMinimumWidth(0)
    cell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return cell


def _formatted_paragraph_view(block: ParagraphBlockData) -> QTextEdit:
    return _formatted_text_view(block.spans, plain_text=block.plain_text, object_name="ChatParagraphText")


def _list_item_text_widget(item: ListItemData) -> QWidget:
    display_text = _inline_display_text(item.spans, fallback=item.plain_text)
    if all(span.kind == "text" for span in item.spans):
        label = QLabel(display_text)
        label.setObjectName("ChatListItemText")
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("plain_text", display_text)
        return label
    return _formatted_text_view(item.spans, plain_text=display_text, object_name="ChatListItemText")


def _inline_display_text(spans: list[InlineSpan], *, fallback: str) -> str:
    if all(span.kind == "text" for span in spans):
        return fallback
    return "".join(span.text for span in spans)


def _formatted_text_view(spans: list[InlineSpan], *, plain_text: str, object_name: str) -> QTextEdit:
    view = _InlineParagraphView()
    view.setObjectName(object_name)
    view.setProperty("plain_text", plain_text)
    cursor = view.textCursor()

    for span in spans:
        cursor.insertText(span.text, _inline_char_format(view, span))
    view.setTextCursor(cursor)
    view.moveCursor(QTextCursor.MoveOperation.Start)
    view.ensureCursorVisible()
    view.sync_height()
    return view


def _rich_inline_html(spans: list[InlineSpan]) -> str:
    html_parts: list[str] = []
    for span in spans:
        text = escape(span.text)
        if span.kind == "code":
            html_parts.append(f"<code>{text}</code>")
        elif span.kind == "strong":
            html_parts.append(f"<strong>{text}</strong>")
        elif span.kind == "emphasis":
            html_parts.append(f"<em>{text}</em>")
        elif span.kind == "link":
            html_parts.append(f'<a href="{escape(span.href or span.text)}">{text}</a>')
        else:
            html_parts.append(text)
    return "".join(html_parts)


def _inline_char_format(widget: QWidget, span: InlineSpan) -> QTextCharFormat:
    if span.kind == "code":
        return _inline_code_char_format(widget)
    if span.kind == "strong":
        return _inline_font_format(weight=QFont.Weight.Bold)
    if span.kind == "emphasis":
        return _inline_font_format(italic=True)
    if span.kind == "link":
        return _inline_link_char_format(widget)
    return QTextCharFormat()


def _inline_code_char_format(widget: QWidget) -> QTextCharFormat:
    is_dark = widget.palette().window().color().lightness() < 128
    code_format = QTextCharFormat()
    code_format.setFontFamilies(list(UI_FONT_FAMILIES))
    code_format.setFontFixedPitch(False)
    code_format.setBackground(QColor("#252b35" if is_dark else "#f8f9fb"))
    code_format.setForeground(QColor("#b2bac7" if is_dark else "#66707f"))
    return code_format


def _inline_font_format(*, weight: QFont.Weight | None = None, italic: bool = False) -> QTextCharFormat:
    text_format = QTextCharFormat()
    if weight is not None:
        text_format.setFontWeight(weight)
    text_format.setFontItalic(italic)
    return text_format


def _inline_link_char_format(widget: QWidget) -> QTextCharFormat:
    is_dark = widget.palette().window().color().lightness() < 128
    text_format = QTextCharFormat()
    text_format.setAnchor(True)
    text_format.setFontUnderline(True)
    text_format.setForeground(QColor("#b2bac7" if is_dark else "#66707f"))
    return text_format


_TEXT_HEIGHT_SAFETY_MARGIN = 4


def _heading_font(base_font: QFont, level: int) -> QFont:
    font = QFont(base_font)
    base_size = _font_pixel_size(font)
    size_offsets = {1: 5, 2: 3, 3: 2, 4: 1, 5: 0, 6: 0}
    font.setPointSizeF(point_size_for_pixel_size(base_size + size_offsets.get(level, 0)))
    font.setWeight(QFont.Weight.Bold if level <= 2 else QFont.Weight.DemiBold)
    return font


def _font_pixel_size(font: QFont) -> float:
    if font.pixelSize() > 0:
        return float(font.pixelSize())
    if font.pointSizeF() > 0:
        return font.pointSizeF() * 96 / 72
    return 13.0


def _code_block_font(base_font: QFont, *, bold: bool = False) -> QFont:
    font = QFont(base_font)
    font.setFamilies(list(UI_FONT_FAMILIES))
    font.setFixedPitch(False)
    font.setKerning(False)
    if bold:
        font.setBold(True)
    return font


def _natural_document_width(document_blocks: DocumentBlockList, max_width: int) -> int:
    layout = document_blocks.layout()
    if layout is None or layout.count() == 0:
        return max_width
    natural = 0
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget() if item is not None else None
        if widget is not None:
            natural = max(natural, _block_natural_width(widget, max_width))
    if natural <= 0:
        return max_width
    return max(1, min(max_width, natural))


def _block_natural_width(block: QWidget, max_width: int) -> int:
    if block.objectName() in {"ChatCodeBlock", "ChatTableBlock", "ChatListBlock", "ChatQuoteBlock"}:
        return max_width
    plain_text = block.property("plain_text")
    if isinstance(plain_text, str) and plain_text.strip():
        text_widget = block if isinstance(block, (QLabel, QTextEdit)) else None
        if text_widget is None:
            text_widget = block.findChild(QTextEdit) or block.findChild(QLabel)
        if text_widget is not None:
            metrics = QFontMetrics(text_widget.font())
            return metrics.horizontalAdvance(plain_text.replace("\n", " "))
    if isinstance(block, QLabel):
        metrics = QFontMetrics(block.font())
        return metrics.horizontalAdvance(block.text())
    return block.sizeHint().width()


def _message_avatar(role: str) -> QLabel:
    avatar = QLabel("U" if role == "user" else "L")
    avatar.setAlignment(Qt.AlignCenter)
    avatar.setObjectName("UserAvatar" if role == "user" else "AssistantAvatar")
    avatar.setFixedSize(36, 36)
    return avatar


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        child_layout = item.layout()
        if child_layout is not None:
            _clear_layout(child_layout)
            continue
        widget = item.widget()
        if widget is not None:
            widget.hide()
            if shiboken6.isValid(widget):
                shiboken6.delete(widget)


class _InlineParagraphView(QTextEdit):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ChatParagraphText")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self.document().setDocumentMargin(0)
        self.document().contentsChanged.connect(self.sync_height)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        super().resizeEvent(event)
        self.sync_height()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt override.
        event.ignore()

    def sync_height(self, width: int | None = None) -> None:
        text_width = max(0, width if width is not None else self.viewport().width())
        self.document().setTextWidth(text_width)
        height = self.document().documentLayout().documentSize().height()
        vertical_margins = self.contentsMargins().top() + self.contentsMargins().bottom() + (self.frameWidth() * 2)
        self.setFixedHeight(int(math.ceil(height + vertical_margins + _TEXT_HEIGHT_SAFETY_MARGIN)))
