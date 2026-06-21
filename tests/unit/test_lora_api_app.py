from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware

from lora.tracing import EventStore
from lora_api.app import create_app
from lora_api.dependencies import ApiContext
from lora_api.routers.tool_results import get_tool_result


def test_create_app_allows_desktop_renderer_cors_requests() -> None:
    app = create_app(workspace_root=".")

    middleware_types = [entry.cls for entry in app.user_middleware]

    assert CORSMiddleware in middleware_types


def test_get_tool_result_returns_persisted_result(tmp_path) -> None:
    context = ApiContext(workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json"))
    session = context.manager.create(case_id="chat", mode="chat")
    run = context.manager.start_case_run(session.session_id, "chat", run_config=context.config)
    store = EventStore(run)
    call_id = store.append(
        "tool.call",
        actor="assistant",
        payload={"tool_name": "glob", "args": {"pattern": "**/*.py"}},
    )
    store.append(
        "tool.result",
        actor="tool",
        payload={"tool_call_id": call_id, "status": "success", "result": "complete output"},
    )

    response = get_tool_result(call_id, context)

    assert response.model_dump() == {
        "tool_call_id": call_id,
        "tool_name": "glob",
        "status": "success",
        "result": "complete output",
        "error": None,
        "result_size": len("complete output"),
        "created_at": response.created_at,
    }


def test_get_tool_result_accepts_model_tool_call_id(tmp_path) -> None:
    context = ApiContext(workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json"))
    session = context.manager.create(case_id="chat", mode="chat")
    run = context.manager.start_case_run(session.session_id, "chat", run_config=context.config)
    store = EventStore(run)
    trace_call_id = store.append(
        "tool.call",
        actor="assistant",
        payload={
            "tool_name": "bash",
            "args": {"command": "echo hi"},
            "model_tool_call_id": "call_model_bash",
        },
    )
    store.append(
        "tool.result",
        actor="tool",
        payload={
            "tool_call_id": trace_call_id,
            "model_tool_call_id": "call_model_bash",
            "status": "success",
            "result": "hi",
        },
    )

    response = get_tool_result("call_model_bash", context)

    assert response.tool_call_id == "call_model_bash"
    assert response.tool_name == "bash"
    assert response.status == "success"
    assert response.result == "hi"


def test_get_tool_result_returns_404_for_missing_id(tmp_path) -> None:
    context = ApiContext(workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json"))

    with pytest.raises(HTTPException) as exc_info:
        get_tool_result("missing-call", context)

    assert exc_info.value.status_code == 404
    assert "missing-call" in str(exc_info.value.detail)
