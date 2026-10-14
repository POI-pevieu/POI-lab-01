# Roteiro: VM, monitoramento e experimentos

Passo a passo para rodar os experimentos. A configuração da VM é feita **à mão**, pela interface do VirtualBox. Cada cenário é rodado pelo `loadtest/run_scenario.sh`.

```
 Notebook (host)                                        VM VirtualBox (Ubuntu Server 22.04)
 ─────────────────────────────────                      ────────────────────────────────────
 run_scenario.sh ── ssh 127.0.0.1:2222 ───────────────▶ sobe/para a app (./run.sh)
        ├── k6 ──── HTTP 127.0.0.1:800x ──────────────▶ app (gunicorn) :8001 / :8002 / :8003
        └── metricas_vm.py ─┐
 Prometheus :9090 ──────────┴ scrape 127.0.0.1:9100 ──▶ node_exporter :9100
 Grafana :3000 (dashboard "VM - recursos")
```

A carga sai do notebook, nunca de dentro da VM: assim o k6 não disputa CPU e RAM com a app medida.

## 1. Criar a VM (uma vez)

Siga o roteiro do professor (`docs/atividade/Atividade 2 - Roteiro virtualização local (VirtualBox).pdf`):

1. Copie o disco `ubuntu_serve_2204_poi.vdi` (no LCC fica em `/local/uasc/images`).
2. No VirtualBox, crie a VM: tipo Linux / Ubuntu 64-bit, usando o disco existente.
3. Em **Configurações → Rede → Adaptador 1 (NAT) → Avançado → Redirecionamento de portas**, crie as regras abaixo. Em todas: protocolo TCP, IP do hospedeiro `127.0.0.1`, IP do convidado em branco.

| Nome | Porta do hospedeiro | Porta do convidado | Para quê |
|---|---|---|---|
| ssh | 2222 | 22 | o script entra na VM |
| cofre | 8001 | 8001 | app CPU-bound |
| galeria | 8002 | 8002 | app memory-bound |
| bolsa | 8003 | 8003 | app IO-bound |
| node | 9100 | 9100 | métricas da VM (Prometheus) |

4. Ligue a VM e entre com `osboxes` / `osboxes.org`.

## 2. Preparar a VM (uma vez, dentro da VM)

```bash
sudo apt update
sudo apt install -y git python3-venv openssh-server prometheus-node-exporter
systemctl status prometheus-node-exporter        # deve estar "active (running)", porta 9100

git clone <url-do-repositorio> ~/POI-lab-01
cd ~/POI-lab-01/apps
for app in cofre_digital galeria_filtros bolsa_ficticia; do
  (cd $app && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)
done
```

Teste uma app na mão. É também o que você mostra em sala:

```bash
cd ~/POI-lab-01/apps/cofre_digital && WORKERS=auto ./run.sh     # Ctrl+C para parar
```

No notebook, `curl localhost:8001/health` deve responder.

## 3. Preparar o notebook (uma vez)

```bash
# chave SSH para o script entrar na VM sem pedir senha
ssh-keygen -t ed25519                         # só se você ainda não tiver uma chave
ssh-copy-id -p 2222 osboxes@127.0.0.1
ssh -p 2222 osboxes@127.0.0.1 true            # não pode pedir senha

# k6: siga https://grafana.com/docs/k6/latest/set-up/install-k6/ (ou baixe o binário em
# https://github.com/grafana/k6/releases e coloque em ~/.local/bin)
k6 version

# ambiente Python da análise
python3 -m venv loadtest/.venv && loadtest/.venv/bin/pip install -r loadtest/requirements.txt
```

## 4. Subir o monitoramento (a cada sessão)

```bash
cd monitoring && docker compose up -d
```

- Prometheus: <http://localhost:9090>. Em *Status → Targets*, o alvo `vm` deve estar **UP** com a VM ligada.
- Grafana: <http://localhost:3000>. Abre direto no dashboard **VM - recursos**, sem login.

## 5. Rodar um cenário (repetir para cada um)

| Cenário | vCPU | RAM | O que muda em relação ao anterior |
|---|---:|---:|---|
| `1cpu-1gb` | 1 | 1024 MB | ponto de partida |
| `1cpu-2gb` | 1 | 2048 MB | só a RAM, o que interessa para a galeria |
| `2cpu-2gb` | 2 | 2048 MB | só a CPU, o que interessa para o cofre |
| `4cpu-4gb` | 4 | 4096 MB | cenário folgado, para comparação |

1. Desligue a VM (`sudo poweroff` dentro dela).
2. No VirtualBox, em **Configurações → Sistema**, ajuste **Memória base** (aba Placa-mãe) e **Processadores** (aba Processador).
3. Ligue a VM e espere o boot. Não precisa logar: o node_exporter e o SSH sobem sozinhos.
4. No notebook, na raiz do repositório:

   ```bash
   loadtest/run_scenario.sh 1cpu-1gb
   ```

O script confere se a VM tem mesmo os vCPUs e a RAM do nome do cenário. Se você esquecer de mudar a configuração, ele para antes de começar. Depois roda as 3 apps, cada uma em 5 níveis de demanda (2 min cada + 10 s de rampa + 30 s de pausa), em ~40 min. Durante esse tempo:

- não use o notebook para nada pesado (navegador com vídeo, compilação, downloads);
- deixe o notebook na tomada, porque na bateria a CPU fica mais lenta;
- acompanhe pelo Grafana, se quiser.

Se algo interromper o teste, rode o mesmo comando de novo: os níveis que já terminaram são pulados. Para refazer um nível, apague o `results/<app>/<cenario>/<nivel>.k6.json` dele.

Ao final, salve os resultados:

```bash
git add results && git commit -m "resultados 1cpu-1gb" && git push
```

## 6. Gerar os gráficos

```bash
loadtest/.venv/bin/python loadtest/analyze.py
```

Saída em `results/_analysis/`: `resumo.csv` e, por app, um gráfico de cada métrica × usuários (uma linha por cenário), uma visão geral e a série no tempo de CPU/RAM/disco de cada cenário.

**Como ler:**

- **Degradação:** a vazão (req/s) para de crescer enquanto os usuários aumentam, e a latência p95 dispara. Esse "joelho" é a capacidade daquele provisionamento.
- **Efeito do provisionamento:** compare onde o joelho aparece em cada cenário.
  - **cofre:** deve melhorar com mais vCPUs.
  - **galeria:** deve melhorar com mais RAM. Olhe os acertos no cache e a swap.
  - **bolsa:** deve mudar pouco com vCPU e RAM, porque o gargalo é o disco. Olhe iowait, escrita e fsync.

## Cuidados que valem citar no relatório

- **NAT do VirtualBox:** o NAT passa cada pacote por um processo do host e pode limitar a vazão em demanda alta, principalmente na bolsa, com 200 usuários. Se a vazão ficar plana com a CPU e o disco da VM ociosos, suspeite do NAT. Use a mesma rede em todos os cenários.
- **fsync na VM (bolsa):** dependendo da configuração do disco virtual, o VirtualBox pode atender o `fsync` pelo cache do host, e aí a escrita fica mais barata do que num disco real. O `fsync_medio_ms` salvo em `<nivel>.app.json` mostra o custo real observado.
- **Pouca RAM (galeria em 1 GB):** cada filtro aloca cópias temporárias das imagens. Com muitos usuários, a VM pode entrar em swap ou o kernel pode matar o worker (OOM). O gunicorn sobe outro, o cache recomeça vazio e aparecem falhas. Isso é degradação de verdade, não erro do teste.
- **Overhead do monitoramento:** o node_exporter usa ~15 MB de RAM e bem menos de 1% de CPU, o mesmo em todos os cenários.
- **1 repetição por nível:** cada ponto dos gráficos é uma medição só.

## Backup opcional do Prometheus

Os CSVs exportados em `results/` já guardam o que o relatório usa. Se quiser guardar também o banco do Prometheus:

```bash
curl -XPOST http://localhost:9090/api/v1/admin/tsdb/snapshot   # cria o snapshot dentro do volume
cd monitoring && docker compose cp prometheus:/prometheus/snapshots ./backup-prometheus
```

Guarde essa pasta no Drive junto com o disco da VM, não no git.
