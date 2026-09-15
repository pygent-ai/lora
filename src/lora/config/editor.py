from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from pygent.llm import ModelConfig

from .loader import USER_CONFIG_FILENAME
from .yaml_subset import dump_yaml_subset, parse_yaml_subset


def replace_user_model_config(
    user_lora_root: str | Path,
    *,
    model_config: dict[str, Any],
    agents: list[dict[str, Any]],
) -> Path:
    """Replace native Pygent model settings while preserving unrelated YAML."""

    parsed = ModelConfig.from_mapping(model_config)
    if not agents:
        raise ValueError("agents must contain at least one agent")
    normalized_agents: list[dict[str, Any]] = []
    for index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise ValueError(f"agents[{index}] must be a mapping")
        alias = agent.get("alias")
        request = agent.get("model_request")
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError(f"agents[{index}].alias must be a non-empty string")
        if not isinstance(request, dict):
            raise ValueError(f"agents[{index}].model_request must be a mapping")
        group_name = request.get("default_model_group")
        if not isinstance(group_name, str) or group_name not in parsed.model_groups:
            raise ValueError(
                f"agents[{index}].model_request.default_model_group references "
                f"unknown model group {group_name!r}"
            )
        normalized_agents.append(agent)

    root = Path(user_lora_root).expanduser().resolve()
    path = root / USER_CONFIG_FILENAME
    data = parse_yaml_subset(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["models"] = model_config["models"]
    data["model_groups"] = model_config.get("model_groups", {})
    data["agents"] = normalized_agents
    default_alias = data.get("agent")
    if not isinstance(default_alias, dict) or not isinstance(
        default_alias.get("default_alias"), str
    ):
        data["agent"] = {"default_alias": normalized_agents[0]["alias"]}
    return _write_config(path, data)


def clear_user_model_config(user_lora_root: str | Path) -> Path:
    """Remove model-related settings while preserving unrelated user settings."""

    path = Path(user_lora_root).expanduser().resolve() / USER_CONFIG_FILENAME
    data = parse_yaml_subset(path.read_text(encoding="utf-8")) if path.exists() else {}
    for key in ("models", "model_groups", "agents", "agent"):
        data.pop(key, None)
    return _write_config(path, data)


def update_user_approvals(user_lora_root: str | Path, *, enabled: bool) -> Path:
    """Persist tool approval mode while preserving other runtime settings."""
    path = Path(user_lora_root).expanduser().resolve() / USER_CONFIG_FILENAME
    data = parse_yaml_subset(path.read_text(encoding="utf-8")) if path.exists() else {}
    runtime = data.setdefault("runtime", {})
    approvals = runtime.setdefault("approvals", {})
    approvals["enabled"] = enabled
    return _write_config(path, data)


def _write_config(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(dump_yaml_subset(data))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


__all__ = [
    "clear_user_model_config",
    "replace_user_model_config",
    "update_user_approvals",
]
