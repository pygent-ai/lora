from __future__ import annotations

from fastapi import APIRouter, Depends

from lora_api.dependencies import ApiContext, get_api_context
from lora_api.models.responses import ProjectListResponse
from lora_api.services.project_service import project_list_response

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=ProjectListResponse)
def list_projects(context: ApiContext = Depends(get_api_context)) -> ProjectListResponse:
    return project_list_response(context)
