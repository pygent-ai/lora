from __future__ import annotations

from typing import Any


def parse_yaml_subset(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by lora.yaml and MVP case files."""

    text = text.lstrip("\ufeff")
    lines = []
    for raw in text.splitlines():
        line = _strip_yaml_comment(raw).rstrip()
        if line.strip():
            lines.append(line)
    if not lines:
        return {}
    parsed, next_index = _parse_block(lines, 0, _indent(lines[0]))
    if next_index != len(lines):
        raise ValueError("Invalid YAML indentation")
    if not isinstance(parsed, dict):
        raise ValueError("Top-level YAML value must be a mapping")
    return parsed


def _parse_block(lines: list[str], index: int, indent: int) -> tuple[Any, int]:
    if lines[index].strip().startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_map(lines, index, indent)


def _parse_map(lines: list[str], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line = lines[index]
        current_indent = _indent(line)
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"Invalid YAML indentation: {line}")
        stripped = line.strip()
        if stripped.startswith("- "):
            break
        key, value = _split_key_value(stripped)
        if value:
            result[key] = _parse_scalar(value)
            index += 1
            continue
        if index + 1 >= len(lines) or _indent(lines[index + 1]) <= indent:
            result[key] = {}
            index += 1
            continue
        result[key], index = _parse_block(lines, index + 1, _indent(lines[index + 1]))
    return result, index


def _parse_list(lines: list[str], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line = lines[index]
        current_indent = _indent(line)
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"Invalid YAML indentation: {line}")
        stripped = line.strip()
        if not stripped.startswith("- "):
            break
        item_text = stripped[2:].strip()
        if not item_text:
            if index + 1 >= len(lines):
                result.append({})
                index += 1
            else:
                item, index = _parse_block(lines, index + 1, _indent(lines[index + 1]))
                result.append(item)
            continue
        if ":" in item_text:
            key, value = _split_key_value(item_text)
            item_dict: dict[str, Any] = {key: _parse_scalar(value)} if value else {key: {}}
            index += 1
            if index < len(lines) and _indent(lines[index]) > indent:
                nested, index = _parse_map(lines, index, _indent(lines[index]))
                item_dict.update(nested)
            result.append(item_dict)
            continue
        result.append(_parse_scalar(item_text))
        index += 1
    return result, index


def _split_key_value(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise ValueError(f"Invalid YAML line: {text}")
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"Invalid YAML key: {text}")
    return key, value.strip()


def _strip_yaml_comment(text: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if quote == '"':
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = None
            continue
        if quote == "'":
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#":
            return text[:index]
    return text


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        content = value[1:-1].strip()
        return [] if not content else [_parse_scalar(item.strip()) for item in content.split(",")]
    if value.startswith("{") and value.endswith("}"):
        content = value[1:-1].strip()
        if not content:
            return {}
        return {
            key: _parse_scalar(item)
            for key, item in (_split_key_value(part.strip()) for part in content.split(","))
        }
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


__all__ = ["parse_yaml_subset"]
