"""Mosaico das imagens mais recentes do cache (aloca um array grande)."""

from __future__ import annotations

import math
import os
import time

import numpy as np

from ..exceptions import CacheVazio
from ..repositories.imagem_cache_repository import ImagemCacheRepository
from .imagem_service import resumo

QTD_MIN = 1
QTD_MAX = 64


def geometria_mosaico(resolucoes: list[int], max_bytes: int) -> tuple[int, int, int]:
    """(linhas, colunas, lado_do_ladrilho) para k imagens em grade quase quadrada.

    O ladrilho e o menor lado entre as imagens (recorte do canto superior
    esquerdo), reduzido se necessario para que o mosaico caiba em `max_bytes`.
    """
    k = len(resolucoes)
    colunas = math.ceil(math.sqrt(k))
    linhas = math.ceil(k / colunas)
    lado = min(resolucoes)
    limite = int(math.isqrt(max_bytes // (linhas * colunas * 3)))
    return linhas, colunas, max(1, min(lado, limite))


def montar_mosaico(imgs: list[np.ndarray], max_bytes: int) -> np.ndarray:
    linhas, colunas, lado = geometria_mosaico([i.shape[0] for i in imgs], max_bytes)
    out = np.zeros((linhas * lado, colunas * lado, 3), dtype=np.uint8)
    for n, img in enumerate(imgs):
        lin, col = divmod(n, colunas)
        out[lin * lado:(lin + 1) * lado, col * lado:(col + 1) * lado] = img[:lado, :lado]
    return out


class MosaicoService:
    def __init__(self, repo: ImagemCacheRepository, max_bytes: int) -> None:
        self.repo = repo
        self.max_bytes = max_bytes

    def montar(self, quantidade: int) -> dict:
        """Mosaico das `quantidade` imagens mais recentes. Levanta CacheVazio."""
        itens = self.repo.mais_recentes(quantidade)
        if not itens:
            raise CacheVazio()
        t0 = time.perf_counter()
        ids = [k for k, _ in itens]
        arr = montar_mosaico([v for _, v in itens], self.max_bytes)
        r = resumo(arr)
        return {
            "quantidade_pedida": quantidade,
            "quantidade_usada": len(ids),
            "ids": ids,
            **r,
            "mosaico_ms": round((time.perf_counter() - t0) * 1000, 3),
            "pid": os.getpid(),
        }
