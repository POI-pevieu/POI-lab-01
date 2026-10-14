"""SQL da tabela ``ordens``."""

from __future__ import annotations

import sqlite3


def inserir(conn: sqlite3.Connection, investidor_id: int, ticker: str, tipo: str, quantidade: int,
            preco_centavos: int, valor_centavos: int, saldo_apos_centavos: int, origem: str,
            agora: float) -> int:
    cur = conn.execute(
        """INSERT INTO ordens (investidor_id, ticker, tipo, quantidade, preco_centavos, valor_centavos,
                               saldo_apos_centavos, origem, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (investidor_id, ticker, tipo, quantidade, preco_centavos, valor_centavos, saldo_apos_centavos, origem, agora),
    )
    return cur.lastrowid


def listar(conn: sqlite3.Connection, investidor_id: int | None, limit: int) -> list[sqlite3.Row]:
    if investidor_id is None:
        return conn.execute("SELECT * FROM ordens ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return conn.execute(
        "SELECT * FROM ordens WHERE investidor_id = ? ORDER BY id DESC LIMIT ?", (investidor_id, limit)
    ).fetchall()


def contar(conn: sqlite3.Connection, investidor_id: int | None = None) -> int:
    if investidor_id is None:
        return conn.execute("SELECT COUNT(*) FROM ordens").fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM ordens WHERE investidor_id = ?", (investidor_id,)).fetchone()[0]
