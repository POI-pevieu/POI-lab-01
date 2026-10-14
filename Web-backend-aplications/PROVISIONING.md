# Provisioning scenarios and measurement methodology

The collection harness writes **raw data only**. Tables and charts come afterwards, offline, from `loadtest/analyze.py` (see the end of this file).

## Topology (recommended)

```
 HOST (load generator)                          VirtualBox VM (Ubuntu Server = system under test)
 ───────────────────────                        ──────────────────────────────────────────────
 run_experiment.py ── locust ── HTTP :800x ───▶ gunicorn + uvicorn workers (one app at a time)
        │                                       vm_agent.py :9100
        └──── HTTP :9100 (start/stop/fetch) ──▶   ├─ monitor.py (psutil, system + server process tree)
                                                  └─ vmstat / mpstat / pidstat / iostat (sysstat)
```

- Locust runs on the **host**, so it does not compete with the server for the VM's vCPUs and RAM. If the load generator ran inside the VM, the CPU numbers would also include locust's own CPU use.
- Server-side metrics are collected **on the VM** by `vm_agent.py`. The files are downloaded into the same `rep<k>/` directory as the locust CSVs.
- `meta.json` stores `clock_offset_vm_minus_host`, which lets you align VM samples (VM clock) with locust timestamps (host clock). Keep NTP / VirtualBox Guest Additions time sync enabled.
- Only one app should run in the VM during a measurement. Stop other units and close interactive sessions that consume CPU.

## Suggested provisioning scenarios (>= 4 per app)

Change these with `scripts/provision_vbox.sh <vm> <cpus> <MB>` while the VM is powered off. The label is free text but must be consistent. Use `<cpus>cpu-<ram>gb`.

| label        | vCPU | RAM  | cofre_digital (CPU) | galeria_filtros (memory) | bolsa_ficticia (IO) |
|--------------|-----:|-----:|:---:|:---:|:---:|
| `1cpu-1gb`   | 1 | 1 GB | ✔ | ✔ | ✔ |
| `2cpu-1gb`   | 2 | 1 GB | ✔ | ✔ | |
| `2cpu-2gb`   | 2 | 2 GB | ✔ | ✔ | ✔ |
| `4cpu-2gb`   | 4 | 2 GB | ✔ | | ✔ |
| `2cpu-4gb`   | 2 | 4 GB | | ✔ | |
| `4cpu-4gb`   | 4 | 4 GB | ✔ | ✔ | ✔ |

- **CPU-bound (cofre_digital)**: vary **vCPU** with RAM fixed (1/2/4 vCPU at 2 GB) and keep `WORKERS` = vCPU count — the scenario uses `workers: auto`, which does this automatically (override with `--workers N`). One extra run with `WORKERS=1` on 4 vCPU shows the effect of the GIL. Never give the VM more vCPUs than the host has physical cores.
- **Memory-bound (galeria_filtros)**: vary **RAM** with vCPU fixed (2 vCPU at 1/2/4 GB). The scenario uses `CACHE_MAX_MB=auto` (in `server_env`), so the cache scales with RAM. Watch swap columns. Use `--restart-per-rep` so every repetition starts with an empty cache.
- **IO-bound (bolsa_ficticia)**: vary both. For more contrast, change the VM disk's host I/O cache setting (`VBoxManage storagectl <vm> --name SATA --hostiocache on|off`) and record it in the label (e.g. `2cpu-2gb-iocacheoff`).
- Optional: `--cpu-cap PCT` (VirtualBox CPU execution cap) simulates slower vCPUs.

## Procedure per (app, label)

1. Power off the VM. Run `./scripts/provision_vbox.sh <vm> <cpus> <MB>` and boot the VM.
2. Inside the VM, check `nproc` and `free -m`. `meta.json` records these automatically as `server_host`.
3. Start the agent in the VM: `loadtest/.venv/bin/python loadtest/vm_agent.py --token SECRET`.
4. On the host, run:
   `./scripts/run_all_scenarios.sh <app> <label> --host http://127.0.0.1:<port> --agent http://127.0.0.1:9100 --agent-token SECRET --start-server --workers <N> [--restart-per-rep]`.
5. Do not touch the host or VM during the run. Close browsers and avoid background downloads.
6. If the run is interrupted, re-run the same command with `--resume`. Completed repetitions are skipped.

## What every repetition does

1. (Optional) Restart the server (`--restart-per-rep`) and wait for `GET /health`.
2. (Optional) `POST reset_endpoint` (for example, bolsa_ficticia clears its DB and ledger).
3. Start the collectors, then record `--baseline` seconds (default 3 s) of idle samples.
4. **Warm-up**: locust for `warmup_s` at the same user count (`warmup_*` files). Discard this phase in the analysis.
5. **Measurement**: locust for `duration_s` (`locust_*` files, `locust.html`). The window is recorded as `timings.measure_start_epoch` / `measure_end_epoch` in `meta.json`. Users ramp up at `spawn_rate`, and `meta.json` stores `ramp_up_s`.
6. `--tail` seconds (default 3 s), then stop the collectors and download the data.
7. **Cool-down** for `cooldown_s` before the next repetition.

Use at least 3 repetitions. `--order rep-major` interleaves levels, which reduces bias from slow drift such as host thermal throttling or a growing page cache.

## Raw files per repetition (`results/<app>/<label>/<level>/rep<k>/`)

| file | source | content |
|---|---|---|
| `locust_stats.csv`, `locust_stats_history.csv`, `locust_failures.csv`, `locust_exceptions.csv` | Locust | response times (percentiles), req/s, failures. The history file has 1 row per second |
| `locust.html`, `locust_stdout.log` | Locust | Locust's own report and log |
| `warmup_*` | Locust | same files for the warm-up phase |
| `monitor.csv` | psutil (`monitor.py`) | 1 row per interval: system CPU (total, per core, iowait, steal), load, memory, swap, disk and network deltas, context switches; server process tree: #procs, CPU%, RSS/USS/PSS, threads, fds, IO bytes |
| `vmstat.log`, `mpstat.log`, `pidstat.log`, `iostat.log` | sysstat/procps | raw text output (Linux only; missing tools only produce a warning) |
| `collectors.json` | harness | exact collector commands, PIDs at start, exit codes |
| `server.log` | gunicorn | only with `--start-server --restart-per-rep` |
| `meta.json` | harness | host + VM facts (CPU model, nproc, RAM, swap, kernel, distro, virtualization, packages, app `pip freeze`), git commit, workers, server env, locust env, scenario YAML, all timestamps, status |

Counters in `monitor.csv` (`*_delta`) are differences from the previous row. Divide by `dt_s` to get rates. `proc_cpu_pct` is summed over all processes in the tree and can exceed 100 (100 = one core).

## Network: NAT vs host-only

The roteiro uses NAT with port forwarding (`127.0.0.1:800x` on the host). VirtualBox's NAT engine relays every
packet through a user-space process on the host, which costs host CPU and can cap throughput at high demand
(bolsa_ficticia with 100-200 users sends many small requests). If the throughput curve goes flat while the VM's CPU
and disk are not busy, suspect NAT. Add a host-only adapter (`./scripts/provision_vbox.sh <vm> 2 2048 --hostonly vboxnet0`)
and use `--host http://VM_IP:<port> --agent http://VM_IP:9100`. Whatever you choose, use the same network for every
label and state it in the report.

## Analysis

After all labels of an app are collected (on the host):

```bash
loadtest/.venv/bin/pip install -r loadtest/requirements-analysis.txt
loadtest/.venv/bin/python loadtest/analyze.py --app cofre_digital
```

Read the charts in `results/_analysis/<app>/`:
- **Degradation**: throughput stops growing with users (saturation) while p95/p99 latency rises steeply. Where this knee
  happens is the capacity of that provisioning.
- **Provisioning effect**: compare where the knee happens for each label. cofre_digital should move it with vCPUs (if
  `WORKERS` = vCPUs), galeria_filtros with RAM (watch swap and memory available), bolsa_ficticia mostly with the disk
  (iowait, writes/s), not with vCPUs.
- `reps.csv` flags repetitions where workers were replaced (`proc_distinct_pids` > `proc_n_max`, e.g. OOM kill);
  the script also prints a NOTE for them.

