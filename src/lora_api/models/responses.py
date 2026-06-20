from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str


class RuntimeConfigResponse(BaseModel):
    workspace_root: str
    lora_root: str
    agent: str
    model: str | None
    api_key_env: str
    api_key_source: str
    user_lora_root: str
    base_url: str | None
    max_steps: int


class ProjectResponse(BaseModel):
    scope_id: str
    label: str
    workspace_root: str | None
    lora_root: str
    tooltip: str = ""


class ProjectListResponse(BaseModel):
    active: ProjectResponse
    projects: list[ProjectResponse]


class SessionRecordResponse(BaseModel):
    session_id: str
    session_dir: str
    scope_id: str | None = None
    case_id: str
    mode: str
    created_at: str
    updated_at: str
    title: str
    last_case_run_id: str | None = None
    last_case_run_status: str | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionRecordResponse]


class SessionScopeResponse(BaseModel):
    scope_id: str
    label: str
    tooltip: str
    workspace_root: str | None
    lora_root: str
    runtime_workspace_root: str


class SessionGroupResponse(BaseModel):
    scope: SessionScopeResponse
    sessions: list[SessionRecordResponse]
    collapsed: bool = False


class SessionGroupListResponse(BaseModel):
    active_scope_id: str
    groups: list[SessionGroupResponse]


class SessionDetailResponse(BaseModel):
    session: SessionRecordResponse
    history: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeleteResponse(BaseModel):
    deleted: bool


class TraceEventsResponse(BaseModel):
    session_id: str
    case_run_id: str
    events: list[dict[str, Any]]
