from __future__ import annotations

from dataclasses import dataclass
import re

__all__ = [
    "InlineSpan",
    "HeadingBlockData",
    "ParagraphBlockData",
    "QuoteBlockData",
    "ListItemData",
    "ListBlockData",
    "TableBlockData",
    "HorizontalRuleBlockData",
    "CodeBlockData",
    "BlockData",
    "parse_inline_spans",
    "parse_markdown_blocks",
]


@dataclass(frozen=True, slots=True)
class InlineSpan:
    kind: str
    text: str
    href: str | None = None


@dataclass(frozen=True, slots=True)
class BlockData:
    pass


@dataclass(frozen=True, slots=True)
class HeadingBlockData(BlockData):
    level: int
    text: str
    spans: list[InlineSpan]


@dataclass(frozen=True, slots=True)
class ParagraphBlockData(BlockData):
    plain_text: str
    spans: list[InlineSpan]


@dataclass(frozen=True, slots=True)
class QuoteBlockData(BlockData):
    text: str


@dataclass(frozen=True, slots=True)
class ListItemData:
    plain_text: str
    spans: list[InlineSpan]


@dataclass(frozen=True, slots=True)
class ListBlockData(BlockData):
    ordered: bool
    items: list[ListItemData]


@dataclass(frozen=True, slots=True)
class TableBlockData(BlockData):
    headers: list[str]
    rows: list[list[str]]


@dataclass(frozen=True, slots=True)
class HorizontalRuleBlockData(BlockData):
    pass


@dataclass(frozen=True, slots=True)
class CodeBlockData(BlockData):
    language: str | None
    code: str
    closed: bool


_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_UNORDERED_LIST_RE = re.compile(r"^\s{0,3}[-*+]\s+(.*)$")
_ORDERED_LIST_RE = re.compile(r"^\s{0,3}\d+\.\s+(.*)$")
_OPEN_FENCE_RE = re.compile(r"^```(?:[ \t]*(.*?))?[ \t]*$")
_HORIZONTAL_RULE_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")


def parse_markdown_blocks(text: str) -> list[BlockData]:
    lines = text.splitlines()
    blocks: list[BlockData] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        if _is_horizontal_rule(line):
            blocks.append(HorizontalRuleBlockData())
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

        table_block, next_index = _parse_table_block(lines, index)
        if table_block is not None:
            blocks.append(table_block)
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
    return HeadingBlockData(level=level, text=text, spans=parse_inline_spans(text))


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
    items = [_list_item_data(item_text)]
    index = start_index + 1
    while index < len(lines):
        current = lines[index]
        if not current.strip():
            break
        next_item = _parse_list_item(current)
        if next_item is None or next_item[0] != ordered:
            break
        items.append(_list_item_data(next_item[1]))
        index += 1
    return ListBlockData(ordered=ordered, items=items), index


def _list_item_data(text: str) -> ListItemData:
    plain_text = text.strip()
    return ListItemData(plain_text=plain_text, spans=parse_inline_spans(plain_text))


def _parse_list_item(line: str) -> tuple[bool, str] | None:
    unordered = _UNORDERED_LIST_RE.match(line)
    if unordered:
        return False, unordered.group(1).strip()
    ordered = _ORDERED_LIST_RE.match(line)
    if ordered:
        return True, ordered.group(1).strip()
    return None


def _parse_table_block(lines: list[str], start_index: int) -> tuple[TableBlockData | None, int]:
    if start_index + 1 >= len(lines):
        return None, start_index
    header_cells = _parse_table_cells(lines[start_index])
    if header_cells is None:
        return None, start_index
    if not _is_table_separator_row(lines[start_index + 1], expected_columns=len(header_cells)):
        return None, start_index

    rows: list[list[str]] = []
    index = start_index + 2
    while index < len(lines):
        current_cells = _parse_table_cells(lines[index])
        if current_cells is None:
            break
        if len(current_cells) != len(header_cells):
            break
        rows.append(current_cells)
        index += 1
    return TableBlockData(headers=header_cells, rows=rows), index


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
    return ParagraphBlockData(plain_text=plain_text, spans=parse_inline_spans(plain_text)), index


def _starts_new_block(line: str) -> bool:
    return (
        _parse_heading(line) is not None
        or _is_horizontal_rule(line)
        or _parse_opening_fence(line) is not None
        or line.lstrip().startswith(">")
        or _parse_list_item(line) is not None
        or _parse_table_cells(line) is not None
    )


def parse_inline_spans(text: str) -> list[InlineSpan]:
    spans: list[InlineSpan] = []
    buffer: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "`":
            closing = text.find("`", index + 1)
            if closing != -1:
                _flush_text_span(buffer, spans)
                spans.append(InlineSpan(kind="code", text=text[index + 1 : closing]))
                index = closing + 1
                continue
        if text.startswith("**", index):
            closing = text.find("**", index + 2)
            if closing != -1:
                _flush_text_span(buffer, spans)
                spans.append(InlineSpan(kind="strong", text=text[index + 2 : closing]))
                index = closing + 2
                continue
        if text[index] == "*":
            closing = text.find("*", index + 1)
            if closing != -1:
                _flush_text_span(buffer, spans)
                spans.append(InlineSpan(kind="emphasis", text=text[index + 1 : closing]))
                index = closing + 1
                continue
        if text[index] == "[":
            label_end = text.find("]", index + 1)
            if label_end != -1 and label_end + 1 < len(text) and text[label_end + 1] == "(":
                href_end = text.find(")", label_end + 2)
                if href_end != -1:
                    _flush_text_span(buffer, spans)
                    spans.append(
                        InlineSpan(
                            kind="link",
                            text=text[index + 1 : label_end],
                            href=text[label_end + 2 : href_end],
                        )
                    )
                    index = href_end + 1
                    continue
        buffer.append(text[index])
        index += 1
    if buffer or not spans:
        spans.append(InlineSpan(kind="text", text="".join(buffer)))
    return spans


def _flush_text_span(buffer: list[str], spans: list[InlineSpan]) -> None:
    if not buffer:
        return
    spans.append(InlineSpan(kind="text", text="".join(buffer)))
    buffer.clear()


def _parse_table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if stripped.count("|") < 2:
        return None
    inner = stripped[1:-1] if stripped.startswith("|") and stripped.endswith("|") else stripped
    if "|" not in inner:
        return None
    return [cell.strip() for cell in inner.split("|")]


def _is_table_separator_row(line: str, *, expected_columns: int) -> bool:
    cells = _parse_table_cells(line)
    if cells is None or len(cells) != expected_columns:
        return False
    for cell in cells:
        normalized = cell.replace(":", "").replace("-", "")
        if normalized.strip():
            return False
        if "-" not in cell:
            return False
    return True


def _parse_opening_fence(line: str) -> str | None:
    match = _OPEN_FENCE_RE.match(line)
    if match is None:
        return None
    info = (match.group(1) or "").strip()
    return info


def _is_closing_fence(line: str) -> bool:
    return line.strip() == "```"


def _is_horizontal_rule(line: str) -> bool:
    return _HORIZONTAL_RULE_RE.match(line) is not None
