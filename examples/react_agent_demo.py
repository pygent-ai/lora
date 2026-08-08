r"""Pygent 0.2.3 ReAct demo using an OpenAI-compatible DeepSeek endpoint.

Run from the repository root::

    .\.venv\Scripts\python.exe examples\react_agent_demo.py

The example demonstrates the 0.2 API: immutable Context values, a stateless
Module graph, @tool/ToolKit declarations, explicit authorization, one
ModelCallLayer, one ReActLayer, and ExecutionEvent streaming.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from pygent import (
    AIMessage,
    Context,
    FallbackPolicy,
    GenerationConfig,
    ModelCallLayer,
    ModelGroupConfig,
    ModelRoute,
    Module,
    ReActLayer,
    RetryPolicy,
    ToolAuthorizationDecision,
    ToolAuthorizationRequest,
    UserMessage,
)
from pygent.llm import (
    DefaultModelInvoker,
    ModelEventKind,
    ModelProviderCapabilities,
    OpenAICompatibleAdapter,
    OpenAICompatibleClient,
)
from pygent.tool import StandardTools

TOOL_PERMISSIONS = frozenset({"filesystem:read", "filesystem:write", "shell:execute"})


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class DemoAuthorization(Module[ToolAuthorizationRequest, ToolAuthorizationDecision]):
    async def forward(
        self, request: ToolAuthorizationRequest, context: Context
    ) -> tuple[ToolAuthorizationDecision, Context]:
        allowed = set(request.spec.required_permissions) <= TOOL_PERMISSIONS
        return (
            ToolAuthorizationDecision(
                call_id=request.call.call_id,
                allowed=allowed,
                reason_code="allowed" if allowed else "missing_permission",
            ),
            context,
        )


class DeepSeekAgent(Module[UserMessage, AIMessage]):
    def __init__(self, react: ReActLayer) -> None:
        super().__init__()
        self.react = react

    async def forward(
        self, message: UserMessage, context: Context
    ) -> tuple[AIMessage, Context]:
        return await self.react(message, context)


def build_agent(workspace_root: Path, api_key: str, model_name: str) -> tuple[DeepSeekAgent, object]:
    toolkit = StandardTools(workspace_root=workspace_root).toolkit
    invoker = DefaultModelInvoker(
        adapters={"openai": OpenAICompatibleAdapter()},
        clients={
            "primary": OpenAICompatibleClient(
                base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                api_key=api_key,
            )
        },
        capabilities={"primary": ModelProviderCapabilities(streaming=True)},
    )
    model = ModelCallLayer(
        model_group=ModelGroupConfig(
            name="deepseek-demo",
            routes=(ModelRoute("primary", "openai", model_name),),
            fallback=FallbackPolicy(("primary",)),
            max_concurrency=2,
        ),
        retry_policy=RetryPolicy(attempt_timeout_seconds=60.0),
        generation=GenerationConfig(temperature=0.1, max_output_tokens=1000, tool_choice="auto"),
        tools=toolkit.definitions,
        invoker=invoker,
    )
    tool_layer = toolkit.local_layer(authorization=DemoAuthorization(), max_concurrency=3)
    agent = DeepSeekAgent(
        ReActLayer(
            model=model,
            tools=tool_layer,
            max_steps=8,
            max_model_calls=8,
            max_tool_calls=12,
        )
    )
    context = toolkit.make_visible_in(
        Context(
            system_prompt="You are a concise ReAct agent. Use tools when helpful, then answer in Chinese.",
            metadata={"permissions": sorted(TOOL_PERMISSIONS)},
        )
    )
    return agent, (invoker, context)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Pygent 0.2.3 ReAct demo")
    parser.add_argument(
        "prompt",
        nargs="?",
        default=(
            "请列出 examples 目录，读取 examples/react_agent_demo.py 的开头，"
            "再计算 2+40，最后用一句话总结。"
        ),
    )
    parser.add_argument("--model", default="deepseek-chat")
    args = parser.parse_args()

    workspace_root = Path(__file__).resolve().parents[1]
    _load_env_file(workspace_root / ".env")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is missing; configure it in .env first")

    agent, resources = build_agent(workspace_root, api_key, args.model)
    invoker, context = resources
    try:
        async with agent.stream(UserMessage(content=args.prompt), context) as stream:
            async for event in stream:
                if event.kind == ModelEventKind.TEXT_DELTA.value:
                    print(event.data.get("text", ""), end="", flush=True)
                elif event.kind == "tool.completed":
                    print("\n[tool completed]\n", end="", flush=True)
            answer, next_context = await stream.final_result()
        print(f"\n\nfinal: {answer.content}")
        print(f"context messages: {len(next_context.messages)}")
    finally:
        await invoker.aclose()


if __name__ == "__main__":
    asyncio.run(main())
