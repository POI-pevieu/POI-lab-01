"""Domain exceptions raised by services and translated to HTTP by controllers."""
from __future__ import annotations


class CofreError(Exception):
    """Base class for Cofre Digital domain errors."""


class ParametroInvalidoError(CofreError, ValueError):
    """An input is outside the domain accepted by a service (-> HTTP 422).

    In normal operation FastAPI/Pydantic reject out-of-range input first (same
    bounds, from ``config``); services re-check so they are safe to call
    directly (unit tests, scripts).
    """

    def __init__(self, campo: str, mensagem: str):
        self.campo = campo
        self.mensagem = mensagem
        super().__init__(f"{campo}: {mensagem}")
