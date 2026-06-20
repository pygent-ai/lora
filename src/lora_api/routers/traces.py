from __future__ import annotations

from fastapi import APIRouter, Depends

from lora.tracing import EventStore

from lora_api.dependencies import ApiContext, get_api_context
from lora_api.models.responses import TraceEventsResponse

router = APIRouter(prefix="/traces", tags=["traces"])


@router.get("/{session_id}/{case_run_id}", response_model=TraceEventsResponse)
def get_trace_events(
    session_id: str,
    case_run_id: str,
    context: ApiContext = Depends(get_api_context),
) -> TraceEventsResponse:
    run_ref = context.manager.find_case_run(session_id, case_run_id)
    store = EventStore(run_ref)
    return TraceEventsResponse(
        session_id=session_id,
        case_run_id=case_run_id,
        events=list(EventStore.iter_jsonl(store.events_path)),
    )
