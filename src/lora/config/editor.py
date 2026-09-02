from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from .loader import USER_CONFIG_FILENAME
from .yaml_subset import dump_yaml_subset, parse_yaml_subset


def update_user_model_group(
    user_lora_root: str | Path,
    *,
    alias: str,
    model_request: dict[str, Any],
) -> Path:
    """Upsert one agent's model group in the user configuration atomically."""

    root = Path(user_lora_root).expanduser().resolve()
    path = root / USER_CONFIG_FILENAME
    data = parse_yaml_subset(path.read_text(encoding="utf-8")) if path.exists() else {}
    agents = data.get("agents")
    if agents is None:
        agents = []
        data["agents"] = agents
    if not isinstance(agents, list):
        raise ValueError("agents must be a list")

    replacement = {"alias": alias, "model_request": model_request}
    for index, agent in enumerate(agents):
        if isinstance(agent, dict) and agent.get("alias") == alias:
            agents[index] = replacement
            break
    else:
        agents.append(replacement)

    agent_config = data.get("agent")
    if agent_config is None:
        data["agent"] = {"default_alias": alias}

    root.mkdir(parents=True, exist_ok=True)
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


__all__ = ["update_user_model_group"]
