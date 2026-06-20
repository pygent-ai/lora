from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApiEvent(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    case_run_id: str | None = None
    sequence: int | None = None
