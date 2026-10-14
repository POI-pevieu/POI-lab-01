"""Conexão SQLite, schema, seed e transações da Bolsa Fictícia.

Uma conexão por requisição (cada thread do threadpool abre/fecha a sua).
Toda escrita usa ``BEGIN IMMEDIATE`` para pegar o lock de escrita logo no
início da transação: leitura do saldo/posição e a escrita correspondente
ficam atômicas mesmo com vários workers gunicorn (processos) e várias threads.
Quem abre a transação são os services (ver ``transacao``); os repositórios
apenas executam SQL na conexão recebida.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Empresas fictícias e preço inicial (centavos).
EMPRESAS: list[tuple[str, str, int]] = [
    ("CAFE3", "Cafeeira Serra Azul S.A.", 3250),
    ("MINA3", "Mineradora Vale Dourado S.A.", 6820),
    ("TECN3", "Tecnova Sistemas S.A.", 1575),
    ("ENER3", "Energia Ventos do Sul S.A.", 4410),
]

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS acoes (
        ticker TEXT PRIMARY KEY,
        nome TEXT NOT NULL,
        preco_centavos INTEGER NOT NULL CHECK (preco_centavos > 0),
        preco_anterior_centavos INTEGER NOT NULL CHECK (preco_anterior_centavos > 0),
        preco_inicial_centavos INTEGER NOT NULL,
        atualizado_em REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS mercado (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        tick INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS historico_precos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL REFERENCES acoes(ticker),
        tick INTEGER NOT NULL,
        preco_centavos INTEGER NOT NULL,
        ts REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS investidores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        saldo_inicial_centavos INTEGER NOT NULL CHECK (saldo_inicial_centavos >= 0),
        saldo_centavos INTEGER NOT NULL CHECK (saldo_centavos >= 0),
        criado_em REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS posicoes (
        investidor_id INTEGER NOT NULL REFERENCES investidores(id),
        ticker TEXT NOT NULL REFERENCES acoes(ticker),
        quantidade INTEGER NOT NULL CHECK (quantidade >= 0),
        custo_centavos INTEGER NOT NULL CHECK (custo_centavos >= 0),
        PRIMARY KEY (investidor_id, ticker))""",
    """CREATE TABLE IF NOT EXISTS ordens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        investidor_id INTEGER NOT NULL REFERENCES investidores(id),
        ticker TEXT NOT NULL REFERENCES acoes(ticker),
        tipo TEXT NOT NULL CHECK (tipo IN ('compra', 'venda')),
        quantidade INTEGER NOT NULL CHECK (quantidade > 0),
        preco_centavos INTEGER NOT NULL,
        valor_centavos INTEGER NOT NULL,
        saldo_apos_centavos INTEGER NOT NULL,
        origem TEXT NOT NULL,
        criado_em REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_ordens_investidor ON ordens(investidor_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_historico_ticker ON historico_precos(ticker, id)",
]


@contextmanager
def transacao(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transação de escrita: BEGIN IMMEDIATE ... COMMIT (ROLLBACK em erro)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        # Em alguns erros (disco cheio, IO) o SQLite já desfez a transação sozinho;
        # um ROLLBACK aí falharia e esconderia a exceção original.
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


class Database:
    def __init__(self, path: Path, journal_mode: str = "WAL", synchronous: str = "FULL",
                 busy_timeout_ms: int = 30000):
        self.path = Path(path)
        self.journal_mode = journal_mode
        self.synchronous = synchronous
        self.busy_timeout_ms = busy_timeout_ms

    # ------------------------------------------------------------ conexões
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000.0,
            isolation_level=None,  # autocommit; transações explícitas via BEGIN
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        conn.execute(f"PRAGMA synchronous = {self.synchronous}")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def conexao(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def escrita(self) -> Iterator[sqlite3.Connection]:
        """Conexão nova + ``BEGIN IMMEDIATE`` (COMMIT ao sair, ROLLBACK em exceção)."""
        with self.conexao() as conn, transacao(conn):
            yield conn

    # ------------------------------------------------------- schema / seed
    @staticmethod
    def _seed(conn: sqlite3.Connection, agora: float) -> None:
        for ticker, nome, preco in EMPRESAS:
            conn.execute(
                "INSERT OR IGNORE INTO acoes VALUES (?, ?, ?, ?, ?, ?)",
                (ticker, nome, preco, preco, preco, agora),
            )
        cur = conn.execute("INSERT OR IGNORE INTO mercado (id, tick) VALUES (1, 0)")
        if cur.rowcount:
            conn.executemany(
                "INSERT INTO historico_precos (ticker, tick, preco_centavos, ts) VALUES (?, 0, ?, ?)",
                [(t, p, agora) for t, _, p in EMPRESAS],
            )

    def init_db(self) -> None:
        """Cria diretório, aplica journal_mode e schema. Seguro com vários workers."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prazo = time.monotonic() + max(self.busy_timeout_ms / 1000.0, 10.0)
        while True:
            try:
                with self.conexao() as conn:
                    modo = conn.execute(f"PRAGMA journal_mode = {self.journal_mode}").fetchone()[0]
                    if modo.upper() != self.journal_mode:
                        raise RuntimeError(f"journal_mode={modo}, esperado {self.journal_mode}")
                    with transacao(conn):
                        for stmt in SCHEMA:
                            conn.execute(stmt)
                        self._seed(conn, time.time())
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or time.monotonic() > prazo:
                    raise
                time.sleep(0.05)

    def resetar(self, conn: sqlite3.Connection) -> None:
        """Apaga investidores/ordens/histórico e volta os preços ao inicial (dentro de transação)."""
        agora = time.time()
        for tabela in ("ordens", "posicoes", "investidores", "historico_precos"):
            conn.execute(f"DELETE FROM {tabela}")
        conn.execute("DELETE FROM sqlite_sequence")
        conn.execute("DELETE FROM mercado")
        conn.execute("DELETE FROM acoes")
        self._seed(conn, agora)

    @staticmethod
    def compactar(conn: sqlite3.Connection) -> None:
        """wal_checkpoint(TRUNCATE) + VACUUM, fora de transação; ignora se outra conexão estiver ativa."""
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
        except sqlite3.OperationalError:
            pass  # outra conexão ativa; não afeta a corretude

    # -------------------------------------------------------------- infos
    def ping(self) -> None:
        with self.conexao() as conn:
            conn.execute("SELECT 1").fetchone()

    def tamanho(self) -> dict:
        out = {}
        for sufixo in ("", "-wal", "-shm", "-journal"):
            p = Path(str(self.path) + sufixo)
            out["db" + sufixo.replace("-", "_") + "_bytes"] = p.stat().st_size if p.exists() else 0
        return out
