"""Traducao das excecoes de dominio para HTTPException."""

from __future__ import annotations

from fastapi import HTTPException

from ..exceptions import (
    CacheVazio,
    ErroGaleria,
    ImagemMaiorQueCache,
    ImagemNaoEncontrada,
    ParametroInvalido,
)

STATUS_POR_EXCECAO: dict[type[ErroGaleria], int] = {
    ImagemNaoEncontrada: 404,
    CacheVazio: 409,
    ParametroInvalido: 422,
    ImagemMaiorQueCache: 507,
}


def http_erro(e: ErroGaleria) -> HTTPException:
    for tipo, status in STATUS_POR_EXCECAO.items():
        if isinstance(e, tipo):
            return HTTPException(status_code=status, detail=str(e))
    return HTTPException(status_code=500, detail=str(e))
