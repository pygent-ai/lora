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

from lora.runtime.agent.core import LoraAgent
from lora.runtime.agent.pipeline import checkpoint_conversation_message
from lora.runtime.context import LoraContext
from lora.runtime.service import LoraRuntimeService
from lora.sessions import SessionManager
from lora.schema import ResolvedAgentConfig, RunConfig
from tests.unit.test_model_configuration import native_mapping


def model_agent(tmp_path):
    mapping = native_mapping(group=("main", "backup"))
    mapping["connections"]["shared"]["credential"] = {"none": True}
    config = RunConfig(
        workspace_root=tmp_path,
        lora_root=tmp_path / ".lora",
        model_config_mapping=mapping,
        resolved_agent=ResolvedAgentConfig(
            alias="default", default_model_group="coding"
        ),
    )
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
    entries_by_name = {entry.name: entry for entry in agent._model_entries()}
    assert entries_by_name["main"].spec.provider_options.to_dict() == {}


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
    assert handle.ensure_profile.await_count == 2
    calls = handle.ensure_profile.await_args_list
    assert [call.kwargs["profile"] for call in calls] == [
        "preferred:main",
        "preferred:backup",
    ]
    assert [entry.name for entry in calls[1].kwargs["models"]] == [
        "backup",
        "main",
    ]


@pytest.mark.asyncio
async def test_start_turn_admits_persisted_session_preference(tmp_path):
    config = model_agent(tmp_path).config
    manager = SessionManager(config)
    session = manager.create(case_id="chat", mode="agent")
    manager.set_selected_model(session.session_id, "backup")
    run = manager.start_case_run(session.session_id, "chat", run_config=config)
    agent = SimpleNamespace(llm=object())
    handle = SimpleNamespace(execution_id="execution-1")
    service = LoraRuntimeService.__new__(LoraRuntimeService)
    service.config = config
    service.initialize = AsyncMock()
    service.new_agent = Mock(return_value=agent)
    service._prepare_turn = AsyncMock(
        return_value=(
            UserMessage(content="hello"),
            LoraContext(
                session_id=session.session_id,
                case_id="chat",
                case_run_id=run.case_run_id,
                run_dir=run.run_dir,
                turn_id="turn-1",
            ),
        )
    )
    service.bind = AsyncMock(return_value=object())
    service._start_agent_execution = AsyncMock(return_value=handle)
    service.reminders = SimpleNamespace(
        acknowledge_initial=AsyncMock(),
        release_initial=AsyncMock(),
    )
    service._record_execution_id = Mock()

    returned = await service.start_turn(
        manager=manager,
        message="hello",
        run_ref=run,
        turn_id="turn-1",
        interactive_approvals=True,
    )

    options = service._start_agent_execution.await_args.kwargs["execution"]
    assert returned is handle
    assert options.model_calls.to_dict() == {
        "lora:coding": {"profile": "preferred:backup"}
    }


@pytest.mark.asyncio
async def test_usage_after_finish_survives_native_session_storage(tmp_path):
    config = model_agent(tmp_path).config
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
