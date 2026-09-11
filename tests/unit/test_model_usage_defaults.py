import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pygent import Context, GenerationConfig, UserMessage, freeze_json_object
from pygent.llm import ModelProviderRequest, OpenAICompatibleAdapter
from pygent.llm._adapter_contracts import ModelProviderStreamPart
from pygent.llm._stream_accumulator import ModelStreamAccumulator
from pygent.llm.layer import _message_effect_value, _message_from_effect

from lora.config import load_run_config
from lora.runtime.agent.core import LoraAgent
from lora.runtime.agent.pipeline import checkpoint_conversation_message
from lora.runtime.context import LoraContext
from lora.runtime.service import LoraRuntimeService
from lora.sessions import SessionManager


def model_agent(tmp_path):
    config = load_run_config(workspace_root=tmp_path)
    for route in config.resolved_agent.routes:
        route.base_url = "http://113.46.219.251:8080/v1"
        route.api_key = "test-only"
    return LoraAgent(config)


def test_all_model_entries_enable_streaming_and_leave_usage_to_client(tmp_path):
    agent = model_agent(tmp_path)
    entries = agent.new_model_layer().model_group.models
    assert entries
    for entry in entries:
        request = ModelProviderRequest(
            model_key=entry.name,
            model=entry.spec,
            message=UserMessage(content="OK"),
            context=Context(),
            generation=GenerationConfig(),
        )
        assert entry.spec.capabilities.streaming.output == ("text",)
        assert "stream_options" not in OpenAICompatibleAdapter().build_request(
            request
        ).to_dict()
    official_route = agent.resolved_agent.routes[0]
    official_route.base_url = "https://api.deepseek.com"
    entries_by_name = {entry.name: entry for entry in agent._model_entries()}
    assert entries_by_name[official_route.id].spec.provider_options.to_dict() == {
        "thinking": {"type": "disabled"}
    }


@pytest.mark.asyncio
async def test_managed_binding_uses_same_usage_routes(tmp_path):
    agent = model_agent(tmp_path)
    handle = SimpleNamespace(ensure_profile=AsyncMock())
    bound = SimpleNamespace(
        model_groups=SimpleNamespace(get=lambda requirement: handle)
    )
    service = SimpleNamespace(
        initialize=AsyncMock(),
        binding=SimpleNamespace(bind=lambda module: bound),
        model_resolver=SimpleNamespace(register=Mock(), resolver_id="test"),
        config=agent.config,
    )
    await LoraRuntimeService.bind(service, agent, agent)
    models = handle.ensure_profile.call_args.kwargs["models"]
    assert models == agent._model_entries()
    assert all(model.spec.capabilities.streaming.output == ("text",) for model in models)


@pytest.mark.asyncio
async def test_usage_after_finish_survives_native_session_storage(tmp_path):
    config = load_run_config(workspace_root=tmp_path)
    manager = SessionManager(config)
    session = manager.create(case_id="chat", mode="agent")
    run = manager.start_case_run(session.session_id, "chat", run_config=config)
    context = LoraContext(
        session_id=run.session_id,
        case_id=run.case_id,
        case_run_id=run.case_run_id,
        run_dir=run.run_dir,
        turn_id="usage-test",
    )
    accumulator = ModelStreamAccumulator(generation=GenerationConfig(), tools=())
    # Shape/order observed from the IP service: usage comes AFTER finish_reason.
    chunks = [
        {"choices": [{"delta": {"content": "OK"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {
            "choices": [{"delta": {}}],
            "usage": {
                "prompt_tokens": 88,
                "completion_tokens": 16,
                "total_tokens": 104,
                "completion_tokens_details": {"reasoning_tokens": 14},
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
        {"done": True},
    ]
    adapter = OpenAICompatibleAdapter()
    entry = LoraAgent(config)._model_entries()[0]
    request = ModelProviderRequest(
        model_key=entry.name,
        model=entry.spec,
        message=UserMessage(content="OK"),
        context=Context(),
        generation=GenerationConfig(),
    )
    decoder = adapter.create_stream_decoder(request)
    for chunk in chunks:
        for part in decoder.feed(freeze_json_object(chunk)):
            await accumulator.consume(
                ModelProviderStreamPart(
                    part.kind,
                    {**part.data.to_dict(), "model_key": entry.name, "attempt": 1},
                ),
                None,
            )
    decoder.finish()
    response = await accumulator.finish(None)
    message = _message_from_effect(
        freeze_json_object(
            {
                "outcome": "succeeded",
                "message": _message_effect_value(response.message),
                "usage": response.usage.to_dict(),
                "provider_request_id": response.provider_request_id,
            }
        )
    )
    await checkpoint_conversation_message(config, context, message, boundary="model")
    expected = {
        "input_tokens": 88,
        "output_tokens": 16,
        "total_tokens": 104,
        "reasoning_tokens": 14,
        "cached_input_tokens": 0,
    }
    messages = list(Path(config.lora_root).glob("sessions/**/messages.jsonl"))
    assert messages
    for path in messages:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").split("\n")
            if line.strip()
        ]
        assert rows[-1]["usage"] == expected
