from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import get_cache_service
from ..schemas import CacheLimpezaResposta, CacheStatsResposta
from ..services.cache_service import CacheService

router = APIRouter(prefix="/cache", tags=["cache"])


@router.get("/stats", response_model=CacheStatsResposta)
def cache_stats(service: CacheService = Depends(get_cache_service)) -> dict:
    return service.estatisticas()


@router.delete("", response_model=CacheLimpezaResposta)
def limpar_cache(service: CacheService = Depends(get_cache_service)) -> dict:
    return service.limpar()
