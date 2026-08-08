from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lora_api.dependencies import ApiContext
from lora_api.routers import chat, health, projects, runtime, sessions, settings, tool_results, traces


def create_app(
    *,
    workspace_root: str | None = None,
    config_path: str | None = None,
    agent_alias: str | None = None,
    max_steps: int | None = None,
) -> FastAPI:
    context = ApiContext(
        workspace_root=workspace_root,
        config_path=config_path,
        agent_alias=agent_alias,
        max_steps=max_steps,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await context.runtime_service.initialize()
        yield
        await context.aclose()

    app = FastAPI(title="Lora Local API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.api_context = context
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(runtime.router)
    app.include_router(tool_results.router)
    app.include_router(traces.router)
    app.include_router(settings.router)
    return app
