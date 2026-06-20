from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from lora_api.dependencies import ApiContext, get_api_context
from lora_api.models.requests import ChatTurnRequest
from lora_api.services.chat_runner import stream_chat_turn

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/stream")
def stream_turn(
    request: ChatTurnRequest,
    context: ApiContext = Depends(get_api_context),
) -> StreamingResponse:
    return StreamingResponse(stream_chat_turn(context, request), media_type="text/event-stream")
