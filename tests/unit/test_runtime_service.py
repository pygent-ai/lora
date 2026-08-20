from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from pygent import AIMessage, Context, Module, ToolCall, UserMessage, thaw_json
from pygent.core import EffectSafety, ExecutionRequirements, RecoverySafety
from pygent.runtime import ExecutionOptions
from pygent.tool import AgentToolExecutor

from lora.config import load_run_config
from lora.runtime.context import LoraContext
from lora.runtime.agent import PromptRenderContext, _render_available_tools_prompt
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

            snapshots = await asyncio.gather(*(handle.snapshot() for handle in handles))

            async def collect_events(handle):
                async with handle.subscribe() as events:
                    return [event async for event in events]

            event_sets, results = await asyncio.gather(
                asyncio.gather(*(collect_events(handle) for handle in handles)),
                asyncio.gather(*(handle.result() for handle in handles)),
            )
            stored = await asyncio.gather(
                *(service.history.get_execution(handle.execution_id) for handle in handles)
            )
        finally:
            await service.close()

    assert all(row is not None for row in stored)
    assert all(snapshot.execution_id for snapshot in snapshots)
    assert all(events[-1].kind == "execution.completed" for events in event_sets)
    assert [result[0].content for result in results] == [str(index) for index in range(20)]


@pytest.mark.asyncio
async def test_runtime_replays_completed_execution_through_pygent_handle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = load_run_config(workspace_root=Path(tmp))
        service = LoraRuntimeService(config)
        try:
            await service.initialize()
            handle = await service.binding.bind(_DurableEcho()).start(
                UserMessage(content="durable"), Context()
            )
            await handle.result()
            execution_id = handle.execution_id
        finally:
            await service.close()

        restored = LoraRuntimeService(config)
        try:
            await restored.initialize()
            attached = await restored.runtime.get_execution_handle(execution_id)
            outcome = await attached.outcome()
            async with attached.subscribe(after=outcome.terminal_sequence - 1) as events:
                replay = [event async for event in events]
            result, _ = await attached.result()
        finally:
            await restored.close()

    assert [event.kind for event in replay] == ["execution.completed"]
    assert result.content == "durable"


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
                interactive_approvals=False,
            )
            read_definition = next(
                definition
                for definition in agent.tool_definitions
                if definition.name == "read"
            )
            read_properties = thaw_json(read_definition.parameters)["properties"]
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
                    LoraContext(
                        session_id=run_ref.session_id,
                        case_id=run_ref.case_id,
                        case_run_id=run_ref.case_run_id,
                        run_dir=run_ref.run_dir,
                        turn_id="turn-0001",
                        tools=(diff_spec.definition,),
                    ),
                execution=ExecutionOptions(request_id=run_ref.case_run_id),
            )
        finally:
            manager.finish_case_run(run_ref, "passed")
            await service.close()

    assert message.results[0].status == "succeeded"
    assert "sandbox-ready" in str(message.results[0].output)
    assert diff_message.results[0].status == "succeeded"
    assert "text lines" in read_properties["limit"]["description"]
    assert "One-based" in read_properties["offset"]["description"]
    assert "PDF-only" in read_properties["pages"]["description"]


@pytest.mark.parametrize(
    ("allowed_agents", "background_enabled", "visible_names"),
    [
        ((), True, set()),
        (("dev",), False, {"delegate"}),
        (("dev",), True, {"delegate", "delegate_background"}),
    ],
)
def test_runtime_exposes_only_executable_delegation_tools(
    allowed_agents: tuple[str, ...],
    background_enabled: bool,
    visible_names: set[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = load_run_config(workspace_root=Path(tmp))
        config.delegation.allowed_agents = allowed_agents
        config.delegation.background_enabled = background_enabled
        service = LoraRuntimeService(config)

    assert {spec.definition.name for spec in service.external_tools} == visible_names
    for spec in service.external_tools:
        properties = thaw_json(spec.definition.parameters)["properties"]
        assert "Configured Lora agent alias" in properties["agent"]["description"]
        assert "Complete task" in properties["task"]["description"]


def test_available_tools_prompt_documents_exact_grep_arguments() -> None:
    root = Path("workspace")
    prompt = _render_available_tools_prompt(
        PromptRenderContext(
            session_id="session",
            workspace_root=root,
            session_dir=root / ".lora" / "sessions" / "session",
            turn_id="turn",
            projection={},
            tool_names=["grep"],
        )
    )

    assert (
        "grep tool accepts only pattern, path, glob, ignoreCase, literal, context, and limit"
        in prompt
    )
    assert "do not use output_mode, head_limit, ignore_case" in prompt


def test_delegation_uses_pygent_agent_tool_executor() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = LoraRuntimeService(load_run_config(workspace_root=Path(tmp)))

    assert isinstance(
        service.executor_registry.resolve("lora.agent.delegate", "1"),
        AgentToolExecutor,
    )


def test_complete_lora_graph_is_eligible_for_pygent_module_boundary_recovery() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = load_run_config(workspace_root=Path(tmp))
        assert config.resolved_agent is not None
        for route in config.resolved_agent.routes:
            route.api_key = "durability-test"
            route.api_key_source = "test"
        service = LoraRuntimeService(config)
        agent = service.new_agent(interactive_approvals=True)
        report = service.binding.bind(agent).durability

    assert report.recovery_level == "module_boundary_retry"
    assert report.checkpoint_policy == "run_and_module_boundaries"
    assert report.replay_policy == "recorded_managed_effects"
    assert report.recovery_undeclared_modules == ()
    assert report.effect_unverified_modules == ()


@pytest.mark.asyncio
async def test_one_agent_definition_is_reused_with_isolated_run_contexts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = load_run_config(workspace_root=Path(tmp))
        manager = SessionManager(config)
        first_session = manager.create(case_id="chat", mode="agent")
        second_session = manager.create(case_id="chat", mode="agent")
        first_run = manager.start_case_run(first_session.session_id, "chat", run_config=config)
        second_run = manager.start_case_run(second_session.session_id, "chat", run_config=config)
        service = LoraRuntimeService(config, max_runnable_executions=2)
        try:
            agent = service.new_agent(interactive_approvals=False)
            assert agent is service.new_agent(interactive_approvals=False)

            first_message, first_context = service._prepare_turn(
                agent=agent,
                manager=manager,
                run_ref=first_run,
                message="first",
                config=config,
                turn_id="turn-first",
            )
            second_message, second_context = service._prepare_turn(
                agent=agent,
                manager=manager,
                run_ref=second_run,
                message="second",
                config=config,
                turn_id="turn-second",
            )

            assert first_message.content != second_message.content
            assert first_context.case_run_ref == first_run
            assert second_context.case_run_ref == second_run
            assert first_context.turn_id == "turn-first"
            assert second_context.turn_id == "turn-second"
            assert not hasattr(agent, "case_run_ref")
            assert not hasattr(agent, "turn_id")
        finally:
            manager.finish_case_run(first_run, "passed")
            manager.finish_case_run(second_run, "passed")
            await service.close()


def test_eternal_turn_context_carries_only_uncovered_history_suffix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = load_run_config(workspace_root=Path(tmp))
        config.eternal_conversation.enabled = True
        manager = SessionManager(config)
        session_ref = manager.create(case_id="chat", mode="agent")
        session = manager.load(session_ref.session_id)
        session.history = [
            {"role": "user", "content": "covered user"},
            {"role": "assistant", "content": "covered answer"},
            {"role": "user", "content": "working user"},
            {"role": "assistant", "content": "working answer"},
        ]
        manager.save(session)
        state_dir = Path(session.session_dir) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "eternal-conversation.json").write_text(
            json.dumps({"covered_through": 2, "snapshot": {}}),
            encoding="utf-8",
        )
        run_ref = manager.start_case_run(session.session_id, "chat", run_config=config)
        service = LoraRuntimeService(config)
        try:
            agent = service.new_agent(interactive_approvals=False)
            _, context = service._prepare_turn(
                agent=agent,
                manager=manager,
                run_ref=run_ref,
                message="next",
                config=config,
                turn_id="turn-next",
            )

            assert context.memory_covered_through == 2
            assert [item["content"] for item in context.history] == [
                "working user",
                "working answer",
            ]
            assert [item.content for item in context.messages] == [
                "working user",
                "working answer",
            ]
        finally:
            manager.finish_case_run(run_ref, "passed")
