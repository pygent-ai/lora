from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .schema import RunConfig


def load_run_config(
    *,
    workspace_root: str | Path | None = None,
    config_file: str | Path | None = None,
    session_id: str | None = None,
    case_file: str | Path | None = None,
    model: str | None = None,
    max_steps: int | None = None,
) -> RunConfig:
    root = Path(workspace_root or os.environ.get("LORA_WORKSPACE_ROOT") or Path.cwd()).expanduser().resolve()
    config_path = Path(config_file or root / "lora.yaml").expanduser()
    config_data = _read_config(config_path if config_path.exists() else None)

    configured_lora_root = _dig(config_data, "lora_root") or os.environ.get("LORA_ROOT") or ".lora"
    lora_root = Path(configured_lora_root)
    if not lora_root.is_absolute():
        lora_root = root / lora_root

    configured_max_steps = (
        max_steps
        if max_steps is not None
        else os.environ.get("LORA_MAX_STEPS")
        or _dig(config_data, "max_steps")
        or _dig(config_data, "runtime.max_steps")
        or 8
    )

    resolved_case_file = str(case_file) if case_file is not None else None
    return RunConfig(
        workspace_root=str(root),
        lora_root=str(lora_root),
        session_id=session_id or os.environ.get("LORA_SESSION_ID") or _dig(config_data, "session_id"),
        case_file=resolved_case_file,
        model=model or os.environ.get("LORA_MODEL") or _dig(config_data, "model") or _dig(config_data, "runtime.model"),
        max_steps=int(configured_max_steps),
    )


def load_mapping_file(path: str | Path) -> dict[str, Any]:
    return _parse_yaml_subset(Path(path).read_text(encoding="utf-8"))


def _read_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        return load_mapping_file(path)
    except OSError as exc:
        raise ValueError(f"Cannot read config file {path}: {exc}") from exc


def _dig(data: dict[str, Any], dotted_key: str) -> Any:
    cur: Any = data
    for key in dotted_key.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _parse_yaml_subset(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by lora.yaml and MVP case files."""
    lines = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
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


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_scalar(value: str) -> Any:
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
