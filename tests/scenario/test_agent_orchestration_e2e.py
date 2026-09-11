from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pygent import AIMessage, ToolCall, ToolMessage, UserMessage
from pygent.llm import ModelExecution, ModelProviderResponse

from lora.config import load_run_config
from lora.orchestration import LocalExecutionHost, WorkspaceRuntimePool
from lora.runtime.service import LoraRuntimeService
from lora.sessions import (
    AgentMessageState,
    CollaborationState,
    SessionCollaborationStore,
    SessionManager,
)


def _result(message: ToolMessage, call_id: str) -> dict[str, Any]:
    result = next(item for item in message.results if item.call_id == call_id)
    payload = json.loads(str(result.output))
    assert payload["status"] == "success", payload
    return dict(payload["result"])


class _OrchestrationInvoker:
    def __init__(self, agent_alias: str) -> None:
        self.agent_alias = agent_alias
        self.parent_session_id = ""
        self.parent_stage = 0
        self.fast_session_id = ""
        self.slow_session_id = ""
        self.fast_operation_id = ""
        self.slow_operation_id = ""
        self.sent_message_id = ""
        self.release_fast = asyncio.Event()
        self.release_slow_tool = asyncio.Event()
        self.release_slow_finish = asyncio.Event()
        self.fast_callback_seen = False
        self.slow_callback_seen = False
        self.slow_message_seen = False

    def validate_route(self, _route: Any) -> None:
        return None

    def validate_model(self, _model: Any) -> None:
        return None

    def execute(
        self,
        *,
        message: Any,
        context: Any,
        **_kwargs: Any,
    ) -> ModelExecution:
        session_id = str(getattr(context, "session_id", ""))

        async def invoke(_emit: Any) -> ModelProviderResponse:
            if session_id == self.parent_session_id:
                answer = await self._parent(message)
            elif (
                isinstance(message, UserMessage) and "fast research" in message.content
            ):
                await self.release_fast.wait()
                answer = AIMessage(content="fast report")
            elif (
                isinstance(message, UserMessage) and "slow research" in message.content
            ):
                await self.release_slow_tool.wait()
                answer = AIMessage(
                    tool_calls=(
                        ToolCall(
                            call_id="slow-read",
                            name="read",
                            arguments={"file_path": "seed.txt"},
                        ),
                    )
                )
            elif session_id == self.slow_session_id and isinstance(
                message, ToolMessage
            ):
                content = message.content
                self.slow_message_seen = (
                    "<runtime-context>" in content
                    and "Extra evidence: &lt;alpha&gt; &amp; beta." in content
                )
                await self.release_slow_finish.wait()
                answer = AIMessage(content="slow report with extra evidence")
            else:  # pragma: no cover - reports unexpected routing with useful context
                raise AssertionError(
                    f"unexpected model request session={session_id!r} "
                    f"message={type(message).__name__}:{getattr(message, 'content', '')!r}"
                )
            return ModelProviderResponse(message=answer, usage={})

        return ModelExecution(invoke)

    async def _parent(self, message: Any) -> AIMessage:
        if self.parent_stage == 0:
            assert isinstance(message, UserMessage)
            self.parent_stage = 1
            return AIMessage(
                tool_calls=(
                    ToolCall(
                        call_id="start-fast",
                        idempotency_key="start-fast",
                        name="agent_start",
                        arguments={
                            "agent": self.agent_alias,
                            "task": "fast research",
                        },
                    ),
                    ToolCall(
                        call_id="start-slow",
                        idempotency_key="start-slow",
                        name="agent_start",
                        arguments={
                            "agent": self.agent_alias,
                            "task": "slow research",
                        },
                    ),
                )
            )
        if self.parent_stage == 1:
            assert isinstance(message, ToolMessage)
            fast = _result(message, "start-fast")
            slow = _result(message, "start-slow")
            self.fast_operation_id = str(fast["operation_id"])
            self.slow_operation_id = str(slow["operation_id"])
            self.fast_session_id = str(fast["target_session_id"])
            self.slow_session_id = str(slow["target_session_id"])
            self.parent_stage = 2
            return AIMessage(
                tool_calls=(
                    ToolCall(call_id="list-initial", name="agent_list", arguments={}),
                    ToolCall(
                        call_id="status-fast",
                        name="agent_status",
                        arguments={"collaboration_id": self.fast_operation_id},
                    ),
                    ToolCall(
                        call_id="status-slow",
                        name="agent_status",
                        arguments={"collaboration_id": self.slow_operation_id},
                    ),
                    ToolCall(
                        call_id="send-slow",
                        idempotency_key="send-slow",
                        name="agent_send",
                        arguments={
                            "session_id": self.slow_session_id,
                            "message": "Extra evidence: <alpha> & beta.",
                        },
                    ),
                )
            )
        if self.parent_stage == 2:
            assert isinstance(message, ToolMessage)
            listed = _result(message, "list-initial")
            assert {item["operation_id"] for item in listed["operations"]} == {
                self.fast_operation_id,
                self.slow_operation_id,
            }
            fast_status = _result(message, "status-fast")
            slow_status = _result(message, "status-slow")
            assert fast_status["status"] in {
                "queued",
                "starting",
                "running",
            }, (fast_status["status"], fast_status["error"])
            assert slow_status["status"] in {
                "queued",
                "starting",
                "running",
            }, (slow_status["status"], slow_status["error"])
            sent = _result(message, "send-slow")
            self.sent_message_id = str(sent["message_id"])
            self.release_slow_tool.set()
            self.release_fast.set()
            self.parent_stage = 3
            return AIMessage(
                tool_calls=(
                    ToolCall(
                        call_id="wait-first",
                        name="agent_wait",
                        arguments={
                            "collaboration_ids": [
                                self.fast_operation_id,
                                self.slow_operation_id,
                            ],
                            "timeout_seconds": 10,
                        },
                    ),
                )
            )
        if self.parent_stage == 3:
            assert isinstance(message, ToolMessage)
            waited = _result(message, "wait-first")
            ready_summary = [
                (item["operation_id"], item["status"], item["error"])
                for item in waited["ready"]
            ]
            assert [item["operation_id"] for item in waited["ready"]] == [
                self.fast_operation_id
            ], ready_summary
            assert [item["operation_id"] for item in waited["pending"]] == [
                self.slow_operation_id
            ]
            assert waited["timed_out"] is False
            content = message.content
            self.fast_callback_seen = (
                "<runtime-context>" in content
                and self.fast_operation_id in content
                and "fast report" in content
            )
            self.release_slow_finish.set()
            self.parent_stage = 4
            return AIMessage(
                tool_calls=(
                    ToolCall(
                        call_id="wait-slow",
                        name="agent_wait",
                        arguments={
                            "collaboration_id": self.slow_operation_id,
                            "timeout_seconds": 10,
                        },
                    ),
                )
            )
        assert self.parent_stage == 4
        assert isinstance(message, ToolMessage)
        waited = _result(message, "wait-slow")
        assert waited["status"] == "passed"
        assert waited["final_answer"] == "slow report with extra evidence"
        content = message.content
        self.slow_callback_seen = (
            "<runtime-context>" in content
            and self.slow_operation_id in content
            and "slow report with extra evidence" in content
        )
        self.parent_stage = 5
        return AIMessage(content="coordination complete")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_parent_agent_fanout_send_wait_any_and_callbacks_persist(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "seed.txt").write_text("seed evidence", encoding="utf-8")
    config = load_run_config(workspace_root=workspace)
    config.eternal_conversation.enabled = False
    config.runtime_approvals.enabled = False
    config.max_steps = 8
    assert config.resolved_agent is not None
    agent_alias = config.resolved_agent.alias
    config.delegation.allowed_agents = (agent_alias,)
    for route in config.resolved_agent.routes:
        route.api_key = "agent-orchestration-e2e"
        route.api_key_source = "test"

    invoker = _OrchestrationInvoker(agent_alias)

    def runtime_factory(run_config: Any, **kwargs: Any) -> LoraRuntimeService:
        assert run_config.resolved_agent is not None
        for route in run_config.resolved_agent.routes:
            route.api_key = "agent-orchestration-e2e"
            route.api_key_source = "test"
        service = LoraRuntimeService(run_config, **kwargs)
        service._model_invokers[run_config.resolved_agent.alias] = invoker
        for agent in service._agent_definitions.values():
            agent.llm = invoker
        return service

    pool = WorkspaceRuntimePool(runtime_factory=runtime_factory)
    manager = SessionManager(config)
    parent = manager.create("parent-orchestrator", mode="agent")
    invoker.parent_session_id = parent.session_id

    async with LocalExecutionHost(runtime_pool=pool) as host:
        turn = await host.turns.submit(
            config=config,
            manager=manager,
            session_id=parent.session_id,
            message="Run fast and slow research, enrich the slow task, and combine results.",
            case_id="parent-orchestrator",
            interactive_approvals=False,
        )
        output, _ = await asyncio.wait_for(turn.result(), timeout=30)

    assert output.content == "coordination complete"
    assert invoker.parent_stage == 5
    assert invoker.fast_callback_seen
    assert invoker.slow_callback_seen
    assert invoker.slow_message_seen

    reopened = SessionCollaborationStore(config.lora_root)
    operations = reopened.list_operations(parent.session_id)
    assert {item.operation_id for item in operations} == {
        invoker.fast_operation_id,
        invoker.slow_operation_id,
    }
    assert all(item.state is CollaborationState.PASSED for item in operations)
    sent = reopened.get_message(invoker.sent_message_id)
    assert sent.state is AgentMessageState.DELIVERED
    assert sent.target_session_id == invoker.slow_session_id
    assert sent.delivered_execution_id

    parent_history = manager.load(parent.session_id).history
    assert parent_history[-1]["content"] == "coordination complete"
