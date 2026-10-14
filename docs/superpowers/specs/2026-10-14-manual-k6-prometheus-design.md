# Design: k6 + Prometheus/Grafana com a VM configurada à mão

Data: 2026-10-14 · Status: aprovado e implementado (ver nota no fim)

## Objetivo

Hoje o repositório automatiza o experimento inteiro: provisionamento da VM via `VBoxManage`, agente HTTP dentro da VM, orquestrador Python de ~570 linhas e Locust. O grupo quer:

1. **A VM é configurada à mão.** O aluno cria a VM, faz o setup inicial, troca vCPU/RAM entre os cenários e liga/desliga pela interface do VirtualBox.
2. **Um script curto faz a parte repetitiva** de cada cenário: subir cada app na VM, gerar a carga, coletar as métricas e parar a app. Deve ser bash legível (ssh + curl + k6), fácil de explicar em sala.
3. **Trocar o Locust pelo k6.**
4. **Monitorar a VM com Prometheus + Grafana**, com o armazenamento fora da VM, no notebook.

Restrições que continuam valendo:
- Análise das 3 apps, cada uma com ≥4 cenários de provisionamento × vários níveis de demanda (correção do professor).
- Carga gerada fora da VM.
- Resultados versionados no repo.
- Em sala: visão geral do relatório + pelo menos uma app rodando na VM.

Fora do escopo: mudar as apps (código, camadas, testes e gargalo de cada uma continuam como estão).

## Divisão de trabalho

| Quem | O quê | Quantas vezes |
|---|---|---|
| Aluno (à mão) | Criar a VM a partir do VDI, configurar o redirecionamento de portas, setup inicial dentro da VM (clonar repo, venvs, `node_exporter`, chave SSH) | 1× |
| Aluno (à mão) | `docker compose up -d` em `monitoring/` | 1× por sessão |
| Aluno (à mão) | Desligar a VM, ajustar vCPU/RAM, ligar, rodar `run_scenario.sh <cenario>` | 4× |
| `run_scenario.sh` | Para cada app: sobe na VM via SSH, roda os 5 níveis no k6, exporta as métricas, para a app | automático, ~40 min por cenário |
| Aluno | `analyze.py` + commit dos resultados | 1× no fim |

## Arquitetura

```
VM (VirtualBox, NAT)                         Notebook (host)
─────────────────────                        ─────────────────────────────────────
sshd :22  ← 127.0.0.1:2222   ◄────────────  run_scenario.sh (ssh: start/stop app)
app (./run.sh) :8001/8002/8003  ◄─────────    ├─ curl (health, reset)
                                               ├─ k6 → results/<app>/<cenario>/<nivel>.k6.json
node_exporter :9100  ◄─────────────────────    └─ metricas_vm.py exportar → metricas_vm.csv
                                             Prometheus (scrape 1 s) ─► Grafana :3000
                                             analyze.py → results/_analysis/*.png
```

Prometheus e Grafana rodam em Docker com `network_mode: host`, porque o NAT do VirtualBox escuta em `127.0.0.1` do host, que um container em rede bridge não alcança. O Grafana serve para acompanhar ao vivo e para a demo. Os gráficos do relatório saem do `analyze.py`.

## Componentes

### Remover
- `scripts/` inteiro: `provision_vbox.sh`, `setup_vm.sh`, `run_all_scenarios.sh`, `systemd/`.
- `loadtest/run_experiment.py`, `loadtest/vm_agent.py`, `loadtest/collectors.py`, `loadtest/monitor.py`.
- `loadtest/locustfiles/`, `loadtest/scenarios/`, `loadtest/requirements.txt`.

### Manter sem mudanças
- `apps/*` (código, `run.sh`, `gunicorn.conf.py`, testes).

### `loadtest/k6/<app>.js` (adaptar)
- Passam a ser a ferramenta principal. Tirar o comentário "Secondary… Primary tool is Locust".
- **Defaults iguais aos valores que hoje estão nos YAML.** Galeria: `GALERIA_RES_MIN=1000`, `GALERIA_RES_MAX=3000`, `GALERIA_ID_SPACE=48`, pesos 3/6/1/1. Bolsa: pesos 10/2/2/3/3/2, saldo 10000. Todos: espera de 0,5–1,5 s.
- `stages`: 10 s de ramp-up até `VUS`, depois 2 min constantes.
- `handleSummary` grava o resumo do k6 + `inicio_epoch`/`fim_epoch` em `results/<app>/<cenario>/<nivel>.k6.json`, a partir de `-e CENARIO=… -e NIVEL=…`, e mantém o resumo de texto no terminal.

### `loadtest/run_scenario.sh <cenario> [app ...]` (novo, bash)
- Sem apps na linha de comando, roda as 3 em sequência.
- Configuração no topo do script, em variáveis simples e sobrescrevíveis por env: `VM_SSH` (padrão `ssh -p 2222 osboxes@127.0.0.1`), `VM_REPO` (caminho do repo na VM) e `PROM_URL` (padrão `http://127.0.0.1:9090`).
- Parâmetros por app (porta, variáveis de ambiente do servidor, reset, níveis) declarados no próprio script, em bloco `case` legível (tabela abaixo).
- Fluxo por app:
  1. Checagens: Prometheus responde, o alvo `vm` está `up`, a porta da app está livre.
  2. Sobe a app na VM via SSH (`nohup ... ./run.sh`, PID salvo em arquivo na VM) e espera `/health` responder do host (timeout de 60 s).
  3. Para cada nível: reset (se a app tiver), `k6 run`, pausa de 30 s.
  4. `metricas_vm.py exportar results/<app>/<cenario>`.
  5. Para a app via SSH (mata pelo PID salvo). Para também se o script for interrompido (`trap`).
- **Retomada:** se `results/<app>/<cenario>/<nivel>.k6.json` já existir, o nível é pulado. Isso permite rodar de novo após uma interrupção.
- Imprime em que app e nível está e uma estimativa de término.

### `loadtest/metricas_vm.py exportar` (novo, pequeno, só stdlib)
- `metricas_vm.py exportar results/<app>/<cenario>` lê o menor `inicio_epoch` e o maior `fim_epoch` dos JSON da pasta, consulta `query_range` no Prometheus (passo de 1 s) e grava `metricas_vm.csv`: timestamp, CPU %, iowait %, RAM usada, disco lido/escrito/s, rede rx/tx/s.

### `loadtest/analyze.py` (reescrever, enxuto)
- **Comparativos por app**, com uma linha por cenário: latência p50/p95 × usuários, req/s × usuários, taxa de erro × usuários, CPU/RAM média × usuários (média do CSV dentro da janela de cada nível).
- **Série no tempo por app × cenário** (substitui os prints do Grafana): CPU, RAM e disco ao longo do teste, com faixas marcando cada nível de usuários.
- Grava PNG + uma tabela CSV-resumo em `results/_analysis/`. Avisa quais cenários ou níveis estão faltando.
- Dependências: `pandas`, `matplotlib` (`loadtest/requirements.txt`).

### `monitoring/` (novo)
- `docker-compose.yml`: Prometheus + Grafana, ambos com `network_mode: host`, e volume para os dados do Prometheus.
- `prometheus.yml`: job `vm`, alvo `127.0.0.1:9100`, `scrape_interval: 1s`.
- Provisioning do Grafana: datasource Prometheus e um dashboard "VM" (CPU % user/system/iowait/steal, RAM, IO de disco, rede, load average).

### Documentação
- `PROVISIONING.md` vira o **roteiro manual**: criar a VM e o NAT (2222→22, 8001–8003, 9100), setup inicial na VM (clonar, venvs, `apt install prometheus-node-exporter`, `ssh-copy-id`), subir o `monitoring/`, o ciclo por cenário (desligar → vCPU/RAM → ligar → `run_scenario.sh`) e a análise. Serve de base para a seção de metodologia do relatório.
- `README.md`, `CONTRACT.md` (seção de carga) e `CLAUDE.md` atualizados.

## Parâmetros do experimento (vindos dos YAML que serão removidos)

| App | Porta | Variáveis do servidor | Antes de cada nível | Níveis (usuários) |
|---|---|---|---|---|
| cofre_digital | 8001 | `WORKERS=auto` | — | 1, 4, 8, 16, 32 |
| galeria_filtros | 8002 | `WORKERS=1 CACHE_MAX_MB=auto CACHE_RAM_FRACTION=0.4 MOSAICO_MAX_MB=256 MALLOC_ARENA_MAX=2` | `DELETE /cache` | 2, 5, 10, 20, 40 |
| bolsa_ficticia | 8003 | `WORKERS=4 SQLITE_JOURNAL_MODE=WAL SQLITE_SYNCHRONOUS=FULL SQLITE_BUSY_TIMEOUT_MS=30000 LEDGER_FSYNC=1` | `POST /admin/reset` | 5, 20, 50, 100, 200 |

- Nomes dos níveis: cofre e galeria usam `minima, baixa, media, alta, extrema`. Bolsa usa `baixa, media, alta, muito_alta, extrema`.
- Cenários de provisionamento (um recurso muda por vez): `1cpu-1gb`, `1cpu-2gb`, `2cpu-2gb`, `4cpu-4gb`.
- **1 repetição por nível** (decisão do grupo, por tempo): ~40 min por cenário, ~2 h 40 min no total. A limitação vai declarada no relatório.

## Estrutura dos resultados (versionada)

```
results/<app>/<cenario>/
    <nivel>.k6.json        # resumo k6 + inicio/fim
    metricas_vm.csv     # export do Prometheus
results/_analysis/      # gerado pelo analyze.py
```

Backup opcional do Prometheus (API de snapshot) fora do repo, só uma linha no roteiro.

## Tratamento de erros

- Erros HTTP ou de validação (`check` do k6) entram na taxa de erro do JSON e dos gráficos. Erros em demanda alta são resultado, não falha do script.
- `run_scenario.sh` aborta com mensagem clara se: o SSH falhar, o Prometheus ou o alvo `vm` não estiver up, a porta da app já estiver ocupada ou `/health` não responder em 60 s. Em qualquer saída, o `trap` para a app na VM.
- `metricas_vm.py exportar` falha com mensagem clara se a janela não tiver amostras (por exemplo, NAT da 9100 esquecido).

## Verificação

- Testes das apps continuam passando (`.venv/bin/pytest -q` em cada app).
- Smoke local sem VM: `VM_SSH=""` faz o script rodar os comandos localmente (`bash -c`), com `VM_REPO` apontando para o repo do host e um `node_exporter` local (container). `DURACAO=20s PAUSA=5 run_scenario.sh smoke cofre_digital` (duração e pausa sobrescrevíveis por env), depois `metricas_vm.py exportar` e `analyze.py` geram os arquivos esperados.
- `grep` não encontra referências a Locust, `run_experiment`, `vm_agent`, `monitor.py` ou `scripts/`.

## Notas da implementação

- `export_metrics.py` virou `loadtest/metricas_vm.py`, com dois subcomandos: `checar <cenario>` roda antes do cenário e aborta se o Prometheus não está coletando a VM ou se vCPU/RAM não batem com o nome; `exportar <pasta>` grava o CSV.
- Os arquivos de resultado são `<nivel>.k6.json` (k6) e `<nivel>.app.json` (estatísticas da app: `/cache/stats` na galeria, `/livro-caixa/stats` na bolsa).
- O repositório foi achatado: o conteúdo de `Web-backend-aplications/` foi para a raiz, os documentos foram para `docs/` e o material da disciplina para `docs/atividade/`. O lixo do macOS (`__MACOSX/`, ~19 mil arquivos) e o `.idea/` saíram do git.
