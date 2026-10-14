#!/usr/bin/env bash
# Inicia a Galeria de Filtros com gunicorn + uvicorn workers.
# Variaveis: HOST PORT WORKERS TIMEOUT LOG_LEVEL CACHE_MAX_MB CACHE_RAM_FRACTION CACHE_MAX_ITEMS MOSAICO_MAX_MB
set -euo pipefail
cd "$(dirname "$0")"
# usa o venv do app so se o interpretador funciona aqui (venv copiado de outro SO quebra)
if [[ -x .venv/bin/python ]] && .venv/bin/python -c "import gunicorn" 2>/dev/null; then
  GUNICORN=(.venv/bin/python -m gunicorn)
else
  GUNICORN=(gunicorn)
fi
# Limita o numero de arenas do glibc malloc (reduz fragmentacao/RSS retido com muitas threads).
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"
exec "${GUNICORN[@]}" -c gunicorn.conf.py app.main:app
