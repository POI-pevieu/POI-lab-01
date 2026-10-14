"""Estatísticas do livro-caixa e do banco."""

from __future__ import annotations

from ..repositories import acao_repository, investidor_repository, ordem_repository
from ..repositories.database import Database
from ..repositories.ledger_repository import LedgerRepository


class LivroCaixaService:
    def __init__(self, db: Database, ledger: LedgerRepository):
        self.db = db
        self.ledger = ledger

    def estatisticas(self) -> dict:
        with self.db.conexao() as conn:
            n_ordens = ordem_repository.contar(conn)
            n_invest = investidor_repository.contar(conn)
            tick = acao_repository.obter_tick(conn)
        return {
            "livro_caixa": self.ledger.estatisticas(),
            "banco": {"arquivo": str(self.db.path), "journal_mode": self.db.journal_mode,
                      "synchronous": self.db.synchronous, **self.db.tamanho(),
                      "ordens": n_ordens, "investidores": n_invest, "tick": tick},
        }
