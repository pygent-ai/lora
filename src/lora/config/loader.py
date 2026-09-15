from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pygent.llm import ModelConfig

from lora.core.io import non_empty_string as _non_empty
from lora.credentials import load_credentials
from lora.schema import (
    BashCliPreset,
    DelegationConfig,
    EternalConversationConfig,
    MCPServerConfig,
    ModelRetryConfig,
    ResolvedAgentConfig,
    RunConfig,
    RuntimeApprovalConfig,
    RuntimeCapacityConfig,
    RuntimeDurabilityConfig,
    default_cli_bash_presets,
)

from .yaml_subset import parse_yaml_subset

USER_CONFIG_FILENAME = "config.yaml"


def load_run_config(
    *,
    workspace_root: str | Path | None = None,
    session_id: str | None = None,
    case_file: str | Path | None = None,
    agent_alias: str | None = None,
    max_steps: int | None = None,
    context_window: int | None = None,
) -> RunConfig:
    root = (
        Path(workspace_root or os.environ.get("LORA_WORKSPACE_ROOT") or Path.cwd())
        .expanduser()
        .resolve()
    )
    user_lora_root = (Path.home() / ".lora").expanduser().resolve()
    load_credentials(user_lora_root=user_lora_root)
    config_path = user_lora_root / USER_CONFIG_FILENAME
    file_config = _read_config(config_path if config_path.exists() else None)
    config_data = _merge_config(_default_config(), file_config)
    _validate_config_shape(config_data)

    configured_max_steps = _int_config(
        max_steps,
        os.environ.get("LORA_MAX_STEPS"),
        _dig(config_data, "max_steps"),
        default=-1,
    )

    resolved_case_file = str(case_file) if case_file is not None else None
    alias, agent_profile = _resolve_agent_profile(
        config_data=config_data,
        cli_agent_alias=agent_alias,
    )
    model_mapping, model_config, model_status, model_error = _parse_model_config(
        config_data
    )
    resolved_agent = (
        _resolve_agent_config(
            alias=alias,
            profile=agent_profile,
            model_config=model_config,
        )
        if model_config is not None
        else None
    )
    model_request = (
        agent_profile.get("model_request")
        if isinstance(agent_profile.get("model_request"), dict)
        else {}
    )
    assert isinstance(model_request, dict)
    return RunConfig(
        workspace_root=str(root),
        session_id=session_id
        or os.environ.get("LORA_SESSION_ID")
        or _dig(config_data, "session_id"),
        case_file=resolved_case_file,
        max_steps=int(configured_max_steps),
        agent_alias=alias,
        resolved_agent=resolved_agent,
        model_config_mapping=model_mapping,
        model_config=model_config,
        model_configuration_status=model_status,
        model_configuration_error=model_error,
        user_identity=_non_empty(_dig(config_data, "user.identity")) or "default",
        cli_bash_presets=_resolve_cli_bash_presets(config_data),
        bash_full_output_allowlist=_resolve_bash_full_output_allowlist(config_data),
        allow_read_outside_workspace=_bool_config(
            os.environ.get("LORA_ALLOW_READ_OUTSIDE_WORKSPACE"),
            _dig(config_data, "allow_read_outside_workspace"),
            default=True,
        ),
        user_lora_root=str(user_lora_root),
        context_window=_optional_int_config(
            context_window,
            model_request.get("context_window"),
            os.environ.get("LORA_CONTEXT_WINDOW"),
            _dig(config_data, "context_window"),
        ),
        context_compression_enabled=_bool_config(
            os.environ.get("CONTEXT_COMPRESSION_ENABLED"),
            _dig(config_data, "context_compression.enabled"),
            default=True,
        ),
        context_compression_trigger_ratio=_float_config(
            os.environ.get("CONTEXT_COMPRESSION_TRIGGER_RATIO"),
            _dig(config_data, "context_compression.trigger_ratio"),
            default=0.9,
        ),
        context_compression_file_read_count=_int_config(
            os.environ.get("CONTEXT_COMPRESSION_FILE_READ_COUNT"),
            _dig(config_data, "context_compression.file_read_count"),
            default=5,
        ),
        context_compression_file_read_max_chars=_int_config(
            os.environ.get("CONTEXT_COMPRESSION_FILE_READ_MAX_CHARS"),
            _dig(config_data, "context_compression.file_read_max_chars"),
            default=5000,
        ),
        runtime_durability=RuntimeDurabilityConfig(
            **(_dig(config_data, "runtime.durability") or {})
        ),
        runtime_capacity=RuntimeCapacityConfig(
            **(_dig(config_data, "runtime.capacity") or {})
        ),
        runtime_approvals=_resolve_runtime_approvals(config_data),
        mcp_servers=_resolve_mcp_servers(config_data, root),
        delegation=_resolve_delegation(config_data),
        eternal_conversation=_resolve_eternal_conversation(
            config_data, alias, root
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


def _default_config() -> dict[str, Any]:
    return {
        "max_steps": -1,
        "session_id": None,
        "allow_read_outside_workspace": True,
        "context_window": None,
        "agent": {"default_alias": "default"},
        "user": {"identity": "default"},
        "cli": {
            "bash": {
                "presets": [
                    {
                        "name": preset.name,
                        "command": preset.command,
                        "description": preset.description,
                    }
                    for preset in default_cli_bash_presets()
                ],
                "full_output_allowlist": [],
            }
        },
        "context_compression": {
            "enabled": True,
            "trigger_ratio": 0.9,
            "file_read_count": 5,
            "file_read_max_chars": 5000,
        },
        "runtime": {
            "durability": {
                "mode": "preferred",
            },
            "capacity": {
                "scope": "runtime_instance",
            },
            "approvals": {
                "enabled": True,
                "timeout_seconds": 300.0,
                "preauthorized_tools": [],
            },
        },
        "mcp": {"servers": []},
        "delegation": {
            "allowed_agents": [],
            "max_depth": 4,
            "max_parallel": 4,
            "background_enabled": True,
        },
        "eternal_conversation": {
            "enabled": False,
            "extractor_agent_alias": None,
            "builder_agent_alias": None,
            "dynamic_memory_cli_path": None,
        },
    }


def _merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)  # type: ignore[arg-type]
        else:
            merged[key] = value
    return merged


def _dig(data: dict[str, Any], dotted_key: str) -> Any:
    cur: Any = data
    for key in dotted_key.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _validate_config_shape(data: dict[str, Any]) -> None:
    _require_known_keys(
        data,
        {
            "max_steps",
            "session_id",
            "allow_read_outside_workspace",
            "context_window",
            "agent",
            "agents",
            "user",
            "cli",
            "context_compression",
            "runtime",
            "mcp",
            "delegation",
            "eternal_conversation",
            "models",
            "model_groups",
        },
        "config",
    )
    _validate_mapping(data.get("agent"), {"default_alias"}, "agent")
    _validate_mapping(data.get("user"), {"identity"}, "user")
    _validate_mapping(
        data.get("context_compression"),
        {"enabled", "trigger_ratio", "file_read_count", "file_read_max_chars"},
        "context_compression",
    )
    _validate_mapping(data.get("cli"), {"bash"}, "cli")
    cli = data.get("cli")
    if isinstance(cli, dict):
        _validate_mapping(
            cli.get("bash"), {"presets", "full_output_allowlist"}, "cli.bash"
        )
        bash = cli.get("bash")
        if isinstance(bash, dict) and isinstance(bash.get("presets"), list):
            for index, preset in enumerate(bash["presets"]):
                if isinstance(preset, dict):
                    _require_known_keys(
                        preset,
                        {"name", "command", "description"},
                        f"cli.bash.presets[{index}]",
                    )
    _validate_mapping(
        data.get("runtime"), {"durability", "capacity", "approvals"}, "runtime"
    )
    runtime = data.get("runtime")
    if isinstance(runtime, dict):
        _validate_mapping(
            runtime.get("durability"), {"mode", "history_path"}, "runtime.durability"
        )
        _validate_mapping(
            runtime.get("capacity"), {"scope", "coordinator_path"}, "runtime.capacity"
        )
        _validate_mapping(
            runtime.get("approvals"),
            {"enabled", "timeout_seconds", "preauthorized_tools"},
            "runtime.approvals",
        )
    _validate_mapping(data.get("mcp"), {"servers"}, "mcp")
    mcp = data.get("mcp")
    if isinstance(mcp, dict) and isinstance(mcp.get("servers"), list):
        for index, server in enumerate(mcp["servers"]):
            if isinstance(server, dict):
                _require_known_keys(
                    server,
                    {
                        "name",
                        "transport",
                        "command",
                        "args",
                        "cwd",
                        "env_from",
                        "url",
                        "headers_env",
                        "timeout",
                        "required",
                    },
                    f"mcp.servers[{index}]",
                )
    _validate_mapping(
        data.get("delegation"),
        {"allowed_agents", "max_depth", "max_parallel", "background_enabled"},
        "delegation",
    )
    _validate_mapping(
        data.get("eternal_conversation"),
        {
            "enabled",
            "extractor_agent_alias",
            "builder_agent_alias",
            "dynamic_memory_cli_path",
        },
        "eternal_conversation",
    )
    agents = data.get("agents")
    if agents is not None and not isinstance(agents, list):
        raise ValueError("agents must be a list")
    for index, agent in enumerate(agents or []):
        if not isinstance(agent, dict):
            raise ValueError(f"agents[{index}] must be a mapping")
        _require_known_keys(agent, {"alias", "model_request"}, f"agents[{index}]")
        request = agent.get("model_request")
        if not isinstance(request, dict):
            raise ValueError(f"agents[{index}].model_request must be a mapping")
        allowed_request_fields = {"default_model_group", "retry", "context_window"}
        if {"profile", "routes", "fallback"} & set(request):
            allowed_request_fields.update({"profile", "routes", "fallback"})
        _require_known_keys(
            request, allowed_request_fields, f"agents[{index}].model_request"
        )
        retry_fields = {
            "max_attempts_per_model",
            "attempt_idle_timeout_seconds",
            "backoff_initial",
            "backoff_maximum",
            "backoff_multiplier",
        }
        if {"profile", "routes", "fallback"} & set(request):
            retry_fields.add("max_attempts_per_route")
        _validate_mapping(
            request.get("retry"),
            retry_fields,
            f"agents[{index}].model_request.retry",
        )


def _validate_mapping(value: Any, allowed: set[str], path: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a mapping")
    _require_known_keys(value, allowed, path)


def _require_known_keys(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{path} contains unknown fields: {', '.join(unknown)}")


def _resolve_agent_profile(
    *,
    config_data: dict[str, Any],
    cli_agent_alias: str | None,
) -> tuple[str, dict[str, Any]]:
    alias = (
        _non_empty(cli_agent_alias)
        or _non_empty(_dig(config_data, "agent.default_alias"))
        or "default"
    )
    return alias, _agent_profile(config_data, alias)


def _resolve_agent_config(
    *,
    alias: str,
    profile: dict[str, Any],
    model_config: ModelConfig,
) -> ResolvedAgentConfig:
    model_request = (
        profile.get("model_request")
        if isinstance(profile.get("model_request"), dict)
        else {}
    )
    assert isinstance(model_request, dict)

    default_model_group = _non_empty(model_request.get("default_model_group"))
    if default_model_group is None:
        raise ValueError(
            f"Agent alias {alias!r} requires model_request.default_model_group"
        )
    if default_model_group not in model_config.model_groups:
        raise ValueError(
            f"Agent alias {alias!r} references unknown model group "
            f"{default_model_group!r}"
        )
    retry_data = model_request.get("retry") or {}
    if not isinstance(retry_data, dict):
        raise ValueError("model_request.retry must be a mapping")
    return ResolvedAgentConfig(
        alias=alias,
        default_model_group=default_model_group,
        retry=ModelRetryConfig(**retry_data),
    )


def _parse_model_config(
    config_data: dict[str, Any],
) -> tuple[
    dict[str, Any],
    ModelConfig | None,
    Literal["configured", "unconfigured", "legacy"],
    str | None,
]:
    mapping = {
        key: config_data[key]
        for key in ("models", "model_groups")
        if key in config_data
    }
    if _contains_legacy_model_fields(config_data):
        return (
            mapping,
            None,
            "legacy",
            "legacy_model_configuration: configure native models and model_groups",
        )
    if not mapping:
        return {}, None, "unconfigured", "model_configuration_required"
    return mapping, ModelConfig.from_mapping(mapping), "configured", None


def _contains_legacy_model_fields(config_data: dict[str, Any]) -> bool:
    for agent in config_data.get("agents") or []:
        request = agent.get("model_request") if isinstance(agent, dict) else None
        if isinstance(request, dict) and {"profile", "routes", "fallback"} & set(
            request
        ):
            return True
    return False


def _runtime_path(root: Path, value: object, default: str) -> str:
    path = Path(_non_empty(value) or default).expanduser()
    return str(path if path.is_absolute() else root / path)


def _resolve_runtime_approvals(data: dict[str, Any]) -> RuntimeApprovalConfig:
    tools = _dig(data, "runtime.approvals.preauthorized_tools") or []
    if not isinstance(tools, list):
        raise ValueError("runtime.approvals.preauthorized_tools must be a list")
    return RuntimeApprovalConfig(
        enabled=_bool_config(_dig(data, "runtime.approvals.enabled"), default=True),
        timeout_seconds=_float_config(
            _dig(data, "runtime.approvals.timeout_seconds"), default=300.0
        ),
        preauthorized_tools=tuple(str(item) for item in tools),
    )


def _resolve_mcp_servers(data: dict[str, Any], root: Path) -> list[MCPServerConfig]:
    servers = _dig(data, "mcp.servers") or []
    if not isinstance(servers, list):
        raise ValueError("mcp.servers must be a list")
    resolved: list[MCPServerConfig] = []
    for index, item in enumerate(servers):
        if not isinstance(item, dict):
            raise ValueError(f"mcp.servers[{index}] must be a mapping")
        cwd = item.get("cwd")
        if cwd:
            cwd = _runtime_path(root, cwd, ".")
        resolved.append(MCPServerConfig(**{**item, "cwd": cwd}))
    return resolved


def _resolve_delegation(data: dict[str, Any]) -> DelegationConfig:
    agents = _dig(data, "delegation.allowed_agents") or []
    if not isinstance(agents, list):
        raise ValueError("delegation.allowed_agents must be a list")
    return DelegationConfig(
        allowed_agents=tuple(str(item) for item in agents),
        max_depth=(
            4
            if _dig(data, "delegation.max_depth") is None
            else int(_dig(data, "delegation.max_depth"))
        ),
        max_parallel=(
            4
            if _dig(data, "delegation.max_parallel") is None
            else int(_dig(data, "delegation.max_parallel"))
        ),
        background_enabled=_bool_config(
            _dig(data, "delegation.background_enabled"), default=True
        ),
    )


def _resolve_eternal_conversation(
    data: dict[str, Any], default_alias: str, root: Path
) -> EternalConversationConfig:
    configured_path = _dig(data, "eternal_conversation.dynamic_memory_cli_path")
    cli_path = None
    if configured_path:
        candidate = Path(str(configured_path)).expanduser()
        cli_path = str(
            (candidate if candidate.is_absolute() else root / candidate).resolve()
        )
    return EternalConversationConfig(
        enabled=_bool_config(_dig(data, "eternal_conversation.enabled"), default=False),
        extractor_agent_alias=_non_empty(
            _dig(data, "eternal_conversation.extractor_agent_alias")
        )
        or default_alias,
        builder_agent_alias=_non_empty(
            _dig(data, "eternal_conversation.builder_agent_alias")
        )
        or default_alias,
        dynamic_memory_cli_path=cli_path,
    )


def _resolve_cli_bash_presets(config_data: dict[str, Any]) -> list[BashCliPreset]:
    presets = _dig(config_data, "cli.bash.presets")
    if presets is None:
        return default_cli_bash_presets()
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


def _resolve_bash_full_output_allowlist(config_data: dict[str, Any]) -> list[str]:
    entries = _dig(config_data, "cli.bash.full_output_allowlist")
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError("cli.bash.full_output_allowlist must be a list")
    resolved: list[str] = []
    for index, item in enumerate(entries):
        value = _non_empty(item)
        if value is None:
            raise ValueError(
                f"cli.bash.full_output_allowlist[{index}] must be a non-empty string"
            )
        resolved.append(value)
    return resolved


def _agent_profile(config_data: dict[str, Any], alias: str) -> dict[str, Any]:
    agents = config_data.get("agents")
    if agents is None:
        if alias == "default":
            return {"alias": "default", "model_request": {}}
        raise ValueError(f"Agent alias {alias!r} is not configured")
    if not isinstance(agents, list):
        raise ValueError("agents must be a list")
    for item in agents:
        if isinstance(item, dict) and item.get("alias") == alias:
            return item
    raise ValueError(f"Agent alias {alias!r} is not configured")


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


def _int_config(*values: Any, default: int) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Expected integer config value, got {value!r}") from exc
    return default


def _optional_int_config(*values: Any) -> int | None:
    for value in values:
        if value is not None:
            return _int_config(value, default=0)
    return None


def _float_config(*values: Any, default: float) -> float:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Expected float config value, got {value!r}") from exc
    return default


_parse_yaml_subset = parse_yaml_subset
