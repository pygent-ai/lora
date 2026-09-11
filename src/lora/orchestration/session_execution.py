from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lora.core.io import plain_object
from lora.schema import CaseRunRef

from .managed_turn import ManagedSessionTurn
from .models import TurnCommand, TurnState


@dataclass(frozen=True, slots=True)
class SessionExecutionKey:
    """Unambiguous identity for one session execution lane."""

    sessions_root: str
    session_id: str

    @classmethod
    def from_manager(cls, manager: Any, session_id: str) -> "SessionExecutionKey":
        return cls(
            sessions_root=str(Path(manager.sessions_root).resolve()),
            session_id=session_id,
        )


@dataclass(slots=True)
class _SessionLane:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class SessionExecutionCoordinator:
    """Own submission, lifecycle, lookup, and shutdown for session turns."""

    def __init__(self) -> None:
        self._runs: dict[str, ManagedSessionTurn] = {}
        self._case_runs: dict[str, ManagedSessionTurn] = {}
        self._managed_runs: dict[int, ManagedSessionTurn] = {}
        self._session_lanes: dict[SessionExecutionKey, _SessionLane] = {}
        self._lock = asyncio.Lock()
        self._closing = False

    async def submit_turn(
        self,
        *,
        lease: Any,
        command: TurnCommand,
    ) -> ManagedSessionTurn:
        """Admit a turn and return immediately, even when its session is busy."""

        turn = ManagedSessionTurn(
            lease=lease,
            command=command,
        )
        await self._admit(turn)
        return turn

    async def recover_turn(
        self,
        *,
        lease: Any,
        run_ref: CaseRunRef,
        execution_id: str,
    ) -> ManagedSessionTurn:
        """Recover a durable execution exactly once across concurrent callers."""

        existing: ManagedSessionTurn | None = None
        async with self._lock:
            existing = self._runs.get(execution_id)
            if existing is None and self._closing:
                raise RuntimeError("session execution coordinator is closing")
            if existing is None:
                turn = ManagedSessionTurn(
                    lease=lease,
                    command=TurnCommand(
                        session_id=run_ref.session_id,
                        message="recovered execution",
                        case_id=run_ref.case_id,
                    ),
                    run_ref=run_ref,
                    recovery_execution_id=execution_id,
                )
                self._managed_runs[id(turn)] = turn
                self._runs[execution_id] = turn
                self._case_runs[run_ref.case_run_id] = turn
                turn.task = asyncio.create_task(
                    self._drive_turn(turn),
                    name=f"session-turn-recovery:{run_ref.case_run_id}",
                )
                return turn
        await lease.release()
        assert existing is not None
        return existing

    async def _admit(self, turn: ManagedSessionTurn) -> None:
        async with self._lock:
            if self._closing:
                raise RuntimeError("session execution coordinator is closing")
            self._managed_runs[id(turn)] = turn
            identity = turn.command.submission_id or turn.command.session_id
            turn.task = asyncio.create_task(
                self._drive_turn(turn),
                name=f"session-turn:{identity}",
            )

    async def _drive_turn(self, turn: ManagedSessionTurn) -> None:
        try:
            async with self.session_execution(
                turn.manager,
                turn.command.session_id,
                admitted=True,
            ):
                await self._drive_in_session_lane(turn)
        except asyncio.CancelledError:
            turn.state = TurnState.SKIPPED
            turn.ready.set()
            turn.finalized.set()
        except BaseException as exc:
            turn.startup_error = exc
            turn.state = TurnState.ERROR
            turn.ready.set()
            turn.finalized.set()
        finally:
            await self._remove(turn)

    async def _drive_in_session_lane(self, turn: ManagedSessionTurn) -> None:
        status = "error"
        try:
            turn.state = TurnState.STARTING
            if turn.run_ref is None:
                turn.run_ref = turn.manager.start_case_run(
                    turn.command.session_id,
                    turn.command.case_id,
                    run_config=turn.manager.config,
                )
            run_ref = turn.run_ref
            assert run_ref is not None
            await self._register_case_run(turn)
            deadline = asyncio.get_running_loop().time() + 30 * 60
            if turn.recovery_execution_id is None:
                turn.execution_handle = await turn.runtime_service.start_turn(
                    manager=turn.manager,
                    message=turn.command.message,
                    run_ref=run_ref,
                    turn_id=(
                        turn.command.turn_id or f"turn-{run_ref.case_run_id[-8:]}"
                    ),
                    interactive_approvals=turn.command.interactive_approvals,
                    message_kind=turn.command.message_kind,
                    message_data=turn.command.message_data,
                    deadline=deadline,
                )
            else:
                turn.execution_handle = await turn.runtime_service.recover_turn(
                    turn.recovery_execution_id,
                    deadline=deadline,
                )
            execution_handle = turn.execution_handle
            assert execution_handle is not None
            await self._attach_execution(turn)
            turn.state = TurnState.RUNNING
            turn.ready.set()
            turn.output, turn.output_context = await execution_handle.result()
            output = turn.output
            assert output is not None
            result = plain_object(plain_object(output.data).get("result"))
            status = str(result.get("status") or "passed")
        except asyncio.CancelledError:
            status = "skipped"
            if turn.execution_handle is not None:
                await turn.execution_handle.cancel()
        except BaseException as exc:
            turn.startup_error = exc
        finally:
            turn.state = TurnState.FINALIZING
            terminal = _terminal_state(status)
            if turn.run_ref is not None:
                # The next turn cannot enter until final metadata is durable.
                turn.manager.finish_case_run(turn.run_ref, terminal.value)
            turn.state = terminal
            turn.ready.set()
            turn.finalized.set()

    @contextlib.asynccontextmanager
    async def session_execution(
        self,
        manager: Any,
        session_id: str,
        *,
        admitted: bool = False,
    ) -> AsyncIterator[None]:
        key = SessionExecutionKey.from_manager(manager, session_id)
        async with self._lock:
            if self._closing and not admitted:
                raise RuntimeError("session execution coordinator is closing")
            lane = self._session_lanes.setdefault(key, _SessionLane())
            lane.users += 1
        try:
            async with lane.lock:
                yield
        finally:
            async with self._lock:
                lane.users -= 1
                if lane.users == 0 and self._session_lanes.get(key) is lane:
                    del self._session_lanes[key]

    async def find_execution(self, execution_id: str) -> ManagedSessionTurn | None:
        async with self._lock:
            return self._runs.get(execution_id)

    async def find_case_run(self, case_run_id: str) -> ManagedSessionTurn | None:
        async with self._lock:
            return self._case_runs.get(case_run_id)

    async def _register_case_run(self, turn: ManagedSessionTurn) -> None:
        if turn.run_ref is None:
            raise RuntimeError("cannot register a turn before its case run exists")
        async with self._lock:
            self._case_runs[turn.run_ref.case_run_id] = turn

    async def _attach_execution(self, turn: ManagedSessionTurn) -> None:
        if turn.execution_id is None:
            return
        async with self._lock:
            self._runs[turn.execution_id] = turn

    async def _remove(self, turn: ManagedSessionTurn) -> None:
        async with self._lock:
            self._managed_runs.pop(id(turn), None)
            if (
                turn.execution_id is not None
                and self._runs.get(turn.execution_id) is turn
            ):
                del self._runs[turn.execution_id]
            if (
                turn.run_ref is not None
                and self._case_runs.get(turn.run_ref.case_run_id) is turn
            ):
                del self._case_runs[turn.run_ref.case_run_id]
        await turn.lease.release()

    async def close(self) -> None:
        async with self._lock:
            self._closing = True
            turns = tuple(self._managed_runs.values())
        if turns:
            await asyncio.gather(
                *(turn.cancel() for turn in turns),
                return_exceptions=True,
            )
            # A task cancelled before its coroutine receives its first timeslice
            # cannot execute _drive_turn's finally block. Seal those handles and
            # clear every index explicitly so shutdown remains observable.
            for turn in turns:
                if not turn.finalized.is_set():
                    turn.state = TurnState.SKIPPED
                    turn.ready.set()
                    turn.finalized.set()
            async with self._lock:
                for turn in turns:
                    self._managed_runs.pop(id(turn), None)
                    if (
                        turn.execution_id is not None
                        and self._runs.get(turn.execution_id) is turn
                    ):
                        del self._runs[turn.execution_id]
                    if (
                        turn.run_ref is not None
                        and self._case_runs.get(turn.run_ref.case_run_id) is turn
                    ):
                        del self._case_runs[turn.run_ref.case_run_id]
            await asyncio.gather(
                *(turn.lease.release() for turn in turns),
                return_exceptions=True,
            )


def _terminal_state(status: str) -> TurnState:
    try:
        state = TurnState(status)
    except ValueError:
        return TurnState.ERROR
    return state if state.terminal else TurnState.ERROR


__all__ = ["SessionExecutionCoordinator", "SessionExecutionKey"]
