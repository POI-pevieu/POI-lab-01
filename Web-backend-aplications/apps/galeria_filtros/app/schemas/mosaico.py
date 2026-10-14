"""Modelo de resposta de /mosaico."""

from typing import List

from pydantic import BaseModel


class MosaicoResposta(BaseModel):
    quantidade_pedida: int
    quantidade_usada: int
    ids: List[str]
    shape: List[int]
    dtype: str
    bytes: int
    media: float
    checksum_crc32: int
    mosaico_ms: float
    pid: int
