"""Iterated SHA-256 signature.

``hashlib`` only releases the GIL for inputs larger than 2047 bytes, so each
round over a 32-byte digest runs with the GIL held and the per-round Python
loop overhead dominates.
"""
from __future__ import annotations

import hashlib
import time

from app.config import settings
from app.exceptions import ParametroInvalidoError

ALGORITMO = "sha256_iterado"


def sha256_iterado(texto: str, rounds: int) -> str:
    """h1 = SHA256(utf8(texto)); h(k+1) = SHA256(h(k)) on the raw 32-byte digest.

    Returns hex of h(rounds).
    """
    sha = hashlib.sha256
    h = sha(texto.encode("utf-8")).digest()
    for _ in range(rounds - 1):
        h = sha(h).digest()
    return h.hex()


class AssinaturaService:
    def assinar(self, texto: str, rounds: int) -> dict:
        if not settings.rounds_min <= rounds <= settings.rounds_max:
            raise ParametroInvalidoError(
                "rounds", f"deve estar em [{settings.rounds_min}, {settings.rounds_max}]"
            )
        if len(texto) > settings.texto_max_len:
            raise ParametroInvalidoError(
                "texto", f"no maximo {settings.texto_max_len} caracteres"
            )
        t0 = time.perf_counter()
        digest = sha256_iterado(texto, rounds)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "assinatura": digest,
            "rounds": rounds,
            "algoritmo": ALGORITMO,
            "elapsed_ms": round(elapsed_ms, 3),
        }
