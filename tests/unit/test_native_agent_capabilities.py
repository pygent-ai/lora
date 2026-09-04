from __future__ import annotations

import time
import shutil
from pathlib import Path
from typing import Any
import json

import pytest
from pygent import AIMessage, ToolCall, ToolMessage, UserMessage
from pygent.llm import ModelExecution, ModelProviderResponse

from lora.config import load_run_config
from lora.schema import BashCliPreset
from lora.runtime.service import LoraRuntimeService
from lora.runtime.context_snapshots import ContextSnapshotStore
from lora.sessions import SessionManager
from lora.tracing import EventStore


class _CapabilityInvoker:
    def __init__(self) -> None:
        self.requests: list[tuple[Any, Any]] = []

    def validate_route(self, _route: Any) -> None:
        return None

    def execute(self, *, message: Any, context: Any, **_kwargs: Any) -> ModelExecution:
        self.requests.append((message, context))

        async def invoke(_emit: Any) -> ModelProviderResponse:
            if isinstance(message, UserMessage):
                answer = AIMessage(
                    content="creating capability fixtures",
                    tool_calls=(
                        ToolCall(
                            call_id="write-skill",
                            name="write",
                            arguments={
                                "file_path": ".lora/skills/native-created/SKILL.md",
                                "content": (
                                    "---\nname: native-created\n"
                                    "description: Created during the ReAct turn.\n---\n"
                                ),
                            },
                        ),
                        ToolCall(
                            call_id="write-file",
                            name="write",
                            arguments={
                                "file_path": "created.txt",
                                "content": "created by native ReAct",
                            },
                        ),
                        ToolCall(
                            call_id="read-file",
                            name="read",
                            arguments={"file_path": "seed.txt"},
                        ),
                        ToolCall(
                            call_id="discover-cli",
                            name="bash",
                            arguments={"command": "echo ready > cli-ready.flag"},
                        ),
                    ),
                )
            elif isinstance(message, ToolMessage):
                answer = AIMessage(content="capabilities observed")
            else:  # pragma: no cover - the native ReAct contract is exhaustive here.
                raise AssertionError(
                    f"unexpected model message: {type(message).__name__}"
                )
            return ModelProviderResponse(message=answer, usage={})

        return ModelExecution(invoke)

    async def aclose(self) -> None:
        return None


class _ProjectionInvoker:
    def __init__(self) -> None:
        self.requests: list[tuple[Any, Any]] = []

    def validate_route(self, _route: Any) -> None:
        return None

    def execute(self, *, message: Any, context: Any, **_kwargs: Any) -> ModelExecution:
        self.requests.append((message, context))

        async def invoke(_emit: Any) -> ModelProviderResponse:
            return ModelProviderResponse(
                message=AIMessage(content="projection applied"),
                usage={},
            )

        return ModelExecution(invoke)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_authorized_external_file_tools_complete_with_audit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "authorized"
    external.mkdir()
    target = external / "example.txt"
    calls = [
        ("write", {"file_path": str(target), "content": "before"}),
        ("edit", {"file_path": str(target), "old_string": "before", "new_string": "after"}),
        ("read", {"file_path": str(target)}),
        ("glob", {"path": str(external), "pattern": "*.txt"}),
        ("grep", {"path": str(external), "pattern": "after"}),
        ("bash", {"working_directory": str(external), "command": "cat example.txt"}),
        ("write", {"file_path": "local.txt", "content": "local"}),
    ]

    class Invoker(_ProjectionInvoker):
        def execute(self, *, message: Any, context: Any, **_kwargs: Any) -> ModelExecution:
            index = len(self.requests)
            self.requests.append((message, context))

            async def invoke(_emit: Any) -> ModelProviderResponse:
                answer = AIMessage(content="done")
                if index < len(calls):
                    name, arguments = calls[index]
                    answer = AIMessage(tool_calls=(ToolCall(
                        call_id=f"external-{index}", name=name, arguments=arguments,
                    ),))
                return ModelProviderResponse(message=answer, usage={})

            return ModelExecution(invoke)

    config = load_run_config(workspace_root=workspace)
    config.eternal_conversation.enabled = False
    config.runtime_approvals.enabled = False
    assert config.resolved_agent is not None
    for route in config.resolved_agent.routes:
        route.api_key = "test-key"
        route.api_key_source = "test"
    manager = SessionManager(config)
    session = manager.create(case_id="external-tools", mode="chat")
    run = manager.start_case_run(session.session_id, "external-tools", run_config=config)
    invoker = Invoker()
    service = LoraRuntimeService(config)
    service._model_invokers[config.resolved_agent.alias] = invoker
    for agent in service._agent_definitions.values():
        agent.llm = invoker
    try:
        handle = await service.start_turn(
            manager=manager, message=f"Read and write files in {external}; this path is authorized.",
            run_ref=run, turn_id="external-tools", interactive_approvals=False,
            deadline=time.monotonic() + 60,
        )
        output, _ = await handle.result()
        assert output.content == "done"
        assert len(invoker.requests) == len(calls) + 1
        for message, _ in invoker.requests[1:]:
            assert isinstance(message, ToolMessage)
            assert all(result.status == "succeeded" for result in message.results)
        assert target.read_text(encoding="utf-8") == "after"
        assert (workspace / "local.txt").read_text(encoding="utf-8") == "local"
        for index in (3, 5, 6):
            assert "after" in str(invoker.requests[index][0].results[0].output)
        assert "example.txt" in str(invoker.requests[4][0].results[0].output)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_native_react_preserves_skill_cli_and_file_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "seed.txt").write_text("seed content", encoding="utf-8")
    existing_skill = tmp_path / ".lora" / "skills" / "existing-skill" / "SKILL.md"
    existing_skill.parent.mkdir(parents=True)
    existing_skill.write_text(
        "---\nname: existing-skill\ndescription: Available before the turn.\n---\n",
        encoding="utf-8",
    )
    config = load_run_config(workspace_root=tmp_path)
    config.eternal_conversation.enabled = False
    config.runtime_approvals.enabled = False
    config.cli_bash_presets = [
        BashCliPreset(
            name="uv",
            command="uv --version",
            description="Python project manager.",
        ),
        BashCliPreset(
            name="rg",
            command="rg --version",
            description="Fast text search.",
        ),
    ]
    real_which = shutil.which

    def staged_which(name: str) -> str | None:
        if name == "rg" and not (tmp_path / "cli-ready.flag").exists():
            return None
        return real_which(name)

    monkeypatch.setattr("lora.runtime.reminders.cli_context.shutil.which", staged_which)
    assert config.resolved_agent is not None
    for route in config.resolved_agent.routes:
        route.api_key = "capability-test"
        route.api_key_source = "test"

    manager = SessionManager(config)
    session_ref = manager.create(case_id="native-capabilities", mode="chat")
    run_ref = manager.start_case_run(
        session_ref.session_id,
        "native-capabilities",
        run_config=config,
    )
    invoker = _CapabilityInvoker()
    service = LoraRuntimeService(config)
    service._model_invokers[config.resolved_agent.alias] = invoker
    for agent in service._agent_definitions.values():
        agent.llm = invoker

    try:
        handle = await service.start_turn(
            manager=manager,
            message="exercise the native capability chain",
            run_ref=run_ref,
            turn_id="turn-capabilities",
            interactive_approvals=False,
            deadline=time.monotonic() + 60,
        )
        output, context = await handle.result()
    finally:
        manager.finish_case_run(run_ref, "passed")
        await service.close()

    assert output.content == "capabilities observed"
    assert len(invoker.requests) == 2
    first_message, first_context = invoker.requests[0]
    initial_projection = f"{first_context.system_prompt}\n{first_message.content}"
    assert "<skills-context>" in initial_projection
    assert "existing-skill" in initial_projection
    assert "<available-bash-cli>" in initial_projection
    assert "uv --version" in initial_projection

    followup, _ = invoker.requests[1]
    assert isinstance(followup, ToolMessage)
    tool_projection = "\n".join(str(result.output) for result in followup.results)
    assert "<new-skills>" in tool_projection
    assert "native-created" in tool_projection
    assert "<new-bash-cli>" in tool_projection
    assert "rg --version" in tool_projection

    file_events = list(
        EventStore.iter_jsonl(
            Path(session_ref.session_dir) / "logs" / "file_events.jsonl"
        )
        or []
    )
    event_types = {str(event.get("type")) for event in file_events}
    assert "file.read" in event_types
    assert "file.write" in event_types
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == (
        "created by native ReAct"
    )
    assert (Path(run_ref.run_dir) / "diffs" / "diff_events.jsonl").exists()
    assert [message.role for message in context.committed_messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    snapshots = ContextSnapshotStore(session_ref.session_dir).list()
    assert [snapshot["phase"] for snapshot in snapshots] == [
        "request",
        "response",
        "request",
        "response",
    ]
    assert [snapshot["compression_version"] for snapshot in snapshots] == [
        0,
        0,
        0,
        0,
    ]
    assert [snapshot["messages"][-1]["role"] for snapshot in snapshots] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert [message["role"] for message in snapshots[-1]["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_eternal_memory_replaces_projection_before_first_model_call(
    tmp_path: Path,
) -> None:
    config = load_run_config(workspace_root=tmp_path)
    config.eternal_conversation.enabled = True
    config.runtime_approvals.enabled = False
    assert config.resolved_agent is not None
    for route in config.resolved_agent.routes:
        route.api_key = "projection-test"
        route.api_key_source = "test"

    manager = SessionManager(config)
    session_ref = manager.create(case_id="native-projection", mode="chat")
    session = manager.load(session_ref.session_id)
    session.history = [
        {"role": "user", "content": "previous request"},
        {"role": "assistant", "content": "previous final answer"},
    ]
    manager.save(session)
    state_dir = Path(session.session_dir) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "eternal-conversation.json").write_text(
        json.dumps(
            {
                "covered_through": 2,
                "snapshot_revision": 1,
                "snapshot": {"recent_context": ["previous request completed"]},
            }
        ),
        encoding="utf-8",
    )
    run_ref = manager.start_case_run(
        session_ref.session_id,
        "native-projection",
        run_config=config,
    )
    invoker = _ProjectionInvoker()
    service = LoraRuntimeService(config)
    service._model_invokers[config.resolved_agent.alias] = invoker
    for agent in service._agent_definitions.values():
        agent.llm = invoker
        agent.memory_harness = None

    first_finished = False
    second_run = None
    try:
        handle = await service.start_turn(
            manager=manager,
            message="current request",
            run_ref=run_ref,
            turn_id="turn-projection",
            interactive_approvals=False,
            deadline=time.monotonic() + 60,
        )
        output, context = await handle.result()
        manager.finish_case_run(run_ref, "passed")
        first_finished = True
        second_run = manager.start_case_run(
            session_ref.session_id,
            "native-projection-2",
            run_config=config,
        )
        second_handle = await service.start_turn(
            manager=manager,
            message="second request",
            run_ref=second_run,
            turn_id="turn-projection-2",
            interactive_approvals=False,
            deadline=time.monotonic() + 60,
        )
        second_output, second_context = await second_handle.result()
    finally:
        if not first_finished:
            manager.finish_case_run(run_ref, "passed")
        if second_run is not None:
            manager.finish_case_run(second_run, "passed")
        await service.close()

    assert output.content == "projection applied"
    assert len(invoker.requests) == 2
    current, model_context = invoker.requests[0]
    assert current.data["raw_content"] == "current request"
    assert [item.kind for item in model_context.messages] == [
        "lora.memory.snapshot",
        None,
        None,
    ]
    assert [item.content for item in model_context.messages[1:]] == [
        "previous request",
        "previous final answer",
    ]
    assert "<memory-access-instruction>" in model_context.system_prompt
    assert "<memory-snapshot" not in model_context.system_prompt
    assert [item.role for item in context.committed_messages] == ["user", "assistant"]
    assert second_output.content == "projection applied"
    second_current, second_model_context = invoker.requests[1]
    assert second_current.data["raw_content"] == "second request"
    assert second_model_context.messages[1].data["raw_content"] == "current request"
    assert second_model_context.messages[2].content == "projection applied"
    assert [item.role for item in second_context.committed_messages] == [
        "user",
        "assistant",
    ]
    assert [item["role"] for item in manager.load(session_ref.session_id).history] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
