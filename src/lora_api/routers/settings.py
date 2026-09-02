from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from lora.config import load_run_config, update_user_model_group
from lora.core.io import non_empty_string
from lora.credentials import set_user_credential
from lora_api.dependencies import ApiContext, get_api_context
from lora_api.models.requests import UpdateSettingsRequest
from lora_api.models.responses import RuntimeConfigResponse
from lora_api.services.project_service import config_response

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=RuntimeConfigResponse)
def get_settings(context: ApiContext = Depends(get_api_context)) -> RuntimeConfigResponse:
    return config_response(context.config)


@router.patch("", response_model=RuntimeConfigResponse)
async def update_settings(
    request: UpdateSettingsRequest,
    context: ApiContext = Depends(get_api_context),
) -> RuntimeConfigResponse:
    overrides = _settings_overrides(request)
    user_lora_root = context.config.user_lora_root or ""
    agent_alias = overrides.get("agent_alias", context.agent_alias or context.config.agent_alias)
    if request.model_group is not None:
        model_request = _model_request_payload(request)
        update_user_model_group(
            user_lora_root,
            alias=agent_alias,
            model_request=model_request,
        )
        for route in request.model_group.routes:
            api_key = non_empty_string(route.api_key)
            if api_key:
                set_user_credential(user_lora_root, route.api_key_env.strip(), api_key)
    target_config = load_run_config(
        workspace_root=overrides.get("workspace_root", context.workspace_root),
        agent_alias=overrides.get("agent_alias", context.agent_alias),
        max_steps=overrides.get("max_steps", context.max_steps),
        context_window=overrides.get("context_window", context.context_window),
    )
    api_key = non_empty_string(request.api_key)
    if api_key:
        if target_config.resolved_agent is None:
            raise ValueError("selected agent has no model routes")
        api_key_env = target_config.resolved_agent.routes[0].api_key_env
        set_user_credential(target_config.user_lora_root or "", api_key_env, api_key)
    config = await context.areload(overrides)
    context.remember_project(config.workspace_root)
    return config_response(config)


def _model_request_payload(request: UpdateSettingsRequest) -> dict[str, Any]:
    group = request.model_group
    if group is None:
        raise ValueError("model_group is required")
    return {
        "profile": group.profile.strip(),
        "routes": [
            {
                "id": route.id.strip(),
                "provider": route.provider.strip(),
                "model_name": route.model_name.strip(),
                "base_url": route.base_url.strip(),
                "api_key_env": route.api_key_env.strip(),
            }
            for route in group.routes
        ],
        "fallback": [route_id.strip() for route_id in group.fallback],
        "retry": group.retry.model_dump(),
    }


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
