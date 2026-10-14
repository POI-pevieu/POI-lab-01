from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import get_health_service
from ..services.health_service import HealthService

router = APIRouter(tags=["health"])


@router.get("/health")
def health(svc: HealthService = Depends(get_health_service)):
    return svc.status()
