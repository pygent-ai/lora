from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from lora_api.dependencies import ApiContext, get_api_context
from lora_api.models.requests import TerminalCommandRequest, TerminalResetRequest
from lora_api.models.responses import DeleteResponse, TerminalCommandResponse

router = APIRouter(prefix="/terminal", tags=["terminal"])


@router.post("/execute", response_model=TerminalCommandResponse)
def execute_terminal_command(
    request: TerminalCommandRequest,
    context: ApiContext = Depends(get_api_context),
) -> TerminalCommandResponse:
    try:
        return context.terminal_service.execute(context, request.scope_id, request.command)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/reset", response_model=DeleteResponse)
def reset_terminal(
    request: TerminalResetRequest,
    context: ApiContext = Depends(get_api_context),
) -> DeleteResponse:
    return DeleteResponse(deleted=context.terminal_service.reset(request.scope_id))
