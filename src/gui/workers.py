from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from lora.runtime import AgentRuntimeAdapter, RuntimeMessage
from lora.schema import RunConfig
from lora.session import SessionManager

try:  # Keep pure helper tests importable before PySide6 is installed.
    from PySide6.QtCore import QObject, Signal, Slot
except ModuleNotFoundError:  # pragma: no cover - exercised only in dependency bootstrap.
    QObject = object  # type: ignore[assignment,misc]

    class Signal:  # type: ignore[no-redef]
        def __init__(self, *types: object):
            self.types = types

        def emit(self, *args: object) -> None:
            return None

    def Slot(*types: object):  # type: ignore[no-redef]
        def decorate(function: object) -> object:
            return function

        return decorate


@dataclass(frozen=True, slots=True)
class InspectorEvent:
    kind: str
    title: str
    detail: str
    tone: str = "neutral"
    metadata: dict[str, Any] = field(default_factory=dict)


def runtime_message_to_inspector_event(message: RuntimeMessage) -> InspectorEvent:
    return runtime_message_to_inspector_events(message)[0]


def runtime_message_to_inspector_events(message: RuntimeMessage) -> list[InspectorEvent]:
    if message.role == "assistant":
        tool_calls = _tool_calls(message)
        if tool_calls:
            return [
                InspectorEvent(
                    kind="tool",
                    title=f"Tool call: {_tool_call_name(tool_call)}",
                    detail=_tool_call_arguments(tool_call),
                    tone="accent",
                    metadata={"tool_call_id": _tool_call_id(tool_call)},
                )
                for tool_call in tool_calls
            ]
        return [
            InspectorEvent(
            kind="event",
            title="Assistant message",
            detail=message.content.strip(),
            tone="neutral",
            )
        ]

    if message.role == "tool":
        payload = _parse_json_object(message.content)
        status = str(payload.get("status") or "result") if payload else "result"
        tool_call_id = str((message.payload or {}).get("tool_call_id") or payload.get("tool_call_id") or "tool")
        detail_source = payload.get("result") if payload and "result" in payload else message.content
        if payload and payload.get("error"):
            detail_source = payload["error"]
        tone = "error" if status == "error" or (payload and payload.get("error")) else "ok"
        return [
            InspectorEvent(
                kind="tool",
                title=f"Tool result: {tool_call_id} {status}",
                detail=_stringify_detail(detail_source),
                tone=tone,
                metadata={"tool_call_id": tool_call_id},
            )
        ]

    return [InspectorEvent(kind="event", title=f"{message.role} message", detail=message.content, tone="neutral")]


class ChatTurnWorker(QObject):
    assistant_delta = Signal(str)
    runtime_event = Signal(object)
    run_started = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        config: RunConfig,
        manager: SessionManager,
        session_id: str,
        user_input: str,
        turn_index: int,
    ):
        super().__init__()
        self.config = config
        self.manager = manager
        self.session_id = session_id
        self.user_input = user_input
        self.turn_index = turn_index

    @Slot()
    def run(self) -> None:
        run_ref = None
        status = "error"
        try:
            run_ref = self.manager.start_case_run(self.session_id, "chat", run_config=self.config)
            self.run_started.emit(run_ref)
            session = self.manager.load(self.session_id)
            adapter = AgentRuntimeAdapter(config=self.config, session_manager=self.manager)
            result = asyncio.run(
                adapter.run_turn(
                    session=session,
                    user_input=self.user_input,
                    case_run_ref=run_ref,
                    turn_id=f"turn-{self.turn_index:04d}",
                    on_assistant_delta=self.assistant_delta.emit,
                    on_runtime_message=self._emit_runtime_message,
                )
            )
            status = str(result["status"])
            self.completed.emit({**run_ref.to_dict(), **result})
        except Exception as exc:  # noqa: BLE001 - Qt worker boundary reports readable failures.
            self.failed.emit(str(exc))
        finally:
            if run_ref is not None:
                self.manager.finish_case_run(run_ref, status)

    def _emit_runtime_message(self, message: RuntimeMessage) -> None:
        for event in runtime_message_to_inspector_events(message):
            self.runtime_event.emit(event)


def _tool_calls(message: RuntimeMessage) -> list[dict[str, Any]]:
    payload = message.payload or {}
    tool_calls = payload.get("tool_calls") or []
    return [tool_call for tool_call in tool_calls if isinstance(tool_call, dict)]


def _tool_call_name(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"])
    return str(tool_call.get("name") or tool_call.get("tool_name") or "tool")


def _tool_call_id(tool_call: dict[str, Any]) -> str:
    return str(tool_call.get("id") or tool_call.get("tool_call_id") or "")


def _tool_call_arguments(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function")
    if isinstance(function, dict) and function.get("arguments") is not None:
        return str(function["arguments"])
    arguments = tool_call.get("arguments")
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _stringify_detail(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
