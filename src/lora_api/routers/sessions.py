from __future__ import annotations

from fastapi import APIRouter, Depends

from lora_api.dependencies import ApiContext, get_api_context
from lora_api.models.requests import CreateSessionRequest
from lora_api.models.responses import (
    DeleteResponse,
    SessionDetailResponse,
    SessionGroupListResponse,
    SessionListResponse,
    SessionRecordResponse,
)
from lora_api.services.session_service import SessionService, session_groups_response

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
def list_sessions(context: ApiContext = Depends(get_api_context)) -> SessionListResponse:
    return SessionListResponse(sessions=SessionService(context.manager).list_chat_sessions())


@router.get("/groups", response_model=SessionGroupListResponse)
def list_session_groups(context: ApiContext = Depends(get_api_context)) -> SessionGroupListResponse:
    return session_groups_response(context)


@router.post("", response_model=SessionRecordResponse)
def create_session(
    request: CreateSessionRequest,
    context: ApiContext = Depends(get_api_context),
) -> SessionRecordResponse:
    return SessionService(context.manager).create_session(case_id=request.case_id, mode=request.mode)


@router.get("/{session_id}", response_model=SessionDetailResponse)
def get_session(session_id: str, context: ApiContext = Depends(get_api_context)) -> SessionDetailResponse:
    return SessionService(context.manager).load_detail(session_id)


@router.delete("/{session_id}", response_model=DeleteResponse)
def delete_session(session_id: str, context: ApiContext = Depends(get_api_context)) -> DeleteResponse:
    return DeleteResponse(deleted=SessionService(context.manager).delete_session(session_id))
