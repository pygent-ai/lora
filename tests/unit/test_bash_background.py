from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

import pytest
from pygent import AIMessage, ToolCall, ToolResult, freeze_json
from pygent.tool import ToolTask, ToolTaskState

from lora.config import load_run_config
from tests.unit.test_model_configuration import native_runtime_config
from lora.core.io import plain_object
from lora.runtime.context import LoraContext
from lora.runtime.service import LoraRuntimeService
from lora.runtime.tools import ToolObserver
from lora.schema import CaseRunRef
from lora.tracing import EventStore
from lora.sessions import SessionManager


def test_detached_result_preserves_task_and_output_without_error(tmp_path: Path) -> None:
    run = CaseRunRef(session_id="s", case_id="c", case_run_id="r", run_dir=str(tmp_path / "run"))
    observer = ToolObserver(EventStore(run), workspace_root=tmp_path,
                            track_file_effects=True, defer_file_effects=True)
    result = ToolResult(call_id="bash-1", name="bash", status="detached", output="started",
                        task=ToolTask(task_id="task-1", call_id="bash-1",
                                      tool_id="standard.shell.bash", version="3.1.0",
                                      state=ToolTaskState.RUNNING))
    payload, job = observer.record_framework_result(
        "bash", {"command": "sleep 1; echo done > output.txt"}, "turn-1", result,
    )
    assert payload["status"] == "running"
    assert payload["task"]["task_id"] == "task-1"
    assert payload["result"] == "started"
    assert "error" not in payload
    assert job is None  # A running command must not finalize its file effects.
    saved = list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
    assert saved[0]["task"]["task_id"] == "task-1"


@pytest.mark.parametrize("name,status", [
    ("bash", "detached"), ("bash", "unknown"),
    ("tool_task_get", "succeeded"), ("tool_task_stop", "succeeded"),
])
def test_background_output_keeps_existing_preview_limits(
    tmp_path: Path, name: str, status: Literal["detached", "unknown", "succeeded"],
) -> None:
    run = CaseRunRef(session_id="s", case_id="c", case_run_id="r", run_dir=str(tmp_path / "run"))
    observer = ToolObserver(EventStore(run))
    text = "diagnostic " * 3000
    output = text if name == "bash" else {"output": text, "result": {"output": text}}
    result = ToolResult(call_id="large", name=name, status=status, output=freeze_json(output),
                            task=ToolTask(task_id="large-task", call_id="large",
                                      tool_id="standard.shell.bash", version="3.1.0", state=ToolTaskState.RUNNING))
    payload, _ = observer.record_framework_result(name, {}, "turn-1", result)
    assert "diagnostic" in json.dumps(payload)
    assert text not in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_seconds", [0, 0.05])
async def test_managed_bash_background_can_be_queried_stopped_and_read_after_restart(tmp_path: Path, wait_seconds: float) -> None:
    config = native_runtime_config(tmp_path)
    service = LoraRuntimeService(config, tool_max_concurrency=1)
    task_id = None
    try:
        await service.initialize()
        agent = service.new_agent(interactive_approvals=False)
        tools = agent.new_tool_layer()
        bound = service.runtime.bind(tools, binding=service.binding)
        context = LoraContext(tools=agent.tool_definitions)
        answer, _ = await bound.invoke(AIMessage(tool_calls=(ToolCall(
            call_id="start", name="bash",
            arguments={"command": "echo started; sleep 30", "timeout": wait_seconds},
        ),)), context)
        result = answer.results[0]
        assert result.status == "detached", result
        assert result.task is not None
        task_id = result.task.task_id
        async with asyncio.timeout(5):
            while "started" not in str(await service.get_task_output(task_id)):
                await asyncio.sleep(0.02)
        # Control calls must not wait behind the only occupied tool permit.
        queried, _ = await asyncio.wait_for(bound.invoke(AIMessage(tool_calls=(ToolCall(
            call_id="query", name="tool_task_get", arguments={"task_id": task_id},
        ),)), context), 5)
        assert queried.results[0].status == "succeeded"
        assert "started" in str(queried.results[0].output)
        stopped, _ = await asyncio.wait_for(bound.invoke(AIMessage(tool_calls=(ToolCall(
            call_id="stop", name="tool_task_stop", arguments={"task_id": task_id},
        ),)), context), 10)
        assert stopped.results[0].status == "succeeded"
        assert plain_object(stopped.results[0].output)["cancel_requested"] is True
        final = await service.get_task_result(task_id)
        assert final is not None and final.status != "succeeded"
    finally:
        await service.close()
    restarted = LoraRuntimeService(config)
    try:
        await restarted.initialize()
        assert await restarted.get_task(task_id) is not None
        assert await restarted.get_task_result(task_id) is not None
        assert "started" in str(await restarted.get_task_output(task_id))
    finally:
        await restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["complete", "cancel", "shutdown", "restore"])
async def test_background_completion_finalizes_audit_and_late_file_writes(tmp_path: Path, finish: str) -> None:
    from lora.runtime.agent.pipeline import ToolAuditModule
    from lora.runtime.bash_tasks import BashTaskObservations

    config = native_runtime_config(tmp_path)
    manager = SessionManager(config)
    session = manager.create("chat", mode="chat")
    run = manager.start_case_run(session.session_id, "chat", run_config=config)
    service = LoraRuntimeService(config)
    try:
        await service.initialize()
        agent = service.new_agent(interactive_approvals=False)
        context = LoraContext(session_id=run.session_id, case_id=run.case_id,
                              case_run_id=run.case_run_id, run_dir=str(run.run_dir),
                              turn_id="turn-1", tools=agent.tool_definitions)
        await service.bash_tasks.prepare(context)
        command = ("sleep 0.5; echo final > late.txt" if finish in {"complete", "restore"}
                   else "echo diagnostic; echo partial > late.txt; sleep 30")
        message = AIMessage(tool_calls=(ToolCall(call_id="late-write", name="bash",
            arguments={"command": command, "timeout": 0}),))
        bound = service.runtime.bind(agent.new_tool_layer(), binding=service.binding)
        answer, _ = await bound.invoke(message, context)
        assert answer.results[0].status == "detached"
        audit = ToolAuditModule(config, bash_tasks=service.bash_tasks)
        audited, _ = await service.runtime.bind(audit, binding=service.binding).invoke(
            answer, context + message,
        )
        assert isinstance(audited.results[0].output, str)
        assert json.loads(audited.results[0].output)["status"] == "running"
        if finish == "restore":
            previous = service.bash_tasks
            previous._closed = True
            for task in previous._observers.values():
                task.cancel()
            await asyncio.gather(*previous._observers.values(), return_exceptions=True)
            service.bash_tasks = BashTaskObservations(config, service.runtime, previous.directory)
            await service.bash_tasks.restore()
        elif finish in {"cancel", "shutdown"}:
            async with asyncio.timeout(5):
                while not (tmp_path / "late.txt").exists():
                    await asyncio.sleep(0.02)
            if finish == "cancel":
                assert answer.results[0].task is not None
                assert await service.cancel_task(answer.results[0].task.task_id)
            else:
                await service.close()
        # The foreground invocation has returned. The task and its finalizer continue.
        async with asyncio.timeout(10):
            while not (Path(run.run_dir) / "file_events.jsonl").exists():
                await asyncio.sleep(0.02)
        events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
        assert any(Path(event["path"]).name == "late.txt" for event in events)
        results = list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
        expected = "success" if finish in {"complete", "restore"} else "error"
        assert [item["status"] for item in results] == ["running", expected]
        assert results[0]["tool_call_id"] == results[1]["tool_call_id"]
        if finish in {"cancel", "shutdown"}:
            assert "diagnostic" in str(results[1]["result"])
        assert len(list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_calls.jsonl"))) == 1
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"is_background": True}, {"timeout": 0}, {"timeout": -1},
])
async def test_bash_background_never_bypasses_approval(tmp_path: Path, arguments: dict) -> None:
    config = native_runtime_config(tmp_path)
    config.runtime_approvals.enabled = True
    config.runtime_approvals.preauthorized_tools = ()
    service = LoraRuntimeService(config)
    try:
        await service.initialize()
        agent = service.new_agent(interactive_approvals=False)
        bound = service.runtime.bind(agent.new_tool_layer(), binding=service.binding)
        answer, _ = await bound.invoke(AIMessage(tool_calls=(ToolCall(
            call_id="forbidden", name="bash",
            arguments={"command": "echo forbidden > forbidden.txt", **arguments},
        ),)), LoraContext(tools=agent.tool_definitions))
        assert answer.results[0].status == "rejected"
        assert not (tmp_path / "forbidden.txt").exists()
    finally:
        await service.close()
