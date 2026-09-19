from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from lora.runtime.context_snapshots import ContextSnapshotStore
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
    # A run directory can be missing while a turn is starting up. Report that as
    # a client error: an unhandled 500 is produced outside the CORS middleware,
    # so the renderer only sees an opaque "failed to fetch".
    try:
        run_ref = context.manager.find_case_run(session_id, case_run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store = EventStore(run_ref)
    return TraceEventsResponse(
        session_id=session_id,
        case_run_id=case_run_id,
        events=list(EventStore.iter_jsonl(store.events_path)),
        context_snapshots=(
            []
            if store.session_dir is None
            else ContextSnapshotStore(store.session_dir).list()
        ),
    )
