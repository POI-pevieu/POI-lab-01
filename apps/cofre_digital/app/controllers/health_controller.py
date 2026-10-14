from __future__ import annotations

import os

from fastapi import APIRouter

from app.config import settings
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health():
    return {"status": "ok", "pid": os.getpid(), "app": settings.app_name}
