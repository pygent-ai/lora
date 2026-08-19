from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pygent import (
    Context,
    ToolAuthorizationRequest,
    ToolCall,
    ToolDefinition,
    ToolSpec,
    thaw_json,
)
from pygent.tool import ToolSideEffect

from lora.config import load_run_config
from lora.runtime.agent import LoraToolAuthorization
from lora.runtime.service import LoraRuntimeService


@pytest.mark.asyncio
async def test_approval_waiter_exists_when_request_event_is_published(tmp_path: Path) -> None:
    with patch("lora.config.loader.Path.home", return_value=tmp_path / "home"):
        service = LoraRuntimeService(load_run_config(workspace_root=tmp_path))
    await service.initialize()
    try:
        authorization = LoraToolAuthorization(
            enabled=True,
            timeout_seconds=5,
            preauthorized_tools=(),
            interactive=True,
            scope_key="case-1",
        )
        spec = ToolSpec(
            "write-tool",
            "1",
            ToolDefinition("write", "Write a file", {"type": "object"}),
            side_effect=ToolSideEffect.WRITE,
        )
        request = ToolAuthorizationRequest(
            call=ToolCall(
                call_id="call-1",
                name="write",
                arguments={},
                tool_id=spec.tool_id,
                tool_version=spec.version,
            ),
            spec=spec,
        )
        handle = await service.binding.bind(authorization).start(request, Context())

        async with handle.subscribe() as events:
            async for event in events:
                if event.kind == "lora.approval.requested":
                    approval_id = str(thaw_json(event.data)["approval_id"])
                    assert await service.deliver_approval(
                        approval_id,
                        approved=True,
                        comment="approved immediately",
                    )

        decision, _ = await handle.result()
        assert decision.allowed is True
    finally:
        await service.close()
