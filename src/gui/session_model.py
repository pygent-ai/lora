from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from lora.io import read_json
from lora.io import validate_path_id
from lora.session import SessionManager


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
