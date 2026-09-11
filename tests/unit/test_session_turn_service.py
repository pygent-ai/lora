from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from lora.orchestration import LocalExecutionHost, SessionTurnService
from lora.schema import RunConfig


class _Lease:
    def __init__(self) -> None:
        self.releases = 0
        self.prewarmed: list[str] = []
        self.runtime = SimpleNamespace(
            reminders=SimpleNamespace(prewarm_session=self.prewarmed.append),
        )

    async def release(self) -> None:
        self.releases += 1


@pytest.mark.asyncio
async def test_submit_creates_session_prewarms_titles_and_transfers_lease(
    tmp_path,
) -> None:
    lease = _Lease()
    submitted = []

    class Manager:
        config = RunConfig(
            workspace_root=str(tmp_path), lora_root=str(tmp_path / ".lora")
        )

        def create(self, case_id, *, mode):
            assert (case_id, mode) == ("chat", "chat")
            return SimpleNamespace(session_id="session-1")

        def save_title_from_user_input(self, session_id, message):
            assert (session_id, message) == ("session-1", "hello")

    class Coordinator:
        async def submit_turn(self, *, lease, command):
            submitted.append((lease, command))
            return SimpleNamespace(command=command)

    async def acquire_runtime(**_kwargs):
        return lease

    manager = Manager()
    service = SessionTurnService(
        coordinator=cast(Any, Coordinator()),
        acquire_runtime=cast(Any, acquire_runtime),
    )
    turn = await service.submit(
        config=manager.config,
        manager=cast(Any, manager),
        message="hello",
        interactive_approvals=False,
    )

    assert turn.command.session_id == "session-1"
    assert turn.command.interactive_approvals is False
    assert lease.prewarmed == ["session-1"]
    assert lease.releases == 0
    assert submitted[0][0] is lease


@pytest.mark.asyncio
async def test_submit_releases_lease_when_admission_fails(tmp_path) -> None:
    lease = _Lease()
    config = RunConfig(workspace_root=str(tmp_path), lora_root=str(tmp_path / ".lora"))
    manager = SimpleNamespace(
        create=lambda *_args, **_kwargs: SimpleNamespace(session_id="session-1"),
        save_title_from_user_input=lambda *_args: None,
    )

    class Coordinator:
        async def submit_turn(self, **_kwargs):
            raise RuntimeError("closing")

    async def acquire_runtime(**_kwargs):
        return lease

    service = SessionTurnService(
        coordinator=cast(Any, Coordinator()),
        acquire_runtime=cast(Any, acquire_runtime),
    )
    with pytest.raises(RuntimeError, match="closing"):
        await service.submit(config=config, manager=cast(Any, manager), message="hello")
    assert lease.releases == 1


@pytest.mark.asyncio
async def test_local_execution_host_closes_in_resource_order() -> None:
    calls: list[str] = []

    class Pool:
        async def stop_accepting(self):
            calls.append("stop")

        async def close(self, *, cancel):
            assert cancel is True
            calls.append("pool")

    class Coordinator:
        async def close(self):
            calls.append("coordinator")

    host = LocalExecutionHost(
        runtime_pool=cast(Any, Pool()),
        coordinator=cast(Any, Coordinator()),
    )
    await host.aclose()
    await host.aclose()

    assert calls == ["stop", "coordinator", "pool"]
