from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pygent.llm import ConnectionConfig, ModelConfig

from lora.config import replace_user_model_config, update_user_approvals
from lora.core.io import non_empty_string
from lora.credentials import set_user_credential
from lora.runtime.model_configuration import (
    CredentialEnvironment,
    builtin_model_catalogs,
    discover_models,
)
from lora.core.io import read_json
from lora_api.dependencies import ApiContext, get_api_context
from lora_api.models.requests import DiscoverModelsRequest, UpdateSettingsRequest
from lora_api.models.responses import RuntimeConfigResponse
from lora_api.services.project_service import config_response

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=RuntimeConfigResponse)
def get_settings(
    context: ApiContext = Depends(get_api_context),
) -> RuntimeConfigResponse:
    return config_response(context.config)


@router.patch("", response_model=RuntimeConfigResponse)
async def update_settings(
    request: UpdateSettingsRequest,
    context: ApiContext = Depends(get_api_context),
) -> RuntimeConfigResponse:
    overrides = _settings_overrides(request)
    if overrides.get("workspace_root"):
        workspace = Path(overrides["workspace_root"]).expanduser()
        if not workspace.is_dir():
            raise HTTPException(
                status_code=400,
                detail="Project folder does not exist or is not accessible. Choose an existing folder.",
            )
    user_lora_root = context.config.user_lora_root or ""
    agent_alias = str(
        overrides.get("agent_alias", context.agent_alias or context.config.agent_alias)
    )
    if request.native_model_config is not None:
        _replace_native_model_settings(
            request,
            user_lora_root=user_lora_root,
            agent_alias=agent_alias,
            context=context,
        )
    if request.approvals_enabled is not None:
        update_user_approvals(user_lora_root, enabled=request.approvals_enabled)
    config = await context.areload(overrides)
    context.remember_project(config.workspace_root)
    return config_response(config)


@router.get("/model-catalogs")
def get_model_catalogs() -> dict[str, Any]:
    return builtin_model_catalogs()


@router.post("/models/discover")
async def discover_connection_models(
    request: DiscoverModelsRequest,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, Any]:
    try:
        connection = ConnectionConfig.from_mapping(request.connection)
        credential = request.connection.get("credential")
        env_name = credential.get("env") if isinstance(credential, dict) else None
        transient = (
            {env_name: request.credential_value}
            if isinstance(env_name, str) and request.credential_value
            else {}
        )
        models = await discover_models(
            connection_name="discovery",
            protocol=request.protocol,
            connection=connection,
            credential_environ=CredentialEnvironment(
                context.config.user_lora_root or "", transient=transient
            ),
            timeout=request.timeout_seconds,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "model_discovery_failed", "message": str(exc)},
        ) from exc
    return {
        "models": [
            {"id": item.id, "created": item.created, "owned_by": item.owned_by}
            for item in models
        ]
    }


def _replace_native_model_settings(
    request: UpdateSettingsRequest,
    *,
    user_lora_root: str,
    agent_alias: str,
    context: ApiContext,
) -> None:
    assert request.native_model_config is not None
    try:
        parsed = ModelConfig.from_mapping(request.native_model_config)
        group_name = non_empty_string(request.default_model_group)
        if group_name is None or group_name not in parsed.model_groups:
            raise ValueError(
                f"default_model_group references unknown model group {group_name!r}"
            )
        _guard_persisted_session_selections(context, parsed)
        referenced_credentials = {
            credential["env"]
            for connection in request.native_model_config.get("connections", {}).values()
            if isinstance(connection, dict)
            and isinstance((credential := connection.get("credential")), dict)
            and isinstance(credential.get("env"), str)
        }
        unknown = set(request.credential_values) - referenced_credentials
        if unknown:
            raise ValueError(
                "credential_values references unknown environment variables: "
                + ", ".join(sorted(unknown))
            )
        retry = (
            request.retry.model_dump()
            if request.retry is not None
            else {"max_attempts_per_model": 2}
        )
        agents = [
            {
                "alias": agent_alias,
                "model_request": {
                    "default_model_group": group_name,
                    "retry": retry,
                },
            }
        ]
        for env_name, value in request.credential_values.items():
            secret = non_empty_string(value)
            if secret:
                set_user_credential(user_lora_root, env_name, secret)
        replace_user_model_config(
            user_lora_root,
            model_config=request.native_model_config,
            agents=agents,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_model_configuration",
                "path": "model_config",
                "message": str(exc),
            },
        ) from exc


def _guard_persisted_session_selections(
    context: ApiContext, replacement: ModelConfig
) -> None:
    from lora_api.project_state import build_session_scopes

    roots = [Path(context.config.lora_root) / "sessions"]
    roots.extend(
        Path(scope.lora_root) / "sessions"
        for scope in build_session_scopes(
            context.project_state,
            active_workspace_root=context.config.workspace_root,
        )
    )
    checked_roots: set[Path] = set()
    for root in roots:
        sessions_root = root.resolve()
        if sessions_root in checked_roots or not sessions_root.is_dir():
            continue
        checked_roots.add(sessions_root)
        for metadata_path in sessions_root.glob("*/metadata.json"):
            metadata = read_json(metadata_path)
            group_name = metadata.get("model_group_name")
            model_key = metadata.get("selected_model_key")
            if not isinstance(group_name, str) or not group_name:
                continue
            group = replacement.model_groups.get(group_name)
            if group is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Model group {group_name!r} is used by an existing session",
                )
            if isinstance(model_key, str) and model_key not in {
                entry.key for entry in group.models
            }:
                raise HTTPException(
                    status_code=409,
                    detail=f"Model {model_key!r} is used by an existing session",
                )


def _settings_overrides(request: UpdateSettingsRequest) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for request_field, context_field in (
        ("workspace_root", "workspace_root"),
        ("agent_alias", "agent_alias"),
    ):
        if request_field not in request.model_fields_set:
            continue
        overrides[context_field] = non_empty_string(getattr(request, request_field))
    if "max_steps" in request.model_fields_set:
        overrides["max_steps"] = request.max_steps
    if "context_window" in request.model_fields_set:
        overrides["context_window"] = request.context_window
    return overrides
