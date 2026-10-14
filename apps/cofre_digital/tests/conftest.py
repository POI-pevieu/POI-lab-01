"""Spawns a REAL Gunicorn+Uvicorn server for the functional tests.

Set COFRE_TEST_URL=http://host:port to test an already-running server instead
(e.g. the deployed VM).  No mocks: every test talks HTTP to a live process.
"""
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(url: str, proc: subprocess.Popen | None, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            raise RuntimeError(f"server exited early (rc={proc.returncode}):\n{out}")
        try:
            r = httpx.get(f"{url}/health", timeout=2)
            if r.status_code == 200:
                return
        except httpx.HTTPError as e:  # not up yet
            last_err = e
        time.sleep(0.2)
    raise RuntimeError(f"server did not become healthy at {url}: {last_err}")


@pytest.fixture(scope="session")
def base_url():
    external = os.environ.get("COFRE_TEST_URL")
    if external:
        url = external.rstrip("/")
        _wait_health(url, None)
        yield url
        return

    port = _free_port()
    env = dict(os.environ, HOST="127.0.0.1", PORT=str(port), WORKERS="2",
               TIMEOUT="120", LOG_LEVEL="warning")
    proc = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "-c", "gunicorn.conf.py", "app.main:app"],
        cwd=APP_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_health(url, proc)
        yield url
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture(scope="session")
def client(base_url):
    with httpx.Client(base_url=base_url, timeout=120) as c:
        yield c
