"""Gunicorn config da Bolsa Fictícia (honra HOST/PORT/WORKERS/TIMEOUT/LOG_LEVEL)."""
import os

chdir = os.path.dirname(os.path.abspath(__file__))
wsgi_app = "app.main:app"
bind = f"{os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', '8003')}"
_workers = os.environ.get("WORKERS", "1").strip().lower()
# WORKERS=auto -> one worker per vCPU visible to the VM (scales with provisioning)
workers = (os.cpu_count() or 1) if _workers == "auto" else int(_workers)
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.environ.get("TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5
loglevel = os.environ.get("LOG_LEVEL", "warning")
# Access log desligado por padrão (não queremos IO de log medido junto). ACCESS_LOG=1 liga.
accesslog = "-" if os.environ.get("ACCESS_LOG", "0") == "1" else None
errorlog = "-"
