from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..dependencies import get_ordem_service
from ..schemas import NovaOrdem
from ..services.ordem_service import OrdemService

router = APIRouter(tags=["ordens"])


@router.post("/ordens", status_code=201)
def criar_ordem(body: NovaOrdem, svc: OrdemService = Depends(get_ordem_service)):
    return svc.executar(body.investidor_id, body.ticker.upper(), body.tipo, body.quantidade, "manual")


@router.get("/ordens")
def listar_ordens(investidor_id: int | None = None, limit: int = Query(default=50, ge=1, le=500),
                  svc: OrdemService = Depends(get_ordem_service)):
    return {"ordens": svc.listar(investidor_id, limit)}
