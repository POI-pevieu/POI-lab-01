FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1 \
  PIP_NO_CACHE_DIR=1 \
  PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY gunicorn.conf.py .
COPY app ./app

RUN useradd --system --uid 10001 --no-create-home app \
  && mkdir -p /data && chown app:app /data
USER app
VOLUME ["/data"]

ENV HOST=0.0.0.0 PORT=8324 WORKERS=4 TIMEOUT=120 LOG_LEVEL=warning \
  DATA_DIR=/data SQLITE_JOURNAL_MODE=WAL SQLITE_SYNCHRONOUS=FULL \
  SQLITE_BUSY_TIMEOUT_MS=30000 LEDGER_FSYNC=1
EXPOSE 8324

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"

CMD ["gunicorn", "-c", "gunicorn.conf.py"]
