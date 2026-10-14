"""Locust workload for Cofre Digital (CPU-bound app, port 8001).

Environment knobs (all optional):

  LT_WAIT_MIN / LT_WAIT_MAX     think time between tasks, seconds (0.5 / 1.5)

  Randomised mode (default, user class ``CofreUser``):
  COFRE_N_MIN / COFRE_N_MAX           range for GET /primos/{n}          (50000 / 500000)
  COFRE_ROUNDS_MIN / COFRE_ROUNDS_MAX range for POST /assinar rounds     (50000 / 500000)
  COFRE_MATRIZ_MIN / COFRE_MATRIZ_MAX range for GET /cifrar-matriz/{t}   (60 / 160)
  COFRE_PESOS                         task weights "primos,assinar,matriz" ("3,3,2")

  Fixed-parameter mode (controlled experiments, user class ``CofreFixedUser``):
  COFRE_FIXED=1                       use fixed parameters instead of random ranges
  COFRE_N_FIXED                       (200000)
  COFRE_ROUNDS_FIXED                  (200000)
  COFRE_MATRIZ_FIXED                  (120)
  COFRE_SEED_FIXED                    (12345) seed for /cifrar-matriz
  In fixed mode the expected results are computed ONCE locally at start-up and
  every response is checked for exact equality.

Stats are grouped with ``name=`` (``/primos/[n]``, ``/assinar``,
``/cifrar-matriz/[tamanho]``).  Every response body is validated; a wrong body
is reported as a failure.
"""
from __future__ import annotations

import hashlib
import os
import random

from locust import HttpUser, between


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


WAIT_MIN = float(os.environ.get("LT_WAIT_MIN", "0.5"))
WAIT_MAX = float(os.environ.get("LT_WAIT_MAX", "1.5"))

N_MIN, N_MAX = _int("COFRE_N_MIN", 50_000), _int("COFRE_N_MAX", 500_000)
ROUNDS_MIN, ROUNDS_MAX = _int("COFRE_ROUNDS_MIN", 50_000), _int("COFRE_ROUNDS_MAX", 500_000)
MATRIZ_MIN, MATRIZ_MAX = _int("COFRE_MATRIZ_MIN", 60), _int("COFRE_MATRIZ_MAX", 160)
PESOS = [int(x) for x in os.environ.get("COFRE_PESOS", "3,3,2").split(",")]

FIXED = os.environ.get("COFRE_FIXED", "0").lower() in ("1", "true", "yes")
N_FIXED = _int("COFRE_N_FIXED", 200_000)
ROUNDS_FIXED = _int("COFRE_ROUNDS_FIXED", 200_000)
MATRIZ_FIXED = _int("COFRE_MATRIZ_FIXED", 120)
SEED_FIXED = _int("COFRE_SEED_FIXED", 12345)
TEXTO_FIXED = "cofre digital - documento fixo"

HEX = set("0123456789abcdef")


def _is_prime(k: int) -> bool:
    if k < 2:
        return False
    if k % 2 == 0:
        return k == 2
    d = 3
    while d * d <= k:
        if k % d == 0:
            return False
        d += 2
    return True


def _is_hex64(s) -> bool:
    return isinstance(s, str) and len(s) == 64 and set(s) <= HEX


def _check(resp, cond: bool, msg: str) -> None:
    if cond:
        resp.success()
    else:
        resp.failure(f"{msg}: {resp.text[:200]}")


def _json(resp):
    if resp.status_code != 200:
        resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        return resp.json()
    except ValueError:
        resp.failure("invalid JSON")
        return None


# ---------------------------------------------------------------- tasks
def primos(user, n: int, expected=None):
    with user.client.get(f"/primos/{n}", name="/primos/[n]", catch_response=True) as r:
        b = _json(r)
        if b is None:
            return
        if expected is not None:
            _check(r, (b.get("quantidade"), b.get("maior_primo")) == expected, "resultado errado")
            return
        mp = b.get("maior_primo")
        _check(
            r,
            b.get("n") == n and isinstance(mp, int) and mp <= n
            and isinstance(b.get("quantidade"), int) and b["quantidade"] > 0
            and _is_prime(mp),
            "corpo invalido",
        )


def assinar(user, texto: str, rounds: int, expected=None):
    with user.client.post("/assinar", json={"texto": texto, "rounds": rounds},
                          name="/assinar", catch_response=True) as r:
        b = _json(r)
        if b is None:
            return
        ok = b.get("rounds") == rounds and _is_hex64(b.get("assinatura"))
        if expected is not None:
            ok = ok and b["assinatura"] == expected
        _check(r, ok, "assinatura invalida")


def cifrar(user, tamanho: int, seed: int, expected=None):
    with user.client.get(f"/cifrar-matriz/{tamanho}", params={"seed": seed},
                         name="/cifrar-matriz/[tamanho]", catch_response=True) as r:
        b = _json(r)
        if b is None:
            return
        ok = (b.get("tamanho") == tamanho and b.get("seed") == seed
              and isinstance(b.get("checksum"), int) and isinstance(b.get("traco"), int)
              and b["checksum"] >= b["traco"] >= 0)
        if expected is not None:
            ok = ok and (b["checksum"], b["traco"]) == expected
        _check(r, ok, "cifra invalida")


# ---------------------------------------------------------------- users
class CofreUser(HttpUser):
    """Random parameters within the env-configured ranges."""

    abstract = FIXED
    wait_time = between(WAIT_MIN, WAIT_MAX)

    def t_primos(self):
        primos(self, random.randint(N_MIN, N_MAX))

    def t_assinar(self):
        texto = f"doc-{random.getrandbits(48):012x}"
        assinar(self, texto, random.randint(ROUNDS_MIN, ROUNDS_MAX))

    def t_cifrar(self):
        cifrar(self, random.randint(MATRIZ_MIN, MATRIZ_MAX), random.getrandbits(32))

    tasks = {t_primos: PESOS[0], t_assinar: PESOS[1], t_cifrar: PESOS[2]}


def _expected_fixed():
    """Independent local computation of the fixed-mode answers (run once)."""
    # primes <= N_FIXED via a simple bytearray sieve (different algorithm than the server)
    sieve = bytearray([1]) * (N_FIXED + 1)
    sieve[0:2] = b"\x00\x00"
    for i in range(2, int(N_FIXED ** 0.5) + 1):
        if sieve[i]:
            sieve[i * i::i] = bytes(len(range(i * i, N_FIXED + 1, i)))
    count = sum(sieve)
    largest = max(i for i in range(N_FIXED, 1, -1) if sieve[i]) if count else None

    h = hashlib.sha256(TEXTO_FIXED.encode()).digest()
    for _ in range(ROUNDS_FIXED - 1):
        h = hashlib.sha256(h).digest()

    t = MATRIZ_FIXED
    rng = random.Random(SEED_FIXED)
    a = [[rng.getrandbits(8) for _ in range(t)] for _ in range(t)]
    msg = "cofre digital".encode()
    flat = [msg[k % len(msg)] for k in range(t * t)]
    cols = list(zip(*[flat[i * t:(i + 1) * t] for i in range(t)]))
    c = [[sum(x * y for x, y in zip(row, col)) for col in cols] for row in a]
    return (count, largest), h.hex(), (sum(map(sum, c)), sum(c[i][i] for i in range(t)))


class CofreFixedUser(HttpUser):
    """Fixed parameters (COFRE_FIXED=1) with exact result verification."""

    abstract = not FIXED
    wait_time = between(WAIT_MIN, WAIT_MAX)
    _expected = None

    def on_start(self):
        if CofreFixedUser._expected is None:
            CofreFixedUser._expected = _expected_fixed()

    def t_primos(self):
        primos(self, N_FIXED, self._expected[0])

    def t_assinar(self):
        assinar(self, TEXTO_FIXED, ROUNDS_FIXED, self._expected[1])

    def t_cifrar(self):
        cifrar(self, MATRIZ_FIXED, SEED_FIXED, self._expected[2])

    tasks = {t_primos: PESOS[0], t_assinar: PESOS[1], t_cifrar: PESOS[2]}
