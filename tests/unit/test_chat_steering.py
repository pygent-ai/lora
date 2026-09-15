from __future__ import annotations

import asyncio
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from pygent import AIMessage
from pygent.llm import ModelExecution, ModelProviderResponse

from lora.config import load_run_config
from tests.unit.test_model_configuration import native_runtime_config
from lora.runtime.service import LoraRuntimeService
from lora.sessions import SessionManager
from lora_api.dependencies import get_api_context
from lora_api.routers.chat import router
from lora_api.services.chat_runner import ChatRunRegistry


@pytest.mark.asyncio
async def test_http_steering_reaches_running_react_and_persists_once(tmp_path: Path) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    requests = []

    class Invoker:
        def validate_model(self, model):
            pass

        def execute(self, *, message, context, **kwargs):
            requests.append((message, context))
            index = len(requests)

            async def invoke(emit):
                if index == 1:
                    started.set()
                    await release.wait()
                return ModelProviderResponse(message=AIMessage(content=f"answer-{index}"), usage={})
            return ModelExecution(invoke)

        async def aclose(self):
            pass

    config = native_runtime_config(tmp_path)
    config.eternal_conversation.enabled = False
    manager = SessionManager(config)
    session = manager.create("chat", mode="chat")
    run = manager.start_case_run(session.session_id, "chat", run_config=config)
    service = LoraRuntimeService(config)
    invoker = Invoker()
    service._model_invokers[config.resolved_agent.alias] = invoker
    for agent in service._agent_definitions.values():
        agent.llm = invoker
    try:
        handle = await service.start_turn(
            manager=manager, message="original task", run_ref=run, turn_id="steering-test",
            interactive_approvals=False, deadline=time.monotonic() + 30,
        )
        await asyncio.wait_for(started.wait(), 10)
        active = SimpleNamespace(run_ref=run, runtime_service=service)
        coordinator = SimpleNamespace(find_execution=AsyncMock(return_value=active))
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_api_context] = lambda: SimpleNamespace(chat_registry=ChatRunRegistry(coordinator))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            url = f"/chat/executions/{handle.execution_id}/steering"
            body = {"session_id": session.session_id, "input_id": "input-1", "message": "focus on tests"}
            assert (await client.post(url, json={**body, "message": "  "})).status_code == 422
            assert (await client.post(url, json={**body, "session_id": "another-session"})).status_code == 404
            response = await client.post(url, json=body)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "accepted"
            assert (await client.post(url, json=body)).json()["status"] == "duplicate"
            assert (await client.post(url, json={**body, "input_id": "input-2", "message": "use Chinese"})).json()["status"] == "accepted"
            release.set()
            await asyncio.wait_for(handle.result(), 10)
            assert (await client.post(url, json={**body, "input_id": "late"})).status_code == 409
        assert len(requests) == 2
        message, context = requests[1]
        assert message.content == "use Chinese"
        assert any(item.content == "focus on tests" for item in context.messages)
        history = manager.load(session.session_id).history
        steering = [item for item in history if item.get("kind") == "lora.user.steering"]
        assert [item["content"] for item in steering] == ["focus on tests", "use Chinese"]
    finally:
        release.set()
        await service.close()
