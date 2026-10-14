"""Carga para a Galeria de Filtros (memory-bound, porta 8002).

Variaveis de ambiente (knobs de intensidade):
  LT_WAIT_MIN / LT_WAIT_MAX   espera entre tarefas por usuario, s (padrao 0.5 / 1.5)
  GALERIA_RES_MIN / _MAX      faixa de resolucao (px) das imagens criadas (padrao 500 / 2000)
  GALERIA_RESOLUCAO           se definido, fixa a resolucao (sobrepoe MIN/MAX)
  GALERIA_ID_SPACE            quantidade de ids distintos (padrao 32). Junto com CACHE_MAX_MB
                              do servidor controla a taxa de acerto: se ID_SPACE * bytes medios
                              >> capacidade, haverá muitos misses/despejos.
  GALERIA_MOSAICO_K           imagens por mosaico (padrao 4)
  GALERIA_W_CRIAR / _FILTRO / _MOSAICO / _STATS   pesos das tarefas (padrao 3 / 6 / 1 / 1)

Nomes agregados no relatorio:
  POST /imagens/[id]
  GET  /imagens/[id]/filtro?tipo=negativo|cinza          (hit: 200)
  GET  /imagens/[id]/filtro?tipo=... [miss 404]          (miss esperado: id ainda nao criado ou
                                                          despejado — contado como sucesso)
  GET  /mosaico?quantidade=[k]                           (e "[vazio 409]" se o cache estiver vazio)
  GET  /cache/stats
Qualquer outro status ou corpo invalido e falha.
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, task


def _env_int(nome: str, padrao: int) -> int:
    return int(os.environ.get(nome, padrao))


WAIT_MIN = float(os.environ.get("LT_WAIT_MIN", "0.5"))
WAIT_MAX = float(os.environ.get("LT_WAIT_MAX", "1.5"))
if os.environ.get("GALERIA_RESOLUCAO"):
    RES_MIN = RES_MAX = _env_int("GALERIA_RESOLUCAO", 1000)
else:
    RES_MIN = _env_int("GALERIA_RES_MIN", 500)
    RES_MAX = _env_int("GALERIA_RES_MAX", 2000)
ID_SPACE = _env_int("GALERIA_ID_SPACE", 32)
MOSAICO_K = _env_int("GALERIA_MOSAICO_K", 4)
W_CRIAR = _env_int("GALERIA_W_CRIAR", 3)
W_FILTRO = _env_int("GALERIA_W_FILTRO", 6)
W_MOSAICO = _env_int("GALERIA_W_MOSAICO", 1)
W_STATS = _env_int("GALERIA_W_STATS", 1)

if not (500 <= RES_MIN <= RES_MAX <= 4000):
    raise ValueError("GALERIA_RES_MIN/MAX devem satisfazer 500 <= MIN <= MAX <= 4000")


def _id() -> str:
    return f"img{random.randrange(ID_SPACE)}"


class GaleriaUser(HttpUser):
    wait_time = between(WAIT_MIN, WAIT_MAX)

    @task(W_CRIAR)
    def criar(self):
        iid, res = _id(), random.randint(RES_MIN, RES_MAX)
        with self.client.post(f"/imagens/{iid}", params={"resolucao": res},
                              name="POST /imagens/[id]", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}: {r.text[:200]}")
                return
            try:
                j = r.json()
                ok = j["id"] == iid and j["shape"] == [res, res, 3] and j["bytes"] == res * res * 3
            except (ValueError, KeyError, TypeError) as e:
                r.failure(f"corpo invalido: {e}")
                return
            if not ok:
                r.failure(f"corpo inconsistente: {r.text[:200]}")

    @task(W_FILTRO)
    def filtro(self):
        iid, tipo = _id(), random.choice(("negativo", "cinza"))
        nome = f"GET /imagens/[id]/filtro?tipo={tipo}"
        with self.client.get(f"/imagens/{iid}/filtro", params={"tipo": tipo},
                             name=nome, catch_response=True) as r:
            if r.status_code == 404:
                # miss de cache esperado: agrupa separadamente para nao misturar latencias
                r.request_meta["name"] = nome + " [miss 404]"
                if "cache" in r.text:
                    r.success()
                else:
                    r.failure(f"404 inesperado: {r.text[:200]}")
                return
            if r.status_code != 200:
                r.failure(f"status {r.status_code}: {r.text[:200]}")
                return
            try:
                j = r.json()
                f, o = j["filtrada"], j["original"]
                n = o["shape"][0]
                esperado = [n, n, 3] if tipo == "negativo" else [n, n]
                ok = (j["id"] == iid and f["shape"] == esperado
                      and 0.0 <= f["media"] <= 255.0 and isinstance(f["checksum_crc32"], int))
            except (ValueError, KeyError, TypeError, IndexError) as e:
                r.failure(f"corpo invalido: {e}")
                return
            if not ok:
                r.failure(f"corpo inconsistente: {r.text[:200]}")

    @task(W_MOSAICO)
    def mosaico(self):
        nome = "GET /mosaico?quantidade=[k]"
        with self.client.get("/mosaico", params={"quantidade": MOSAICO_K},
                             name=nome, catch_response=True) as r:
            if r.status_code == 409:
                r.request_meta["name"] = nome + " [vazio 409]"
                r.success()
                return
            if r.status_code != 200:
                r.failure(f"status {r.status_code}: {r.text[:200]}")
                return
            try:
                j = r.json()
                h, w, c = j["shape"]
                ok = (1 <= j["quantidade_usada"] <= MOSAICO_K and c == 3
                      and j["bytes"] == h * w * c and len(j["ids"]) == j["quantidade_usada"])
            except (ValueError, KeyError, TypeError) as e:
                r.failure(f"corpo invalido: {e}")
                return
            if not ok:
                r.failure(f"corpo inconsistente: {r.text[:200]}")

    @task(W_STATS)
    def stats(self):
        with self.client.get("/cache/stats", name="GET /cache/stats", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}")
                return
            try:
                j = r.json()
                ok = (0 <= j["bytes"] <= j["capacidade_bytes"] and j["rss_bytes"] > 0
                      and isinstance(j["pid"], int))
            except (ValueError, KeyError, TypeError) as e:
                r.failure(f"corpo invalido: {e}")
                return
            if not ok:
                r.failure(f"corpo inconsistente: {r.text[:200]}")
