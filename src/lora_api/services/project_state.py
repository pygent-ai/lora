from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


def default_state_path() -> Path:
    return Path.home() / ".lora" / "gui" / "state.json"


@dataclass(slots=True)
class GuiProjectState:
    state_path: Path
    default_project_path: str | None = None
    recent_project_paths: list[str] | None = None
    collapsed_scope_ids: list[str] | None = None

    @classmethod
    def load(cls, state_path: str | Path | None = None) -> "GuiProjectState":
        path = Path(state_path or default_state_path()).expanduser().resolve()
        if not path.exists():
            return cls(state_path=path, recent_project_paths=[], collapsed_scope_ids=[])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        recent = [_resolve_project(item) for item in data.get("recent_project_paths") or [] if str(item).strip()]
        default = data.get("default_project_path")
        collapsed = [_normalize_scope_id(item) for item in data.get("collapsed_scope_ids") or []]
        return cls(
            state_path=path,
            default_project_path=_resolve_project(default) if isinstance(default, str) and default.strip() else None,
            recent_project_paths=_unique(recent),
            collapsed_scope_ids=_unique([item for item in collapsed if item]),
        )

    @property
    def state_dir(self) -> Path:
        return self.state_path.parent

    def remember_project(self, project_path: str | Path) -> None:
        resolved = _resolve_project(project_path)
        recent = [resolved]
        for item in self.recent_project_paths or []:
            normalized = _resolve_project(item)
            if normalized != resolved:
                recent.append(normalized)
        self.default_project_path = resolved
        self.recent_project_paths = recent[:12]
        self.save()

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "default_project_path": self.default_project_path,
                    "recent_project_paths": self.recent_project_paths or [],
                    "collapsed_scope_ids": self.collapsed_scope_ids or [],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True, slots=True)
class SessionScope:
    scope_id: str
    label: str
    tooltip: str
    workspace_root: str | None
    lora_root: str
    runtime_workspace_root: str


def build_session_scopes(state: GuiProjectState, *, active_workspace_root: str | None = None) -> list[SessionScope]:
    conversation_root = state.state_dir / "conversations"
    project_paths = list(state.recent_project_paths or [])
    if active_workspace_root:
        active_project = _resolve_project(active_workspace_root)
        if active_project not in project_paths:
            project_paths.insert(0, active_project)
    active_project = _resolve_project(active_workspace_root) if active_workspace_root else ""
    project_paths = [
        item
        for item in project_paths
        if item == active_project or Path(item).expanduser().exists()
    ]

    projects = [Path(item).expanduser().resolve() for item in _unique(project_paths)]
    scopes: list[SessionScope] = []
    for project in projects:
        scopes.append(
            SessionScope(
                scope_id=f"project:{project}",
                label=_project_label(project, projects),
                tooltip=str(project),
                workspace_root=str(project),
                runtime_workspace_root=str(project),
                lora_root=str((project / ".lora").resolve()),
            )
        )

    scopes.append(
        SessionScope(
            scope_id="conversation",
            label="Conversations",
            tooltip="Conversations without a project path",
            workspace_root=None,
            runtime_workspace_root=str((conversation_root / "workspace").resolve()),
            lora_root=str((conversation_root / ".lora").resolve()),
        )
    )
    return scopes


def active_project_scope_id(workspace_root: str) -> str:
    return f"project:{_resolve_project(workspace_root)}"


def _resolve_project(project_path: str | Path) -> str:
    return str(Path(project_path).expanduser().resolve())


def _normalize_scope_id(scope_id: object) -> str:
    value = str(scope_id).strip()
    if value == "conversation":
        return value
    if value.startswith("project:"):
        project = value.removeprefix("project:")
        return f"project:{_resolve_project(project)}" if project.strip() else ""
    return ""


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _project_label(project: Path, all_projects: list[Path]) -> str:
    name = project.name or str(project)
    if sum(1 for item in all_projects if item.name == name) <= 1:
        return name
    parent = project.parent.name
    return f"{name} - {parent}" if parent else name
