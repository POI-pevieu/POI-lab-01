# Web backend applications: provisioning experiments

This is a cloud-computing assignment. Three FastAPI services each stress a different resource. They are deployed on a VirtualBox VM (Ubuntu Server) and load-tested under several **provisioning scenarios** (vCPU/RAM) and **demand levels**. The test collects response time, throughput, CPU, memory and disk IO as raw data, for offline analysis.

| app | resource | port | details |
|---|---|---|---|
| [`apps/cofre_digital`](apps/cofre_digital/README.md) | CPU (pure-Python hashing, primes, matrix) | 8001 | per-app README |
| [`apps/galeria_filtros`](apps/galeria_filtros/README.md) | memory (in-RAM image cache, filters) | 8002 | per-app README |
| [`apps/bolsa_ficticia`](apps/bolsa_ficticia/README.md) | disk IO (SQLite + fsync'd ledger) | 8003 | per-app README |

Shared conventions (env vars, layout, scenario YAML) are in [CONTRACT.md](CONTRACT.md).

## Run an app locally

```bash
cd apps/cofre_digital
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
WORKERS=2 ./run.sh                      # gunicorn + uvicorn workers on :8001
curl localhost:8001/health
```

## Functional tests

Each app has pytest tests that start a **real** server subprocess (no mocks):

```bash
cd apps/cofre_digital && .venv/bin/pytest -q
```

## Experiments (raw data collection only)

```bash
python3 -m venv loadtest/.venv && loadtest/.venv/bin/pip install -r loadtest/requirements.txt

# show the plan
loadtest/.venv/bin/python loadtest/run_experiment.py --scenario loadtest/scenarios/cofre_digital.yaml --label 1cpu-1gb --dry-run

# everything on one machine (harness starts/stops the server)
loadtest/.venv/bin/python loadtest/run_experiment.py --scenario loadtest/scenarios/cofre_digital.yaml \
    --label local-test --start-server --workers 2 --levels baixa --reps 1 --duration 20

# recommended: load from the HOST, server + collectors in the VM (vm_agent.py running there)
./scripts/run_all_scenarios.sh cofre_digital 2cpu-2gb --host http://127.0.0.1:8001 \
    --agent http://127.0.0.1:9100 --agent-token SECRET --start-server --workers 2
```

The output goes to `results/<app>/<label>/<level>/rep<k>/` (versioned, except the large `locust.html`). Each directory holds the Locust CSVs and HTML report, `monitor.csv` (psutil), the raw `vmstat`/`mpstat`/`pidstat`/`iostat` logs and `meta.json`.

## Analysis (tables and charts)

```bash
loadtest/.venv/bin/pip install -r loadtest/requirements-analysis.txt   # host only
loadtest/.venv/bin/python loadtest/analyze.py                         # all apps in results/
loadtest/.venv/bin/python loadtest/analyze.py --app cofre_digital --labels 1cpu-2gb,2cpu-2gb,4cpu-2gb
```

`results/_analysis/` gets `reps.csv` (one row per repetition), `summary.csv` (mean/std per app × label × level), `endpoints.csv` and, per app, one PNG per metric (throughput, p50/p95/p99 latency, failures, server and VM CPU, memory, plus swap for galeria, iowait/disk writes for bolsa, load/steal for cofre) with one line per provisioning label, and an `overview.png`. The window excludes warm-up and ramp-up, and VM samples are aligned to the host clock.

| component | purpose |
|---|---|
| `loadtest/run_experiment.py` | orchestrator: levels × repetitions, warm-up, measurement, cool-down, metadata |
| `loadtest/analyze.py` | offline: per-repetition metrics, mean/std tables and charts in `results/_analysis/` |
| `loadtest/monitor.py` | psutil sampler (system + gunicorn process tree), CSV flushed every row |
| `loadtest/vm_agent.py` | small HTTP agent in the VM; starts/stops collectors and server, serves the files (**lab use only**) |
| `loadtest/locustfiles/`, `loadtest/scenarios/` | workloads and demand levels per app |
| `loadtest/k6/` | optional k6 scripts |
| `scripts/setup_vm.sh` | Ubuntu VM setup: apt packages (sysstat…), one venv per app, systemd units |
| `scripts/provision_vbox.sh` | `VBoxManage modifyvm --cpus/--memory` + NAT port forwards |
| `scripts/run_all_scenarios.sh` | runs all levels for one `<app> <label>` |
| `scripts/systemd/` | unit templates (apps + vm_agent) |

Methodology, suggested provisioning scenarios and the full file list are in [PROVISIONING.md](PROVISIONING.md).

### Useful `run_experiment.py` flags

`--levels a,b` · `--reps N` · `--duration S` · `--warmup S` · `--cooldown S` · `--workers N` · `--start-server` · `--restart-per-rep` · `--agent URL --agent-token T` · `--order rep-major` · `--interval S` · `--no-sysstat` · `--resume` / `--overwrite` · `--dry-run`

Optional scenario YAML keys used by the harness, in addition to those in CONTRACT.md:

- `workers`
- `server_env` (env for the server; applied only with `--start-server`)
- `reset_endpoint` + `reset_method` (default `POST`; called before each repetition)
- `health_path` (default `/health`)
- per-level `spawn_rate` / `env`
