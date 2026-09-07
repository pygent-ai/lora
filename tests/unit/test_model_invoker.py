from __future__ import annotations

import asyncio

import pytest
from pygent import AIMessage, freeze_json_object
from pygent.llm import DefaultModelInvoker, ModelExecution, ModelProviderResponse

from lora.runtime.agent.model_invoker import LoraModelInvoker


@pytest.mark.asyncio
async def test_reasoning_is_scoped_to_each_call_and_reset_on_retry(monkeypatch):
    def execute(self, *, label):
        async def invoke(emit):
            identity = {'route_id': 'primary', 'attempt': 1}
            await emit('model.reasoning.delta', freeze_json_object({**identity, 'text': 'discarded'}))
            await asyncio.sleep(0)
            await emit('model.output.reset', freeze_json_object(identity))
            await emit('model.reasoning.delta', freeze_json_object({**identity, 'text': label}))
            return ModelProviderResponse(message=AIMessage(content=label, metadata={'route_id': 'primary'}), usage={})
        return ModelExecution(invoke)

    monkeypatch.setattr(DefaultModelInvoker, 'execute', execute)
    invoker = LoraModelInvoker(adapters={}, clients={})
    try:
        responses = await asyncio.gather(*(invoker.execute(label=label).result() for label in ('first', 'second')))
        assert [dict(response.message.metadata) for response in responses] == [
            {'route_id': 'primary', 'reasoning_content': 'first'},
            {'route_id': 'primary', 'reasoning_content': 'second'},
        ]
    finally:
        await invoker.aclose()


@pytest.mark.asyncio
async def test_cancel_waits_for_native_model_and_event_relay_cleanup(monkeypatch):
    started = asyncio.Event()
    cleaned = asyncio.Event()

    def execute(self, **kwargs):
        async def invoke(emit):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        return ModelExecution(invoke)

    monkeypatch.setattr(DefaultModelInvoker, 'execute', execute)
    invoker = LoraModelInvoker(adapters={}, clients={})
    try:
        execution = invoker.execute()
        await started.wait()
        await execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution.result()
        assert cleaned.is_set()
        assert not any(task.get_name() == 'lora-model-display-events' and not task.done() for task in asyncio.all_tasks())
    finally:
        await invoker.aclose()
