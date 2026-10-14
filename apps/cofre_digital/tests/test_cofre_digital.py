"""Functional tests for Cofre Digital against a live server.

Expected values are independent of the server code: well-known prime counts
(pi(x)), hashlib computed here, and a matrix product recomputed here with a
different (column/zip based) multiplication.
"""
from __future__ import annotations

import hashlib
import random

import pytest


# ------------------------------------------------------------------ health
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "cofre_digital"
    assert isinstance(body["pid"], int) and body["pid"] > 0
    assert float(r.headers["x-process-time"]) >= 0


# ------------------------------------------------------------------ primos
# pi(x) and largest prime <= x: classical published values.
@pytest.mark.parametrize(
    "n,count,largest",
    [
        (2, 1, 2),
        (10, 4, 7),
        (100, 25, 97),
        (10_000, 1229, 9973),
        (100_000, 9592, 99991),
        (1_000_000, 78498, 999983),
    ],
)
def test_primos_known_values(client, n, count, largest):
    r = client.get(f"/primos/{n}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n"] == n
    assert body["quantidade"] == count
    assert body["maior_primo"] == largest
    assert body["elapsed_ms"] >= 0
    assert len(body["chave"]) == 64
    assert "x-process-time" in r.headers


@pytest.mark.parametrize("n", [0, 1, -5, 5_000_001])
def test_primos_out_of_bounds_422(client, n):
    assert client.get(f"/primos/{n}").status_code == 422


def test_primos_non_integer_422(client):
    assert client.get("/primos/abc").status_code == 422


# ------------------------------------------------------------------ assinar
def _sha_iter(texto: str, rounds: int) -> str:
    h = hashlib.sha256(texto.encode("utf-8")).digest()
    for _ in range(rounds - 1):
        h = hashlib.sha256(h).digest()
    return h.hex()


def test_assinar_single_round_equals_plain_sha256(client):
    r = client.post("/assinar", json={"texto": "abc", "rounds": 1})
    assert r.status_code == 200
    # FIPS 180-2 test vector for SHA-256("abc")
    assert r.json()["assinatura"] == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


@pytest.mark.parametrize("texto,rounds", [("abc", 2), ("cofre digital", 10_000),
                                          ("ação çñ ✓", 50_000), ("", 100_000)])
def test_assinar_matches_hashlib(client, texto, rounds):
    r = client.post("/assinar", json={"texto": texto, "rounds": rounds})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rounds"] == rounds
    assert body["assinatura"] == _sha_iter(texto, rounds)
    assert body["elapsed_ms"] >= 0


@pytest.mark.parametrize(
    "payload",
    [
        {"texto": "x", "rounds": 0},
        {"texto": "x", "rounds": 5_000_001},
        {"texto": "x"},
        {"rounds": 10},
        {"texto": "x", "rounds": "muitos"},
        {"texto": "x" * 10_001, "rounds": 10},
    ],
)
def test_assinar_invalid_422(client, payload):
    assert client.post("/assinar", json=payload).status_code == 422


# ------------------------------------------------------------------ cifrar-matriz
def _expected_matrix(tamanho: int, seed: int, mensagem: str):
    rng = random.Random(seed)
    a = [[rng.getrandbits(8) for _ in range(tamanho)] for _ in range(tamanho)]
    dados = mensagem.encode("utf-8")
    flat = [dados[k % len(dados)] for k in range(tamanho * tamanho)]
    b = [flat[i * tamanho:(i + 1) * tamanho] for i in range(tamanho)]
    cols = list(zip(*b))
    c = [[sum(x * y for x, y in zip(row, col)) for col in cols] for row in a]
    checksum = sum(map(sum, c))
    traco = sum(c[i][i] for i in range(tamanho))
    amostra = bytes(v % 256 for v in c[0][:16]).hex()
    return checksum, traco, amostra


@pytest.mark.parametrize("tamanho,seed,mensagem", [
    (2, 0, "ab"), (50, 42, "cofre digital"), (97, 123456789, "segredo ✓"),
])
def test_cifrar_matriz_matches_independent_recomputation(client, tamanho, seed, mensagem):
    r = client.get(f"/cifrar-matriz/{tamanho}", params={"seed": seed, "mensagem": mensagem})
    assert r.status_code == 200, r.text
    body = r.json()
    checksum, traco, amostra = _expected_matrix(tamanho, seed, mensagem)
    assert body["tamanho"] == tamanho
    assert body["seed"] == seed
    assert body["checksum"] == checksum
    assert body["traco"] == traco
    assert body["amostra_hex"] == amostra


def test_cifrar_matriz_default_message_and_determinism(client):
    r1 = client.get("/cifrar-matriz/60", params={"seed": 7}).json()
    r2 = client.get("/cifrar-matriz/60", params={"seed": 7}).json()
    checksum, traco, _ = _expected_matrix(60, 7, "cofre digital")
    assert r1["checksum"] == r2["checksum"] == checksum
    assert r1["traco"] == r2["traco"] == traco


def test_cifrar_matriz_without_seed_returns_reproducible_seed(client):
    body = client.get("/cifrar-matriz/30").json()
    checksum, _, _ = _expected_matrix(30, body["seed"], "cofre digital")
    assert body["checksum"] == checksum


@pytest.mark.parametrize("url", [
    "/cifrar-matriz/1", "/cifrar-matriz/401", "/cifrar-matriz/50?seed=-1",
    "/cifrar-matriz/50?mensagem=", "/cifrar-matriz/abc",
])
def test_cifrar_matriz_invalid_422(client, url):
    assert client.get(url).status_code == 422

