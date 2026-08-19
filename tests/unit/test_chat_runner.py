from __future__ import annotations

from types import SimpleNamespace

import pytest

from lora_api.services.chat_runner import ChatRunRegistry


class _Runtime:
    def __init__(self) -> None:
        self.closed: list[bool] = []
        self.approvals: list[tuple[str, bool, str]] = []

    async def close(self, *, cancel: bool) -> None:
        self.closed.append(cancel)

    async def deliver_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        comment: str,
    ) -> bool:
        self.approvals.append((approval_id, approved, comment))
        return True


@pytest.mark.asyncio
async def test_retired_runtime_keeps_active_approval_waiter_alive() -> None:
    registry = ChatRunRegistry()
    old_runtime = _Runtime()
    new_runtime = _Runtime()
    active = SimpleNamespace(
        done=False,
        execution_id="execution-1",
        runtime_service=old_runtime,
        run_ref=SimpleNamespace(case_run_id="case-1"),
    )
    registry._runs[active.execution_id] = active
    registry._case_runs[active.run_ref.case_run_id] = active

    await registry.retire_runtime(old_runtime)
    delivered = await registry.deliver_approval(
        SimpleNamespace(runtime_service=new_runtime),
        "case-1:call-1",
        approved=True,
        comment="approved",
    )

    assert delivered is True
    assert old_runtime.closed == []
    assert old_runtime.approvals == [("case-1:call-1", True, "approved")]
    assert new_runtime.approvals == []

    active.done = True
    await registry.remove(active)

    assert old_runtime.closed == [False]
