"""Modelos Pydantic de entrada (as respostas são dicts montados pelos services)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NovoInvestidor(BaseModel):
    nome: str = Field(min_length=1, max_length=100)
    saldo_inicial: float = Field(default=10000.0, ge=0, le=1e9)


class NovaOrdem(BaseModel):
    investidor_id: int = Field(gt=0)
    ticker: str = Field(min_length=1, max_length=10)
    tipo: Literal["compra", "venda"]
    quantidade: int = Field(gt=0, le=1_000_000)


class Tick(BaseModel):
    passos: int = Field(default=1, ge=1, le=100)


class NovoPreco(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    preco: float = Field(ge=1.0, le=1e6)


def para_centavos(reais: float) -> int:
    return int(round(reais * 100))
