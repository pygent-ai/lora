from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pygent import (
    AIMessage,
    CapabilityPresetCatalog,
    Context,
    GenerationConfig,
    ModelEntry,
    ModelErrorKind,
    ModelSpec,
    Module,
    PygentAgent,
    PygentAgentContext,
    ToolCall,
    ToolMessage,
    UserMessage,
    freeze_json_object,
)
from pygent.llm import (
    ModelProviderError,
    ModelProviderRequest,
    OpenAICompatibleAdapter,
)

from lora.config import load_run_config
from lora.runtime.agent.common import DEFAULT_REACT_MAX_STEPS
from lora.runtime.agent.compressor import LoraCompressorModule
from lora.runtime.agent.core import (
    MODEL_RETRYABLE_ERROR_KINDS,
    PYGENT_VERIFY_SSL_ENV,
    LoraAgent,
    _actual_model_route,
    _model_route_trace_payload,
    _preferred_model_route,
    _verify_ssl_from_env,
)
from lora.runtime.agent.pipeline import (
    MAX_IDENTICAL_FOREGROUND_TOOL_CALLS,
    RepeatedToolCallGuardModule,
)
from lora.runtime.agent.prompt_models import PromptRenderContext
from lora.runtime.context import LoraContext
from lora.schema import ModelRouteConfig, ResolvedAgentConfig


def test_agent_runtime_is_split_by_responsibility() -> None:
    assert LoraAgent.__module__ == "lora.runtime.agent.core"
    assert PromptRenderContext.__module__ == "lora.runtime.agent.prompt_models"


def test_runtime_packages_do_not_reexport_implementation_symbols() -> None:
    import lora.runtime as runtime_package
    import lora.runtime.agent as agent_package

    assert not hasattr(runtime_package, "ToolObserver")
    assert not hasattr(runtime_package, "LoraAgent")
    assert not hasattr(agent_package, "LoraAgent")
    assert not hasattr(agent_package, "PromptRenderContext")


class _EchoToolModule(Module[AIMessage, ToolMessage]):
    async def forward(self, message, context):
        del message
        return ToolMessage(content="ok"), context


@pytest.mark.asyncio
async def test_foreground_identical_tool_call_guard_is_scoped_to_turn() -> None:
    guard = RepeatedToolCallGuardModule(_EchoToolModule())
    message = AIMessage(
        tool_calls=(
            ToolCall(
                call_id="call-1",
                name="bash",
                arguments={"command": "same command"},
            ),
        )
    )
    first_turn = LoraContext(turn_id="turn-1")

    for _ in range(MAX_IDENTICAL_FOREGROUND_TOOL_CALLS - 1):
        await guard.invoke(message, first_turn)
    with pytest.raises(
        RuntimeError, match="identical tool arguments.*durable baseline"
    ):
        await guard.invoke(message, first_turn)

    await guard.invoke(message, LoraContext(turn_id="turn-2"))


def test_lora_foreground_uses_pygent_030_native_agent_and_compressor(
    tmp_path: Path,
) -> None:
    config = load_run_config(workspace_root=tmp_path)
    assert config.resolved_agent is not None
    for route in config.resolved_agent.routes:
        route.api_key = "native-agent-test"
        route.api_key_source = "test"

    model_agent = LoraAgent(config)

    assert issubclass(LoraContext, PygentAgentContext)
    assert isinstance(model_agent.foreground, PygentAgent)
    assert isinstance(
        model_agent.foreground.react.model.compressor, LoraCompressorModule
    )
    assert model_agent.foreground.react.max_steps == DEFAULT_REACT_MAX_STEPS == 500
    assert model_agent.foreground.react.max_model_calls == DEFAULT_REACT_MAX_STEPS


@pytest.mark.parametrize(
    "base_url", ["https://api.deepseek.com", "https://api.openai.com/v1"]
)
def test_model_invoker_enables_streaming_for_all_routes(
    monkeypatch, base_url: str
) -> None:
    import lora.runtime.agent.core as core

    route = SimpleNamespace(
        id="primary", provider="openai", base_url=base_url, api_key="test"
    )
    agent = SimpleNamespace(_resolved_routes=lambda: (route,))
    monkeypatch.setattr(core, "OpenAICompatibleClient", lambda **kwargs: object())
    monkeypatch.setattr(core, "LoraModelInvoker", lambda **kwargs: kwargs)

    invoker = LoraAgent._build_model_invoker(agent)
    assert set(invoker["adapters"]) == {"openai_chat_completions"}
    assert set(invoker["clients"]) == {"primary"}


def test_coding_agent_uses_provider_generation_defaults(tmp_path: Path) -> None:
    config = load_run_config(workspace_root=tmp_path)
    assert config.resolved_agent is not None
    for route in config.resolved_agent.routes:
        route.api_key = "unbounded-output-test"
        route.api_key_source = "test"

    model_agent = LoraAgent(config)

    generation = model_agent.new_model_layer().generation
    assert generation.temperature is None
    assert generation.max_output_tokens is None


def test_trace_preferred_route_follows_fallback_order_instead_of_route_order() -> None:
    fallback_route = ModelRouteConfig(
        id="fallback",
        provider="openai",
        model_name="fallback-model",
        base_url="https://fallback.example/v1",
        api_key_env="FALLBACK_KEY",
        api_key_source="env:FALLBACK_KEY",
    )
    primary_route = ModelRouteConfig(
        id="primary",
        provider="openai",
        model_name="primary-model",
        base_url="https://primary.example/v1",
        api_key_env="PRIMARY_KEY",
        api_key_source="env:PRIMARY_KEY",
    )
    config = ResolvedAgentConfig(
        alias="trace-test",
        routes=(fallback_route, primary_route),
        fallback=("primary", "fallback"),
    )

    preferred = _preferred_model_route(config)

    assert preferred is primary_route
    assert _model_route_trace_payload(preferred) == {
        "model_name": "primary-model",
        "model_route": "primary",
        "model_base_url": "https://primary.example/v1",
        "api_key_source": "env:PRIMARY_KEY",
    }


def test_trace_actual_route_comes_from_model_response_metadata() -> None:
    primary_route = ModelRouteConfig(
        id="primary",
        provider="openai",
        model_name="primary-model",
        base_url="https://primary.example/v1",
        api_key_env="PRIMARY_KEY",
    )
    fallback_route = ModelRouteConfig(
        id="fallback",
        provider="openai",
        model_name="fallback-model",
        base_url="https://fallback.example/v1",
        api_key_env="FALLBACK_KEY",
    )
    config = ResolvedAgentConfig(
        alias="trace-test",
        routes=(primary_route, fallback_route),
        fallback=("primary", "fallback"),
    )

    actual = _actual_model_route(
        config,
        AIMessage(content="ok", metadata={"model_key": "fallback"}),
    )

    assert actual is fallback_route
    assert _actual_model_route(config, AIMessage(content="missing metadata")) is None


def test_file_editing_guidance_comes_from_pygent_tool_definitions(
    tmp_path: Path,
) -> None:
    model_agent = LoraAgent(load_run_config(workspace_root=tmp_path))
    definitions = {
        definition.name: definition for definition in model_agent.tool_definitions
    }

    assert "multiple smaller atomic edit calls" in definitions["edit"].description
    assert "Prefer edit for focused changes" in definitions["write"].description
    assert "omission placeholders" in definitions["write"].description


def test_model_retry_policy_retries_incomplete_provider_responses() -> None:
    retry = SimpleNamespace(
        max_attempts_per_route=5,
        attempt_idle_timeout_seconds=60,
        backoff_initial=0.5,
        backoff_maximum=4,
        backoff_multiplier=2,
    )
    route = SimpleNamespace(
        id="primary",
        provider="openai",
        model_name="test-model",
        base_url="https://example.test/v1",
    )
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
    capabilities = CapabilityPresetCatalog.builtin().presets[
        "text_tools_structured_reasoning"
    ].materialize(context_tokens=1_000_000, max_output_tokens=384_000)
    entry = ModelEntry(
        "primary",
        ModelSpec(
            provider="openai",
            model_id="test-model",
            protocol="openai_chat_completions",
            capabilities=capabilities,
        ),
    )
    request = ModelProviderRequest(
        model_key=entry.name,
        model=entry.spec,
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
