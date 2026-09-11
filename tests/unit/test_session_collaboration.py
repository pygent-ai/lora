from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from lora.orchestration import SessionCollaborationService
from lora.schema import CaseRunRef, RunConfig
from lora.sessions import (
    AgentMessageState,
    CollaborationOperation,
    CollaborationState,
    SessionCollaborationStore,
    SessionManager,
)


class _Turn:
    def __init__(
        self, session_id: str, index: int, release: asyncio.Event | None = None
    ) -> None:
        self.execution_id = f"execution-{index}"
        self.run_ref = CaseRunRef(
            session_id=session_id,
            case_id="collaboration",
            case_run_id=f"run-{index}",
            run_dir=f"runs/{index}",
        )
        self.startup_error = None
        self.status = "passed"
        self._release = release
        self.cancelled = False

    async def wait_ready(self) -> None:
        return None

    async def result(self) -> tuple[Any, None]:
        if self._release is not None:
            await self._release.wait()
        return (
            SimpleNamespace(
                content="answer",
                data={"result": {"status": "passed", "final_answer": "answer"}},
            ),
            None,
        )

    async def cancel(self) -> bool:
        self.cancelled = True
        if self._release is not None:
            self._release.set()
        return True


class _Turns:
    def __init__(self, releases: list[asyncio.Event | None] | None = None) -> None:
        self.submissions: list[dict[str, Any]] = []
        self.releases = list(releases or [])

    async def submit(self, **kwargs: Any) -> _Turn:
        self.submissions.append(kwargs)
        release = self.releases.pop(0) if self.releases else None
        return _Turn(str(kwargs["session_id"]), len(self.submissions), release)


def _setup(tmp_path):
    config = RunConfig(workspace_root=str(tmp_path), lora_root=str(tmp_path / ".lora"))
    manager = SessionManager(config)
    source = manager.create("chat", mode="chat").session_id
    target = manager.create("chat", mode="chat").session_id
    return config, manager, source, target


def test_store_binds_idempotency_key_to_request_fingerprint(tmp_path) -> None:
    store = SessionCollaborationStore(tmp_path / ".lora")
    values = {
        "submission_id": "submission-1",
        "fingerprint": "same",
        "source_session_id": None,
        "target_session_id": "target-1",
        "agent_alias": "default",
        "case_id": "collaboration",
        "message": "hello",
    }

    first = store.reserve(**values)
    second = store.reserve(**values)

    assert second.operation_id == first.operation_id
    with pytest.raises(ValueError, match="different request"):
        store.reserve(**{**values, "fingerprint": "different"})


def test_store_recognizes_only_persisted_parent_child_relationships(tmp_path) -> None:
    store = SessionCollaborationStore(tmp_path / ".lora")
    store.reserve(
        submission_id="relationship",
        fingerprint="relationship",
        source_session_id="parent",
        target_session_id="child",
        agent_alias="default",
        case_id="collaboration",
        message="work",
    )

    assert store.related_sessions("parent", "child")
    assert store.related_sessions("child", "parent")
    assert not store.related_sessions("parent", "unrelated")


def test_store_lists_only_operations_related_to_the_session(tmp_path) -> None:
    store = SessionCollaborationStore(tmp_path / ".lora")
    first = store.reserve(
        submission_id="first",
        fingerprint="first",
        source_session_id="parent",
        target_session_id="first-child",
        agent_alias="default",
        case_id="collaboration",
        message="first",
    )
    second = store.reserve(
        submission_id="second",
        fingerprint="second",
        source_session_id="other-parent",
        target_session_id="parent",
        agent_alias="default",
        case_id="collaboration",
        message="second",
    )
    store.reserve(
        submission_id="unrelated",
        fingerprint="unrelated",
        source_session_id="other-parent",
        target_session_id="other-child",
        agent_alias="default",
        case_id="collaboration",
        message="unrelated",
    )

    assert [item.operation_id for item in store.list_operations("parent")] == [
        second.operation_id,
        first.operation_id,
    ]


@pytest.mark.asyncio
async def test_send_queues_persistent_message_without_starting_a_turn(tmp_path) -> None:
    config, manager, source, target = _setup(tmp_path)
    turns = _Turns()
    service = SessionCollaborationService(turns=cast(Any, turns), poll_interval=0.01)

    first = await service.send(
        config=config,
        manager=manager,
        source_session_id=source,
        target_session_id=target,
        message="please inspect this",
        submission_id="submission-1",
    )
    second = await service.send(
        config=config,
        manager=manager,
        source_session_id=source,
        target_session_id=target,
        message="please inspect this",
        submission_id="submission-1",
    )
    assert second.message_id == first.message_id
    assert first.state is AgentMessageState.QUEUED
    assert turns.submissions == []
    persisted = await service.message_status(config=config, message_id=first.message_id)
    assert persisted.content == "please inspect this"
    assert persisted.source_session_id == source
    await service.aclose()


@pytest.mark.asyncio
async def test_agent_messages_are_claimed_fifo_and_acknowledged(tmp_path) -> None:
    config, manager, source, target = _setup(tmp_path)
    turns = _Turns()
    service = SessionCollaborationService(turns=cast(Any, turns), poll_interval=0.01)

    first = await service.send(
        config=config,
        manager=manager,
        source_session_id=source,
        target_session_id=target,
        message="first",
        submission_id="submission-first",
    )
    second = await service.send(
        config=config,
        manager=manager,
        source_session_id=source,
        target_session_id=target,
        message="second",
        submission_id="submission-second",
    )
    store = SessionCollaborationStore(config.lora_root)
    claimed = store.claim_messages(target, claim_id="delivery-1", lease_seconds=30)
    assert [item.message_id for item in claimed] == [
        first.message_id,
        second.message_id,
    ]

    store.acknowledge_message(
        first.message_id,
        claim_id="delivery-1",
        execution_id="execution-1",
    )
    store.release_message(second.message_id, claim_id="delivery-1")

    assert store.get_message(first.message_id).state is AgentMessageState.DELIVERED
    assert store.get_message(second.message_id).state is AgentMessageState.QUEUED
    assert turns.submissions == []
    await service.aclose()


@pytest.mark.asyncio
async def test_start_creates_exactly_one_target_for_repeated_submission(
    tmp_path,
) -> None:
    config, manager, source, _target = _setup(tmp_path)
    turns = _Turns()
    service = SessionCollaborationService(turns=cast(Any, turns), poll_interval=0.01)

    first = await service.start(
        config=config,
        manager=manager,
        source_session_id=source,
        message="new task",
        submission_id="submission-new",
    )
    second = await service.start(
        config=config,
        manager=manager,
        source_session_id=source,
        message="new task",
        submission_id="submission-new",
    )
    completed = await service.wait(
        config=config, operation_id=first.operation_id, timeout=2
    )

    assert first.target_session_id is not None
    assert second.target_session_id == first.target_session_id
    assert completed.state is CollaborationState.PASSED
    assert len(turns.submissions) == 1

    store = SessionCollaborationStore(config.lora_root)
    callback = store.claim_messages(
        source,
        claim_id="parent-turn",
        lease_seconds=30,
    )
    assert len(callback) == 1
    assert callback[0].source_session_id == completed.target_session_id
    assert callback[0].source_agent_alias == config.agent_alias
    payload = json.loads(callback[0].content)
    assert payload == {
        "type": "session.completed",
        "operation_id": completed.operation_id,
        "session_id": completed.target_session_id,
        "case_id": "collaboration",
        "execution_id": "execution-1",
        "case_run_id": "run-1",
        "status": "passed",
        "final_answer": "answer",
        "error": None,
    }
    await service.aclose()


@pytest.mark.asyncio
async def test_start_without_source_does_not_queue_completion_message(tmp_path) -> None:
    config, manager, _source, _target = _setup(tmp_path)
    turns = _Turns()
    service = SessionCollaborationService(turns=cast(Any, turns), poll_interval=0.01)

    operation = await service.start(
        config=config,
        manager=manager,
        source_session_id=None,
        message="standalone task",
    )
    completed = await service.wait(
        config=config, operation_id=operation.operation_id, timeout=2
    )

    assert completed.target_session_id is not None
    store = SessionCollaborationStore(config.lora_root)
    assert (
        store.claim_messages(
            completed.target_session_id,
            claim_id="child-turn",
            lease_seconds=30,
        )
        == []
    )
    await service.aclose()


@pytest.mark.asyncio
async def test_two_services_serialize_same_target_in_fifo_order(tmp_path) -> None:
    config, manager, source, target = _setup(tmp_path)
    release_first = asyncio.Event()
    first_turns = _Turns([release_first])
    second_turns = _Turns()
    first_service = SessionCollaborationService(
        turns=cast(Any, first_turns), poll_interval=0.01
    )
    second_service = SessionCollaborationService(
        turns=cast(Any, second_turns), poll_interval=0.01
    )

    store = SessionCollaborationStore(config.lora_root)
    first = store.reserve(
        submission_id="operation-first",
        fingerprint="operation-first",
        source_session_id=source,
        target_session_id=target,
        agent_alias=config.agent_alias,
        case_id="collaboration",
        message="first",
    )
    second = store.reserve(
        submission_id="operation-second",
        fingerprint="operation-second",
        source_session_id=source,
        target_session_id=target,
        agent_alias=config.agent_alias,
        case_id="collaboration",
        message="second",
    )
    await first_service.resume_operation(
        config=config, manager=manager, operation_id=first.operation_id
    )
    await _eventually(lambda: len(first_turns.submissions) == 1)
    await second_service.resume_operation(
        config=config, manager=manager, operation_id=second.operation_id
    )
    await asyncio.sleep(0.05)
    assert second_turns.submissions == []

    release_first.set()
    await first_service.wait(config=config, operation_id=first.operation_id, timeout=2)
    await second_service.wait(
        config=config, operation_id=second.operation_id, timeout=2
    )
    assert [call["message"] for call in first_turns.submissions] == ["first"]
    assert [call["message"] for call in second_turns.submissions] == ["second"]
    await first_service.aclose()
    await second_service.aclose()


@pytest.mark.asyncio
async def test_wait_any_returns_when_the_first_operation_finishes(tmp_path) -> None:
    config, manager, source, _target = _setup(tmp_path)
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    turns = _Turns([release_first, release_second])
    service = SessionCollaborationService(turns=cast(Any, turns), poll_interval=0.01)
    first = await service.start(
        config=config,
        manager=manager,
        source_session_id=source,
        message="first",
    )
    second = await service.start(
        config=config,
        manager=manager,
        source_session_id=source,
        message="second",
    )
    await _eventually(lambda: len(turns.submissions) == 2)

    waiting = asyncio.create_task(
        service.wait_any(
            config=config,
            collaboration_ids=(first.operation_id, second.operation_id),
            timeout=2,
        )
    )
    release_second.set()
    items, timed_out = await waiting

    assert all(isinstance(item, CollaborationOperation) for item in items)
    operations = cast(tuple[CollaborationOperation, ...], items)
    states = {item.operation_id: item.state for item in operations}
    assert timed_out is False
    assert states[second.operation_id] is CollaborationState.PASSED
    assert not states[first.operation_id].terminal

    release_first.set()
    await service.wait(config=config, operation_id=first.operation_id, timeout=2)
    await service.aclose()


@pytest.mark.asyncio
async def test_wait_any_timeout_returns_current_snapshots(tmp_path) -> None:
    config, manager, source, _target = _setup(tmp_path)
    release = asyncio.Event()
    turns = _Turns([release])
    service = SessionCollaborationService(turns=cast(Any, turns), poll_interval=0.01)
    operation = await service.start(
        config=config,
        manager=manager,
        source_session_id=source,
        message="long task",
    )
    await _eventually(lambda: len(turns.submissions) == 1)

    items, timed_out = await service.wait_any(
        config=config,
        collaboration_ids=(operation.operation_id,),
        timeout=0.02,
    )

    assert timed_out is True
    assert len(items) == 1
    assert not items[0].done
    release.set()
    await service.wait(config=config, operation_id=operation.operation_id, timeout=2)
    await service.aclose()


@pytest.mark.asyncio
async def test_close_marks_owned_execution_skipped(tmp_path) -> None:
    config, manager, source, target = _setup(tmp_path)
    release = asyncio.Event()
    turns = _Turns([release])
    service = SessionCollaborationService(turns=cast(Any, turns), poll_interval=0.01)
    operation = await service.start(
        config=config,
        manager=manager,
        source_session_id=source,
        message="long task",
    )
    await _eventually(lambda: len(turns.submissions) == 1)

    await service.aclose()
    status = await service.status(config=config, operation_id=operation.operation_id)

    assert status.state is CollaborationState.SKIPPED


async def _eventually(predicate, *, timeout: float = 1.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout)
