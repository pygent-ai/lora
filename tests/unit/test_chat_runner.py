from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pygent import AIMessage, Context

from lora.orchestration import ManagedSessionTurn, TurnCommand, TurnState
from lora.schema import CaseRunRef
from lora_api.models.requests import ChatTurnRequest
from lora_api.services import chat_runner
from lora_api.services.chat_runner import (
    ActiveChatRun,
    ChatRunRegistry,
    _sse,
    stream_chat_turn,
)


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


class _Lease:
    def __init__(self, manager, runtime_service) -> None:
        self.runtime = SimpleNamespace(
            manager=manager,
            runtime_service=runtime_service,
        )
        self.releases = 0

    async def release(self) -> None:
        self.releases += 1


@pytest.mark.asyncio
async def test_stream_serializes_startup_failures_instead_of_dropping_connection() -> (
    None
):
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

    turn = ManagedSessionTurn(
        lease=_Lease(SimpleNamespace(), SimpleNamespace()),
        command=TurnCommand(session_id="session-1", message="hello"),
        run_ref=CaseRunRef(
            session_id="session-1",
            case_id="chat",
            case_run_id="run-1",
            run_dir=str((Path.cwd() / ".test-runs" / "run-1").resolve()),
        ),
        execution_handle=_Handle(),
    )
    run = ActiveChatRun(turn)
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
async def test_active_approval_uses_the_turn_runtime() -> None:
    registry = ChatRunRegistry()
    old_runtime = _Runtime()
    new_runtime = _Runtime()
    active = ManagedSessionTurn(
        lease=_Lease(SimpleNamespace(), old_runtime),
        command=TurnCommand(session_id="session-1", message="hello"),
        run_ref=SimpleNamespace(case_run_id="case-1"),
        execution_handle=SimpleNamespace(execution_id="execution-1"),
        state=TurnState.RUNNING,
    )
    registry.coordinator._managed_runs[id(active)] = active
    registry.coordinator._runs[active.execution_id] = active
    registry.coordinator._case_runs[active.run_ref.case_run_id] = active

    delivered = await registry.deliver_approval(
        SimpleNamespace(
            acquire_runtime=lambda: _async_value(_Lease(SimpleNamespace(), new_runtime))
        ),
        "case-1:call-1",
        approved=True,
        comment="approved",
    )

    assert delivered is True
    assert old_runtime.closed == []
    assert old_runtime.approvals == [("case-1:call-1", True, "approved")]
    assert new_runtime.approvals == []


@pytest.mark.asyncio
async def test_nonterminal_durable_execution_is_recovered_instead_of_only_attached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            self.sessions_root = tmp_path / "sessions"
            self.config = SimpleNamespace()
            self.finished: list[tuple[CaseRunRef, str]] = []

        def load_run_config(self, ref: CaseRunRef, *, credential_source):
            assert ref is run_ref
            assert credential_source is self.config
            return self.config

        def finish_case_run(self, ref: CaseRunRef, status: str) -> None:
            self.finished.append((ref, status))

    service = _RecoveryService()
    manager = _Manager()

    class _Context:
        async def acquire_runtime(self, **_kwargs):
            return _Lease(manager, service)

    monkeypatch.setattr(
        chat_runner,
        "session_service_for_scope",
        lambda _context, _scope_id: SimpleNamespace(manager=manager),
    )
    context = _Context()
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


@pytest.mark.asyncio
async def test_same_session_creates_next_case_run_only_after_finalization(
    tmp_path: Path,
) -> None:
    releases = [asyncio.Event(), asyncio.Event()]

    class _Handle:
        def __init__(self, index: int) -> None:
            self.execution_id = f"execution-{index}"
            self.index = index

        async def result(self):
            await releases[self.index].wait()
            return AIMessage(
                content="done",
                kind="lora.chat.result",
                data={"result": {"status": "passed"}},
            ), Context()

        async def cancel(self) -> bool:
            releases[self.index].set()
            return True

    class _RuntimeService:
        def __init__(self) -> None:
            self.started = 0

        async def start_turn(self, **_kwargs):
            index = self.started
            self.started += 1
            return _Handle(index)

    class _Manager:
        def __init__(self) -> None:
            self.sessions_root = tmp_path / "sessions"
            self.config = SimpleNamespace()
            self.started: list[str] = []
            self.finished: list[str] = []

        def start_case_run(self, session_id, case_id, *, run_config):
            del run_config
            run_id = f"run-{len(self.started)}"
            self.started.append(run_id)
            return CaseRunRef(
                session_id=session_id,
                case_id=case_id,
                case_run_id=run_id,
                run_dir=str(tmp_path / run_id),
            )

        def finish_case_run(self, ref, status):
            assert status == "passed"
            self.finished.append(ref.case_run_id)

    coordinator = ChatRunRegistry().coordinator
    manager = _Manager()
    runtime = _RuntimeService()
    first = await coordinator.submit_turn(
        lease=_Lease(manager, runtime),
        command=TurnCommand(session_id="session-1", message="first"),
    )
    await first.wait_ready()
    second = await coordinator.submit_turn(
        lease=_Lease(manager, runtime),
        command=TurnCommand(session_id="session-1", message="second"),
    )
    await asyncio.sleep(0)

    assert manager.started == ["run-0"]
    assert manager.finished == []
    assert second.state is TurnState.QUEUED

    releases[0].set()
    await asyncio.wait_for(second.wait_ready(), 1)

    assert manager.finished == ["run-0"]
    assert manager.started == ["run-0", "run-1"]

    releases[1].set()
    await asyncio.gather(first.task, second.task)
    assert manager.finished == ["run-0", "run-1"]


@pytest.mark.asyncio
async def test_closing_frontend_subscription_does_not_cancel_execution():
    cancelled = []

    class Handle:
        execution_id = "still-running"

        async def cancel(self):
            cancelled.append(True)

        @contextlib.asynccontextmanager
        async def subscribe(self, *, after):
            async def events():
                yield {
                    "schema_version": "1",
                    "event_id": "e1",
                    "execution_id": self.execution_id,
                    "attempt_id": "a1",
                    "trace_id": "t1",
                    "span_id": "s1",
                    "parent_span_id": None,
                    "timestamp_unix_ns": 1,
                    "module_path": "root",
                    "kind": "model.text.delta",
                    "data": {"text": "working"},
                    "sequence": 1,
                }
                await asyncio.Event().wait()

            yield events()

    turn = ManagedSessionTurn(
        lease=_Lease(SimpleNamespace(), SimpleNamespace()),
        command=TurnCommand(session_id="s1", message="work"),
        run_ref=CaseRunRef(
            session_id="s1", case_id="chat", case_run_id="r1", run_dir=str(Path.cwd())
        ),
        execution_handle=Handle(),
    )
    run = ActiveChatRun(turn)
    stream = run.events(after=None)
    await anext(stream)
    await stream.aclose()
    await asyncio.sleep(0.01)
    assert run.subscribers == 0
    assert cancelled == []
    assert not run.done


async def _async_value(value):
    return value
