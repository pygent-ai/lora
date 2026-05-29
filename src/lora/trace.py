from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redaction import redact_secrets
from .schema import CaseRunRef, ContextEvent


DESIGN_EVENT_TYPES = frozenset(
    {
        "case.started",
        "case.finished",
        "conversation.user_message",
        "conversation.assistant_message",
        "conversation.tool_message",
        "model.request",
        "model.response",
        "prompt.rendered",
        "tool.call",
        "tool.result",
        "file.read",
        "file.write",
        "file.edit",
        "file.delete",
        "context.checkpoint",
        "context.projection_created",
        "analysis.created",
        "test.generated",
        "repair.started",
        "repair.patch_created",
        "repair.finished",
        "regression.started",
        "regression.finished",
    }
)

LORA_EVENT_TYPES = DESIGN_EVENT_TYPES | frozenset(
    {
        "chat.started",
        "chat.finished",
        "runtime.error",
        "error",
    }
)


class EventStore:
    def __init__(self, case_run_ref: CaseRunRef):
        self.case_run_ref = case_run_ref
        self.run_dir = Path(case_run_ref.run_dir)
        self.session_dir = _find_session_dir(self.run_dir)
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
        persisted_source = _clone_event(event)
        if persisted_source.type == "prompt.rendered":
            self._persist_rendered_prompt(persisted_source)
        persisted_event = redact_secrets(persisted_source.to_dict())
        self._append_jsonl(self.events_path, persisted_event)
        if event.type.startswith("conversation."):
            record = redact_secrets(_message_record(event))
            self._append_jsonl(
                self.messages_path,
                record,
            )
            self._append_session_history(record)
        elif event.type == "tool.call":
            record = redact_secrets(_tool_call_record(event))
            self._append_jsonl(self.tool_calls_path, record)
            self._append_session_log("tool_calls.jsonl", record)
        elif event.type == "tool.result":
            record = redact_secrets(_tool_result_record(event))
            self._append_jsonl(self.tool_results_path, record)
            self._append_session_log("tool_results.jsonl", record)
        elif event.type.startswith("file."):
            record = redact_secrets(_file_event_record(event))
            self._append_jsonl(self.file_events_path, record)
            self._append_session_log("file_events.jsonl", record)

    def _append_session_history(self, record: dict[str, Any]) -> None:
        if self.session_dir is None:
            return
        path = self.session_dir / "context" / "history.jsonl"
        row = {"seq": _next_jsonl_seq(path), **record}
        self._append_jsonl(path, row)

    def _append_session_log(self, name: str, record: dict[str, Any]) -> None:
        if self.session_dir is None:
            return
        self._append_jsonl(self.session_dir / "logs" / name, record)

    def _persist_rendered_prompt(self, event: ContextEvent) -> None:
        prompt = event.payload.pop("prompt", None)
        if prompt is None:
            prompt = event.payload.pop("content", None)
        if prompt is None:
            prompt = event.payload.pop("text", None)
        if not isinstance(prompt, str):
            return
        persisted_prompt = redact_secrets(prompt)

        prompt_file_name = _turn_file_name(event.turn_id)
        run_prompt_dir = self.run_dir / "rendered_prompts"
        run_text_path = run_prompt_dir / f"{prompt_file_name}.txt"
        run_metadata_path = run_prompt_dir / f"{prompt_file_name}.json"
        prompt_metadata = {
            "event_id": event.id,
            "session_id": event.session_id,
            "case_id": event.case_id,
            "case_run_id": event.case_run_id,
            "turn_id": event.turn_id,
            "created_at": event.timestamp,
            "prompt_hash": event.payload.get("prompt_hash"),
            "module_ids": event.payload.get("module_ids", []),
            "modules": event.payload.get("modules", []),
            "dynamic_inputs": event.payload.get("dynamic_inputs", {}),
        }
        _write_text(run_text_path, persisted_prompt)
        _write_json(run_metadata_path, prompt_metadata)
        event.payload["prompt_text_path"] = str(run_text_path)
        event.payload["prompt_metadata_path"] = str(run_metadata_path)

        if self.session_dir is None:
            return
        session_prompt_dir = self.session_dir / "context" / "rendered_prompts" / str(event.case_run_id or "session")
        session_text_path = session_prompt_dir / f"{prompt_file_name}.txt"
        session_metadata_path = session_prompt_dir / f"{prompt_file_name}.json"
        _write_text(session_text_path, persisted_prompt)
        _write_json(session_metadata_path, prompt_metadata)
        self._append_jsonl(
            self.session_dir / "logs" / "rendered_prompts.jsonl",
            {
                **prompt_metadata,
                "prompt_text_path": str(session_text_path),
                "prompt_metadata_path": str(session_metadata_path),
            },
        )

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
            self._validate_event_type(event.type)
            return event
        if actor is None:
            raise TypeError("actor is required when appending by event type")
        self._validate_event_type(event)
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
    def _validate_event_type(event_type: str) -> None:
        if event_type not in LORA_EVENT_TYPES:
            raise ValueError(f"Unsupported event type: {event_type}")

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


def _clone_event(event: ContextEvent) -> ContextEvent:
    return ContextEvent.from_dict(deepcopy(event.to_dict()))


def _message_record(event: ContextEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "session_id": event.session_id,
        "case_id": event.case_id,
        "case_run_id": event.case_run_id,
        "turn_id": event.turn_id,
        "role": event.payload.get("role", event.actor),
        "content": event.payload.get("content", ""),
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


def _find_session_dir(run_dir: Path) -> Path | None:
    for parent in [run_dir, *run_dir.parents]:
        if (parent / "session.json").exists():
            return parent
    return None


def _turn_file_name(turn_id: str | None) -> str:
    return str(turn_id or "turn-unknown")


def _next_jsonl_seq(path: Path) -> int:
    if not path.exists():
        return 1
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count + 1


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
