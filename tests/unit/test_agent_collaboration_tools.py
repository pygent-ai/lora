from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, cast

import pytest
from pygent import AIMessage, ToolCall, ToolMessage, UserMessage
from pygent.llm import ModelExecution, ModelProviderResponse

from lora.config import load_run_config
from lora.runtime.service import LoraRuntimeService
from lora.sessions import (
    AgentMessage,
    AgentMessageState,
    CollaborationOperation,
    CollaborationState,
    SessionManager,
)


def _operation(
    *,
    state: CollaborationState,
    parent_session_id: str,
    agent_alias: str,
    operation_id: str = "op-related",
) -> CollaborationOperation:
    return CollaborationOperation(
        operation_id=operation_id,
        submission_id="start-call",
        source_session_id=parent_session_id,
        target_session_id="child-session",
        agent_alias=agent_alias,
        case_id="collaboration",
        message="research",
        state=state,
        created_at="now",
        updated_at="now",
        final_answer="done" if state.terminal else "",
    )


class _Collaboration:
    def __init__(self, parent_session_id: str, agent_alias: str) -> None:
        self.parent_session_id = parent_session_id
        self.agent_alias = agent_alias
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self, **kwargs: Any) -> CollaborationOperation:
        self.calls.append(("start", kwargs))
        return _operation(
            state=CollaborationState.QUEUED,
            parent_session_id=self.parent_session_id,
            agent_alias=self.agent_alias,
        )

    async def related_sessions(self, **kwargs: Any) -> bool:
        self.calls.append(("related", kwargs))
        return True

    async def send(self, **kwargs: Any) -> AgentMessage:
        self.calls.append(("send", kwargs))
        return AgentMessage(
            message_id="msg-related",
            submission_id="send-call",
            source_session_id=self.parent_session_id,
            source_agent_alias=self.agent_alias,
            target_session_id="child-session",
            content="more context",
            state=AgentMessageState.QUEUED,
            created_at="now",
            updated_at="now",
        )

    async def item_status(self, **kwargs: Any) -> AgentMessage | CollaborationOperation:
        self.calls.append(("item_status", kwargs))
        operation_id = str(kwargs["collaboration_id"])
        return _operation(
            state=(
                CollaborationState.PASSED
                if operation_id == "op-ready"
                else CollaborationState.RUNNING
            ),
            parent_session_id=(
                "foreign-session"
                if operation_id == "op-foreign"
                else self.parent_session_id
            ),
            agent_alias=self.agent_alias,
            operation_id=operation_id,
        )

    async def list_operations(self, **kwargs: Any) -> list[CollaborationOperation]:
        self.calls.append(("list_operations", kwargs))
        return [
            _operation(
                state=CollaborationState.RUNNING,
                parent_session_id=self.parent_session_id,
                agent_alias=self.agent_alias,
            )
        ]

    async def resume_operation(self, **kwargs: Any) -> CollaborationOperation:
        self.calls.append(("resume", kwargs))
        return _operation(
            state=CollaborationState.RUNNING,
            parent_session_id=self.parent_session_id,
            agent_alias=self.agent_alias,
        )

    async def wait_any(
        self, **kwargs: Any
    ) -> tuple[tuple[CollaborationOperation, ...], bool]:
        self.calls.append(("wait_any", kwargs))
        return (
            tuple(
                _operation(
                    state=(
                        CollaborationState.PASSED
                        if operation_id in {"op-related", "op-ready"}
                        else CollaborationState.RUNNING
                    ),
                    parent_session_id=self.parent_session_id,
                    agent_alias=self.agent_alias,
                    operation_id=operation_id,
                )
                for operation_id in kwargs["collaboration_ids"]
            ),
            False,
        )


class _Invoker:
    def __init__(self, agent_alias: str) -> None:
        self.agent_alias = agent_alias
        self.requests: list[tuple[Any, Any]] = []

    def validate_model(self, _model: Any) -> None:
        return None

    def execute(self, *, message: Any, context: Any, **_kwargs: Any) -> ModelExecution:
        self.requests.append((message, context))

        async def invoke(_emit: Any) -> ModelProviderResponse:
            if isinstance(message, UserMessage):
                answer = AIMessage(
                    tool_calls=(
                        ToolCall(
                            call_id="start-call",
                            idempotency_key="start-call",
                            name="agent_start",
                            arguments={"agent": self.agent_alias, "task": "research"},
                        ),
                        ToolCall(
                            call_id="send-call",
                            idempotency_key="send-call",
                            name="agent_send",
                            arguments={
                                "session_id": "child-session",
                                "message": "more context",
                            },
                        ),
                        ToolCall(
                            call_id="status-call",
                            name="agent_status",
                            arguments={"collaboration_id": "op-related"},
                        ),
                        ToolCall(
                            call_id="list-call",
                            name="agent_list",
                            arguments={},
                        ),
                        ToolCall(
                            call_id="wait-single-call",
                            name="agent_wait",
                            arguments={
                                "collaboration_id": "op-related",
                                "timeout_seconds": 1,
                            },
                        ),
                        ToolCall(
                            call_id="wait-any-call",
                            name="agent_wait",
                            arguments={
                                "collaboration_ids": ["op-related", "op-ready"],
                                "timeout_seconds": 1,
                            },
                        ),
                    )
                )
            elif isinstance(message, ToolMessage):
                answer = AIMessage(content="collaboration tools observed")
            else:  # pragma: no cover - native ReAct message contract
                raise AssertionError(type(message).__name__)
            return ModelProviderResponse(message=answer, usage={})

        return ModelExecution(invoke)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_model_tools_share_the_injected_session_collaboration_service(
    tmp_path: Path,
) -> None:
    config = load_run_config(workspace_root=tmp_path)
    assert config.resolved_agent is not None
    agent_alias = config.resolved_agent.alias
    config.delegation.allowed_agents = (agent_alias,)
    config.runtime_approvals.enabled = False
    for route in config.resolved_agent.routes:
        route.api_key = "agent-collaboration-test"
        route.api_key_source = "test"

    manager = SessionManager(config)
    parent = manager.create("parent", mode="agent")
    run = manager.start_case_run(parent.session_id, "parent", run_config=config)
    collaboration = _Collaboration(parent.session_id, agent_alias)
    invoker = _Invoker(agent_alias)
    service = LoraRuntimeService(config, collaboration=cast(Any, collaboration))
    service._model_invokers[config.resolved_agent.alias] = invoker
    for agent in service._agent_definitions.values():
        agent.llm = invoker
    try:
        handle = await service.start_turn(
            manager=manager,
            message="coordinate the child",
            run_ref=run,
            turn_id="parent-turn",
            interactive_approvals=False,
            deadline=time.monotonic() + 60,
        )
        output, _ = await handle.result()
    finally:
        manager.finish_case_run(run, "passed")
        await service.close()

    assert output.content == "collaboration tools observed"
    assert len(invoker.requests) == 2
    tool_message = invoker.requests[1][0]
    assert isinstance(tool_message, ToolMessage)
    assert [result.status for result in tool_message.results] == [
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
    ], repr(tool_message.results)
    listed = json.loads(str(tool_message.results[3].output))["result"]
    assert [item["operation_id"] for item in listed["operations"]] == ["op-related"]
    waited = json.loads(str(tool_message.results[5].output))["result"]
    assert [item["operation_id"] for item in waited["ready"]] == ["op-ready"]
    assert [item["operation_id"] for item in waited["pending"]] == ["op-related"]
    assert waited["timed_out"] is False
    calls = {name: values for name, values in collaboration.calls}
    assert calls["start"]["source_session_id"] == parent.session_id
    assert calls["start"]["submission_id"] == "start-call"
    assert calls["send"]["source_session_id"] == parent.session_id
    assert calls["send"]["submission_id"] == "send-call"
    assert calls["list_operations"]["session_id"] == parent.session_id
    assert calls["wait_any"]["timeout"] == 1


@pytest.mark.asyncio
async def test_wait_any_checks_every_id_before_resuming_work(tmp_path: Path) -> None:
    config = load_run_config(workspace_root=tmp_path)
    assert config.resolved_agent is not None
    agent_alias = config.resolved_agent.alias
    config.delegation.allowed_agents = (agent_alias,)
    parent = SessionManager(config).create("parent", mode="agent")
    collaboration = _Collaboration(parent.session_id, agent_alias)
    service = LoraRuntimeService(config, collaboration=cast(Any, collaboration))
    try:
        with pytest.raises(PermissionError, match="outside this collaboration"):
            await service._wait_for_agent_collaborations(
                cast(Any, collaboration),
                ("op-related", "op-foreign"),
                parent.session_id,
                timeout=1,
                multiple=True,
            )
    finally:
        await service.close()

    assert not any(name == "resume" for name, _kwargs in collaboration.calls)
