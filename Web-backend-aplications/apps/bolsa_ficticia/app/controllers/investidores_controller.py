from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..dependencies import get_estrategia_service, get_investidor_service
from ..schemas import NovoInvestidor, para_centavos
from ..services.estrategia_service import EstrategiaService
from ..services.investidor_service import InvestidorService

router = APIRouter(tags=["investidores"])


@router.post("/investidores", status_code=201)
def criar_investidor(body: NovoInvestidor, svc: InvestidorService = Depends(get_investidor_service)):
    return svc.criar(body.nome, para_centavos(body.saldo_inicial))


@router.get("/investidores/{investidor_id}")
def obter_investidor(investidor_id: int, svc: InvestidorService = Depends(get_investidor_service)):
    return svc.obter(investidor_id)


@router.get("/investidores/{investidor_id}/carteira")
def obter_carteira(investidor_id: int, svc: InvestidorService = Depends(get_investidor_service)):
    return svc.carteira(investidor_id)


@router.post("/investidores/{investidor_id}/operar")
def operar(investidor_id: int, ticker: str | None = Query(default=None, max_length=10),
           svc: EstrategiaService = Depends(get_estrategia_service)):
    return svc.operar(investidor_id, ticker.upper() if ticker else None)
