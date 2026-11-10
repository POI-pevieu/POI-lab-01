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

RUN useradd --system --uid 10001 --no-create-home app
USER app

ENV HOST=0.0.0.0 PORT=8124 WORKERS=auto TIMEOUT=120 LOG_LEVEL=warning
EXPOSE 8124

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"

CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]
