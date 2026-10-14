"""Shared helpers for run_experiment.py and vm_agent.py (stdlib + psutil only).

- system_info(): facts about the machine (for meta.json)
- Collectors: starts/stops monitor.py + sysstat tools (vmstat, mpstat, pidstat,
  iostat) writing RAW logs into a directory. Missing sysstat tools -> warning.
- AppServer: starts/stops an app (apps/<app>/run.sh, or gunicorn from the app venv)
  in its own process group.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
APPS = ("cofre_digital", "galeria_filtros", "bolsa_ficticia")
KEY_PACKAGES = ("locust", "psutil", "PyYAML", "requests", "gevent", "fastapi",
                "uvicorn", "gunicorn", "starlette", "pydantic", "numpy", "pillow")


def log(msg: str) -> None:
    ts = _dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


def _run(cmd, timeout=15, **kw) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------------------
# system information
# ---------------------------------------------------------------------------
def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    if sys.platform == "darwin":
        out = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if out:
            return out
    return platform.processor() or platform.machine()


def _distro() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]}"
    return platform.platform()


def _cgroup_limits() -> dict:
    out = {}
    for name, path in (("cpu.max", "/sys/fs/cgroup/cpu.max"), ("memory.max", "/sys/fs/cgroup/memory.max")):
        try:
            out[name] = Path(path).read_text().strip()
        except OSError:
            pass
    return out


def git_info() -> dict:
    commit = _run(["git", "-C", str(REPO), "rev-parse", "HEAD"])
    dirty = _run(["git", "-C", str(REPO), "status", "--porcelain"])
    return {"commit": commit or None, "dirty": bool(dirty) if commit else None}


def package_versions() -> dict:
    from importlib import metadata
    out = {}
    for pkg in KEY_PACKAGES:
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    return out


def _venv_works(v: Path) -> bool:
    """True if the venv's interpreter actually runs on this machine.

    A venv copied from another OS (e.g. a macOS zip, where symlinks become plain
    files) still has an executable bin/python, but it cannot start.
    """
    py = v / "bin" / "python"
    if not (py.exists() and os.access(py, os.X_OK)):
        return False
    try:
        return subprocess.run([str(py), "-c", "import gunicorn"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def app_venv(app: str | None) -> Path | None:
    if not app:
        return None
    for name in (".venv", "venv"):
        v = REPO / "apps" / app / name
        if (v / "bin" / "python").exists():
            if _venv_works(v):
                return v
            log(f"WARNING: ignoring broken venv {v} (recreate it: python3 -m venv --clear {v})")
    return None


def app_pip_freeze(app: str | None) -> list[str] | None:
    v = app_venv(app)
    if v is None:
        return None
    out = _run([str(v / "bin" / "python"), "-m", "pip", "freeze", "--disable-pip-version-check"], timeout=60)
    return out.splitlines() if out else None


def system_info(app: str | None = None) -> dict:
    import psutil
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    try:
        freq = psutil.cpu_freq()
        freq = freq._asdict() if freq else None
    except Exception:
        freq = None
    info = {
        "hostname": socket.gethostname(),
        "nproc": os.cpu_count(),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "cpu_affinity_count": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "cpu_model": _cpu_model(),
        "cpu_freq_mhz": freq,
        "mem_total_bytes": vm.total,
        "mem_available_bytes_at_start": vm.available,
        "swap_total_bytes": sw.total,
        "kernel": platform.release(),
        "os": platform.system(),
        "distro": _distro(),
        "arch": platform.machine(),
        "virtualization": _run(["systemd-detect-virt"]) or None if shutil.which("systemd-detect-virt") else None,
        "cgroup_limits": _cgroup_limits(),
        "boot_time": psutil.boot_time(),
        "python": sys.version,
        "python_executable": sys.executable,
        "packages": package_versions(),
        "sysstat_tools": {t: shutil.which(t) for t in ("vmstat", "mpstat", "pidstat", "iostat")},
        "sysstat_version": (_run(["mpstat", "-V"]) or None) if shutil.which("mpstat") else None,
        "git": git_info(),
        "time_epoch": time.time(),
    }
    if app:
        info["app_pip_freeze"] = app_pip_freeze(app)
    return info


# ---------------------------------------------------------------------------
# process helpers
# ---------------------------------------------------------------------------
def start_proc(cmd, stdout_path=None, env=None, cwd=None) -> subprocess.Popen:
    """Start a child in its own session/process group (so we can signal the group)."""
    fh = open(stdout_path, "wb") if stdout_path else subprocess.DEVNULL
    try:
        return subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT if stdout_path else subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, env=env, cwd=cwd, start_new_session=True)
    finally:
        if stdout_path:
            fh.close()


def stop_proc(p: subprocess.Popen | None, sig=signal.SIGINT, timeout=10.0) -> int | None:
    """Signal the whole process group, escalate to SIGTERM then SIGKILL."""
    if p is None or p.poll() is not None:
        return None if p is None else p.returncode
    for s, t in ((sig, timeout), (signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(p.pid, s)
        except (ProcessLookupError, PermissionError):
            try:
                p.send_signal(s)
            except ProcessLookupError:
                pass
        try:
            return p.wait(timeout=t)
        except subprocess.TimeoutExpired:
            continue
    return p.poll()


# ---------------------------------------------------------------------------
# collectors (monitor.py + sysstat)
# ---------------------------------------------------------------------------
class Collectors:
    """monitor.py + sysstat tools writing raw logs into `out_dir`."""

    def __init__(self, out_dir: Path, interval: float = 1.0, match_port: int | None = None,
                 sysstat: bool = True, uss: bool = True):
        self.out_dir = Path(out_dir)
        self.interval = interval
        self.match_port = match_port
        self.sysstat = sysstat
        self.uss = uss
        self.procs: dict[str, subprocess.Popen] = {}
        self.info: dict = {"warnings": [], "commands": {}}

    def _tool_cmd(self, cmd: list[str]) -> list[str]:
        # line-buffer stdout so a SIGINT/kill does not lose buffered rows
        if shutil.which("stdbuf"):
            return ["stdbuf", "-oL", "-eL"] + cmd
        return cmd

    def start(self) -> dict:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.info["started_epoch"] = time.time()
        # psutil monitor
        mcmd = [sys.executable, str(HERE / "monitor.py"), "--out", str(self.out_dir / "monitor.csv"),
                "--interval", str(self.interval)]
        if self.match_port:
            mcmd += ["--match-port", str(self.match_port)]
        if not self.uss:
            mcmd.append("--no-uss")
        self.procs["monitor"] = start_proc(mcmd, self.out_dir / "monitor.stderr.log")
        self.info["commands"]["monitor"] = mcmd

        if not self.sysstat:
            return self.info
        if not sys.platform.startswith("linux"):
            msg = f"sysstat collectors are Linux-only (platform={sys.platform}) - skipping"
            self.info["warnings"].append(msg)
            log(f"WARNING: {msg}")
            return self.info
        env = dict(os.environ, LC_ALL="C", LANG="C", S_TIME_FORMAT="ISO")
        iv = str(max(1, int(round(self.interval))))
        pids = []
        if self.match_port:
            sys.path.insert(0, str(HERE))
            from monitor import find_root_pid_for_port, process_tree_pids  # noqa: E402
            root = find_root_pid_for_port(self.match_port)
            pids = process_tree_pids(root) if root else []
        self.info["server_pids_at_start"] = pids
        tools = {
            "vmstat": ["vmstat", "-w", "-t", "-n", iv],
            "mpstat": ["mpstat", "-P", "ALL", iv],
            "iostat": ["iostat", "-d", "-x", "-t", iv],
        }
        if pids:
            tools["pidstat"] = ["pidstat", "-h", "-u", "-r", "-d", "-w", "-p", ",".join(map(str, pids)), iv]
        elif self.match_port:
            tools["pidstat"] = ["pidstat", "-h", "-u", "-r", "-d", "-w", "-C", "gunicorn", iv]
            self.info["warnings"].append("no server PID found on port; pidstat uses -C gunicorn")
        for name, cmd in tools.items():
            if not shutil.which(cmd[0]):
                msg = f"{cmd[0]} not found (install sysstat/procps) - skipping"
                self.info["warnings"].append(msg)
                log(f"WARNING: {msg}")
                continue
            full = self._tool_cmd(cmd)
            self.procs[name] = start_proc(full, self.out_dir / f"{name}.log", env=env)
            self.info["commands"][name] = full
        return self.info

    def stop(self) -> dict:
        self.info["stopped_epoch"] = time.time()
        rcs = {}
        for name, p in self.procs.items():
            rcs[name] = stop_proc(p, signal.SIGINT if name != "monitor" else signal.SIGTERM)
        self.info["exit_codes"] = rcs
        self.procs = {}
        (self.out_dir / "collectors.json").write_text(json.dumps(self.info, indent=2, default=str))
        return self.info


# ---------------------------------------------------------------------------
# app server
# ---------------------------------------------------------------------------
class AppServer:
    """Start an app via apps/<app>/run.sh (preferred) or gunicorn, in its own process group."""

    def __init__(self, app: str, port: int, workers: "int | str | None", server_env: dict | None,
                 log_path: Path, cmd: list[str] | None = None):
        if app not in APPS and not (REPO / "apps" / app).is_dir():
            raise ValueError(f"unknown app {app!r}")
        self.app = app
        self.port = port
        self.workers = workers
        self.server_env = {str(k): str(v) for k, v in (server_env or {}).items()}
        self.log_path = Path(log_path)
        self.cmd = cmd
        self.proc: subprocess.Popen | None = None
        self.started_epoch = None

    def command(self) -> list[str]:
        if self.cmd:
            return self.cmd
        app_dir = REPO / "apps" / self.app
        if (app_dir / "run.sh").exists():
            return ["bash", str(app_dir / "run.sh")]
        return ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]

    def env(self) -> dict:
        env = dict(os.environ)
        venv = app_venv(self.app)
        if venv is not None:
            env["VIRTUAL_ENV"] = str(venv)
            env["PATH"] = f"{venv / 'bin'}{os.pathsep}{env.get('PATH', '')}"
        env["PORT"] = str(self.port)
        env.setdefault("HOST", "0.0.0.0")
        env.setdefault("LOG_LEVEL", "warning")
        if self.workers is not None:
            env["WORKERS"] = str(self.workers)
        env.update(self.server_env)
        return env

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not wait_port_free(self.port, timeout=15):
            raise RuntimeError(f"port {self.port} is already in use - another server running? "
                               "(stop it, or run without --start-server to use it)")
        cmd = self.command()
        log(f"starting server: {' '.join(cmd)} (WORKERS={self.workers}, env={self.server_env})")
        self.proc = start_proc(cmd, self.log_path, env=self.env(), cwd=str(REPO / "apps" / self.app))
        self.started_epoch = time.time()

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> int | None:
        if self.proc is None:
            return None
        log("stopping server")
        rc = stop_proc(self.proc, signal.SIGTERM, timeout=30)
        self.proc = None
        return rc


def wait_health(url: str, timeout: float = 90.0, proc_alive=None) -> bool:
    """Poll GET url until HTTP 200. Returns False on timeout or if proc_alive() turns False."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        if proc_alive is not None and not proc_alive():
            return False
        time.sleep(0.5)
    return False


def wait_port_free(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.5)
    return False
