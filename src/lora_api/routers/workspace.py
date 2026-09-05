from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from lora_api.dependencies import ApiContext, get_api_context
from lora_api.models.responses import WorkspaceEntriesResponse, WorkspaceFileResponse
from lora_api.services.workspace_service import list_workspace_entries, read_workspace_file

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("/entries", response_model=WorkspaceEntriesResponse)
def workspace_entries(
    scope_id: str,
    path: str = "",
    context: ApiContext = Depends(get_api_context),
) -> WorkspaceEntriesResponse:
    try:
        return list_workspace_entries(context, scope_id=scope_id, relative_path=path)
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/file", response_model=WorkspaceFileResponse)
def workspace_file(
    scope_id: str,
    path: str,
    context: ApiContext = Depends(get_api_context),
) -> WorkspaceFileResponse:
    try:
        return read_workspace_file(context, scope_id=scope_id, relative_path=path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"File {path!r} was not found") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
