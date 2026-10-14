"""Mercado: cotações e passeio aleatório geométrico dos preços."""

from __future__ import annotations

import math
import random
import sqlite3
import time

from ..exceptions import AcaoNaoEncontrada
from ..repositories import acao_repository
from ..repositories.database import Database
from .formatacao import acao_dict

PRECO_MINIMO_CENTAVOS = 100


class MercadoService:
    def __init__(self, db: Database, volatilidade: float = 0.02, rng: random.Random | None = None):
        self.db = db
        self.volatilidade = volatilidade
        self.rng = rng or random.SystemRandom()

    # ----------------------------------------------- operações com conexão
    def listar_acoes_em(self, conn: sqlite3.Connection) -> dict:
        tick = acao_repository.obter_tick(conn)
        return {"tick": tick, "acoes": [acao_dict(r) for r in acao_repository.listar(conn)]}

    def obter_acao_em(self, conn: sqlite3.Connection, ticker: str) -> sqlite3.Row:
        acao = acao_repository.obter(conn, ticker)
        if acao is None:
            raise AcaoNaoEncontrada(ticker)
        return acao

    def registrar_precos_em(self, conn: sqlite3.Connection, novos: dict[str, int]) -> int:
        """Avança o tick e grava os novos preços + histórico. Deve rodar dentro de transação."""
        agora = time.time()
        tick = acao_repository.incrementar_tick(conn)
        for ticker, preco in novos.items():
            acao_repository.atualizar_preco(conn, ticker, preco, agora)
            acao_repository.inserir_historico(conn, ticker, tick, preco, agora)
        return tick

    # --------------------------------------------------------- casos de uso
    def listar_acoes(self) -> dict:
        with self.db.conexao() as conn:
            return self.listar_acoes_em(conn)

    def tick(self, passos: int = 1) -> dict:
        """Avança o mercado `passos` vezes (passeio aleatório geométrico), numa única transação."""
        with self.db.escrita() as conn:
            for _ in range(passos):
                novos = {}
                for r in acao_repository.listar_precos(conn):
                    p = r["preco_centavos"] * math.exp(self.rng.gauss(0.0, self.volatilidade))
                    novos[r["ticker"]] = max(PRECO_MINIMO_CENTAVOS, int(round(p)))
                self.registrar_precos_em(conn, novos)
            return self.listar_acoes_em(conn)
