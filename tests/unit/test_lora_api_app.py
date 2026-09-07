from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from lora.tracing import EventStore
from lora.runtime.context_snapshots import ContextSnapshotStore
from lora.runtime.reminders import BootstrapStatus
from lora_api.app import create_app
from lora_api.dependencies import ApiContext
from lora_api.routers.health import health
from lora_api.routers.tool_results import get_tool_result
from lora_api.routers.traces import get_trace_events
from lora_api.services.session_service import SessionService


@pytest.mark.asyncio
async def test_lifespan_closes_runtime_when_application_body_fails(monkeypatch):
    app = create_app(workspace_root=".")
    runtime = AsyncMock()
    app.state.api_context._runtime_service = runtime
    close = AsyncMock()
    monkeypatch.setattr(ApiContext, "aclose", close)
    with pytest.raises(RuntimeError, match="application failed"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("application failed")
    close.assert_awaited_once()


def test_create_app_allows_desktop_renderer_cors_requests() -> None:
    app = create_app(workspace_root=".")

    middleware_types = [entry.cls for entry in app.user_middleware]

    assert CORSMiddleware in middleware_types


def test_create_app_exposes_runtime_approval_and_task_contracts() -> None:
    schema = create_app(workspace_root=".").openapi()

    assert "post" in schema["paths"]["/chat/approvals/{approval_id}"]
    assert {"get", "delete"} <= set(schema["paths"]["/runtime/tasks/{task_id}"])


@pytest.mark.asyncio
async def test_session_creation_prewarms_without_constructing_runtime(tmp_path) -> None:
    context = ApiContext(
        workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json")
    )
    reminders = context.reminders

    session = SessionService(context.manager, reminders).create_session()
    task = reminders._preparations[session.session_id]
    await task

    assert context._runtime_service is None
    state = reminders.store.load_bootstrap(reminders.scope(session.session_id))
    assert state["status"] == BootstrapStatus.READY.value
    await context.aclose()


def test_health_exposes_backend_instance_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LORA_BACKEND_INSTANCE_ID", "desktop-instance-1")
    response = Response()

    payload = health(response)

    assert payload.status == "ok"
    assert payload.service == "lora-api"
    assert response.headers["X-Lora-Backend-Instance"] == "desktop-instance-1"


@pytest.mark.asyncio
async def test_missing_session_returns_readable_404_after_listing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    app = create_app(workspace_root=str(tmp_path))
    context = app.state.api_context
    ref = context.manager.create("chat", mode="chat")
    origin = "http://127.0.0.1:5173"
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers={"Origin": origin},
    ) as client:
        groups = (await client.get("/sessions/groups")).json()
        assert any(
            session["session_id"] == ref.session_id
            for group in groups["groups"]
            for session in group["sessions"]
        )
        assert (await client.get(f"/sessions/{ref.session_id}")).status_code == 200
        (Path(ref.session_dir) / "session.json").unlink()
        response = await client.get(f"/sessions/{ref.session_id}")

    assert response.status_code == 404
    assert ref.session_id in response.json()["detail"]
    assert response.headers["access-control-allow-origin"] == origin
    assert (Path(ref.session_dir) / "metadata.json").is_file()


@pytest.mark.asyncio
async def test_chat_stream_returns_structured_error_for_stale_session(tmp_path) -> None:
    app = create_app(workspace_root=str(tmp_path))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/chat/stream",
            json={
                "message": "hello",
                "session_id": "missing-session",
                "case_id": "chat",
            },
        )

    assert response.status_code == 200
    assert "lora.transport.error" in response.text
    assert "FileNotFoundError" in response.text
    assert "missing-session" in response.text


def test_get_tool_result_returns_persisted_result(tmp_path) -> None:
    context = ApiContext(
        workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json")
    )
    session = context.manager.create(case_id="chat", mode="chat")
    run = context.manager.start_case_run(
        session.session_id, "chat", run_config=context.config
    )
    store = EventStore(run)
    call_id = store.append(
        "tool.call",
        actor="assistant",
        payload={"tool_name": "glob", "args": {"pattern": "**/*.py"}},
    )
    store.append(
        "tool.result",
        actor="tool",
        payload={
            "tool_call_id": call_id,
            "status": "success",
            "result": "complete output",
        },
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


def test_trace_response_includes_session_context_snapshots(tmp_path) -> None:
    context = ApiContext(
        workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json")
    )
    session = context.manager.create(case_id="chat", mode="chat")
    run = context.manager.start_case_run(
        session.session_id, "chat", run_config=context.config
    )
    ContextSnapshotStore(session.session_dir).save(
        {
            "snapshot_id": "context-1",
            "session_id": session.session_id,
            "case_run_id": run.case_run_id,
            "turn_id": "turn-1",
            "compression_version": 0,
            "projection_revision": 1,
            "system_prompt": "system",
            "messages": [],
        }
    )

    response = get_trace_events(session.session_id, run.case_run_id, context)

    assert [item["snapshot_id"] for item in response.context_snapshots] == ["context-1"]


def test_get_tool_result_accepts_model_tool_call_id(tmp_path) -> None:
    context = ApiContext(
        workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json")
    )
    session = context.manager.create(case_id="chat", mode="chat")
    run = context.manager.start_case_run(
        session.session_id, "chat", run_config=context.config
    )
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
    context = ApiContext(
        workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json")
    )

    with pytest.raises(HTTPException) as exc_info:
        get_tool_result("missing-call", context)

    assert exc_info.value.status_code == 404
    assert "missing-call" in str(exc_info.value.detail)
