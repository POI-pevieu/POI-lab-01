from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import get_admin_service
from ..schemas import NovoPreco, para_centavos
from ..services.admin_service import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/precos")
def admin_definir_preco(body: NovoPreco, svc: AdminService = Depends(get_admin_service)):
    """Força o preço de uma ação (para testes/experimentos determinísticos)."""
    return svc.definir_preco(body.ticker.upper(), para_centavos(body.preco))


@router.post("/reset")
def admin_reset(svc: AdminService = Depends(get_admin_service)):
    """Zera banco e livro-caixa (repetibilidade entre repetições do experimento)."""
    return svc.reset()
