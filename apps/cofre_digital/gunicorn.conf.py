"""Gunicorn configuration for Cofre Digital (CPU-bound, default port 8001).

Env vars: HOST, PORT, WORKERS, TIMEOUT, LOG_LEVEL.
Access logs are disabled unless LOG_LEVEL is debug/info, to keep logging
overhead out of load-test measurements.
"""
import os

_host = os.environ.get("HOST", "0.0.0.0")
_port = os.environ.get("PORT", "8001")

bind = f"{_host}:{_port}"
_workers = os.environ.get("WORKERS", "1").strip().lower()
# WORKERS=auto -> one worker per vCPU visible to the VM (scales with provisioning)
workers = (os.cpu_count() or 1) if _workers == "auto" else int(_workers)
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.environ.get("TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5
loglevel = os.environ.get("LOG_LEVEL", "warning")
accesslog = "-" if loglevel in ("debug", "info") else None
errorlog = "-"
wsgi_app = "app.main:app"
proc_name = "cofre_digital"
