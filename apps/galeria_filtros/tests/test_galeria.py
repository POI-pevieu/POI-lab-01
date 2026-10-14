"""Testes funcionais contra o servidor real (sem mocks)."""
from __future__ import annotations

import io
import zlib

import numpy as np
import psutil
import pytest
from PIL import Image

from app.services.filtro_service import filtro_cinza
from app.services.imagem_service import gerar_imagem

IMG500 = 500 * 500 * 3


def criar(c, id_, r=500):
    resp = c.post(f"/imagens/{id_}", params={"resolucao": r})
    assert resp.status_code == 200, resp.text
    return resp.json()


def stats(c):
    resp = c.get("/cache/stats")
    assert resp.status_code == 200
    return resp.json()


def test_health(cliente):
    r = cliente.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert isinstance(r.json()["pid"], int)


@pytest.mark.parametrize("res", [500, 777, 1100])
def test_criar_bytes_e_shape(cliente, res):
    j = criar(cliente, f"img{res}", res)
    assert j["shape"] == [res, res, 3]
    assert j["bytes"] == res * res * 3
    s = stats(cliente)
    assert s["bytes"] == res * res * 3
    assert s["itens"] == 1


def test_recriar_mesmo_id_substitui(cliente):
    criar(cliente, "x", 500)
    criar(cliente, "x", 600)
    s = stats(cliente)
    assert s["itens"] == 1
    assert s["bytes"] == 600 * 600 * 3
    assert s["evictions"] == 0


def test_lru_despejo_em_ordem(cliente):
    for i in "abcde":  # 5 * 750_000 = 3_750_000 <= 4 MiB
        criar(cliente, i)
    s = stats(cliente)
    assert s["ids_lru"] == list("abcde")
    assert s["evictions"] == 0

    assert cliente.get("/imagens/a/filtro", params={"tipo": "negativo"}).status_code == 200
    assert stats(cliente)["ids_lru"] == list("bcdea")  # 'a' virou o mais recente

    j = criar(cliente, "f")  # nao cabe: despeja o LRU = 'b'
    assert j["despejadas"] == ["b"]
    s = stats(cliente)
    assert s["ids_lru"] == list("cdeaf")
    assert s["evictions"] == 1
    assert s["bytes"] == 5 * IMG500
    assert s["bytes"] <= s["capacidade_bytes"] == 4 * 1024 * 1024

    assert cliente.get("/imagens/b/filtro", params={"tipo": "cinza"}).status_code == 404
    assert cliente.get("/imagens/a/filtro", params={"tipo": "cinza"}).status_code == 200

    # imagem maior despeja varias de uma vez
    j = criar(cliente, "grande", 1000)  # 3_000_000 bytes
    assert j["despejadas"] == ["c", "d", "e", "f"]  # ordem LRU apos o acesso a "a"
    assert stats(cliente)["ids_lru"] == ["a", "grande"]


def test_hits_e_misses(cliente):
    criar(cliente, "h")
    for _ in range(3):
        assert cliente.get("/imagens/h/filtro", params={"tipo": "negativo"}).status_code == 200
    for _ in range(2):
        r = cliente.get("/imagens/nao_existe/filtro", params={"tipo": "negativo"})
        assert r.status_code == 404
    s = stats(cliente)
    assert (s["hits"], s["misses"]) == (3, 2)
    assert s["hit_ratio"] == pytest.approx(0.6)


def test_negativo_checksum_independente(cliente):
    res = 640
    criar(cliente, "neg", res)
    j = cliente.get("/imagens/neg/filtro", params={"tipo": "negativo"}).json()
    orig = gerar_imagem("neg", res)
    esperado = (255 - orig.astype(np.int16)).astype(np.uint8)
    f = j["filtrada"]
    assert f["shape"] == [res, res, 3]
    assert f["bytes"] == res * res * 3
    assert f["checksum_crc32"] == zlib.crc32(esperado.tobytes())
    assert f["media"] == pytest.approx(255 - orig.mean(), abs=1e-6)


def test_cinza(cliente):
    res = 520
    criar(cliente, "gray", res)
    j = cliente.get("/imagens/gray/filtro", params={"tipo": "cinza"}).json()
    orig = gerar_imagem("gray", res).astype(np.float64)
    lum = np.rint(orig[..., 0] * 0.299 + orig[..., 1] * 0.587 + orig[..., 2] * 0.114)
    f = j["filtrada"]
    assert f["shape"] == [res, res]
    assert f["bytes"] == res * res
    assert f["media"] == pytest.approx(lum.mean(), abs=0.01)
    assert f["checksum_crc32"] == zlib.crc32(filtro_cinza(gerar_imagem("gray", res)).tobytes())


def test_deterministico_por_id(cliente):
    criar(cliente, "det", 500)
    c1 = cliente.get("/imagens/det/filtro", params={"tipo": "negativo"}).json()["filtrada"]["checksum_crc32"]
    cliente.delete("/cache")
    criar(cliente, "det", 500)
    c2 = cliente.get("/imagens/det/filtro", params={"tipo": "negativo"}).json()["filtrada"]["checksum_crc32"]
    criar(cliente, "outro", 500)
    c3 = cliente.get("/imagens/outro/filtro", params={"tipo": "negativo"}).json()["filtrada"]["checksum_crc32"]
    assert c1 == c2 != c3


def test_png(cliente):
    criar(cliente, "png", 500)
    r = cliente.get("/imagens/png/filtro", params={"tipo": "negativo", "formato": "png"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    arr = np.asarray(Image.open(io.BytesIO(r.content)))
    assert np.array_equal(arr, 255 - gerar_imagem("png", 500))


def test_mosaico(cliente):
    assert cliente.get("/mosaico", params={"quantidade": 2}).status_code == 409  # cache vazio
    criar(cliente, "m1", 700)
    criar(cliente, "m2", 600)
    criar(cliente, "m3", 500)
    j = cliente.get("/mosaico", params={"quantidade": 3}).json()
    # grade 2x2, ladrilho = menor lado (500)
    assert j["shape"] == [1000, 1000, 3]
    assert j["bytes"] == 1000 * 1000 * 3
    assert j["ids"] == ["m3", "m2", "m1"]  # mais recentes primeiro
    # mosaico calculado de forma independente
    esperado = np.zeros((1000, 1000, 3), np.uint8)
    for n, (i, r) in enumerate([("m3", 500), ("m2", 600), ("m1", 700)]):
        lin, col = divmod(n, 2)
        esperado[lin * 500:(lin + 1) * 500, col * 500:(col + 1) * 500] = gerar_imagem(i, r)[:500, :500]
    assert j["checksum_crc32"] == zlib.crc32(esperado.tobytes())

    j = cliente.get("/mosaico", params={"quantidade": 10}).json()
    assert j["quantidade_usada"] == 3
    j = cliente.get("/mosaico", params={"quantidade": 1}).json()
    assert j["shape"] == [500, 500, 3]


def test_stats_e_reset(cliente):
    pid = cliente.get("/health").json()["pid"]
    criar(cliente, "s1")
    criar(cliente, "s2", 800)
    s = stats(cliente)
    assert s["pid"] == pid
    assert s["bytes"] == IMG500 + 800 * 800 * 3
    assert s["mb"] == pytest.approx(s["bytes"] / 2**20, abs=1e-3)
    assert s["inserts"] == 2
    assert s["rss_bytes"] >= s["bytes"]  # RSS real do processo contem o cache
    assert s["rss_bytes"] == pytest.approx(psutil.Process(pid).memory_info().rss, rel=0.5)
    r = cliente.delete("/cache").json()
    assert r["removidos"] == 2
    s = stats(cliente)
    assert (s["bytes"], s["itens"], s["hits"], s["misses"], s["evictions"]) == (0, 0, 0, 0, 0)


def test_imagem_maior_que_capacidade(cliente):
    r = cliente.post("/imagens/enorme", params={"resolucao": 4000})
    assert r.status_code == 507
    assert stats(cliente)["itens"] == 0


@pytest.mark.parametrize(
    "metodo,url,params",
    [
        ("post", "/imagens/a", {"resolucao": 499}),
        ("post", "/imagens/a", {"resolucao": 4001}),
        ("post", "/imagens/a", {"resolucao": "abc"}),
        ("post", "/imagens/id%20invalido", {"resolucao": 500}),
        ("get", "/imagens/a/filtro", {"tipo": "sepia"}),
        ("get", "/imagens/a/filtro", {}),
        ("get", "/imagens/a/filtro", {"tipo": "negativo", "formato": "jpeg"}),
        ("get", "/mosaico", {"quantidade": 0}),
        ("get", "/mosaico", {"quantidade": 65}),
    ],
)
def test_validacao_422(cliente, metodo, url, params):
    r = getattr(cliente, metodo)(url, params=params)
    assert r.status_code == 422, r.text
    assert stats(cliente)["misses"] == 0  # validacao falha antes de tocar no cache


def test_capacidade_auto_itens_e_limite_mosaico(servidor_auto):
    c = servidor_auto
    c.delete("/cache")
    s = stats(c)
    assert s["capacidade_bytes"] == int(psutil.virtual_memory().total * 0.01 / 1)
    assert s["modo_capacidade"].startswith("auto")
    assert s["capacidade_itens"] == 4
    for i in range(5):
        criar(c, f"q{i}")
    s = stats(c)
    assert s["ids_lru"] == ["q1", "q2", "q3", "q4"]  # limite por itens
    assert s["evictions"] == 1
    # MOSAICO_MAX_MB=1: grade 2x2 com ladrilho reduzido para caber em 1 MiB
    j = c.get("/mosaico", params={"quantidade": 4}).json()
    lado = int(np.sqrt(2**20 // 12))
    assert j["shape"] == [2 * lado, 2 * lado, 3]
    assert j["bytes"] <= 2**20
