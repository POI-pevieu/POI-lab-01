"""Sobe um servidor REAL (gunicorn, 2 workers) com DATA_DIR temporário.

Os testes falam HTTP com ele e conferem invariantes lendo diretamente o
arquivo SQLite e o livro-caixa gravados em disco pelo servidor.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

APP_DIR = Path(__file__).resolve().parent.parent


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def servidor(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("bolsa_data")
    porta = _porta_livre()
    env = {**os.environ, "HOST": "127.0.0.1", "PORT": str(porta), "WORKERS": "2",
           "DATA_DIR": str(data_dir), "LOG_LEVEL": "warning"}
    log = open(data_dir.parent / "servidor.log", "w+")
    proc = subprocess.Popen([sys.executable, "-m", "gunicorn", "-c", "gunicorn.conf.py"],
                            cwd=APP_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{porta}"
    prazo = time.monotonic() + 30
    while True:
        if proc.poll() is not None:
            log.seek(0)
            raise RuntimeError(f"servidor morreu:\n{log.read()}")
        try:
            if httpx.get(base + "/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > prazo:
            proc.kill()
            raise RuntimeError("servidor não respondeu /health")
        time.sleep(0.2)
    yield {"base": base, "data_dir": data_dir}
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


@pytest.fixture
def api(servidor):
    with httpx.Client(base_url=servidor["base"], timeout=60) as c:
        r = c.post("/admin/reset")
        assert r.status_code == 200, r.text
        yield c


@pytest.fixture
def disco(servidor):
    return Disco(servidor["data_dir"])


class Disco:
    """Leitura direta dos arquivos gravados pelo servidor."""

    def __init__(self, data_dir: Path):
        self.db_path = data_dir / "bolsa.db"
        self.ledger_path = data_dir / "livro_caixa.ledger"

    def query(self, sql: str, args=()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def ledger(self) -> list[dict]:
        raw = self.ledger_path.read_bytes()
        assert raw == b"" or raw.endswith(b"\n"), "livro-caixa termina com linha incompleta"
        return [json.loads(linha) for linha in raw.decode().splitlines()]  # falha se linha corrompida

    def checar_invariantes(self) -> dict:
        ordens = {r["id"]: dict(r) for r in self.query("SELECT * FROM ordens")}
        linhas = self.ledger()

        # 1) cada ordem tem exatamente uma linha no livro, com os mesmos dados
        ids_ledger = [l["ordem_id"] for l in linhas]
        assert len(ids_ledger) == len(set(ids_ledger)), "ordem duplicada no livro-caixa"
        assert set(ids_ledger) == set(ordens), "livro-caixa e tabela ordens divergem"
        assert ids_ledger == sorted(ids_ledger), "livro-caixa fora da ordem de execução"
        for l in linhas:
            o = ordens[l["ordem_id"]]
            for campo in ("investidor_id", "ticker", "tipo", "quantidade", "preco_centavos",
                          "valor_centavos", "saldo_apos_centavos", "origem"):
                assert l[campo] == o[campo], (campo, l, o)
            assert o["valor_centavos"] == o["preco_centavos"] * o["quantidade"]

        # 2) saldo e posições batem com o histórico de ordens
        for inv in self.query("SELECT * FROM investidores"):
            assert inv["saldo_centavos"] >= 0
            minhas = sorted((o for o in ordens.values() if o["investidor_id"] == inv["id"]), key=lambda o: o["id"])
            fluxo = sum(-o["valor_centavos"] if o["tipo"] == "compra" else o["valor_centavos"] for o in minhas)
            assert inv["saldo_inicial_centavos"] + fluxo == inv["saldo_centavos"], f"saldo inconsistente {dict(inv)}"
            # saldo_apos de cada ordem reproduz a sequência
            saldo = inv["saldo_inicial_centavos"]
            for o in minhas:
                saldo += -o["valor_centavos"] if o["tipo"] == "compra" else o["valor_centavos"]
                assert saldo >= 0 and saldo == o["saldo_apos_centavos"]
            qtd: dict[str, int] = {}
            for o in minhas:
                qtd[o["ticker"]] = qtd.get(o["ticker"], 0) + (o["quantidade"] if o["tipo"] == "compra" else -o["quantidade"])
                assert qtd[o["ticker"]] >= 0, "vendeu mais do que possuía"
            pos = {p["ticker"]: p for p in self.query("SELECT * FROM posicoes WHERE investidor_id = ?", (inv["id"],))}
            for t, q in qtd.items():
                assert pos[t]["quantidade"] == q
                assert pos[t]["custo_centavos"] >= 0
            for t, p in pos.items():
                assert p["quantidade"] == qtd.get(t, 0)
        return {"ordens": len(ordens), "linhas": len(linhas)}
