"""Geracao de imagens sinteticas deterministicas e resumo de arrays.

As funcoes puras daqui sao importadas tambem pelos testes para recalcular, de
forma independente, os resultados esperados (mesma semente => mesma imagem).
"""

from __future__ import annotations

import os
import time
import zlib

import numpy as np

from ..exceptions import ResolucaoInvalida
from ..repositories.imagem_cache_repository import ImagemCacheRepository

RES_MIN = 500
RES_MAX = 4000
BLOCO_RUIDO = 512


def semente(id_imagem: str) -> int:
    return zlib.crc32(id_imagem.encode("utf-8"))


def gerar_imagem(id_imagem: str, resolucao: int) -> np.ndarray:
    """Imagem RGB uint8 (resolucao x resolucao x 3) deterministica dado (id, resolucao).

    Gradiente horizontal no R, vertical no G, diagonal deslocada no B, mais ruido
    uniforme (soma uint8 com wrap-around, deterministica).
    """
    if not (RES_MIN <= resolucao <= RES_MAX):
        raise ResolucaoInvalida(f"resolucao fora de [{RES_MIN}, {RES_MAX}]")
    s = semente(id_imagem)
    rng = np.random.default_rng(s)
    eixo = np.linspace(0, 255, resolucao).astype(np.uint8)
    img = np.empty((resolucao, resolucao, 3), dtype=np.uint8)
    img[..., 0] = eixo[None, :]
    img[..., 1] = eixo[:, None]
    img[..., 2] = (eixo[None, :] // 2) + (eixo[:, None] // 2) + np.uint8(s % 256)
    # o ruido e sorteado so num bloco de BLOCO_RUIDO x BLOCO_RUIDO e repetido pela
    # imagem: sortear um valor por pixel custava ~3/4 do tempo de gerar a imagem
    lado = min(BLOCO_RUIDO, resolucao)
    bloco = rng.integers(0, 48, size=(lado, lado, 3), dtype=np.uint8)
    for y in range(0, resolucao, lado):
        for x in range(0, resolucao, lado):
            janela = img[y:y + lado, x:x + lado]
            janela += bloco[:janela.shape[0], :janela.shape[1]]
    return img


def checksum(arr: np.ndarray) -> int:
    return zlib.crc32(np.ascontiguousarray(arr))


def resumo(arr: np.ndarray) -> dict:
    return {
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "bytes": int(arr.nbytes),
        "media": float(arr.mean()),
        "checksum_crc32": checksum(arr),
    }


class ImagemService:
    """Cria imagens e as guarda no cache LRU."""

    def __init__(self, repo: ImagemCacheRepository) -> None:
        self.repo = repo

    def criar(self, id_imagem: str, resolucao: int) -> dict:
        """Gera a imagem e insere no cache.

        Levanta ImagemMaiorQueCache se a imagem sozinha nao cabe no cache.
        """
        t0 = time.perf_counter()
        img = gerar_imagem(id_imagem, resolucao)  # fora do lock: geracao em paralelo entre threads
        despejadas = self.repo.put(id_imagem, img)
        snap = self.repo.snapshot(max_ids=0)
        return {
            "id": id_imagem,
            "resolucao": resolucao,
            "shape": list(img.shape),
            "bytes": int(img.nbytes),
            "despejadas": despejadas,
            "geracao_ms": round((time.perf_counter() - t0) * 1000, 3),
            "cache": {k: snap[k] for k in ("bytes", "itens", "capacidade_bytes", "evictions")},
            "pid": os.getpid(),
        }
