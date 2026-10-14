#!/usr/bin/env bash
# Start Cofre Digital under Gunicorn + Uvicorn workers.
# Env: HOST (0.0.0.0) PORT (8001) WORKERS (1) TIMEOUT (120) LOG_LEVEL (warning)
set -euo pipefail
cd "$(dirname "$0")"
# use the app venv only if its interpreter really runs here (a venv copied from
# another OS is executable but broken)
if [[ -z "${VIRTUAL_ENV:-}" && -x .venv/bin/python ]] && .venv/bin/python -c "import gunicorn" 2>/dev/null; then
  source .venv/bin/activate
fi
exec gunicorn -c gunicorn.conf.py app.main:app "$@"
