from __future__ import annotations

from lora.schema import RunConfig
from lora_api.dependencies import ApiContext
from lora_api.models.responses import ProjectListResponse, ProjectResponse, RuntimeConfigResponse
from lora_api.services.project_state import SessionScope, active_project_scope_id, build_session_scopes


def project_list_response(context: ApiContext) -> ProjectListResponse:
    config = context.config
    active_scope_id = active_project_scope_id(config.workspace_root)
    scopes = [
        scope
        for scope in build_session_scopes(context.project_state, active_workspace_root=config.workspace_root)
        if scope.workspace_root is not None
    ]
    projects = [_project_response(scope) for scope in scopes]
    active = next((project for project in projects if project.scope_id == active_scope_id), projects[0])
    return ProjectListResponse(active=active, projects=projects)


def config_response(config: RunConfig) -> RuntimeConfigResponse:
    api_key_env = "DEEPSEEK_API_KEY"
    if config.resolved_agent is not None:
        api_key_env = config.resolved_agent.api_key_env
    return RuntimeConfigResponse(
        workspace_root=config.workspace_root,
        lora_root=config.lora_root,
        agent=config.agent_alias,
        model=config.model_name,
        api_key_env=api_key_env,
        api_key_source=config.api_key_source,
        user_lora_root=config.user_lora_root or "",
        base_url=config.base_url,
        max_steps=config.max_steps,
    )


def _project_response(scope: SessionScope) -> ProjectResponse:
    return ProjectResponse(
        scope_id=scope.scope_id,
        label=scope.label,
        workspace_root=scope.workspace_root,
        lora_root=scope.lora_root,
        tooltip=scope.tooltip,
    )
