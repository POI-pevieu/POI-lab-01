"""Carga para a Bolsa Fictícia (IO-bound, porta 8003).

Cada usuário Locust é um investidor: no on_start cria sua conta
(POST /investidores) e depois opera. Ordens executadas geram escrita no
SQLite (BEGIN IMMEDIATE + COMMIT) e uma linha com fsync no livro-caixa.

Variáveis de ambiente:
  LT_WAIT_MIN / LT_WAIT_MAX  espera entre tarefas por usuário, s (padrão 0.5 / 1.5)
  BOLSA_SALDO                saldo inicial de cada investidor, R$ (padrão 10000)
  BOLSA_OPERAR_WEIGHT        peso de POST /investidores/[id]/operar (estratégia)   (padrão 10)
  BOLSA_ORDEM_WEIGHT         peso de POST /ordens (ordem manual de 1..BOLSA_ORDEM_QTD_MAX) (padrão 2)
  BOLSA_TICK_WEIGHT          peso de POST /mercado/tick (move preços => muda tendências) (padrão 2)
  BOLSA_CARTEIRA_WEIGHT      peso de GET /investidores/[id]/carteira (padrão 3)
  BOLSA_ACOES_WEIGHT         peso de GET /acoes                       (padrão 3)
  BOLSA_ORDENS_WEIGHT        peso de GET /ordens?investidor_id=[id]   (padrão 2)
  BOLSA_ORDEM_QTD_MAX        quantidade máxima da ordem manual (padrão 10)
  BOLSA_ORDENS_LIMIT         limit da listagem de ordens (padrão 50)

O tick é uma tarefa ponderada do próprio investidor, então a taxa de ticks
cresce com a demanda (mais contenção de escrita, como num mercado mais ativo).

Validação: todo corpo é conferido; 409 (saldo/posição insuficiente) na ordem
manual é resposta de negócio válida e conta como sucesso no nome
"POST /ordens [rejeitada 409]". 503 = SQLite ocupado além do busy_timeout
(falha). Nomes agregados: /investidores/[id]/operar, /investidores/[id]/carteira,
/ordens?investidor_id=[id].
"""
from __future__ import annotations

import os
import random

from locust import HttpUser, between, task

WAIT_MIN = float(os.environ.get("LT_WAIT_MIN", "0.5"))
WAIT_MAX = float(os.environ.get("LT_WAIT_MAX", "1.5"))
SALDO = float(os.environ.get("BOLSA_SALDO", "10000"))
QTD_MAX = int(os.environ.get("BOLSA_ORDEM_QTD_MAX", "10"))
ORDENS_LIMIT = int(os.environ.get("BOLSA_ORDENS_LIMIT", "50"))
TICKERS = ["CAFE3", "MINA3", "TECN3", "ENER3"]


def _w(nome: str, padrao: int) -> int:
    return int(os.environ.get(nome, padrao))


def _ordem_ok(o: dict, inv: int) -> bool:
    return (o["investidor_id"] == inv and o["tipo"] in ("compra", "venda") and o["quantidade"] > 0
            and o["valor_centavos"] == o["preco_centavos"] * o["quantidade"] and o["saldo_apos_centavos"] >= 0)


class Investidor(HttpUser):
    wait_time = between(WAIT_MIN, WAIT_MAX)
    inv_id: int | None = None

    def on_start(self):
        nome = f"locust-{random.getrandbits(32):08x}"
        with self.client.post("/investidores", json={"nome": nome, "saldo_inicial": SALDO},
                              name="/investidores", catch_response=True) as r:
            try:
                b = r.json()
                if r.status_code != 201 or b["nome"] != nome or b["saldo_centavos"] != round(SALDO * 100):
                    r.failure(f"criação inválida: {r.status_code} {r.text[:200]}")
                    return
                self.inv_id = b["id"]
                r.success()
            except Exception as e:  # noqa: BLE001
                r.failure(f"corpo inválido: {e!r} {r.text[:200]}")

    @task(_w("BOLSA_OPERAR_WEIGHT", 10))
    def operar(self):
        if self.inv_id is None:
            return
        with self.client.post(f"/investidores/{self.inv_id}/operar", name="/investidores/[id]/operar",
                              catch_response=True) as r:
            try:
                b = r.json()
                ok = (r.status_code == 200 and b["investidor_id"] == self.inv_id
                      and b["decisao"] in ("compra", "venda", "manter") and b["ticker"] in TICKERS)
                if ok and b["decisao"] == "manter":
                    ok = b["ordem"] is None
                elif ok:
                    o = b["ordem"]
                    esperado = {"compra": "alta", "venda": "baixa"}[b["decisao"]]
                    ok = b["tendencia"] == esperado and o["tipo"] == b["decisao"] and _ordem_ok(o, self.inv_id)
                r.success() if ok else r.failure(f"resposta inválida: {r.status_code} {r.text[:200]}")
            except Exception as e:  # noqa: BLE001
                r.failure(f"corpo inválido: {e!r} {r.text[:200]}")

    @task(_w("BOLSA_ORDEM_WEIGHT", 2))
    def ordem_manual(self):
        if self.inv_id is None:
            return
        body = {"investidor_id": self.inv_id, "ticker": random.choice(TICKERS),
                "tipo": random.choice(["compra", "venda"]), "quantidade": random.randint(1, QTD_MAX)}
        with self.client.post("/ordens", json=body, name="/ordens [POST]", catch_response=True) as r:
            try:
                if r.status_code == 409 and "insuficiente" in r.json()["detail"]:
                    r.success()  # regra de negócio respeitada
                    return
                o = r.json()
                ok = (r.status_code == 201 and _ordem_ok(o, self.inv_id) and o["ticker"] == body["ticker"]
                      and o["tipo"] == body["tipo"] and o["quantidade"] == body["quantidade"])
                r.success() if ok else r.failure(f"resposta inválida: {r.status_code} {r.text[:200]}")
            except Exception as e:  # noqa: BLE001
                r.failure(f"corpo inválido: {e!r} {r.text[:200]}")

    @task(_w("BOLSA_TICK_WEIGHT", 2))
    def tick(self):
        with self.client.post("/mercado/tick", name="/mercado/tick", catch_response=True) as r:
            try:
                b = r.json()
                ok = (r.status_code == 200 and b["tick"] >= 1 and len(b["acoes"]) == 4
                      and all(a["preco_centavos"] > 0 for a in b["acoes"]))
                r.success() if ok else r.failure(f"resposta inválida: {r.status_code} {r.text[:200]}")
            except Exception as e:  # noqa: BLE001
                r.failure(f"corpo inválido: {e!r} {r.text[:200]}")

    @task(_w("BOLSA_CARTEIRA_WEIGHT", 3))
    def carteira(self):
        if self.inv_id is None:
            return
        with self.client.get(f"/investidores/{self.inv_id}/carteira", name="/investidores/[id]/carteira",
                             catch_response=True) as r:
            try:
                b = r.json()
                soma = sum(p["valor_mercado_centavos"] for p in b["posicoes"])
                ok = (r.status_code == 200 and b["id"] == self.inv_id and b["saldo_centavos"] >= 0
                      and b["valor_acoes_centavos"] == soma
                      and b["patrimonio_centavos"] == b["saldo_centavos"] + soma)
                r.success() if ok else r.failure(f"resposta inválida: {r.status_code} {r.text[:200]}")
            except Exception as e:  # noqa: BLE001
                r.failure(f"corpo inválido: {e!r} {r.text[:200]}")

    @task(_w("BOLSA_ACOES_WEIGHT", 3))
    def acoes(self):
        with self.client.get("/acoes", name="/acoes", catch_response=True) as r:
            try:
                b = r.json()
                ok = r.status_code == 200 and sorted(a["ticker"] for a in b["acoes"]) == sorted(TICKERS)
                r.success() if ok else r.failure(f"resposta inválida: {r.status_code} {r.text[:200]}")
            except Exception as e:  # noqa: BLE001
                r.failure(f"corpo inválido: {e!r} {r.text[:200]}")

    @task(_w("BOLSA_ORDENS_WEIGHT", 2))
    def ordens(self):
        if self.inv_id is None:
            return
        with self.client.get("/ordens", params={"investidor_id": self.inv_id, "limit": ORDENS_LIMIT},
                             name="/ordens?investidor_id=[id]", catch_response=True) as r:
            try:
                lista = r.json()["ordens"]
                ok = (r.status_code == 200 and len(lista) <= ORDENS_LIMIT
                      and all(_ordem_ok(o, self.inv_id) for o in lista)
                      and [o["id"] for o in lista] == sorted((o["id"] for o in lista), reverse=True))
                r.success() if ok else r.failure(f"resposta inválida: {r.status_code} {r.text[:200]}")
            except Exception as e:  # noqa: BLE001
                r.failure(f"corpo inválido: {e!r} {r.text[:200]}")
