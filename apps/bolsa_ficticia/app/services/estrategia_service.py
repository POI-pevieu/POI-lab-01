"""Estratégia automática: ação em alta -> compra; em baixa -> vende; estável -> mantém."""

from __future__ import annotations

import math
import random

from ..repositories import acao_repository, investidor_repository
from ..repositories.database import Database
from .formatacao import reais, tendencia
from .investidor_service import InvestidorService
from .mercado_service import MercadoService
from .ordem_service import OrdemService


class EstrategiaService:
    def __init__(self, db: Database, investidores: InvestidorService, mercado: MercadoService,
                 ordens: OrdemService, fracao_compra: float = 0.2, fracao_venda: float = 1.0,
                 rng: random.Random | None = None):
        self.db = db
        self.investidores = investidores
        self.mercado = mercado
        self.ordens = ordens
        self.fracao_compra = fracao_compra
        self.fracao_venda = fracao_venda
        self.rng = rng or random.SystemRandom()

    def operar(self, investidor_id: int, ticker: str | None) -> dict:
        """Decide e (se for o caso) executa a ordem, tudo numa única transação BEGIN IMMEDIATE."""
        with self.db.escrita() as conn:
            inv = self.investidores.obter_em(conn, investidor_id)
            if ticker is None:
                ticker = self.rng.choice(acao_repository.listar_tickers(conn))
            acao = self.mercado.obter_acao_em(conn, ticker)
            preco = acao["preco_centavos"]
            tend = tendencia(preco, acao["preco_anterior_centavos"])
            resp = {"investidor_id": investidor_id, "ticker": ticker, "tendencia": tend,
                    "preco": reais(preco), "decisao": "manter", "motivo": None, "ordem": None}

            if tend == "alta":
                qtd = int(inv["saldo_centavos"] * self.fracao_compra) // preco
                if qtd <= 0:
                    resp["motivo"] = "saldo insuficiente para comprar 1 ação"
                    return resp
                resp["decisao"] = "compra"
                resp["motivo"] = "preço subindo"
                resp["ordem"] = self.ordens.executar_em(conn, investidor_id, ticker, "compra", qtd, "estrategia")
            elif tend == "baixa":
                pos = investidor_repository.obter_posicao(conn, investidor_id, ticker)
                possui = pos["quantidade"] if pos else 0
                if possui <= 0:
                    resp["motivo"] = "preço caindo, mas sem posição para vender"
                    return resp
                qtd = min(possui, max(1, math.ceil(possui * self.fracao_venda)))
                resp["decisao"] = "venda"
                resp["motivo"] = "preço caindo"
                resp["ordem"] = self.ordens.executar_em(conn, investidor_id, ticker, "venda", qtd, "estrategia")
            else:
                resp["motivo"] = "preço estável"
            return resp
