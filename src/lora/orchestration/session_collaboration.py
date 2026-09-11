from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from pygent.core import independent_execution

from lora.core.io import plain_object
from lora.schema import RunConfig
from lora.sessions import (
    AgentMessage,
    CollaborationOperation,
    CollaborationState,
    SessionCollaborationStore,
    SessionManager,
)

from .managed_turn import ManagedSessionTurn
from .session_turns import SessionTurnService


class SessionCollaborationService:
    """Manage background starts and tool-boundary Agent-message delivery."""

    def __init__(
        self,
        *,
        turns: SessionTurnService,
        lease_seconds: float = 30.0,
        poll_interval: float = 0.1,
    ) -> None:
        if lease_seconds <= 0 or poll_interval <= 0:
            raise ValueError("collaboration timing values must be positive")
        self.turns = turns
        self.lease_seconds = float(lease_seconds)
        self.poll_interval = float(poll_interval)
        self.owner_id = f"worker-{uuid.uuid4().hex}"
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._turns: dict[str, ManagedSessionTurn] = {}
        self._stores: dict[str, SessionCollaborationStore] = {}
        self._accepting = True
        self._lock = asyncio.Lock()

    async def send(
        self,
        *,
        config: RunConfig,
        manager: SessionManager,
        target_session_id: str,
        message: str,
        source_session_id: str | None = None,
        source_agent_alias: str | None = None,
        submission_id: str | None = None,
    ) -> AgentMessage:
        self._validate_request(manager, source_session_id, target_session_id, message)
        async with self._lock:
            if not self._accepting:
                raise RuntimeError("session collaboration service is closing")
        effective_submission_id = submission_id or f"submission-{uuid.uuid4().hex}"
        fingerprint = _fingerprint(
            intent="agent-message",
            source_session_id=source_session_id,
            target_session_id=target_session_id,
            message=message,
            source_agent_alias=source_agent_alias,
        )
        return await asyncio.to_thread(
            self._store(config).enqueue_message,
            submission_id=effective_submission_id,
            fingerprint=fingerprint,
            source_session_id=source_session_id,
            source_agent_alias=source_agent_alias,
            target_session_id=target_session_id,
            content=message,
        )

    async def message_status(
        self,
        *,
        config: RunConfig,
        message_id: str,
    ) -> AgentMessage:
        return await asyncio.to_thread(self._store(config).get_message, message_id)

    async def related_sessions(
        self,
        *,
        config: RunConfig,
        first_session_id: str,
        second_session_id: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._store(config).related_sessions,
            first_session_id,
            second_session_id,
        )

    async def list_operations(
        self,
        *,
        config: RunConfig,
        session_id: str,
        limit: int = 100,
    ) -> list[CollaborationOperation]:
        return await asyncio.to_thread(
            self._store(config).list_operations,
            session_id,
            limit=limit,
        )

    async def wait_message(
        self,
        *,
        config: RunConfig,
        message_id: str,
        timeout: float | None = None,
    ) -> AgentMessage:
        async def _wait() -> AgentMessage:
            while True:
                message = await self.message_status(
                    config=config, message_id=message_id
                )
                if message.done:
                    return message
                await asyncio.sleep(self.poll_interval)

        return (
            await _wait()
            if timeout is None
            else await asyncio.wait_for(_wait(), timeout)
        )

    async def item_status(
        self,
        *,
        config: RunConfig,
        collaboration_id: str,
    ) -> AgentMessage | CollaborationOperation:
        if collaboration_id.startswith("msg-"):
            return await self.message_status(
                config=config,
                message_id=collaboration_id,
            )
        return await self.status(config=config, operation_id=collaboration_id)

    async def wait_any(
        self,
        *,
        config: RunConfig,
        collaboration_ids: tuple[str, ...],
        timeout: float,
    ) -> tuple[tuple[AgentMessage | CollaborationOperation, ...], bool]:
        if not collaboration_ids:
            raise ValueError("collaboration_ids must be non-empty")
        if len(collaboration_ids) > 32:
            raise ValueError("collaboration_ids cannot contain more than 32 items")
        if len(set(collaboration_ids)) != len(collaboration_ids):
            raise ValueError("collaboration_ids must be unique")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        deadline = time.monotonic() + timeout
        while True:
            items = tuple(
                await asyncio.gather(
                    *(
                        self.item_status(config=config, collaboration_id=item_id)
                        for item_id in collaboration_ids
                    )
                )
            )
            if any(item.done for item in items):
                return items, False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return items, True
            await asyncio.sleep(min(self.poll_interval, remaining))

    async def start(
        self,
        *,
        config: RunConfig,
        manager: SessionManager,
        message: str,
        source_session_id: str | None = None,
        submission_id: str | None = None,
        case_id: str = "collaboration",
    ) -> CollaborationOperation:
        return await self._start(
            config=config,
            manager=manager,
            message=message,
            source_session_id=source_session_id,
            submission_id=submission_id,
            case_id=case_id,
            dispatch=True,
        )

    async def enqueue_start(
        self,
        *,
        config: RunConfig,
        manager: SessionManager,
        message: str,
        source_session_id: str | None = None,
        submission_id: str | None = None,
        case_id: str = "collaboration",
    ) -> CollaborationOperation:
        return await self._start(
            config=config,
            manager=manager,
            message=message,
            source_session_id=source_session_id,
            submission_id=submission_id,
            case_id=case_id,
            dispatch=False,
        )

    async def _start(
        self,
        *,
        config: RunConfig,
        manager: SessionManager,
        message: str,
        source_session_id: str | None,
        submission_id: str | None,
        case_id: str,
        dispatch: bool,
    ) -> CollaborationOperation:
        if not message.strip():
            raise ValueError("message must be non-empty")
        if source_session_id is not None:
            manager.load(source_session_id)
        operation = await self._reserve(
            config=config,
            source_session_id=source_session_id,
            target_session_id=None,
            message=message,
            submission_id=submission_id,
            case_id=case_id,
            intent="start",
        )
        store = self._store(config)
        if operation.target_session_id is None:
            operation = await self._prepare_target(
                store,
                operation.operation_id,
                manager=manager,
                case_id=case_id,
            )
        if dispatch:
            await self._schedule(operation.operation_id, config=config, manager=manager)
        return operation

    async def status(
        self, *, config: RunConfig, operation_id: str
    ) -> CollaborationOperation:
        return await asyncio.to_thread(self._store(config).get, operation_id)

    async def wait(
        self,
        *,
        config: RunConfig,
        operation_id: str,
        timeout: float | None = None,
    ) -> CollaborationOperation:
        async def _wait() -> CollaborationOperation:
            while True:
                operation = await self.status(config=config, operation_id=operation_id)
                if operation.done:
                    return operation
                await asyncio.sleep(self.poll_interval)

        return (
            await _wait()
            if timeout is None
            else await asyncio.wait_for(_wait(), timeout)
        )

    async def resume_operation(
        self,
        *,
        config: RunConfig,
        manager: SessionManager,
        operation_id: str,
    ) -> CollaborationOperation:
        operation = await self.status(config=config, operation_id=operation_id)
        if operation.agent_alias != config.agent_alias:
            raise ValueError(
                f"collaboration operation requires agent alias {operation.agent_alias!r}"
            )
        if operation.target_session_id is None:
            raise RuntimeError("collaboration operation has no target session")
        manager.load(operation.target_session_id)
        await self._schedule(operation_id, config=config, manager=manager)
        return operation

    async def aclose(self) -> None:
        async with self._lock:
            if not self._accepting:
                return
            self._accepting = False
            tasks = tuple(self._tasks.values())
            turns = tuple(self._turns.values())
        if turns:
            await asyncio.gather(
                *(turn.cancel() for turn in turns), return_exceptions=True
            )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _reserve(
        self,
        *,
        config: RunConfig,
        source_session_id: str | None,
        target_session_id: str | None,
        message: str,
        submission_id: str | None,
        case_id: str,
        intent: str,
    ) -> CollaborationOperation:
        async with self._lock:
            if not self._accepting:
                raise RuntimeError("session collaboration service is closing")
        effective_submission_id = submission_id or f"submission-{uuid.uuid4().hex}"
        fingerprint = _fingerprint(
            intent=intent,
            source_session_id=source_session_id,
            target_session_id=target_session_id,
            agent_alias=config.agent_alias,
            case_id=case_id,
            message=message,
        )
        return await asyncio.to_thread(
            self._store(config).reserve,
            submission_id=effective_submission_id,
            fingerprint=fingerprint,
            source_session_id=source_session_id,
            target_session_id=target_session_id,
            agent_alias=config.agent_alias,
            case_id=case_id,
            message=message,
        )

    async def _schedule(
        self,
        operation_id: str,
        *,
        config: RunConfig,
        manager: SessionManager,
    ) -> None:
        async with self._lock:
            if not self._accepting:
                raise RuntimeError("session collaboration service is closing")
            existing = self._tasks.get(operation_id)
            if existing is not None and not existing.done():
                return
            with independent_execution():
                task = asyncio.create_task(
                    self._drive(operation_id, config=config, manager=manager),
                    name=f"session-collaboration:{operation_id}",
                )
            self._tasks[operation_id] = task
            task.add_done_callback(
                lambda completed: self._forget_task(operation_id, completed)
            )

    async def _drive(
        self,
        operation_id: str,
        *,
        config: RunConfig,
        manager: SessionManager,
    ) -> None:
        store = self._store(config)
        turn: ManagedSessionTurn | None = None
        heartbeat: asyncio.Task[None] | None = None
        claimed = False
        try:
            while True:
                operation = await asyncio.to_thread(store.get, operation_id)
                if operation.done:
                    return
                claimed = await asyncio.to_thread(
                    store.claim,
                    operation_id,
                    owner_id=self.owner_id,
                    lease_seconds=self.lease_seconds,
                )
                if claimed:
                    break
                await asyncio.sleep(self.poll_interval)
            assert operation.target_session_id is not None
            turn = await self.turns.submit(
                config=config,
                manager=manager,
                session_id=operation.target_session_id,
                message=operation.message,
                case_id=operation.case_id,
                submission_id=operation.operation_id,
                interactive_approvals=False,
            )
            self._turns[operation_id] = turn
            heartbeat = asyncio.create_task(
                self._heartbeat(store, operation_id),
                name=f"session-collaboration-heartbeat:{operation_id}",
            )
            await turn.wait_ready()
            if turn.startup_error is not None:
                raise turn.startup_error
            if turn.execution_id is None or turn.run_ref is None:
                raise RuntimeError(
                    "collaboration turn did not produce an execution identity"
                )
            await asyncio.to_thread(
                store.mark_running,
                operation_id,
                owner_id=self.owner_id,
                execution_id=turn.execution_id,
                case_run_id=turn.run_ref.case_run_id,
                lease_seconds=self.lease_seconds,
            )
            result_task = asyncio.create_task(
                turn.result(),
                name=f"session-collaboration-result:{operation_id}",
            )
            done, _pending = await asyncio.wait(
                (result_task, heartbeat),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done:
                heartbeat.result()
                raise RuntimeError("collaboration heartbeat stopped unexpectedly")
            output, _ = result_task.result()
            result = plain_object(plain_object(output.data).get("result"))
            await asyncio.to_thread(
                store.finish,
                operation_id,
                owner_id=self.owner_id,
                state=_terminal_state(str(result.get("status") or turn.status)),
                final_answer=str(result.get("final_answer") or output.content or ""),
                error=None if not result.get("error") else str(result["error"]),
            )
        except asyncio.CancelledError:
            if turn is not None:
                await turn.cancel()
            if claimed:
                await self._finish_safely(
                    store,
                    operation_id,
                    state=CollaborationState.SKIPPED,
                    error="collaboration service stopped",
                )
            raise
        except BaseException as exc:
            if turn is not None:
                await turn.cancel()
            if claimed:
                await self._finish_safely(
                    store,
                    operation_id,
                    state=CollaborationState.ERROR,
                    error=str(exc),
                )
        finally:
            self._turns.pop(operation_id, None)
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(
        self, store: SessionCollaborationStore, operation_id: str
    ) -> None:
        interval = min(self.lease_seconds / 3, 10.0)
        while True:
            await asyncio.sleep(interval)
            renewed = await asyncio.to_thread(
                store.heartbeat,
                operation_id,
                owner_id=self.owner_id,
                lease_seconds=self.lease_seconds,
            )
            if not renewed:
                raise RuntimeError("collaboration operation lease was lost")

    async def _finish_safely(
        self,
        store: SessionCollaborationStore,
        operation_id: str,
        *,
        state: CollaborationState,
        error: str,
    ) -> None:
        try:
            await asyncio.to_thread(
                store.finish,
                operation_id,
                owner_id=self.owner_id,
                state=state,
                error=error,
            )
        except (FileNotFoundError, RuntimeError):
            pass

    async def _prepare_target(
        self,
        store: SessionCollaborationStore,
        operation_id: str,
        *,
        manager: SessionManager,
        case_id: str,
    ) -> CollaborationOperation:
        while True:
            operation = await asyncio.to_thread(store.get, operation_id)
            if operation.target_session_id is not None or operation.done:
                return operation
            claimed = await asyncio.to_thread(
                store.claim_preparation,
                operation_id,
                owner_id=self.owner_id,
                lease_seconds=self.lease_seconds,
            )
            if claimed:
                try:
                    target = manager.create(case_id, mode="agent")
                    return await asyncio.to_thread(
                        store.attach_target,
                        operation_id,
                        target.session_id,
                        owner_id=self.owner_id,
                    )
                except BaseException as exc:
                    await self._finish_safely(
                        store,
                        operation_id,
                        state=CollaborationState.ERROR,
                        error=str(exc),
                    )
                    raise
            await asyncio.sleep(self.poll_interval)

    def _store(self, config: RunConfig) -> SessionCollaborationStore:
        key = str(Path(config.lora_root).resolve())
        return self._stores.setdefault(key, SessionCollaborationStore(key))

    def _forget_task(self, operation_id: str, completed: asyncio.Task[None]) -> None:
        if self._tasks.get(operation_id) is completed:
            self._tasks.pop(operation_id, None)

    @staticmethod
    def _validate_request(
        manager: SessionManager,
        source_session_id: str | None,
        target_session_id: str,
        message: str,
    ) -> None:
        if not message.strip():
            raise ValueError("message must be non-empty")
        manager.load(target_session_id)
        if source_session_id is not None:
            manager.load(source_session_id)
            if source_session_id == target_session_id:
                raise ValueError("source and target sessions must be different")


def _fingerprint(**values: Any) -> str:
    payload = json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _terminal_state(status: str) -> CollaborationState:
    try:
        state = CollaborationState(status)
    except ValueError:
        return CollaborationState.ERROR
    return state if state.terminal else CollaborationState.ERROR


__all__ = ["SessionCollaborationService"]
