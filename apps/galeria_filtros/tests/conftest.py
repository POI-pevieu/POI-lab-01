"""Fixtures: sobe um servidor REAL (gunicorn + uvicorn worker) em porta livre."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))  # permite `from app.services... import ...` nos testes


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _subir(env_extra: dict[str, str]):
    porta = _porta_livre()
    env = {**os.environ, "HOST": "127.0.0.1", "PORT": str(porta), "WORKERS": "1",
           "LOG_LEVEL": "warning", **env_extra}
    proc = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "-c", "gunicorn.conf.py", "app.main:app"],
        cwd=APP_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{porta}"
    prazo = time.time() + 30
    while time.time() < prazo:
        if proc.poll() is not None:
            raise RuntimeError("servidor morreu ao subir:\n" + proc.stdout.read().decode(errors="replace"))
        try:
            if httpx.get(base + "/health", timeout=1).status_code == 200:
                return proc, base
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    _parar(proc)
    raise RuntimeError("servidor nao respondeu /health em 30s")


def _parar(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def _cliente(env_extra):
    proc, base = _subir(env_extra)
    with httpx.Client(base_url=base, timeout=60) as c:
        yield c
    _parar(proc)


# 4 MiB = 4_194_304 bytes: cabem 5 imagens 500x500x3 (750_000 bytes cada).
@pytest.fixture(scope="session")
def servidor():
    yield from _cliente({"CACHE_MAX_MB": "4", "MOSAICO_MAX_MB": "64"})


@pytest.fixture(scope="session")
def servidor_auto():
    yield from _cliente({"CACHE_MAX_MB": "auto", "CACHE_RAM_FRACTION": "0.01",
                         "CACHE_MAX_ITEMS": "4", "MOSAICO_MAX_MB": "1"})


@pytest.fixture
def cliente(servidor):
    r = servidor.delete("/cache")
    assert r.status_code == 200
    return servidor
