from __future__ import annotations

import json
from html import unescape
from pathlib import Path
from typing import Any

from lora.core.io import read_json
from lora.core.io import validate_path_id
from lora.core.io import write_json
from lora.schema import AgentSession
from lora.sessions import SessionManager
from lora.tracing.events import EventStore

from lora_api.dependencies import ApiContext
from lora_api.models.responses import (
    SessionDetailResponse,
    SessionGroupListResponse,
    SessionGroupResponse,
    SessionRecordResponse,
    SessionScopeResponse,
)
from lora_api.services.project_state import active_project_scope_id, build_session_scopes


class SessionService:
    def __init__(self, manager: SessionManager):
        self.manager = manager

    def list_chat_sessions(self, *, scope_id: str | None = None) -> list[SessionRecordResponse]:
        sessions_root = Path(self.manager.sessions_root)
        if not sessions_root.exists():
            return []
        records: list[SessionRecordResponse] = []
        for metadata_path in sessions_root.glob("*/metadata.json"):
            metadata = read_json(metadata_path)
            if metadata.get("mode") != "chat":
                continue
            records.append(_record_from_metadata(metadata_path.parent, metadata, scope_id=scope_id))
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def create_session(self, *, case_id: str = "chat", mode: str = "chat") -> SessionRecordResponse:
        ref = self.manager.create(case_id, mode=mode)
        metadata = read_json(Path(ref.session_dir) / "metadata.json")
        return _record_from_metadata(Path(ref.session_dir), metadata)

    def load_detail(self, session_id: str) -> SessionDetailResponse:
        session = self.manager.load(session_id)
        metadata = read_json(Path(session.session_dir) / "metadata.json")
        return SessionDetailResponse(
            session=_record_from_metadata(Path(session.session_dir), metadata),
            history=session.history,
            metadata=session.metadata,
        )

    def delete_session(self, session_id: str) -> bool:
        validate_path_id(session_id, "session_id")
        deleted = False
        root = Path(self.manager.sessions_root)
        session_dir = (root / session_id).resolve()
        if not session_dir.is_relative_to(root.resolve()):
            raise ValueError("session_id escapes sessions root")
        if session_dir.exists():
            _delete_tree(session_dir)
            deleted = True
        return deleted

    def save_title_from_user_input(self, session_id: str, user_input: str) -> None:
        session = self.manager.load(session_id)
        metadata_path = Path(session.session_dir) / "metadata.json"
        metadata = read_json(metadata_path)
        if _clean_title(str(metadata.get("title") or "")):
            return
        title = _clean_title(user_input)
        if not title:
            return
        metadata["title"] = title[:120]
        write_json(metadata_path, metadata)


def session_groups_response(context: ApiContext) -> SessionGroupListResponse:
    config = context.config
    active_scope_id = active_project_scope_id(config.workspace_root)
    collapsed_ids = set(context.project_state.collapsed_scope_ids or [])
    groups: list[SessionGroupResponse] = []
    for scope in build_session_scopes(context.project_state, active_workspace_root=config.workspace_root):
        manager = SessionManager(context.config_for_scope(scope))
        records = SessionService(manager).list_chat_sessions(scope_id=scope.scope_id)
        groups.append(
            SessionGroupResponse(
                scope=SessionScopeResponse(
                    scope_id=scope.scope_id,
                    label=scope.label,
                    tooltip=scope.tooltip,
                    workspace_root=scope.workspace_root,
                    lora_root=scope.lora_root,
                    runtime_workspace_root=scope.runtime_workspace_root,
                ),
                sessions=records,
                collapsed=scope.scope_id in collapsed_ids,
            )
        )
    return SessionGroupListResponse(active_scope_id=active_scope_id, groups=groups)


def _record_from_metadata(
    session_dir: Path,
    metadata: dict[str, Any],
    *,
    scope_id: str | None = None,
) -> SessionRecordResponse:
    session_id = str(metadata["session_id"])
    case_id = str(metadata.get("case_id") or "chat")
    mode = str(metadata.get("mode") or "chat")
    created_at = str(metadata.get("created_at") or "")
    updated_at = str(metadata.get("updated_at") or created_at)
    title = _clean_title(str(metadata.get("title") or "")) or _first_user_message_title(session_dir) or _display_title(session_id)
    return SessionRecordResponse(
        session_id=session_id,
        session_dir=str(session_dir),
        scope_id=scope_id,
        case_id=case_id,
        mode=mode,
        created_at=created_at,
        updated_at=updated_at,
        title=title,
        last_case_run_id=_optional_str(metadata.get("last_case_run_id")),
        last_case_run_status=_optional_str(metadata.get("last_case_run_status")),
    )


def _session_from_manager(manager: SessionManager, session_id: str) -> AgentSession:
    return manager.load(session_id)


def _display_title(session_id: str) -> str:
    if session_id.startswith("chat-chat-"):
        return f"chat-{session_id.removeprefix('chat-chat-')}"
    return session_id


def _first_user_message_title(session_dir: Path) -> str:
    title = _first_user_message_from_events(session_dir)
    if title:
        return title
    title = _first_user_message_from_history_log(session_dir)
    if title:
        return title
    return _first_user_message_from_session_json(session_dir)


def _first_user_message_from_events(session_dir: Path) -> str:
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


def _first_user_message_from_history_log(session_dir: Path) -> str:
    history_path = session_dir / "context" / "history.jsonl"
    for row in EventStore.iter_jsonl(history_path):
        if not isinstance(row, dict) or row.get("role") != "user":
            continue
        title = _clean_title(_unwrap_user_message(str(row.get("content") or "")))
        if title:
            return title
    return ""


def _first_user_message_from_session_json(session_dir: Path) -> str:
    try:
        data = read_json(session_dir / "session.json")
    except (OSError, json.JSONDecodeError):
        return ""
    for message in data.get("history") or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        title = _clean_title(_unwrap_user_message(str(message.get("content") or "")))
        if title:
            return title
    return ""


def _title_from_user_payload(payload: dict[str, Any]) -> str:
    raw_content = payload.get("raw_content")
    if isinstance(raw_content, str):
        title = _clean_title(raw_content)
        if title:
            return title
    content = payload.get("content")
    if isinstance(content, str):
        return _clean_title(_unwrap_user_message(content))
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


def _clean_title(value: str) -> str:
    return " ".join(value.split())


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _delete_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path)
