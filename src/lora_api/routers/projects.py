from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from lora_api.dependencies import ApiContext, get_api_context
from lora_api.models.responses import DeleteResponse, ProjectListResponse
from lora_api.services.project_service import project_list_response, remove_project

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=ProjectListResponse)
def list_projects(context: ApiContext = Depends(get_api_context)) -> ProjectListResponse:
    return project_list_response(context)


@router.delete("", response_model=DeleteResponse)
def delete_project(
    scope_id: str,
    context: ApiContext = Depends(get_api_context),
) -> DeleteResponse:
    try:
        return DeleteResponse(deleted=remove_project(context, scope_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
