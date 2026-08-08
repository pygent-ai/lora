from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from pygent import AIMessage, Context, Module, ToolCall, UserMessage
from pygent.core import EffectSafety, ExecutionRequirements, RecoverySafety
from pygent.runtime import ExecutionOptions
from pygent.tool import AgentToolExecutor

from lora.config import load_run_config
from lora.runtime.service import LoraRuntimeService
from lora.sessions import SessionManager


class _DurableEcho(Module[UserMessage, AIMessage]):
    execution_requirements = ExecutionRequirements(
        required_capabilities=("durability.sqlite",),
        recovery_safety=RecoverySafety.MODULE_BOUNDARY_RETRY,
        effect_safety=EffectSafety.EFFECT_FREE,
    )

    async def forward(
        self, message: UserMessage, context: Context
    ) -> tuple[AIMessage, Context]:
        await asyncio.sleep(0)
        return AIMessage(content=message.content), context


class _WideManagedGraph(Module[UserMessage, AIMessage]):
    execution_requirements = _DurableEcho.execution_requirements

    def __init__(self, child_calls: int) -> None:
        super().__init__()
        self.child = _DurableEcho()
        self.child_calls = child_calls

    async def forward(
        self, message: UserMessage, context: Context
    ) -> tuple[AIMessage, Context]:
        for _ in range(self.child_calls):
            _, context = await self.child(message, context)
        return AIMessage(content="done"), context


@pytest.mark.asyncio
async def test_runtime_admits_concurrent_executions_without_application_delay() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = LoraRuntimeService(load_run_config(workspace_root=Path(tmp)))
        try:
            await service.initialize()
            bound = service.binding.bind(_DurableEcho())
            handles = await asyncio.gather(
                *(
                    bound.start(UserMessage(content=str(index)), Context())
                    for index in range(20)
                )
            )

            stored = await asyncio.gather(
                *(service.history.get_execution(handle.execution_id) for handle in handles)
            )

            async def collect_events(handle):
                async with handle.subscribe() as events:
                    return [event async for event in events]

            event_sets, results = await asyncio.gather(
                asyncio.gather(*(collect_events(handle) for handle in handles)),
                asyncio.gather(*(handle.result() for handle in handles)),
            )
        finally:
            await service.close()

    assert all(row is not None for row in stored)
    assert all(events for events in event_sets)
    assert [result[0].content for result in results] == [str(index) for index in range(20)]


@pytest.mark.asyncio
async def test_runtime_allows_managed_graph_beyond_old_64_child_limit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = LoraRuntimeService(load_run_config(workspace_root=Path(tmp)))
        try:
            await service.initialize()
            result, _ = await service.binding.bind(_WideManagedGraph(96)).invoke(
                UserMessage(content="wide"), Context()
            )
        finally:
            await service.close()

    assert result.content == "done"


@pytest.mark.asyncio
async def test_standard_read_tool_advertises_its_workspace_sandbox() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "sample.txt").write_text("sandbox-ready", encoding="utf-8")
        config = load_run_config(workspace_root=root)
        manager = SessionManager(config)
        session = manager.create(case_id="sandbox", mode="agent")
        run_ref = manager.start_case_run(session.session_id, "sandbox", run_config=config)
        service = LoraRuntimeService(config)
        try:
            await service.initialize()
            agent = service.new_agent(
                run_ref,
                "turn-0001",
                interactive_approvals=False,
            )
            layer = agent.new_tool_layer()
            assert layer.executor_registry is None
            read_spec = next(spec for spec in layer.tools if spec.definition.name == "read")
            message, _ = await service.binding.bind(layer).invoke(
                AIMessage(
                    tool_calls=(
                        ToolCall(
                            call_id="read-1",
                            name="read",
                            arguments={"file_path": "sample.txt"},
                            tool_id=read_spec.tool_id,
                            tool_version=read_spec.version,
                        ),
                    )
                ),
                Context(tools=(read_spec.definition,)),
            )
            diff_spec = next(spec for spec in layer.tools if spec.definition.name == "diff")
            diff_message, _ = await service.binding.bind(layer).invoke(
                AIMessage(
                    tool_calls=(
                        ToolCall(
                            call_id="diff-1",
                            name="diff",
                            arguments={"scope": "run"},
                            tool_id=diff_spec.tool_id,
                            tool_version=diff_spec.version,
                        ),
                    )
                ),
                Context(tools=(diff_spec.definition,)),
                execution=ExecutionOptions(request_id=run_ref.case_run_id),
            )
        finally:
            manager.finish_case_run(run_ref, "passed")
            await service.close()

    assert message.results[0].status == "succeeded"
    assert "sandbox-ready" in str(message.results[0].output)
    assert diff_message.results[0].status == "succeeded"


def test_delegation_uses_pygent_agent_tool_executor() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = LoraRuntimeService(load_run_config(workspace_root=Path(tmp)))

    assert isinstance(
        service.executor_registry.resolve("lora.agent.delegate", "1"),
        AgentToolExecutor,
    )
