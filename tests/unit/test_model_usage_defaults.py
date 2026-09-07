from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import json
from pathlib import Path

import pytest
from pygent import Context, GenerationConfig, UserMessage, freeze_json_object
from pygent.llm import ModelProviderRequest, OpenAICompatibleAdapter
from pygent.llm._adapter_contracts import ModelProviderStreamPart
from pygent.llm._stream_accumulator import ModelStreamAccumulator
from pygent.llm.layer import _message_from_effect, _message_effect_value

from lora.config import load_run_config
from lora.runtime.agent.core import LoraAgent
from lora.runtime.agent.pipeline import checkpoint_conversation_message
from lora.runtime.context import LoraContext
from lora.runtime.service import LoraRuntimeService
from lora.sessions import SessionManager


def model_agent(tmp_path):
    config = load_run_config(workspace_root=tmp_path)
    for route in config.resolved_agent.routes:
        route.base_url = 'http://113.46.219.251:8080/v1'
        route.api_key = 'test-only'
    return LoraAgent(config)


def test_all_streaming_routes_request_usage(tmp_path):
    agent = model_agent(tmp_path)
    routes = agent.new_model_layer().model_group.routes
    assert routes
    for route in routes:
        request = ModelProviderRequest(route=route, message=UserMessage(content='OK'),
                                       context=Context(), generation=GenerationConfig())
        assert OpenAICompatibleAdapter().build_request(request).to_dict()['stream_options'] == {'include_usage': True}
    agent.resolved_agent.routes[0].base_url = 'https://api.deepseek.com'
    assert agent._model_routes()[0].provider_options.to_dict() == {'stream_options': {'include_usage': True}}


@pytest.mark.asyncio
async def test_managed_binding_uses_same_usage_routes(tmp_path):
    agent = model_agent(tmp_path)
    handle = SimpleNamespace(ensure_profile=AsyncMock())
    bound = SimpleNamespace(model_groups=SimpleNamespace(get=lambda requirement: handle))
    service = SimpleNamespace(initialize=AsyncMock(), binding=SimpleNamespace(bind=lambda module: bound),
                              model_resolver=SimpleNamespace(register=Mock(), resolver_id='test'),
                              config=agent.config)
    await LoraRuntimeService.bind(service, agent, agent)
    routes = handle.ensure_profile.call_args.kwargs['routes']
    assert routes == agent._model_routes()
    assert all(r.provider_options.to_dict()['stream_options']['include_usage'] for r in routes)


@pytest.mark.asyncio
async def test_usage_after_finish_survives_native_session_storage(tmp_path):
    config = load_run_config(workspace_root=tmp_path)
    manager = SessionManager(config)
    session = manager.create(case_id='chat', mode='agent')
    run = manager.start_case_run(session.session_id, 'chat', run_config=config)
    context = LoraContext(session_id=run.session_id, case_id=run.case_id,
                          case_run_id=run.case_run_id, run_dir=run.run_dir, turn_id='usage-test')
    accumulator = ModelStreamAccumulator(generation=GenerationConfig(), tools=())
    # Shape/order observed from the IP service: usage comes AFTER finish_reason.
    chunks = [
        {'choices': [{'delta': {'content': 'OK'}}]},
        {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
        {'choices': [{'delta': {}}], 'usage': {
            'prompt_tokens': 88, 'completion_tokens': 16, 'total_tokens': 104,
            'completion_tokens_details': {'reasoning_tokens': 14},
            'prompt_tokens_details': {'cached_tokens': 0}}},
        {'done': True},
    ]
    adapter = OpenAICompatibleAdapter()
    for chunk in chunks:
        for part in adapter.parse_stream_events(None, freeze_json_object(chunk)):
            await accumulator.consume(ModelProviderStreamPart(
                part.kind, {**part.data.to_dict(), 'route_id': 'primary', 'attempt': 1}), None)
    response = await accumulator.finish(None)
    message = _message_from_effect(freeze_json_object({
        'outcome': 'succeeded', 'message': _message_effect_value(response.message),
        'usage': response.usage.to_dict(), 'provider_request_id': response.provider_request_id}))
    await checkpoint_conversation_message(config, context, message, boundary='model')
    expected = {'input_tokens': 88, 'output_tokens': 16, 'total_tokens': 104,
                'reasoning_tokens': 14, 'cached_input_tokens': 0}
    messages = list(Path(config.lora_root).glob('sessions/**/messages.jsonl'))
    assert messages
    for path in messages:
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').split('\n') if line.strip()]
        assert rows[-1]['usage'] == expected
