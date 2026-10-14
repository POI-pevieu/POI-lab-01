from __future__ import annotations

from pydantic import BaseModel


class CifrarMatrizResponse(BaseModel):
    tamanho: int
    seed: int
    checksum: int
    traco: int
    amostra_hex: str
    elapsed_ms: float
