from __future__ import annotations

import json
from dataclasses import dataclass
from html import unescape
from pathlib import Path
import shutil

from lora.core.io import read_json
from lora.core.io import validate_path_id
from lora.core.io import write_json
from lora.schema import RunConfig
from lora.sessions import SessionManager
from lora.tracing import EventStore


GUI_STATE_PATH = Path.home() / ".lora" / "gui" / "state.json"


@dataclass(slots=True)
class GuiProjectState:
    state_path: Path
    default_project_path: str | None = None
    recent_project_paths: list[str] | None = None
    scope_order: list[str] | None = None
    collapsed_scope_ids: list[str] | None = None

    @classmethod
    def load(cls, state_path: str | Path | None = None) -> "GuiProjectState":
        path = Path(state_path or GUI_STATE_PATH).expanduser().resolve()
        if not path.exists():
            return cls(state_path=path, recent_project_paths=[], scope_order=[], collapsed_scope_ids=[])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        recent = [str(Path(item).expanduser().resolve()) for item in data.get("recent_project_paths") or [] if str(item).strip()]
        default = data.get("default_project_path")
        resolved_default = str(Path(default).expanduser().resolve()) if isinstance(default, str) and default.strip() else None
        scope_order = [_normalize_scope_id(str(item)) for item in data.get("scope_order") or []]
        collapsed = [_normalize_scope_id(str(item)) for item in data.get("collapsed_scope_ids") or []]
        scope_order = [item for item in scope_order if item]
        collapsed = [item for item in collapsed if item]
        return cls(
            state_path=path,
            default_project_path=resolved_default,
            recent_project_paths=recent,
            scope_order=_unique(scope_order),
            collapsed_scope_ids=_unique(collapsed),
        )

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

    def remember_scope_order(self, scope_ids: list[str], *, persist: bool = True) -> None:
        seen: set[str] = set()
        ordered: list[str] = []
        for scope_id in scope_ids:
            normalized = _normalize_scope_id(scope_id)
            if normalized and normalized not in seen:
                ordered.append(normalized)
                seen.add(normalized)
        self.scope_order = ordered
        if persist:
            self.save()

    def set_scope_collapsed(self, scope_id: str, collapsed: bool, *, persist: bool = True) -> None:
        normalized = _normalize_scope_id(scope_id)
        if not normalized:
            return
        collapsed_ids = [item for item in self.collapsed_scope_ids or [] if item != normalized]
        if collapsed:
            collapsed_ids.append(normalized)
        self.collapsed_scope_ids = collapsed_ids
        if persist:
            self.save()

    def forget_project(self, project_path: str | Path) -> None:
        resolved = str(Path(project_path).expanduser().resolve())
        scope_id = f"project:{resolved}"
        recent = [
            item
            for item in self.recent_project_paths or []
            if str(Path(item).expanduser().resolve()) != resolved
        ]
        self.recent_project_paths = recent
        if self.default_project_path and str(Path(self.default_project_path).expanduser().resolve()) == resolved:
            self.default_project_path = recent[0] if recent else None
        self.scope_order = [item for item in self.scope_order or [] if _normalize_scope_id(item) != scope_id]
        self.collapsed_scope_ids = [
            item for item in self.collapsed_scope_ids or [] if _normalize_scope_id(item) != scope_id
        ]
        self.save()

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "default_project_path": self.default_project_path,
                    "recent_project_paths": self.recent_project_paths or [],
                    "scope_order": self.scope_order or [],
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

    def to_run_config(self) -> RunConfig:
        return RunConfig(workspace_root=self.runtime_workspace_root, lora_root=self.lora_root)


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


@dataclass(frozen=True, slots=True)
class SessionGroupRecord:
    scope: SessionScope
    records: list[ChatSessionRecord]
    collapsed: bool = False


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
    scope_by_id = {scope.scope_id: scope for scope in scopes}
    order_source = state.scope_order or _default_scope_order(scopes, state)
    ordered: list[SessionScope] = []
    for scope_id in order_source:
        normalized = _normalize_scope_id(scope_id)
        scope = scope_by_id.get(normalized)
        if scope is not None:
            ordered.append(scope)
    ordered_ids = {scope.scope_id for scope in ordered}
    for scope_id in _default_scope_order(scopes, state):
        if scope_id not in ordered_ids:
            ordered.append(scope_by_id[scope_id])
    return ordered


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

    def save_user_title(self, session_id: str, user_input: str) -> None:
        session_dir = self._session_dir(session_id)
        metadata_path = session_dir / "metadata.json"
        metadata = read_json(metadata_path)
        if _clean_title_text(str(metadata.get("title") or "")):
            return
        title = _first_user_question_title(session_dir) or _clean_title_text(user_input)
        if not title:
            return
        metadata["title"] = title
        write_json(metadata_path, metadata)

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

    def _session_dir(self, session_id: str) -> Path:
        validate_path_id(session_id, "session_id")
        root = Path(self.manager.sessions_root).resolve()
        session_dir = (root / session_id).resolve()
        if not session_dir.is_relative_to(root):
            raise ValueError("session_id escapes sessions root")
        return session_dir


def _normalize_scope_id(scope_id: str) -> str:
    value = str(scope_id).strip()
    if not value:
        return ""
    if value == "conversation":
        return value
    if value.startswith("project:"):
        project = value.removeprefix("project:")
        if not project.strip():
            return ""
        return f"project:{Path(project).expanduser().resolve()}"
    return ""


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _default_scope_order(scopes: list[SessionScope], state: GuiProjectState) -> list[str]:
    scope_ids = [scope.scope_id for scope in scopes]
    ordered: list[str] = []
    if state.default_project_path:
        default_id = f"project:{Path(state.default_project_path).expanduser().resolve()}"
        if default_id in scope_ids:
            ordered.append(default_id)
    if "conversation" in scope_ids and "conversation" not in ordered:
        ordered.append("conversation")
    for scope_id in scope_ids:
        if scope_id not in ordered:
            ordered.append(scope_id)
    return ordered


def _record_from_metadata(session_dir: Path, metadata: dict[str, object]) -> ChatSessionRecord:
    session_id = str(metadata["session_id"])
    case_id = str(metadata.get("case_id") or "chat")
    mode = str(metadata.get("mode") or "chat")
    created_at = str(metadata.get("created_at") or "")
    updated_at = str(metadata.get("updated_at") or created_at)
    title = (
        _clean_title_text(str(metadata.get("title") or ""))
        or _first_user_question_title(session_dir)
        or _display_title(session_id)
    )
    return ChatSessionRecord(
        session_id=session_id,
        session_dir=str(session_dir),
        case_id=case_id,
        mode=mode,
        created_at=created_at,
        updated_at=updated_at,
        title=title,
    )


def _display_title(session_id: str) -> str:
    if session_id.startswith("chat-chat-"):
        return f"chat-{session_id.removeprefix('chat-chat-')}"
    return session_id


def _first_user_question_title(session_dir: Path) -> str:
    title = _first_user_question_from_events(session_dir)
    if title:
        return title
    title = _first_user_question_from_history_log(session_dir)
    if title:
        return title
    return _first_user_question_from_session_json(session_dir)


def _first_user_question_from_events(session_dir: Path) -> str:
    candidates: list[tuple[str, str]] = []
    for events_path in (session_dir / "cases").glob("*/runs/*/events.jsonl"):
        for row in EventStore.iter_jsonl(events_path):
            if not isinstance(row, dict) or row.get("type") != "conversation.user_message":
                continue
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            title = _title_from_user_payload(payload)
            if title:
                candidates.append((str(row.get("timestamp") or ""), title))
    if not candidates:
        return ""
    return sorted(candidates, key=lambda item: item[0])[0][1]


def _first_user_question_from_history_log(session_dir: Path) -> str:
    history_path = session_dir / "context" / "history.jsonl"
    for row in EventStore.iter_jsonl(history_path):
        if not isinstance(row, dict) or row.get("role") != "user":
            continue
        title = _clean_title_text(_unwrap_user_message(str(row.get("content") or "")))
        if title:
            return title
    return ""


def _first_user_question_from_session_json(session_dir: Path) -> str:
    try:
        data = read_json(session_dir / "session.json")
    except (OSError, json.JSONDecodeError):
        return ""
    for message in data.get("history") or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        title = _clean_title_text(_unwrap_user_message(str(message.get("content") or "")))
        if title:
            return title
    return ""


def _title_from_user_payload(payload: dict[str, object]) -> str:
    raw_content = payload.get("raw_content")
    if isinstance(raw_content, str):
        title = _clean_title_text(raw_content)
        if title:
            return title
    content = payload.get("content")
    if isinstance(content, str):
        return _clean_title_text(_unwrap_user_message(content))
    return ""


def _unwrap_user_message(content: str) -> str:
    start_token = "<user-message>"
    end_token = "</user-message>"
    start = content.find(start_token)
    if start < 0:
        return content
    start += len(start_token)
    end = content.find(end_token, start)
    if end < 0:
        return content
    return unescape(content[start:end])


def _clean_title_text(value: str) -> str:
    return " ".join(str(value).split())


def _project_label(project: Path, all_projects: list[Path]) -> str:
    name = project.name or str(project)
    if sum(1 for item in all_projects if item.name == name) <= 1:
        return name
    parent = project.parent.name
    return f"{name} · {parent}" if parent else name
