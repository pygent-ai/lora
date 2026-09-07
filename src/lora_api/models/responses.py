from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str


class RuntimeConfigResponse(BaseModel):
    approvals_enabled: bool
    workspace_root: str
    lora_root: str
    agent: str
    profile: str
    routes: list[dict[str, Any]]
    fallback: list[str]
    retry: dict[str, Any]
    user_lora_root: str
    max_steps: int
    context_window: int | None
    context_compression_trigger_ratio: float


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
    runtime_execution_id: str | None = None
    run_history_start_index: int = 0


class DeleteResponse(BaseModel):
    deleted: bool


class WorkspaceEntryResponse(BaseModel):
    name: str
    path: str
    kind: str
    size: int | None = None


class WorkspaceEntriesResponse(BaseModel):
    root: str
    path: str
    entries: list[WorkspaceEntryResponse]


class WorkspaceFileResponse(BaseModel):
    path: str
    content: str
    encoding: str
    size: int


class TerminalCommandResponse(BaseModel):
    output: str
    exit_code: int
    cwd: str


class TraceEventsResponse(BaseModel):
    session_id: str
    case_run_id: str
    events: list[dict[str, Any]]
    context_snapshots: list[dict[str, Any]] = Field(default_factory=list)


class ToolResultResponse(BaseModel):
    tool_call_id: str
    tool_name: str
    status: str
    result: Any = None
    error: str | None = None
    result_size: int
    created_at: str | None = None
