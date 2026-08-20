from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from pygent import AIMessage, ToolCall, ToolMessage, UserMessage
from pygent.llm import ModelExecution, ModelProviderResponse

from lora.config import load_run_config
from lora.runtime.service import LoraRuntimeService
from lora.sessions import SessionManager


class ScriptedRecoveryInvoker:
    def validate_route(self, _route: Any) -> None:
        return None

    def execute(self, *, message: Any, **_kwargs: Any) -> ModelExecution:
        async def invoke(_emit: Any) -> ModelProviderResponse:
            if (
                isinstance(message, UserMessage)
                and "create a detailed summary" in message.content
            ):
                answer = AIMessage(
                    content=(
                        "<analysis>recovery compression</analysis>"
                        "<summary>Durable recovery summary.</summary>"
                    )
                )
            elif isinstance(message, ToolMessage):
                answer = AIMessage(content="recovered final answer")
            else:
                answer = AIMessage(
                    content="writing recovery output",
                    tool_calls=(
                        ToolCall(
                            call_id="recovery-write-1",
                            name="write",
                            arguments={
                                "file_path": "recovery-output.txt",
                                "content": "durable tool output",
                            },
                        ),
                    ),
                )
            return ModelProviderResponse(message=answer, usage={})

        return ModelExecution(invoke)

    async def aclose(self) -> None:
        return None


def recovery_service(workspace: Path) -> tuple[Any, LoraRuntimeService]:
    config = load_run_config(workspace_root=workspace)
    config.eternal_conversation.enabled = False
    config.runtime_approvals.enabled = False
    assert config.resolved_agent is not None
    for route in config.resolved_agent.routes:
        route.api_key = "recovery-test"
        route.api_key_source = "test"
    service = LoraRuntimeService(config)
    invoker = ScriptedRecoveryInvoker()
    service._model_invokers[config.resolved_agent.alias] = invoker
    for agent in service._agent_definitions.values():
        agent.llm = invoker
    service.runtime._recovery_lease_ttl = 0.25
    return config, service


async def crash_at_boundary(
    workspace: Path,
    boundary: str,
    execution_path: Path,
    reached_path: Path,
    force_compression: bool,
) -> None:
    config, service = recovery_service(workspace)
    manager = SessionManager(config)
    session_ref = manager.create(case_id="recovery", mode="chat")
    if force_compression:
        config.context_window = 10
        config.context_compression_trigger_ratio = 0.5
        session = manager.load(session_ref.session_id)
        session.token_usage = {"context_tokens": 10}
        manager.save(session)
    run_ref = manager.start_case_run(
        session_ref.session_id,
        "recovery",
        run_config=config,
    )
    from lora.runtime.agent import core as core_module
    from lora.runtime.agent import pipeline as pipeline_module

    original = pipeline_module.checkpoint_conversation_message

    async def crashing_checkpoint(*args: Any, **kwargs: Any) -> None:
        await original(*args, **kwargs)
        message = args[2]
        reached = message.role == boundary
        if boundary == "assistant":
            reached = reached and bool(message.tool_calls)
        if reached:
            reached_path.write_text(boundary, encoding="utf-8")
            os._exit(91)

    pipeline_module.checkpoint_conversation_message = crashing_checkpoint
    core_module.checkpoint_conversation_message = crashing_checkpoint
    handle = await service.start_turn(
        manager=manager,
        message="recover this turn",
        run_ref=run_ref,
        turn_id="turn-recovery",
        interactive_approvals=True,
        deadline=asyncio.get_running_loop().time() + 60,
    )
    execution_path.write_text(handle.execution_id, encoding="utf-8")
    await handle.result()
    raise RuntimeError(f"execution completed before {boundary!r} checkpoint crash")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("boundary", choices=("user", "assistant", "tool"))
    parser.add_argument("execution_path", type=Path)
    parser.add_argument("reached_path", type=Path)
    parser.add_argument("compression", choices=("on", "off"))
    args = parser.parse_args()
    asyncio.run(
        crash_at_boundary(
            args.workspace,
            args.boundary,
            args.execution_path,
            args.reached_path,
            args.compression == "on",
        )
    )


if __name__ == "__main__":
    main()
