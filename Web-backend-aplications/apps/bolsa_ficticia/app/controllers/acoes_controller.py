from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import get_mercado_service
from ..services.mercado_service import MercadoService

router = APIRouter(tags=["acoes"])


@router.get("/acoes")
def listar_acoes(svc: MercadoService = Depends(get_mercado_service)):
    return svc.listar_acoes()
