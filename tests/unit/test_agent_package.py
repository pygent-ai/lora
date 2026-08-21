from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pygent import (
    Context,
    GenerationConfig,
    ModelErrorKind,
    ModelRoute,
    UserMessage,
    freeze_json_object,
)
from pygent.llm import (
    ModelProviderError,
    ModelProviderRequest,
    OpenAICompatibleAdapter,
)

from lora.config import load_run_config
from lora.runtime import agent
from lora.runtime.agent import LoraAgent, PromptRenderContext, _render_available_tools_prompt
from lora.runtime.agent.core import (
    MODEL_MAX_OUTPUT_TOKENS,
    MODEL_RETRYABLE_ERROR_KINDS,
    PYGENT_VERIFY_SSL_ENV,
    _route_supports_streaming,
    _verify_ssl_from_env,
)


def test_agent_package_exports_supported_entry_points() -> None:
    assert LoraAgent is agent.LoraAgent
    assert PromptRenderContext is agent.PromptRenderContext
    assert callable(_render_available_tools_prompt)


def test_agent_runtime_is_split_by_responsibility() -> None:
    assert LoraAgent.__module__ == "lora.runtime.agent.core"
    assert PromptRenderContext.__module__ == "lora.runtime.agent.prompt_models"


def test_deepseek_routes_disable_streaming_for_reliable_tool_arguments() -> None:
    assert _route_supports_streaming(SimpleNamespace(base_url="https://api.deepseek.com")) is False
    assert _route_supports_streaming(SimpleNamespace(base_url="https://api.openai.com/v1")) is True


def test_coding_agent_has_enough_output_budget_to_reach_tool_execution() -> None:
    assert MODEL_MAX_OUTPUT_TOKENS >= 4096


def test_file_editing_guidance_comes_from_pygent_tool_definitions(tmp_path: Path) -> None:
    model_agent = LoraAgent(load_run_config(workspace_root=tmp_path))
    definitions = {definition.name: definition for definition in model_agent.tool_definitions}

    assert "multiple smaller atomic edit calls" in definitions["edit"].description
    assert "Prefer edit for focused changes" in definitions["write"].description
    assert "omission placeholders" in definitions["write"].description


def test_model_retry_policy_retries_incomplete_provider_responses() -> None:
    retry = SimpleNamespace(
        max_attempts_per_route=5,
        attempt_timeout_seconds=60,
        backoff_initial=0.5,
        backoff_maximum=4,
        backoff_multiplier=2,
    )
    route = SimpleNamespace(id="primary", provider="openai", model_name="test-model")
    model_agent = LoraAgent.__new__(LoraAgent)
    model_agent.resolved_agent = SimpleNamespace(
        alias="test",
        routes=(route,),
        fallback=(route.id,),
        retry=retry,
    )
    model_agent.managed_model = False
    model_agent.llm = object()
    model_agent._toolkit = None
    model_agent._external_tools = ()

    retry_policy = model_agent.new_model_layer().retry_policy

    assert retry_policy.max_attempts_per_route == 5
    assert retry_policy.retry_on == MODEL_RETRYABLE_ERROR_KINDS
    assert ModelErrorKind.INVALID_RESPONSE in retry_policy.retry_on
    assert ModelErrorKind.AUTHENTICATION not in retry_policy.retry_on
    assert ModelErrorKind.INVALID_REQUEST not in retry_policy.retry_on


def test_pygent_adapter_classifies_invalid_tool_calls_for_model_retry() -> None:
    adapter = OpenAICompatibleAdapter()
    request = ModelProviderRequest(
        route=ModelRoute("primary", provider="openai", model="test-model"),
        message=UserMessage(content="inspect the workspace"),
        context=Context(),
        generation=GenerationConfig(),
    )
    payload = freeze_json_object(
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [{"type": "function", "function": {}}],
                    }
                }
            ]
        }
    )

    with pytest.raises(ModelProviderError) as caught:
        adapter.parse_response(request, payload)

    assert caught.value.kind is ModelErrorKind.INVALID_RESPONSE
    assert caught.value.kind in MODEL_RETRYABLE_ERROR_KINDS


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("true", True),
        ("1", True),
        ("YES", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("NO", False),
        ("off", False),
    ],
)
def test_verify_ssl_is_controlled_by_environment(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
    expected: bool,
) -> None:
    monkeypatch.setenv(PYGENT_VERIFY_SSL_ENV, raw_value)

    assert _verify_ssl_from_env() is expected


def test_verify_ssl_uses_pygent_default_when_environment_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PYGENT_VERIFY_SSL_ENV, raising=False)

    assert _verify_ssl_from_env() is None


def test_verify_ssl_rejects_invalid_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PYGENT_VERIFY_SSL_ENV, "sometimes")

    with pytest.raises(ValueError, match=PYGENT_VERIFY_SSL_ENV):
        _verify_ssl_from_env()
