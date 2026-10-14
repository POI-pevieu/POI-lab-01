"""Single place for Cofre Digital settings.

Validation bounds are fixed constants (not env-driven) so that every deployment
exposes exactly the same API surface; requests outside them get HTTP 422.
Server/runtime env vars (HOST, PORT, WORKERS, TIMEOUT, LOG_LEVEL) are read by
``gunicorn.conf.py``.  Note: the ``COFRE_*`` env vars belong to the locustfile,
not to the app.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "cofre_digital"
    title: str = "Cofre Digital"
    description: str = (
        "Aplicacao CPU-bound: primos, assinatura SHA-256 iterada e cifra por matrizes."
    )
    version: str = "1.0.0"

    primos_n_min: int = 2
    primos_n_max: int = 5_000_000

    rounds_min: int = 1
    rounds_max: int = 5_000_000
    texto_max_len: int = 10_000

    matriz_min: int = 2
    matriz_max: int = 400
    seed_max: int = 2**63 - 1
    mensagem_max_len: int = 1_000
    mensagem_padrao: str = "cofre digital"


settings = Settings()
