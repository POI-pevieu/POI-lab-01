"""Matrix "cipher": pure-Python integer matrix multiplication.

numpy is avoided on purpose: it would release the GIL and spawn BLAS threads,
muddying the CPU profile.
"""
from __future__ import annotations

import random
import time

from app.config import settings
from app.exceptions import ParametroInvalidoError


def gerar_matrizes(tamanho: int, seed: int, mensagem: str):
    """Key matrix A: random bytes from ``random.Random(seed)`` (row-major,
    ``getrandbits(8)`` per entry).  Message matrix B: UTF-8 bytes of
    ``mensagem`` repeated cyclically, row-major."""
    rng = random.Random(seed)
    a = [[rng.getrandbits(8) for _ in range(tamanho)] for _ in range(tamanho)]
    dados = mensagem.encode("utf-8") or b"\x00"
    L = len(dados)
    b = [[dados[(i * tamanho + j) % L] for j in range(tamanho)] for i in range(tamanho)]
    return a, b


def multiplicar(a: list[list[int]], b: list[list[int]]) -> list[list[int]]:
    """Classic O(n^3) triple loop (i-k-j order), pure Python ints."""
    n = len(a)
    m = len(b[0])
    c = [[0] * m for _ in range(n)]
    for i in range(n):
        ai = a[i]
        ci = c[i]
        for k in range(len(b)):
            aik = ai[k]
            if aik == 0:
                continue
            bk = b[k]
            for j in range(m):
                ci[j] += aik * bk[j]
    return c


def seed_aleatoria() -> int:
    return random.SystemRandom().getrandbits(32)


class MatrizService:
    def cifrar(self, tamanho: int, seed: int | None, mensagem: str) -> dict:
        if not settings.matriz_min <= tamanho <= settings.matriz_max:
            raise ParametroInvalidoError(
                "tamanho", f"deve estar em [{settings.matriz_min}, {settings.matriz_max}]"
            )
        if seed is not None and not 0 <= seed <= settings.seed_max:
            raise ParametroInvalidoError("seed", f"deve estar em [0, {settings.seed_max}]")
        if not 1 <= len(mensagem) <= settings.mensagem_max_len:
            raise ParametroInvalidoError(
                "mensagem", f"deve ter de 1 a {settings.mensagem_max_len} caracteres"
            )
        if seed is None:
            seed = seed_aleatoria()
        t0 = time.perf_counter()
        a, b = gerar_matrizes(tamanho, seed, mensagem)
        c = multiplicar(a, b)
        checksum = sum(sum(linha) for linha in c)
        traco = sum(c[i][i] for i in range(tamanho))
        amostra = bytes(v % 256 for v in c[0][:16]).hex()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "tamanho": tamanho,
            "seed": seed,
            "checksum": checksum,
            "traco": traco,
            "amostra_hex": amostra,
            "elapsed_ms": round(elapsed_ms, 3),
        }
