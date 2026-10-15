"""Cofre Digital -- CPU-bound FastAPI application (port 8001).

Service–Controller layout:

* ``controllers/`` -- APIRouters, HTTP only (validation, 422 mapping).
* ``services/``    -- pure-Python, GIL-bound algorithms (no FastAPI imports):
  trial-division primes, iterated SHA-256 over a 32-byte digest, and a
  pure-Python integer matrix product.
* ``schemas/``     -- Pydantic request/response models.
* ``config.py``    -- settings and validation bounds.
* ``exceptions.py``-- domain errors raised by services.

Why plain ``def`` handlers?
    FastAPI runs ``def`` endpoints in the AnyIO worker threadpool, so the
    asyncio event loop stays free to accept connections and answer ``/health``
    while a request is computing.  Because the work is GIL-bound, threads inside
    one worker do NOT add CPU parallelism: one Gunicorn worker process can use
    at most ~1 core.  Scaling across vCPUs therefore comes from ``WORKERS``
    (separate processes), which is exactly the variable the experiments study.
    An ``async def`` handler would instead block the event loop for the whole
    computation, artificially serialising even trivial requests.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.config import settings
from app.controllers import (
    assinatura_controller,
    health_controller,
    matriz_controller,
    primos_controller,
)
from app.middleware import ProcessTimeMiddleware

app = FastAPI(
    title=settings.title,
    description=settings.description,
    version=settings.version,
)
app.add_middleware(ProcessTimeMiddleware)

app.include_router(health_controller.router)
app.include_router(primos_controller.router)
app.include_router(assinatura_controller.router)
app.include_router(matriz_controller.router)
