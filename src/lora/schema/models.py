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
class ResolvedAgentConfig:
    alias: str
    model_name: str
    api_key: str | None = None
    api_key_source: str = "missing"
    base_url: str | None = None

    def __post_init__(self) -> None:
        self.alias = _require(self.alias, "alias")
        self.model_name = _require(self.model_name, "model_name")
        if self.api_key is not None and not isinstance(self.api_key, str):
            raise ValueError("api_key must be a string or None")
        self.api_key_source = _require(self.api_key_source, "api_key_source")
        if self.base_url is not None:
            self.base_url = _require(self.base_url, "base_url")

    def safe_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "model_name": self.model_name,
            "api_key_source": self.api_key_source,
            "base_url": self.base_url,
        }


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
    ]


@dataclass(slots=True)
class RunConfig:
    workspace_root: str
    lora_root: str
    session_id: str | None = None
    case_file: str | None = None
    model: str | None = None
    max_steps: int = -1
    agent_alias: str = "default"
    model_name: str | None = None
    api_key_source: str = "missing"
    base_url: str | None = None
    resolved_agent: ResolvedAgentConfig | None = field(default=None, repr=False, compare=False)
    user_identity: str = "default"
    cli_bash_presets: list[BashCliPreset] = field(default_factory=_default_cli_bash_presets)

    def __post_init__(self) -> None:
        self.workspace_root = _abs_path(self.workspace_root)
        self.lora_root = _abs_path(self.lora_root)
        if self.case_file is not None:
            self.case_file = _abs_path(self.case_file)
        if self.max_steps != -1 and self.max_steps <= 0:
            raise ValueError("max_steps must be -1 or greater than 0")
        self.agent_alias = _require(self.agent_alias, "agent_alias")
        if self.model_name is not None:
            self.model_name = _require(self.model_name, "model_name")
        if self.base_url is not None:
            self.base_url = _require(self.base_url, "base_url")
        self.user_identity = _require(self.user_identity or "default", "user_identity")
        self.cli_bash_presets = [
            preset if isinstance(preset, BashCliPreset) else BashCliPreset(**preset)
            for preset in self.cli_bash_presets
        ]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("resolved_agent", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunConfig":
        clean = dict(data)
        clean.pop("resolved_agent", None)
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
            history=list(data.get("history") or []),
            metadata=dict(data.get("metadata") or {}),
        )
