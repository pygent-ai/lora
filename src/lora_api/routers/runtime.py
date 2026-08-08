from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pygent import thaw_json

from lora.core.io import plain_data
from lora_api.dependencies import ApiContext, get_api_context

router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, Any]:
    task = await context.runtime_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="runtime task not found")
    return _task_payload(task)


@router.delete("/tasks/{task_id}")
async def cancel_task(
    task_id: str,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, Any]:
    cancelled = await context.runtime_service.cancel_task(task_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="runtime task not found or already terminal")
    task = await context.runtime_service.get_task(task_id)
    return {"cancelled": True, "task": None if task is None else _task_payload(task)}


def _task_payload(task: Any) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "call_id": task.call_id,
        "tool_id": task.tool_id,
        "version": task.version,
        "state": task.state.value,
        "job_id": task.job_id,
        "metadata": plain_data(thaw_json(task.metadata)),
    }
