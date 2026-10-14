"""Testes funcionais da Bolsa Fictícia contra servidor real (sem mocks)."""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor

import httpx

TICKERS = {"CAFE3", "MINA3", "TECN3", "ENER3"}


def novo_investidor(api, nome="Ana", saldo=10000.0) -> int:
    r = api.post("/investidores", json={"nome": nome, "saldo_inicial": saldo})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def definir_preco(api, ticker, preco):
    r = api.post("/admin/precos", json={"ticker": ticker, "preco": preco})
    assert r.status_code == 200, r.text


def test_health_e_acoes(api):
    h = api.get("/health").json()
    assert h["status"] == "ok" and h["journal_mode"] == "WAL" and h["ledger_fsync"] is True
    body = api.get("/acoes").json()
    assert {a["ticker"] for a in body["acoes"]} == TICKERS
    assert all(a["tendencia"] == "estavel" and a["preco"] > 0 for a in body["acoes"])
    assert body["tick"] == 0


def test_tick_move_precos_e_grava_historico(api, disco):
    antes = {a["ticker"]: a["preco_centavos"] for a in api.get("/acoes").json()["acoes"]}
    r = api.post("/mercado/tick", json={"passos": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["tick"] == 5
    depois = {a["ticker"]: a for a in body["acoes"]}
    assert any(depois[t]["preco_centavos"] != antes[t] for t in TICKERS)
    for a in depois.values():
        esperado = ("alta" if a["preco_centavos"] > a["preco_anterior_centavos"] else
                    "baixa" if a["preco_centavos"] < a["preco_anterior_centavos"] else "estavel")
        assert a["tendencia"] == esperado
    # histórico: 4 preços iniciais + 4 por tick
    assert disco.query("SELECT COUNT(*) c FROM historico_precos")[0]["c"] == 4 + 4 * 5
    ultimo = {r["ticker"]: r["preco_centavos"] for r in
              disco.query("SELECT ticker, preco_centavos FROM historico_precos WHERE tick = 5")}
    assert ultimo == {t: a["preco_centavos"] for t, a in depois.items()}


def test_estrategia_compra_na_alta_vende_na_baixa(api, disco):
    inv = novo_investidor(api)
    # estável -> mantém, sem gravar nada
    r = api.post(f"/investidores/{inv}/operar", params={"ticker": "CAFE3"}).json()
    assert r["decisao"] == "manter" and r["ordem"] is None

    # alta -> compra 20% do saldo
    definir_preco(api, "CAFE3", 40.00)
    r = api.post(f"/investidores/{inv}/operar", params={"ticker": "CAFE3"}).json()
    assert r["tendencia"] == "alta" and r["decisao"] == "compra"
    assert r["ordem"]["quantidade"] == 50 and r["ordem"]["valor_centavos"] == 200000

    # baixa sem posição em outro papel -> mantém
    definir_preco(api, "MINA3", 50.00)
    r = api.post(f"/investidores/{inv}/operar", params={"ticker": "MINA3"}).json()
    assert r["tendencia"] == "baixa" and r["decisao"] == "manter"

    # baixa com posição -> vende tudo
    definir_preco(api, "CAFE3", 38.00)
    r = api.post(f"/investidores/{inv}/operar", params={"ticker": "CAFE3"}).json()
    assert r["decisao"] == "venda" and r["ordem"]["quantidade"] == 50
    assert r["ordem"]["valor_centavos"] == 50 * 3800

    cart = api.get(f"/investidores/{inv}/carteira").json()
    assert cart["saldo_centavos"] == 1000000 - 200000 + 190000
    assert cart["posicoes"] == [] and cart["n_ordens"] == 2
    assert disco.checar_invariantes() == {"ordens": 2, "linhas": 2}


def test_ordem_manual_e_carteira(api, disco):
    inv = novo_investidor(api, saldo=1000.0)
    definir_preco(api, "TECN3", 10.00)
    r = api.post("/ordens", json={"investidor_id": inv, "ticker": "tecn3", "tipo": "compra", "quantidade": 30})
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["ticker"] == "TECN3" and o["valor"] == 300.0 and o["saldo_apos"] == 700.0 and o["origem"] == "manual"
    definir_preco(api, "TECN3", 12.00)
    cart = api.get(f"/investidores/{inv}/carteira").json()
    assert cart["posicoes"][0]["quantidade"] == 30
    assert cart["valor_acoes_centavos"] == 30 * 1200
    assert cart["patrimonio_centavos"] == 70000 + 36000
    assert api.post("/ordens", json={"investidor_id": inv, "ticker": "TECN3", "tipo": "venda",
                                     "quantidade": 10}).status_code == 201
    lista = api.get("/ordens", params={"investidor_id": inv, "limit": 10}).json()["ordens"]
    assert [x["tipo"] for x in lista] == ["venda", "compra"]
    ledger = disco.ledger()
    assert [l["ordem_id"] for l in ledger] == [lista[1]["id"], lista[0]["id"]]
    assert disco.checar_invariantes()["ordens"] == 2


def test_ordens_invalidas_rejeitadas(api, disco):
    inv = novo_investidor(api, saldo=100.0)
    casos = [
        ({"investidor_id": inv, "ticker": "CAFE3", "tipo": "compra", "quantidade": 1000}, 409),  # sem saldo
        ({"investidor_id": inv, "ticker": "CAFE3", "tipo": "venda", "quantidade": 1}, 409),      # sem ações
        ({"investidor_id": inv, "ticker": "XXXX3", "tipo": "compra", "quantidade": 1}, 404),
        ({"investidor_id": 99999, "ticker": "CAFE3", "tipo": "compra", "quantidade": 1}, 404),
        ({"investidor_id": inv, "ticker": "CAFE3", "tipo": "doacao", "quantidade": 1}, 422),
        ({"investidor_id": inv, "ticker": "CAFE3", "tipo": "compra", "quantidade": 0}, 422),
        ({"investidor_id": inv, "ticker": "CAFE3", "tipo": "compra", "quantidade": -5}, 422),
        ({"investidor_id": inv, "ticker": "CAFE3", "tipo": "compra"}, 422),
    ]
    for body, status in casos:
        r = api.post("/ordens", json=body)
        assert r.status_code == status, (body, r.status_code, r.text)
    assert api.post("/investidores", json={"nome": "", "saldo_inicial": 10}).status_code == 422
    assert api.post("/investidores", json={"nome": "x", "saldo_inicial": -1}).status_code == 422
    assert api.post("/investidores/99999/operar").status_code == 404
    assert api.get("/investidores/99999/carteira").status_code == 404
    assert api.post("/admin/precos", json={"ticker": "NOPE3", "preco": 10}).status_code == 404
    # nada foi gravado e o saldo ficou intacto
    assert disco.checar_invariantes() == {"ordens": 0, "linhas": 0}
    assert api.get(f"/investidores/{inv}").json()["saldo_centavos"] == 10000


def test_compras_concorrentes_nao_estouram_saldo(api, disco):
    """20 compras simultâneas de R$1000 com saldo de R$10000: exatamente 10 passam."""
    inv = novo_investidor(api, saldo=10000.0)
    definir_preco(api, "MINA3", 100.00)
    body = {"investidor_id": inv, "ticker": "MINA3", "tipo": "compra", "quantidade": 10}

    def comprar(_):
        with httpx.Client(base_url=str(api.base_url), timeout=60) as c:
            return c.post("/ordens", json=body).status_code

    with ThreadPoolExecutor(20) as ex:
        status = list(ex.map(comprar, range(20)))
    assert sorted(status) == [201] * 10 + [409] * 10
    cart = api.get(f"/investidores/{inv}/carteira").json()
    assert cart["saldo_centavos"] == 0 and cart["posicoes"][0]["quantidade"] == 100
    assert disco.checar_invariantes() == {"ordens": 10, "linhas": 10}


def test_operacoes_concorrentes_sem_perda_nem_corrupcao(api, disco):
    """Muitas threads operando + ticks simultâneos; nenhuma escrita perdida."""
    invs = [novo_investidor(api, nome=f"inv{i}") for i in range(10)]
    n_ops = 400

    def tarefa(i):
        rnd = random.Random(i)
        with httpx.Client(base_url=str(api.base_url), timeout=60) as c:
            if i % 10 == 0:
                r = c.post("/mercado/tick")
                assert r.status_code == 200, r.text
                return None
            if i % 7 == 0:
                r = c.post("/ordens", json={"investidor_id": rnd.choice(invs), "ticker": rnd.choice(sorted(TICKERS)),
                                            "tipo": rnd.choice(["compra", "venda"]), "quantidade": rnd.randint(1, 20)})
                assert r.status_code in (201, 409), r.text
                return r.json()["id"] if r.status_code == 201 else None
            r = c.post(f"/investidores/{rnd.choice(invs)}/operar")
            assert r.status_code == 200, r.text
            o = r.json()["ordem"]
            return o["id"] if o else None

    # garante tendência para haver compras desde o início
    api.post("/mercado/tick", json={"passos": 1})
    with ThreadPoolExecutor(32) as ex:
        ids = [x for x in ex.map(tarefa, range(n_ops)) if x is not None]

    assert len(ids) > 50, "poucas ordens executadas — teste não exercitou concorrência"
    assert len(ids) == len(set(ids))
    res = disco.checar_invariantes()
    assert res["ordens"] == res["linhas"] == len(ids)
    stats = api.get("/livro-caixa/stats").json()
    assert stats["livro_caixa"]["linhas"] == len(ids) == stats["banco"]["ordens"]
    assert stats["livro_caixa"]["tamanho_bytes"] == disco.ledger_path.stat().st_size


def test_reset_zera_banco_e_livro(api, disco):
    inv = novo_investidor(api)
    definir_preco(api, "ENER3", 50.00)
    assert api.post(f"/investidores/{inv}/operar", params={"ticker": "ENER3"}).json()["decisao"] == "compra"
    assert disco.ledger_path.stat().st_size > 0
    assert api.post("/admin/reset").status_code == 200
    assert disco.ledger_path.stat().st_size == 0
    assert disco.query("SELECT COUNT(*) c FROM ordens")[0]["c"] == 0
    assert disco.query("SELECT COUNT(*) c FROM investidores")[0]["c"] == 0
    acoes = api.get("/acoes").json()
    assert acoes["tick"] == 0 and all(a["tendencia"] == "estavel" for a in acoes["acoes"])
    # ids recomeçam
    assert novo_investidor(api) == 1
