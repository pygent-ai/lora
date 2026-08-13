from __future__ import annotations

from pygent import IdempotencyPolicy, ToolDefinition, ToolSideEffect
from pygent.tool import ToolSpec

from lora.schema import RunConfig


def _delegation_spec(name: str, description: str) -> ToolSpec:
    return ToolSpec(
        tool_id=f"lora.agent.{name}",
        version="1",
        definition=ToolDefinition(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Configured Lora agent alias allowed for delegation."},
                    "task": {"type": "string", "minLength": 1, "description": "Complete task for the delegated agent to perform."},
                },
                "required": ["agent", "task"],
                "additionalProperties": False,
            },
        ),
        side_effect=ToolSideEffect.EXTERNAL,
        idempotency=IdempotencyPolicy.REQUIRES_KEY,
        resource_key="lora-agent",
        sandbox_profile="agent",
    )


DELEGATE_TOOL_SPEC = _delegation_spec("delegate", "Run a task synchronously with an allowed Lora agent.")
DELEGATE_BACKGROUND_TOOL_SPEC = _delegation_spec(
    "delegate_background", "Start a durable background task with an allowed Lora agent."
)


def visible_delegation_specs(config: RunConfig) -> tuple[ToolSpec, ...]:
    if not config.delegation.allowed_agents:
        return ()
    if config.delegation.background_enabled:
        return DELEGATE_TOOL_SPEC, DELEGATE_BACKGROUND_TOOL_SPEC
    return (DELEGATE_TOOL_SPEC,)


__all__ = ["DELEGATE_BACKGROUND_TOOL_SPEC", "DELEGATE_TOOL_SPEC", "visible_delegation_specs"]
