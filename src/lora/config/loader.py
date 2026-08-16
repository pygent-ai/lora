from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lora.schema import (
    BashCliPreset,
    DelegationConfig,
    EternalConversationConfig,
    MCPServerConfig,
    ModelRetryConfig,
    ModelRouteConfig,
    ResolvedAgentConfig,
    RunConfig,
    RuntimeApprovalConfig,
    RuntimeCapacityConfig,
    RuntimeDurabilityConfig,
)
from lora.credentials import (
    DEFAULT_API_KEY_ENV,
    lookup_credential,
    load_credentials,
)
from .yaml_subset import parse_yaml_subset


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
    BashCliPreset(
        name="lora-chat",
        command='uv run lora chat --help',
        description=(
            'Project chat CLI. Use `uv run lora chat --new -m "<task>"` to start a new sub-agent session, '
            'or `uv run lora chat --session <session_id> -m "<task>"` to continue one.'
        ),
    ),
]


def load_run_config(
    *,
    workspace_root: str | Path | None = None,
    config_file: str | Path | None = None,
    session_id: str | None = None,
    case_file: str | Path | None = None,
    agent_alias: str | None = None,
    max_steps: int | None = None,
    context_window: int | None = None,
) -> RunConfig:
    root = Path(workspace_root or os.environ.get("LORA_WORKSPACE_ROOT") or Path.cwd()).expanduser().resolve()
    user_lora_root = (Path.home() / ".lora").expanduser().resolve()
    load_credentials(user_lora_root=user_lora_root, workspace_root=root)
    config_path = Path(config_file or root / "lora.yaml").expanduser()
    if not config_path.is_absolute():
        config_path = root / config_path
    config_data = _read_config(config_path if config_path.exists() else None)
    _validate_config_shape(config_data)

    configured_lora_root = _dig(config_data, "lora_root") or os.environ.get("LORA_ROOT") or ".lora"
    lora_root = Path(configured_lora_root)
    if not lora_root.is_absolute():
        lora_root = root / lora_root

    configured_max_steps = (
        max_steps
        if max_steps is not None
        else os.environ.get("LORA_MAX_STEPS")
        or _dig(config_data, "max_steps")
        or -1
    )

    resolved_case_file = str(case_file) if case_file is not None else None
    resolved_agent = _resolve_agent_config(
        config_data=config_data,
        cli_agent_alias=agent_alias,
    )
    agent_profile = _agent_profile(config_data, resolved_agent.alias)
    model_request = agent_profile.get("model_request") if isinstance(agent_profile.get("model_request"), dict) else {}
    assert isinstance(model_request, dict)
    return RunConfig(
        workspace_root=str(root),
        lora_root=str(lora_root),
        session_id=session_id or os.environ.get("LORA_SESSION_ID") or _dig(config_data, "session_id"),
        case_file=resolved_case_file,
        max_steps=int(configured_max_steps),
        agent_alias=resolved_agent.alias,
        resolved_agent=resolved_agent,
        user_identity=_non_empty(_dig(config_data, "user.identity")) or "default",
        cli_bash_presets=_resolve_cli_bash_presets(config_data),
        bash_full_output_allowlist=_resolve_bash_full_output_allowlist(config_data),
        allow_read_outside_workspace=_bool_config(
            os.environ.get("LORA_ALLOW_READ_OUTSIDE_WORKSPACE"),
            _dig(config_data, "allow_read_outside_workspace"),
            default=True,
        ),
        user_lora_root=str(user_lora_root),
        context_window=_int_config(
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
        runtime_durability=_resolve_runtime_durability(config_data, root),
        runtime_capacity=_resolve_runtime_capacity(config_data, root),
        runtime_approvals=_resolve_runtime_approvals(config_data),
        mcp_servers=_resolve_mcp_servers(config_data, root),
        delegation=_resolve_delegation(config_data),
        eternal_conversation=_resolve_eternal_conversation(config_data, resolved_agent.alias, root),
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


def _validate_config_shape(data: dict[str, Any]) -> None:
    _require_known_keys(
        data,
        {
            "lora_root", "max_steps", "session_id", "allow_read_outside_workspace",
            "context_window", "agent", "agents", "user", "cli", "context_compression",
            "runtime", "mcp", "delegation", "eternal_conversation",
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
        _validate_mapping(cli.get("bash"), {"presets", "full_output_allowlist"}, "cli.bash")
        bash = cli.get("bash")
        if isinstance(bash, dict) and isinstance(bash.get("presets"), list):
            for index, preset in enumerate(bash["presets"]):
                if isinstance(preset, dict):
                    _require_known_keys(preset, {"name", "command", "description"}, f"cli.bash.presets[{index}]")
    _validate_mapping(data.get("runtime"), {"durability", "capacity", "approvals"}, "runtime")
    runtime = data.get("runtime")
    if isinstance(runtime, dict):
        _validate_mapping(runtime.get("durability"), {"mode", "history_path"}, "runtime.durability")
        _validate_mapping(runtime.get("capacity"), {"scope", "coordinator_path"}, "runtime.capacity")
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
                    {"name", "transport", "command", "args", "cwd", "env_from", "url", "headers_env", "timeout", "required"},
                    f"mcp.servers[{index}]",
                )
    _validate_mapping(
        data.get("delegation"),
        {"allowed_agents", "max_depth", "max_parallel", "background_enabled"},
        "delegation",
    )
    _validate_mapping(
        data.get("eternal_conversation"),
        {"enabled", "extractor_agent_alias", "builder_agent_alias", "dynamic_memory_cli_path"},
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
        _require_known_keys(
            request,
            {"profile", "routes", "fallback", "retry", "context_window"},
            f"agents[{index}].model_request",
        )
        routes = request.get("routes")
        if isinstance(routes, list):
            for route_index, route in enumerate(routes):
                if isinstance(route, dict):
                    _require_known_keys(
                        route,
                        {"id", "provider", "model_name", "base_url", "api_key_env"},
                        f"agents[{index}].model_request.routes[{route_index}]",
                    )
        _validate_mapping(
            request.get("retry"),
            {"max_attempts_per_route", "attempt_timeout_seconds", "backoff_initial", "backoff_maximum", "backoff_multiplier"},
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


def _resolve_agent_config(
    *,
    config_data: dict[str, Any],
    cli_agent_alias: str | None,
) -> ResolvedAgentConfig:
    alias = _non_empty(cli_agent_alias) or _non_empty(_dig(config_data, "agent.default_alias")) or "default"
    profile = _agent_profile(config_data, alias)
    model_request = profile.get("model_request") if isinstance(profile.get("model_request"), dict) else {}
    assert isinstance(model_request, dict)

    routes = _resolve_model_routes(model_request)
    fallback = model_request.get("fallback")
    if fallback is None:
        fallback = [route.id for route in routes]
    if not isinstance(fallback, list):
        raise ValueError("model_request.fallback must be a list")
    retry_data = model_request.get("retry") or {}
    if not isinstance(retry_data, dict):
        raise ValueError("model_request.retry must be a mapping")
    return ResolvedAgentConfig(
        alias=alias,
        profile=_non_empty(model_request.get("profile")) or "default",
        routes=tuple(routes),
        fallback=tuple(str(item) for item in fallback),
        retry=ModelRetryConfig(**retry_data),
    )


def _resolve_model_routes(
    model_request: dict[str, Any],
) -> list[ModelRouteConfig]:
    raw_routes = model_request.get("routes")
    if raw_routes is None:
        raise ValueError("agents[].model_request.routes is required")
    if not isinstance(raw_routes, list) or not raw_routes:
        raise ValueError("model_request.routes must be a non-empty list")
    routes: list[ModelRouteConfig] = []
    for index, item in enumerate(raw_routes):
        if not isinstance(item, dict):
            raise ValueError(f"model_request.routes[{index}] must be a mapping")
        env_name = _non_empty(item.get("api_key_env")) or DEFAULT_API_KEY_ENV
        key, source = lookup_credential(env_name)
        routes.append(
            ModelRouteConfig(
                id=_required_config(item.get("id"), f"model_request.routes[{index}].id"),
                provider=_required_config(item.get("provider"), f"model_request.routes[{index}].provider"),
                model_name=_required_config(item.get("model_name"), f"model_request.routes[{index}].model_name"),
                base_url=_required_config(item.get("base_url"), f"model_request.routes[{index}].base_url"),
                api_key_env=env_name,
                api_key=key,
                api_key_source=source,
            )
        )
    return routes


def _required_config(value: object, name: str) -> str:
    resolved = _non_empty(value)
    if resolved is None:
        raise ValueError(f"{name} is required")
    return resolved


def _runtime_path(root: Path, value: object, default: str) -> str:
    path = Path(_non_empty(value) or default).expanduser()
    return str(path if path.is_absolute() else root / path)


def _resolve_runtime_durability(data: dict[str, Any], root: Path) -> RuntimeDurabilityConfig:
    mode = _non_empty(_dig(data, "runtime.durability.mode")) or "preferred"
    return RuntimeDurabilityConfig(
        mode=mode,  # type: ignore[arg-type]
        history_path=_runtime_path(
            root,
            _dig(data, "runtime.durability.history_path"),
            ".lora/runtime/executions-v1.sqlite3",
        ),
    )


def _resolve_runtime_capacity(data: dict[str, Any], root: Path) -> RuntimeCapacityConfig:
    scope = _non_empty(_dig(data, "runtime.capacity.scope")) or "runtime_instance"
    return RuntimeCapacityConfig(
        scope=scope,  # type: ignore[arg-type]
        coordinator_path=_runtime_path(
            root,
            _dig(data, "runtime.capacity.coordinator_path"),
            ".lora/runtime/capacity-v1.sqlite3",
        ),
    )


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
        max_depth=int(_dig(data, "delegation.max_depth") or 4),
        max_parallel=int(_dig(data, "delegation.max_parallel") or 4),
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
        cli_path = str((candidate if candidate.is_absolute() else root / candidate).resolve())
    return EternalConversationConfig(
        enabled=_bool_config(_dig(data, "eternal_conversation.enabled"), default=False),
        extractor_agent_alias=_non_empty(_dig(data, "eternal_conversation.extractor_agent_alias")) or default_alias,
        builder_agent_alias=_non_empty(_dig(data, "eternal_conversation.builder_agent_alias")) or default_alias,
        dynamic_memory_cli_path=cli_path,
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
            raise ValueError(f"cli.bash.full_output_allowlist[{index}] must be a non-empty string")
        resolved.append(value)
    return resolved


def _agent_profile(config_data: dict[str, Any], alias: str) -> dict[str, Any]:
    agents = config_data.get("agents")
    if agents is None:
        if alias == "default":
            return {
                "alias": "default",
                "model_request": {
                    "profile": "default",
                    "routes": [
                        {
                            "id": "primary",
                            "provider": "openai",
                            "model_name": DEFAULT_MODEL_NAME,
                            "base_url": DEFAULT_BASE_URL,
                            "api_key_env": DEFAULT_API_KEY_ENV,
                        }
                    ],
                    "fallback": ["primary"],
                },
            }
        raise ValueError(f"Agent alias {alias!r} is not configured")
    if not isinstance(agents, list):
        raise ValueError("agents must be a list")
    for item in agents:
        if isinstance(item, dict) and item.get("alias") == alias:
            return item
    raise ValueError(f"Agent alias {alias!r} is not configured")


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


def _int_config(*values: Any, default: int | None = None) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Expected integer config value, got {value!r}") from exc
    return default


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
