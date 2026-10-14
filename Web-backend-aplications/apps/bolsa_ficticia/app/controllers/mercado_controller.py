from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import get_mercado_service
from ..schemas import Tick
from ..services.mercado_service import MercadoService

router = APIRouter(tags=["mercado"])


@router.post("/mercado/tick")
def tick_mercado(body: Tick | None = None, svc: MercadoService = Depends(get_mercado_service)):
    return svc.tick(body.passos if body else 1)
