from __future__ import annotations

from pydantic import BaseModel


class PrimosResponse(BaseModel):
    n: int
    quantidade: int
    maior_primo: int
    chave: str
    algoritmo: str
    elapsed_ms: float
