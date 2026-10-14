"""Testes unitarios das camadas service/repository (objetos reais, sem mocks, sem HTTP)."""
from __future__ import annotations

import zlib

import numpy as np
import pytest

from app.exceptions import (
    CacheVazio,
    FiltroDesconhecido,
    ImagemMaiorQueCache,
    ImagemNaoEncontrada,
    ResolucaoInvalida,
)
from app.repositories.imagem_cache_repository import ImagemCacheRepository
from app.services.cache_service import CacheService
from app.services.filtro_service import FiltroService, aplicar_filtro, codificar_png
from app.services.imagem_service import ImagemService, gerar_imagem, resumo
from app.services.mosaico_service import MosaicoService, geometria_mosaico

IMG500 = 500 * 500 * 3


def arr(n: int, valor: int = 0) -> np.ndarray:
    """Array uint8 pequeno de n bytes."""
    return np.full(n, valor, dtype=np.uint8)


@pytest.fixture
def repo():
    return ImagemCacheRepository(max_bytes=4 * IMG500 + 10)  # cabem 4 imagens 500x500


# ---------------------------------------------------------------- repository

def test_repo_lru_por_bytes():
    r = ImagemCacheRepository(max_bytes=30)
    for k in "abc":
        assert r.put(k, arr(10)) == []
    assert r.get("a") is not None  # 'a' vira o mais recente
    assert r.put("d", arr(10)) == ["b"]
    s = r.snapshot()
    assert s["ids_lru"] == ["c", "a", "d"]
    assert (s["bytes"], s["itens"], s["evictions"], s["evicted_bytes"], s["inserts"]) == (30, 3, 1, 10, 4)
    assert r.put("grande", arr(25)) == ["c", "a", "d"]
    assert r.snapshot()["ids_lru"] == ["grande"]


def test_repo_limite_itens_e_substituicao():
    r = ImagemCacheRepository(max_bytes=1000, max_items=2)
    r.put("a", arr(10))
    r.put("a", arr(20))  # substitui sem despejar
    assert r.snapshot()["bytes"] == 20
    r.put("b", arr(10))
    assert r.put("c", arr(10)) == ["a"]
    assert r.snapshot()["evictions"] == 1


def test_repo_maior_que_capacidade_hits_misses_limpar():
    r = ImagemCacheRepository(max_bytes=10)
    with pytest.raises(ImagemMaiorQueCache):
        r.put("x", arr(11))
    assert r.snapshot()["itens"] == 0
    r.put("y", arr(5))
    r.get("y")
    r.get("nao")
    s = r.snapshot()
    assert (s["hits"], s["misses"], s["hit_ratio"]) == (1, 1, 0.5)
    assert r.mais_recentes(5)[0][0] == "y"
    assert r.limpar() == 1
    s = r.snapshot()
    assert (s["bytes"], s["itens"], s["hits"], s["misses"], s["hit_ratio"]) == (0, 0, 0, 0, None)


# ---------------------------------------------------------------- imagem

def test_gerar_imagem_deterministica_e_limites():
    a = gerar_imagem("abc", 500)
    assert a.shape == (500, 500, 3) and a.dtype == np.uint8
    assert np.array_equal(a, gerar_imagem("abc", 500))
    assert not np.array_equal(a, gerar_imagem("abd", 500))
    for res in (499, 4001):
        with pytest.raises(ResolucaoInvalida):
            gerar_imagem("abc", res)


def test_imagem_service_criar(repo):
    svc = ImagemService(repo)
    j = svc.criar("i1", 500)
    assert (j["id"], j["resolucao"], j["shape"], j["bytes"]) == ("i1", 500, [500, 500, 3], IMG500)
    assert j["despejadas"] == []
    assert j["cache"] == {"bytes": IMG500, "itens": 1, "capacidade_bytes": repo.max_bytes, "evictions": 0}
    for i in range(2, 6):
        j = svc.criar(f"i{i}", 500)
    assert j["despejadas"] == ["i1"]
    with pytest.raises(ImagemMaiorQueCache):
        svc.criar("enorme", 1500)


# ---------------------------------------------------------------- filtro

def test_filtro_service_negativo_e_miss(repo):
    ImagemService(repo).criar("f", 500)
    svc = FiltroService(repo)
    j = svc.filtrar("f", "negativo")
    esperado = (255 - gerar_imagem("f", 500).astype(np.int16)).astype(np.uint8)
    assert j["original"] == {"shape": [500, 500, 3], "bytes": IMG500}
    assert j["filtrada"]["checksum_crc32"] == zlib.crc32(esperado.tobytes())
    with pytest.raises(ImagemNaoEncontrada):
        svc.filtrar("nao_existe", "negativo")
    s = repo.snapshot()
    assert (s["hits"], s["misses"]) == (1, 1)


def test_filtro_cinza_e_png(repo):
    ImagemService(repo).criar("g", 500)
    svc = FiltroService(repo)
    j = svc.filtrar("g", "cinza")
    assert j["filtrada"]["shape"] == [500, 500]
    assert j["filtrada"]["dtype"] == "uint8"
    png = svc.filtrar_png("g", "cinza")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png == codificar_png(aplicar_filtro(gerar_imagem("g", 500), "cinza"))
    with pytest.raises(FiltroDesconhecido):
        aplicar_filtro(gerar_imagem("g", 500), "sepia")


# ---------------------------------------------------------------- mosaico

def test_mosaico_service(repo):
    svc = MosaicoService(repo, max_bytes=64 * 1024 * 1024)
    with pytest.raises(CacheVazio):
        svc.montar(2)
    ImagemService(repo).criar("m1", 600)
    ImagemService(repo).criar("m2", 500)
    j = svc.montar(4)
    assert (j["quantidade_pedida"], j["quantidade_usada"], j["ids"]) == (4, 2, ["m2", "m1"])
    assert j["shape"] == [500, 1000, 3]  # 1 linha x 2 colunas, ladrilho 500
    esperado = np.zeros((500, 1000, 3), np.uint8)
    esperado[:, :500] = gerar_imagem("m2", 500)
    esperado[:, 500:] = gerar_imagem("m1", 600)[:500, :500]
    assert j["checksum_crc32"] == resumo(esperado)["checksum_crc32"]


def test_geometria_mosaico_limite_bytes():
    assert geometria_mosaico([500, 600, 700], 64 * 2**20) == (2, 2, 500)
    lin, col, lado = geometria_mosaico([500] * 4, 2**20)
    assert (lin, col) == (2, 2) and lado == int(np.sqrt(2**20 // 12))


# ---------------------------------------------------------------- cache service

def test_cache_service_stats_e_limpar(repo):
    ImagemService(repo).criar("s", 500)
    svc = CacheService(repo, "fixo (teste)")
    s = svc.estatisticas()
    assert s["bytes"] == IMG500 and s["itens"] == 1
    assert s["modo_capacidade"] == "fixo (teste)"
    assert s["mb"] == round(IMG500 / 2**20, 3)
    assert s["rss_bytes"] >= IMG500
    assert set(s["sistema"]) == {"ram_total", "ram_disponivel", "ram_percent"}
    r = svc.limpar()
    assert r["removidos"] == 1
    assert r["malloc_trim"] in (True, False, "indisponivel")
    assert repo.snapshot()["itens"] == 0


def test_settings_workers_auto_divide_ram_por_vcpu(monkeypatch):
    """WORKERS=auto (mesma regra do gunicorn.conf.py) divide a fracao de RAM por os.cpu_count()."""
    import os

    import psutil

    from app.config import Settings

    monkeypatch.setenv("WORKERS", "auto")
    monkeypatch.setenv("CACHE_MAX_MB", "auto")
    monkeypatch.setenv("CACHE_RAM_FRACTION", "0.4")
    s = Settings.do_ambiente()
    n = os.cpu_count() or 1
    assert s.workers == n
    assert s.cache_max_bytes == int(psutil.virtual_memory().total * 0.4 / n)
