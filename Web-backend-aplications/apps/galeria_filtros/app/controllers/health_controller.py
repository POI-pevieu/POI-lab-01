from __future__ import annotations

import os

from fastapi import APIRouter

from ..schemas import HealthResposta

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResposta)
def health() -> dict:
    return {"status": "ok", "app": "galeria_filtros", "pid": os.getpid()}
