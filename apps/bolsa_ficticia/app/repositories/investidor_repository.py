"""SQL das tabelas ``investidores`` e ``posicoes``."""

from __future__ import annotations

import sqlite3


def obter(conn: sqlite3.Connection, investidor_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM investidores WHERE id = ?", (investidor_id,)).fetchone()


def inserir(conn: sqlite3.Connection, nome: str, saldo_centavos: int, agora: float) -> int:
    cur = conn.execute(
        "INSERT INTO investidores (nome, saldo_inicial_centavos, saldo_centavos, criado_em) VALUES (?, ?, ?, ?)",
        (nome, saldo_centavos, saldo_centavos, agora),
    )
    return cur.lastrowid


def atualizar_saldo(conn: sqlite3.Connection, investidor_id: int, saldo_centavos: int) -> None:
    conn.execute("UPDATE investidores SET saldo_centavos = ? WHERE id = ?", (saldo_centavos, investidor_id))


def contar(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM investidores").fetchone()[0]


# ------------------------------------------------------------------ posições
def obter_posicao(conn: sqlite3.Connection, investidor_id: int, ticker: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT quantidade, custo_centavos FROM posicoes WHERE investidor_id = ? AND ticker = ?",
        (investidor_id, ticker),
    ).fetchone()


def salvar_posicao(conn: sqlite3.Connection, investidor_id: int, ticker: str, quantidade: int,
                   custo_centavos: int) -> None:
    conn.execute(
        """INSERT INTO posicoes (investidor_id, ticker, quantidade, custo_centavos) VALUES (?, ?, ?, ?)
           ON CONFLICT (investidor_id, ticker) DO UPDATE SET
             quantidade = excluded.quantidade, custo_centavos = excluded.custo_centavos""",
        (investidor_id, ticker, quantidade, custo_centavos),
    )


def listar_posicoes_com_preco(conn: sqlite3.Connection, investidor_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT p.ticker, p.quantidade, p.custo_centavos, a.preco_centavos
           FROM posicoes p JOIN acoes a USING (ticker)
           WHERE p.investidor_id = ? AND p.quantidade > 0 ORDER BY p.ticker""",
        (investidor_id,),
    ).fetchall()
