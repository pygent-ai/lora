from __future__ import annotations

from fastapi import APIRouter, Depends

from lora.config import load_run_config
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
def update_settings(
    request: UpdateSettingsRequest,
    context: ApiContext = Depends(get_api_context),
) -> RuntimeConfigResponse:
    overrides = _settings_overrides(request)
    target_config = load_run_config(
        workspace_root=overrides.get("workspace_root", context.workspace_root),
        config_file=overrides.get("config_path", context.config_path),
        agent_alias=overrides.get("agent_alias", context.agent_alias),
        model=overrides.get("model", context.model),
        max_steps=overrides.get("max_steps", context.max_steps),
        context_window=overrides.get("context_window", context.context_window),
    )
    api_key = _clean_optional_string(request.api_key)
    if api_key:
        api_key_env = "DEEPSEEK_API_KEY"
        if target_config.resolved_agent is not None:
            api_key_env = target_config.resolved_agent.api_key_env
        set_user_credential(target_config.user_lora_root or "", api_key_env, api_key)
    config = context.reload(overrides)
    context.remember_project(config.workspace_root)
    return config_response(config)


def _settings_overrides(request: UpdateSettingsRequest) -> dict[str, object | None]:
    overrides: dict[str, object | None] = {}
    for request_field, context_field in (
        ("workspace_root", "workspace_root"),
        ("config_path", "config_path"),
        ("agent_alias", "agent_alias"),
        ("model", "model"),
    ):
        if request_field not in request.model_fields_set:
            continue
        overrides[context_field] = _clean_optional_string(getattr(request, request_field))
    if "max_steps" in request.model_fields_set:
        overrides["max_steps"] = request.max_steps
    if "context_window" in request.model_fields_set:
        overrides["context_window"] = request.context_window
    return overrides


def _clean_optional_string(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean or None
