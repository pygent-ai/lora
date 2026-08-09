from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from pygent import thaw_json

from lora.schema import CaseRunRef
from lora_api.dependencies import ApiContext
from lora_api.models.events import ExecutionEvent
from lora_api.models.requests import ChatTurnRequest
from lora_api.services.session_service import SessionService

CHAT_DISCONNECT_GRACE_SECONDS = 60.0
CHAT_KEEPALIVE_SECONDS = 10.0


async def stream_chat_turn(
    context: ApiContext,
    request: ChatTurnRequest,
    *,
    registry: ChatRunRegistry | None = None,
) -> AsyncIterator[str]:
    registry = registry or context.chat_registry
    run = await registry.resolve(context, request)
    if run is None:
        yield _sse(
            ExecutionEvent(
                schema_version="1",
                event_id="lora-transport-error",
                execution_id=request.execution_id or "",
                attempt_id="transport",
                trace_id="transport",
                span_id="transport",
                sequence=0,
                timestamp_unix_ns=0,
                module_path="lora.transport",
                kind="lora.transport.error",
                data={"error": "execution not found", "error_type": "ExecutionNotFound"},
            )
        )
        return
    async for event in run.events(after=request.after_sequence):
        yield _sse(event)


@dataclass(slots=True)
class ActiveChatRun:
    context: ApiContext
    request: ChatTurnRequest
    run_ref: CaseRunRef
    registry: ChatRunRegistry
    done: bool = False
    status: str = "running"
    task: asyncio.Task[None] | None = None
    execution_handle: Any | None = None
    disconnect_timer: asyncio.Task[None] | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    subscribers: int = 0
    startup_error: BaseException | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def execution_id(self) -> str | None:
        return None if self.execution_handle is None else self.execution_handle.execution_id

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run_turn(), name=f"chat-run:{self.run_ref.case_run_id}")
        await self.ready.wait()

    async def events(self, *, after: int | None) -> AsyncIterator[ExecutionEvent]:
        if self.startup_error is not None and self.execution_handle is None:
            yield ExecutionEvent(
                schema_version="1",
                event_id="lora-transport-error",
                execution_id="",
                attempt_id="transport",
                trace_id="transport",
                span_id="transport",
                sequence=0,
                timestamp_unix_ns=0,
                module_path="lora.transport",
                kind="lora.transport.error",
                data={
                    "error": str(self.startup_error),
                    "error_type": type(self.startup_error).__name__,
                },
            )
            return
        handle = self.execution_handle
        if handle is None:
            return
        async with self.lock:
            self.subscribers += 1
            self._cancel_disconnect_timer_locked()
        try:
            async with handle.subscribe(after=after) as execution_events:
                while True:
                    try:
                        raw = await asyncio.wait_for(anext(execution_events), CHAT_KEEPALIVE_SECONDS)
                    except TimeoutError:
                        yield ExecutionEvent(
                            execution_id=handle.execution_id,
                            sequence=after or 0,
                            kind="lora.transport.keepalive",
                        )
                        continue
                    except StopAsyncIteration:
                        break
                    yield _execution_event(raw)
        finally:
            async with self.lock:
                self.subscribers -= 1
                if not self.done and self.subscribers == 0:
                    self._schedule_disconnect_cancel_locked()

    async def _run_turn(self) -> None:
        status = "error"
        try:
            async with self.registry.session_execution(self.run_ref.session_id):
                service = self.context.runtime_service
                self.execution_handle = await service.start_turn(
                    manager=self.context.manager,
                    message=self.request.message or "",
                    run_ref=self.run_ref,
                    turn_id=self.request.turn_id or f"turn-{self.run_ref.case_run_id[-8:]}",
                    interactive_approvals=True,
                    deadline=asyncio.get_running_loop().time() + 30 * 60,
                )
                await self.registry.attach(self)
                self.ready.set()
                output, _ = await self.execution_handle.result()
                result = dict(thaw_json(output.data).get("result") or {})
                status = str(result.get("status") or "passed")
        except asyncio.CancelledError:
            status = "skipped"
            if self.execution_handle is not None:
                await self.execution_handle.cancel()
        except BaseException as exc:
            self.startup_error = exc
        finally:
            self.done = True
            self.status = status if status in {"passed", "failed", "error", "skipped"} else "error"
            self.ready.set()
            self.context.manager.finish_case_run(self.run_ref, self.status)
            await self.registry.remove(self)

    def _schedule_disconnect_cancel_locked(self) -> None:
        if self.disconnect_timer is None or self.disconnect_timer.done():
            self.disconnect_timer = asyncio.create_task(self._cancel_after_disconnect_grace())

    def _cancel_disconnect_timer_locked(self) -> None:
        if self.disconnect_timer is not None and not self.disconnect_timer.done():
            self.disconnect_timer.cancel()
        self.disconnect_timer = None

    async def _cancel_after_disconnect_grace(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(self.registry.disconnect_grace_seconds)
            async with self.lock:
                should_cancel = not self.done and self.subscribers == 0
            if should_cancel and self.execution_handle is not None:
                await self.execution_handle.cancel()


@dataclass(slots=True)
class AttachedExecutionRun:
    execution_handle: Any

    async def events(self, *, after: int | None) -> AsyncIterator[ExecutionEvent]:
        async with self.execution_handle.subscribe(after=after) as events:
            async for raw in events:
                yield _execution_event(raw)


class ChatRunRegistry:
    def __init__(self, *, disconnect_grace_seconds: float = CHAT_DISCONNECT_GRACE_SECONDS) -> None:
        self.disconnect_grace_seconds = disconnect_grace_seconds
        self._runs: dict[str, ActiveChatRun] = {}
        self._session_gates: dict[str, _SessionGate] = {}
        self._lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def session_execution(self, session_id: str) -> AsyncIterator[None]:
        async with self._lock:
            gate = self._session_gates.setdefault(session_id, _SessionGate())
            gate.users += 1
        try:
            async with gate.lock:
                yield
        finally:
            async with self._lock:
                gate.users -= 1
                if gate.users == 0 and self._session_gates.get(session_id) is gate:
                    del self._session_gates[session_id]

    async def resolve(
        self, context: ApiContext, request: ChatTurnRequest
    ) -> ActiveChatRun | AttachedExecutionRun | None:
        if request.execution_id:
            async with self._lock:
                active = self._runs.get(request.execution_id)
            if active is not None:
                return active
            await context.runtime_service.initialize()
            try:
                handle = await context.runtime_service.runtime.get_execution_handle(
                    request.execution_id
                )
            except KeyError:
                return None
            return AttachedExecutionRun(handle)
        if not request.message:
            raise ValueError("message is required when execution_id is not provided")
        service = SessionService(context.manager)
        session_id = request.session_id or service.create_session(case_id=request.case_id, mode="chat").session_id
        service.save_title_from_user_input(session_id, request.message)
        run_ref = context.manager.start_case_run(session_id, request.case_id, run_config=context.config)
        active = ActiveChatRun(
            context=context,
            request=request.model_copy(update={"session_id": session_id}),
            run_ref=run_ref,
            registry=self,
        )
        await active.start()
        return active

    async def attach(self, active: ActiveChatRun) -> None:
        if active.execution_id is not None:
            async with self._lock:
                self._runs[active.execution_id] = active

    async def remove(self, active: ActiveChatRun) -> None:
        if active.execution_id is not None:
            async with self._lock:
                if self._runs.get(active.execution_id) is active:
                    del self._runs[active.execution_id]

    async def close(self) -> None:
        async with self._lock:
            runs = tuple(self._runs.values())
            self._runs.clear()
        for run in runs:
            if run.task is not None and not run.task.done():
                run.task.cancel()
        if runs:
            await asyncio.gather(
                *(run.task for run in runs if run.task is not None),
                return_exceptions=True,
            )


@dataclass(slots=True)
class _SessionGate:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


def _execution_event(raw: Any) -> ExecutionEvent:
    if isinstance(raw, dict):
        value = thaw_json(raw)
    else:
        value = {
            "schema_version": raw.schema_version,
            "event_id": raw.event_id,
            "execution_id": raw.execution_id,
            "attempt_id": raw.attempt_id,
            "trace_id": raw.trace_id,
            "span_id": raw.span_id,
            "parent_span_id": raw.parent_span_id,
            "sequence": raw.sequence,
            "timestamp_unix_ns": raw.timestamp_unix_ns,
            "kind": raw.kind,
            "module_path": raw.module_path,
            "data": thaw_json(raw.data),
        }
    return ExecutionEvent.model_validate(value)


def _sse(event: ExecutionEvent) -> str:
    if event.kind == "lora.transport.keepalive":
        return ": keep-alive\n\n"
    return f"event: execution.event\ndata: {json.dumps(event.model_dump(), ensure_ascii=False, sort_keys=True)}\n\n"
