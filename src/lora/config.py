from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .schema import BashCliPreset, ResolvedAgentConfig, RunConfig


DEFAULT_MODEL_NAME = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_CLI_BASH_PRESETS = [
    BashCliPreset(
        name="rg",
        command="rg --help",
        description="Fast recursive text search. Prefer it for code and file text search.",
    ),
    BashCliPreset(
        name="pyright",
        command="pyright --help",
        description="Python type checker. Use it for static type validation when available.",
    ),
]


def load_run_config(
    *,
    workspace_root: str | Path | None = None,
    config_file: str | Path | None = None,
    session_id: str | None = None,
    case_file: str | Path | None = None,
    model: str | None = None,
    agent_alias: str | None = None,
    max_steps: int | None = None,
) -> RunConfig:
    root = Path(workspace_root or os.environ.get("LORA_WORKSPACE_ROOT") or Path.cwd()).expanduser().resolve()
    _load_env_file(root / ".env")
    config_path = Path(config_file or root / "lora.yaml").expanduser()
    if not config_path.is_absolute():
        config_path = root / config_path
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
        or -1
    )

    resolved_case_file = str(case_file) if case_file is not None else None
    resolved_agent = _resolve_agent_config(
        config_data=config_data,
        cli_agent_alias=agent_alias,
        cli_model=model,
    )
    return RunConfig(
        workspace_root=str(root),
        lora_root=str(lora_root),
        session_id=session_id or os.environ.get("LORA_SESSION_ID") or _dig(config_data, "session_id"),
        case_file=resolved_case_file,
        model=model or os.environ.get("LORA_MODEL") or _dig(config_data, "model") or _dig(config_data, "runtime.model"),
        max_steps=int(configured_max_steps),
        agent_alias=resolved_agent.alias,
        model_name=resolved_agent.model_name,
        api_key_source=resolved_agent.api_key_source,
        base_url=resolved_agent.base_url,
        resolved_agent=resolved_agent,
        user_identity=_non_empty(_dig(config_data, "user.identity")) or "default",
        cli_bash_presets=_resolve_cli_bash_presets(config_data),
        allow_read_outside_workspace=_bool_config(
            os.environ.get("LORA_ALLOW_READ_OUTSIDE_WORKSPACE"),
            _dig(config_data, "allow_read_outside_workspace"),
            _dig(config_data, "runtime.allow_read_outside_workspace"),
            default=True,
        ),
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


def _resolve_agent_config(
    *,
    config_data: dict[str, Any],
    cli_agent_alias: str | None,
    cli_model: str | None,
) -> ResolvedAgentConfig:
    alias = _non_empty(cli_agent_alias) or _non_empty(_dig(config_data, "agent.default_alias")) or "default"
    profile = _agent_profile(config_data, alias)
    model_request = profile.get("model_request") if isinstance(profile.get("model_request"), dict) else {}
    assert isinstance(model_request, dict)

    model_name = (
        _non_empty(cli_model)
        or _non_empty(model_request.get("model_name"))
        or _non_empty(os.environ.get("LORA_MODEL"))
        or _non_empty(os.environ.get("DEEPSEEK_MODEL"))
        or _non_empty(_dig(config_data, "model"))
        or _non_empty(_dig(config_data, "runtime.model"))
        or DEFAULT_MODEL_NAME
    )
    api_key, api_key_source = _resolve_api_key(model_request)
    base_url = (
        _non_empty(model_request.get("base_url"))
        or _non_empty(os.environ.get("DEEPSEEK_BASE_URL"))
        or _non_empty(_dig(config_data, "base_url"))
        or _non_empty(_dig(config_data, "runtime.base_url"))
        or DEFAULT_BASE_URL
    )
    return ResolvedAgentConfig(
        alias=alias,
        model_name=model_name,
        api_key=api_key,
        api_key_source=api_key_source,
        base_url=base_url,
    )


def _resolve_cli_bash_presets(config_data: dict[str, Any]) -> list[BashCliPreset]:
    presets = _dig(config_data, "cli.bash.presets")
    if presets is None:
        return list(DEFAULT_CLI_BASH_PRESETS)
    if not isinstance(presets, list):
        raise ValueError("cli.bash.presets must be a list")
    resolved: list[BashCliPreset] = []
    for index, item in enumerate(presets):
        if not isinstance(item, dict):
            raise ValueError(f"cli.bash.presets[{index}] must be a mapping")
        resolved.append(
            BashCliPreset(
                name=_non_empty(item.get("name")) or "",
                command=str(item.get("command") or ""),
                description=str(item.get("description") or ""),
            )
        )
    return resolved


def _agent_profile(config_data: dict[str, Any], alias: str) -> dict[str, Any]:
    agents = config_data.get("agents")
    if agents is None:
        if alias == "default":
            return {}
        raise ValueError(f"Agent alias {alias!r} is not configured")
    if not isinstance(agents, list):
        raise ValueError("agents must be a list")
    for item in agents:
        if isinstance(item, dict) and item.get("alias") == alias:
            return item
    raise ValueError(f"Agent alias {alias!r} is not configured")


def _resolve_api_key(model_request: dict[str, Any]) -> tuple[str | None, str]:
    api_key_env = _non_empty(model_request.get("api_key_env"))
    if api_key_env:
        value = _non_empty(os.environ.get(api_key_env))
        if value:
            return value, f"env:{api_key_env}"
    configured = _non_empty(model_request.get("api_key"))
    if configured:
        return configured, "config:model_request.api_key"
    deepseek_key = _non_empty(os.environ.get("DEEPSEEK_API_KEY"))
    if deepseek_key:
        return deepseek_key, "env:DEEPSEEK_API_KEY"
    return None, "missing"


def _non_empty(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _bool_config(*values: Any, default: bool) -> bool:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise ValueError(f"Expected boolean config value, got {value!r}")
    return default


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _parse_yaml_subset(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by lora.yaml and MVP case files."""
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
