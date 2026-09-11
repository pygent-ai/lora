from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import (
    Automation,
    AutomationDestination,
    AutomationRun,
    AutomationRunStatus,
    AutomationStatus,
    next_occurrence,
    normalize_rrule,
    normalize_timezone,
    parse_at_time,
    utc_now,
)


class AutomationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create(
        self,
        *,
        name: str,
        prompt: str,
        workspace_root: str,
        destination: str,
        target_session_id: str | None = None,
        timezone_name: str | None = None,
        at_time: str | None = None,
        rrule: str | None = None,
    ) -> Automation:
        clean_name = _required(name, "name")
        clean_prompt = _required(prompt, "prompt")
        workspace = str(Path(_required(workspace_root, "workspace_root")).resolve())
        destination_value = AutomationDestination(destination).value
        if destination_value == AutomationDestination.HEARTBEAT.value:
            target_session_id = _required(target_session_id or "", "target_session_id")
        else:
            target_session_id = None
        zone = normalize_timezone(timezone_name)
        if bool(at_time) == bool(rrule):
            raise ValueError("exactly one of at_time or rrule is required")
        now = utc_now()
        dtstart = now
        clean_at = parse_at_time(at_time, zone) if at_time else None
        clean_rrule = normalize_rrule(rrule) if rrule else None
        next_run = clean_at or next_occurrence(
            rrule=clean_rrule or "", timezone_name=zone, dtstart=dtstart, after=now
        )
        if next_run is None:
            raise ValueError("schedule has no future occurrence")
        automation_id = f"auto-{uuid.uuid4().hex}"
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO automations(
                       automation_id, name, prompt, workspace_root, destination,
                       target_session_id, timezone, at_time, rrule, dtstart,
                       status, next_run_at, last_run_at, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    automation_id,
                    clean_name,
                    clean_prompt,
                    workspace,
                    destination_value,
                    target_session_id,
                    zone,
                    clean_at,
                    clean_rrule,
                    dtstart,
                    AutomationStatus.ACTIVE.value,
                    next_run,
                    now,
                    now,
                ),
            )
            connection.commit()
        return self.get(automation_id)

    def list(self, *, status: str | None = None) -> list[Automation]:
        query = "SELECT * FROM automations"
        values: tuple[object, ...] = ()
        if status:
            query += " WHERE status = ?"
            values = (AutomationStatus(status).value,)
        query += " ORDER BY updated_at DESC"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, values).fetchall()
        return [_automation(row) for row in rows]

    def get(self, automation_id: str) -> Automation:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM automations WHERE automation_id = ?", (automation_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"automation not found: {automation_id}")
        return _automation(row)

    def update(self, automation_id: str, **changes: Any) -> Automation:
        current = self.get(automation_id)
        allowed = {
            "name",
            "prompt",
            "workspace_root",
            "destination",
            "target_session_id",
            "timezone",
            "at_time",
            "rrule",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported automation fields: {sorted(unknown)}")
        values = current.to_dict()
        values.update({key: value for key, value in changes.items() if value is not None})
        zone = normalize_timezone(str(values["timezone"]))
        destination = AutomationDestination(str(values["destination"])).value
        target = values.get("target_session_id")
        if destination == AutomationDestination.HEARTBEAT.value:
            target = _required(str(target or ""), "target_session_id")
        else:
            target = None
        at_value = values.get("at_time")
        rule_value = values.get("rrule")
        if "at_time" in changes and changes["at_time"] is not None:
            rule_value = None
        if "rrule" in changes and changes["rrule"] is not None:
            at_value = None
        if bool(at_value) == bool(rule_value):
            raise ValueError("exactly one of at_time or rrule is required")
        now = utc_now()
        clean_at = parse_at_time(str(at_value), zone) if at_value else None
        clean_rule = normalize_rrule(str(rule_value)) if rule_value else None
        next_run = clean_at or next_occurrence(
            rrule=clean_rule or "",
            timezone_name=zone,
            dtstart=current.dtstart,
            after=now,
        )
        with closing(self._connect()) as connection:
            connection.execute(
                """UPDATE automations SET name=?, prompt=?, workspace_root=?,
                       destination=?, target_session_id=?, timezone=?, at_time=?,
                       rrule=?, next_run_at=?, status=?, updated_at=?
                   WHERE automation_id=?""",
                (
                    _required(str(values["name"]), "name"),
                    _required(str(values["prompt"]), "prompt"),
                    str(Path(str(values["workspace_root"])).resolve()),
                    destination,
                    target,
                    zone,
                    clean_at,
                    clean_rule,
                    next_run,
                    AutomationStatus.ACTIVE.value,
                    now,
                    automation_id,
                ),
            )
            connection.commit()
        return self.get(automation_id)

    def set_status(self, automation_id: str, status: str) -> Automation:
        value = AutomationStatus(status).value
        if value == AutomationStatus.ACTIVE.value:
            current = self.get(automation_id)
            next_run = current.next_run_at
            if current.rrule:
                next_run = next_occurrence(
                    rrule=current.rrule,
                    timezone_name=current.timezone,
                    dtstart=current.dtstart,
                    after=utc_now(),
                )
            elif current.at_time and current.at_time <= utc_now():
                raise ValueError("a completed one-time automation cannot be resumed")
        else:
            next_run = self.get(automation_id).next_run_at
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE automations SET status=?, next_run_at=?, updated_at=? WHERE automation_id=?",
                (value, next_run, utc_now(), automation_id),
            )
            connection.commit()
        return self.get(automation_id)

    def delete(self, automation_id: str) -> bool:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "DELETE FROM automations WHERE automation_id=?", (automation_id,)
            )
            connection.commit()
        return cursor.rowcount > 0

    def request_run(self, automation_id: str) -> AutomationRun:
        self.get(automation_id)
        now = utc_now()
        run_id = f"arun-{uuid.uuid4().hex}"
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO automation_runs(
                       run_id, automation_id, scheduled_for, trigger, status,
                       final_answer, created_at
                   ) VALUES (?, ?, ?, 'manual', 'queued', '', ?)""",
                (run_id, automation_id, now, now),
            )
            connection.commit()
        return self.get_run(run_id)

    def list_runs(self, automation_id: str, *, limit: int = 100) -> list[AutomationRun]:
        self.get(automation_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT * FROM automation_runs WHERE automation_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (automation_id, max(1, min(limit, 500))),
            ).fetchall()
        return [_run(row) for row in rows]

    def get_run(self, run_id: str) -> AutomationRun:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM automation_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"automation run not found: {run_id}")
        return _run(row)

    def claim_ready_runs(self, *, owner: str, limit: int = 4) -> list[AutomationRun]:
        now = utc_now()
        lease_expires_at = (datetime.now(UTC) + timedelta(minutes=35)).isoformat()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE automation_runs SET status='queued', claim_owner=NULL,
                       lease_expires_at=NULL, started_at=NULL
                   WHERE status='running' AND lease_expires_at IS NOT NULL
                     AND lease_expires_at <= ?""",
                (now,),
            )
            due = connection.execute(
                """SELECT * FROM automations
                   WHERE status='active' AND next_run_at IS NOT NULL AND next_run_at <= ?
                   ORDER BY next_run_at LIMIT ?""",
                (now, limit),
            ).fetchall()
            for row in due:
                automation = _automation(row)
                running = connection.execute(
                    """SELECT 1 FROM automation_runs
                       WHERE automation_id=? AND status='running' LIMIT 1""",
                    (automation.automation_id,),
                ).fetchone()
                run_id = f"arun-{uuid.uuid4().hex}"
                status = (
                    AutomationRunStatus.SKIPPED.value
                    if running
                    else AutomationRunStatus.QUEUED.value
                )
                error = "previous automation run is still active" if running else None
                try:
                    connection.execute(
                        """INSERT INTO automation_runs(
                               run_id, automation_id, scheduled_for, trigger, status,
                               final_answer, error, finished_at, created_at
                           ) VALUES (?, ?, ?, 'schedule', ?, '', ?, ?, ?)""",
                        (
                            run_id,
                            automation.automation_id,
                            automation.next_run_at,
                            status,
                            error,
                            now if running else None,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError:
                    pass
                next_run = None
                next_status = AutomationStatus.COMPLETED.value
                if automation.rrule:
                    next_run = next_occurrence(
                        rrule=automation.rrule,
                        timezone_name=automation.timezone,
                        dtstart=automation.dtstart,
                        after=now,
                    )
                    next_status = (
                        AutomationStatus.ACTIVE.value
                        if next_run
                        else AutomationStatus.COMPLETED.value
                    )
                connection.execute(
                    """UPDATE automations SET next_run_at=?, last_run_at=?,
                           status=?, updated_at=? WHERE automation_id=?""",
                    (next_run, automation.next_run_at, next_status, now, automation.automation_id),
                )
            queued = connection.execute(
                """SELECT * FROM automation_runs WHERE status='queued'
                   ORDER BY created_at LIMIT ?""",
                (limit,),
            ).fetchall()
            claimed: list[AutomationRun] = []
            for row in queued:
                running = connection.execute(
                    """SELECT 1 FROM automation_runs WHERE automation_id=?
                       AND status='running' AND run_id<>? LIMIT 1""",
                    (row["automation_id"], row["run_id"]),
                ).fetchone()
                if running:
                    connection.execute(
                        """UPDATE automation_runs SET status='skipped',
                               error='previous automation run is still active', finished_at=?
                           WHERE run_id=? AND status='queued'""",
                        (now, row["run_id"]),
                    )
                    continue
                cursor = connection.execute(
                    """UPDATE automation_runs SET status='running', started_at=?,
                           claim_owner=?, lease_expires_at=?
                       WHERE run_id=? AND status='queued'""",
                    (now, owner, lease_expires_at, row["run_id"]),
                )
                if cursor.rowcount:
                    claimed.append(
                        _run(connection.execute(
                            "SELECT * FROM automation_runs WHERE run_id=?", (row["run_id"],)
                        ).fetchone())
                    )
            connection.commit()
        return claimed

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        session_id: str | None = None,
        case_run_id: str | None = None,
        execution_id: str | None = None,
        final_answer: str = "",
        error: str | None = None,
    ) -> AutomationRun:
        with closing(self._connect()) as connection:
            connection.execute(
                """UPDATE automation_runs SET status=?, session_id=?, case_run_id=?,
                       execution_id=?, final_answer=?, error=?, finished_at=?,
                       claim_owner=NULL, lease_expires_at=NULL
                   WHERE run_id=?""",
                (
                    AutomationRunStatus(status).value,
                    session_id,
                    case_run_id,
                    execution_id,
                    final_answer,
                    error,
                    utc_now(),
                    run_id,
                ),
            )
            connection.commit()
        return self.get_run(run_id)

    def pause_after_failure(self, automation_id: str) -> None:
        self.set_status(automation_id, AutomationStatus.PAUSED.value)

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS automations(
                    automation_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    target_session_id TEXT,
                    timezone TEXT NOT NULL,
                    at_time TEXT,
                    rrule TEXT,
                    dtstart TEXT NOT NULL,
                    status TEXT NOT NULL,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS automation_runs(
                    run_id TEXT PRIMARY KEY,
                    automation_id TEXT NOT NULL REFERENCES automations(automation_id) ON DELETE CASCADE,
                    scheduled_for TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    status TEXT NOT NULL,
                    session_id TEXT,
                    case_run_id TEXT,
                    execution_id TEXT,
                    final_answer TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    claim_owner TEXT,
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(automation_id, scheduled_for, trigger)
                );
                CREATE INDEX IF NOT EXISTS ix_automations_due
                    ON automations(status, next_run_at);
                CREATE INDEX IF NOT EXISTS ix_automation_runs_status
                    ON automation_runs(status, created_at);
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _automation(row: sqlite3.Row) -> Automation:
    return Automation(**{field: row[field] for field in Automation.__dataclass_fields__})


def _run(row: sqlite3.Row) -> AutomationRun:
    return AutomationRun(**{field: row[field] for field in AutomationRun.__dataclass_fields__})


def _required(value: str, name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{name} must be non-empty")
    return clean


__all__ = ["AutomationStore"]
