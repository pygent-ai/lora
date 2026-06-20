from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from lora.runtime import AgentRuntimeAdapter, RuntimeMessage

from lora_api.dependencies import ApiContext
from lora_api.models.events import ApiEvent
from lora_api.models.requests import ChatTurnRequest
from lora_api.services.session_service import SessionService


async def stream_chat_turn(context: ApiContext, request: ChatTurnRequest) -> AsyncIterator[str]:
    service = SessionService(context.manager)
    session_id = request.session_id
    if session_id is None:
        session_id = service.create_session(case_id=request.case_id, mode="chat").session_id
    service.save_title_from_user_input(session_id, request.message)

    run_ref = context.manager.start_case_run(session_id, request.case_id, run_config=context.config)
    queue: asyncio.Queue[ApiEvent | None] = asyncio.Queue()
    adapter = AgentRuntimeAdapter(config=context.config, session_manager=context.manager)

    async def on_assistant_delta(delta: str) -> None:
        await queue.put(
            ApiEvent(
                type="assistant.delta",
                session_id=session_id,
                case_run_id=run_ref.case_run_id,
                payload={"delta": delta},
            )
        )

    async def on_runtime_message(message: RuntimeMessage) -> None:
        await queue.put(
            ApiEvent(
                type="runtime.message",
                session_id=session_id,
                case_run_id=run_ref.case_run_id,
                payload=_runtime_message_payload(message),
            )
        )

    async def run_turn() -> None:
        status = "error"
        try:
            session = context.manager.load(session_id)
            result = await adapter.run_turn(
                session=session,
                user_input=request.message,
                case_run_ref=run_ref,
                turn_id=request.turn_id,
                on_assistant_delta=on_assistant_delta,
                on_runtime_message=on_runtime_message,
            )
            status = str(result.get("status") or "passed")
            await queue.put(
                ApiEvent(
                    type="chat.completed",
                    session_id=session_id,
                    case_run_id=run_ref.case_run_id,
                    payload=result,
                )
            )
        except Exception as exc:  # noqa: BLE001 - API stream should surface runtime failures.
            await queue.put(
                ApiEvent(
                    type="chat.error",
                    session_id=session_id,
                    case_run_id=run_ref.case_run_id,
                    payload={"error": str(exc), "error_type": type(exc).__name__},
                )
            )
        finally:
            context.manager.finish_case_run(run_ref, _case_run_status(status))
            await queue.put(None)

    task = asyncio.create_task(run_turn())
    yield _sse(
        ApiEvent(
            type="chat.started",
            session_id=session_id,
            case_run_id=run_ref.case_run_id,
            payload=run_ref.to_dict(),
        )
    )
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield _sse(event)
        await task
    finally:
        if not task.done():
            task.cancel()


def _runtime_message_payload(message: RuntimeMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "message_type": message.type,
        "payload": message.payload or {},
        "is_delta": message.is_delta,
    }


def _case_run_status(status: str) -> str:
    return status if status in {"passed", "failed", "error", "skipped"} else "error"


def _sse(event: ApiEvent) -> str:
    data = json.dumps(event.model_dump(), ensure_ascii=False, sort_keys=True)
    return f"event: {event.type}\ndata: {data}\n\n"
