from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pygent import thaw_json

from lora.core.io import append_jsonl
from lora.schema import CaseRunRef
from lora_api.container import ApiContext
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
    async for event in run.events(
        after=request.after_sequence,
        log_model_text_deltas=request.log_model_text_deltas,
    ):
        yield _sse(event)


@dataclass(slots=True)
class ActiveChatRun:
    runtime_service: Any
    manager: Any
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
    recovery_execution_id: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def execution_id(self) -> str | None:
        return None if self.execution_handle is None else self.execution_handle.execution_id

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run_turn(), name=f"chat-run:{self.run_ref.case_run_id}")
        await self.ready.wait()

    async def events(
        self,
        *,
        after: int | None,
        log_model_text_deltas: bool = False,
    ) -> AsyncIterator[ExecutionEvent]:
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
                pending_event: asyncio.Task[Any] | None = None
                try:
                    while True:
                        if pending_event is None:
                            pending_event = asyncio.create_task(anext(execution_events))
                        try:
                            raw = await asyncio.wait_for(
                                asyncio.shield(pending_event),
                                CHAT_KEEPALIVE_SECONDS,
                            )
                        except TimeoutError:
                            yield _keepalive_event(handle.execution_id, after or 0)
                            continue
                        except StopAsyncIteration:
                            pending_event = None
                            break
                        pending_event = None
                        event = _execution_event(raw)
                        if log_model_text_deltas:
                            _append_model_text_delta(
                                event,
                                stream_dir=Path(self.run_ref.run_dir),
                            )
                        yield event
                finally:
                    if pending_event is not None and not pending_event.done():
                        pending_event.cancel()
                        await asyncio.gather(pending_event, return_exceptions=True)
        finally:
            async with self.lock:
                self.subscribers -= 1
                if not self.done and self.subscribers == 0:
                    self._schedule_disconnect_cancel_locked()

    async def _run_turn(self) -> None:
        status = "error"
        try:
            async with self.registry.session_execution(self.run_ref.session_id):
                deadline = asyncio.get_running_loop().time() + 30 * 60
                if self.recovery_execution_id is None:
                    self.execution_handle = await self.runtime_service.start_turn(
                        manager=self.manager,
                        message=self.request.message or "",
                        run_ref=self.run_ref,
                        turn_id=(
                            self.request.turn_id
                            or f"turn-{self.run_ref.case_run_id[-8:]}"
                        ),
                        interactive_approvals=True,
                        deadline=deadline,
                    )
                else:
                    self.execution_handle = await self.runtime_service.recover_turn(
                        self.recovery_execution_id,
                        deadline=deadline,
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
            self.manager.finish_case_run(self.run_ref, self.status)
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
            approval_timeout = self.runtime_service.config.runtime_approvals.timeout_seconds
            await asyncio.sleep(max(self.registry.disconnect_grace_seconds, approval_timeout))
            async with self.lock:
                should_cancel = not self.done and self.subscribers == 0
            if should_cancel and self.execution_handle is not None:
                await self.execution_handle.cancel()


@dataclass(slots=True)
class AttachedExecutionRun:
    execution_handle: Any
    run_ref: CaseRunRef | None = None

    async def events(
        self,
        *,
        after: int | None,
        log_model_text_deltas: bool = False,
    ) -> AsyncIterator[ExecutionEvent]:
        async with self.execution_handle.subscribe(after=after) as events:
            async for raw in events:
                event = _execution_event(raw)
                if log_model_text_deltas and self.run_ref is not None:
                    _append_model_text_delta(
                        event,
                        stream_dir=Path(self.run_ref.run_dir),
                    )
                yield event


class ChatRunRegistry:
    def __init__(self, *, disconnect_grace_seconds: float = CHAT_DISCONNECT_GRACE_SECONDS) -> None:
        self.disconnect_grace_seconds = disconnect_grace_seconds
        self._runs: dict[str, ActiveChatRun] = {}
        self._case_runs: dict[str, ActiveChatRun] = {}
        self._retired_runtimes: set[Any] = set()
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
            snapshot = await handle.snapshot()
            if snapshot.status.terminal:
                return AttachedExecutionRun(handle)
            run_ref = await context.runtime_service.recovery_case_run(
                request.execution_id
            )
            recovered = ActiveChatRun(
                runtime_service=context.runtime_service,
                manager=context.manager,
                request=request,
                run_ref=run_ref,
                registry=self,
                recovery_execution_id=request.execution_id,
            )
            existing_recovery = None
            async with self._lock:
                active = self._runs.get(request.execution_id)
                if active is not None:
                    existing_recovery = active
                else:
                    self._runs[request.execution_id] = recovered
                    self._case_runs[run_ref.case_run_id] = recovered
            if existing_recovery is not None:
                await existing_recovery.ready.wait()
                return existing_recovery
            await recovered.start()
            return recovered
        if not request.message:
            raise ValueError("message is required when execution_id is not provided")
        service = SessionService(context.manager)
        session_id = request.session_id or service.create_session(case_id=request.case_id, mode="chat").session_id
        service.save_title_from_user_input(session_id, request.message)
        run_ref = context.manager.start_case_run(session_id, request.case_id, run_config=context.config)
        active = ActiveChatRun(
            runtime_service=context.runtime_service,
            manager=context.manager,
            request=request.model_copy(update={"session_id": session_id}),
            run_ref=run_ref,
            registry=self,
        )
        async with self._lock:
            self._case_runs[run_ref.case_run_id] = active
        await active.start()
        return active

    async def attach(self, active: ActiveChatRun) -> None:
        if active.execution_id is not None:
            async with self._lock:
                self._runs[active.execution_id] = active

    async def remove(self, active: ActiveChatRun) -> None:
        runtime_to_close = None
        async with self._lock:
            if active.execution_id is not None and self._runs.get(active.execution_id) is active:
                del self._runs[active.execution_id]
            if self._case_runs.get(active.run_ref.case_run_id) is active:
                del self._case_runs[active.run_ref.case_run_id]
            if active.runtime_service in self._retired_runtimes and not self._runtime_in_use_locked(
                active.runtime_service
            ):
                self._retired_runtimes.remove(active.runtime_service)
                runtime_to_close = active.runtime_service
        if runtime_to_close is not None:
            await runtime_to_close.close(cancel=False)

    async def deliver_approval(
        self,
        context: ApiContext,
        approval_id: str,
        *,
        approved: bool,
        comment: str,
    ) -> bool:
        case_run_id = approval_id.split(":", 1)[0]
        async with self._lock:
            active = self._case_runs.get(case_run_id)
        runtime = context.runtime_service if active is None else active.runtime_service
        return await runtime.deliver_approval(
            approval_id,
            approved=approved,
            comment=comment,
        )

    async def retire_runtime(self, runtime: Any) -> None:
        async with self._lock:
            if self._runtime_in_use_locked(runtime):
                self._retired_runtimes.add(runtime)
                return
        await runtime.close(cancel=False)

    def _runtime_in_use_locked(self, runtime: Any) -> bool:
        return any(not run.done and run.runtime_service is runtime for run in self._case_runs.values())

    async def close(self) -> None:
        async with self._lock:
            runs = tuple(self._case_runs.values())
            self._runs.clear()
            self._case_runs.clear()
            retired_runtimes = tuple(self._retired_runtimes)
            self._retired_runtimes.clear()
        for run in runs:
            if run.task is not None and not run.task.done():
                run.task.cancel()
        if runs:
            await asyncio.gather(
                *(run.task for run in runs if run.task is not None),
                return_exceptions=True,
            )
        if retired_runtimes:
            await asyncio.gather(
                *(runtime.close(cancel=True) for runtime in retired_runtimes),
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


def _keepalive_event(execution_id: str, sequence: int) -> ExecutionEvent:
    timestamp = time.time_ns()
    return ExecutionEvent(
        schema_version="1",
        event_id=f"lora-transport-keepalive-{timestamp}",
        execution_id=execution_id,
        attempt_id="transport",
        trace_id="transport",
        span_id="transport",
        sequence=sequence,
        timestamp_unix_ns=timestamp,
        module_path="lora.transport",
        kind="lora.transport.keepalive",
    )


def _append_model_text_delta(event: ExecutionEvent, *, stream_dir: Path) -> None:
    if event.kind != "model.text.delta":
        return
    text = event.data.get("text")
    if not isinstance(text, str) or not text:
        return
    append_jsonl(
        stream_dir / "streams" / "model_text_deltas.jsonl",
        {
            "execution_id": event.execution_id,
            "sequence": event.sequence,
            "timestamp_unix_ns": event.timestamp_unix_ns,
            "module_path": event.module_path,
            "text": text,
        },
    )


def _sse(event: ExecutionEvent) -> str:
    if event.kind == "lora.transport.keepalive":
        return ": keep-alive\n\n"
    return f"event: execution.event\ndata: {json.dumps(event.model_dump(), ensure_ascii=False, sort_keys=True)}\n\n"
