from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pygent import AIMessage, Context

from lora.schema import CaseRunRef
from lora_api.models.requests import ChatTurnRequest
from lora_api.services import chat_runner
from lora_api.services.chat_runner import ActiveChatRun, ChatRunRegistry, _sse, stream_chat_turn


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
async def test_stream_serializes_startup_failures_instead_of_dropping_connection() -> None:
    class _FailingRegistry:
        async def resolve(self, context, request):
            del context, request
            raise FileNotFoundError("stale session")

    chunks = [
        chunk
        async for chunk in stream_chat_turn(
            SimpleNamespace(),
            ChatTurnRequest(message="hello", session_id="missing-session"),
            registry=_FailingRegistry(),
        )
    ]

    assert len(chunks) == 1
    assert '"kind": "lora.transport.error"' in chunks[0]
    assert '"error": "stale session"' in chunks[0]
    assert '"error_type": "FileNotFoundError"' in chunks[0]


@pytest.mark.asyncio
async def test_keepalive_preserves_pending_runtime_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_event = asyncio.Event()
    raw_event = {
        "schema_version": "1",
        "event_id": "event-1",
        "execution_id": "execution-1",
        "attempt_id": "attempt-1",
        "trace_id": "trace-1",
        "span_id": "span-1",
        "parent_span_id": None,
        "sequence": 8,
        "timestamp_unix_ns": 123,
        "module_path": "agent.model",
        "kind": "model.text.delta",
        "data": {"text": "finished waiting"},
    }

    class _Handle:
        execution_id = "execution-1"

        @contextlib.asynccontextmanager
        async def subscribe(self, *, after: int | None):
            assert after == 7

            async def delayed_events():
                await release_event.wait()
                yield raw_event

            yield delayed_events()

    run = ActiveChatRun(
        runtime_service=SimpleNamespace(),
        manager=SimpleNamespace(),
        request=ChatTurnRequest(message="hello"),
        run_ref=CaseRunRef(
            session_id="session-1",
            case_id="chat",
            case_run_id="run-1",
            run_dir=str((Path.cwd() / ".test-runs" / "run-1").resolve()),
        ),
        registry=ChatRunRegistry(),
        done=True,
        execution_handle=_Handle(),
    )
    monkeypatch.setattr(chat_runner, "CHAT_KEEPALIVE_SECONDS", 0.01)

    events = run.events(after=7)
    keepalive = await asyncio.wait_for(anext(events), 0.2)

    assert keepalive.kind == "lora.transport.keepalive"
    assert keepalive.execution_id == "execution-1"
    assert keepalive.sequence == 7
    assert keepalive.module_path == "lora.transport"
    assert _sse(keepalive) == ": keep-alive\n\n"

    release_event.set()
    model_event = await asyncio.wait_for(anext(events), 0.2)

    assert model_event.event_id == "event-1"
    assert model_event.data == {"text": "finished waiting"}
    await events.aclose()
    assert run.subscribers == 0


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


@pytest.mark.asyncio
async def test_nonterminal_durable_execution_is_recovered_instead_of_only_attached() -> None:
    run_ref = CaseRunRef(
        session_id="session-1",
        case_id="chat",
        case_run_id="run-1",
        run_dir=str((Path.cwd() / ".test-runs" / "run-1").resolve()),
    )

    class _Handle:
        execution_id = "execution-1"

        async def snapshot(self):
            return SimpleNamespace(status=SimpleNamespace(terminal=False))

        async def result(self):
                return AIMessage(
                    content="recovered",
                    kind="lora.chat.result",
                    data={"result": {"status": "passed"}},
                ), Context()

    class _ManagedRuntime:
        async def get_execution_handle(self, execution_id: str):
            assert execution_id == "execution-1"
            return _Handle()

    class _RecoveryService:
        def __init__(self) -> None:
            self.runtime = _ManagedRuntime()
            self.recovered: list[str] = []

        async def initialize(self) -> None:
            return None

        async def recovery_case_run(self, execution_id: str) -> CaseRunRef:
            assert execution_id == "execution-1"
            return run_ref

        async def recover_turn(self, execution_id: str, *, deadline: float):
            assert deadline > 0
            self.recovered.append(execution_id)
            return _Handle()

    class _Manager:
        def __init__(self) -> None:
            self.finished: list[tuple[CaseRunRef, str]] = []

        def finish_case_run(self, ref: CaseRunRef, status: str) -> None:
            self.finished.append((ref, status))

    service = _RecoveryService()
    manager = _Manager()
    context = SimpleNamespace(runtime_service=service, manager=manager)
    registry = ChatRunRegistry()

    run = await registry.resolve(
        context,
        ChatTurnRequest(execution_id="execution-1", after_sequence=4),
    )
    assert run is not None
    await run.task

    assert service.recovered == ["execution-1"]
    assert run.startup_error is None
    assert manager.finished == [(run_ref, "passed")]
