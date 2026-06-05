from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import shutil

from lora.io import read_json
from lora.io import validate_path_id
from lora.schema import RunConfig
from lora.session import SessionManager


GUI_STATE_PATH = Path.home() / ".lora" / "gui" / "state.json"


@dataclass(slots=True)
class GuiProjectState:
    state_path: Path
    default_project_path: str | None = None
    recent_project_paths: list[str] | None = None

    @classmethod
    def load(cls, state_path: str | Path | None = None) -> "GuiProjectState":
        path = Path(state_path or GUI_STATE_PATH).expanduser().resolve()
        if not path.exists():
            return cls(state_path=path, recent_project_paths=[])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        recent = [str(Path(item).expanduser().resolve()) for item in data.get("recent_project_paths") or [] if str(item).strip()]
        default = data.get("default_project_path")
        resolved_default = str(Path(default).expanduser().resolve()) if isinstance(default, str) and default.strip() else None
        return cls(state_path=path, default_project_path=resolved_default, recent_project_paths=recent)

    @property
    def state_dir(self) -> Path:
        return self.state_path.parent

    def remember_project(self, project_path: str | Path) -> None:
        resolved = str(Path(project_path).expanduser().resolve())
        recent = [resolved]
        for item in self.recent_project_paths or []:
            normalized = str(Path(item).expanduser().resolve())
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

    def to_run_config(self) -> RunConfig:
        return RunConfig(workspace_root=self.runtime_workspace_root, lora_root=self.lora_root)


def build_session_scopes(state: GuiProjectState) -> list[SessionScope]:
    conversation_root = state.state_dir / "conversations"
    scopes = [
        SessionScope(
            scope_id="conversation",
            label="对话",
            tooltip="Conversations without a project path",
            workspace_root=None,
            runtime_workspace_root=str((conversation_root / "workspace").resolve()),
            lora_root=str((conversation_root / ".lora").resolve()),
        )
    ]
    for project_path in state.recent_project_paths or []:
        project = Path(project_path).expanduser().resolve()
        scopes.append(
            SessionScope(
                scope_id=f"project:{project}",
                label=_project_label(project, [Path(item).expanduser().resolve() for item in state.recent_project_paths or []]),
                tooltip=str(project),
                workspace_root=str(project),
                runtime_workspace_root=str(project),
                lora_root=str((project / ".lora").resolve()),
            )
        )
    return scopes


@dataclass(frozen=True, slots=True)
class ChatSessionRecord:
    session_id: str
    session_dir: str
    case_id: str
    mode: str
    created_at: str
    updated_at: str
    title: str
    runtime_status: str = "ready"


class ChatSessionStore:
    def __init__(self, manager: SessionManager):
        self.manager = manager

    def list_chat_sessions(self) -> list[ChatSessionRecord]:
        sessions_root = Path(self.manager.sessions_root)
        if not sessions_root.exists():
            return []
        records: list[ChatSessionRecord] = []
        for metadata_path in sessions_root.glob("*/metadata.json"):
            metadata = read_json(metadata_path)
            if metadata.get("mode") != "chat":
                continue
            records.append(_record_from_metadata(metadata_path.parent, metadata))
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def create_chat_session(self) -> ChatSessionRecord:
        ref = self.manager.create("chat", mode="chat")
        metadata = read_json(Path(ref.session_dir) / "metadata.json")
        return _record_from_metadata(Path(ref.session_dir), metadata)

    def delete_chat_session(self, session_id: str) -> bool:
        validate_path_id(session_id, "session_id")
        deleted = False
        for root in (Path(self.manager.sessions_root), Path(self.manager.workspace_root) / "sessions"):
            session_dir = (root / session_id).resolve()
            if not session_dir.is_relative_to(root.resolve()):
                raise ValueError("session_id escapes sessions root")
            if session_dir.exists():
                shutil.rmtree(session_dir)
                deleted = True
        return deleted


def _record_from_metadata(session_dir: Path, metadata: dict[str, object]) -> ChatSessionRecord:
    session_id = str(metadata["session_id"])
    case_id = str(metadata.get("case_id") or "chat")
    mode = str(metadata.get("mode") or "chat")
    created_at = str(metadata.get("created_at") or "")
    updated_at = str(metadata.get("updated_at") or created_at)
    return ChatSessionRecord(
        session_id=session_id,
        session_dir=str(session_dir),
        case_id=case_id,
        mode=mode,
        created_at=created_at,
        updated_at=updated_at,
        title=_display_title(session_id),
    )


def _display_title(session_id: str) -> str:
    if session_id.startswith("chat-chat-"):
        return f"chat-{session_id.removeprefix('chat-chat-')}"
    return session_id


def _project_label(project: Path, all_projects: list[Path]) -> str:
    name = project.name or str(project)
    if sum(1 for item in all_projects if item.name == name) <= 1:
        return name
    parent = project.parent.name
    return f"{name} · {parent}" if parent else name
