from __future__ import annotations

from lora.schema import RunConfig
from lora_api.container import ApiContext
from lora_api.models.responses import (
    ProjectListResponse,
    ProjectResponse,
    RuntimeConfigResponse,
)
from lora_api.project_state import (
    SessionScope,
    active_project_scope_id,
    build_session_scopes,
)


def project_list_response(context: ApiContext) -> ProjectListResponse:
    config = context.config
    active_scope_id = active_project_scope_id(config.workspace_root)
    scopes = [
        scope
        for scope in build_session_scopes(
            context.project_state, active_workspace_root=config.workspace_root
        )
        if scope.workspace_root is not None
    ]
    projects = [_project_response(scope) for scope in scopes]
    active = next(
        (project for project in projects if project.scope_id == active_scope_id),
        projects[0],
    )
    return ProjectListResponse(active=active, projects=projects)


def remove_project(context: ApiContext, scope_id: str) -> bool:
    active_scope_id = active_project_scope_id(context.config.workspace_root)
    if scope_id == active_scope_id:
        raise ValueError("Open another project before removing the active project")
    scope = next(
        (
            item
            for item in build_session_scopes(
                context.project_state,
                active_workspace_root=context.config.workspace_root,
            )
            if item.scope_id == scope_id and item.workspace_root is not None
        ),
        None,
    )
    if scope is None:
        return False
    workspace_root = scope.workspace_root
    assert workspace_root is not None
    return context.project_state.forget_project(workspace_root)


def config_response(config: RunConfig) -> RuntimeConfigResponse:
    if config.resolved_agent is None:
        raise ValueError("selected agent has no model routes")
    return RuntimeConfigResponse(
        approvals_enabled=config.runtime_approvals.enabled,
        workspace_root=config.workspace_root,
        lora_root=config.lora_root,
        agent=config.agent_alias,
        profile=config.resolved_agent.profile,
        routes=config.resolved_agent.safe_dict()["routes"],
        fallback=list(config.resolved_agent.fallback),
        retry={
            "max_attempts_per_route": config.resolved_agent.retry.max_attempts_per_route,
            "attempt_idle_timeout_seconds": config.resolved_agent.retry.attempt_idle_timeout_seconds,
            "backoff_initial": config.resolved_agent.retry.backoff_initial,
            "backoff_maximum": config.resolved_agent.retry.backoff_maximum,
            "backoff_multiplier": config.resolved_agent.retry.backoff_multiplier,
        },
        user_lora_root=config.user_lora_root or "",
        max_steps=config.max_steps,
        context_window=config.context_window,
        context_compression_trigger_ratio=config.context_compression_trigger_ratio,
    )


def _project_response(scope: SessionScope) -> ProjectResponse:
    return ProjectResponse(
        scope_id=scope.scope_id,
        label=scope.label,
        workspace_root=scope.workspace_root,
        lora_root=scope.lora_root,
        tooltip=scope.tooltip,
    )
