from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.config import settings
from app.controllers import http_422
from app.dependencies import get_matriz_service
from app.exceptions import ParametroInvalidoError
from app.schemas import CifrarMatrizResponse
from app.services.matriz_service import MatrizService

router = APIRouter(tags=["matriz"])


@router.get("/cifrar-matriz/{tamanho}", response_model=CifrarMatrizResponse)
def cifrar_matriz(
    tamanho: int = Path(..., ge=settings.matriz_min, le=settings.matriz_max),
    seed: int | None = Query(None, ge=0, le=settings.seed_max),
    mensagem: str = Query(
        settings.mensagem_padrao, min_length=1, max_length=settings.mensagem_max_len
    ),
    service: MatrizService = Depends(get_matriz_service),
):
    try:
        return service.cifrar(tamanho, seed, mensagem)
    except ParametroInvalidoError as exc:
        raise http_422(exc) from exc
