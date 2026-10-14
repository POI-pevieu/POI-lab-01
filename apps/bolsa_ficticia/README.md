# Bolsa Fictícia (IO-bound, porta 8003)

A simulated brokerage. Each investor starts with a fictitious balance (R$ 10.000 by default) and trades shares of 4 fictitious companies
(`CAFE3`, `MINA3`, `TECN3`, `ENER3`). The automatic strategy **buys when the price is rising** and **sells when it is
falling**. Every executed order is written to **SQLite** and also appended as one JSON line to the **livro-caixa**
(`livro_caixa.ledger`), with `fsync` on every line, so no transaction is lost.

## Why it is IO-bound

Each order runs as one `BEGIN IMMEDIATE` transaction: it reads the balance and position, updates them, inserts the
order row, appends the line to the livro-caixa (`flock` + `write` + `fsync`) and then commits. With
`synchronous=FULL`, the `COMMIT` does another `fsync`. Writes are serialized across all workers by SQLite's write
lock, so throughput is limited by the `fsync` latency of the VM's virtual disk, not by the CPU.

The livro-caixa line is written **before** the `COMMIT`, while the write lock is still held:

- If the `fsync` fails, the transaction is rolled back.
- The ledger lines come out in the same order as the order ids.
- Order ids and ledger lines match one to one, which the tests check.

Known limitation: the ledger and SQLite are two files, not one atomic commit. If the `COMMIT` itself fails after the
ledger `fsync` (disk full, I/O error) or the VM loses power exactly between the two, the ledger keeps a line for an
order that was rolled back. The ledger can be ahead of the database, never behind. Because the rolled-back id is reused
by the next order, reconcile by keeping, for each `ordem_id`, only the line whose fields match the row in `ordens`.

## Code structure (Service–Controller)

```
app/
  main.py              # creates the FastAPI app, registers error handlers, includes routers; lifespan builds services + init
  config.py            # Settings.from_env(): the only place env vars are read (except gunicorn.conf.py)
  dependencies.py      # Depends providers: return the per-worker services stored in app.state
  exceptions.py        # domain errors: InvestidorNaoEncontrado, AcaoNaoEncontrada, SaldoInsuficiente,
                       #   PosicaoInsuficiente, OrdemInvalida (base ErroDominio)
  controllers/         # HTTP only: one APIRouter per resource
    health_controller.py, acoes_controller.py, mercado_controller.py, investidores_controller.py,
    ordens_controller.py, livro_caixa_controller.py, admin_controller.py
    erros.py           # domain error → status (404/409/400); sqlite "locked"/"busy" → 503, other sqlite errors → 500
  services/            # business rules, no FastAPI imports; each write use case owns ONE BEGIN IMMEDIATE transaction
    __init__.py        # Servicos container + construir_servicos(settings)
    mercado_service.py, investidor_service.py, ordem_service.py, estrategia_service.py,
    livro_caixa_service.py, admin_service.py, health_service.py
    formatacao.py      # centavos → reais, trend, response dicts
  repositories/        # data access only
    database.py        # Database: connection per request, PRAGMAs, schema/seed, transacao(), reset, sizes
    ledger_repository.py  # LedgerRepository: append + flock + fsync, truncate, per-process stats
    acao_repository.py, investidor_repository.py, ordem_repository.py   # SQL only, receive the connection
  schemas/             # Pydantic request models
```

A request flows as controller → service → repositories. For an order, `OrdemService.executar` opens the connection and
`BEGIN IMMEDIATE`, then calls `executar_em(conn, ...)`. That method reads and updates the rows through the repositories,
appends and fsyncs the ledger line, and only then does the context manager `COMMIT`. Any exception, including a failed
`fsync`, causes a `ROLLBACK`. `EstrategiaService.operar` calls the same `executar_em` inside its own single transaction.
The `*_em(conn, ...)` methods are the composable versions that run inside a transaction that is already open.

Tests: `tests/test_bolsa.py` covers HTTP against a live gunicorn server. `tests/test_services.py` calls the real services
against a real SQLite file and ledger in `tmp_path`, with no mocks. It includes ledger-failure rollback and concurrent
threads.

## Design choices

- **Handlers are plain `def`.** `sqlite3` and `fsync` block, so FastAPI runs the handlers in its threadpool (AnyIO,
  40 threads per worker) and the event loop does not become an artificial bottleneck.
- **One SQLite connection per request.**
- **Prices are shared by all workers.** They live in the `acoes` table, and every move is recorded in
  `historico_precos`.
- **Prices change in two ways.** `POST /mercado/tick` applies a geometric random walk. `POST /admin/precos` sets a price
  directly.
- **Trend** compares the current price with the previous one: `alta` (rising), `baixa` (falling) or `estavel` (flat).
- **Money is stored as integer centavos.** Responses also show the amount in reais.

## Endpoints

| method | route | description |
|---|---|---|
| GET | `/health` | status, pid, journal_mode, synchronous, ledger_fsync |
| GET | `/acoes` | current prices, previous prices, change and trend, plus the current `tick` |
| POST | `/mercado/tick` | body `{"passos": 1}` (optional, 1..100). Moves all prices one random-walk step per `passos` |
| POST | `/investidores` | `{"nome": "Ana", "saldo_inicial": 10000}` → 201 |
| GET | `/investidores/{id}` | investor details |
| GET | `/investidores/{id}/carteira` | cash, positions at market price, total value (`patrimonio`), profit/loss (`resultado`), number of orders |
| POST | `/investidores/{id}/operar?ticker=X` | runs the strategy (`ticker` optional; random if omitted). Rising → buys `ESTRATEGIA_FRACAO_COMPRA` of the cash. Falling → sells `ESTRATEGIA_FRACAO_VENDA` of the position. Flat, no cash or no shares → `manter` (hold, nothing is written) |
| POST | `/ordens` | `{"investidor_id", "ticker", "tipo": "compra"\|"venda", "quantidade"}` → 201. 409 = not enough cash or shares. 404 = unknown investor or ticker. 422 = invalid input |
| GET | `/ordens?investidor_id=&limit=50` | latest orders (`limit` ≤ 500) |
| GET | `/livro-caixa/stats` | ledger size and line count; this **process's** fsync stats (count, average, max, total); db, wal and shm sizes; order count |
| POST | `/admin/precos` | `{"ticker": "CAFE3", "preco": 40.0}` sets a price (the current price becomes the previous one). Used for tests and deterministic experiments |
| POST | `/admin/reset` | wipes investors, orders and history, truncates the livro-caixa, resets prices to their initial values, then runs `wal_checkpoint` and `VACUUM`. **Call before each repetition** |

`/livro-caixa/stats` → `processo` covers only the worker that answered. The line count and file sizes are global.

## Environment variables

| var | default | meaning |
|---|---|---|
| `HOST` / `PORT` | `0.0.0.0` / `8003` | bind address |
| `WORKERS` | `1` | gunicorn workers (all share the same db and ledger) |
| `TIMEOUT` | `120` | gunicorn worker timeout |
| `LOG_LEVEL` | `warning` | gunicorn log level |
| `ACCESS_LOG` | `0` | `1` turns on the access log (leave it off during load tests) |
| `DATA_DIR` | `./data` | directory for `bolsa.db` and `livro_caixa.ledger`. Point it at the virtual disk you want to measure |
| `SQLITE_JOURNAL_MODE` | `WAL` | `DELETE` (or `TRUNCATE`/`PERSIST`) for comparison. Switch modes only with no server running |
| `SQLITE_SYNCHRONOUS` | `FULL` | `NORMAL`/`OFF`/`EXTRA` for comparison |
| `SQLITE_BUSY_TIMEOUT_MS` | `30000` | how long to wait for the write lock; when it runs out the response is 503 |
| `LEDGER_FSYNC` | `1` | `0` turns off the ledger `fsync`. **Comparison experiments only**: this drops the durability guarantee |
| `MERCADO_VOLATILIDADE` | `0.02` | standard deviation of the log return per tick |
| `ESTRATEGIA_FRACAO_COMPRA` | `0.2` | fraction of cash spent on an automatic buy |
| `ESTRATEGIA_FRACAO_VENDA` | `1.0` | fraction of the position sold on an automatic sell |

On macOS, `os.fsync` does not flush the disk cache (that needs `F_FULLFSYNC`). On Linux, the VM target, `fsync` is the
real durability barrier.

## Running

```bash
cd apps/bolsa_ficticia
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
WORKERS=4 DATA_DIR=/var/lib/bolsa ./run.sh     # http://0.0.0.0:8003
.venv/bin/python -m pytest -q tests            # starts its own server (2 workers, temporary DATA_DIR)
```

## Load tests

- **k6:** `loadtest/k6/bolsa_ficticia.js`. The `BOLSA_*` knobs are documented at the top of the file.
- In the experiment, `loadtest/run_scenario.sh` starts the app with `WORKERS=4`, WAL and `synchronous=FULL`.
  It calls `POST /admin/reset` before each level and saves `/livro-caixa/stats` after it.
