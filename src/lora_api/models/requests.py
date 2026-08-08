from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateSessionRequest(BaseModel):
    case_id: str = "chat"
    mode: str = "chat"


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str | None = Field(default=None, min_length=1)
    session_id: str | None = None
    case_id: str = "chat"
    turn_id: str | None = None
    execution_id: str | None = None
    after_sequence: int | None = None

    @model_validator(mode="after")
    def validate_start_or_resume(self) -> "ChatTurnRequest":
        if bool(self.message) == bool(self.execution_id):
            raise ValueError("provide exactly one of message or execution_id")
        if self.after_sequence is not None and self.execution_id is None:
            raise ValueError("after_sequence requires execution_id")
        return self


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_root: str | None = None
    config_path: str | None = None
    agent_alias: str | None = None
    max_steps: int | None = None
    context_window: int | None = None
    api_key: str | None = None


class ToolApprovalRequest(BaseModel):
    approved: bool
    comment: str = ""
