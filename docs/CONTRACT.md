# Convenções do projeto

Regras compartilhadas pelas apps, pelos scripts de carga e pelos resultados. Antes de mudar uma app, um script k6 ou o formato dos resultados, confira aqui.

Python >= 3.10 (a VM é Ubuntu 22.04, Python 3.10). O código também precisa rodar no Python mais novo do notebook.

## Stack

- **Apps:** FastAPI + workers Uvicorn sob Gunicorn (`gunicorn -k uvicorn.workers.UvicornWorker`).
- **Carga:** k6, rodando no notebook (nunca dentro da VM).
- **Métricas da VM:** node_exporter na VM, Prometheus + Grafana no notebook (`monitoring/`).
- **Testes funcionais:** pytest + httpx contra um servidor **real** (subprocesso). Sem mocks, sem fakes, sem monkeypatch de resultados.
- **Análise:** `loadtest/analyze.py` (pandas + matplotlib), só no notebook. A coleta grava dados brutos; agregação e gráficos ficam na análise.

## Estrutura

```
apps/
  cofre_digital/     # CPU-bound    — porta 8001
  galeria_filtros/   # memory-bound — porta 8002
  bolsa_ficticia/    # IO-bound     — porta 8003
    app/main.py            # objeto `app` (import app.main:app, rodar de dentro da pasta da app)
    gunicorn.conf.py       # lê as variáveis abaixo
    run.sh                 # sobe o gunicorn com gunicorn.conf.py
    requirements.txt       # (+ requirements-dev.txt com pytest/httpx)
    tests/                 # testes contra servidor real (o conftest sobe o servidor)
    README.md              # endpoints e variáveis
loadtest/
  k6/<app>.js + comum.js   # carga de cada app; comum.js = rampa/patamar + resumo JSON
  run_scenario.sh          # roda um cenário inteiro (3 apps × 5 níveis)
  metricas_vm.py           # checa a VM e exporta as métricas do Prometheus para CSV
  analyze.py               # tabela e gráficos
monitoring/                # docker compose: Prometheus + Grafana (dashboard provisionado)
results/                   # versionado: dados brutos + results/_analysis/
```

## Estrutura interna das apps (Service–Controller)

```
app/
  main.py          # só cria o FastAPI, middlewares, routers e startup
  config.py        # único lugar que lê variáveis de ambiente
  controllers/     # um APIRouter por recurso: só HTTP (valida entrada, chama service, traduz exceção → status)
  services/        # regras de negócio; nunca importam fastapi/starlette; levantam exceções de domínio
  repositories/    # acesso a dados quando há estado (SQLite, livro-caixa, cache LRU)
  schemas/         # modelos Pydantic
  exceptions.py    # exceções de domínio
```

- Controllers não têm algoritmo nem SQL.
- Dependências são ligadas via `Depends` (ou um `dependencies.py` pequeno).
- Handlers pesados de CPU/memória/IO são `def` (rodam no threadpool), para o event loop não virar gargalo artificial.
- **Cada app tem um gargalo pretendido. Não o remova:** o cofre usa Python puro preso ao GIL, a galeria usa o cache em RAM e a bolsa usa fsync do SQLite e do livro-caixa.

## Variáveis que todo `gunicorn.conf.py` respeita

| variável | padrão | significado |
|---|---|---|
| `HOST` | `0.0.0.0` | endereço |
| `PORT` | porta da app | porta |
| `WORKERS` | `1` | processos gunicorn; `auto` = um por vCPU |
| `TIMEOUT` | `120` | timeout do worker (s) |
| `LOG_LEVEL` | `warning` | access log desligado durante os testes |

## Scripts k6

- Importam `opcoes()` e `resumo()` de `comum.js`, que cuidam da rampa (`RAMPA`), do patamar (`DURACAO`), dos `VUS` e do JSON de saída (`SAIDA`).
- Os parâmetros da carga vêm de variáveis com o prefixo da app (`COFRE_*`, `GALERIA_*`, `BOLSA_*`) e `LT_WAIT_MIN/MAX`. Os defaults do script são os valores usados no experimento.
- Toda resposta é validada com `check`: corpo errado conta como falha. Status esperados que não são erro (404 de miss, 409 de saldo) são declarados com `responseCallback`.
- Sem `thresholds`: falhas em demanda alta são resultado, não motivo para abortar.

## Formato dos resultados

`results/<app>/<cenario>/`, com cenário no padrão `<N>cpu-<M>gb`:

| arquivo | origem | conteúdo |
|---|---|---|
| `<nivel>.k6.json` | k6 (`comum.js`) | `app`, `cenario`, `nivel`, `vus`, `rampa_s`, `duracao_s`, `inicio_epoch`, `fim_epoch` e `metricas` (resumo completo do k6) |
| `<nivel>.app.json` | `GET /cache/stats` (galeria) ou `GET /livro-caixa/stats` (bolsa) | estatísticas da app depois do nível |
| `metricas_vm.csv` | Prometheus (`metricas_vm.py exportar`) | 1 linha/s: CPU (total, user, system, iowait, steal), load, RAM, swap, disco, rede |

`results/_analysis/` é gerado pelo `analyze.py` e pode ser apagado e refeito a qualquer momento.
