# Cofre Digital (CPU-bound) — porta 8001

FastAPI app whose heavy endpoints are **pure-Python, CPU- and GIL-bound** by design
(no numpy, no C-level sieves), so CPU usage scales with Gunicorn worker *processes*
and saturates per vCPU — the behaviour the provisioning experiments measure.

## Endpoints
| Método | Rota | Parâmetros | Trabalho |
|---|---|---|---|
| GET | `/primos/{n}` | `n` ∈ [2, 5 000 000] | Gera todos os primos ≤ n por divisão por tentativa (pelos primos já encontrados). Retorna `quantidade`, `maior_primo`, `chave` (SHA-256 dos 8 maiores primos), `elapsed_ms`. |
| POST | `/assinar` | JSON `{"texto": str (≤10 000), "rounds": int ∈ [1, 5 000 000]}` | `h1 = SHA256(utf8(texto))`, `hk+1 = SHA256(hk)` sobre o digest bruto de 32 bytes; retorna `assinatura` (hex de h_rounds), `rounds`, `elapsed_ms`. |
| GET | `/cifrar-matriz/{tamanho}` | `tamanho` ∈ [2, 400]; query `seed` (int ≥ 0, opcional — aleatório se omitido e devolvido na resposta), `mensagem` (padrão `"cofre digital"`) | A = matriz-chave aleatória (`random.Random(seed).getrandbits(8)` por elemento, linha a linha); B = bytes UTF-8 da mensagem repetidos ciclicamente; C = A×B em laço triplo Python puro. Retorna `checksum` (soma de C), `traco`, `amostra_hex`, `seed`, `elapsed_ms`. |
| GET | `/health` | — | `{"status":"ok","pid":...,"app":"cofre_digital"}` |

Out-of-range/invalid input → **422**. Every response has an `X-Process-Time` header
(seconds, measured by a pure-ASGI middleware).

Typical cost (Apple M-series, Python 3.14; expect a slower VM):
`/primos/1000000` ≈ 0.3 s, `/assinar` 500k rounds ≈ 0.1 s, `/cifrar-matriz/200` ≈ 0.18 s, `/cifrar-matriz/400` ≈ 1.4 s.

### Why plain `def` handlers
FastAPI runs `def` endpoints in its threadpool, keeping the event loop free to accept
connections and answer `/health`. Since the work holds the GIL, threads in one worker
do not add parallelism — one worker ≈ at most one core; parallelism comes from
`WORKERS`. An `async def` handler would block the event loop for the full computation.
numpy was avoided because it releases the GIL and uses multithreaded BLAS, which would
blur the CPU profile.

## Estrutura (padrão Service–Controller, ver CONTRACT.md)
```
app/
  main.py                  # cria o FastAPI, middleware, include_router — expõe `app` (app.main:app)
  config.py                # Settings: nome, limites de validação, mensagem padrão
  exceptions.py            # ParametroInvalidoError (domínio) → 422 nos controllers
  middleware.py            # ProcessTimeMiddleware (ASGI puro, header X-Process-Time)
  dependencies.py          # providers `Depends` (get_primos_service, ...)
  controllers/             # HTTP apenas: validação (Path/Query/Body), mapeamento de erros
    health_controller.py   #   GET /health
    primos_controller.py   #   GET /primos/{n}
    assinatura_controller.py # POST /assinar
    matriz_controller.py   #   GET /cifrar-matriz/{tamanho}
  services/                # lógica de negócio, Python puro, sem imports de FastAPI
    primos_service.py      #   gerar_primos, PrimosService
    assinatura_service.py  #   sha256_iterado, AssinaturaService
    matriz_service.py      #   gerar_matrizes, multiplicar, MatrizService
  schemas/                 # modelos Pydantic (AssinarRequest e respostas)
```
Os limites são validados na borda HTTP pelo FastAPI/Pydantic (422 com o corpo padrão do
FastAPI); os services revalidam e lançam `ParametroInvalidoError` quando chamados
diretamente, que os controllers traduzem em 422. Sem repositories (app sem estado).

## Running
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # + requirements-dev.txt for tests
WORKERS=2 ./run.sh                        # gunicorn -c gunicorn.conf.py app.main:app
```
| env | default | |
|---|---|---|
| `HOST` | `0.0.0.0` | bind host |
| `PORT` | `8001` | bind port |
| `WORKERS` | `1` | Gunicorn (UvicornWorker) processes — use ≈ nº de vCPUs |
| `TIMEOUT` | `120` | worker timeout (s) |
| `LOG_LEVEL` | `warning` | access log only at `info`/`debug` |

## Tests (real server, no mocks)
```bash
pip install -r requirements-dev.txt
python -m pytest -q tests                              # spawns gunicorn (2 workers) on a free port
COFRE_TEST_URL=http://VM_IP:8001 python -m pytest -q tests   # against a running server
```
Checks known values (π(10⁴)=1229/9973, π(10⁶)=78498/999983, …), SHA-256 FIPS vector and
iterated hashes recomputed with `hashlib`, matrix results recomputed independently, and 422s.
`tests/test_services.py` unit-tests the service layer directly (real functions, known values).

## Load test
k6 script: `loadtest/k6/cofre_digital.js`. The knobs are documented at the top of the file: `COFRE_N_MIN/MAX`,
`COFRE_ROUNDS_MIN/MAX`, `COFRE_MATRIZ_MIN/MAX`. In the experiment, `loadtest/run_scenario.sh` starts
the app with `WORKERS=auto` (one worker per vCPU) and runs 1, 4, 8, 16 and 32 users.
