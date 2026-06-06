from __future__ import annotations

from dataclasses import dataclass
import re

__all__ = [
    "InlineSpan",
    "HeadingBlockData",
    "ParagraphBlockData",
    "QuoteBlockData",
    "ListBlockData",
    "CodeBlockData",
    "BlockData",
    "parse_markdown_blocks",
]


@dataclass(frozen=True, slots=True)
class InlineSpan:
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class BlockData:
    pass


@dataclass(frozen=True, slots=True)
class HeadingBlockData(BlockData):
    level: int
    text: str


@dataclass(frozen=True, slots=True)
class ParagraphBlockData(BlockData):
    plain_text: str
    spans: list[InlineSpan]


@dataclass(frozen=True, slots=True)
class QuoteBlockData(BlockData):
    text: str


@dataclass(frozen=True, slots=True)
class ListBlockData(BlockData):
    ordered: bool
    items: list[str]


@dataclass(frozen=True, slots=True)
class CodeBlockData(BlockData):
    language: str | None
    code: str
    closed: bool


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UNORDERED_LIST_RE = re.compile(r"^[-*+]\s+(.*)$")
_ORDERED_LIST_RE = re.compile(r"^\d+\.\s+(.*)$")
_OPEN_FENCE_RE = re.compile(r"^```(?:[ \t]*(.*?))?[ \t]*$")


def parse_markdown_blocks(text: str) -> list[BlockData]:
    lines = text.splitlines()
    blocks: list[BlockData] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        heading = _parse_heading(line)
        if heading is not None:
            blocks.append(heading)
            index += 1
            continue

        code_block, next_index = _parse_fenced_code_block(lines, index)
        if code_block is not None:
            blocks.append(code_block)
            index = next_index
            continue

        quote_block, next_index = _parse_quote_block(lines, index)
        if quote_block is not None:
            blocks.append(quote_block)
            index = next_index
            continue

        list_block, next_index = _parse_list_block(lines, index)
        if list_block is not None:
            blocks.append(list_block)
            index = next_index
            continue

        paragraph_block, next_index = _parse_paragraph_block(lines, index)
        blocks.append(paragraph_block)
        index = next_index

    return blocks


def _parse_heading(line: str) -> HeadingBlockData | None:
    match = _HEADING_RE.match(line)
    if not match:
        return None
    level = len(match.group(1))
    text = match.group(2).strip()
    return HeadingBlockData(level=level, text=text)


def _parse_fenced_code_block(lines: list[str], start_index: int) -> tuple[CodeBlockData | None, int]:
    opener = _parse_opening_fence(lines[start_index])
    if opener is None:
        return None, start_index

    language = opener or None
    code_lines: list[str] = []
    index = start_index + 1
    closed = False
    while index < len(lines):
        if _is_closing_fence(lines[index]):
            closed = True
            index += 1
            break
        code_lines.append(lines[index])
        index += 1
    return CodeBlockData(language=language, code="\n".join(code_lines), closed=closed), index


def _parse_quote_block(lines: list[str], start_index: int) -> tuple[QuoteBlockData | None, int]:
    if not lines[start_index].lstrip().startswith(">"):
        return None, start_index

    quote_lines: list[str] = []
    index = start_index
    while index < len(lines):
        current = lines[index]
        if not current.lstrip().startswith(">"):
            break
        stripped = current.lstrip()[1:]
        quote_lines.append(stripped.lstrip())
        index += 1
    return QuoteBlockData(text="\n".join(quote_lines).strip()), index


def _parse_list_block(lines: list[str], start_index: int) -> tuple[ListBlockData | None, int]:
    first_item = _parse_list_item(lines[start_index])
    if first_item is None:
        return None, start_index

    ordered, item_text = first_item
    items = [item_text]
    index = start_index + 1
    while index < len(lines):
        current = lines[index]
        if not current.strip():
            break
        next_item = _parse_list_item(current)
        if next_item is None or next_item[0] != ordered:
            break
        items.append(next_item[1])
        index += 1
    return ListBlockData(ordered=ordered, items=items), index


def _parse_list_item(line: str) -> tuple[bool, str] | None:
    unordered = _UNORDERED_LIST_RE.match(line)
    if unordered:
        return False, unordered.group(1).strip()
    ordered = _ORDERED_LIST_RE.match(line)
    if ordered:
        return True, ordered.group(1).strip()
    return None


def _parse_paragraph_block(lines: list[str], start_index: int) -> tuple[ParagraphBlockData, int]:
    paragraph_lines: list[str] = []
    index = start_index
    while index < len(lines):
        current = lines[index]
        if not current.strip():
            break
        if index != start_index and _starts_new_block(current):
            break
        paragraph_lines.append(current.rstrip())
        index += 1
    plain_text = "\n".join(paragraph_lines).strip()
    return ParagraphBlockData(plain_text=plain_text, spans=_parse_inline_spans(plain_text)), index


def _starts_new_block(line: str) -> bool:
    return (
        _parse_heading(line) is not None
        or _parse_opening_fence(line) is not None
        or line.lstrip().startswith(">")
        or _parse_list_item(line) is not None
    )


def _parse_inline_spans(text: str) -> list[InlineSpan]:
    spans: list[InlineSpan] = []
    buffer: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "`":
            buffer.append(char)
            index += 1
            continue
        closing = text.find("`", index + 1)
        if closing == -1:
            buffer.append(char)
            index += 1
            continue
        if buffer:
            spans.append(InlineSpan(kind="text", text="".join(buffer)))
            buffer.clear()
        spans.append(InlineSpan(kind="code", text=text[index + 1 : closing]))
        index = closing + 1
    if buffer or not spans:
        spans.append(InlineSpan(kind="text", text="".join(buffer)))
    return spans


def _parse_opening_fence(line: str) -> str | None:
    match = _OPEN_FENCE_RE.match(line)
    if match is None:
        return None
    info = (match.group(1) or "").strip()
    return info


def _is_closing_fence(line: str) -> bool:
    return line.strip() == "```"
