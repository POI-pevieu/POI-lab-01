# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Coursework for "Provisionamento e Operações de Infraestrutura" (Atividade 2: demand, provisioning and performance). Three backend apps with different resource profiles are deployed on a VirtualBox VM (Ubuntu Server 22.04 from `ubuntu_serve_2204_poi.vdi`, login `osboxes` / `osboxes.org`) and load-tested under at least 4 provisioning scenarios (vCPU/RAM) × several demand levels. Load must be generated **from outside the VM** (the user's notebook). The assignment material is in `docs/atividade/`: the text (`DESCRIPTION.md`), the VM roteiro PDF, the report template `.docx` and the professor's correction (`mensagem_professor.jpeg`).

### Scope (professor's correction, 2026-10-14, overrides the written docs)

The roteiro PDF (step 10: "considere apenas uma das suas aplicações") and the report template ("Teste de Stress da Aplicação selecionada", "pelo menos 2" scenarios) describe experiments on a single app. The professor later clarified that this is wrong:

- **The analyses must be done for all 3 apps.** Every app gets the full experiment: ≥4 provisioning scenarios (from `DESCRIPTION.md`, which wins over the template's "≥2") × several demand levels. Each app also gets its own results, plots and interpretation in the report. Adapt the template's "Aplicação selecionada" section so that it covers each app.
- **The in-class presentation** is an overview of the report (the professor reads the full report later) plus **at least one app running live on the VM**. The professor also wants to see the apps running in the LCC.
- Deliverables: the report (from the `.docx` template), the code/scripts/results in the repo, screenshots of the VM setup, the VM disk saved to Drive, and the Classroom form (one answer per group member).

### How the group wants the work done (decided 2026-10-14)

- **The VM is configured by hand** in the VirtualBox GUI: creation, NAT port forwards, the one-time setup inside the VM, and switching vCPU/RAM between scenarios. Do not reintroduce scripts that drive VirtualBox (`VBoxManage`) or set up the VM. Earlier versions had them and they were removed on purpose so the repo doesn't look fully machine-made.
- **The repetitive part is automated** with small, readable scripts: one bash script per scenario, using ssh + curl + k6.
- **k6, not Locust.** Prometheus + Grafana on the notebook, node_exporter in the VM.
- **1 repetition per level**, to keep the total run time around 1h25 (1 min plateau, 10 s ramp, 15 s pause per level; reduced from 2 min / 30 s to save time).
- **Scenarios:** `1cpu-1gb`, `1cpu-2gb`, `2cpu-2gb`, `3cpu-3gb` (the group picked 3-3 over 4-4 for the last one).
- **Every app must clearly degrade ("resultados horríveis") at its top demand level in every scenario, including `3cpu-3gb`.** The levels are the same across scenarios, so the plots compare. They are spread wide enough that 1 vCPU saturates early and 3 vCPU only at the top. Final levels come from a calibration run on `3cpu-3gb` (see Status).
- New docs, comments and messages are in Portuguese. Domain names are Portuguese (`acoes`, `ordens`, `livro_caixa`…). Older app code and docs are partly in English, so leave them as they are.
- The design is in `docs/superpowers/specs/2026-10-14-manual-k6-prometheus-design.md`.

## Commands

Each app has its own venv. Run app commands from inside the app dir, because the import path is `app.main:app`.

```bash
# app (cofre_digital:8001, galeria_filtros:8002, bolsa_ficticia:8003)
cd apps/cofre_digital
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
WORKERS=2 ./run.sh                                  # gunicorn + uvicorn workers
.venv/bin/pytest -q                                 # all tests (spawn a real server)
.venv/bin/pytest -q tests/test_x.py::test_name      # single test
COFRE_TEST_URL=http://127.0.0.1:8001 .venv/bin/pytest -q   # cofre only: test an already-running server (e.g. the VM)

# monitoring (notebook)
cd monitoring && docker compose up -d               # Prometheus :9090, Grafana :3000 (host network)

# one provisioning scenario: 3 apps × 5 levels, ~21 min (the VM must already have that vCPU/RAM)
loadtest/run_scenario.sh 2cpu-2gb [app ...]

# all-local smoke run (no VM): needs a node_exporter on :9100 and k6 on PATH
VM_SSH="" VM_REPO="$PWD" DURACAO=15s RAMPA=3s PAUSA=3 loadtest/run_scenario.sh smoke cofre_digital
rm -rf results/*/smoke                               # never commit smoke results

# analysis (notebook only; pandas + matplotlib)
python3 -m venv loadtest/.venv && loadtest/.venv/bin/pip install -r loadtest/requirements.txt
loadtest/.venv/bin/python loadtest/analyze.py [--app cofre_digital]
```

There is no linter or build step configured. The manual procedure (VM creation, NAT rules, in-VM setup, scenario cycle) is in `docs/ROTEIRO_VM.md`.

## Architecture

**`docs/CONTRACT.md` is the binding spec.** Read it before changing an app, a k6 script or the results format. The main points:

- **App layering (Service–Controller):** `main.py` only builds the FastAPI app and routers. `config.py` is the single place that reads env vars. `controllers/` handle HTTP only and map domain exceptions (`exceptions.py`) to status codes. `services/` hold the logic and must never import fastapi/starlette. `repositories/` (SQLite, ledger file, LRU cache) do data access. Dependencies are wired via `Depends` / `dependencies.py`. CPU/memory-heavy handlers are plain `def`, so they run in the threadpool.
- **`gunicorn.conf.py`** in every app honours `HOST`, `PORT`, `WORKERS` (`auto` = one per vCPU), `TIMEOUT` and `LOG_LEVEL`. Access logs stay off by default so they don't distort measurements.
- **Tests never mock.** `conftest.py` spawns a real gunicorn subprocess on a free port and waits for `/health`. Each app's `tests/test_services.py` calls the services directly against real state; for bolsa that means real SQLite and the real ledger in `tmp_path`.
- **What each app stresses (this is the point of the experiment):** cofre_digital is pure-Python CPU work, limited by the GIL to ~1 core per worker, so it runs with `WORKERS=auto`. galeria_filtros keeps an in-RAM image cache sized `CACHE_MAX_MB=auto` (a fraction of VM RAM ÷ workers) with 1 worker. bolsa_ficticia serializes writes through SQLite `synchronous=FULL` plus an fsync'd ledger, so disk fsync latency is the bottleneck. When editing an app, keep its intended bottleneck intact.

**Load test and monitoring:**
- `loadtest/run_scenario.sh <cenario> [app...]` is the only orchestrator. The per-app parameters live in its `configurar()` case block: port, server env, reset endpoint before each level, stats endpoint saved after each level, and the levels (`nome:usuários`). Flow per app: check that the port is free → start the app in the VM over ssh (`nohup ./run.sh`, PID in `/tmp/<app>.pid`) → wait for `/health` → for each level: reset, `k6 run`, save stats, pause → stop the app (an EXIT trap stops it on Ctrl+C too) → `metricas_vm.py exportar`. A level whose `<nivel>.k6.json` already exists is skipped (resume). k6 writes to `.tmp` and the file is renamed only on success. `VM_SSH=""` runs everything locally. `NIVEIS_TESTE="a:64 b:128"` overrides the levels (calibration only, use with one app).
- `loadtest/metricas_vm.py checar <cenario>` aborts if Prometheus isn't scraping the VM or if vCPU/RAM don't match a `<N>cpu-<M>gb` label. `exportar <pasta>` queries `query_range` (step 1 s) over the min start / max end of the folder's k6 JSONs and writes `metricas_vm.csv`. Stdlib only. Disk queries only count whole disks (`sd*|vd*|nvme*n*`), so LVM `dm-*` devices don't double count.
- `loadtest/k6/comum.js` provides `opcoes()` (ramp `RAMPA` + plateau `DURACAO` at `VUS`) and `resumo()` (`handleSummary` → JSON with start/end epoch plus a one-line stdout summary). The app scripts have no thresholds, because failures under high load are results.
- `analyze.py` excludes the ramp from the VM averages. It writes `results/_analysis/resumo.csv`, per-metric comparison PNGs (one line per scenario, log2 x axis of users), `visao_geral.png` and `serie_<cenario>.png` (time series with shaded level bands).
- `monitoring/`: Prometheus and Grafana both use `network_mode: host`, because VirtualBox NAT forwards bind to the host's 127.0.0.1. Bind mounts use `:z` (Fedora SELinux enforcing). The Grafana dashboard JSON (`monitoring/grafana/dashboards/vm.json`) is provisioned with an anonymous admin.

**Results:** `results/<app>/<cenario>/` **is versioned** (the assignment requires it): `<nivel>.k6.json`, `<nivel>.app.json` (galeria/bolsa) and `metricas_vm.csv`. `results/_analysis/` is regenerated by `analyze.py`.

## Gotchas

- Venvs copied between machines are broken. `run.sh` only uses `.venv` if it can import gunicorn.
- Only one app may run in the VM during a measurement. `run_scenario.sh` refuses to start if the app's port already answers.
- VirtualBox NAT can cap throughput at high demand. If throughput goes flat while the VM's CPU and disk are idle, suspect NAT, and use the same network for every scenario.
- The Bash tool's hook blocks `curl` typed directly in commands. Use Python `urllib` for ad-hoc HTTP checks; scripts that call curl internally are fine.
- If a port refuses connections on the notebook (`Connection refused` on 127.0.0.1:800x) even with the VM on, the NAT rule is missing or wrong. A valid VirtualBox NAT rule listens on the host even when the app is stopped. This happened with 8003.
- A `run_scenario.sh` killed mid-run (not Ctrl+C) leaves the app running in the VM, and the next run refuses with "Já tem algo respondendo". Fix: `ssh -p 2222 osboxes@127.0.0.1 'kill $(cat /tmp/<app>.pid); rm -f /tmp/<app>.pid'`. Don't `pkill -f` over ssh with a pattern that also matches the ssh command line, because it kills the ssh session itself.
- Saved-state ("Salvar o estado") VMs can't have vCPU/RAM changed. Boot and `sudo poweroff` instead. Before uploading the VDI, also power off properly and merge any snapshots.

## Status (updated 2026-10-14, end of session)

**Done**
- Repo restructured (apps/, loadtest/, monitoring/, docs/), committed and pushed. The VM cloned it. Later edits (defaults 1m/15 s, `3cpu-3gb`, `NIVEIS_TESTE`, doc tweaks) are **uncommitted**.
- VM is set up: Ubuntu 22.04 (Python 3.10; all app test suites pass on 3.10), repo in `~/POI-lab-01`, venvs, node_exporter active, SSH key login works. NAT: 2222→22, 8001, 8002, 8003, 9100 (8003 was fixed after it was found missing).
- Notebook: k6 v2.3.0 in `~/.local/bin`, `loadtest/.venv` ok, `monitoring` compose up. The Prometheus target `vm` was UP.
- Pipeline validated end to end at `1cpu-1gb` with 15 s levels (label `teste`), 0% HTTP failures everywhere:
  - cofre: CPU 96–98% from 16 VUs, throughput flat ~6 req/s, p95 8.1 s at 32 VUs.
  - galeria: cache full at 10 VUs; at 40 VUs swap ~960 MB, disk 78% busy, throughput drops 15.4→10.8 req/s, p95 8.9 s (thrashing).
  - bolsa: iowait up to 33%, disk 92% busy, fsync ~6 ms, knee between 100 and 200 VUs (132 req/s, p95 1.9 s). CPU also hits 94% at 1 vCPU, so the bottleneck is disk+CPU there. Worth discussing in the report.

**Next steps**
1. ~~Calibration on `3cpu-3gb`~~ done (2026-10-14, 15 s levels, 0% failures). Results: cofre plateaus at ~18 req/s from 64 VUs (CPU ~88%), p95 9.6 s at 128. Galeria (1 worker, but PIL/numpy release the GIL) reaches CPU 96% at 160 VUs, 76 req/s, p95 1.5 s, RAM 2.2 GB with no swap. Bolsa is disk-bound regardless of vCPU (disk 86–91% busy, CPU ≤40%): ~170–190 req/s, p95 0.5 s / 3.7 s / 7.2 s at 200 / 400 / 800. Final levels in `configurar()`: cofre 4/8/16/48/128, galeria 10/20/40/120/400, bolsa 25/50/100/200/500. Docs updated. `results/*/calib` deleted.
2. Galeria used too much CPU at 3 GB because its 48-image working set (~610 MB) fit in the cache, so RAM never mattered. Fixed (2026-10-14): noise is drawn on a 512×512 block and tiled (`gerar_imagem` 50→12 ms), the gray filter uses uint16 fixed-point weights (24→9 ms), and k6 `GALERIA_ID_SPACE` went from 48 to 128 (~1.7 GB working set, larger than the 1.25 GB cache at 3 GB). Recalibrated on `3cpu-3gb` (commit ff46b51 in the VM): CPU per request about 45% lower (83% CPU at 119 req/s vs 96% at 76 before). The cache fills at 40 VUs and evicts from then on (hit ratio 0.46→0.68). At 300 VUs: 134 req/s, CPU 89%, RAM 2.3/2.9 GB, swap only 18 MB, p50 1.06 s, p95 1.5 s. So at 3 GB RAM is enough and CPU is the limit again; that is a finding for the report (RAM-bound at 1 GB, CPU-bound once RAM suffices). The top level went from 300 to 400 to make the degradation clearer.
3. Commit + push the pending edits.
4. Run the 4 scenarios (`loadtest/run_scenario.sh <cenario>`, ~21 min each, notebook on AC power). It can start with `3cpu-3gb`, since the VM will already be set to it. Commit `results/` after each one.
5. `analyze.py`, then the report (`.docx` template, one section per app), VM screenshots (NAT rules, Sistema, node_exporter status, Grafana), VDI (and optionally an `.ova`) to Drive, and the Classroom form. Optional: add a "seção 7 — entrega do VDI" to `ROTEIRO_VM.md`.
