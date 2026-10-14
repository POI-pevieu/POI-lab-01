"""POST /imagens/{id} e GET /imagens/{id}/filtro.

Handlers sao `def` (threadpool do AnyIO): a geracao/filtragem numpy libera o GIL
e o event loop nao vira gargalo artificial.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Response

from ..dependencies import get_filtro_service, get_imagem_service
from ..exceptions import ErroGaleria
from ..schemas import FiltroResposta, FormatoSaida, ImagemCriadaResposta, TipoFiltro
from ..services.filtro_service import FiltroService
from ..services.imagem_service import RES_MAX, RES_MIN, ImagemService
from .erros import http_erro

router = APIRouter(prefix="/imagens", tags=["imagens"])

ID_PATH = Path(..., pattern=r"^[A-Za-z0-9_-]{1,64}$", description="identificador da imagem")


@router.post("/{id}", response_model=ImagemCriadaResposta)
def criar_imagem(
    id: str = ID_PATH,
    resolucao: int = Query(1000, ge=RES_MIN, le=RES_MAX),
    service: ImagemService = Depends(get_imagem_service),
) -> dict:
    try:
        return service.criar(id, resolucao)
    except ErroGaleria as e:
        raise http_erro(e) from e


@router.get(
    "/{id}/filtro",
    response_model=FiltroResposta,
    responses={200: {"content": {"image/png": {}}}, 404: {"description": "miss no cache"}},
)
def filtrar_imagem(
    id: str = ID_PATH,
    tipo: TipoFiltro = Query(...),
    formato: FormatoSaida = Query("resumo"),
    service: FiltroService = Depends(get_filtro_service),
):
    try:
        if formato == "png":
            return Response(content=service.filtrar_png(id, tipo), media_type="image/png")
        return service.filtrar(id, tipo)
    except ErroGaleria as e:
        raise http_erro(e) from e
