from __future__ import annotations

import hashlib
import json
import sqlite3
import shutil
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from lora.core.io import (
    append_jsonl,
    read_json,
    utc_now,
    validate_path_id,
    write_json,
    write_json_atomic,
)
from lora.schema import AgentSession, CaseRunRef, RunConfig, SessionRef, SessionSpec


class SessionManager:
    def __init__(self, config: RunConfig):
        self.config = config
        self.workspace_root = Path(config.workspace_root)
        self.lora_root = Path(config.lora_root)
        self.sessions_root = self.lora_root / "sessions"

    def create(self, case_id: str, mode: str = "e2e") -> SessionRef:
        validate_path_id(case_id, "case_id")
        session_id = self._new_session_id(case_id, mode)
        session_dir = self.sessions_root / session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        for child in ("context", "state", "logs", "cases"):
            (session_dir / child).mkdir(parents=True, exist_ok=True)

        now = utc_now()
        session = AgentSession(
            session_id=session_id,
            workspace_root=str(self.workspace_root),
            session_dir=str(session_dir),
            created_at=now,
            updated_at=now,
            metadata={"active_case_id": case_id, "mode": mode},
        )
        write_json_atomic(session_dir / "session.json", session.to_dict())
        write_json(
            session_dir / "metadata.json",
            {
                "session_id": session_id,
                "case_id": case_id,
                "mode": mode,
                "created_at": now,
                "updated_at": now,
                "workspace_root": str(self.workspace_root),
            },
        )
        return SessionRef(session_id=session_id, session_dir=str(session_dir), workspace_root=str(self.workspace_root))

    def load(self, session_id: str) -> AgentSession:
        session_dir = self._session_dir(session_id)
        path = session_dir / "session.json"
        if not path.exists():
            raise FileNotFoundError(f"Session {session_id!r} does not exist")
        data = read_json(path)
        data["session_dir"] = str(session_dir)
        return self._apply_history_checkpoints(AgentSession.from_dict(data))

    def load_or_create(self, spec: SessionSpec) -> AgentSession:
        if spec.mode in {"resume", "shared"}:
            if not spec.session_id:
                raise ValueError(f"session_id is required for {spec.mode} mode")
            return self.load(spec.session_id)
        if spec.mode == "fork":
            if not spec.source_session_id:
                raise ValueError("source_session_id is required for fork mode")
            return self.load(self.fork(spec.source_session_id).session_id)
        return self.load(self.create(spec.case_id).session_id)

    def fork(self, source_session_id: str) -> SessionRef:
        source_dir = self._session_dir(source_session_id)
        if not source_dir.exists():
            raise FileNotFoundError(f"Source session {source_session_id!r} does not exist")
        source_meta = read_json(source_dir / "metadata.json")
        case_id = source_meta.get("case_id", "fork")
        target = self.create(case_id=case_id, mode="fork")
        target_dir = Path(target.session_dir)
        # A fork is a semantic continuation at the source cursor. Copy every
        # durable conversation layer, not only the foreground context mirror.
        for name in (
            "session.json",
            "context",
            "state",
            "memory",
            "raw-history",
            "agent-history",
        ):
            src = source_dir / name
            dst = target_dir / name
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            elif src.exists():
                shutil.copy2(src, dst)
        data = read_json(target_dir / "session.json")
        data.update(
            {
                "session_id": target.session_id,
                "session_dir": str(target_dir),
                "updated_at": utc_now(),
            }
        )
        session = AgentSession.from_dict(data)
        session.metadata.update({"forked_from": source_session_id})
        self._save_session(session)
        return target

    def start_case_run(self, session_id: str, case_id: str, run_config: RunConfig | None = None) -> CaseRunRef:
        validate_path_id(case_id, "case_id")
        self.load(session_id)
        case_run_id = self._new_case_run_id(session_id, case_id)
        run_dir = self._session_dir(session_id) / "cases" / case_id / "runs" / case_run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        ref = CaseRunRef(session_id=session_id, case_id=case_id, case_run_id=case_run_id, run_dir=str(run_dir))
        config = run_config or self.config
        write_json(run_dir / "run_config.json", config.to_dict())
        write_json(
            run_dir / "run_metadata.json",
            {"status": "running", "started_at": utc_now(), **ref.to_dict()},
        )
        self._append_session_event(session_id, "case.started", {"case_id": case_id, "case_run_id": case_run_id})
        return ref

    def finish_case_run(self, case_run_ref: CaseRunRef, status: str) -> None:
        if status not in {"passed", "failed", "error", "skipped"}:
            raise ValueError("status must be one of passed, failed, error, skipped")
        run_dir = Path(case_run_ref.run_dir)
        metadata_path = run_dir / "run_metadata.json"
        metadata = read_json(metadata_path)
        metadata.update({"status": status, "finished_at": utc_now()})
        write_json(metadata_path, metadata)
        session = self.load(case_run_ref.session_id)
        session.updated_at = utc_now()
        session.metadata.update(
            {
                "active_case_id": case_run_ref.case_id,
                "last_case_run_id": case_run_ref.case_run_id,
                "last_case_run_status": status,
            }
        )
        self._save_session(session)
        metadata_path = self._session_dir(case_run_ref.session_id) / "metadata.json"
        session_metadata = read_json(metadata_path)
        session_metadata.update(
            {
                "updated_at": session.updated_at,
                "last_case_run_id": case_run_ref.case_run_id,
                "last_case_run_status": status,
            }
        )
        write_json(metadata_path, session_metadata)
        self._append_session_event(
            case_run_ref.session_id,
            "case.finished",
            {"case_id": case_run_ref.case_id, "case_run_id": case_run_ref.case_run_id, "status": status},
        )

    def show(self, session_id: str) -> dict[str, Any]:
        session = self.load(session_id)
        metadata = read_json(self._session_dir(session_id) / "metadata.json")
        return {"session": session.to_dict(), "metadata": metadata}

    def save(self, session: AgentSession) -> None:
        session.updated_at = utc_now()
        self._apply_history_checkpoints(session)
        self._save_session(session)

    def append_history_checkpoint(
        self,
        case_run_ref: CaseRunRef,
        *,
        turn_id: str | None,
        checkpoint_id: str,
        message: dict[str, Any],
        include_in_session: bool = True,
    ) -> bool:
        """Durably append one raw conversation boundary exactly once."""

        validate_path_id(case_run_ref.session_id, "session_id")
        database_path = self._checkpoint_database_path(case_run_ref.session_id)
        with closing(self._checkpoint_connection(database_path)) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO conversation_checkpoints (
                        checkpoint_id,
                        session_id,
                        case_id,
                        case_run_id,
                        turn_id,
                        role,
                        message_json,
                        include_in_session,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        checkpoint_id,
                        case_run_ref.session_id,
                        case_run_ref.case_id,
                        case_run_ref.case_run_id,
                        turn_id,
                        str(message.get("role") or "user"),
                        json.dumps(message, ensure_ascii=False, sort_keys=True),
                        int(include_in_session),
                        utc_now(),
                    ),
                )
                return cursor.rowcount == 1

    def find_case_run(self, session_id: str, case_run_id: str) -> CaseRunRef:
        session_dir = self._session_dir(session_id)
        matches = list((session_dir / "cases").glob(f"*/runs/{case_run_id}"))
        if not matches:
            raise FileNotFoundError(f"Case run {case_run_id!r} does not exist under session {session_id!r}")
        if len(matches) > 1:
            raise ValueError(f"Case run {case_run_id!r} is ambiguous under session {session_id!r}")
        run_dir = matches[0]
        metadata = read_json(run_dir / "run_metadata.json")
        return CaseRunRef(
            session_id=session_id,
            case_id=metadata["case_id"],
            case_run_id=case_run_id,
            run_dir=str(run_dir),
        )

    def _save_session(self, session: AgentSession) -> None:
        validate_path_id(session.session_id, "session_id")
        session_dir = self._session_dir(session.session_id)
        write_json_atomic(session_dir / "session.json", session.to_dict())

    def _apply_history_checkpoints(self, session: AgentSession) -> AgentSession:
        database_path = self._checkpoint_database_path(session.session_id)
        if not database_path.exists():
            return session
        cursor_sequence = int(session.metadata.get("history_checkpoint_seq") or 0)
        with closing(self._checkpoint_connection(database_path)) as connection:
            rows = connection.execute(
                """
                SELECT sequence, message_json, include_in_session
                FROM conversation_checkpoints
                WHERE sequence > ?
                ORDER BY sequence
                """,
                (cursor_sequence,),
            ).fetchall()
        for sequence, message_json, include_in_session in rows:
            cursor_sequence = int(sequence)
            if include_in_session:
                message = json.loads(message_json)
                if not isinstance(message, dict):
                    raise ValueError("conversation checkpoint message must be a JSON object")
                session.history.append(message)
        if rows:
            session.metadata["history_checkpoint_seq"] = cursor_sequence
        return session

    def _checkpoint_database_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "context" / "conversation-checkpoints.sqlite3"

    @staticmethod
    def _checkpoint_connection(path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_checkpoints (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                checkpoint_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                case_run_id TEXT NOT NULL,
                turn_id TEXT,
                role TEXT NOT NULL,
                message_json TEXT NOT NULL,
                include_in_session INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        return connection

    def _session_dir(self, session_id: str) -> Path:
        validate_path_id(session_id, "session_id")
        session_dir = (self.sessions_root / session_id).resolve()
        if not session_dir.is_relative_to(self.sessions_root.resolve()):
            raise ValueError("session_id escapes sessions root")
        return session_dir

    def _new_session_id(self, case_id: str, mode: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        digest = hashlib.sha1(f"{case_id}:{mode}:{stamp}:{utc_now()}".encode("utf-8")).hexdigest()[:6]
        return f"{mode}-{_slug(case_id)}-{stamp}-{digest}"

    def _new_case_run_id(self, session_id: str, case_id: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        digest = hashlib.sha1(f"{session_id}:{case_id}:{stamp}".encode("utf-8")).hexdigest()[:6]
        return f"run-{stamp}-{digest}"

    def _append_session_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        path = self._session_dir(session_id) / "logs" / "session-events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"type": event_type, "timestamp": utc_now(), "session_id": session_id, "payload": payload}
        append_jsonl(path, event)

def _slug(value: str) -> str:
    clean = "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")
    return clean or "case"
