from __future__ import annotations

from contextlib import asynccontextmanager
from importlib.metadata import version as package_version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lora_api.container import ApiContext
from lora_api.routers import (
    automations,
    chat,
    health,
    projects,
    runtime,
    sessions,
    settings,
    terminal,
    tool_results,
    traces,
    workspace,
)
from lora_api.services.chat_runner import ChatRunRegistry


def create_app(
    *,
    workspace_root: str | None = None,
    agent_alias: str | None = None,
    max_steps: int | None = None,
) -> FastAPI:
    context = ApiContext(
        workspace_root=workspace_root,
        agent_alias=agent_alias,
        max_steps=max_steps,
    )
    context.attach_chat_registry(ChatRunRegistry(context.session_coordinator))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        lease = None
        try:
            lease = await context.acquire_runtime()
            await lease.release()
            lease = None
            context.start_automation_scheduler()
            yield
        finally:
            if lease is not None:
                await lease.release()
            await context.aclose()

    app = FastAPI(
        title="Lora Local API", version=package_version("lora"), lifespan=lifespan
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.api_context = context
    app.include_router(health.router)
    app.include_router(automations.router)
    app.include_router(projects.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(runtime.router)
    app.include_router(tool_results.router)
    app.include_router(traces.router)
    app.include_router(settings.router)
    app.include_router(workspace.router)
    app.include_router(terminal.router)
    return app
