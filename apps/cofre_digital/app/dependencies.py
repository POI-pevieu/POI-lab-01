"""``Depends`` providers wiring services into controllers.

Services are stateless, so a single module-level instance per process is shared.
"""
from __future__ import annotations

from app.services.assinatura_service import AssinaturaService
from app.services.matriz_service import MatrizService
from app.services.primos_service import PrimosService

_primos = PrimosService()
_assinatura = AssinaturaService()
_matriz = MatrizService()


def get_primos_service() -> PrimosService:
    return _primos


def get_assinatura_service() -> AssinaturaService:
    return _assinatura


def get_matriz_service() -> MatrizService:
    return _matriz
