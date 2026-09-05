from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from lora.schema import RunConfig
from lora.sessions import SessionManager
from lora_api.models.requests import ChatTurnRequest
from lora_api.services.chat_runner import ActiveChatRun, AttachedExecutionRun, ChatRunRegistry
from lora_api.services.session_service import SessionService


def make_manager(tmp_path):
    return SessionManager(RunConfig(workspace_root=tmp_path, lora_root=tmp_path / ".lora"))


def test_history_timing_uses_checkpoint_ownership_across_restart(tmp_path):
    manager = make_manager(tmp_path)
    session = manager.create("chat", mode="chat")
    # Identical message text in distinct runs must never collapse their identities.
    runs = []
    for status in ["passed", "error", "skipped"]:
        run = manager.start_case_run(session.session_id, "chat")
        runs.append(run)
        for role in ["user", "assistant"]:
            manager.append_history_checkpoint(
                run, turn_id="turn", checkpoint_id=f"{run.case_run_id}:{role}",
                message={"role": role, "content": "same text"},
            )
        manager.finish_case_run(run, status)
    restarted = make_manager(tmp_path)
    detail = SessionService(restarted).load_detail(session.session_id)
    for index, run in enumerate(runs):
        expected = manager.run_timing(run)
        assert expected["started_at"] and expected["finished_at"]
        assert detail.history[index * 2]["run_timing"] == expected
        assert detail.history[index * 2 + 1]["run_timing"] == expected
    assert all("run_timing" not in row for row in restarted.load(session.session_id).history)


def test_legacy_history_has_unknown_timing(tmp_path):
    manager = make_manager(tmp_path)
    ref = manager.create("chat")
    session = manager.load(ref.session_id)
    session.history = [{"role": "assistant", "content": "old answer"}]
    manager.save(session)
    assert SessionService(manager).load_detail(ref.session_id).history == session.history


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,status", [
    ("execution.completed", "passed"), ("execution.failed", "error"),
    ("execution.cancelled", "skipped"), ("execution.deadline_exceeded", "error"),
])
async def test_terminal_stream_waits_for_durable_timing_and_replays_it(tmp_path, kind, status):
    manager = make_manager(tmp_path)
    ref = manager.create("chat")
    run_ref = manager.start_case_run(ref.session_id, "chat")
    terminal_sent = asyncio.Event()

    class Handle:
        execution_id = "execution-1"

        @asynccontextmanager
        async def subscribe(self, *, after):
            async def events():
                terminal_sent.set()
                yield {
                    "schema_version": "1", "event_id": "event-1", "execution_id": self.execution_id,
                    "attempt_id": "attempt-1", "trace_id": "trace-1", "span_id": "span-1",
                    "sequence": 1, "timestamp_unix_ns": 123, "module_path": "lora",
                    "kind": kind, "data": {},
                }
            yield events()

    async def finalize():
        await terminal_sent.wait()
        await asyncio.sleep(0)
        manager.finish_case_run(run_ref, status)

    task = asyncio.create_task(finalize())
    run = ActiveChatRun(
        runtime_service=SimpleNamespace(), manager=manager, request=ChatTurnRequest(message="hi"),
        run_ref=run_ref, registry=ChatRunRegistry(), execution_handle=Handle(), task=task, done=True,
    )
    events = [event async for event in run.events(after=None)]
    timing = events[0].data["run_timing"]
    assert timing["finished_at"] and timing["status"] == status
    replay = [event async for event in AttachedExecutionRun(Handle(), run_ref, manager).events(after=None)]
    assert replay[0].data["run_timing"] == timing
