from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from .schema import CaseRunRef
from .trace import EventStore

FILE_UNCHANGED_STUB = (
    "File unchanged since last read. The content from the earlier Read tool_result in this conversation "
    "is still current. Refer to that instead of re-reading."
)
FILE_RANGE_CONTAINED_STUB = (
    "Requested range is contained in an earlier Read tool_result for the same file version. "
    "Refer to the earlier result instead of re-reading."
)
FILE_RANGE_OVERLAP_STUB = (
    "Requested range partially overlaps an earlier Read tool_result for the same file version. "
    "Returning only the unread portion."
)


@dataclass(slots=True)
class ReadRange:
    unit: Literal["line", "full"] = "full"
    start: int = 1
    end: int | Literal["EOF"] = "EOF"

    def covers(self, other: "ReadRange") -> bool:
        if self.unit == "full":
            return True
        if other.unit == "full" or self.unit != other.unit:
            return False
        return self.start <= other.start and _end_value(self.end) >= _end_value(other.end)

    def overlaps(self, other: "ReadRange") -> bool:
        if self.unit == "full" or other.unit == "full":
            return True
        if self.unit != other.unit:
            return False
        return self.start <= _end_value(other.end) and other.start <= _end_value(self.end)


@dataclass(slots=True)
class ReadDedupDecision:
    action: Literal["read", "stub", "partial"]
    reason: Literal["none", "exact", "contained", "overlap"]
    content: str | None = None
    previous_event_id: str | None = None


@dataclass(slots=True)
class ToolContext:
    case_run_ref: CaseRunRef
    turn_id: str | None = None


@dataclass(slots=True)
class ToolResult:
    status: Literal["success", "error", "stubbed", "partial"]
    result: Any = None
    error: str | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FileStateTracker:
    def __init__(self, session_state_dir: str | Path):
        self.state_dir = Path(session_state_dir)
        self.file_state_path = self.state_dir / "file_state.json"
        self.read_state_path = self.state_dir / "read_state.json"

    def record_read(self, path: str | Path, content_hash: str, read_range: ReadRange, event_id: str) -> None:
        normalized = _normalize(path)
        read_state = self._read_json(self.read_state_path)
        ranges = [
            row
            for row in read_state.get(normalized, [])
            if row.get("content_hash") == content_hash
        ]
        ranges.append(
            {
                "event_id": event_id,
                "content_hash": content_hash,
                "unit": read_range.unit,
                "start": read_range.start,
                "end": read_range.end,
                "read_at": _now(),
            }
        )
        read_state[normalized] = ranges
        self._write_json(self.read_state_path, read_state)

        file_state = self._read_json(self.file_state_path)
        file_state[normalized] = {
            "path": normalized,
            "exists": True,
            "content_hash": content_hash,
            "last_read_event_id": event_id,
            "read_ranges": ranges,
            "last_known_at": _now(),
        }
        self._write_json(self.file_state_path, file_state)

    def should_stub_read(
        self,
        path: str | Path,
        content_hash: str,
        read_range: ReadRange,
        dedup_level: Literal["exact", "contained", "overlap"] = "contained",
    ) -> ReadDedupDecision:
        ranges = self._read_json(self.read_state_path).get(_normalize(path), [])
        for row in ranges:
            if row.get("content_hash") != content_hash:
                continue
            previous = ReadRange(unit=row["unit"], start=int(row["start"]), end=row["end"])
            if previous.unit == read_range.unit and previous.start == read_range.start and previous.end == read_range.end:
                return ReadDedupDecision("stub", "exact", FILE_UNCHANGED_STUB, row.get("event_id"))
            if dedup_level in {"contained", "overlap"} and previous.covers(read_range):
                return ReadDedupDecision("stub", "contained", FILE_RANGE_CONTAINED_STUB, row.get("event_id"))
            if dedup_level == "overlap" and previous.overlaps(read_range):
                return ReadDedupDecision("partial", "overlap", FILE_RANGE_OVERLAP_STUB, row.get("event_id"))
        return ReadDedupDecision("read", "none")

    def read_text_file(
        self,
        path: str | Path,
        *,
        offset: int | None = None,
        limit: int | None = None,
        dedup_level: Literal["exact", "contained", "overlap"] = "contained",
        event_store: EventStore | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        target = Path(path).expanduser().resolve()
        content = target.read_text(encoding="utf-8")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        read_range = _line_range(content, offset, limit)
        decision = self.should_stub_read(target, content_hash, read_range, dedup_level)
        if decision.action == "stub":
            return {"status": "stubbed", "content": decision.content, "dedup": decision.reason}

        selected = _select_lines(content, read_range)
        if decision.action == "partial":
            selected = f"{decision.content}\n\n{selected}"
        returned_range = asdict(read_range)
        event_payload = {
            "path": str(target),
            "content_hash": content_hash,
            "size": len(content.encode("utf-8")),
            "requested_range": asdict(read_range),
            "returned_range": returned_range,
            "dedup": decision.reason,
            "returned_content": selected,
        }
        event_id = (
            event_store.append("file.read", actor="tool", payload=event_payload, turn_id=turn_id)
            if event_store is not None
            else f"read_{uuid.uuid4().hex}"
        )
        self.record_read(target, content_hash, read_range, event_id)
        return {
            "status": "partial" if decision.action == "partial" else "success",
            "content": selected,
            "content_hash": content_hash,
            "range": asdict(read_range),
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ToolInterceptor:
    def __init__(self, store: EventStore):
        self.store = store

    def call_tool(self, name: str, args: dict[str, Any], ctx: ToolContext, tool: Callable[..., Any]) -> ToolResult:
        call_id = self.store.append(
            "tool.call",
            actor="assistant",
            payload={"tool_name": name, "args": args},
            turn_id=ctx.turn_id,
        )
        try:
            result = tool(**args)
        except Exception as exc:  # noqa: BLE001 - tool boundary records all failures.
            payload = {"tool_call_id": call_id, "status": "error", "error": str(exc), "error_type": type(exc).__name__}
            self.store.append("tool.result", actor="tool", payload=payload, turn_id=ctx.turn_id)
            return ToolResult(status="error", error=str(exc), tool_call_id=call_id)

        status = "success"
        if isinstance(result, dict) and result.get("status") in {"stubbed", "partial"}:
            status = result["status"]
        self.store.append(
            "tool.result",
            actor="tool",
            payload={"tool_call_id": call_id, "status": status, "result": result},
            turn_id=ctx.turn_id,
        )
        return ToolResult(status=status, result=result, tool_call_id=call_id)  # type: ignore[arg-type]


def _normalize(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _line_range(content: str, offset: int | None, limit: int | None) -> ReadRange:
    if offset is None and limit is None:
        return ReadRange()
    line_count = max(1, len(content.splitlines()))
    start = max(1, int(offset or 1))
    end: int | Literal["EOF"]
    if limit is None:
        end = "EOF"
    else:
        end = min(line_count, start + max(0, int(limit)) - 1)
    return ReadRange(unit="line", start=start, end=end)


def _select_lines(content: str, read_range: ReadRange) -> str:
    if read_range.unit == "full":
        return content
    lines = content.splitlines()
    end = len(lines) if read_range.end == "EOF" else int(read_range.end)
    return "\n".join(lines[read_range.start - 1 : end])


def _end_value(value: int | Literal["EOF"]) -> int:
    return 2**31 - 1 if value == "EOF" else int(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
