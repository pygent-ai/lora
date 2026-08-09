from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


def _abs_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve())


def _require(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


@dataclass(slots=True)
class ModelRouteConfig:
    id: str
    provider: str
    model_name: str
    base_url: str
    api_key_env: str
    api_key: str | None = field(default=None, repr=False, compare=False)
    api_key_source: str = "missing"

    def __post_init__(self) -> None:
        self.id = _require(self.id, "model route id")
        self.provider = _require(self.provider, "model route provider")
        self.model_name = _require(self.model_name, "model route model_name")
        self.base_url = _require(self.base_url, "model route base_url")
        self.api_key_env = _require(self.api_key_env, "model route api_key_env")
        self.api_key_source = _require(self.api_key_source, "model route api_key_source")


@dataclass(slots=True)
class ModelRetryConfig:
    max_attempts_per_route: int = 2
    attempt_timeout_seconds: float = 60.0
    backoff_initial: float = 0.5
    backoff_maximum: float = 4.0
    backoff_multiplier: float = 2.0

    def __post_init__(self) -> None:
        self.max_attempts_per_route = int(self.max_attempts_per_route)
        self.attempt_timeout_seconds = float(self.attempt_timeout_seconds)
        self.backoff_initial = float(self.backoff_initial)
        self.backoff_maximum = float(self.backoff_maximum)
        self.backoff_multiplier = float(self.backoff_multiplier)
        if self.max_attempts_per_route < 1:
            raise ValueError("max_attempts_per_route must be at least one")
        if self.attempt_timeout_seconds <= 0:
            raise ValueError("attempt_timeout_seconds must be greater than zero")
        if not 0 <= self.backoff_initial <= self.backoff_maximum:
            raise ValueError("backoff requires 0 <= initial <= maximum")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be at least one")


@dataclass(slots=True)
class ResolvedAgentConfig:
    alias: str
    profile: str = "default"
    routes: tuple[ModelRouteConfig, ...] = ()
    fallback: tuple[str, ...] = ()
    retry: ModelRetryConfig = field(default_factory=ModelRetryConfig)

    def __post_init__(self) -> None:
        self.alias = _require(self.alias, "alias")
        self.profile = _require(self.profile, "profile")
        self.routes = tuple(
            route if isinstance(route, ModelRouteConfig) else ModelRouteConfig(**route)
            for route in self.routes
        )
        self.fallback = tuple(self.fallback or (route.id for route in self.routes))
        if not self.routes:
            raise ValueError("model_request.routes must contain at least one route")
        route_ids = {route.id for route in self.routes}
        if len(route_ids) != len(self.routes):
            raise ValueError("model route ids must be unique")
        if any(route_id not in route_ids for route_id in self.fallback):
            raise ValueError("fallback references an unknown model route")
        if not isinstance(self.retry, ModelRetryConfig):
            self.retry = ModelRetryConfig(**self.retry)

    def safe_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "profile": self.profile,
            "routes": [
                {
                    "id": route.id,
                    "provider": route.provider,
                    "model_name": route.model_name,
                    "base_url": route.base_url,
                    "api_key_env": route.api_key_env,
                    "api_key_source": route.api_key_source,
                }
                for route in self.routes
            ],
            "fallback": list(self.fallback),
        }


@dataclass(slots=True)
class RuntimeDurabilityConfig:
    mode: Literal["disabled", "preferred", "required"] = "preferred"
    history_path: str = ".lora/runtime/executions-v1.sqlite3"

    def __post_init__(self) -> None:
        if self.mode not in {"disabled", "preferred", "required"}:
            raise ValueError("runtime durability mode is invalid")
        self.history_path = _require(self.history_path, "runtime history_path")


@dataclass(slots=True)
class RuntimeCapacityConfig:
    scope: Literal["runtime_instance", "deployment"] = "runtime_instance"
    coordinator_path: str = ".lora/runtime/capacity-v1.sqlite3"

    def __post_init__(self) -> None:
        if self.scope not in {"runtime_instance", "deployment"}:
            raise ValueError("runtime capacity scope is invalid")
        self.coordinator_path = _require(self.coordinator_path, "capacity coordinator_path")


@dataclass(slots=True)
class RuntimeApprovalConfig:
    enabled: bool = True
    timeout_seconds: float = 300.0
    preauthorized_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.timeout_seconds = float(self.timeout_seconds)
        if self.timeout_seconds <= 0:
            raise ValueError("approval timeout_seconds must be greater than zero")
        self.preauthorized_tools = tuple(
            _require(item, "preauthorized tool") for item in self.preauthorized_tools
        )


@dataclass(slots=True)
class MCPServerConfig:
    name: str
    transport: Literal["stdio", "sse"] = "stdio"
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env_from: tuple[str, ...] = ()
    url: str | None = None
    headers_env: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    required: bool = False

    def __post_init__(self) -> None:
        self.name = _require(self.name, "MCP server name")
        self.args = tuple(str(item) for item in self.args)
        self.env_from = tuple(_require(item, "MCP env_from item") for item in self.env_from)
        self.timeout = float(self.timeout)
        if self.timeout <= 0:
            raise ValueError("MCP timeout must be greater than zero")
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP server requires command")
        if self.transport == "sse" and not self.url:
            raise ValueError("sse MCP server requires url")


@dataclass(slots=True)
class DelegationConfig:
    allowed_agents: tuple[str, ...] = ()
    max_depth: int = 4
    max_parallel: int = 4
    background_enabled: bool = True

    def __post_init__(self) -> None:
        self.allowed_agents = tuple(_require(item, "delegation agent") for item in self.allowed_agents)
        self.max_depth = int(self.max_depth)
        self.max_parallel = int(self.max_parallel)
        if self.max_depth < 1 or self.max_parallel < 1:
            raise ValueError("delegation limits must be positive")


@dataclass(slots=True)
class BashCliPreset:
    name: str
    command: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        self.name = _require(self.name, "name")
        if self.command is None:
            self.command = ""
        if self.description is None:
            self.description = ""


def _default_cli_bash_presets() -> list[BashCliPreset]:
    return [
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


@dataclass(slots=True)
class RunConfig:
    workspace_root: str
    lora_root: str
    session_id: str | None = None
    case_file: str | None = None
    max_steps: int = -1
    agent_alias: str = "default"
    resolved_agent: ResolvedAgentConfig | None = field(default=None, repr=False, compare=False)
    user_identity: str = "default"
    cli_bash_presets: list[BashCliPreset] = field(default_factory=_default_cli_bash_presets)
    bash_full_output_allowlist: list[str] = field(default_factory=list)
    allow_read_outside_workspace: bool = True
    user_lora_root: str | None = None
    context_window: int | None = None
    context_compression_enabled: bool = True
    context_compression_trigger_ratio: float = 0.9
    context_compression_file_read_count: int = 5
    context_compression_file_read_max_chars: int = 5000
    runtime_durability: RuntimeDurabilityConfig = field(default_factory=RuntimeDurabilityConfig)
    runtime_capacity: RuntimeCapacityConfig = field(default_factory=RuntimeCapacityConfig)
    runtime_approvals: RuntimeApprovalConfig = field(default_factory=RuntimeApprovalConfig)
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    delegation: DelegationConfig = field(default_factory=DelegationConfig)

    def __post_init__(self) -> None:
        self.workspace_root = _abs_path(self.workspace_root)
        self.lora_root = _abs_path(self.lora_root)
        self.user_lora_root = _abs_path(self.user_lora_root or (Path.home() / ".lora"))
        if self.case_file is not None:
            self.case_file = _abs_path(self.case_file)
        if self.max_steps != -1 and self.max_steps <= 0:
            raise ValueError("max_steps must be -1 or greater than 0")
        if self.context_window is not None:
            self.context_window = int(self.context_window)
            if self.context_window <= 0:
                raise ValueError("context_window must be greater than 0")
        self.agent_alias = _require(self.agent_alias, "agent_alias")
        self.user_identity = _require(self.user_identity or "default", "user_identity")
        self.cli_bash_presets = [
            preset if isinstance(preset, BashCliPreset) else BashCliPreset(**preset)
            for preset in self.cli_bash_presets
        ]
        if not isinstance(self.bash_full_output_allowlist, list):
            raise ValueError("bash_full_output_allowlist must be a list")
        self.bash_full_output_allowlist = [
            _require(item, "bash_full_output_allowlist item")
            for item in self.bash_full_output_allowlist
        ]
        self.context_compression_enabled = bool(self.context_compression_enabled)
        self.context_compression_trigger_ratio = float(self.context_compression_trigger_ratio)
        if self.context_compression_trigger_ratio <= 0:
            raise ValueError("context_compression_trigger_ratio must be greater than 0")
        self.context_compression_file_read_count = int(self.context_compression_file_read_count)
        if self.context_compression_file_read_count < 0:
            raise ValueError("context_compression_file_read_count must be greater than or equal to 0")
        self.context_compression_file_read_max_chars = int(self.context_compression_file_read_max_chars)
        if self.context_compression_file_read_max_chars < 0:
            raise ValueError("context_compression_file_read_max_chars must be greater than or equal to 0")
        for name, cls in (
            ("runtime_durability", RuntimeDurabilityConfig),
            ("runtime_capacity", RuntimeCapacityConfig),
            ("runtime_approvals", RuntimeApprovalConfig),
            ("delegation", DelegationConfig),
        ):
            value = getattr(self, name)
            if not isinstance(value, cls):
                setattr(self, name, cls(**value))
        for settings, field_name in (
            (self.runtime_durability, "history_path"),
            (self.runtime_capacity, "coordinator_path"),
        ):
            path = Path(getattr(settings, field_name)).expanduser()
            if not path.is_absolute():
                path = Path(self.workspace_root) / path
            setattr(settings, field_name, str(path.resolve()))
        self.mcp_servers = [
            item if isinstance(item, MCPServerConfig) else MCPServerConfig(**item)
            for item in self.mcp_servers
        ]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["resolved_agent"] = None if self.resolved_agent is None else self.resolved_agent.safe_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunConfig":
        clean = dict(data)
        resolved = clean.get("resolved_agent")
        if isinstance(resolved, dict):
            clean["resolved_agent"] = ResolvedAgentConfig(**resolved)
        return cls(**clean)


@dataclass(slots=True)
class SessionRef:
    session_id: str
    session_dir: str
    workspace_root: str

    def __post_init__(self) -> None:
        self.session_id = _require(self.session_id, "session_id")
        self.session_dir = _abs_path(self.session_dir)
        self.workspace_root = _abs_path(self.workspace_root)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionRef":
        return cls(**data)


@dataclass(slots=True)
class CaseRunRef:
    session_id: str
    case_id: str
    case_run_id: str
    run_dir: str

    def __post_init__(self) -> None:
        self.session_id = _require(self.session_id, "session_id")
        self.case_id = _require(self.case_id, "case_id")
        self.case_run_id = _require(self.case_run_id, "case_run_id")
        self.run_dir = _abs_path(self.run_dir)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaseRunRef":
        return cls(**data)


@dataclass(slots=True)
class CaseRunResult:
    session_id: str
    case_id: str
    case_run_id: str
    status: Literal["passed", "failed", "error", "skipped"]
    final_answer: str = ""
    error: str | None = None
    event_count: int = 0
    message_count: int = 0

    def __post_init__(self) -> None:
        self.session_id = _require(self.session_id, "session_id")
        self.case_id = _require(self.case_id, "case_id")
        self.case_run_id = _require(self.case_run_id, "case_run_id")
        if self.status not in {"passed", "failed", "error", "skipped"}:
            raise ValueError("status must be one of passed, failed, error, skipped")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaseRunResult":
        return cls(**data)


@dataclass(slots=True)
class WorkspaceRef:
    workspace_root: str
    case_run_id: str
    baseline_path: str | None = None

    def __post_init__(self) -> None:
        self.workspace_root = _abs_path(self.workspace_root)
        self.case_run_id = _require(self.case_run_id, "case_run_id")
        if self.baseline_path is not None:
            self.baseline_path = _abs_path(self.baseline_path)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvaluationResult:
    status: Literal["passed", "failed", "error"]
    metrics: dict[str, Any] = field(default_factory=dict)
    verdict: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"passed", "failed", "error"}:
            raise ValueError("status must be one of passed, failed, error")
        if not isinstance(self.metrics, dict):
            raise ValueError("metrics must be a dict")
        if not isinstance(self.verdict, dict):
            raise ValueError("verdict must be a dict")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ContextEvent:
    id: str
    session_id: str
    case_id: str | None
    case_run_id: str | None
    turn_id: str | None
    type: str
    timestamp: str
    actor: Literal["user", "assistant", "tool", "system"]
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = _require(self.id, "id")
        self.session_id = _require(self.session_id, "session_id")
        self.type = _require(self.type, "type")
        self.timestamp = _require(self.timestamp, "timestamp")
        if self.actor not in {"user", "assistant", "tool", "system"}:
            raise ValueError("actor must be one of user, assistant, tool, system")
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be a dict")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextEvent":
        return cls(**data)


@dataclass(slots=True)
class CaseDefinition:
    id: str
    title: str
    type: str
    session: dict[str, Any] = field(default_factory=dict)
    workspace: dict[str, Any] = field(default_factory=dict)
    input: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = _require(self.id, "id")
        self.title = _require(self.title, "title")
        self.type = _require(self.type, "type")
        for name in ("session", "workspace", "input", "expect", "metrics"):
            if not isinstance(getattr(self, name), dict):
                raise ValueError(f"{name} must be a dict")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaseDefinition":
        return cls(
            id=data["id"],
            title=data.get("title", data["id"]),
            type=data.get("type", "e2e"),
            session=dict(data.get("session") or {}),
            workspace=dict(data.get("workspace") or {}),
            input=dict(data.get("input") or {}),
            expect=dict(data.get("expect") or {}),
            metrics=dict(data.get("metrics") or {}),
        )


@dataclass(slots=True)
class SessionSpec:
    case_id: str
    mode: str = "new"
    session_id: str | None = None
    source_session_id: str | None = None

    def __post_init__(self) -> None:
        self.case_id = _require(self.case_id, "case_id")
        if self.mode not in {"new", "resume", "fork", "shared"}:
            raise ValueError("mode must be one of new, resume, fork, shared")


@dataclass(slots=True)
class AgentSession:
    session_id: str
    workspace_root: str
    session_dir: str
    created_at: str
    updated_at: str
    system_prompt: str = ""
    status: str = "normal"
    token_usage: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.session_id = _require(self.session_id, "session_id")
        self.workspace_root = _abs_path(self.workspace_root)
        self.session_dir = _abs_path(self.session_dir)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["version"] = "1.0"
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentSession":
        return cls(
            session_id=data["session_id"],
            workspace_root=data["workspace_root"],
            session_dir=data.get("session_dir", ""),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            system_prompt=data.get("system_prompt", ""),
            status=data.get("status", "normal"),
            token_usage=dict(data.get("token_usage") or {}),
            history=list(data.get("history") or []),
            metadata=dict(data.get("metadata") or {}),
        )
