# Project contract (shared conventions for all apps and load tests)

Python >= 3.10 (target VM: Ubuntu Server, Python 3.10/3.12). Code must also run on Python 3.14 locally.

## Stack
- Web: FastAPI + Uvicorn workers under Gunicorn (`gunicorn -k uvicorn.workers.UvicornWorker`).
- Load generation: **Locust** (headless, `--csv`, `--csv-full-history`) — the primary tool.
  Optional secondary: `wrk`/`k6` scripts are welcome but not required.
- Resource metrics: **psutil** sampler (system + per-process tree) **and** sysstat tools
  (`vmstat`, `pidstat`, `iostat`, `mpstat`) when installed. The collection harness writes raw data only;
  aggregation and charts are a separate offline step (`loadtest/analyze.py`, pandas + matplotlib, host only).
- Functional tests: **pytest + httpx** against a REAL running server (spawned subprocess). No mocks, no fakes, no monkeypatching of results.

## Layout
```
apps/
  cofre_digital/     # CPU-bound   — port 8001
  galeria_filtros/   # Memory-bound — port 8002
  bolsa_ficticia/    # IO-bound    — port 8003
    app/main.py            # FastAPI `app` object (import path: app.main:app, run from the app dir)
    gunicorn.conf.py       # reads env vars below
    requirements.txt
    run.sh                 # starts gunicorn using gunicorn.conf.py
    tests/                 # pytest functional tests vs live server (conftest spawns server)
    README.md              # endpoints + env vars
loadtest/
  locustfiles/cofre_digital.py
  locustfiles/galeria_filtros.py
  locustfiles/bolsa_ficticia.py
  scenarios/*.yaml          # demand levels per app
  run_experiment.py         # orchestrator (see below)
  monitor.py                # psutil sampler
  requirements.txt
scripts/                    # VM setup, systemd units, provisioning helpers
results/                    # versioned raw outputs (except locust.html); results/_analysis/ = tables + charts
```

## Internal app structure (Service–Controller pattern)
```
app/
  main.py            # only: create FastAPI app, middleware, include routers, startup. Exposes `app`.
  config.py          # Settings read from env vars (single place)
  controllers/       # FastAPI APIRouter per resource: HTTP only (parse/validate input, map exceptions → HTTP status, call service)
    __init__.py
    <recurso>_controller.py
  services/          # business logic, no FastAPI/HTTP imports; raise domain exceptions
    __init__.py
    <recurso>_service.py
  repositories/      # (when there is persistence/state: SQLite, ledger file, LRU cache) data access only
  schemas/           # Pydantic request/response models
  exceptions.py      # domain exceptions raised by services, translated by controllers
```
Rules: controllers never contain algorithms or SQL; services never import fastapi/starlette; dependencies wired via `Depends` providers in controllers (or a small `dependencies.py`).

## Env vars every app's gunicorn.conf.py must honour
| var | default | meaning |
|---|---|---|
| `HOST` | `0.0.0.0` | bind host |
| `PORT` | app's port | bind port |
| `WORKERS` | `1` | gunicorn worker processes; `auto` = one per vCPU (`os.cpu_count()`) |
| `TIMEOUT` | `120` | gunicorn worker timeout (s) |
| `LOG_LEVEL` | `warning` | keep access logs off during load tests |

CPU/memory heavy handlers must be plain `def` (threadpool) or run work so the event loop isn't the bottleneck artificially; document the choice.

## Locustfile contract
- Each locustfile defines `HttpUser` subclasses with realistic task weights and `wait_time` configurable by env
  `LT_WAIT_MIN`/`LT_WAIT_MAX` (seconds, default 0.5/1.5).
- Workload intensity knobs come from env vars prefixed with the app (e.g. `COFRE_N_MAX`, `GALERIA_RESOLUCAO`, `BOLSA_...`) — documented at top of file.
- Use `name=` grouping so per-endpoint stats aggregate (e.g. `/primos/[n]`).
- Responses must be validated (`catch_response=True`) — wrong body => failure. Never fabricate results.

## Scenario YAML contract (loadtest/scenarios/<app>.yaml)
```yaml
app: cofre_digital
port: 8001
locustfile: loadtest/locustfiles/cofre_digital.py
warmup_s: 15
duration_s: 120
cooldown_s: 15
repetitions: 3
spawn_rate: 5
env: {COFRE_N_MAX: "200000"}          # passed to locust
levels:                               # demand levels (concurrent users)
  - {name: baixa,   users: 5}
  - {name: media,   users: 20}
  - {name: alta,    users: 50}
  - {name: extrema, users: 100}
```

## run_experiment.py contract
`python loadtest/run_experiment.py --scenario loadtest/scenarios/cofre_digital.yaml --label 1cpu-1gb [--host http://127.0.0.1:8001] [--workers N] [--start-server]`
- `--label` = provisioning scenario name (VM vCPU/RAM config) chosen by the operator.
- Writes to `results/<app>/<label>/<level>/rep<k>/`: locust CSVs, monitor CSV (psutil), sysstat raw logs, `meta.json`
  (hostname, nproc, total RAM, kernel, python, git commit, workers, timestamps, scenario).
- No aggregation/analysis/plots here; `loadtest/analyze.py` does that afterwards.
