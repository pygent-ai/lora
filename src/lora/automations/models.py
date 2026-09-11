from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from tzlocal import get_localzone_name


class AutomationStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class AutomationDestination(StrEnum):
    STANDALONE = "standalone"
    HEARTBEAT = "heartbeat"


class AutomationRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Automation:
    automation_id: str
    name: str
    prompt: str
    workspace_root: str
    destination: str
    target_session_id: str | None
    timezone: str
    at_time: str | None
    rrule: str | None
    dtstart: str
    status: str
    next_run_at: str | None
    last_run_at: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AutomationRun:
    run_id: str
    automation_id: str
    scheduled_for: str
    trigger: str
    status: str
    session_id: str | None
    case_run_id: str | None
    execution_id: str | None
    final_answer: str
    error: str | None
    started_at: str | None
    finished_at: str | None
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_timezone(value: str | None) -> str:
    name = (value or _local_timezone_name()).strip()
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {name}") from exc
    return name


def normalize_rrule(value: str) -> str:
    rule = value.strip()
    if rule.upper().startswith("RRULE:"):
        rule = rule[6:]
    if not rule:
        raise ValueError("rrule must be non-empty")
    return rule.upper()


def parse_at_time(value: str, timezone_name: str) -> str:
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(UTC).isoformat()


def next_occurrence(
    *, rrule: str, timezone_name: str, dtstart: str, after: str
) -> str | None:
    zone = ZoneInfo(timezone_name)
    start = datetime.fromisoformat(dtstart).astimezone(zone)
    boundary = datetime.fromisoformat(after).astimezone(zone)
    recurrence = rrulestr(normalize_rrule(rrule), dtstart=start)
    value = recurrence.after(boundary, inc=False)
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(UTC).isoformat()


def _local_timezone_name() -> str:
    try:
        return get_localzone_name()
    except Exception:  # noqa: BLE001 - platform timezone discovery is best effort
        local = datetime.now().astimezone().tzinfo
        return getattr(local, "key", None) or "UTC"


__all__ = [
    "Automation",
    "AutomationDestination",
    "AutomationRun",
    "AutomationRunStatus",
    "AutomationStatus",
    "next_occurrence",
    "normalize_rrule",
    "normalize_timezone",
    "parse_at_time",
    "utc_now",
]
