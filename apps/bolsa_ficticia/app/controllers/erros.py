"""Tradução de exceções de domínio/infra para respostas HTTP ``{"detail": ...}``."""

from __future__ import annotations

import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..exceptions import (AcaoNaoEncontrada, ErroDominio, InvestidorNaoEncontrado, OrdemInvalida,
                          PosicaoInsuficiente, SaldoInsuficiente)

STATUS_POR_ERRO: dict[type[ErroDominio], int] = {
    InvestidorNaoEncontrado: 404,
    AcaoNaoEncontrada: 404,
    SaldoInsuficiente: 409,
    PosicaoInsuficiente: 409,
    OrdemInvalida: 400,
}


def status_de(exc: ErroDominio) -> int:
    for tipo in type(exc).__mro__:
        if tipo in STATUS_POR_ERRO:
            return STATUS_POR_ERRO[tipo]
    return 400


def _erro_dominio(_: Request, exc: ErroDominio):
    return JSONResponse(status_code=status_de(exc), content={"detail": str(exc)})


def _erro_sqlite(_: Request, exc: sqlite3.OperationalError):
    # "database is locked" após SQLITE_BUSY_TIMEOUT_MS: sobrecarga, não bug.
    status = 503 if "locked" in str(exc) or "busy" in str(exc) else 500
    return JSONResponse(status_code=status, content={"detail": f"sqlite: {exc}"})


def registrar_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ErroDominio, _erro_dominio)
    app.add_exception_handler(sqlite3.OperationalError, _erro_sqlite)
