from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from app.config import settings
from app.controllers import http_422
from app.dependencies import get_primos_service
from app.exceptions import ParametroInvalidoError
from app.schemas import PrimosResponse
from app.services.primos_service import PrimosService

router = APIRouter(tags=["primos"])


@router.get("/primos/{n}", response_model=PrimosResponse)
def primos(
    n: int = Path(..., ge=settings.primos_n_min, le=settings.primos_n_max),
    service: PrimosService = Depends(get_primos_service),
):
    try:
        return service.calcular(n)
    except ParametroInvalidoError as exc:
        raise http_422(exc) from exc
