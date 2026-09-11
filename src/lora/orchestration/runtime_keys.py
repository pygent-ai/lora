from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from lora.schema import RunConfig


@dataclass(frozen=True, slots=True)
class RuntimeScopeKey:
    workspace_root: str
    lora_root: str

    @classmethod
    def from_config(cls, config: RunConfig) -> "RuntimeScopeKey":
        return cls(
            workspace_root=str(Path(config.workspace_root).resolve()),
            lora_root=str(Path(config.lora_root).resolve()),
        )


@dataclass(frozen=True, slots=True)
class RuntimeGenerationKey:
    scope: RuntimeScopeKey
    config_fingerprint: str

    @classmethod
    def from_config(cls, config: RunConfig) -> "RuntimeGenerationKey":
        payload = config.to_dict()
        if config.resolved_agent is not None:
            payload["resolved_route_credentials"] = [
                {
                    "id": route.id,
                    "api_key": route.api_key,
                }
                for route in config.resolved_agent.routes
            ]
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            scope=RuntimeScopeKey.from_config(config),
            config_fingerprint=hashlib.sha256(encoded).hexdigest(),
        )


__all__ = ["RuntimeGenerationKey", "RuntimeScopeKey"]
