from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException
from pygent import ToolResult, freeze_json
from pygent.tool import ToolTask, ToolTaskState

from lora_api.dependencies import ApiContext
from lora_api.routers.runtime import cancel_task, get_task


class TaskContext:
    """Runtime boundary that rejects queries after its lease is released."""

    def __init__(self, state: str | None = "running") -> None:
        self.task = None if state is None else SimpleNamespace(
            task_id="task-1", call_id="call-1", tool_id="Bash", version="1",
            state=SimpleNamespace(value=state), job_id="job-1", metadata={"cwd": "/work"},
        )
        self.output = "partial output\n"
        self.result: Any = None
        self.cancel_accepted = True
        self.state_after_cancel: str | None = None
        self.failure: str | None = None
        self.released = False
        self.runtime = SimpleNamespace(runtime_service=self)

    async def acquire_runtime(self) -> TaskContext:
        return self

    async def release(self) -> None:
        assert not self.released
        self.released = True

    def _check(self, task_id: str, operation: str) -> None:
        assert task_id == "task-1"
        assert not self.released, "runtime queried after release"
        if self.failure == operation:
            raise RuntimeError("runtime unavailable")

    async def get_task(self, task_id: str) -> Any:
        self._check(task_id, "task")
        return self.task

    async def get_task_output(self, task_id: str) -> str:
        self._check(task_id, "output")
        return self.output

    async def get_task_result(self, task_id: str) -> Any:
        self._check(task_id, "result")
        return self.result

    async def cancel_task(self, task_id: str) -> bool:
        self._check(task_id, "cancel")
        if self.state_after_cancel is not None and self.task is not None:
            self.task.state.value = self.state_after_cancel
        return self.cancel_accepted and self.task is not None

    @property
    def api_context(self) -> ApiContext:
        return cast(ApiContext, self)


async def test_get_task_keeps_flat_fields_and_returns_plain_final_result() -> None:
    context = TaskContext("completed")
    context.output = "finished\n"
    context.result = ToolResult(
        call_id="call-1", name="Bash", status="succeeded", output=freeze_json({"exit_code": 0}),
    )

    payload = await get_task("task-1", context.api_context)

    result = payload.pop("result")
    assert isinstance(result, dict)
    assert result["status"] == "succeeded"
    assert result["call_id"] == "call-1"
    assert result["output"] == {"exit_code": 0}
    assert payload == {
        "task_id": "task-1", "call_id": "call-1", "tool_id": "Bash", "version": "1",
        "state": "completed", "job_id": "job-1", "metadata": {"cwd": "/work"},
        "output": {"exit_code": 0},
    }
    assert context.released


async def test_final_result_replaces_stale_running_snapshot_and_partial_output() -> None:
    context = TaskContext("running")
    context.result = ToolResult(
        call_id="call-1", name="Bash", status="succeeded",
        task=ToolTask(
            task_id="task-1", call_id="call-1", tool_id="Bash", version="1",
            state=ToolTaskState.SUCCEEDED, job_id="job-1", metadata={"final": True},
        ),
        output="finished output\n",
    )

    payload = await get_task("task-1", context.api_context)

    assert payload["state"] == "succeeded"
    assert payload["metadata"] == {"final": True}
    assert payload["output"] == "finished output\n"
    assert payload["result"]["task"]["state"] == payload["state"]
    assert context.released


async def test_cancelled_result_without_snapshot_or_output_keeps_observed_values() -> None:
    context = TaskContext("cancelled")
    context.result = ToolResult(call_id="call-1", name="Bash", status="cancelled")

    payload = await get_task("task-1", context.api_context)

    assert payload["state"] == "cancelled"
    assert payload["output"] == "partial output\n"
    assert payload["result"]["status"] == "cancelled"
    assert context.released


async def test_running_task_returns_partial_output_without_waiting_for_result() -> None:
    context = TaskContext()
    payload = await get_task("task-1", context.api_context)
    assert payload["state"] == "running"
    assert payload["output"] == "partial output\n"
    assert payload["result"] is None
    assert context.released


@pytest.mark.parametrize("endpoint", [get_task, cancel_task])
async def test_unknown_task_returns_404_and_releases_lease(endpoint: Any) -> None:
    context = TaskContext(None)
    with pytest.raises(HTTPException) as error:
        await endpoint("task-1", context.api_context)
    assert error.value.status_code == 404
    assert context.released


@pytest.mark.parametrize("state_after_cancel", [None, "cancelled"])
async def test_cancel_reports_request_and_observed_state(state_after_cancel: str | None) -> None:
    context = TaskContext()
    context.state_after_cancel = state_after_cancel
    payload = await cancel_task("task-1", context.api_context)
    assert payload["cancelled"] is True  # Historical cancellation-request acknowledgement.
    assert payload["cancel_requested"] is True
    assert payload["task"]["state"] == (state_after_cancel or "running")
    assert context.released


async def test_already_terminal_task_is_returned_instead_of_false_404() -> None:
    context = TaskContext("completed")
    context.cancel_accepted = False
    payload = await cancel_task("task-1", context.api_context)
    assert payload["cancelled"] is False
    assert payload["cancel_requested"] is False
    assert payload["task"]["state"] == "completed"
    assert context.released


@pytest.mark.parametrize("operation", ["task", "output", "result", "cancel"])
async def test_runtime_failure_releases_lease(operation: str) -> None:
    context = TaskContext()
    context.failure = operation
    endpoint = cancel_task if operation == "cancel" else get_task
    with pytest.raises(RuntimeError, match="runtime unavailable"):
        await endpoint("task-1", context.api_context)
    assert context.released
