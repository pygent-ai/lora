from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from lora_api.dependencies import ApiContext, get_api_context
from lora_api.models.requests import ChatSteeringRequest, ChatTurnRequest, ToolApprovalRequest
from lora_api.services.chat_runner import stream_chat_turn

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/stream")
def stream_turn(
    request: ChatTurnRequest,
    context: ApiContext = Depends(get_api_context),
) -> StreamingResponse:
    return StreamingResponse(stream_chat_turn(context, request), media_type="text/event-stream")


@router.post("/approvals/{approval_id}")
async def deliver_approval(
    approval_id: str,
    request: ToolApprovalRequest,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        delivered = await context.chat_registry.deliver_approval(
            context,
            approval_id,
            approved=request.approved,
            comment=request.comment,
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"approval_id": approval_id, "delivered": delivered}


@router.post("/executions/{execution_id}/steering")
async def deliver_steering(
    execution_id: str,
    request: ChatSteeringRequest,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        receipt = await context.chat_registry.deliver_steering(
            execution_id, session_id=request.session_id,
            input_id=request.input_id, message=request.message,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if receipt.status == "execution_finished":
        raise HTTPException(status_code=409, detail="当前执行已结束，无法追加指令；请作为新消息发送。")
    return {"execution_id": execution_id, "input_id": receipt.input_id, "status": receipt.status}
