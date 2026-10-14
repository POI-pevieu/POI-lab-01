"""Configuração da Bolsa Fictícia: único lugar que lê variáveis de ambiente.

Variáveis de ambiente:
  DATA_DIR                  diretório do banco e do livro-caixa (default: <app>/data)
  SQLITE_JOURNAL_MODE       WAL (default) | DELETE | TRUNCATE | PERSIST
  SQLITE_SYNCHRONOUS        FULL (default) | NORMAL | OFF | EXTRA
  SQLITE_BUSY_TIMEOUT_MS    espera máxima pelo lock de escrita (default 30000)
  LEDGER_FSYNC              1 (default) = os.fsync após cada linha do livro-caixa.
                            0 = sem fsync (SÓ para experimentos de comparação).
  MERCADO_VOLATILIDADE      desvio-padrão do retorno log por tick (default 0.02)
  ESTRATEGIA_FRACAO_COMPRA  fração do saldo usada numa compra automática (default 0.2)
  ESTRATEGIA_FRACAO_VENDA   fração da posição vendida numa venda automática (default 1.0)

(HOST/PORT/WORKERS/TIMEOUT/LOG_LEVEL/ACCESS_LOG são lidas pelo gunicorn.conf.py.)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

APP_DIR = Path(__file__).resolve().parent.parent

JOURNAL_MODES = {"WAL", "DELETE", "TRUNCATE", "PERSIST"}
SYNC_MODES = {"OFF", "NORMAL", "FULL", "EXTRA"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    journal_mode: str = "WAL"
    synchronous: str = "FULL"
    busy_timeout_ms: int = 30000
    ledger_fsync: bool = True
    volatilidade: float = 0.02
    fracao_compra: float = 0.2
    fracao_venda: float = 1.0

    def __post_init__(self) -> None:
        if self.journal_mode not in JOURNAL_MODES:
            raise RuntimeError(f"SQLITE_JOURNAL_MODE inválido: {self.journal_mode} (use {sorted(JOURNAL_MODES)})")
        if self.synchronous not in SYNC_MODES:
            raise RuntimeError(f"SQLITE_SYNCHRONOUS inválido: {self.synchronous} (use {sorted(SYNC_MODES)})")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bolsa.db"

    @property
    def ledger_path(self) -> Path:
        return self.data_dir / "livro_caixa.ledger"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        return cls(
            data_dir=Path(env.get("DATA_DIR") or (APP_DIR / "data")).resolve(),
            journal_mode=env.get("SQLITE_JOURNAL_MODE", "WAL").strip().upper(),
            synchronous=env.get("SQLITE_SYNCHRONOUS", "FULL").strip().upper(),
            busy_timeout_ms=int(env.get("SQLITE_BUSY_TIMEOUT_MS", "30000")),
            ledger_fsync=env.get("LEDGER_FSYNC", "1").strip().lower() not in ("0", "false", "no", "off"),
            volatilidade=float(env.get("MERCADO_VOLATILIDADE", "0.02")),
            fracao_compra=float(env.get("ESTRATEGIA_FRACAO_COMPRA", "0.2")),
            fracao_venda=float(env.get("ESTRATEGIA_FRACAO_VENDA", "1.0")),
        )
