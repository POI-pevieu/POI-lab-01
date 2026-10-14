from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..dependencies import get_mosaico_service
from ..exceptions import ErroGaleria
from ..schemas import MosaicoResposta
from ..services.mosaico_service import QTD_MAX, QTD_MIN, MosaicoService
from .erros import http_erro

router = APIRouter(tags=["mosaico"])


@router.get("/mosaico", response_model=MosaicoResposta, responses={409: {"description": "cache vazio"}})
def mosaico(
    quantidade: int = Query(4, ge=QTD_MIN, le=QTD_MAX),
    service: MosaicoService = Depends(get_mosaico_service),
) -> dict:
    try:
        return service.montar(quantidade)
    except ErroGaleria as e:
        raise http_erro(e) from e
