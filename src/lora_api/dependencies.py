"""FastAPI dependency adapters for the application container."""

from pathlib import Path

from fastapi import Request

from .container import ApiContext


def get_api_context(request: Request) -> ApiContext:
    context = getattr(request.app.state, "api_context", None)
    if not isinstance(context, ApiContext):
        context = ApiContext(workspace_root=str(Path.cwd()))
        request.app.state.api_context = context
    return context


__all__ = ["ApiContext", "get_api_context"]
