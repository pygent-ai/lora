from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from lora.orchestration import SessionExecutionCoordinator, TurnCommand, TurnState
from lora.schema import CaseRunRef


class _Lease:
    def __init__(self, manager, runtime_service) -> None:
        self.runtime = SimpleNamespace(
            manager=manager,
            runtime_service=runtime_service,
        )
        self.released = False

    async def release(self) -> None:
        self.released = True


@pytest.mark.asyncio
async def test_same_session_and_root_share_one_execution_lane(tmp_path) -> None:
    coordinator = SessionExecutionCoordinator()
    manager = SimpleNamespace(sessions_root=tmp_path / "sessions")
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first() -> None:
        async with coordinator.session_execution(manager, "session-1"):
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        await first_entered.wait()
        async with coordinator.session_execution(manager, "session-1"):
            second_entered.set()

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_entered.wait()
    await asyncio.sleep(0)
    assert not second_entered.is_set()

    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert second_entered.is_set()


@pytest.mark.asyncio
async def test_equal_session_ids_in_different_roots_do_not_block(tmp_path) -> None:
    coordinator = SessionExecutionCoordinator()
    first_manager = SimpleNamespace(sessions_root=tmp_path / "one" / "sessions")
    second_manager = SimpleNamespace(sessions_root=tmp_path / "two" / "sessions")
    release_first = asyncio.Event()
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()

    async def first() -> None:
        async with coordinator.session_execution(first_manager, "session-1"):
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        await first_entered.wait()
        async with coordinator.session_execution(second_manager, "session-1"):
            second_entered.set()

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_entered.wait()
    await asyncio.wait_for(second_entered.wait(), 1)

    release_first.set()
    await asyncio.gather(first_task, second_task)


@pytest.mark.asyncio
async def test_close_seals_a_turn_cancelled_before_start(tmp_path) -> None:
    coordinator = SessionExecutionCoordinator()

    class _Manager:
        sessions_root = tmp_path / "sessions"
        config = SimpleNamespace()

        def start_case_run(self, session_id, case_id, *, run_config):
            del run_config
            return CaseRunRef(
                session_id=session_id,
                case_id=case_id,
                case_run_id="run-1",
                run_dir=str(tmp_path / "run-1"),
            )

        def finish_case_run(self, _ref, _status):
            return None

    class _Runtime:
        async def start_turn(self, **_kwargs):
            await asyncio.Event().wait()

    lease = _Lease(_Manager(), _Runtime())
    turn = await coordinator.submit_turn(
        lease=lease,
        command=TurnCommand(session_id="session-1", message="hello"),
    )

    await coordinator.close()

    assert turn.state is TurnState.SKIPPED
    assert turn.ready.is_set()
    assert turn.finalized.is_set()
    assert lease.released is True
    assert coordinator._managed_runs == {}
