"""Configuracao lida do ambiente (lugar unico).

| var | padrao | significado |
|---|---|---|
| WORKERS | 1 | usado no modo CACHE_MAX_MB=auto (divide a fracao de RAM) |
| CACHE_MAX_MB | 1024 | capacidade do cache por processo em MiB, ou `auto` |
| CACHE_RAM_FRACTION | 0.5 | com `auto`: capacidade = fracao x RAM total / WORKERS |
| CACHE_MAX_ITEMS | (vazio) | limite adicional por numero de itens |
| MOSAICO_MAX_MB | 512 | tamanho maximo do array do mosaico |

HOST/PORT/TIMEOUT/LOG_LEVEL sao lidos pelo gunicorn.conf.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import psutil

MB = 1024 * 1024


def _workers() -> int:
    """Mesma regra do gunicorn.conf.py: WORKERS=auto -> um worker por vCPU."""
    bruto = os.environ.get("WORKERS", "1").strip().lower()
    if bruto == "auto":
        return os.cpu_count() or 1
    try:
        return max(1, int(bruto))
    except ValueError:
        return 1


def _capacidade_bytes() -> tuple[int, str]:
    """CACHE_MAX_MB: numero (MiB) ou 'auto' = CACHE_RAM_FRACTION * RAM total / WORKERS."""
    bruto = os.environ.get("CACHE_MAX_MB", "1024").strip().lower()
    if bruto == "auto":
        frac = float(os.environ.get("CACHE_RAM_FRACTION", "0.5"))
        if not (0 < frac <= 1):
            raise ValueError("CACHE_RAM_FRACTION deve estar em (0, 1]")
        total = psutil.virtual_memory().total
        return int(total * frac / _workers()), f"auto ({frac} da RAM / {_workers()} workers)"
    return int(float(bruto) * MB), "fixo (CACHE_MAX_MB)"


def _max_itens() -> int | None:
    v = os.environ.get("CACHE_MAX_ITEMS", "").strip()
    return int(v) if v else None


@dataclass(frozen=True)
class Settings:
    workers: int
    cache_max_bytes: int
    modo_capacidade: str
    cache_max_itens: int | None
    mosaico_max_bytes: int

    @classmethod
    def do_ambiente(cls) -> "Settings":
        capacidade, modo = _capacidade_bytes()
        return cls(
            workers=_workers(),
            cache_max_bytes=capacidade,
            modo_capacidade=modo,
            cache_max_itens=_max_itens(),
            mosaico_max_bytes=int(float(os.environ.get("MOSAICO_MAX_MB", "512")) * MB),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings do processo (lidas uma vez por worker)."""
    return Settings.do_ambiente()
