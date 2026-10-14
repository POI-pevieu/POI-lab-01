"""Investidores: cadastro, consulta e carteira a preço de mercado."""

from __future__ import annotations

import sqlite3
import time

from ..exceptions import InvestidorNaoEncontrado
from ..repositories import investidor_repository, ordem_repository
from ..repositories.database import Database
from .formatacao import investidor_dict, reais


class InvestidorService:
    def __init__(self, db: Database):
        self.db = db

    def obter_em(self, conn: sqlite3.Connection, investidor_id: int) -> sqlite3.Row:
        inv = investidor_repository.obter(conn, investidor_id)
        if inv is None:
            raise InvestidorNaoEncontrado(investidor_id)
        return inv

    def criar(self, nome: str, saldo_centavos: int) -> dict:
        with self.db.escrita() as conn:
            novo_id = investidor_repository.inserir(conn, nome, saldo_centavos, time.time())
            return investidor_dict(self.obter_em(conn, novo_id))

    def obter(self, investidor_id: int) -> dict:
        with self.db.conexao() as conn:
            return investidor_dict(self.obter_em(conn, investidor_id))

    def carteira(self, investidor_id: int) -> dict:
        with self.db.conexao() as conn:
            inv = self.obter_em(conn, investidor_id)
            rows = investidor_repository.listar_posicoes_com_preco(conn, investidor_id)
            posicoes = []
            total_mercado = 0
            for r in rows:
                valor = r["quantidade"] * r["preco_centavos"]
                total_mercado += valor
                posicoes.append({
                    "ticker": r["ticker"],
                    "quantidade": r["quantidade"],
                    "custo_centavos": r["custo_centavos"],
                    "preco_medio": reais(r["custo_centavos"] // r["quantidade"]),
                    "preco_atual": reais(r["preco_centavos"]),
                    "valor_mercado": reais(valor),
                    "valor_mercado_centavos": valor,
                })
            patrimonio = inv["saldo_centavos"] + total_mercado
            n_ordens = ordem_repository.contar(conn, investidor_id)
        return {
            **investidor_dict(inv),
            "posicoes": posicoes,
            "valor_acoes": reais(total_mercado),
            "valor_acoes_centavos": total_mercado,
            "patrimonio": reais(patrimonio),
            "patrimonio_centavos": patrimonio,
            "resultado": reais(patrimonio - inv["saldo_inicial_centavos"]),
            "n_ordens": n_ordens,
        }
