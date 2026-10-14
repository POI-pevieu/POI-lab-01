"""Regras de negócio da Bolsa Fictícia (sem FastAPI/HTTP).

Cada caso de uso de escrita abre UMA transação ``BEGIN IMMEDIATE`` e passa a
conexão para os repositórios; métodos ``*_em(conn, ...)`` compõem dentro de
uma transação já aberta. ``construir_servicos`` monta o grafo de objetos.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..repositories.database import Database
from ..repositories.ledger_repository import LedgerRepository
from .admin_service import AdminService
from .estrategia_service import EstrategiaService
from .health_service import HealthService
from .investidor_service import InvestidorService
from .livro_caixa_service import LivroCaixaService
from .mercado_service import MercadoService
from .ordem_service import OrdemService


@dataclass
class Servicos:
    db: Database
    ledger: LedgerRepository
    mercado: MercadoService
    investidores: InvestidorService
    ordens: OrdemService
    estrategia: EstrategiaService
    livro_caixa: LivroCaixaService
    admin: AdminService
    health: HealthService

    def inicializar(self) -> None:
        """Cria diretório/schema/seed e o arquivo do livro-caixa (seguro com vários workers)."""
        self.db.init_db()
        self.ledger.init()


def construir_servicos(settings: Settings) -> Servicos:
    db = Database(settings.db_path, settings.journal_mode, settings.synchronous, settings.busy_timeout_ms)
    ledger = LedgerRepository(settings.ledger_path, settings.ledger_fsync)
    mercado = MercadoService(db, settings.volatilidade)
    investidores = InvestidorService(db)
    ordens = OrdemService(db, ledger, investidores, mercado)
    estrategia = EstrategiaService(db, investidores, mercado, ordens, settings.fracao_compra,
                                   settings.fracao_venda, mercado.rng)
    return Servicos(
        db=db, ledger=ledger, mercado=mercado, investidores=investidores, ordens=ordens,
        estrategia=estrategia, livro_caixa=LivroCaixaService(db, ledger),
        admin=AdminService(db, ledger, mercado), health=HealthService(db, ledger),
    )
