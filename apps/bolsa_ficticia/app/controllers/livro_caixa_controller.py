from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import get_livro_caixa_service
from ..services.livro_caixa_service import LivroCaixaService

router = APIRouter(tags=["livro-caixa"])


@router.get("/livro-caixa/stats")
def livro_caixa_stats(svc: LivroCaixaService = Depends(get_livro_caixa_service)):
    return svc.estatisticas()
