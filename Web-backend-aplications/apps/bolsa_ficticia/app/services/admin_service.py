"""Operações administrativas: forçar preço e zerar banco + livro-caixa."""

from __future__ import annotations

from ..repositories.database import Database, transacao
from ..repositories.ledger_repository import LedgerRepository
from .mercado_service import MercadoService


class AdminService:
    def __init__(self, db: Database, ledger: LedgerRepository, mercado: MercadoService):
        self.db = db
        self.ledger = ledger
        self.mercado = mercado

    def definir_preco(self, ticker: str, preco_centavos: int) -> dict:
        """Força o preço de uma ação (o preço atual vira o anterior)."""
        with self.db.escrita() as conn:
            self.mercado.obter_acao_em(conn, ticker)
            self.mercado.registrar_precos_em(conn, {ticker: preco_centavos})
            return self.mercado.listar_acoes_em(conn)

    def reset(self) -> dict:
        """Zera banco e livro-caixa (repetibilidade entre repetições do experimento)."""
        with self.db.conexao() as conn:
            with transacao(conn):
                self.db.resetar(conn)
                # Escritas no livro só ocorrem com o lock de escrita do SQLite,
                # que estamos segurando: truncar aqui é seguro entre workers.
                self.ledger.truncar()
            self.db.compactar(conn)
        return {"status": "ok", **self.db.tamanho()}
