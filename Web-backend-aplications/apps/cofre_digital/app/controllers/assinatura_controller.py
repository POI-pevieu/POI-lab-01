from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.controllers import http_422
from app.dependencies import get_assinatura_service
from app.exceptions import ParametroInvalidoError
from app.schemas import AssinarRequest, AssinarResponse
from app.services.assinatura_service import AssinaturaService

router = APIRouter(tags=["assinatura"])


@router.post("/assinar", response_model=AssinarResponse)
def assinar(
    req: AssinarRequest = Body(...),
    service: AssinaturaService = Depends(get_assinatura_service),
):
    try:
        return service.assinar(req.texto, req.rounds)
    except ParametroInvalidoError as exc:
        raise http_422(exc) from exc
