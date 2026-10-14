"""Providers de dependencias (usados via `Depends` nos controllers).

Ha UMA instancia do repositorio (cache LRU) por processo, compartilhada por
todos os services e threads do worker. Cada worker gunicorn cria a sua.
"""

from __future__ import annotations

from functools import lru_cache

from .config import get_settings
from .repositories.imagem_cache_repository import ImagemCacheRepository
from .services.cache_service import CacheService
from .services.filtro_service import FiltroService
from .services.imagem_service import ImagemService
from .services.mosaico_service import MosaicoService


@lru_cache(maxsize=1)
def get_cache_repository() -> ImagemCacheRepository:
    s = get_settings()
    return ImagemCacheRepository(max_bytes=s.cache_max_bytes, max_items=s.cache_max_itens)


@lru_cache(maxsize=1)
def get_imagem_service() -> ImagemService:
    return ImagemService(get_cache_repository())


@lru_cache(maxsize=1)
def get_filtro_service() -> FiltroService:
    return FiltroService(get_cache_repository())


@lru_cache(maxsize=1)
def get_mosaico_service() -> MosaicoService:
    return MosaicoService(get_cache_repository(), get_settings().mosaico_max_bytes)


@lru_cache(maxsize=1)
def get_cache_service() -> CacheService:
    return CacheService(get_cache_repository(), get_settings().modo_capacidade)


def inicializar() -> None:
    """Cria settings, repositorio e services na carga da app (falha cedo se o env for invalido)."""
    get_imagem_service()
    get_filtro_service()
    get_mosaico_service()
    get_cache_service()
