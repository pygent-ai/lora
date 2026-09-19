from __future__ import annotations

from collections import deque
from typing import Any

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
    event_limit: int | None = None,
    context_snapshot_limit: int | None = None,
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
    events, events_total = _latest_jsonl_items(store.events_path, event_limit)
    snapshots = (
        []
        if store.session_dir is None
        else ContextSnapshotStore(store.session_dir).list()
    )
    snapshots_total = len(snapshots)
    snapshots = _latest_items(snapshots, context_snapshot_limit)
    return TraceEventsResponse(
        session_id=session_id,
        case_run_id=case_run_id,
        events=events,
        events_total=events_total,
        events_truncated=len(events) < events_total,
        context_snapshots=snapshots,
        context_snapshots_total=snapshots_total,
        context_snapshots_truncated=len(snapshots) < snapshots_total,
    )


def _latest_jsonl_items(path: Any, limit: int | None) -> tuple[list[dict[str, Any]], int]:
    if limit is None or limit <= 0:
        items = list(EventStore.iter_jsonl(path))
        return items, len(items)
    recent: deque[dict[str, Any]] = deque(maxlen=limit)
    total = 0
    for item in EventStore.iter_jsonl(path):
        total += 1
        recent.append(item)
    return list(recent), total


def _latest_items(items: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    if limit is None or limit <= 0 or len(items) <= limit:
        return items
    return items[-limit:]
