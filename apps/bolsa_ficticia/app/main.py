"""Bolsa Fictícia — aplicação IO-bound (porta 8003).

Corretora simulada: investidores começam com saldo fictício e compram/vendem
ações de 4 empresas fictícias. Toda ordem executada é gravada no SQLite e no
livro-caixa em disco (append + fsync).

Estrutura Service–Controller: controllers/ (HTTP) -> services/ (regras,
transação) -> repositories/ (SQL, arquivo do livro-caixa). Este módulo só cria
o app, registra handlers de erro, inclui os routers e inicializa no startup.

Os handlers são `def` (não `async def`): sqlite3 e fsync são bloqueantes, então
o FastAPI os executa no threadpool (AnyIO, 40 threads por worker) e o event
loop não vira gargalo artificial. O gargalo medido é o disco.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings
from .controllers import ROUTERS
from .controllers.erros import registrar_handlers
from .services import construir_servicos


@asynccontextmanager
async def lifespan(app: FastAPI):
    servicos = construir_servicos(Settings.from_env())
    servicos.inicializar()
    app.state.servicos = servicos
    yield


app = FastAPI(title="Bolsa Fictícia", version="1.0.0", lifespan=lifespan)
registrar_handlers(app)
for router in ROUTERS:
    app.include_router(router)
