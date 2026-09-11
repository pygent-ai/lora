from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pygent import thaw_json

from lora.core.io import append_jsonl, read_json
from lora.orchestration import (
    ManagedSessionTurn,
    SessionExecutionCoordinator,
    SessionTurnService,
)
from lora.schema import CaseRunRef
from lora_api.container import ApiContext
from lora_api.models.events import ExecutionEvent
from lora_api.models.requests import ChatTurnRequest
from lora_api.services.session_service import session_service_for_scope

CHAT_KEEPALIVE_SECONDS = 10.0
_TERMINAL_EVENTS = {
    "execution.completed",
    "execution.failed",
    "execution.cancelled",
    "execution.deadline_exceeded",
}


def _with_run_timing(
    event: ExecutionEvent, manager: Any, ref: CaseRunRef
) -> ExecutionEvent:
    if event.kind not in _TERMINAL_EVENTS | {"execution.started", "lora.chat.started"}:
        return event
    return event.model_copy(
        update={
            "data": {**event.data, "run_timing": manager.run_timing(ref)},
        }
    )


async def stream_chat_turn(
    context: ApiContext,
    request: ChatTurnRequest,
    *,
    registry: ChatRunRegistry | None = None,
) -> AsyncIterator[str]:
    active_registry = registry if registry is not None else context.chat_registry
    try:
        run = await active_registry.resolve(context, request)
    except Exception as exc:
        yield _sse(_transport_error_event(request.execution_id or "", exc))
        return
    if run is None:
        yield _sse(
            _transport_error_event(
                request.execution_id or "", LookupError("execution not found")
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
    """SSE-facing view over a transport-independent managed session turn."""

    turn: ManagedSessionTurn
    subscribers: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def execution_id(self) -> str | None:
        return self.turn.execution_id

    @property
    def execution_handle(self) -> Any | None:
        return self.turn.execution_handle

    @property
    def run_ref(self) -> CaseRunRef | None:
        return self.turn.run_ref

    @property
    def manager(self) -> Any:
        return self.turn.manager

    @property
    def runtime_service(self) -> Any:
        return self.turn.runtime_service

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self.turn.task

    @property
    def done(self) -> bool:
        return self.turn.done

    @property
    def status(self) -> str:
        return self.turn.status

    @property
    def startup_error(self) -> BaseException | None:
        return self.turn.startup_error

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
        run_ref = self.run_ref
        if run_ref is None:  # pragma: no cover - ready implies case-run creation
            raise RuntimeError("chat execution has no case run")
        async with self.lock:
            self.subscribers += 1
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
                        if event.kind in _TERMINAL_EVENTS:
                            # Publish completion only after run metadata and history
                            # are durable, so the client's immediate reload is final.
                            await asyncio.shield(self.turn.wait_finalized())
                        event = _with_run_timing(event, self.manager, run_ref)
                        if log_model_text_deltas:
                            _append_model_text_delta(
                                event,
                                stream_dir=Path(run_ref.run_dir),
                            )
                        yield event
                finally:
                    if pending_event is not None and not pending_event.done():
                        pending_event.cancel()
                        await asyncio.gather(pending_event, return_exceptions=True)
            # Publish final session metadata before the client refreshes its view.
            await asyncio.shield(self.turn.wait_finalized())
        finally:
            async with self.lock:
                self.subscribers -= 1


@dataclass(slots=True)
class AttachedExecutionRun:
    execution_handle: Any
    run_ref: CaseRunRef | None = None
    manager: Any | None = None
    lease: Any | None = None

    async def events(
        self,
        *,
        after: int | None,
        log_model_text_deltas: bool = False,
    ) -> AsyncIterator[ExecutionEvent]:
        try:
            async with self.execution_handle.subscribe(after=after) as events:
                async for raw in events:
                    event = _execution_event(raw)
                    if self.manager is not None and self.run_ref is not None:
                        event = _with_run_timing(event, self.manager, self.run_ref)
                    if log_model_text_deltas and self.run_ref is not None:
                        _append_model_text_delta(
                            event,
                            stream_dir=Path(self.run_ref.run_dir),
                        )
                    yield event
        finally:
            if self.lease is not None:
                await self.lease.release()


class ChatRunRegistry:
    """HTTP-facing adapter over the application execution coordinator."""

    def __init__(self, coordinator: SessionExecutionCoordinator | None = None) -> None:
        self.coordinator = coordinator or SessionExecutionCoordinator()

    async def resolve(
        self, context: ApiContext, request: ChatTurnRequest
    ) -> ActiveChatRun | AttachedExecutionRun | None:
        if request.execution_id:
            active = await self.coordinator.find_execution(request.execution_id)
            if active is not None:
                await active.wait_ready()
                return ActiveChatRun(active)
            service = session_service_for_scope(context, request.scope_id)
            manager = service.manager
            run_ref = _request_case_run(manager, request)
            probe_lease = None
            if run_ref is None:
                probe_lease = await context.acquire_runtime(
                    config=manager.config,
                    manager=manager,
                )
                probe_runtime = probe_lease.runtime.runtime_service
                try:
                    run_ref = await probe_runtime.recovery_case_run(
                        request.execution_id
                    )
                finally:
                    await probe_lease.release()
            run_config = manager.load_run_config(
                run_ref,
                credential_source=manager.config,
            )
            lease = await context.acquire_runtime(config=run_config)
            runtime_service = lease.runtime.runtime_service
            try:
                handle = await runtime_service.runtime.get_execution_handle(
                    request.execution_id
                )
            except KeyError:
                await lease.release()
                return None
            try:
                snapshot = await handle.snapshot()
            except BaseException:
                await lease.release()
                raise
            if snapshot.status.terminal:
                return AttachedExecutionRun(
                    handle, run_ref, lease.runtime.manager, lease
                )
            try:
                recovered = await self.coordinator.recover_turn(
                    lease=lease,
                    run_ref=run_ref,
                    execution_id=request.execution_id,
                )
            except BaseException:
                await lease.release()
                raise
            await recovered.wait_ready()
            return ActiveChatRun(recovered)
        if not request.message:
            raise ValueError("message is required when execution_id is not provided")
        service = session_service_for_scope(
            context,
            request.scope_id,
        )
        manager = service.manager
        turn_service = SessionTurnService(
            coordinator=self.coordinator,
            acquire_runtime=context.acquire_runtime,
        )
        turn = await turn_service.submit(
            config=manager.config,
            manager=manager,
            session_id=request.session_id,
            message=request.message,
            case_id=request.case_id,
            turn_id=request.turn_id,
        )
        await turn.wait_ready()
        return ActiveChatRun(turn)

    async def deliver_approval(
        self,
        context: ApiContext,
        approval_id: str,
        *,
        approved: bool,
        comment: str,
    ) -> bool:
        case_run_id = approval_id.split(":", 1)[0]
        active = await self.coordinator.find_case_run(case_run_id)
        if active is not None:
            return await active.runtime_service.deliver_approval(
                approval_id,
                approved=approved,
                comment=comment,
            )
        lease = await context.acquire_runtime()
        try:
            return await lease.runtime.runtime_service.deliver_approval(
                approval_id,
                approved=approved,
                comment=comment,
            )
        finally:
            await lease.release()

    async def close(self) -> None:
        await self.coordinator.close()


def _request_case_run(manager: Any, request: ChatTurnRequest) -> CaseRunRef | None:
    if not request.session_id or not request.execution_id:
        return None
    metadata = manager.show(request.session_id)["metadata"]
    case_run_id = metadata.get("last_case_run_id")
    if not isinstance(case_run_id, str) or not case_run_id:
        return None
    run_ref = manager.find_case_run(request.session_id, case_run_id)
    run_metadata = read_json(Path(run_ref.run_dir) / "run_metadata.json")
    return (
        run_ref
        if run_metadata.get("runtime_execution_id") == request.execution_id
        else None
    )


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


def _transport_error_event(execution_id: str, error: BaseException) -> ExecutionEvent:
    return ExecutionEvent(
        schema_version="1",
        event_id="lora-transport-error",
        execution_id=execution_id,
        attempt_id="transport",
        trace_id="transport",
        span_id="transport",
        sequence=0,
        timestamp_unix_ns=time.time_ns(),
        module_path="lora.transport",
        kind="lora.transport.error",
        data={"error": str(error), "error_type": type(error).__name__},
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
