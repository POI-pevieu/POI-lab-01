"""Unit tests for the service layer: real functions, known values, no mocks."""
from __future__ import annotations

import hashlib

import pytest

from app.exceptions import ParametroInvalidoError
from app.services.assinatura_service import AssinaturaService, sha256_iterado
from app.services.matriz_service import MatrizService, gerar_matrizes, multiplicar
from app.services.primos_service import PrimosService, gerar_primos


def test_gerar_primos_small():
    assert gerar_primos(1) == []
    assert gerar_primos(2) == [2]
    assert gerar_primos(30) == [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]


def test_primos_service_pi_10000():
    r = PrimosService().calcular(10_000)
    assert (r["quantidade"], r["maior_primo"]) == (1229, 9973)
    # 8 largest primes <= 10 000 (published tables)
    esperado = hashlib.sha256(b"9907,9923,9929,9931,9941,9949,9967,9973").hexdigest()
    assert r["chave"] == esperado


def test_sha256_iterado_fips_vector():
    assert sha256_iterado("abc", 1) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    h2 = hashlib.sha256(hashlib.sha256(b"abc").digest()).hexdigest()
    assert sha256_iterado("abc", 2) == h2


def test_multiplicar_known_product():
    a = [[1, 2], [3, 4]]
    b = [[5, 6], [7, 8]]
    assert multiplicar(a, b) == [[19, 22], [43, 50]]


def test_gerar_matrizes_message_cyclic():
    _, b = gerar_matrizes(3, 0, "ab")
    assert b == [[97, 98, 97], [98, 97, 98], [97, 98, 97]]


def test_matriz_service_deterministic():
    s = MatrizService()
    r1 = s.cifrar(20, 7, "cofre digital")
    r2 = s.cifrar(20, 7, "cofre digital")
    assert r1["seed"] == 7
    assert (r1["checksum"], r1["traco"], r1["amostra_hex"]) == (
        r2["checksum"], r2["traco"], r2["amostra_hex"])


@pytest.mark.parametrize("call", [
    lambda: PrimosService().calcular(1),
    lambda: AssinaturaService().assinar("x", 0),
    lambda: AssinaturaService().assinar("x" * 10_001, 1),
    lambda: MatrizService().cifrar(1, 0, "a"),
    lambda: MatrizService().cifrar(10, -1, "a"),
    lambda: MatrizService().cifrar(10, 0, ""),
])
def test_services_reject_out_of_domain(call):
    with pytest.raises(ParametroInvalidoError):
        call()
