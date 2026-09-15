from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pygent.runtime.codec import tool_result_to_dict

from lora.core.io import plain_data
from lora_api.dependencies import ApiContext, get_api_context

router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, Any]:
    lease = await context.acquire_runtime()
    try:
        runtime = lease.runtime.runtime_service
        task = await runtime.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="runtime task not found")
        output = await runtime.get_task_output(task_id)
        result = await runtime.get_task_result(task_id)
        if result is not None:
            # Completion can occur between queries; prefer its final observation.
            if result.task is not None:
                task = result.task
            if result.output is not None:
                output = result.output
        return {
            **_task_payload(task),
            "output": plain_data(output),
            "result": None if result is None else plain_data(tool_result_to_dict(result)),
        }
    finally:
        await lease.release()


@router.delete("/tasks/{task_id}")
async def cancel_task(
    task_id: str,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, Any]:
    lease = await context.acquire_runtime()
    try:
        runtime = lease.runtime.runtime_service
        cancel_requested = await runtime.cancel_task(task_id)
        task = await runtime.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="runtime task not found")
        return {
            # Retain the historical acknowledgement; the snapshot reports actual state.
            "cancelled": cancel_requested,
            "cancel_requested": cancel_requested,
            "task": _task_payload(task),
        }
    finally:
        await lease.release()


def _task_payload(task: Any) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "call_id": task.call_id,
        "tool_id": task.tool_id,
        "version": task.version,
        "state": task.state.value,
        "job_id": task.job_id,
        "metadata": plain_data(task.metadata),
    }
