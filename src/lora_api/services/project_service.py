from __future__ import annotations

from copy import deepcopy

from lora.credentials import lookup_credential
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
    mapping = deepcopy(config.model_config_mapping or {})
    connections = mapping.get("connections", {})
    if not isinstance(connections, dict):
        connections = {}
    safe_connections: dict[str, dict] = {}
    for key, raw_connection in connections.items():
        if not isinstance(key, str) or not isinstance(raw_connection, dict):
            continue
        connection = deepcopy(raw_connection)
        credential = connection.get("credential")
        env_name = credential.get("env") if isinstance(credential, dict) else None
        if isinstance(env_name, str):
            _, source = lookup_credential(
                env_name, user_lora_root=config.user_lora_root
            )
            connection["credential_source"] = source
        else:
            connection["credential_source"] = "none"
        safe_connections[key] = connection
    models = mapping.get("models", {})
    if not isinstance(models, dict):
        models = {}
    safe_models: dict[str, dict] = {}
    for key, raw_model in models.items():
        if not isinstance(key, str) or not isinstance(raw_model, dict):
            continue
        safe_models[key] = deepcopy(raw_model)
    groups = mapping.get("model_groups", {})
    if not isinstance(groups, dict):
        groups = {}
    retry = config.resolved_agent.retry if config.resolved_agent is not None else None
    return RuntimeConfigResponse(
        approvals_enabled=config.runtime_approvals.enabled,
        workspace_root=config.workspace_root,
        lora_root=config.lora_root,
        agent=config.agent_alias,
        model_configuration_status=config.model_configuration_status,
        model_configuration_error=config.model_configuration_error,
        connections=safe_connections,
        models=safe_models,
        model_groups=deepcopy(groups),
        default_model_group=(
            config.resolved_agent.default_model_group
            if config.resolved_agent is not None
            else None
        ),
        retry={
            "max_attempts_per_model": retry.max_attempts_per_model if retry else 2,
            "attempt_idle_timeout_seconds": retry.attempt_idle_timeout_seconds if retry else 60.0,
            "backoff_initial": retry.backoff_initial if retry else 0.5,
            "backoff_maximum": retry.backoff_maximum if retry else 4.0,
            "backoff_multiplier": retry.backoff_multiplier if retry else 2.0,
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
