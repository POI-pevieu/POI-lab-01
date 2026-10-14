#!/usr/bin/env bash
# Sobe a Bolsa Fictícia com gunicorn + uvicorn workers.
# Ex.: WORKERS=4 DATA_DIR=/var/lib/bolsa ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
# usa o venv do app só se o interpretador funciona aqui (venv copiado de outro SO quebra)
if [ -z "${PYTHON:-}" ] && [ -x .venv/bin/python ] && .venv/bin/python -c "import gunicorn" 2>/dev/null; then
  PY=.venv/bin/python
fi
exec "$PY" -m gunicorn -c gunicorn.conf.py "$@"
