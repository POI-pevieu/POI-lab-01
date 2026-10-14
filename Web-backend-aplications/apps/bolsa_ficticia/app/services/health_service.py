"""Saúde do processo: banco acessível + configuração de durabilidade em uso."""

from __future__ import annotations

import os

from ..repositories.database import Database
from ..repositories.ledger_repository import LedgerRepository


class HealthService:
    def __init__(self, db: Database, ledger: LedgerRepository):
        self.db = db
        self.ledger = ledger

    def status(self) -> dict:
        self.db.ping()
        return {"status": "ok", "app": "bolsa_ficticia", "pid": os.getpid(),
                "journal_mode": self.db.journal_mode, "synchronous": self.db.synchronous,
                "ledger_fsync": self.ledger.fsync}
