"""Providers ``Depends`` dos controllers.

Os services são montados uma vez por worker no ``lifespan`` (``app.state.servicos``).
Os providers são ``async def`` de propósito: só leem um atributo, então rodam
no event loop sem custar um salto extra ao threadpool por dependência; os
handlers continuam ``def`` (IO bloqueante no threadpool).
"""

from __future__ import annotations

from fastapi import Request

from .services import Servicos
from .services.admin_service import AdminService
from .services.estrategia_service import EstrategiaService
from .services.health_service import HealthService
from .services.investidor_service import InvestidorService
from .services.livro_caixa_service import LivroCaixaService
from .services.mercado_service import MercadoService
from .services.ordem_service import OrdemService


def _servicos(request: Request) -> Servicos:
    return request.app.state.servicos


async def get_mercado_service(request: Request) -> MercadoService:
    return _servicos(request).mercado


async def get_investidor_service(request: Request) -> InvestidorService:
    return _servicos(request).investidores


async def get_ordem_service(request: Request) -> OrdemService:
    return _servicos(request).ordens


async def get_estrategia_service(request: Request) -> EstrategiaService:
    return _servicos(request).estrategia


async def get_livro_caixa_service(request: Request) -> LivroCaixaService:
    return _servicos(request).livro_caixa


async def get_admin_service(request: Request) -> AdminService:
    return _servicos(request).admin


async def get_health_service(request: Request) -> HealthService:
    return _servicos(request).health
