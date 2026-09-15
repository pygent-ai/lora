from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from lora_api.dependencies import ApiContext, get_api_context
from lora_api.models.requests import CreateSessionRequest, UpdateSessionModelRequest
from lora_api.models.responses import (
    DeleteResponse,
    SessionDetailResponse,
    SessionGroupListResponse,
    SessionListResponse,
    SessionRecordResponse,
)
from lora_api.services.session_service import SessionService, session_groups_response, session_service_for_scope

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
def list_sessions(context: ApiContext = Depends(get_api_context)) -> SessionListResponse:
    return SessionListResponse(sessions=SessionService(context.manager).list_chat_sessions())


@router.get("/groups", response_model=SessionGroupListResponse)
def list_session_groups(context: ApiContext = Depends(get_api_context)) -> SessionGroupListResponse:
    return session_groups_response(context)


@router.post("", response_model=SessionRecordResponse)
async def create_session(
    request: CreateSessionRequest,
    context: ApiContext = Depends(get_api_context),
) -> SessionRecordResponse:
    try:
        return session_service_for_scope(
            context, request.scope_id, with_reminders=True
        ).create_session(
            case_id=request.case_id,
            mode=request.mode,
            model_group_name=request.model_group_name,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{session_id}/model", response_model=SessionRecordResponse)
async def update_session_model(
    session_id: str,
    request: UpdateSessionModelRequest,
    scope_id: str | None = None,
    context: ApiContext = Depends(get_api_context),
) -> SessionRecordResponse:
    service = session_service_for_scope(context, scope_id)
    if await context.session_coordinator.session_busy(service.manager, session_id):
        raise HTTPException(
            status_code=409,
            detail="Session model cannot change while an execution is active",
        )
    try:
        return service.update_selected_model(
            session_id, request.selected_model_key
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{session_id}", response_model=SessionDetailResponse)
def get_session(
    session_id: str,
    scope_id: str | None = None,
    context: ApiContext = Depends(get_api_context),
) -> SessionDetailResponse:
    try:
        return session_service_for_scope(context, scope_id).load_detail(session_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id!r} is unavailable in the current workspace",
        ) from exc


@router.delete("/{session_id}", response_model=DeleteResponse)
def delete_session(
    session_id: str,
    scope_id: str | None = None,
    context: ApiContext = Depends(get_api_context),
) -> DeleteResponse:
    return DeleteResponse(
        deleted=session_service_for_scope(context, scope_id).delete_session(session_id)
    )
