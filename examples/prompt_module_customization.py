r"""
Prompt module customization demo.

Run from the repository root:

    .\.venv\Scripts\python.exe examples\prompt_module_customization.py

This example shows two common operations:

1. Replace an existing prompt module, such as ``tool.available``.
2. Add a new dynamic prompt module that is injected only before a model request.

The actual prompt bodies are intentionally small placeholders. Replace the
render functions with your real prompt text.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from lora.agent import LoraAgent, PromptModule, PromptRegistry, PromptRenderContext
from lora.runtime import AgentRuntimeAdapter
from lora.schema import RunConfig
from lora.session import SessionManager


def render_custom_available_tools(ctx: PromptRenderContext) -> str:
    """Replace the built-in dynamic ``tool.available`` module."""
    tools = ", ".join(ctx.tool_names) if ctx.tool_names else "none"
    return (
        "Available tools for this request:\n"
        f"- {tools}\n"
        "\n"
        "TODO(prompt): Fill in your tool-selection rules here."
    )


def render_request_guard(ctx: PromptRenderContext) -> str:
    """Add a brand-new dynamic module injected before each model request."""
    return (
        "Request guard:\n"
        f"- request_type: {ctx.request_type}\n"
        f"- turn_id: {ctx.turn_id or 'unknown'}\n"
        "\n"
        "TODO(prompt): Fill in request-level dynamic guidance here."
    )


def build_prompt_registry() -> PromptRegistry:
    registry = PromptRegistry()

    # 1) Modify an existing module by replacing it with the same id.
    # Because this module is dynamic, it is not stored in static_prompt.txt.
    registry.replace(
        PromptModule(
            id="tool.available",
            phase="dynamic",
            type="tool",
            cache_scope="turn",
            order=120,
            render=render_custom_available_tools,
            required=True,
        )
    )

    # 2) Add a new dynamic module. The default PromptInjectionPolicy injects
    # dynamic modules for agent_turn and case_run requests.
    registry.register(
        PromptModule(
            id="runtime.request_guard",
            phase="dynamic",
            type="runtime",
            cache_scope="request",
            order=125,
            render=render_request_guard,
        )
    )

    return registry


async def run_demo() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        workspace_root = Path(tmp)
        config = RunConfig(workspace_root=workspace_root, lora_root=workspace_root / ".lora")
        manager = SessionManager(config)
        session_ref = manager.create("prompt-module-demo", mode="chat")
        run_ref = manager.start_case_run(session_ref.session_id, "chat")
        session = manager.load(session_ref.session_id)

        agent = LoraAgent(config, prompt_registry=build_prompt_registry())
        result = await AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
            session=session,
            user_input="Show me the customized prompt modules.",
            case_run_ref=run_ref,
            turn_id="turn-0001",
        )

        events = _read_jsonl(Path(run_ref.run_dir) / "events.jsonl")
        prompt_event = next(event for event in events if event["type"] == "prompt.rendered")
        prompt_text_path = Path(prompt_event["payload"]["prompt_text_path"])
        static_prompt_path = Path(session_ref.session_dir) / "context" / "prompts" / "static_prompt.txt"

        return {
            "status": result["status"],
            "final_answer": result["final_answer"],
            "static_prompt_path": str(static_prompt_path),
            "rendered_prompt_path": str(prompt_text_path),
            "static_module_ids": prompt_event["payload"]["static_module_ids"],
            "dynamic_module_ids": prompt_event["payload"]["dynamic_module_ids"],
            "custom_dynamic_module_present": "runtime.request_guard"
            in prompt_event["payload"]["dynamic_module_ids"],
            "custom_tool_module_present": "tool.available" in prompt_event["payload"]["dynamic_module_ids"],
        }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_demo()), ensure_ascii=False, indent=2, sort_keys=True))
