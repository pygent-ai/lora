from __future__ import annotations

from fastapi.middleware.cors import CORSMiddleware

from lora_api.app import create_app


def test_create_app_allows_desktop_renderer_cors_requests() -> None:
    app = create_app(workspace_root=".")

    middleware_types = [entry.cls for entry in app.user_middleware]

    assert CORSMiddleware in middleware_types
