#!/usr/bin/env python3
"""psutil resource sampler (raw data only - no aggregation).

Samples system-wide metrics and the metrics of one process tree (e.g. a gunicorn
master + its workers) every --interval seconds and appends one CSV row per sample.
Each row is flushed immediately so data survives a crash / kill -9.

Process selection (optional):
  --pid PID           monitor PID and all its descendants
  --match-port PORT   find the process LISTENING on PORT (gunicorn master; workers
                      share the socket so the topmost listener is chosen) and monitor
                      its whole tree. The tree (children) is re-discovered on EVERY
                      sample, and the root is re-discovered if it disappears (server
                      restarted), so worker restarts are captured.

Counter-type metrics (disk/net/ctx switches/process cpu & io) are written as
DELTAS since the previous sample, together with the exact elapsed time
`dt_s`, so rates can be computed offline. The first row has empty deltas.

Stops after --duration seconds, or on SIGTERM / SIGINT (clean flush & exit).

Examples:
  python loadtest/monitor.py --out results/x/monitor.csv --match-port 8001
  python loadtest/monitor.py --out /tmp/m.csv --pid 1234 --interval 0.5 --duration 60
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

try:
    import psutil
except ImportError:  # pragma: no cover
    sys.stderr.write("monitor.py: psutil is required (pip install psutil)\n")
    sys.exit(2)

LINUX = sys.platform.startswith("linux")

COLUMNS = [
    # time
    "ts_epoch", "ts_iso", "dt_s", "sample",
    # system cpu
    "cpu_pct", "cpu_user_pct", "cpu_system_pct", "cpu_idle_pct", "cpu_iowait_pct",
    "cpu_steal_pct", "cpu_softirq_pct", "cpu_percore_json", "n_cpus",
    "load1", "load5", "load15",
    # memory
    "mem_total", "mem_used", "mem_available", "mem_free", "mem_cached", "mem_buffers",
    "mem_percent", "swap_total", "swap_used", "swap_percent", "swap_in_bytes_delta",
    "swap_out_bytes_delta",
    # disk (all disks summed)
    "disk_read_bytes_delta", "disk_write_bytes_delta", "disk_read_count_delta",
    "disk_write_count_delta", "disk_read_time_ms_delta", "disk_write_time_ms_delta",
    "disk_busy_time_ms_delta",
    # net (all nics summed)
    "net_bytes_sent_delta", "net_bytes_recv_delta", "net_packets_sent_delta",
    "net_packets_recv_delta", "net_errin_delta", "net_errout_delta",
    "net_dropin_delta", "net_dropout_delta",
    # kernel
    "ctx_switches_delta", "interrupts_delta", "soft_interrupts_delta",
    # monitored process tree
    "proc_root_pid", "proc_n", "proc_pids", "proc_cpu_pct", "proc_cpu_user_s_delta",
    "proc_cpu_system_s_delta", "proc_rss", "proc_vms", "proc_uss", "proc_pss",
    "proc_shared", "proc_num_threads", "proc_num_fds", "proc_io_read_bytes_delta",
    "proc_io_write_bytes_delta", "proc_io_read_count_delta", "proc_io_write_count_delta",
    "proc_ctx_vol_delta", "proc_ctx_invol_delta", "proc_uss_error",
]

_stop = threading.Event()


def _handle_signal(signum, _frame):
    _stop.set()


# ---------------------------------------------------------------------------
# process discovery
# ---------------------------------------------------------------------------
def _listening_pids_psutil(port: int) -> set[int]:
    pids: set[int] = set()
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, PermissionError, OSError):
        conns = None
    if conns is not None:
        for c in conns:
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
                pids.add(c.pid)
        if pids:
            return pids
    # per-process scan (works on macOS for own processes without root)
    for p in psutil.process_iter(["pid"]):
        try:
            getter = getattr(p, "net_connections", None) or p.connections
            for c in getter(kind="tcp"):
                if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port:
                    pids.add(p.pid)
                    break
        except (psutil.Error, OSError):
            continue
    return pids


def _listening_pids_external(port: int) -> set[int]:
    pids: set[int] = set()
    if shutil.which("lsof"):
        try:
            out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                                 capture_output=True, text=True, timeout=10).stdout
            pids |= {int(x) for x in out.split() if x.isdigit()}
        except (subprocess.SubprocessError, OSError):
            pass
    if not pids and shutil.which("ss"):
        try:
            out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"],
                                 capture_output=True, text=True, timeout=10).stdout
            import re
            pids |= {int(m) for m in re.findall(r"pid=(\d+)", out)}
        except (subprocess.SubprocessError, OSError):
            pass
    return pids


def find_listening_pids(port: int) -> set[int]:
    """All PIDs with a LISTEN socket on `port` (master + forked workers)."""
    pids = _listening_pids_psutil(port)
    if not pids:
        pids = _listening_pids_external(port)
    return pids


def _process_listens_on(proc: psutil.Process, port: int) -> bool:
    try:
        getter = getattr(proc, "net_connections", None) or proc.connections
        return any(c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port
                   for c in getter(kind="tcp"))
    except (psutil.Error, OSError):
        return False


def find_root_pid_for_port(port: int) -> int | None:
    """Topmost process (e.g. gunicorn master) listening on `port`.

    On Linux psutil.net_connections() maps each socket inode to ONE pid, so a
    listening socket shared by a gunicorn master and its forked workers may be
    reported only for one worker. Climb the parent chain while the parent also
    listens on the port, so the whole tree (master + all workers) is monitored.
    """
    pids = find_listening_pids(port)
    if not pids:
        return None
    roots = set()
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            while (parent := proc.parent()) is not None and _process_listens_on(parent, port):
                proc = parent
        except psutil.Error:
            continue
        roots.add(proc.pid)
    return min(roots) if roots else min(pids)


def process_tree_pids(root_pid: int) -> list[int]:
    try:
        root = psutil.Process(root_pid)
        return [root_pid] + [c.pid for c in root.children(recursive=True)]
    except psutil.Error:
        return []


# ---------------------------------------------------------------------------
# sampler
# ---------------------------------------------------------------------------
class Sampler:
    def __init__(self, pid: int | None, match_port: int | None, uss: bool):
        self.fixed_pid = pid
        self.match_port = match_port
        self.uss = uss
        self.root_pid: int | None = None
        self.procs: dict[int, psutil.Process] = {}
        self.prev_proc: dict[int, dict] = {}
        self.prev_sys: dict | None = None
        self.prev_ts: float | None = None
        self.uss_warned = False
        self.n = 0
        psutil.cpu_percent(percpu=True)  # prime
        psutil.cpu_times_percent()

    # -- process tree -------------------------------------------------------
    def _resolve_root(self) -> int | None:
        if self.fixed_pid is not None:
            return self.fixed_pid if psutil.pid_exists(self.fixed_pid) else None
        if self.match_port is None:
            return None
        if self.root_pid is not None:
            try:
                p = psutil.Process(self.root_pid)
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    return self.root_pid
            except psutil.Error:
                pass
        return find_root_pid_for_port(self.match_port)

    def _proc_sample(self, now: float) -> dict:
        out = {k: "" for k in COLUMNS if k.startswith("proc_")}
        self.root_pid = self._resolve_root()
        if self.root_pid is None:
            self.prev_proc = {}
            out["proc_n"] = 0
            return out
        pids = process_tree_pids(self.root_pid)
        cur: dict[int, dict] = {}
        tot = dict(rss=0, vms=0, uss=0, pss=0, shared=0, thr=0, fds=0)
        d = dict(cu=0.0, cs=0.0, rb=0, wb=0, rc=0, wc=0, cv=0, ci=0)
        have_uss = have_pss = have_fds = have_io = True
        uss_err = ""
        alive = []
        for pid in pids:
            proc = self.procs.get(pid)
            try:
                if proc is None or not proc.is_running():
                    proc = psutil.Process(pid)
                    self.procs[pid] = proc
                with proc.oneshot():
                    ct = proc.cpu_times()
                    mi = proc.memory_info()
                    nthr = proc.num_threads()
                    ctxs = proc.num_ctx_switches()
                    try:
                        fds = proc.num_fds() if hasattr(proc, "num_fds") else proc.num_handles()
                    except (psutil.AccessDenied, AttributeError):
                        fds = None
                    try:
                        io = proc.io_counters() if hasattr(proc, "io_counters") else None
                    except (psutil.AccessDenied, NotImplementedError):
                        io = None
                    create = proc.create_time()
                full = None
                if self.uss:
                    try:
                        full = proc.memory_full_info()
                    except (psutil.AccessDenied, PermissionError, NotImplementedError) as e:
                        uss_err = type(e).__name__
                        if not self.uss_warned:
                            sys.stderr.write(f"monitor.py: USS unavailable for pid {pid}: {e!r}\n")
                            self.uss_warned = True
            except psutil.NoSuchProcess:
                self.procs.pop(pid, None)
                continue
            except psutil.Error:
                continue
            alive.append(pid)
            tot["rss"] += mi.rss
            tot["vms"] += mi.vms
            tot["shared"] += getattr(mi, "shared", 0)
            tot["thr"] += nthr
            if fds is None:
                have_fds = False
            else:
                tot["fds"] += fds
            if full is not None and getattr(full, "uss", None) is not None:
                tot["uss"] += full.uss
                if getattr(full, "pss", None) is not None:
                    tot["pss"] += full.pss
                else:
                    have_pss = False
            else:
                have_uss = have_pss = False
            if io is None:
                have_io = False
            rec = dict(cu=ct.user, cs=ct.system, cv=ctxs.voluntary, ci=ctxs.involuntary,
                       rb=getattr(io, "read_bytes", 0) if io else 0,
                       wb=getattr(io, "write_bytes", 0) if io else 0,
                       rc=getattr(io, "read_count", 0) if io else 0,
                       wc=getattr(io, "write_count", 0) if io else 0,
                       create=create)
            cur[pid] = rec
            prev = self.prev_proc.get(pid)
            if prev is not None and prev["create"] == create:
                base = prev
            elif self.prev_ts is not None and create >= self.prev_ts - 0.01:
                # process born since last sample: all its counters belong to this interval
                base = dict(cu=0.0, cs=0.0, cv=0, ci=0, rb=0, wb=0, rc=0, wc=0)
            else:
                base = None  # first sample for a pre-existing process: no delta yet
            if base is not None:
                for k in d:
                    d[k] += rec[k] - base[k]
        for pid in list(self.procs):
            if pid not in cur:
                self.procs.pop(pid, None)
        dt = (now - self.prev_ts) if self.prev_ts else None
        out["proc_root_pid"] = self.root_pid
        out["proc_n"] = len(alive)
        out["proc_pids"] = ";".join(str(p) for p in alive)
        out["proc_rss"] = tot["rss"]
        out["proc_vms"] = tot["vms"]
        out["proc_shared"] = tot["shared"] if LINUX else ""
        out["proc_uss"] = tot["uss"] if (self.uss and have_uss and alive) else ""
        out["proc_pss"] = tot["pss"] if (self.uss and have_pss and alive) else ""
        out["proc_uss_error"] = uss_err
        out["proc_num_threads"] = tot["thr"]
        out["proc_num_fds"] = tot["fds"] if have_fds else ""
        if dt is not None:
            cpu_s = d["cu"] + d["cs"]
            out["proc_cpu_pct"] = round(100.0 * cpu_s / dt, 3)
            out["proc_cpu_user_s_delta"] = round(d["cu"], 4)
            out["proc_cpu_system_s_delta"] = round(d["cs"], 4)
            out["proc_ctx_vol_delta"] = d["cv"]
            out["proc_ctx_invol_delta"] = d["ci"]
            if have_io:
                out["proc_io_read_bytes_delta"] = d["rb"]
                out["proc_io_write_bytes_delta"] = d["wb"]
                out["proc_io_read_count_delta"] = d["rc"]
                out["proc_io_write_count_delta"] = d["wc"]
        self.prev_proc = cur
        return out

    # -- system -------------------------------------------------------------
    def _sys_counters(self) -> dict:
        c = {}
        try:
            dio = psutil.disk_io_counters(perdisk=False, nowrap=True)
        except Exception:
            dio = None
        if dio:
            c.update(disk_read_bytes=dio.read_bytes, disk_write_bytes=dio.write_bytes,
                     disk_read_count=dio.read_count, disk_write_count=dio.write_count,
                     disk_read_time_ms=getattr(dio, "read_time", None),
                     disk_write_time_ms=getattr(dio, "write_time", None),
                     disk_busy_time_ms=getattr(dio, "busy_time", None))
        try:
            nio = psutil.net_io_counters(pernic=False, nowrap=True)
        except Exception:
            nio = None
        if nio:
            c.update(net_bytes_sent=nio.bytes_sent, net_bytes_recv=nio.bytes_recv,
                     net_packets_sent=nio.packets_sent, net_packets_recv=nio.packets_recv,
                     net_errin=nio.errin, net_errout=nio.errout,
                     net_dropin=nio.dropin, net_dropout=nio.dropout)
        st = psutil.cpu_stats()
        c.update(ctx_switches=st.ctx_switches, interrupts=st.interrupts,
                 soft_interrupts=st.soft_interrupts)
        sw = psutil.swap_memory()
        c.update(swap_in_bytes=sw.sin, swap_out_bytes=sw.sout)
        return c

    def sample(self) -> dict:
        now = time.time()
        row: dict = {k: "" for k in COLUMNS}
        row["ts_epoch"] = f"{now:.3f}"
        row["ts_iso"] = _dt.datetime.fromtimestamp(now).astimezone().isoformat(timespec="milliseconds")
        row["dt_s"] = f"{now - self.prev_ts:.3f}" if self.prev_ts else ""
        row["sample"] = self.n

        percore = psutil.cpu_percent(percpu=True)
        ctp = psutil.cpu_times_percent()
        if self.prev_ts is not None:  # first call only primes psutil's counters -> leave blank
            row["cpu_pct"] = round(sum(percore) / len(percore), 2) if percore else ""
            row["cpu_user_pct"] = ctp.user
            row["cpu_system_pct"] = ctp.system
            row["cpu_idle_pct"] = ctp.idle
            row["cpu_iowait_pct"] = getattr(ctp, "iowait", "")
            row["cpu_steal_pct"] = getattr(ctp, "steal", "")
            row["cpu_softirq_pct"] = getattr(ctp, "softirq", "")
            row["cpu_percore_json"] = json.dumps(percore)
        row["n_cpus"] = len(percore)
        try:
            row["load1"], row["load5"], row["load15"] = (round(x, 2) for x in os.getloadavg())
        except (OSError, AttributeError):
            pass

        vm = psutil.virtual_memory()
        row.update(mem_total=vm.total, mem_used=vm.used, mem_available=vm.available,
                   mem_free=vm.free, mem_cached=getattr(vm, "cached", ""),
                   mem_buffers=getattr(vm, "buffers", ""), mem_percent=vm.percent)
        sw = psutil.swap_memory()
        row.update(swap_total=sw.total, swap_used=sw.used, swap_percent=sw.percent)

        cnt = self._sys_counters()
        if self.prev_sys is not None:
            for k, v in cnt.items():
                pv = self.prev_sys.get(k)
                col = f"{k}_delta"
                if col in row and v is not None and pv is not None:
                    row[col] = v - pv
        self.prev_sys = cnt

        row.update(self._proc_sample(now))
        self.prev_ts = now
        self.n += 1
        return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output CSV path ('-' for stdout)")
    ap.add_argument("--interval", type=float, default=1.0, help="sampling interval in s (default 1)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--pid", type=int, help="root PID of the process tree to monitor")
    g.add_argument("--match-port", type=int, help="monitor the tree of the process listening on this TCP port")
    ap.add_argument("--duration", type=float, default=None, help="stop after N seconds (default: until SIGTERM/SIGINT)")
    ap.add_argument("--no-uss", action="store_true", help="skip memory_full_info (USS/PSS); it is costlier")
    args = ap.parse_args(argv)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _handle_signal)

    if args.out == "-":
        fh = sys.stdout
    else:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        fh = open(args.out, "w", newline="", buffering=1)
    writer = csv.DictWriter(fh, fieldnames=COLUMNS)
    writer.writeheader()
    fh.flush()

    sampler = Sampler(args.pid, args.match_port, uss=not args.no_uss)
    t0 = time.monotonic()
    deadline = t0 + args.duration if args.duration else None
    k = 0
    try:
        while not _stop.is_set():
            writer.writerow(sampler.sample())
            fh.flush()
            k += 1
            nxt = t0 + k * args.interval  # fixed schedule, no drift
            if deadline is not None and nxt > deadline + 1e-6:
                break
            delay = nxt - time.monotonic()
            if delay > 0:
                _stop.wait(delay)
    finally:
        try:
            fh.flush()
            if fh is not sys.stdout:
                os.fsync(fh.fileno())
                fh.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
