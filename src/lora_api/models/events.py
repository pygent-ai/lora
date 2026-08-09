from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ExecutionEvent(BaseModel):
    schema_version: Literal["1"]
    event_id: str
    execution_id: str
    attempt_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    sequence: int
    timestamp_unix_ns: int
    module_path: str
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)
