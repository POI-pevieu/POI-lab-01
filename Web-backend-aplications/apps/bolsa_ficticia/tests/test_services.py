"""Testes de unidade dos services contra SQLite + livro-caixa REAIS em tmp_path (sem mocks)."""

from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.exceptions import (AcaoNaoEncontrada, InvestidorNaoEncontrado, OrdemInvalida,  # noqa: E402
                            PosicaoInsuficiente, SaldoInsuficiente)
from app.services import Servicos, construir_servicos  # noqa: E402


@pytest.fixture
def svc(tmp_path) -> Servicos:
    s = construir_servicos(Settings(data_dir=tmp_path))
    s.inicializar()
    return s


def ledger_linhas(s: Servicos) -> list[dict]:
    return [json.loads(l) for l in s.ledger.path.read_text().splitlines()]


def contar(s: Servicos, tabela: str) -> int:
    with s.db.conexao() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]


def test_settings_from_env_e_validacao(tmp_path):
    st = Settings.from_env({"DATA_DIR": str(tmp_path), "SQLITE_SYNCHRONOUS": "normal", "LEDGER_FSYNC": "0"})
    assert st.data_dir == tmp_path.resolve() and st.synchronous == "NORMAL" and st.ledger_fsync is False
    assert st.db_path.name == "bolsa.db" and st.ledger_path.name == "livro_caixa.ledger"
    with pytest.raises(RuntimeError):
        Settings.from_env({"DATA_DIR": str(tmp_path), "SQLITE_JOURNAL_MODE": "MEMORY"})


def test_inicializar_cria_schema_seed_e_wal(svc):
    assert svc.db.path.exists() and svc.ledger.path.exists() and svc.ledger.path.stat().st_size == 0
    with svc.db.conexao() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    acoes = svc.mercado.listar_acoes()
    assert acoes["tick"] == 0 and {a["ticker"] for a in acoes["acoes"]} == {"CAFE3", "MINA3", "TECN3", "ENER3"}
    svc.inicializar()  # idempotente (vários workers)
    assert contar(svc, "historico_precos") == 4
    assert svc.health.status()["status"] == "ok"


def test_tick_e_definir_preco(svc):
    r = svc.mercado.tick(3)
    assert r["tick"] == 3 and contar(svc, "historico_precos") == 4 + 4 * 3
    r = svc.admin.definir_preco("CAFE3", 99999)
    cafe = next(a for a in r["acoes"] if a["ticker"] == "CAFE3")
    assert r["tick"] == 4 and cafe["preco_centavos"] == 99999
    with pytest.raises(AcaoNaoEncontrada):
        svc.admin.definir_preco("NOPE3", 1000)
    assert svc.mercado.listar_acoes()["tick"] == 4  # rollback: tick não avançou


def test_investidor_criar_obter_e_nao_encontrado(svc):
    inv = svc.investidores.criar("Ana", 123456)
    assert inv["id"] == 1 and inv["saldo_centavos"] == 123456 and inv["saldo"] == 1234.56
    assert svc.investidores.obter(1)["nome"] == "Ana"
    for f in (svc.investidores.obter, svc.investidores.carteira):
        with pytest.raises(InvestidorNaoEncontrado, match="investidor 42 não encontrado"):
            f(42)


def test_ordem_compra_venda_grava_banco_e_ledger(svc):
    inv = svc.investidores.criar("Bia", 100000)["id"]
    svc.admin.definir_preco("TECN3", 1000)
    o1 = svc.ordens.executar(inv, "TECN3", "compra", 30)
    assert o1["valor_centavos"] == 30000 and o1["saldo_apos_centavos"] == 70000 and o1["origem"] == "manual"
    o2 = svc.ordens.executar(inv, "TECN3", "venda", 10)
    assert o2["saldo_apos_centavos"] == 80000
    cart = svc.investidores.carteira(inv)
    assert cart["posicoes"][0]["quantidade"] == 20 and cart["n_ordens"] == 2
    assert cart["posicoes"][0]["custo_centavos"] == 20000
    linhas = ledger_linhas(svc)
    assert [l["ordem_id"] for l in linhas] == [o1["id"], o2["id"]]
    assert linhas[0]["pid"] == os.getpid() and linhas[0]["valor_centavos"] == 30000
    assert [o["id"] for o in svc.ordens.listar(inv, 10)] == [o2["id"], o1["id"]]
    stats = svc.livro_caixa.estatisticas()
    assert stats["livro_caixa"]["linhas"] == 2 == stats["banco"]["ordens"]
    assert stats["livro_caixa"]["processo"]["fsyncs"] == 2


@pytest.mark.parametrize("args,erro", [
    ((1, "CAFE3", "compra", 1000), SaldoInsuficiente),
    ((1, "CAFE3", "venda", 1), PosicaoInsuficiente),
    ((1, "XXXX3", "compra", 1), AcaoNaoEncontrada),
    ((999, "CAFE3", "compra", 1), InvestidorNaoEncontrado),
    ((1, "CAFE3", "doacao", 1), OrdemInvalida),
    ((1, "CAFE3", "compra", 0), OrdemInvalida),
])
def test_ordens_rejeitadas_nao_gravam_nada(svc, args, erro):
    svc.investidores.criar("Caio", 10000)
    with pytest.raises(erro):
        svc.ordens.executar(*args)
    assert contar(svc, "ordens") == 0 and svc.ledger.path.stat().st_size == 0
    assert svc.investidores.obter(1)["saldo_centavos"] == 10000


def test_falha_no_ledger_desfaz_transacao(svc):
    """Se a escrita/fsync do livro-caixa falha, a ordem não é commitada."""
    inv = svc.investidores.criar("Duda", 100000)["id"]
    svc.ledger.path.unlink()
    svc.ledger.path.mkdir()  # os.open(O_WRONLY) num diretório -> OSError real
    with pytest.raises(OSError):
        svc.ordens.executar(inv, "CAFE3", "compra", 1)
    assert contar(svc, "ordens") == 0 and contar(svc, "posicoes") == 0
    assert svc.investidores.obter(inv)["saldo_centavos"] == 100000


def test_estrategia_compra_na_alta_vende_na_baixa(svc):
    inv = svc.investidores.criar("Eva", 1000000)["id"]
    assert svc.estrategia.operar(inv, "CAFE3")["decisao"] == "manter"
    svc.admin.definir_preco("CAFE3", 4000)
    r = svc.estrategia.operar(inv, "CAFE3")
    assert r["decisao"] == "compra" and r["ordem"]["quantidade"] == 50 and r["ordem"]["origem"] == "estrategia"
    svc.admin.definir_preco("MINA3", 5000)
    assert svc.estrategia.operar(inv, "MINA3")["motivo"] == "preço caindo, mas sem posição para vender"
    svc.admin.definir_preco("CAFE3", 3800)
    r = svc.estrategia.operar(inv, "CAFE3")
    assert r["decisao"] == "venda" and r["ordem"]["valor_centavos"] == 50 * 3800
    assert svc.investidores.carteira(inv)["saldo_centavos"] == 1000000 - 200000 + 190000
    assert len(ledger_linhas(svc)) == 2
    assert svc.estrategia.operar(inv, None)["ticker"] in {"CAFE3", "MINA3", "TECN3", "ENER3"}
    with pytest.raises(InvestidorNaoEncontrado):
        svc.estrategia.operar(999, None)


def test_compras_concorrentes_threads_nao_estouram_saldo(svc):
    inv = svc.investidores.criar("Fábio", 1000000)["id"]
    svc.admin.definir_preco("MINA3", 10000)

    def comprar(_):
        try:
            return svc.ordens.executar(inv, "MINA3", "compra", 10)["id"]
        except SaldoInsuficiente:
            return None

    with ThreadPoolExecutor(16) as ex:
        ids = [x for x in ex.map(comprar, range(20)) if x is not None]
    assert len(ids) == 10
    assert svc.investidores.obter(inv)["saldo_centavos"] == 0
    assert [l["ordem_id"] for l in ledger_linhas(svc)] == sorted(ids)


def test_operacoes_concorrentes_mistas_ledger_bate_com_ordens(svc):
    invs = [svc.investidores.criar(f"i{i}", 1000000)["id"] for i in range(5)]
    svc.mercado.tick()

    def tarefa(i):
        rnd = random.Random(i)
        if i % 10 == 0:
            svc.mercado.tick()
        elif i % 3 == 0:
            try:
                svc.ordens.executar(rnd.choice(invs), "ENER3", rnd.choice(["compra", "venda"]), rnd.randint(1, 5))
            except (SaldoInsuficiente, PosicaoInsuficiente):
                pass
        else:
            svc.estrategia.operar(rnd.choice(invs), None)

    with ThreadPoolExecutor(16) as ex:
        list(ex.map(tarefa, range(200)))
    with svc.db.conexao() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM ordens ORDER BY id")]
    assert [l["ordem_id"] for l in ledger_linhas(svc)] == ids


def test_reset_zera_banco_e_ledger(svc):
    inv = svc.investidores.criar("Gil", 100000)["id"]
    svc.ordens.executar(inv, "CAFE3", "compra", 1)
    svc.mercado.tick(2)
    r = svc.admin.reset()
    assert r["status"] == "ok" and "db_bytes" in r
    assert svc.ledger.path.stat().st_size == 0 and contar(svc, "ordens") == 0
    assert svc.mercado.listar_acoes()["tick"] == 0 and contar(svc, "historico_precos") == 4
    assert svc.investidores.criar("Hugo", 1)["id"] == 1
    assert svc.livro_caixa.estatisticas()["livro_caixa"]["processo"]["escritas"] == 0
    with sqlite3.connect(svc.db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM investidores").fetchone()[0] == 1
