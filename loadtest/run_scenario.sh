#!/usr/bin/env bash
# Roda um cenário de provisionamento inteiro, uma app de cada vez:
#   sobe a app na VM (ssh) -> 5 níveis de carga com o k6 -> para a app -> exporta CPU/RAM/disco do Prometheus
#
# Uso:  loadtest/run_scenario.sh <cenario> [app ...]
# Ex.:  loadtest/run_scenario.sh 2cpu-2gb                  as 3 apps (~21 min)
#       loadtest/run_scenario.sh 2cpu-2gb bolsa_ficticia   só uma
#
# Antes: VM ligada com a configuração do cenário e `docker compose up -d` em monitoring/.
# Resultados em results/<app>/<cenario>/. Níveis que já têm resultado são pulados,
# então dá para rodar de novo depois de uma interrupção.
#
# Variáveis opcionais:
#   VM_SSH    comando para entrar na VM (padrão: ssh -p 2222 osboxes@127.0.0.1; vazio = roda tudo local)
#   VM_REPO   onde o repositório está na VM (padrão: ~/POI-lab-01)
#   VM_IP     endereço pelo qual o host acessa as apps (padrão: 127.0.0.1, via NAT)
#   DURACAO   tempo no patamar de cada nível (padrão: 1m)
#   RAMPA     tempo de subida até o número de usuários (padrão: 10s)
#   PAUSA     segundos de descanso entre níveis (padrão: 15)
#   PROM_URL  endereço do Prometheus (padrão: http://127.0.0.1:9090)
#   NIVEIS_TESTE  troca os níveis da app, só para calibrar (ex.: "a:64 b:128"; use com uma app só)
set -euo pipefail

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
VM_SSH="${VM_SSH-ssh -p 2222 -o BatchMode=yes osboxes@127.0.0.1}"
VM_REPO="${VM_REPO:-~/POI-lab-01}"
VM_IP="${VM_IP:-127.0.0.1}"
DURACAO="${DURACAO:-1m}"
RAMPA="${RAMPA:-10s}"
PAUSA="${PAUSA:-15}"
export PROM_URL="${PROM_URL:-http://127.0.0.1:9090}"

CENARIO="${1:?uso: $0 <cenario> [app ...]   ex.: $0 2cpu-2gb}"
shift
if [[ $# -gt 0 ]]; then APPS=("$@"); else APPS=(cofre_digital galeria_filtros bolsa_ficticia); fi

# Parâmetros de cada app: porta, variáveis do servidor, reset antes de cada nível,
# endpoint de estatísticas salvo depois de cada nível e níveis de demanda (nome:usuários).
configurar() {
  case "$1" in
    cofre_digital)
      PORTA=8001
      AMBIENTE="WORKERS=auto"   # um worker por vCPU: o GIL limita cada worker a ~1 núcleo
      RESET=""
      STATS=""
      NIVEIS="minima:4 baixa:8 media:16 alta:48 extrema:128" ;;
    galeria_filtros)
      PORTA=8002
      AMBIENTE="WORKERS=1 CACHE_MAX_MB=auto CACHE_RAM_FRACTION=0.4 MOSAICO_MAX_MB=256 MALLOC_ARENA_MAX=2"
      RESET="DELETE /cache"     # cada nível começa com o cache vazio
      STATS="/cache/stats"      # hits, misses, despejos
      NIVEIS="minima:10 baixa:20 media:40 alta:120 extrema:400" ;;
    bolsa_ficticia)
      PORTA=8003
      AMBIENTE="WORKERS=4 SQLITE_JOURNAL_MODE=WAL SQLITE_SYNCHRONOUS=FULL SQLITE_BUSY_TIMEOUT_MS=30000 LEDGER_FSYNC=1"
      RESET="POST /admin/reset" # zera banco e livro-caixa
      STATS="/livro-caixa/stats" # tempo de fsync, tamanho do banco
      NIVEIS="baixa:25 media:50 alta:100 muito_alta:200 extrema:500" ;;
    *)
      echo "app desconhecida: $1 (use cofre_digital, galeria_filtros ou bolsa_ficticia)" >&2
      exit 1 ;;
  esac
  NIVEIS="${NIVEIS_TESTE:-$NIVEIS}"
  URL="http://$VM_IP:$PORTA"
}

log() { echo "[$(date +%H:%M:%S)] $*"; }

na_vm() {  # roda um comando na VM (ou na própria máquina, se VM_SSH estiver vazio)
  if [[ -n "$VM_SSH" ]]; then $VM_SSH "$1"; else bash -c "$1"; fi
}

segundos() {  # 2m -> 120, 10s -> 10
  local n="${1%[sm]}"
  if [[ "$1" == *m ]]; then echo $((n * 60)); else echo "$n"; fi
}

app_responde() { curl -fsS -m 2 "$URL/health" >/dev/null 2>&1; }

subir_app() {
  if app_responde; then
    echo "Já tem algo respondendo em $URL. Pare essa app na VM antes de rodar o cenário." >&2
    exit 1
  fi
  log "subindo $APP ($AMBIENTE)"
  # "cd ... || exit" separado: com "cd && nohup ... &" o & pegaria a lista toda e o PID salvo
  # seria o de um subshell, não o do gunicorn (run.sh usa exec, então o PID é o do master)
  na_vm "cd $VM_REPO/apps/$APP || exit 1; $AMBIENTE nohup ./run.sh </dev/null >/tmp/$APP.log 2>&1 & echo \$! >/tmp/$APP.pid"
  APP_RODANDO="$APP"
  for _ in $(seq 60); do
    app_responde && return 0
    sleep 1
  done
  echo "$APP não respondeu em 60 s. Final do log na VM (/tmp/$APP.log):" >&2
  na_vm "tail -20 /tmp/$APP.log" >&2 || true
  exit 1
}

parar_app() {
  [[ -n "${APP_RODANDO:-}" ]] || return 0
  log "parando $APP_RODANDO"
  na_vm "kill \$(cat /tmp/$APP_RODANDO.pid) 2>/dev/null; rm -f /tmp/$APP_RODANDO.pid" || true
  for _ in $(seq 30); do
    app_responde || { APP_RODANDO=""; return 0; }
    sleep 1
  done
  log "aviso: $APP_RODANDO ainda responde em $URL depois de 30 s; pare na VM com: pkill -f gunicorn"
  APP_RODANDO=""
}

trap parar_app EXIT
trap 'exit 130' INT TERM

# ---------------------------------------------------------------- checagens
command -v k6 >/dev/null || { echo "k6 não encontrado. Instale (ver README) e rode de novo." >&2; exit 1; }
total_niveis=0
for APP in "${APPS[@]}"; do
  configurar "$APP"
  total_niveis=$((total_niveis + $(wc -w <<<"$NIVEIS")))
done
if [[ -n "$VM_SSH" ]] && ! $VM_SSH true; then
  echo "Não consegui entrar na VM com: $VM_SSH (a chave SSH foi copiada? ver docs/ROTEIRO_VM.md)" >&2
  exit 1
fi
python3 "$RAIZ/loadtest/metricas_vm.py" checar "$CENARIO"

por_nivel=$(( $(segundos "$RAMPA") + $(segundos "$DURACAO") + PAUSA ))
log "cenário $CENARIO: ${APPS[*]} — $total_niveis níveis, previsão de ~$(( total_niveis * por_nivel / 60 )) min"

# ---------------------------------------------------------------- execução
for APP in "${APPS[@]}"; do
  configurar "$APP"
  PASTA="$RAIZ/results/$APP/$CENARIO"
  mkdir -p "$PASTA"
  log "===== $APP ====="
  subir_app

  for par in $NIVEIS; do
    NIVEL="${par%%:*}"
    VUS="${par##*:}"
    ARQ="$PASTA/$NIVEL.k6.json"
    if [[ -f "$ARQ" ]]; then
      log "$NIVEL já tem resultado, pulando"
      continue
    fi
    if [[ -n "$RESET" ]]; then
      # com retentativas: se um worker morreu por falta de memória, o gunicorn leva alguns segundos para repor
      curl -fsS -m 120 --retry 10 --retry-all-errors --retry-delay 3 -X "${RESET%% *}" "$URL${RESET#* }" >/dev/null
    fi
    log "$NIVEL: $VUS usuários por $DURACAO"
    # grava em .tmp e só renomeia se o k6 terminar: um nível interrompido não conta como feito
    k6 run --quiet -e BASE_URL="$URL" -e VUS="$VUS" -e RAMPA="$RAMPA" -e DURACAO="$DURACAO" \
      -e APP="$APP" -e CENARIO="$CENARIO" -e NIVEL="$NIVEL" -e SAIDA="$ARQ.tmp" \
      "$RAIZ/loadtest/k6/$APP.js"
    mv "$ARQ.tmp" "$ARQ"
    if [[ -n "$STATS" ]]; then
      curl -fsS -m 30 "$URL$STATS" >"$PASTA/$NIVEL.app.json" || log "aviso: não consegui ler $STATS"
    fi
    sleep "$PAUSA"
  done

  parar_app
  python3 "$RAIZ/loadtest/metricas_vm.py" exportar "$PASTA"
done

log "cenário $CENARIO concluído. Resultados em results/<app>/$CENARIO/"
