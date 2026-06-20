from __future__ import annotations

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    case_id: str = "chat"
    mode: str = "chat"


class ChatTurnRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None
    case_id: str = "chat"
    turn_id: str | None = None
    resume_case_run_id: str | None = None
    resume_from_event: int | None = None


class UpdateSettingsRequest(BaseModel):
    workspace_root: str | None = None
    config_path: str | None = None
    agent_alias: str | None = None
    model: str | None = None
    max_steps: int | None = None
    context_window: int | None = None
    api_key: str | None = None
