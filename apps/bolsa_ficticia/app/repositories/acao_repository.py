"""SQL das tabelas ``acoes``, ``mercado`` e ``historico_precos``."""

from __future__ import annotations

import sqlite3


def obter_tick(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT tick FROM mercado WHERE id = 1").fetchone()[0]


def incrementar_tick(conn: sqlite3.Connection) -> int:
    conn.execute("UPDATE mercado SET tick = tick + 1 WHERE id = 1")
    return obter_tick(conn)


def listar(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM acoes ORDER BY ticker").fetchall()


def listar_precos(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT ticker, preco_centavos FROM acoes").fetchall()


def listar_tickers(conn: sqlite3.Connection) -> list[str]:
    return [r["ticker"] for r in conn.execute("SELECT ticker FROM acoes").fetchall()]


def obter(conn: sqlite3.Connection, ticker: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM acoes WHERE ticker = ?", (ticker,)).fetchone()


def atualizar_preco(conn: sqlite3.Connection, ticker: str, preco_centavos: int, agora: float) -> None:
    conn.execute(
        """UPDATE acoes SET preco_anterior_centavos = preco_centavos,
                            preco_centavos = ?, atualizado_em = ? WHERE ticker = ?""",
        (preco_centavos, agora, ticker),
    )


def inserir_historico(conn: sqlite3.Connection, ticker: str, tick: int, preco_centavos: int, agora: float) -> None:
    conn.execute(
        "INSERT INTO historico_precos (ticker, tick, preco_centavos, ts) VALUES (?, ?, ?, ?)",
        (ticker, tick, preco_centavos, agora),
    )
