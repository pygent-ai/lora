from __future__ import annotations

import os

from fastapi import APIRouter, Response

from lora_api.models.responses import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    instance_id = os.environ.get("LORA_BACKEND_INSTANCE_ID")
    if instance_id:
        response.headers["X-Lora-Backend-Instance"] = instance_id
    return HealthResponse(status="ok", service="lora-api")
