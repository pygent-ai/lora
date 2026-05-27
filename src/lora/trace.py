from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import CaseRunRef, ContextEvent


class EventStore:
    def __init__(self, case_run_ref: CaseRunRef):
        self.case_run_ref = case_run_ref
        self.run_dir = Path(case_run_ref.run_dir)
        self.events_path = self.run_dir / "events.jsonl"
        self.messages_path = self.run_dir / "messages.jsonl"
        self.tool_calls_path = self.run_dir / "tool_calls.jsonl"
        self.tool_results_path = self.run_dir / "tool_results.jsonl"
        self.file_events_path = self.run_dir / "file_events.jsonl"

    def append(
        self,
        event: ContextEvent | str,
        *,
        actor: str | None = None,
        payload: dict[str, Any] | None = None,
        turn_id: str | None = None,
    ) -> str:
        context_event = self._coerce_event(
            event,
            actor=actor,
            payload=payload,
            turn_id=turn_id,
        )
        self._append_event(context_event)
        return context_event.id

    def append_event(self, event: ContextEvent) -> str:
        context_event = self._coerce_event(event)
        self._append_event(context_event)
        return context_event.id

    def append_error(
        self,
        error: BaseException | str,
        *,
        event_type: str = "error",
        actor: str = "system",
        payload: dict[str, Any] | None = None,
        turn_id: str | None = None,
    ) -> str:
        error_payload = dict(payload or {})
        if isinstance(error, BaseException):
            error_payload.update({"error": str(error), "error_type": type(error).__name__})
        else:
            error_payload.update({"error": error, "error_type": "Error"})
        return self.append(event_type, actor=actor, payload=error_payload, turn_id=turn_id)

    def list_by_run(
        self,
        session_id: str | None = None,
        case_run_id: str | None = None,
    ) -> list[ContextEvent]:
        target_session_id = session_id or self.case_run_ref.session_id
        target_case_run_id = case_run_id or self.case_run_ref.case_run_id
        return [
            event
            for event in self.list_events()
            if event.session_id == target_session_id and event.case_run_id == target_case_run_id
        ]

    def list_by_session(self, session_id: str | None = None) -> list[ContextEvent]:
        target_session_id = session_id or self.case_run_ref.session_id
        return [event for event in self.list_events() if event.session_id == target_session_id]

    def list_by_case(self, case_id: str | None = None) -> list[ContextEvent]:
        target_case_id = case_id or self.case_run_ref.case_id
        return [event for event in self.list_events() if event.case_id == target_case_id]

    def list_by_turn(self, turn_id: str) -> list[ContextEvent]:
        return [event for event in self.list_events() if event.turn_id == turn_id]

    def list_events(self) -> list[ContextEvent]:
        return [ContextEvent.from_dict(row) for row in self.iter_jsonl(self.events_path)]

    @staticmethod
    def iter_jsonl(path: str | Path):
        jsonl_path = Path(path)
        if not jsonl_path.exists():
            return
        with jsonl_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    def _append_event(self, event: ContextEvent) -> None:
        self._append_jsonl(self.events_path, event.to_dict())
        if event.type.startswith("conversation."):
            self._append_jsonl(
                self.messages_path,
                {
                    "event_id": event.id,
                    "session_id": event.session_id,
                    "case_id": event.case_id,
                    "case_run_id": event.case_run_id,
                    "turn_id": event.turn_id,
                    "role": event.payload.get("role", event.actor),
                    "content": event.payload.get("content", ""),
                    "created_at": event.timestamp,
                },
            )
        elif event.type == "tool.call":
            self._append_jsonl(self.tool_calls_path, _tool_call_record(event))
        elif event.type == "tool.result":
            self._append_jsonl(self.tool_results_path, _tool_result_record(event))
        elif event.type.startswith("file."):
            self._append_jsonl(self.file_events_path, _file_event_record(event))

    def _coerce_event(
        self,
        event: ContextEvent | str,
        *,
        actor: str | None = None,
        payload: dict[str, Any] | None = None,
        turn_id: str | None = None,
    ) -> ContextEvent:
        if isinstance(event, ContextEvent):
            self._validate_event_scope(event)
            return event
        if actor is None:
            raise TypeError("actor is required when appending by event type")
        return ContextEvent(
            id=f"evt_{uuid.uuid4().hex}",
            session_id=self.case_run_ref.session_id,
            case_id=self.case_run_ref.case_id,
            case_run_id=self.case_run_ref.case_run_id,
            turn_id=turn_id,
            type=event,
            timestamp=_now(),
            actor=actor,  # type: ignore[arg-type]
            payload=payload or {},
        )

    def _validate_event_scope(self, event: ContextEvent) -> None:
        expected = self.case_run_ref
        if event.session_id != expected.session_id:
            raise ValueError("event session_id does not match this run")
        if event.case_id != expected.case_id:
            raise ValueError("event case_id does not match this run")
        if event.case_run_id != expected.case_run_id:
            raise ValueError("event case_run_id does not match this run")

    @staticmethod
    def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")


def _tool_call_record(event: ContextEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "session_id": event.session_id,
        "case_id": event.case_id,
        "case_run_id": event.case_run_id,
        "turn_id": event.turn_id,
        "tool_name": event.payload.get("tool_name") or event.payload.get("name"),
        "args": event.payload.get("args", {}),
        "created_at": event.timestamp,
    }


def _tool_result_record(event: ContextEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "session_id": event.session_id,
        "case_id": event.case_id,
        "case_run_id": event.case_run_id,
        "turn_id": event.turn_id,
        "tool_call_id": event.payload.get("tool_call_id"),
        "status": event.payload.get("status", "success"),
        "result": event.payload.get("result", event.payload.get("content")),
        "created_at": event.timestamp,
    }


def _file_event_record(event: ContextEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "session_id": event.session_id,
        "case_id": event.case_id,
        "case_run_id": event.case_run_id,
        "turn_id": event.turn_id,
        "type": event.type,
        "path": event.payload.get("path"),
        "content_hash": event.payload.get("content_hash") or event.payload.get("hash"),
        "created_at": event.timestamp,
        "payload": event.payload,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
