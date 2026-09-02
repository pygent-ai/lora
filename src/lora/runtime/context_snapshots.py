from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from lora.core.io import utc_now
from lora.core.redaction import redact_secrets


class ContextSnapshotStore:
    """Durable, session-scoped model context snapshots for the Trace inspector."""

    def __init__(self, session_dir: str | Path):
        self.path = (
            Path(session_dir).expanduser().resolve()
            / "context"
            / "context-snapshots.sqlite3"
        )

    def save(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        safe = redact_secrets(snapshot)
        safe["captured_at"] = str(safe.get("captured_at") or utc_now())
        payload = json.dumps(safe, ensure_ascii=False, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                self._ensure_schema(connection)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO context_snapshots (
                        snapshot_id, session_id, case_run_id, turn_id,
                        compression_version, projection_revision, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        safe["snapshot_id"],
                        safe["session_id"],
                        safe["case_run_id"],
                        safe.get("turn_id"),
                        int(safe.get("compression_version") or 0),
                        int(safe.get("projection_revision") or 0),
                        payload,
                    ),
                )
                row = connection.execute(
                    "SELECT payload_json FROM context_snapshots WHERE snapshot_id = ?",
                    (safe["snapshot_id"],),
                ).fetchone()
        if row is None:  # pragma: no cover - database invariant
            raise RuntimeError("context snapshot was not persisted")
        return json.loads(row[0])

    def list(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with closing(
            sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        ) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'context_snapshots'"
            ).fetchone()
            rows = (
                []
                if exists is None
                else connection.execute(
                    "SELECT payload_json FROM context_snapshots ORDER BY sequence"
                ).fetchall()
            )
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS context_snapshots (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                case_run_id TEXT NOT NULL,
                turn_id TEXT,
                compression_version INTEGER NOT NULL,
                projection_revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )


__all__ = ["ContextSnapshotStore"]
