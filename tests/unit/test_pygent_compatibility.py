from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pygent import Context, UserMessage
from pygent.llm import OpenAICompatibleClient

from examples import react_agent_demo
from lora.config import load_run_config
from lora.runtime.agent import core
from lora.schema import ModelRetryConfig, ModelRouteConfig, ResolvedAgentConfig


def _answer_stream() -> httpx.Response:
    chunk = {
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "content": "compatible"},
            "finish_reason": "stop",
        }],
    }
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [1, 3])
async def test_native_invoker_preserves_configured_retry_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attempts: int,
) -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        requests.append(model)
        if model == "primary":
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return _answer_stream()

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(core, "OpenAICompatibleClient", lambda **kwargs:
            OpenAICompatibleClient(
                base_url=kwargs["base_url"], api_key=kwargs["api_key"], client=client,
            ))
        resolved = ResolvedAgentConfig(
            alias="compatibility",
            routes=tuple(ModelRouteConfig(
                id=name, provider="openai", model_name=name,
                base_url="https://example.test/v1", api_key_env="TEST_API_KEY",
                api_key="test", api_key_source="test",
            ) for name in ("backup", "primary")),
            fallback=("primary", "backup"),
            retry=ModelRetryConfig(
                max_attempts_per_route=attempts, backoff_initial=0, backoff_maximum=0,
            ),
        )
        agent = core.LoraAgent(load_run_config(workspace_root=tmp_path), resolved_agent=resolved)
        try:
            layer = agent.new_model_layer()
            answer, _ = await layer.invoke(
                UserMessage(content="hello"), Context(tools=layer.tools),
            )
            assert answer.content == "compatible"
            assert core._actual_model_route(resolved, answer).id == "backup"
            assert requests == ["primary"] * attempts + ["backup"]
        finally:
            await agent.llm.aclose()


@pytest.mark.asyncio
async def test_react_demo_runs_with_official_model_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: _answer_stream())) as client:
        monkeypatch.setattr(react_agent_demo, "OpenAICompatibleClient", lambda **kwargs:
            OpenAICompatibleClient(
                base_url=kwargs["base_url"], api_key=kwargs["api_key"], client=client,
            ))
        agent, (invoker, context) = react_agent_demo.build_agent(tmp_path, "test", "deepseek-chat")
        try:
            answer, _ = await agent.invoke(UserMessage(content="hello"), context)
            assert answer.content == "compatible"
        finally:
            await invoker.aclose()
