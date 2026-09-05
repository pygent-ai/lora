from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateSessionRequest(BaseModel):
    case_id: str = "chat"
    mode: str = "chat"
    scope_id: str | None = None


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str | None = Field(default=None, min_length=1)
    session_id: str | None = None
    scope_id: str | None = None
    case_id: str = "chat"
    turn_id: str | None = None
    execution_id: str | None = None
    after_sequence: int | None = None
    log_model_text_deltas: bool = False

    @model_validator(mode="after")
    def validate_start_or_resume(self) -> "ChatTurnRequest":
        if bool(self.message) == bool(self.execution_id):
            raise ValueError("provide exactly one of message or execution_id")
        if self.after_sequence is not None and self.execution_id is None:
            raise ValueError("after_sequence requires execution_id")
        return self


class ModelRouteSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)
    api_key: str | None = None


class ModelRetrySettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts_per_route: int = Field(default=2, ge=1)
    attempt_idle_timeout_seconds: float = Field(default=60.0, gt=0)
    backoff_initial: float = Field(default=0.5, ge=0)
    backoff_maximum: float = Field(default=4.0, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1)

    @model_validator(mode="after")
    def validate_backoff(self) -> "ModelRetrySettingsRequest":
        if self.backoff_initial > self.backoff_maximum:
            raise ValueError("backoff_initial must not exceed backoff_maximum")
        return self


class ModelGroupSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    profile: str = Field(default="default", min_length=1)
    routes: list[ModelRouteSettingsRequest] = Field(min_length=1)
    fallback: list[str] = Field(min_length=1)
    retry: ModelRetrySettingsRequest = Field(default_factory=ModelRetrySettingsRequest)

    @model_validator(mode="after")
    def validate_routes_and_fallback(self) -> "ModelGroupSettingsRequest":
        route_ids = [route.id.strip() for route in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("model route ids must be unique")
        fallback = [route_id.strip() for route_id in self.fallback]
        if any(not route_id for route_id in fallback):
            raise ValueError("fallback route ids must be non-empty")
        if len(fallback) != len(set(fallback)):
            raise ValueError("fallback route ids must be unique")
        if unknown := set(fallback) - set(route_ids):
            raise ValueError(f"fallback references unknown routes: {', '.join(sorted(unknown))}")
        return self


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approvals_enabled: bool | None = None
    workspace_root: str | None = None
    agent_alias: str | None = None
    max_steps: int | None = None
    context_window: int | None = None
    api_key: str | None = None
    model_group: ModelGroupSettingsRequest | None = None


class ToolApprovalRequest(BaseModel):
    approved: bool
    comment: str = ""
