#!/usr/bin/env python3
"""Experiment orchestrator - collects RAW data only (no aggregation, no plots).

For every demand level x repetition of a scenario YAML it creates
results/<app>/<label>/<level>/rep<k>/ containing:
  meta.json              machine facts, versions, git commit, workers, env, timings
                         (incl. measure_start_epoch / measure_end_epoch to align samples)
  warmup_*.csv           locust CSVs of the warm-up phase
  locust_*.csv           locust CSVs of the measured phase (stats, history, failures, exceptions)
  locust.html            locust's own HTML report (raw artifact)
  locust_stdout.log, warmup_stdout.log
  monitor.csv            psutil samples (system + server process tree)  [monitor.py]
  vmstat.log mpstat.log pidstat.log iostat.log   raw sysstat output (if installed)
  collectors.json        exact collector commands / pids / exit codes
  server.log             (only with --start-server --restart-per-rep)

Two modes:
  local  : server, load generator and collectors on the same machine.
  agent  : (recommended) load generated on the HOST, collectors (and optionally the
           server) run on the VM through loadtest/vm_agent.py:  --agent http://VM_IP:9100

Examples:
  python loadtest/run_experiment.py --scenario loadtest/scenarios/cofre_digital.yaml --label 1cpu-1gb --dry-run
  python loadtest/run_experiment.py --scenario loadtest/scenarios/cofre_digital.yaml --label 1cpu-1gb \
      --host http://192.168.56.10:8001 --agent http://192.168.56.10:9100 --start-server --workers 1
  python loadtest/run_experiment.py --scenario loadtest/scenarios/galeria_filtros.yaml --label 2cpu-1gb \
      --start-server --restart-per-rep --levels baixa,alta --reps 1 --duration 30
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from collectors import (REPO, AppServer, Collectors, log, start_proc, stop_proc,  # noqa: E402
                        system_info, wait_health, wait_port_free)

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("run_experiment.py: PyYAML is required (pip install -r loadtest/requirements.txt)\n")
    sys.exit(2)

LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class Interrupted(Exception):
    pass


def _on_signal(signum, _frame):
    raise Interrupted(f"signal {signum}")


def iso(t: float | None) -> str | None:
    if t is None:
        return None
    import datetime as _dt
    return _dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str, ensure_ascii=False))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib)
# ---------------------------------------------------------------------------
def http(method: str, url: str, body: dict | None = None, token: str | None = None,
         timeout: float = 30.0, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Agent-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        payload = e.read()
        status = e.code
    if raw:
        return status, payload
    try:
        return status, json.loads(payload or b"null")
    except json.JSONDecodeError:
        return status, payload.decode(errors="replace")


class Agent:
    def __init__(self, url: str, token: str | None):
        self.url = url.rstrip("/")
        self.token = token

    def call(self, method, path, body=None, timeout=60.0, raw=False):
        status, payload = http(method, self.url + path, body, self.token, timeout, raw)
        if status != 200:
            raise RuntimeError(f"agent {method} {path} -> HTTP {status}: {payload!r}"[:500])
        return payload

    def clock_offset(self) -> dict:
        """Estimate VM_clock - host_clock (Cristian's algorithm, best of 5)."""
        best = None
        for _ in range(5):
            t0 = time.time()
            r = self.call("GET", "/time", timeout=10)
            t1 = time.time()
            rtt = t1 - t0
            off = r["time"] - (t0 + t1) / 2
            if best is None or rtt < best["rtt_s"]:
                best = {"offset_s": off, "rtt_s": rtt, "measured_at_epoch": t1}
        return best


# ---------------------------------------------------------------------------
# server control (local or through the agent)
# ---------------------------------------------------------------------------
class ServerCtl:
    def __init__(self, args, sc: dict, agent: Agent | None):
        self.args = args
        self.sc = sc
        self.agent = agent
        self.local: AppServer | None = None
        self.meta: dict = {}

    def start(self, log_path: Path, run_id: str | None):
        a, sc = self.args, self.sc
        if self.agent:
            r = self.agent.call("POST", "/server/start", {
                "app": sc["app"], "port": sc["port"], "workers": sc["workers"],
                "env": sc["server_env"], "run_id": run_id})
            self.meta = {"mode": "agent", **r}
        else:
            if not wait_port_free(sc["port"], timeout=30):
                raise RuntimeError(f"port {sc['port']} is busy - is a server already running? "
                                   f"(drop --start-server to use it)")
            cmd = shlex.split(a.server_cmd) if a.server_cmd else None
            self.local = AppServer(sc["app"], sc["port"], sc["workers"], sc["server_env"], log_path, cmd)
            self.local.start()
            self.meta = {"mode": "local", "command": self.local.command(), "pid": self.local.proc.pid,
                         "workers": sc["workers"], "env": sc["server_env"], "log": str(log_path),
                         "started_epoch": self.local.started_epoch}
        alive = (lambda: self.local.running()) if self.local else None
        url = sc["host"].rstrip("/") + sc["health_path"]
        if not wait_health(url, timeout=a.health_timeout, proc_alive=alive):
            self.stop()
            raise RuntimeError(f"server did not become healthy at {url} (see server log {log_path})")
        self.meta["healthy_epoch"] = time.time()
        log(f"server healthy at {url}")

    def stop(self):
        if self.agent:
            try:
                r = self.agent.call("POST", "/server/stop", {}, timeout=90)
                self.meta["exit_code"] = r.get("exit_code")
            except Exception as e:
                log(f"WARNING: agent server stop failed: {e}")
        elif self.local:
            self.meta["exit_code"] = self.local.stop()
            self.local = None
            wait_port_free(self.sc["port"], timeout=30)
        self.meta["stopped_epoch"] = time.time()


# ---------------------------------------------------------------------------
# scenario
# ---------------------------------------------------------------------------
def load_scenario(args) -> tuple[dict, str]:
    path = Path(args.scenario)
    if not path.is_absolute() and not path.exists():
        path = REPO / args.scenario
    text = path.read_text()
    y = yaml.safe_load(text) or {}
    for k in ("app", "port", "locustfile", "levels"):
        if k not in y:
            raise SystemExit(f"scenario {path}: missing required key {k!r}")
    sc = {
        "path": str(path),
        "app": y["app"],
        "port": int(y["port"]),
        "locustfile": y["locustfile"],
        "warmup_s": float(args.warmup if args.warmup is not None else y.get("warmup_s", 15)),
        "duration_s": float(args.duration if args.duration is not None else y.get("duration_s", 120)),
        "cooldown_s": float(args.cooldown if args.cooldown is not None else y.get("cooldown_s", 15)),
        "repetitions": int(args.reps if args.reps is not None else y.get("repetitions", 3)),
        "spawn_rate": float(y.get("spawn_rate", 5)),
        "env": {str(k): str(v) for k, v in (y.get("env") or {}).items()},
        "server_env": {str(k): str(v) for k, v in (y.get("server_env") or {}).items()},
        "workers": args.workers if args.workers is not None else y.get("workers"),
        "reset_endpoint": y.get("reset_endpoint"),
        "reset_method": str(y.get("reset_method", "POST")).upper(),
        "health_path": y.get("health_path", "/health"),
        "levels": y["levels"],
    }
    if sc["workers"] is not None:
        w = str(sc["workers"]).strip().lower()
        sc["workers"] = "auto" if w == "auto" else int(w)
    sc["host"] = args.host or f"http://127.0.0.1:{sc['port']}"
    lf = Path(sc["locustfile"])
    sc["locustfile_abs"] = str(lf if lf.is_absolute() else REPO / lf)
    names = [lv["name"] for lv in sc["levels"]]
    if args.levels:
        wanted = [x.strip() for x in args.levels.split(",") if x.strip()]
        unknown = set(wanted) - set(names)
        if unknown:
            raise SystemExit(f"unknown level(s) {sorted(unknown)}; available: {names}")
        sc["levels"] = [lv for lv in sc["levels"] if lv["name"] in wanted]
    return sc, text


def build_plan(sc: dict, order: str) -> list[tuple[dict, int]]:
    reps = range(1, sc["repetitions"] + 1)
    if order == "rep-major":
        return [(lv, k) for k in reps for lv in sc["levels"]]
    return [(lv, k) for lv in sc["levels"] for k in reps]


def locust_cmd() -> list[str]:
    if importlib.util.find_spec("locust") is not None:
        return [sys.executable, "-m", "locust"]
    exe = shutil.which("locust")
    if not exe:
        raise SystemExit("locust not found: pip install -r loadtest/requirements.txt")
    return [exe]


# ---------------------------------------------------------------------------
# locust phases
# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, args, sc, scenario_text, agent: Agent | None):
        self.args = args
        self.sc = sc
        self.scenario_text = scenario_text
        self.agent = agent
        self.children: list[subprocess.Popen] = []
        self.collectors: Collectors | None = None
        self.agent_run_id: str | None = None
        self.server = ServerCtl(args, sc, agent) if args.start_server else None
        self.loadgen_info = system_info()
        self.server_info = None
        self.locust = locust_cmd()

    def run_locust(self, rep_dir: Path, prefix: str, users: int, spawn_rate: float,
                   duration_s: float, env: dict, html: bool) -> dict:
        cmd = self.locust + [
            "-f", self.sc["locustfile_abs"], "--headless",
            "-u", str(users), "-r", str(spawn_rate), "-t", f"{int(round(duration_s))}s",
            "--host", self.sc["host"],
            "--csv", str(rep_dir / prefix), "--csv-full-history",
            "--only-summary", "--exit-code-on-error", "0",
        ]
        if html:
            cmd += ["--html", str(rep_dir / "locust.html")]
        full_env = dict(os.environ)
        full_env.update(env)
        out_log = rep_dir / f"{prefix}_stdout.log"
        t0 = time.time()
        p = start_proc(cmd, out_log, env=full_env, cwd=str(REPO))
        self.children.append(p)
        try:
            rc = p.wait(timeout=duration_s + self.args.locust_grace)
        except subprocess.TimeoutExpired:
            log(f"WARNING: locust exceeded {duration_s}+{self.args.locust_grace}s, stopping it")
            rc = stop_proc(p, signal.SIGINT, timeout=15)
        except BaseException:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, signal.SIG_IGN)
            stop_proc(p, signal.SIGINT, timeout=15)  # flushes locust CSVs
            raise
        finally:
            if p.poll() is not None and p in self.children:
                self.children.remove(p)
        t1 = time.time()
        if rc not in (0, None):
            log(f"WARNING: locust exited with code {rc} (see {out_log})")
        return {"command": cmd, "env": env, "exit_code": rc, "start_epoch": t0, "end_epoch": t1,
                "start_iso": iso(t0), "end_iso": iso(t1), "stdout_log": out_log.name}

    # -- collectors ---------------------------------------------------------
    def start_collectors(self, rep_dir: Path, run_id: str) -> dict:
        if self.agent:
            self.agent_run_id = run_id
            return self.agent.call("POST", "/collect/start", {
                "run_id": run_id, "interval": self.args.interval, "match_port": self.sc["port"],
                "sysstat": not self.args.no_sysstat, "uss": not self.args.no_uss})
        self.collectors = Collectors(rep_dir, self.args.interval, self.sc["port"],
                                     sysstat=not self.args.no_sysstat, uss=not self.args.no_uss)
        return self.collectors.start()

    def stop_collectors(self, rep_dir: Path) -> dict:
        if self.agent:
            if self.agent_run_id is None:
                return {}
            run_id, self.agent_run_id = self.agent_run_id, None
            info = self.agent.call("POST", "/collect/stop", {"run_id": run_id}, timeout=90)
            data = self.agent.call("GET", f"/collect/fetch?run_id={run_id}", timeout=300, raw=True)
            extract_tar(data, rep_dir)
            info["fetched_bytes"] = len(data)
            info["agent_run_id"] = run_id
            return info
        if self.collectors is None:
            return {}
        c, self.collectors = self.collectors, None
        return c.stop()

    def cleanup(self):
        # a second Ctrl-C must not abort the cleanup half-way
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        for p in list(self.children):
            stop_proc(p, signal.SIGINT, timeout=10)
        self.children.clear()
        try:
            if self.collectors or self.agent_run_id:
                self.stop_collectors(self._cur_rep_dir)
        except Exception as e:
            log(f"WARNING: stopping collectors failed: {e}")
        if self.server:
            try:
                self.server.stop()
            except Exception as e:
                log(f"WARNING: stopping server failed: {e}")

    # -- one repetition -------------------------------------------------------
    def run_rep(self, level: dict, rep: int, is_last: bool) -> None:
        a, sc = self.args, self.sc
        rep_dir = Path(a.results_dir) / sc["app"] / a.label / level["name"] / f"rep{rep}"
        self._cur_rep_dir = rep_dir
        rep_dir.mkdir(parents=True, exist_ok=True)
        users = int(level["users"])
        spawn_rate = float(level.get("spawn_rate", sc["spawn_rate"]))
        env = dict(sc["env"])
        env.update({str(k): str(v) for k, v in (level.get("env") or {}).items()})
        run_id = f"{sc['app']}__{a.label}__{level['name']}__rep{rep}__{int(time.time())}"
        meta = {
            "status": "running",
            "app": sc["app"], "label": a.label, "level": level["name"], "rep": rep,
            "users": users, "spawn_rate": spawn_rate, "ramp_up_s": users / spawn_rate if spawn_rate else None,
            "warmup_s": sc["warmup_s"], "duration_s": sc["duration_s"], "cooldown_s": sc["cooldown_s"],
            "host_url": sc["host"], "port": sc["port"],
            "workers": sc["workers"], "workers_source": ("cli" if a.workers is not None else "scenario")
            if sc["workers"] is not None else "unknown (server not started by harness)",
            "server_env": sc["server_env"], "locust_env": env,
            "server_managed_by_harness": bool(self.server), "restart_per_rep": a.restart_per_rep,
            "mode": "agent" if self.agent else "local", "agent_url": a.agent,
            "monitor_interval_s": a.interval,
            "scenario_file": sc["path"], "scenario_yaml": self.scenario_text,
            "scenario_effective": {k: v for k, v in sc.items() if k not in ("levels",)},
            "cli_argv": sys.argv,
            "loadgen_host": self.loadgen_info,
            "server_host": self.server_info,
            "git": self.loadgen_info.get("git"),
            "timings": {},
        }
        T = meta["timings"]
        meta_path = rep_dir / "meta.json"
        write_json(meta_path, meta)
        log(f"=== {sc['app']} / {a.label} / {level['name']} (users={users}) rep {rep} -> {rep_dir}")
        try:
            if self.server and a.restart_per_rep:
                T["server_start_epoch"] = time.time()
                self.server.start(rep_dir / "server.log", run_id)
                meta["server"] = dict(self.server.meta)
            elif self.server:
                meta["server"] = dict(self.server.meta)
            health = sc["host"].rstrip("/") + sc["health_path"]
            if not wait_health(health, timeout=a.health_timeout):
                raise RuntimeError(f"server not healthy at {health}")
            if self.agent:
                meta["clock_offset_vm_minus_host"] = self.agent.clock_offset()
            if sc["reset_endpoint"]:
                url = sc["host"].rstrip("/") + sc["reset_endpoint"]
                st, body = http(sc["reset_method"], url, timeout=60)
                meta["reset"] = {"url": url, "method": sc["reset_method"], "status": st, "epoch": time.time(),
                                 "body": body if isinstance(body, (dict, list)) else str(body)[:500]}
                if st >= 400:
                    log(f"WARNING: reset endpoint {url} returned HTTP {st}")
            T["collectors_start_epoch"] = time.time()
            meta["collectors"] = self.start_collectors(rep_dir, run_id)
            write_json(meta_path, meta)
            if a.baseline > 0:
                time.sleep(a.baseline)  # idle samples before load
            if sc["warmup_s"] > 0:
                log(f"warm-up {sc['warmup_s']:.0f}s")
                meta["warmup"] = self.run_locust(rep_dir, "warmup", users, spawn_rate, sc["warmup_s"], env, False)
                T["warmup_start_epoch"] = meta["warmup"]["start_epoch"]
                T["warmup_end_epoch"] = meta["warmup"]["end_epoch"]
            log(f"measuring {sc['duration_s']:.0f}s")
            meta["measure"] = self.run_locust(rep_dir, "locust", users, spawn_rate, sc["duration_s"], env, True)
            T["measure_start_epoch"] = meta["measure"]["start_epoch"]
            T["measure_end_epoch"] = meta["measure"]["end_epoch"]
            T["measure_start_iso"] = iso(T["measure_start_epoch"])
            T["measure_end_iso"] = iso(T["measure_end_epoch"])
            if a.tail > 0:
                time.sleep(a.tail)
            T["collectors_stop_epoch"] = time.time()
            meta["collectors_stop"] = self.stop_collectors(rep_dir)
            if self.server and a.restart_per_rep:
                self.server.stop()
                meta["server"] = dict(self.server.meta)
            meta["status"] = "completed"
        except Interrupted:
            meta["status"] = "interrupted"
            raise
        except Exception as e:
            meta["status"] = "failed"
            meta["error"] = repr(e)
            raise
        finally:
            T["rep_end_epoch"] = time.time()
            write_json(meta_path, meta)
        if not is_last and sc["cooldown_s"] > 0:
            log(f"cool-down {sc['cooldown_s']:.0f}s")
            time.sleep(sc["cooldown_s"])

    def run(self, plan) -> None:
        a = self.args
        self._cur_rep_dir = Path(a.results_dir)
        if self.agent:
            self.server_info = self.agent.call("GET", f"/info?app={self.sc['app']}")
        else:
            self.server_info = system_info(self.sc["app"])
        if self.server and not a.restart_per_rep:
            log_path = Path(a.results_dir) / self.sc["app"] / a.label / f"server_{int(time.time())}.log"
            self.server.start(log_path, None)
        try:
            for i, (lv, k) in enumerate(plan):
                self.run_rep(lv, k, is_last=(i == len(plan) - 1))
        finally:
            self.cleanup()


def extract_tar(data: bytes, dest: Path) -> None:
    dest = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            target = (dest / m.name).resolve()
            if not m.isfile() or (dest not in target.parents):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            if src is None:
                continue
            with src, open(target, "wb") as fh:
                shutil.copyfileobj(src, fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True, help="scenario YAML (loadtest/scenarios/<app>.yaml)")
    ap.add_argument("--label", required=True, help="provisioning scenario name, e.g. 1cpu-1gb")
    ap.add_argument("--host", help="target base URL (default http://127.0.0.1:<port>)")
    ap.add_argument("--workers", help="gunicorn WORKERS: integer or 'auto' (= server nproc); overrides yaml `workers`")
    ap.add_argument("--start-server", action="store_true", help="start/stop the app server (locally or via --agent)")
    ap.add_argument("--restart-per-rep", action="store_true", help="with --start-server: fresh server for every repetition")
    ap.add_argument("--server-cmd", help="local mode: custom server command (default apps/<app>/run.sh)")
    ap.add_argument("--levels", help="comma-separated subset of level names")
    ap.add_argument("--reps", type=int, help="override repetitions")
    ap.add_argument("--duration", type=float, help="override duration_s")
    ap.add_argument("--warmup", type=float, help="override warmup_s")
    ap.add_argument("--cooldown", type=float, help="override cooldown_s")
    ap.add_argument("--order", choices=("level-major", "rep-major"), default="level-major",
                    help="level-major: all reps of a level then next level; rep-major: interleave levels")
    ap.add_argument("--interval", type=float, default=1.0, help="monitor sampling interval s (default 1)")
    ap.add_argument("--baseline", type=float, default=3.0, help="idle seconds recorded before warm-up (default 3)")
    ap.add_argument("--tail", type=float, default=3.0, help="seconds recorded after the measured run (default 3)")
    ap.add_argument("--no-sysstat", action="store_true", help="do not start vmstat/mpstat/pidstat/iostat")
    ap.add_argument("--no-uss", action="store_true", help="monitor: skip USS/PSS (memory_full_info)")
    ap.add_argument("--agent", help="vm_agent URL, e.g. http://192.168.56.10:9100 (collectors run on the VM)")
    ap.add_argument("--agent-token", default=os.environ.get("VM_AGENT_TOKEN"), help="vm_agent token (or env VM_AGENT_TOKEN)")
    ap.add_argument("--results-dir", default=str(REPO / "results"))
    ap.add_argument("--health-timeout", type=float, default=90.0)
    ap.add_argument("--locust-grace", type=float, default=60.0, help="extra seconds before a hung locust is killed")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--resume", action="store_true", help="skip reps whose meta.json says completed")
    g.add_argument("--overwrite", action="store_true", help="delete existing rep dirs before running them")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = ap.parse_args(argv)

    if not LABEL_RE.match(args.label):
        raise SystemExit("--label may contain only letters, digits, '.', '_' and '-'")
    if args.restart_per_rep and not args.start_server:
        raise SystemExit("--restart-per-rep requires --start-server")
    sc, text = load_scenario(args)
    if not Path(sc["locustfile_abs"]).exists():
        raise SystemExit(f"locustfile not found: {sc['locustfile_abs']}")
    plan = build_plan(sc, args.order)

    base = Path(args.results_dir) / sc["app"] / args.label
    todo, skipped, conflicts = [], [], []
    for lv, k in plan:
        d = base / lv["name"] / f"rep{k}"
        if d.exists() and any(d.iterdir()):
            status = None
            try:
                status = json.loads((d / "meta.json").read_text()).get("status")
            except Exception:
                pass
            if args.resume and status == "completed":
                skipped.append(d)
                continue
            if not args.resume and not args.overwrite:
                conflicts.append(d)
        todo.append((lv, k))

    per_rep = args.baseline + sc["warmup_s"] + sc["duration_s"] + args.tail + sc["cooldown_s"]
    print(f"app={sc['app']} label={args.label} host={sc['host']} port={sc['port']} "
          f"mode={'agent ' + args.agent if args.agent else 'local'}")
    print(f"locustfile={sc['locustfile']} workers={sc['workers']} server_env={sc['server_env']} "
          f"locust_env={sc['env']} start_server={args.start_server} restart_per_rep={args.restart_per_rep} "
          f"reset_endpoint={sc['reset_endpoint']}")
    print(f"warmup={sc['warmup_s']}s duration={sc['duration_s']}s cooldown={sc['cooldown_s']}s "
          f"reps={sc['repetitions']} order={args.order}")
    for lv, k in todo:
        print(f"  {lv['name']:<12} users={lv['users']:<5} rep{k}  -> {base / lv['name'] / f'rep{k}'}")
    for d in skipped:
        print(f"  (skip, completed) {d}")
    print(f"{len(todo)} runs, ~{len(todo) * per_rep / 60:.1f} min")
    if conflicts:
        print("ERROR: these result dirs already exist (use --resume or --overwrite):", file=sys.stderr)
        for d in conflicts:
            print(f"  {d}", file=sys.stderr)
        return 1
    if args.dry_run:
        return 0
    if args.overwrite:
        for lv, k in todo:
            d = base / lv["name"] / f"rep{k}"
            if d.exists():
                shutil.rmtree(d)

    agent = Agent(args.agent, args.agent_token) if args.agent else None
    if agent:
        agent.call("GET", "/health", timeout=10)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    runner = Runner(args, sc, text, agent)
    try:
        runner.run(todo)
    except Interrupted as e:
        log(f"interrupted ({e}); children stopped, partial data kept")
        return 130
    except Exception as e:
        log(f"ERROR: {e!r}")
        return 1
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
