#!/usr/bin/env python3
"""Tiny HTTP control agent that runs ON THE VM (the server under test).

Lets run_experiment.py, running on the HOST (load generator), start/stop the
resource collectors (monitor.py + sysstat) and optionally the app server on the
VM, then download the raw files as a tar.gz.  stdlib + psutil only.

    # on the VM (inside the repo, with the loadtest venv active)
    python loadtest/vm_agent.py --port 9100 [--data-dir ~/agent_runs] [--token SECRET]

    # on the host
    python loadtest/run_experiment.py --scenario ... --label ... \
        --host http://VM_IP:8001 --agent http://VM_IP:9100 [--agent-token SECRET]

SECURITY CAVEAT: lab use only. The agent has no TLS and (unless --token is set)
no authentication; anyone who can reach the port can start/stop collectors and
start/stop the app servers of this repo. It never runs arbitrary commands: only
monitor.py, the sysstat tools, and apps/<known app>/run.sh. Bind it to a host-only
/ NAT network, never expose it to the Internet, and stop it when done.

Endpoints (JSON in/out):
  GET  /health                       -> {"ok": true}
  GET  /time                         -> {"time": epoch}          (clock-offset estimation)
  GET  /info?app=<app>               -> system_info()
  POST /collect/start {run_id, interval, match_port, sysstat, uss}
  POST /collect/stop  {run_id}
  GET  /collect/fetch?run_id=<id>    -> application/gzip tar of the run directory
  POST /server/start {app, port, workers, env, run_id?}   (log -> run dir if run_id)
  POST /server/stop
  GET  /server/status
"""
from __future__ import annotations

import argparse
import io
import json
import re
import signal
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collectors import APPS, AppServer, Collectors, log, system_info  # noqa: E402

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")


class State:
    def __init__(self, data_dir: Path, token: str | None):
        self.data_dir = data_dir
        self.token = token
        self.lock = threading.Lock()
        self.collectors: dict[str, Collectors] = {}
        self.server: AppServer | None = None
        self.server_meta: dict = {}

    def run_dir(self, run_id: str) -> Path:
        if not run_id or not RUN_ID_RE.match(run_id):
            raise ValueError("invalid run_id")
        d = (self.data_dir / run_id).resolve()
        if d.parent != self.data_dir.resolve():
            raise ValueError("invalid run_id")
        return d

    def shutdown(self):
        with self.lock:
            for c in self.collectors.values():
                try:
                    c.stop()
                except Exception:
                    pass
            self.collectors.clear()
            if self.server is not None:
                self.server.stop()
                self.server = None


STATE: State


class Handler(BaseHTTPRequestHandler):
    server_version = "vm_agent/1.0"

    def log_message(self, fmt, *args):
        log(f"{self.client_address[0]} {fmt % args}")

    # --- helpers -------------------------------------------------------------
    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self) -> bool:
        if STATE.token and self.headers.get("X-Agent-Token") != STATE.token:
            self._json(401, {"error": "bad or missing X-Agent-Token"})
            return False
        return True

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON object expected")
        return data

    # --- GET -----------------------------------------------------------------
    def do_GET(self):
        if not self._auth():
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/health":
                return self._json(200, {"ok": True})
            if u.path == "/time":
                return self._json(200, {"time": time.time()})
            if u.path == "/info":
                app = q.get("app") if q.get("app") in APPS else None  # app only adds its pip freeze
                return self._json(200, system_info(app))
            if u.path == "/server/status":
                s = STATE.server
                return self._json(200, {"running": bool(s and s.running()),
                                        "pid": s.proc.pid if s and s.proc else None, **STATE.server_meta})
            if u.path == "/collect/fetch":
                d = STATE.run_dir(q.get("run_id", ""))
                if not d.is_dir():
                    return self._json(404, {"error": "no such run"})
                with STATE.lock:
                    if q.get("run_id") in STATE.collectors:
                        return self._json(409, {"error": "collectors still running"})
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                    for f in sorted(d.rglob("*")):
                        if f.is_file():
                            tar.add(str(f), arcname=str(f.relative_to(d)))
                data = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self._json(404, {"error": "not found"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:  # pragma: no cover
            return self._json(500, {"error": repr(e)})

    # --- POST ----------------------------------------------------------------
    def do_POST(self):
        if not self._auth():
            return
        path = urlparse(self.path).path
        try:
            b = self._body()
            if path == "/collect/start":
                run_id = b.get("run_id", "")
                d = STATE.run_dir(run_id)
                with STATE.lock:
                    if run_id in STATE.collectors:
                        return self._json(409, {"error": "already collecting for this run_id"})
                    port = b.get("match_port")
                    c = Collectors(d, interval=float(b.get("interval", 1.0)),
                                   match_port=int(port) if port else None,
                                   sysstat=bool(b.get("sysstat", True)), uss=bool(b.get("uss", True)))
                    info = c.start()
                    STATE.collectors[run_id] = c
                return self._json(200, {"ok": True, "dir": str(d), **info})
            if path == "/collect/stop":
                run_id = b.get("run_id", "")
                STATE.run_dir(run_id)
                with STATE.lock:
                    c = STATE.collectors.pop(run_id, None)
                if c is None:
                    return self._json(404, {"error": "not collecting for this run_id"})
                return self._json(200, {"ok": True, **c.stop()})
            if path == "/server/start":
                app = b.get("app")
                if app not in APPS:
                    return self._json(400, {"error": f"app must be one of {APPS}"})
                port = int(b["port"])
                workers = b.get("workers")
                env = b.get("env") or {}
                if not isinstance(env, dict) or not all(re.match(r"^[A-Z][A-Z0-9_]*$", str(k)) for k in env):
                    return self._json(400, {"error": "env must be a dict of UPPER_CASE names"})
                if b.get("run_id"):
                    log_path = STATE.run_dir(b["run_id"]) / "server.log"
                else:
                    log_path = STATE.data_dir / f"server_{app}_{int(time.time())}.log"
                with STATE.lock:
                    if STATE.server is not None and STATE.server.running():
                        return self._json(409, {"error": "server already running; stop it first"})
                    s = AppServer(app, port, (None if workers is None else ("auto" if str(workers).lower() == "auto" else int(workers))), env, log_path)
                    s.start()
                    STATE.server = s
                    STATE.server_meta = {"app": app, "port": port, "workers": workers, "env": env,
                                         "log": str(log_path), "command": s.command(),
                                         "started_epoch": s.started_epoch}
                return self._json(200, {"ok": True, "pid": s.proc.pid, **STATE.server_meta})
            if path == "/server/stop":
                with STATE.lock:
                    s, STATE.server = STATE.server, None
                rc = s.stop() if s is not None else None
                return self._json(200, {"ok": True, "was_running": s is not None, "exit_code": rc})
            return self._json(404, {"error": "not found"})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:  # pragma: no cover
            return self._json(500, {"error": repr(e)})


def main(argv=None) -> int:
    global STATE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bind", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--data-dir", default=str(Path.home() / "agent_runs"),
                    help="where raw run directories are written (default ~/agent_runs)")
    ap.add_argument("--token", default=None,
                    help="optional shared secret; clients must send header X-Agent-Token")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    STATE = State(data_dir, args.token)
    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    httpd.daemon_threads = True

    def _sig(signum, _f):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    log(f"vm_agent listening on {args.bind}:{args.port}, data dir {data_dir}"
        f"{' (token required)' if args.token else ' (NO AUTH - lab use only)'}")
    try:
        httpd.serve_forever()
    finally:
        log("shutting down: stopping collectors and server")
        STATE.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
