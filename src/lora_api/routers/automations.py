from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from lora_api.container import ApiContext
from lora_api.dependencies import get_api_context
from lora_api.models.automations import (
    AutomationCreateRequest,
    AutomationUpdateRequest,
)

router = APIRouter(prefix="/automations", tags=["automations"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("")
def list_automations(
    status: str | None = None, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        return {
            "automations": [
                item.to_dict() for item in context.automation_store.list(status=status)
            ]
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.post("", status_code=201)
def create_automation(
    request: AutomationCreateRequest,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        values = request.model_dump()
        values["timezone_name"] = values.pop("timezone")
        return context.automation_service.create(**values).to_dict()
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/{automation_id}")
def get_automation(
    automation_id: str, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        return context.automation_store.get(automation_id).to_dict()
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/{automation_id}")
def update_automation(
    automation_id: str,
    request: AutomationUpdateRequest,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        return context.automation_service.update(
            automation_id, **request.model_dump(exclude_unset=True)
        ).to_dict()
    except Exception as exc:
        raise _error(exc) from exc


@router.delete("/{automation_id}")
def delete_automation(
    automation_id: str, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    return {"deleted": context.automation_store.delete(automation_id)}


@router.post("/{automation_id}/pause")
def pause_automation(
    automation_id: str, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        return context.automation_service.pause(automation_id).to_dict()
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{automation_id}/resume")
def resume_automation(
    automation_id: str, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        return context.automation_service.resume(automation_id).to_dict()
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{automation_id}/run")
def run_automation(
    automation_id: str, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        return context.automation_store.request_run(automation_id).to_dict()
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/{automation_id}/runs")
def list_automation_runs(
    automation_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        return {
            "runs": [
                item.to_dict()
                for item in context.automation_store.list_runs(
                    automation_id, limit=limit
                )
            ]
        }
    except Exception as exc:
        raise _error(exc) from exc


__all__ = ["router"]
