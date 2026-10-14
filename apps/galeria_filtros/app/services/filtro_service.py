"""Filtros sobre imagens do cache (cada filtro aloca uma copia nova)."""

from __future__ import annotations

import io
import os
import time

import numpy as np

from ..exceptions import FiltroDesconhecido, ImagemNaoEncontrada
from ..repositories.imagem_cache_repository import ImagemCacheRepository
from .imagem_service import resumo

FILTROS = ("negativo", "cinza")
FORMATOS = ("resumo", "png")

# pesos de luminancia ITU-R BT.601 (0.299, 0.587, 0.114) em ponto fixo /256:
# conta em uint16 e custa metade da versao float32
_PESOS_CINZA = (np.uint16(77), np.uint16(150), np.uint16(29))


def filtro_negativo(img: np.ndarray) -> np.ndarray:
    return np.subtract(np.uint8(255), img)  # nova copia, mesmo tamanho da original


def filtro_cinza(img: np.ndarray) -> np.ndarray:
    r, g, b = _PESOS_CINZA
    lum = img[..., 0] * r  # uint16: no maximo 255 * 256, nao estoura
    lum += img[..., 1] * g
    lum += img[..., 2] * b
    lum += np.uint16(128)  # arredonda
    lum >>= 8
    return lum.astype(np.uint8)


def aplicar_filtro(img: np.ndarray, tipo: str) -> np.ndarray:
    if tipo == "negativo":
        return filtro_negativo(img)
    if tipo == "cinza":
        return filtro_cinza(img)
    raise FiltroDesconhecido(f"filtro desconhecido: {tipo}")


def codificar_png(arr: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


class FiltroService:
    def __init__(self, repo: ImagemCacheRepository) -> None:
        self.repo = repo

    def _buscar(self, id_imagem: str) -> np.ndarray:
        img = self.repo.get(id_imagem)  # conta hit/miss e atualiza a ordem LRU
        if img is None:
            raise ImagemNaoEncontrada(id_imagem)
        return img

    def filtrar(self, id_imagem: str, tipo: str) -> dict:
        """Aplica o filtro e devolve o resumo. Levanta ImagemNaoEncontrada em miss."""
        img = self._buscar(id_imagem)
        t0 = time.perf_counter()
        filtrada = aplicar_filtro(img, tipo)  # copia nova: memoria temporariamente dobra
        r = resumo(filtrada)
        return {
            "id": id_imagem,
            "tipo": tipo,
            "original": {"shape": list(img.shape), "bytes": int(img.nbytes)},
            "filtrada": r,
            "filtro_ms": round((time.perf_counter() - t0) * 1000, 3),
            "pid": os.getpid(),
        }

    def filtrar_png(self, id_imagem: str, tipo: str) -> bytes:
        """Aplica o filtro e devolve a imagem codificada em PNG."""
        img = self._buscar(id_imagem)
        return codificar_png(aplicar_filtro(img, tipo))
