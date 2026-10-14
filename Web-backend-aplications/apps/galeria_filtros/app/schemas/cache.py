"""Modelos de resposta de /cache."""

from typing import List, Optional, Union

from pydantic import BaseModel


class MemoriaSistema(BaseModel):
    ram_total: int
    ram_disponivel: int
    ram_percent: float


class CacheStatsResposta(BaseModel):
    bytes: int
    itens: int
    capacidade_bytes: int
    capacidade_itens: Optional[int]
    hits: int
    misses: int
    hit_ratio: Optional[float]
    evictions: int
    evicted_bytes: int
    inserts: int
    ids_lru: List[str]  # do menos ao mais recentemente usado
    ids_truncado: bool
    mb: float
    capacidade_mb: float
    modo_capacidade: str
    pid: int
    rss_bytes: int
    sistema: MemoriaSistema


class CacheLimpezaResposta(BaseModel):
    removidos: int
    pid: int
    rss_antes: int
    rss_depois: int
    malloc_trim: Union[bool, None, str]  # "indisponivel" fora do Linux/glibc
