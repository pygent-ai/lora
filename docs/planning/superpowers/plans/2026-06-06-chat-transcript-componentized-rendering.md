# Chat Transcript Componentized Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the full chat transcript rendering path so assistant Markdown, user rows, thinking rows, and tool rows render through native Qt widget composition instead of a single rich-text `QLabel`, while preserving streaming, copy/select, tool grouping, and history replay.

**Architecture:** Keep `ChatPane` as the transcript owner and top-level message flow controller, but introduce typed row widgets plus a lightweight Markdown block parser. Assistant content is rendered through a `DocumentBlockList` widget that owns native block widgets such as headings, paragraphs, quotes, lists, and code blocks. Existing tool grouping behavior stays in place, but it is integrated as an explicit transcript row alongside the new message rows.

**Tech Stack:** Python 3.13, PySide6 widgets/layouts/signals, existing `ChatPane`, existing theme system in `src/gui/theme.py`, `pytest` offscreen GUI tests, `unittest`-style test modules already used in `tests/unit`.

---

## Scope Rules

- Preserve streaming assistant deltas.
- Preserve grouped tool rows and tool result matching.
- Preserve selectable / copyable transcript text.
- Preserve transcript order for history replay and live runtime updates.
- Remove assistant Markdown dependence on `QTextDocument -> HTML -> QLabel`.
- Keep the input composer and session sidebar behavior unchanged.
- Do not change runtime persistence formats.

## File Structure

- Create: `src/gui/widgets/chat_markdown.py`
  Responsibility: transcript-oriented Markdown block data structures and parser helpers.

- Create: `src/gui/widgets/chat_rows.py`
  Responsibility: row widgets and document block widgets for user, assistant, thinking, and notebook-style code/quote/list blocks.

- Modify: `src/gui/widgets/chat.py`
  Responsibility: replace label-centric assistant rendering with row widgets, integrate parser + row updates, keep transcript ordering / streaming / tool grouping behavior.

- Modify: `src/gui/theme.py`
  Responsibility: add styling hooks for new row/block widgets and remove old assistant-rich-text assumptions.

- Create: `tests/unit/test_gui_chat_markdown.py`
  Responsibility: parser-only tests for Markdown block extraction and incomplete streaming fragments.

- Modify: `tests/unit/test_gui_chat_widget.py`
  Responsibility: widget tests for block rendering, history replay, streaming, copy/selectability, and tool-row preservation.

---

### Task 1: Introduce a Pure Markdown Block Parser

**Files:**
- Create: `src/gui/widgets/chat_markdown.py`
- Test: `tests/unit/test_gui_chat_markdown.py`

- [ ] **Step 1: Write the failing parser tests**

Create `tests/unit/test_gui_chat_markdown.py` with:

```python
from __future__ import annotations

from gui.widgets.chat_markdown import (
    CodeBlockData,
    HeadingBlockData,
    ListBlockData,
    ParagraphBlockData,
    QuoteBlockData,
    parse_markdown_blocks,
)


def test_parse_markdown_blocks_extracts_notebook_structures() -> None:
    blocks = parse_markdown_blocks(
        "# Heading\n\nParagraph with `code`.\n\n> Quote line\n\n- one\n- two\n\n```python\nprint('hi')\n```"
    )

    assert isinstance(blocks[0], HeadingBlockData)
    assert blocks[0].level == 1
    assert blocks[0].text == "Heading"

    assert isinstance(blocks[1], ParagraphBlockData)
    assert [span.kind for span in blocks[1].inlines] == ["text", "code", "text"]

    assert isinstance(blocks[2], QuoteBlockData)
    assert blocks[2].text == "Quote line"

    assert isinstance(blocks[3], ListBlockData)
    assert blocks[3].ordered is False
    assert blocks[3].items == ["one", "two"]

    assert isinstance(blocks[4], CodeBlockData)
    assert blocks[4].language == "python"
    assert blocks[4].code == "print('hi')"


def test_parse_markdown_blocks_keeps_incomplete_fence_as_code_like_text() -> None:
    blocks = parse_markdown_blocks("```python\nprint('hi')")

    assert len(blocks) == 1
    assert isinstance(blocks[0], CodeBlockData)
    assert blocks[0].closed is False
    assert blocks[0].code == "print('hi')"


def test_parse_markdown_blocks_groups_plain_lines_into_one_paragraph() -> None:
    blocks = parse_markdown_blocks("line one\nline two\n\nline three")

    assert isinstance(blocks[0], ParagraphBlockData)
    assert blocks[0].plain_text == "line one\nline two"
    assert isinstance(blocks[1], ParagraphBlockData)
    assert blocks[1].plain_text == "line three"
```

- [ ] **Step 2: Run the new parser tests and verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_markdown.py -q
```

Expected: FAIL because `src/gui/widgets/chat_markdown.py` does not exist yet.

- [ ] **Step 3: Implement the minimal parser and data types**

Create `src/gui/widgets/chat_markdown.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class InlineSpan:
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class HeadingBlockData:
    level: int
    text: str


@dataclass(frozen=True, slots=True)
class ParagraphBlockData:
    inlines: list[InlineSpan]
    plain_text: str


@dataclass(frozen=True, slots=True)
class QuoteBlockData:
    text: str


@dataclass(frozen=True, slots=True)
class ListBlockData:
    ordered: bool
    items: list[str]


@dataclass(frozen=True, slots=True)
class CodeBlockData:
    language: str
    code: str
    closed: bool = True


BlockData = HeadingBlockData | ParagraphBlockData | QuoteBlockData | ListBlockData | CodeBlockData


def parse_markdown_blocks(text: str) -> list[BlockData]:
    lines = text.splitlines()
    index = 0
    blocks: list[BlockData] = []
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if heading := _parse_heading(stripped):
            blocks.append(heading)
            index += 1
            continue
        if stripped.startswith("```"):
            code_block, index = _parse_code_block(lines, index)
            blocks.append(code_block)
            continue
        if stripped.startswith(">"):
            quote_lines, index = _consume_prefixed_lines(lines, index, ">")
            blocks.append(QuoteBlockData(text="\n".join(quote_lines)))
            continue
        if _is_unordered_list(stripped):
            items, index = _consume_list(lines, index, ordered=False)
            blocks.append(ListBlockData(ordered=False, items=items))
            continue
        if _is_ordered_list(stripped):
            items, index = _consume_list(lines, index, ordered=True)
            blocks.append(ListBlockData(ordered=True, items=items))
            continue
        paragraph_lines, index = _consume_paragraph(lines, index)
        plain_text = "\n".join(paragraph_lines)
        blocks.append(ParagraphBlockData(inlines=_parse_inline_spans(plain_text), plain_text=plain_text))
    return blocks


def _parse_heading(text: str) -> HeadingBlockData | None:
    match = re.match(r"^(#{1,6})\s+(.*)$", text)
    if not match:
        return None
    return HeadingBlockData(level=len(match.group(1)), text=match.group(2).strip())


def _parse_code_block(lines: list[str], start: int) -> tuple[CodeBlockData, int]:
    opener = lines[start].strip()
    language = opener[3:].strip()
    code_lines: list[str] = []
    index = start + 1
    closed = False
    while index < len(lines):
        if lines[index].strip().startswith("```"):
            closed = True
            index += 1
            break
        code_lines.append(lines[index])
        index += 1
    return CodeBlockData(language=language, code="\n".join(code_lines), closed=closed), index


def _consume_prefixed_lines(lines: list[str], start: int, prefix: str) -> tuple[list[str], int]:
    values: list[str] = []
    index = start
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped.startswith(prefix):
            break
        values.append(stripped[len(prefix):].lstrip())
        index += 1
    return values, index


def _consume_list(lines: list[str], start: int, *, ordered: bool) -> tuple[list[str], int]:
    values: list[str] = []
    index = start
    while index < len(lines):
        stripped = lines[index].strip()
        if ordered and not _is_ordered_list(stripped):
            break
        if not ordered and not _is_unordered_list(stripped):
            break
        values.append(re.sub(r"^(\d+\.\s+|[-*+]\s+)", "", stripped))
        index += 1
    return values, index


def _consume_paragraph(lines: list[str], start: int) -> tuple[list[str], int]:
    values: list[str] = []
    index = start
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            break
        if (
            stripped.startswith(("```", ">", "- ", "* ", "+ "))
            or _parse_heading(stripped)
            or _is_ordered_list(stripped)
        ):
            break
        values.append(lines[index])
        index += 1
    return values, index


def _parse_inline_spans(text: str) -> list[InlineSpan]:
    parts = re.split(r"(`[^`]+`)", text)
    spans: list[InlineSpan] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("`") and part.endswith("`") and len(part) >= 2:
            spans.append(InlineSpan(kind="code", text=part[1:-1]))
        else:
            spans.append(InlineSpan(kind="text", text=part))
    return spans


def _is_unordered_list(text: str) -> bool:
    return text.startswith(("- ", "* ", "+ "))


def _is_ordered_list(text: str) -> bool:
    return bool(re.match(r"^\d+\.\s+", text))
```

- [ ] **Step 4: Run parser tests and verify they pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_markdown.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/widgets/chat_markdown.py tests/unit/test_gui_chat_markdown.py
git commit -m "feat(gui): add transcript markdown block parser"
```

---

### Task 2: Build Native Transcript Row and Block Widgets

**Files:**
- Create: `src/gui/widgets/chat_rows.py`
- Modify: `src/gui/theme.py`
- Test: `tests/unit/test_gui_chat_widget.py`

- [ ] **Step 1: Write failing widget rendering tests**

Add these tests to `tests/unit/test_gui_chat_widget.py`:

```python
    def test_assistant_markdown_renders_code_block_widget_instead_of_rich_text_label(self) -> None:
        pane = ChatPane()
        pane.add_message("assistant", "# Heading\n\n```python\nprint('hi')\n```")

        self.assertEqual(pane.findChildren(QLabel, "AssistantBubble"), [])
        headings = pane.findChildren(QWidget, "ChatHeadingBlock")
        code_blocks = pane.findChildren(QWidget, "ChatCodeBlock")
        self.assertEqual(len(headings), 1)
        self.assertEqual(len(code_blocks), 1)

    def test_assistant_markdown_blocks_remain_selectable(self) -> None:
        pane = ChatPane()
        pane.add_message("assistant", "Paragraph with `code`")

        selectable = [
            widget
            for widget in pane.findChildren(QLabel)
            if widget.objectName() in {"ChatParagraphText", "ChatInlineCodeSpan"}
        ]
        self.assertTrue(selectable)
        self.assertTrue(
            all(
                widget.textInteractionFlags() & Qt.TextSelectableByMouse
                for widget in selectable
            )
        )
```

- [ ] **Step 2: Run the widget tests and verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_widget.py -k "code_block_widget_instead_of_rich_text_label or markdown_blocks_remain_selectable" -q
```

Expected: FAIL because block widgets and new object names do not exist yet.

- [ ] **Step 3: Implement transcript row and block widgets**

Create `src/gui/widgets/chat_rows.py`:

```python
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from gui.widgets.chat_markdown import (
    CodeBlockData,
    HeadingBlockData,
    ListBlockData,
    ParagraphBlockData,
    QuoteBlockData,
)


class UserMessageRow(QFrame):
    def __init__(self, text: str, avatar: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("UserMessageRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        bubble = QLabel(text)
        bubble.setObjectName("UserBubble")
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)
        layout.addStretch(1)
        layout.addWidget(bubble, 0, Qt.AlignRight)
        layout.addWidget(avatar)


class AssistantMessageRow(QFrame):
    def __init__(self, avatar: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AssistantMessageRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.blocks = DocumentBlockList()
        layout.addWidget(avatar)
        layout.addWidget(self.blocks, 1, Qt.AlignLeft)
        layout.addStretch(1)

    def render_blocks(self, blocks: list[object]) -> None:
        self.blocks.render_blocks(blocks)


class ThinkingRow(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ThinkingRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(2)
        status_icon = QLabel("Live")
        status_icon.setObjectName("ToolStatusIcon")
        status_icon.setProperty("status", "running")
        title = QLabel("Thinking")
        title.setObjectName("ThinkingStatusTitle")
        layout.addWidget(status_icon)
        layout.addWidget(title, 1)


class DocumentBlockList(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DocumentBlockList")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(8)

    def render_blocks(self, blocks: list[object]) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for block in blocks:
            self.layout.addWidget(build_block_widget(block))


def build_block_widget(block: object) -> QWidget:
    if isinstance(block, HeadingBlockData):
        widget = QLabel(block.text)
        widget.setObjectName("ChatHeadingBlock")
        widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return widget
    if isinstance(block, ParagraphBlockData):
        container = QWidget()
        container.setObjectName("ChatParagraphBlock")
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        for span in block.inlines:
            label = QLabel(span.text)
            label.setObjectName("ChatInlineCodeSpan" if span.kind == "code" else "ChatParagraphText")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(label)
        row.addStretch(1)
        return container
    if isinstance(block, QuoteBlockData):
        label = QLabel(block.text)
        label.setObjectName("ChatQuoteBlock")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label
    if isinstance(block, ListBlockData):
        container = QWidget()
        container.setObjectName("ChatListBlock")
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        for index, item in enumerate(block.items, start=1):
            text = f"{index}. {item}" if block.ordered else f"• {item}"
            label = QLabel(text)
            label.setObjectName("ChatListItem")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            column.addWidget(label)
        return container
    if isinstance(block, CodeBlockData):
        frame = QFrame()
        frame.setObjectName("ChatCodeBlock")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(0)
        code = QLabel(block.code)
        code.setObjectName("ChatCodeBlockText")
        code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code.setWordWrap(True)
        layout.addWidget(code)
        return frame
    fallback = QLabel(str(block))
    fallback.setObjectName("ChatPlainTextBlock")
    fallback.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return fallback
```

Add these theme selectors in `src/gui/theme.py` near the transcript section:

```python
#AssistantMessageRow, #UserMessageRow, #ThinkingRow {
    background: transparent;
    border: 0;
}
#DocumentBlockList {
    background: transparent;
}
#ChatHeadingBlock {
    font-size: 25px;
    font-weight: 700;
    color: #243044;
}
#ChatParagraphText {
    color: #243044;
}
#ChatInlineCodeSpan {
    color: #1c5c98;
    background: #e9eff8;
    border-radius: 7px;
    padding: 2px 6px;
    font-family: "Consolas", "Courier New", monospace;
}
#ChatQuoteBlock {
    color: #5a6b82;
    background: #f2f6fb;
    border: 1px solid #dce4ef;
    border-radius: 10px;
    padding: 10px 12px;
}
#ChatCodeBlock {
    background: #1c2430;
    border-radius: 16px;
}
#ChatCodeBlockText {
    color: #eaf2ff;
    font-family: "Consolas", "Courier New", monospace;
}
```

- [ ] **Step 4: Run the focused widget tests and verify they pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_widget.py -k "code_block_widget_instead_of_rich_text_label or markdown_blocks_remain_selectable" -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/widgets/chat_rows.py src/gui/theme.py tests/unit/test_gui_chat_widget.py
git commit -m "feat(gui): add native transcript row widgets"
```

---

### Task 3: Integrate the New Row Widgets into ChatPane

**Files:**
- Modify: `src/gui/widgets/chat.py`
- Modify: `tests/unit/test_gui_chat_widget.py`
- Test: `tests/unit/test_gui_chat_widget.py`

- [ ] **Step 1: Write failing integration tests for history and streaming**

Add these tests to `tests/unit/test_gui_chat_widget.py`:

```python
    def test_history_assistant_message_replays_as_componentized_row(self) -> None:
        pane = ChatPane()
        pane.render_history([{"role": "assistant", "content": "# Heading\n\nBody"}])

        self.assertEqual(len(pane.findChildren(QWidget, "AssistantMessageRow")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatHeadingBlock")), 1)
        self.assertEqual(len(pane.findChildren(QLabel, "AssistantBubble")), 0)

    def test_streaming_assistant_message_rebuilds_active_block_list(self) -> None:
        pane = ChatPane()
        pane.start_assistant_message()
        pane.append_assistant_delta("# Heading")
        pane.append_assistant_delta("\n\n```python\nprint('hi')\n```")

        self.assertEqual(len(pane.findChildren(QWidget, "AssistantMessageRow")), 1)
        self.assertEqual(len(pane.findChildren(QWidget, "ChatCodeBlock")), 1)
        self.assertEqual([label.text() for label in pane.findChildren(QLabel, "ChatCodeBlockText")], ["print('hi')"])
```

- [ ] **Step 2: Run the integration tests and verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_widget.py -k "componentized_row or active_block_list" -q
```

Expected: FAIL because `ChatPane` still creates assistant `QLabel` rows.

- [ ] **Step 3: Replace assistant label rendering with block-row rendering**

In `src/gui/widgets/chat.py`, update imports:

```python
from gui.widgets.chat_markdown import parse_markdown_blocks
from gui.widgets.chat_rows import AssistantMessageRow, ThinkingRow, UserMessageRow
```

Replace `add_message()` with:

```python
    def add_message(self, role: str, content: str) -> None:
        self._remove_thinking_status()
        self._active_tool_group = None
        if role == "user":
            row = UserMessageRow(_clean_user_content(content), _message_avatar("user"))
        else:
            row = AssistantMessageRow(_message_avatar("assistant"))
            row.render_blocks(parse_markdown_blocks(content or " "))
        self.messages.insertWidget(self.messages.count() - 1, row)
        self._update_bubble_widths()
        self._scroll_to_bottom()
```

Replace `start_assistant_message()` setup:

```python
    def start_assistant_message(self) -> None:
        self._active_tool_group = None
        self._assistant_text = ""
        self._remove_thinking_status()
        self._thinking_widget = ThinkingRow()
        self.messages.insertWidget(self.messages.count() - 1, self._thinking_widget)
        self._scroll_to_bottom()
```

Replace `_create_assistant_message_row()` with:

```python
    def _create_assistant_message_row(self) -> None:
        row = AssistantMessageRow(_message_avatar("assistant"))
        row.hide()
        self._assistant_row = row
        self.messages.insertWidget(self.messages.count() - 1, row)
        self._active_tool_group = None
```

Replace `append_assistant_delta()` with:

```python
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
        self._scroll_to_bottom()
```

Replace `finish_assistant_message()` final replay:

```python
    def finish_assistant_message(self, final_answer: str | None = None) -> None:
        if final_answer and self._assistant_row is not None and final_answer != self._assistant_text:
            self._remove_thinking_status()
            self._assistant_text = final_answer
            self._assistant_row.show()
            self._assistant_row.render_blocks(parse_markdown_blocks(final_answer))
        self._assistant_row = None
        self._assistant_text = ""
```

Update the instance field definitions near `__init__`:

```python
        self._assistant_row: AssistantMessageRow | None = None
```

Delete now-obsolete helpers after migration:

```python
_configure_assistant_message_label
_set_message_bubble_text
_message_bubble_format
_looks_like_markdown_stream
_markdown_to_html
_style_markdown_html
_markdown_quote_blocks
```

- [ ] **Step 4: Run the full chat widget test file and fix regressions**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_widget.py -q
```

Expected: PASS after updating old assertions away from `AssistantBubble` and toward the new row/block widget model where appropriate.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/widgets/chat.py tests/unit/test_gui_chat_widget.py
git commit -m "feat(gui): componentize assistant transcript rendering"
```

---

### Task 4: Preserve Tool Grouping and Transcript Ordering with the New Row Model

**Files:**
- Modify: `src/gui/widgets/chat.py`
- Modify: `tests/unit/test_gui_chat_widget.py`

- [ ] **Step 1: Write failing ordering and grouping assertions against the new row model**

Update / add these tests in `tests/unit/test_gui_chat_widget.py`:

```python
    def test_history_and_runtime_tool_transcripts_render_same_component_order(self) -> None:
        history_pane = ChatPane()
        history_pane.render_history(
            [
                {"role": "assistant", "content": "thinking"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "call_read", "function": {"name": "read", "arguments": '{"description": "Read files"}'}}]},
                {"role": "tool", "content": '{"status": "success", "result": "ok"}', "tool_call_id": "call_read"},
                {"role": "assistant", "content": "first reply"},
            ]
        )

        runtime_pane = ChatPane()
        runtime_pane.start_assistant_message()
        runtime_pane.append_assistant_delta("thinking")
        runtime_pane.add_runtime_event(InspectorEvent(kind="tool", title="Tool call: read", detail='{"description": "Read files"}', tone="accent"))
        runtime_pane.add_runtime_event(InspectorEvent(kind="tool", title="Tool result: call_read success", detail="ok", tone="ok"))
        runtime_pane.append_assistant_delta("first reply")

        self.assertEqual(_visible_chat_flow(history_pane), ["thinking", "Read files", "first reply"])
        self.assertEqual(_visible_chat_flow(runtime_pane), ["thinking", "Read files", "first reply"])
```

- [ ] **Step 2: Run the ordering tests and verify failures**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_widget.py -k "component_order or visible_chat_flow" -q
```

Expected: FAIL until `_visible_chat_flow()` and chat row traversal are updated for new row widgets.

- [ ] **Step 3: Update flow helpers and preserve tool insertion boundaries**

Adjust `_visible_chat_flow()` in `tests/unit/test_gui_chat_widget.py`:

```python
def _visible_chat_flow(pane: ChatPane) -> list[str]:
    values: list[str] = []
    for index in range(pane.messages.count()):
        widget = pane.messages.itemAt(index).widget()
        if widget is None:
            continue
        values.extend(label.text() for label in widget.findChildren(QLabel, "ChatParagraphText"))
        values.extend(label.text() for label in widget.findChildren(QLabel, "ThinkingStatusTitle"))
        values.extend(label.text() for label in widget.findChildren(QLabel, "ToolStatusTitle"))
    return values
```

In `src/gui/widgets/chat.py`, preserve these boundaries:

```python
    def _close_assistant_stream(self) -> None:
        self._assistant_row = None
        self._assistant_text = ""
```

Keep `_active_tool_group = None` exactly where assistant prose should break a tool run cluster:

```python
        self._active_tool_group = None
```

Do not change `_append_tool_calls()` or `_apply_tool_result()` grouping logic beyond adapting widget types.

- [ ] **Step 4: Run the full chat widget file again**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_widget.py -q
```

Expected: PASS with transcript order preserved.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/widgets/chat.py tests/unit/test_gui_chat_widget.py
git commit -m "fix(gui): preserve tool ordering in component transcript"
```

---

### Task 5: Clean Up Width Management and Remove Rich-Text Assumptions

**Files:**
- Modify: `src/gui/widgets/chat.py`
- Modify: `src/gui/theme.py`
- Modify: `tests/unit/test_gui_chat_widget.py`

- [ ] **Step 1: Write failing width/layout regression tests for the component model**

Add these tests to `tests/unit/test_gui_chat_widget.py`:

```python
    def test_component_assistant_rows_fit_inside_viewport_without_horizontal_scroll(self) -> None:
        pane = ChatPane()
        pane.resize(1000, 700)
        pane.show()
        self.app.processEvents()

        pane.add_message("assistant", "# Heading\n\n" + ("paragraph " * 80))
        self.app.processEvents()

        self.assertEqual(pane.scroll_area.horizontalScrollBar().maximum(), 0)

    def test_code_block_uses_notebook_style_widget_names(self) -> None:
        pane = ChatPane()
        pane.add_message("assistant", "```python\nprint('hi')\n```")

        self.assertEqual(len(pane.findChildren(QWidget, "ChatCodeBlock")), 1)
        self.assertEqual(len(pane.findChildren(QLabel, "ChatCodeBlockText")), 1)
```

- [ ] **Step 2: Run the regression tests and verify failures**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_widget.py -k "fit_inside_viewport or notebook_style_widget_names" -q
```

Expected: FAIL if width logic still assumes `AssistantBubble`.

- [ ] **Step 3: Update width management for component rows**

In `src/gui/widgets/chat.py`, replace the old assistant bubble width branch in `_update_bubble_widths()` with:

```python
    def _update_bubble_widths(self) -> None:
        maximum_width = self._maximum_bubble_width()
        for bubble in self.findChildren(QLabel, "UserBubble"):
            bubble_limit = int(maximum_width * 0.72)
            bubble_width = min(_natural_label_width(bubble), bubble_limit)
            bubble.setFixedWidth(bubble_width)
            bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)

        for row in self.findChildren(QWidget, "AssistantMessageRow"):
            row.setMaximumWidth(int(maximum_width * 0.82) + 48)
            row.updateGeometry()
```

Remove helper logic that only exists for rich-text assistant labels:

```python
_sync_assistant_label_height
```

Update `src/gui/theme.py` transcript selectors to remove assumptions about `#AssistantBubble` typography being the only assistant rendering surface. Keep `#UserBubble` styling but add block-specific typography instead.

- [ ] **Step 4: Run all relevant GUI tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_markdown.py tests/unit/test_gui_chat_widget.py tests/unit/test_gui_main_window.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/widgets/chat.py src/gui/theme.py tests/unit/test_gui_chat_widget.py tests/unit/test_gui_chat_markdown.py
git commit -m "refactor(gui): finish componentized transcript migration"
```

---

### Task 6: Final Verification and Manual QA Notes

**Files:**
- Modify: `README.md` only if a short GUI note is needed; otherwise no docs change
- Verify: `src/gui/widgets/chat.py`, `src/gui/widgets/chat_rows.py`, `src/gui/widgets/chat_markdown.py`, `src/gui/theme.py`

- [ ] **Step 1: Run the full targeted test suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_markdown.py tests/unit/test_gui_chat_widget.py tests/unit/test_gui_main_window.py tests/unit/test_gui_session_sidebar.py -q
```

Expected: PASS.

- [ ] **Step 2: Manually verify transcript behavior in the GUI**

Check:

```text
1. Assistant streaming starts after the Thinking row disappears.
2. Headings, paragraphs, quotes, lists, and code blocks render as native widgets.
3. Code block text is truly monospaced and the dark surface is continuous.
4. Selecting text in paragraphs and code blocks works with mouse drag.
5. Tool groups still collapse, expand, and align correctly under assistant rows.
6. History replay produces the same order as live runtime updates.
```

- [ ] **Step 3: Fix any last-mile naming/test mismatches**

If needed, normalize widget object names in `src/gui/widgets/chat_rows.py` so tests and theme selectors match exactly:

```python
"ChatHeadingBlock"
"ChatParagraphText"
"ChatInlineCodeSpan"
"ChatQuoteBlock"
"ChatListBlock"
"ChatListItem"
"ChatCodeBlock"
"ChatCodeBlockText"
```

- [ ] **Step 4: Run tests one more time**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_gui_chat_markdown.py tests/unit/test_gui_chat_widget.py tests/unit/test_gui_main_window.py tests/unit/test_gui_session_sidebar.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/widgets/chat.py src/gui/widgets/chat_rows.py src/gui/widgets/chat_markdown.py src/gui/theme.py tests/unit/test_gui_chat_markdown.py tests/unit/test_gui_chat_widget.py
git commit -m "feat(gui): rebuild chat transcript with native block widgets"
```

