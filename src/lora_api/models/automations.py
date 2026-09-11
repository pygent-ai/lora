from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AutomationCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    workspace_root: str = Field(min_length=1)
    destination: Literal["standalone", "heartbeat"]
    target_session_id: str | None = None
    timezone: str | None = None
    at_time: str | None = None
    rrule: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> AutomationCreateRequest:
        if bool(self.at_time) == bool(self.rrule):
            raise ValueError("exactly one of at_time or rrule is required")
        if self.destination == "heartbeat" and not self.target_session_id:
            raise ValueError("heartbeat requires target_session_id")
        return self


class AutomationUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    prompt: str | None = Field(default=None, min_length=1)
    workspace_root: str | None = Field(default=None, min_length=1)
    destination: Literal["standalone", "heartbeat"] | None = None
    target_session_id: str | None = None
    timezone: str | None = None
    at_time: str | None = None
    rrule: str | None = None


__all__ = ["AutomationCreateRequest", "AutomationUpdateRequest"]
