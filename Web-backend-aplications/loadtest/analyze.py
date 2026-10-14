#!/usr/bin/env python3
"""Offline analysis of the raw experiment data (run on the HOST, after collection).

Reads results/<app>/<label>/<level>/rep<k>/ written by run_experiment.py and
produces, under --out (default results/_analysis/):

  reps.csv                 one row per completed repetition (all derived metrics)
  summary.csv              mean / std / n per (app, label, level) - the table view
  endpoints.csv            per-endpoint latency/throughput per repetition (locust_stats.csv)
  <app>/<metric>.png       one chart per metric: x = demand (users), one line per
                           provisioning label, error bars = std across repetitions
  <app>/overview.png       all metrics of the app in one figure (small multiples)

Measurement window: [measure_start + ramp_up_s, measure_end] on the host clock.
The ramp-up (users / spawn_rate) is excluded so throughput and resource usage are
steady-state. VM samples (monitor.csv) are moved to the host clock with
meta.json clock_offset_vm_minus_host when the run used vm_agent.

  python loadtest/analyze.py                                  # every app in results/
  python loadtest/analyze.py --app cofre_digital --labels 1cpu-2gb,2cpu-2gb,4cpu-2gb
  python loadtest/analyze.py --results-dir /path/results --out /path/report

Requires pandas + matplotlib (pip install -r loadtest/requirements-analysis.txt).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FixedLocator, NullLocator  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MB = 1024 * 1024

# categorical palette (validated: adjacent CVD dE >= 9.1, normal-vision dE >= 19.6 on light surface);
# a label keeps its color in every chart of an app. Distinct markers = secondary encoding.
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SERIES_MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"

# (column in reps.csv, chart title, y-axis label, file name)
COMMON_METRICS = [
    ("rps", "Vazão (estado estacionário)", "requisições/s", "vazao"),
    ("p50_ms", "Latência mediana (p50)", "ms", "latencia_p50"),
    ("p95_ms", "Latência p95", "ms", "latencia_p95"),
    ("p99_ms", "Latência p99", "ms", "latencia_p99"),
    ("fail_pct", "Taxa de falhas", "% das requisições", "falhas"),
    ("proc_cpu_pct", "CPU do servidor (gunicorn: master + workers)", "% (100 = 1 núcleo)", "cpu_servidor"),
    ("cpu_pct", "CPU total da VM", "% de todos os vCPUs", "cpu_vm"),
    ("proc_rss_mb_max", "Memória do servidor (RSS máx.)", "MiB", "memoria_servidor"),
    ("mem_used_mb", "Memória usada na VM", "MiB", "memoria_vm"),
]
APP_METRICS = {
    "galeria_filtros": [
        ("swap_used_mb_max", "Swap usado (máx.)", "MiB", "swap"),
        ("swap_io_mb_s", "Tráfego de swap (in + out)", "MiB/s", "swap_io"),
        ("mem_available_mb_min", "Memória disponível na VM (mín.)", "MiB", "memoria_disponivel"),
    ],
    "bolsa_ficticia": [
        ("cpu_iowait_pct", "CPU em iowait", "%", "iowait"),
        ("disk_write_mb_s", "Escrita em disco", "MiB/s", "disco_escrita_mb"),
        ("disk_write_iops", "Operações de escrita em disco", "ops/s", "disco_escrita_iops"),
    ],
    "cofre_digital": [
        ("load1", "Load average (1 min)", "processos executáveis", "load"),
        ("cpu_steal_pct", "CPU steal (tempo roubado pelo hipervisor)", "%", "steal"),
    ],
}


# ---------------------------------------------------------------------------
# per-repetition extraction
# ---------------------------------------------------------------------------
def _num(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return math.nan
    return x


def _locust_metrics(rep: Path, t0: float, t1: float) -> dict:
    out: dict = {}
    stats = pd.read_csv(rep / "locust_stats.csv")
    agg = stats[stats["Name"] == "Aggregated"]
    if agg.empty:
        return out
    a = agg.iloc[0]
    n, nf = _num(a["Request Count"]), _num(a["Failure Count"])
    out.update(requests=n, failures=nf, fail_pct=100.0 * nf / n if n else math.nan,
               p50_ms=_num(a["50%"]), p95_ms=_num(a["95%"]), p99_ms=_num(a["99%"]),
               avg_ms=_num(a["Average Response Time"]), max_ms=_num(a["Max Response Time"]),
               rps_full=_num(a["Requests/s"]))
    # steady-state throughput from the cumulative counters of the 1-s history
    out["rps"] = out["rps_full"]
    hist_path = rep / "locust_stats_history.csv"
    if hist_path.exists():
        h = pd.read_csv(hist_path)
        h = h[h["Name"] == "Aggregated"].sort_values("Timestamp")
        h = h[(h["Timestamp"] >= t0) & (h["Timestamp"] <= t1)]
        if len(h) >= 2:
            dt = h["Timestamp"].iloc[-1] - h["Timestamp"].iloc[0]
            if dt > 0:
                dn = h["Total Request Count"].iloc[-1] - h["Total Request Count"].iloc[0]
                dfl = h["Total Failure Count"].iloc[-1] - h["Total Failure Count"].iloc[0]
                out["rps"] = dn / dt
                out["fail_rps"] = dfl / dt
    return out


def _monitor_metrics(rep: Path, t0: float, t1: float, offset: float) -> dict:
    path = rep / "monitor.csv"
    if not path.exists():
        return {}
    m = pd.read_csv(path)
    if m.empty:
        return {}
    m["t_host"] = m["ts_epoch"] - offset
    w = m[(m["t_host"] >= t0) & (m["t_host"] <= t1)]
    if w.empty:
        return {}
    dt = w["dt_s"].sum()

    def mean(c):
        return w[c].mean() if c in w and w[c].notna().any() else math.nan

    def rate(c, scale=1.0):
        return w[c].sum() / dt / scale if c in w and dt > 0 and w[c].notna().any() else math.nan

    return {
        "cpu_pct": mean("cpu_pct"),
        "cpu_user_pct": mean("cpu_user_pct"),
        "cpu_system_pct": mean("cpu_system_pct"),
        "cpu_iowait_pct": mean("cpu_iowait_pct"),
        "cpu_steal_pct": mean("cpu_steal_pct"),
        "load1": mean("load1"),
        "n_cpus": w["n_cpus"].max(),
        "mem_total_mb": w["mem_total"].max() / MB,
        "mem_used_mb": mean("mem_used") / MB,
        "mem_available_mb_min": w["mem_available"].min() / MB,
        "swap_used_mb_max": w["swap_used"].max() / MB,
        "swap_io_mb_s": (rate("swap_in_bytes_delta", MB) + rate("swap_out_bytes_delta", MB)),
        "disk_write_mb_s": rate("disk_write_bytes_delta", MB),
        "disk_read_mb_s": rate("disk_read_bytes_delta", MB),
        "disk_write_iops": rate("disk_write_count_delta"),
        "net_recv_mb_s": rate("net_bytes_recv_delta", MB),
        "proc_cpu_pct": mean("proc_cpu_pct"),
        "proc_rss_mb_mean": mean("proc_rss") / MB,
        "proc_rss_mb_max": w["proc_rss"].max() / MB,
        "proc_n_max": w["proc_n"].max(),
        # pids seen in the window; more than proc_n_max means workers were replaced (crash/OOM kill)
        "proc_distinct_pids": len({p for s in w["proc_pids"].dropna().astype(str) for p in s.split(";") if p}),
        "monitor_samples": len(w),
    }


def load_reps(results_dir: Path, apps: list[str] | None, labels: list[str] | None) -> pd.DataFrame:
    rows = []
    for meta_path in sorted(results_dir.glob("*/*/*/rep*/meta.json")):
        rep = meta_path.parent
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            print(f"skip (bad meta.json): {rep}", file=sys.stderr)
            continue
        if meta.get("status") != "completed":
            print(f"skip (status={meta.get('status')}): {rep}", file=sys.stderr)
            continue
        if apps and meta["app"] not in apps or labels and meta["label"] not in labels:
            continue
        tm = meta.get("timings", {})
        ms, me = tm.get("measure_start_epoch"), tm.get("measure_end_epoch")
        if ms is None or me is None:
            continue
        t0 = ms + float(meta.get("ramp_up_s") or 0.0)
        offset = float((meta.get("clock_offset_vm_minus_host") or {}).get("offset_s") or 0.0)
        row = {"app": meta["app"], "label": meta["label"], "level": meta["level"],
               "users": int(meta["users"]), "rep": int(meta["rep"]), "workers": meta.get("workers"),
               "window_s": me - t0, "dir": str(rep)}
        row.update(_locust_metrics(rep, t0, me))
        row.update(_monitor_metrics(rep, t0, me, offset))
        rows.append(row)
    return pd.DataFrame(rows)


def load_endpoints(reps: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for r in reps.itertuples():
        s = pd.read_csv(Path(r.dir) / "locust_stats.csv")
        s = s[s["Name"] != "Aggregated"]
        s = s[["Type", "Name", "Request Count", "Failure Count", "Requests/s", "50%", "95%", "99%",
               "Average Response Time"]].copy()
        s.insert(0, "rep", r.rep)
        s.insert(0, "users", r.users)
        s.insert(0, "level", r.level)
        s.insert(0, "label", r.label)
        s.insert(0, "app", r.app)
        frames.append(s)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def summarize(reps: pd.DataFrame) -> pd.DataFrame:
    keys = ["app", "label", "level", "users"]
    num = [c for c in reps.columns if c not in keys + ["rep", "dir", "workers"]
           and pd.api.types.is_numeric_dtype(reps[c])]
    g = reps.groupby(keys)[num]
    out = g.mean().add_suffix("_mean").join(g.std().add_suffix("_std"))
    out["n_reps"] = reps.groupby(keys).size()
    cols = ["n_reps"] + [c for m in num for c in (f"{m}_mean", f"{m}_std")]
    return out[cols].reset_index().sort_values(["app", "label", "users"])


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------
_LABEL_RE = re.compile(r"^(\d+)cpu-(\d+(?:\.\d+)?)(gb|mb)", re.I)


def label_order(labels) -> list[str]:
    """1cpu-1gb < 1cpu-2gb < 2cpu-1gb ...; free-form labels after, alphabetically."""
    def key(lb):
        m = _LABEL_RE.match(lb)
        if not m:
            return (1, 0.0, 0.0, lb)
        ram = float(m.group(2)) * (1 if m.group(3).lower() == "gb" else 1 / 1024)
        return (0, int(m.group(1)), ram, lb)
    return sorted(set(labels), key=key)


def _style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
        "axes.titlesize": 12, "axes.titleweight": "semibold", "axes.labelsize": 10,
        "xtick.color": INK_2, "ytick.color": INK_2, "xtick.labelsize": 9, "ytick.labelsize": 9,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8,
        "legend.frameon": False, "legend.fontsize": 9, "legend.labelcolor": INK,
        "font.size": 10,
    })


def _plot_metric(ax, summ: pd.DataFrame, labels: list[str], col: str, title: str, ylabel: str):
    users = sorted(summ["users"].unique())
    plotted = False
    top = 0.0
    for i, lb in enumerate(labels):
        d = summ[summ["label"] == lb].sort_values("users")
        if f"{col}_mean" not in d or d[f"{col}_mean"].isna().all():
            continue
        top = max(top, (d[f"{col}_mean"] + d[f"{col}_std"].fillna(0)).max())
        ax.errorbar(d["users"], d[f"{col}_mean"], yerr=d[f"{col}_std"].fillna(0),
                    color=SERIES_COLORS[i % len(SERIES_COLORS)], marker=SERIES_MARKERS[i % len(SERIES_MARKERS)],
                    markersize=6, markeredgecolor=SURFACE, markeredgewidth=1.2,
                    linewidth=2, elinewidth=1, capsize=0, label=lb)
        plotted = True
    ax.set_title(title, loc="left")
    ax.set_ylabel(ylabel)
    ax.set_xlabel("demanda (usuários simultâneos)")
    if len(users) > 1 and min(users) > 0 and max(users) / min(users) >= 8:
        ax.set_xscale("log", base=2)
    ax.xaxis.set_major_locator(FixedLocator(users))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xticklabels([str(u) for u in users])
    ax.set_ylim(0, top * 1.12 if top > 0 else 1)  # headroom so markers at the max are not clipped
    return plotted


def plot_app(app: str, summ: pd.DataFrame, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = label_order(summ["label"])
    if len(labels) > len(SERIES_COLORS):
        print(f"WARNING: {app} has {len(labels)} labels; only {len(SERIES_COLORS)} distinct colors - "
              f"use --labels to plot a subset", file=sys.stderr)
    metrics = COMMON_METRICS + APP_METRICS.get(app, [])
    written = []
    for col, title, ylabel, fname in metrics:
        fig, ax = plt.subplots(figsize=(7, 4.2))
        if _plot_metric(ax, summ, labels, col, title, ylabel):
            if len(labels) > 1:
                ax.legend(title="provisionamento", title_fontsize=9, loc="best")
            fig.tight_layout()
            p = out_dir / f"{fname}.png"
            fig.savefig(p, dpi=160)
            written.append(p)
        plt.close(fig)
    # overview (small multiples, shared legend)
    n = len(metrics)
    cols = 3
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(5.2 * cols, 3.6 * rows), squeeze=False)
    for ax, (col, title, ylabel, _) in zip(axes.flat, metrics):
        _plot_metric(ax, summ, labels, col, title, ylabel)
    for ax in list(axes.flat)[n:]:
        ax.set_visible(False)
    handles, names = axes.flat[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, names, title="provisionamento", loc="upper center", ncol=min(len(names), 6),
                   bbox_to_anchor=(0.5, 1.0))
    fig.suptitle(app, x=0.01, ha="left", fontsize=14, fontweight="semibold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    p = out_dir / "overview.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    written.append(p)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", default=str(REPO / "results"))
    ap.add_argument("--out", help="output directory (default <results-dir>/_analysis)")
    ap.add_argument("--app", help="comma-separated apps (default: all found)")
    ap.add_argument("--labels", help="comma-separated provisioning labels (default: all found)")
    ap.add_argument("--no-plots", action="store_true", help="only write the CSV tables")
    args = ap.parse_args(argv)

    results = Path(args.results_dir)
    out = Path(args.out) if args.out else results / "_analysis"
    apps = [a.strip() for a in args.app.split(",")] if args.app else None
    labels = [x.strip() for x in args.labels.split(",")] if args.labels else None

    reps = load_reps(results, apps, labels)
    if reps.empty:
        print(f"no completed repetitions found under {results}", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    reps.sort_values(["app", "label", "users", "rep"]).to_csv(out / "reps.csv", index=False)
    summ = summarize(reps)
    summ.to_csv(out / "summary.csv", index=False)
    load_endpoints(reps).to_csv(out / "endpoints.csv", index=False)

    view = ["app", "label", "level", "users", "n_reps", "rps_mean", "p95_ms_mean", "fail_pct_mean",
            "proc_cpu_pct_mean", "cpu_pct_mean", "proc_rss_mb_max_mean"]
    with pd.option_context("display.width", 200, "display.max_rows", 500, "display.float_format", "{:.1f}".format):
        print(summ[[c for c in view if c in summ]].to_string(index=False))

    restarted = reps[reps["proc_distinct_pids"] > reps["proc_n_max"]] if "proc_distinct_pids" in reps else []
    for r in getattr(restarted, "itertuples", lambda: [])():
        print(f"NOTE: workers replaced during measurement (crash/OOM?) in {r.dir}", file=sys.stderr)

    if not args.no_plots:
        _style()
        for app, s in summ.groupby("app"):
            files = plot_app(app, s, out / app)
            print(f"{app}: {len(files)} charts in {out / app}")
    print(f"tables: {out / 'reps.csv'}, {out / 'summary.csv'}, {out / 'endpoints.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
