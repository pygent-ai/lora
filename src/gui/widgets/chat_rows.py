from __future__ import annotations

import re

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.chat_markdown import (
    BlockData,
    CodeBlockData,
    HeadingBlockData,
    InlineSpan,
    ListBlockData,
    ParagraphBlockData,
    QuoteBlockData,
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
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.MinimumExpanding)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        bubble = QLabel(text or " ")
        bubble.setObjectName("UserBubble")
        bubble.setWordWrap(True)
        bubble.setTextFormat(Qt.PlainText)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setProperty("format", "text")
        bubble.setProperty("raw_text", text)
        bubble.setMinimumWidth(0)
        bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)

        layout.addStretch(1)
        layout.addWidget(bubble, 0, Qt.AlignRight)
        layout.addWidget(_message_avatar("user"))


class AssistantMessageRow(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AssistantMessageGroup")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.MinimumExpanding)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.document_blocks = DocumentBlockList()
        layout.addWidget(_message_avatar("assistant"), 0, Qt.AlignTop)
        layout.addWidget(self.document_blocks, 1, Qt.AlignLeft | Qt.AlignTop)
        layout.addStretch(1)

    def render_blocks(self, blocks: list[BlockData]) -> None:
        self.document_blocks.render_blocks(blocks)


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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._layout = layout

    def render_blocks(self, blocks: list[BlockData]) -> None:
        _clear_layout(self._layout)
        for block in blocks:
            self._layout.addWidget(_build_block_widget(block))


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
    if isinstance(block, ParagraphBlockData):
        return _paragraph_block(block)
    if isinstance(block, QuoteBlockData):
        return _quote_block(block)
    if isinstance(block, ListBlockData):
        return _list_block(block)
    if isinstance(block, CodeBlockData):
        return _code_block(block)
    raise TypeError(f"Unsupported block type: {type(block)!r}")


def _heading_block(block: HeadingBlockData) -> QLabel:
    label = QLabel(block.text)
    label.setObjectName("ChatHeadingBlock")
    label.setWordWrap(True)
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setProperty("level", block.level)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    return label


def _paragraph_block(block: ParagraphBlockData) -> QWidget:
    container = QWidget()
    container.setObjectName("ChatParagraphBlock")
    container.setProperty("plain_text", block.plain_text)
    container.setMinimumWidth(0)
    container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

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
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        layout.addWidget(label)
        return container

    current_line = _inline_line_widget()
    line_layout = current_line.layout()
    assert isinstance(line_layout, _FlowLayout)

    for kind, text in _flatten_inline_segments(block.spans):
        if kind == "newline":
            if line_layout.count() > 0:
                layout.addWidget(current_line)
            current_line = _inline_line_widget()
            line_layout = current_line.layout()
            assert isinstance(line_layout, _FlowLayout)
            continue
        for widget in _inline_widgets(kind, text):
            line_layout.addWidget(widget)

    if line_layout.count() > 0 or layout.count() == 0:
        layout.addWidget(current_line)
    return container


def _quote_block(block: QuoteBlockData) -> QFrame:
    frame = QFrame()
    frame.setObjectName("ChatQuoteBlock")
    frame.setFrameShape(QFrame.NoFrame)
    frame.setMinimumWidth(0)
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 10, 14, 10)
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
    layout.setSpacing(4)

    for index, item_text in enumerate(block.items, start=1):
        row = QWidget()
        row.setObjectName("ChatListItem")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        bullet = QLabel(f"{index}." if block.ordered else "-")
        bullet.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bullet.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        text = QLabel(item_text)
        text.setWordWrap(True)
        text.setTextFormat(Qt.PlainText)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
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
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(6)

    if block.language:
        language = QLabel(block.language)
        language.setObjectName("ChatCodeBlockLanguage")
        language.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(language, 0, Qt.AlignLeft)

    text = QLabel(block.code)
    text.setObjectName("ChatCodeBlockText")
    text.setTextFormat(Qt.PlainText)
    text.setTextInteractionFlags(Qt.TextSelectableByMouse)
    text.setWordWrap(True)
    text.setMinimumWidth(0)
    layout.addWidget(text)
    return frame


def _inline_line_widget() -> QWidget:
    line = QWidget()
    line.setMinimumWidth(0)
    line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    line.setLayout(_FlowLayout(line, margin=0, h_spacing=4, v_spacing=4))
    return line


def _flatten_inline_segments(spans: list[InlineSpan]) -> list[tuple[str, str]]:
    segments: list[tuple[str, str]] = []
    for span in spans:
        parts = span.text.split("\n")
        for index, part in enumerate(parts):
            if part:
                segments.append((span.kind, part))
            if index < len(parts) - 1:
                segments.append(("newline", "\n"))
    return segments


def _inline_widgets(kind: str, text: str) -> list[QWidget]:
    if kind == "code":
        return [_inline_code_label(text)]
    widgets: list[QWidget] = []
    for token in re.findall(r"\S+|\s+", text):
        if token.isspace():
            spacer = QWidget()
            spacer.setFixedSize(_space_size(token))
            widgets.append(spacer)
        else:
            widgets.append(_paragraph_text_label(token))
    return widgets


def _paragraph_text_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("ChatParagraphText")
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
    return label


def _inline_code_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("ChatInlineCodeSpan")
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
    return label


def _space_size(token: str) -> QSize:
    width = max(4, 4 * len(token.expandtabs(4)))
    return QSize(width, 1)


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
        widget = item.widget()
        child_layout = item.layout()
        if child_layout is not None:
            _clear_layout(child_layout)
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
