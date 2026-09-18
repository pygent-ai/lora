from __future__ import annotations

from pathlib import Path
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

from lora.runtime.agent.common import DEFAULT_REACT_MAX_STEPS
from lora.runtime.agent.compressor import LoraCompressorModule
from lora.runtime.agent.core import (
    MODEL_RETRYABLE_ERROR_KINDS,
    LoraAgent,
    _actual_model_key,
    _model_trace_payload,
)
from lora.runtime.agent.pipeline import (
    MAX_IDENTICAL_FOREGROUND_TOOL_CALLS,
    RepeatedToolCallGuardModule,
)
from lora.runtime.agent.prompt_models import PromptRenderContext
from lora.runtime.context import LoraContext
from tests.unit.test_model_usage_defaults import model_agent


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
    agent = model_agent(tmp_path)

    assert issubclass(LoraContext, PygentAgentContext)
    assert isinstance(agent.foreground, PygentAgent)
    assert isinstance(
        agent.foreground.react.model.compressor, LoraCompressorModule
    )
    assert agent.foreground.react.max_steps == DEFAULT_REACT_MAX_STEPS == 500
    assert agent.foreground.react.max_model_calls == DEFAULT_REACT_MAX_STEPS


def test_coding_agent_uses_provider_generation_defaults(tmp_path: Path) -> None:
    agent = model_agent(tmp_path)

    generation = agent.new_model_layer().generation
    assert generation.temperature is None
    assert generation.max_output_tokens is None


def test_compressor_can_build_request_without_visible_tools(tmp_path: Path) -> None:
    agent = model_agent(tmp_path)
    model = agent.foreground.react.model.compressor.model
    entry = agent._model_entries()[0]
    request = ModelProviderRequest(
        model_key=entry.key,
        model=entry.spec,
        message=UserMessage(content="Summarize the conversation"),
        context=Context(tools=()),
        generation=model.generation,
        tools=(),
    )

    payload = OpenAICompatibleAdapter().build_request(request)

    assert "tools" not in payload
    assert "tool_choice" not in payload


def test_trace_payload_uses_native_model_identity(tmp_path: Path) -> None:
    config = model_agent(tmp_path).config
    assert _model_trace_payload(
        config, group_name="coding", model_key="backup"
    ) == {
        "model_group": "coding",
        "model_key": "backup",
        "provider": "test",
        "model_id": "model-backup",
    }


def test_trace_actual_model_comes_from_response_metadata() -> None:
    assert _actual_model_key(
        AIMessage(content="ok", metadata={"model_key": "backup"})
    ) == "backup"
    assert _actual_model_key(AIMessage(content="missing metadata")) is None


def test_file_editing_guidance_comes_from_pygent_tool_definitions(
    tmp_path: Path,
) -> None:
    agent = model_agent(tmp_path)
    definitions = {
        definition.name: definition for definition in agent.tool_definitions
    }

    assert "multiple smaller atomic edit calls" in definitions["edit"].description
    assert "Prefer edit for focused changes" in definitions["write"].description
    assert "omission placeholders" in definitions["write"].description


def test_model_retry_policy_retries_incomplete_provider_responses(tmp_path: Path) -> None:
    agent = model_agent(tmp_path)
    agent.resolved_agent.retry.max_attempts_per_model = 5

    retry_policy = agent.new_model_layer().retry_policy

    assert retry_policy.max_attempts_per_model == 5
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
        model_key=entry.key,
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
