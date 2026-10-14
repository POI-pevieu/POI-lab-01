"""Modelos de requisicao/resposta de /imagens."""

from typing import List, Literal

from pydantic import BaseModel

TipoFiltro = Literal["negativo", "cinza"]
FormatoSaida = Literal["resumo", "png"]


class ResumoArray(BaseModel):
    shape: List[int]
    dtype: str
    bytes: int
    media: float
    checksum_crc32: int


class CacheResumo(BaseModel):
    bytes: int
    itens: int
    capacidade_bytes: int
    evictions: int


class ImagemCriadaResposta(BaseModel):
    id: str
    resolucao: int
    shape: List[int]
    bytes: int
    despejadas: List[str]
    geracao_ms: float
    cache: CacheResumo
    pid: int


class OriginalInfo(BaseModel):
    shape: List[int]
    bytes: int


class FiltroResposta(BaseModel):
    id: str
    tipo: TipoFiltro
    original: OriginalInfo
    filtrada: ResumoArray
    filtro_ms: float
    pid: int
