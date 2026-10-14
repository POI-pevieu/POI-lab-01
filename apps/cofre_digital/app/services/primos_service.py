"""Prime generation by trial division -- pure Python, GIL-bound on purpose."""
from __future__ import annotations

import hashlib
import time

from app.config import settings
from app.exceptions import ParametroInvalidoError

ALGORITMO = "divisao_por_tentativa_python_puro"


def gerar_primos(n: int) -> list[int]:
    """Return all primes <= n using trial division by the primes found so far.

    For each odd candidate ``c`` we divide by the known primes ``p`` while
    ``p*p <= c``.  Cost grows roughly as O(n * sqrt(n) / log(n)^2) and is
    executed entirely by the interpreter (no numpy, no bytearray sieve, which
    would push the work into C).
    """
    if n < 2:
        return []
    primos = [2]
    for c in range(3, n + 1, 2):
        eh_primo = True
        for p in primos:
            if p * p > c:
                break
            if c % p == 0:
                eh_primo = False
                break
        if eh_primo:
            primos.append(c)
    return primos


def chave_dos_primos(primos: list[int]) -> str:
    """"Key" of the vault: SHA-256 hex of the 8 largest primes joined by commas."""
    return hashlib.sha256(",".join(map(str, primos[-8:])).encode()).hexdigest()


class PrimosService:
    def calcular(self, n: int) -> dict:
        if not settings.primos_n_min <= n <= settings.primos_n_max:
            raise ParametroInvalidoError(
                "n", f"deve estar em [{settings.primos_n_min}, {settings.primos_n_max}]"
            )
        t0 = time.perf_counter()
        lista = gerar_primos(n)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "n": n,
            "quantidade": len(lista),
            "maior_primo": lista[-1],
            "chave": chave_dos_primos(lista),
            "algoritmo": ALGORITMO,
            "elapsed_ms": round(elapsed_ms, 3),
        }
