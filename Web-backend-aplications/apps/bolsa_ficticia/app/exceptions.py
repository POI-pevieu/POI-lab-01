"""Exceções de domínio levantadas pelos services.

Não conhecem HTTP: o mapeamento para status code fica em
``controllers/erros.py``. ``str(exc)`` é a mensagem devolvida em ``detail``.
"""

from __future__ import annotations


class ErroDominio(Exception):
    """Base de todas as regras de negócio violadas."""


class InvestidorNaoEncontrado(ErroDominio):
    def __init__(self, investidor_id: int):
        super().__init__(f"investidor {investidor_id} não encontrado")
        self.investidor_id = investidor_id


class AcaoNaoEncontrada(ErroDominio):
    """Ticker que não existe na bolsa."""

    def __init__(self, ticker: str):
        super().__init__(f"ação {ticker} não encontrada")
        self.ticker = ticker


class OrdemInvalida(ErroDominio):
    """Tipo/quantidade inválidos (o controller já barra com 422; defesa do service)."""


class SaldoInsuficiente(ErroDominio):
    pass


class PosicaoInsuficiente(ErroDominio):
    pass
