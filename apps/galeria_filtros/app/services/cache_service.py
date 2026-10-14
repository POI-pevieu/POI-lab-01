"""Estatisticas do cache/processo e limpeza com devolucao de memoria ao SO."""

from __future__ import annotations

import ctypes
import ctypes.util
import gc
import os
import sys

import psutil

from ..config import MB
from ..repositories.imagem_cache_repository import ImagemCacheRepository


def _carregar_malloc_trim():
    """malloc_trim do glibc (Linux); None em macOS/musl."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        trim = libc.malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        return trim
    except (OSError, AttributeError):  # musl/sem glibc
        return None


_libc_trim = _carregar_malloc_trim()
_proc = psutil.Process()


def rss_bytes() -> int:
    return int(_proc.memory_info().rss)


class CacheService:
    def __init__(self, repo: ImagemCacheRepository, modo_capacidade: str) -> None:
        self.repo = repo
        self.modo_capacidade = modo_capacidade

    def estatisticas(self) -> dict:
        snap = self.repo.snapshot()
        vm = psutil.virtual_memory()
        return {
            **snap,
            "mb": round(snap["bytes"] / MB, 3),
            "capacidade_mb": round(snap["capacidade_bytes"] / MB, 3),
            "modo_capacidade": self.modo_capacidade,
            "pid": os.getpid(),
            "rss_bytes": rss_bytes(),
            "sistema": {"ram_total": vm.total, "ram_disponivel": vm.available, "ram_percent": vm.percent},
        }

    def limpar(self) -> dict:
        """Esvazia o cache, zera contadores, gc.collect() e malloc_trim(0) quando houver glibc."""
        rss_antes = rss_bytes()
        removidos = self.repo.limpar()
        gc.collect()
        trim = None
        if _libc_trim is not None:
            trim = bool(_libc_trim(0))  # devolve heap livre ao SO (glibc); 1 = algo foi liberado
        return {
            "removidos": removidos,
            "pid": os.getpid(),
            "rss_antes": rss_antes,
            "rss_depois": rss_bytes(),
            "malloc_trim": trim if _libc_trim is not None else "indisponivel",
        }
