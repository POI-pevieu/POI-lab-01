"""Configuracao do gunicorn — le HOST/PORT/WORKERS/TIMEOUT/LOG_LEVEL do ambiente."""
import os

bind = f"{os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', '8002')}"
# ATENCAO: cada worker tem seu proprio cache LRU (memoria nao compartilhada).
_workers = os.environ.get("WORKERS", "1").strip().lower()
# WORKERS=auto -> one worker per vCPU visible to the VM (scales with provisioning)
workers = (os.cpu_count() or 1) if _workers == "auto" else int(_workers)
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.environ.get("TIMEOUT", "120"))
graceful_timeout = 30
loglevel = os.environ.get("LOG_LEVEL", "warning")
accesslog = "-" if loglevel in ("debug", "info") else None
errorlog = "-"
# app carregada em cada worker (sem preload) para que cada processo crie seu cache.
preload_app = False
