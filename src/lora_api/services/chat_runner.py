from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lora.tracing import EventStore
from lora.runtime import AgentRuntimeAdapter, RuntimeMessage
from lora.schema import CaseRunRef

from lora_api.dependencies import ApiContext
from lora_api.models.events import ApiEvent
from lora_api.models.requests import ChatTurnRequest
from lora_api.services.session_service import SessionService

CHAT_DISCONNECT_GRACE_SECONDS = 60.0
CHAT_COMPLETED_RESUME_TTL_SECONDS = 300.0
CHAT_EVENT_BUFFER_LIMIT = 1_000
CHAT_KEEPALIVE_SECONDS = 10.0
TOOL_RESULT_PREVIEW_LIMIT = 1_000


async def stream_chat_turn(
    context: ApiContext,
    request: ChatTurnRequest,
    *,
    registry: ChatRunRegistry | None = None,
) -> AsyncIterator[str]:
    registry = registry or _CHAT_RUN_REGISTRY
    active_run = await registry.start_or_resume(context, request)
    if active_run is None:
        yield _sse(
            ApiEvent(
                type="chat.error",
                session_id=request.session_id,
                case_run_id=request.resume_case_run_id,
                payload={
                    "error": "Chat run is no longer active and cannot be resumed.",
                    "error_type": "ResumeUnavailable",
                },
            )
        )
        return

    queue, replay = await active_run.subscribe(after_sequence=request.resume_from_event)
    try:
        for event in replay:
            yield _sse(event)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=CHAT_KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                break
            yield _sse(event)
    finally:
        await active_run.unsubscribe(queue)


@dataclass(slots=True)
class ActiveChatRun:
    context: ApiContext
    request: ChatTurnRequest
    run_ref: CaseRunRef
    registry: ChatRunRegistry
    events: deque[ApiEvent] = field(default_factory=lambda: deque(maxlen=CHAT_EVENT_BUFFER_LIMIT))
    subscribers: set[asyncio.Queue[ApiEvent | None]] = field(default_factory=set)
    next_sequence: int = 0
    done: bool = False
    status: str = "running"
    task: asyncio.Task[None] | None = None
    disconnect_timer: asyncio.Task[None] | None = None
    cleanup_timer: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def session_id(self) -> str:
        return self.run_ref.session_id

    @property
    def case_run_id(self) -> str:
        return self.run_ref.case_run_id

    async def start(self) -> None:
        await self.publish(
            ApiEvent(
                type="chat.started",
                session_id=self.session_id,
                case_run_id=self.case_run_id,
                payload=self.run_ref.to_dict(),
            )
        )
        self.task = asyncio.create_task(self._run_turn(), name=f"chat-run:{self.case_run_id}")

    async def subscribe(
        self,
        *,
        after_sequence: int | None,
    ) -> tuple[asyncio.Queue[ApiEvent | None], list[ApiEvent]]:
        queue: asyncio.Queue[ApiEvent | None] = asyncio.Queue()
        async with self.lock:
            self._cancel_disconnect_timer_locked()
            replay = [
                event
                for event in self.events
                if after_sequence is None or (event.sequence or 0) > after_sequence
            ]
            if self.done:
                queue.put_nowait(None)
            else:
                self.subscribers.add(queue)
        return queue, replay

    async def unsubscribe(self, queue: asyncio.Queue[ApiEvent | None]) -> None:
        async with self.lock:
            self.subscribers.discard(queue)
            if not self.subscribers and not self.done:
                self._schedule_disconnect_cancel_locked()

    async def publish(self, event: ApiEvent) -> None:
        async with self.lock:
            self.next_sequence += 1
            sequenced = event.model_copy(update={"sequence": self.next_sequence})
            self.events.append(sequenced)
            subscribers = list(self.subscribers)
        for queue in subscribers:
            queue.put_nowait(sequenced)

    async def finish(self, status: str) -> None:
        async with self.lock:
            self.done = True
            self.status = status
            self._cancel_disconnect_timer_locked()
            subscribers = list(self.subscribers)
            self.subscribers.clear()
        for queue in subscribers:
            queue.put_nowait(None)
        self.registry.schedule_cleanup(self)

    def _schedule_disconnect_cancel_locked(self) -> None:
        if self.disconnect_timer is not None and not self.disconnect_timer.done():
            return
        self.disconnect_timer = asyncio.create_task(self._cancel_after_disconnect_grace())

    def _cancel_disconnect_timer_locked(self) -> None:
        if self.disconnect_timer is not None and not self.disconnect_timer.done():
            self.disconnect_timer.cancel()
        self.disconnect_timer = None

    async def _cancel_after_disconnect_grace(self) -> None:
        try:
            await asyncio.sleep(self.registry.disconnect_grace_seconds)
            async with self.lock:
                should_cancel = not self.done and not self.subscribers
                task = self.task
            if should_cancel and task is not None and not task.done():
                task.cancel()
        except asyncio.CancelledError:
            return

    async def _run_turn(self) -> None:
        status = "error"
        try:
            session = self.context.manager.load(self.session_id)
            adapter = AgentRuntimeAdapter(config=self.context.config, session_manager=self.context.manager)
            result = await adapter.run_turn(
                session=session,
                user_input=self.request.message,
                case_run_ref=self.run_ref,
                turn_id=self.request.turn_id,
                on_assistant_delta=self._on_assistant_delta,
                on_assistant_reasoning_delta=self._on_assistant_reasoning_delta,
                on_runtime_message=self._on_runtime_message,
            )
            status = str(result.get("status") or "passed")
            await self.publish(
                ApiEvent(
                    type="chat.completed",
                    session_id=self.session_id,
                    case_run_id=self.case_run_id,
                    payload=result,
                )
            )
        except asyncio.CancelledError:
            status = "skipped"
            await self.publish(
                ApiEvent(
                    type="chat.cancelled",
                    session_id=self.session_id,
                    case_run_id=self.case_run_id,
                    payload={"status": status, "reason": "client disconnected before resume timeout"},
                )
            )
        except Exception as exc:  # noqa: BLE001 - API stream should surface runtime failures.
            status = "error"
            await self.publish(
                ApiEvent(
                    type="chat.error",
                    session_id=self.session_id,
                    case_run_id=self.case_run_id,
                    payload={"error": str(exc), "error_type": type(exc).__name__},
                )
            )
        finally:
            self.context.manager.finish_case_run(self.run_ref, _case_run_status(status))
            await self.finish(_case_run_status(status))

    async def _on_assistant_delta(self, delta: str) -> None:
        await self.publish(
            ApiEvent(
                type="assistant.delta",
                session_id=self.session_id,
                case_run_id=self.case_run_id,
                payload={"delta": delta},
            )
        )

    async def _on_assistant_reasoning_delta(self, delta: str) -> None:
        await self.publish(
            ApiEvent(
                type="assistant.reasoning_delta",
                session_id=self.session_id,
                case_run_id=self.case_run_id,
                payload={"delta": delta},
            )
        )

    async def _on_runtime_message(self, message: RuntimeMessage) -> None:
        payload = _tool_result_event_payload(message, run_dir=Path(self.run_ref.run_dir))
        if payload is not None:
            await self.publish(
                ApiEvent(
                    type="tool.result",
                    session_id=self.session_id,
                    case_run_id=self.case_run_id,
                    payload=payload,
                )
            )
            return
        await self.publish(
            ApiEvent(
                type="runtime.message",
                session_id=self.session_id,
                case_run_id=self.case_run_id,
                payload=_runtime_message_payload(message),
            )
        )


class ChatRunRegistry:
    def __init__(
        self,
        *,
        disconnect_grace_seconds: float = CHAT_DISCONNECT_GRACE_SECONDS,
        completed_resume_ttl_seconds: float = CHAT_COMPLETED_RESUME_TTL_SECONDS,
    ) -> None:
        self.disconnect_grace_seconds = disconnect_grace_seconds
        self.completed_resume_ttl_seconds = completed_resume_ttl_seconds
        self._runs: dict[tuple[str, str], ActiveChatRun] = {}
        self._lock = asyncio.Lock()

    async def start_or_resume(self, context: ApiContext, request: ChatTurnRequest) -> ActiveChatRun | None:
        if request.resume_case_run_id:
            if request.session_id is None:
                return None
            return await self.get(request.session_id, request.resume_case_run_id)
        return await self.start(context, request)

    async def start(self, context: ApiContext, request: ChatTurnRequest) -> ActiveChatRun:
        service = SessionService(context.manager)
        session_id = request.session_id
        if session_id is None:
            session_id = service.create_session(case_id=request.case_id, mode="chat").session_id
        service.save_title_from_user_input(session_id, request.message)

        run_ref = context.manager.start_case_run(session_id, request.case_id, run_config=context.config)
        active_run = ActiveChatRun(
            context=context,
            request=request.model_copy(update={"session_id": session_id}),
            run_ref=run_ref,
            registry=self,
        )
        async with self._lock:
            self._runs[(active_run.session_id, active_run.case_run_id)] = active_run
        await active_run.start()
        return active_run

    async def get(self, session_id: str, case_run_id: str) -> ActiveChatRun | None:
        async with self._lock:
            return self._runs.get((session_id, case_run_id))

    def schedule_cleanup(self, active_run: ActiveChatRun) -> None:
        if active_run.cleanup_timer is not None and not active_run.cleanup_timer.done():
            return
        active_run.cleanup_timer = asyncio.create_task(self._cleanup_later(active_run))

    async def _cleanup_later(self, active_run: ActiveChatRun) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(self.completed_resume_ttl_seconds)
            async with self._lock:
                key = (active_run.session_id, active_run.case_run_id)
                if self._runs.get(key) is active_run:
                    del self._runs[key]


def _runtime_message_payload(message: RuntimeMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "reasoning_content": message.reasoning_content,
        "message_type": message.type,
        "payload": message.payload or {},
        "is_delta": message.is_delta,
    }


def _tool_result_event_payload(message: RuntimeMessage, *, run_dir: Path) -> dict[str, Any] | None:
    if message.role != "tool":
        return None
    parsed = _parse_json_object(message.content)
    payload = message.payload or {}
    tool_call_id = str(payload.get("tool_call_id") or parsed.get("tool_call_id") or "").strip()
    if not tool_call_id:
        return None
    error = parsed.get("error")
    result = parsed.get("result", parsed.get("content", message.content))
    status = str(parsed.get("status") or ("error" if error else "success"))
    result_source = error if error else result
    result_text = _stringify(result_source)
    preview = _preview_text(result_text)
    result_size = _serialized_size(result_source)
    return {
        "tool_call_id": tool_call_id,
        "tool_name": _tool_name_for_call(run_dir, tool_call_id),
        "status": status,
        "preview": preview,
        "result_size": result_size,
        "truncated": len(result_text) > TOOL_RESULT_PREVIEW_LIMIT,
        "result_ref": f"/tool-results/{tool_call_id}",
    }


def _parse_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _preview_text(text: str) -> str:
    return text if len(text) <= TOOL_RESULT_PREVIEW_LIMIT else f"{text[:TOOL_RESULT_PREVIEW_LIMIT]}..."


def _serialized_size(value: Any) -> int:
    return len(_stringify(value).encode("utf-8"))


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _tool_name_for_call(run_dir: Path, tool_call_id: str) -> str:
    for row in EventStore.iter_jsonl(run_dir / "tool_calls.jsonl") or []:
        if tool_call_id in {
            str(row.get("event_id") or ""),
            str(row.get("tool_call_id") or ""),
            str(row.get("model_tool_call_id") or ""),
        }:
            return str(row.get("tool_name") or "tool")
    return "tool"


def _case_run_status(status: str) -> str:
    return status if status in {"passed", "failed", "error", "skipped"} else "error"


def _sse(event: ApiEvent) -> str:
    data = json.dumps(event.model_dump(), ensure_ascii=False, sort_keys=True)
    return f"event: {event.type}\ndata: {data}\n\n"


_CHAT_RUN_REGISTRY = ChatRunRegistry()
