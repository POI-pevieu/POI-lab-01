"""Ordens de compra/venda: validação de saldo/posição, SQLite + livro-caixa na mesma transação."""

from __future__ import annotations

import os
import sqlite3
import time

from ..exceptions import OrdemInvalida, PosicaoInsuficiente, SaldoInsuficiente
from ..repositories import investidor_repository, ordem_repository
from ..repositories.database import Database
from ..repositories.ledger_repository import LedgerRepository
from .formatacao import ordem_dict, reais
from .investidor_service import InvestidorService
from .mercado_service import MercadoService


class OrdemService:
    def __init__(self, db: Database, ledger: LedgerRepository, investidores: InvestidorService,
                 mercado: MercadoService):
        self.db = db
        self.ledger = ledger
        self.investidores = investidores
        self.mercado = mercado

    def executar(self, investidor_id: int, ticker: str, tipo: str, quantidade: int, origem: str = "manual") -> dict:
        """Uma ordem = uma transação BEGIN IMMEDIATE (ver ``executar_em``)."""
        with self.db.escrita() as conn:
            return self.executar_em(conn, investidor_id, ticker, tipo, quantidade, origem)

    def executar_em(self, conn: sqlite3.Connection, investidor_id: int, ticker: str, tipo: str,
                    quantidade: int, origem: str) -> dict:
        """Executa ordem ao preço atual. Deve rodar dentro de BEGIN IMMEDIATE.

        A linha do livro-caixa é gravada (com fsync) ANTES do COMMIT, ainda com o
        lock de escrita do SQLite: se o fsync falhar a exceção desfaz a transação,
        e as linhas do livro saem na mesma ordem dos ids das ordens.
        """
        if tipo not in ("compra", "venda"):
            raise OrdemInvalida(f"tipo inválido: {tipo}")
        if quantidade <= 0:
            raise OrdemInvalida("quantidade deve ser positiva")
        inv = self.investidores.obter_em(conn, investidor_id)
        acao = self.mercado.obter_acao_em(conn, ticker)
        preco = acao["preco_centavos"]
        valor = preco * quantidade
        saldo = inv["saldo_centavos"]
        pos = investidor_repository.obter_posicao(conn, investidor_id, ticker)
        qtd_atual = pos["quantidade"] if pos else 0
        custo_atual = pos["custo_centavos"] if pos else 0

        if tipo == "compra":
            if valor > saldo:
                raise SaldoInsuficiente(f"saldo insuficiente: necessário {reais(valor)}, disponível {reais(saldo)}")
            novo_saldo = saldo - valor
            nova_qtd = qtd_atual + quantidade
            novo_custo = custo_atual + valor
        else:
            if quantidade > qtd_atual:
                raise PosicaoInsuficiente(
                    f"posição insuficiente: {qtd_atual} ações de {ticker}, venda de {quantidade}")
            novo_saldo = saldo + valor
            nova_qtd = qtd_atual - quantidade
            novo_custo = 0 if nova_qtd == 0 else custo_atual - (custo_atual * quantidade) // qtd_atual

        investidor_repository.atualizar_saldo(conn, investidor_id, novo_saldo)
        investidor_repository.salvar_posicao(conn, investidor_id, ticker, nova_qtd, novo_custo)
        agora = time.time()
        ordem_id = ordem_repository.inserir(conn, investidor_id, ticker, tipo, quantidade, preco, valor,
                                            novo_saldo, origem, agora)
        ordem = {
            "id": ordem_id, "investidor_id": investidor_id, "ticker": ticker, "tipo": tipo,
            "quantidade": quantidade, "preco_centavos": preco, "valor_centavos": valor,
            "saldo_apos_centavos": novo_saldo, "origem": origem, "criado_em": agora,
        }
        self.ledger.registrar({"ordem_id": ordem_id, "pid": os.getpid(),
                               **{k: v for k, v in ordem.items() if k != "id"}})
        return ordem_dict(ordem)

    def listar(self, investidor_id: int | None, limit: int) -> list[dict]:
        with self.db.conexao() as conn:
            return [ordem_dict(r) for r in ordem_repository.listar(conn, investidor_id, limit)]
