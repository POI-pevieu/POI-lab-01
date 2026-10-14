"""Galeria de Filtros — aplicacao memory-bound (porta 8002).

Imagens sinteticas (numpy uint8 RxRx3) sao geradas e mantidas num cache LRU em
memoria; filtros criam copias temporarias e o mosaico aloca um array grande.

Estrutura Service-Controller (ver README):
controllers/ (HTTP) -> services/ (regras) -> repositories/ (cache LRU).

Handlers pesados sao `def` (nao `async def`): o FastAPI os executa no
threadpool do AnyIO, entao o event loop nao vira gargalo artificial. As
operacoes numpy grandes liberam o GIL, permitindo paralelismo real entre
threads dentro de um mesmo worker.
"""

from __future__ import annotations

from fastapi import FastAPI

from .controllers import routers
from .dependencies import inicializar

# Cria settings + cache (um por processo/worker) ja na carga: env invalido falha no boot.
inicializar()

app = FastAPI(title="Galeria de Filtros", version="1.0.0")
for r in routers:
    app.include_router(r)
