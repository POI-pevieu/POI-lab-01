#!/usr/bin/env python3
"""Consulta no Prometheus as métricas da VM (coletadas pelo node_exporter).

    metricas_vm.py checar <cenario>   confere se o Prometheus está coletando a VM e se
                                      vCPU/RAM batem com o nome do cenário (ex.: 2cpu-2gb)
    metricas_vm.py exportar <pasta>   grava <pasta>/metricas_vm.csv cobrindo a janela dos
                                      testes (lida dos *.k6.json da mesma pasta)

PROM_URL muda o endereço do Prometheus (padrão http://127.0.0.1:9090).
Só usa a biblioteca padrão do Python.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

PROM_URL = os.environ.get("PROM_URL", "http://127.0.0.1:9090").rstrip("/")

# Só os discos inteiros (sda, vda, nvme0n1): partições e LVM (dm-*) contariam o mesmo IO duas vezes.
DISCO = 'device=~"sd[a-z]+|vd[a-z]+|nvme[0-9]+n[0-9]+"'


def cpu(modo: str) -> str:
    return f'100 * avg(rate(node_cpu_seconds_total{{mode="{modo}"}}[5s]))'


# coluna do CSV -> consulta PromQL
CONSULTAS = {
    "cpu_pct": '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5s])))',
    "cpu_user_pct": cpu("user"),
    "cpu_system_pct": cpu("system"),
    "cpu_iowait_pct": cpu("iowait"),
    "cpu_steal_pct": cpu("steal"),
    "load1": "node_load1",
    "mem_total_bytes": "node_memory_MemTotal_bytes",
    "mem_usada_bytes": "node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes",
    "mem_cache_bytes": "node_memory_Cached_bytes + node_memory_Buffers_bytes",
    "swap_usada_bytes": "node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes",
    "disco_leitura_Bps": f"sum(rate(node_disk_read_bytes_total{{{DISCO}}}[5s]))",
    "disco_escrita_Bps": f"sum(rate(node_disk_written_bytes_total{{{DISCO}}}[5s]))",
    "disco_ocupado_pct": f"100 * sum(rate(node_disk_io_time_seconds_total{{{DISCO}}}[5s]))",
    "rede_rx_Bps": 'sum(rate(node_network_receive_bytes_total{device!="lo"}[5s]))',
    "rede_tx_Bps": 'sum(rate(node_network_transmit_bytes_total{device!="lo"}[5s]))',
}


def erro(msg: str) -> None:
    print(f"ERRO: {msg}", file=sys.stderr)
    sys.exit(1)


def prometheus(caminho: str, **params) -> list:
    url = f"{PROM_URL}{caminho}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            corpo = json.load(resp)
    except (urllib.error.URLError, OSError) as e:
        erro(f"não consegui falar com o Prometheus em {PROM_URL} ({e}). "
             "Ele está rodando? (docker compose up -d em monitoring/)")
    if corpo.get("status") != "success":
        erro(f"Prometheus recusou a consulta {params}: {corpo.get('error')}")
    return corpo["data"]["result"]


def valor_atual(consulta: str) -> float | None:
    resultado = prometheus("/api/v1/query", query=consulta)
    return float(resultado[0]["value"][1]) if resultado else None


def checar(cenario: str) -> None:
    if valor_atual('up{job="vm"}') != 1:
        erro("o Prometheus não está coletando a VM (alvo 127.0.0.1:9100). Confira se a VM está ligada, "
             "se o node_exporter está rodando nela e se o NAT da porta 9100 existe.")
    cpus = int(valor_atual('count(node_cpu_seconds_total{mode="idle"})') or 0)
    ram_gib = (valor_atual("node_memory_MemTotal_bytes") or 0) / 2**30
    print(f"VM: {cpus} vCPU, {ram_gib:.2f} GiB de RAM")

    m = re.fullmatch(r"(\d+)cpu-(\d+(?:\.\d+)?)gb", cenario)
    if not m:
        print(f"aviso: '{cenario}' não segue o padrão <N>cpu-<M>gb, então não dá para conferir a VM")
        return
    cpus_esperados, ram_esperada = int(m[1]), float(m[2])
    # o kernel reserva um pouco da RAM, então 1 GB aparece como ~0,95 GiB
    if cpus != cpus_esperados or abs(ram_gib - ram_esperada) > 0.25 * ram_esperada:
        erro(f"o cenário '{cenario}' pede {cpus_esperados} vCPU e {ram_esperada:g} GB, mas a VM tem "
             f"{cpus} vCPU e {ram_gib:.2f} GiB. Esqueceu de mudar a configuração no VirtualBox?")


def exportar(pasta: Path) -> None:
    execucoes = [json.loads(p.read_text()) for p in sorted(pasta.glob("*.k6.json"))]
    if not execucoes:
        erro(f"nenhum *.k6.json em {pasta}")
    inicio = min(e["inicio_epoch"] for e in execucoes)
    fim = max(e["fim_epoch"] for e in execucoes)

    linhas: dict[float, dict] = {}
    for coluna, consulta in CONSULTAS.items():
        series = prometheus("/api/v1/query_range", query=consulta, start=f"{inicio:.3f}",
                            end=f"{fim:.3f}", step="1")
        for ts, valor in (series[0]["values"] if series else []):
            linhas.setdefault(float(ts), {})[coluna] = valor

    if not any("cpu_pct" in v for v in linhas.values()):
        erro("o Prometheus não tem amostras da VM nessa janela. O node_exporter estava acessível durante o teste?")

    destino = pasta / "metricas_vm.csv"
    with destino.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_epoch", *CONSULTAS])
        for ts in sorted(linhas):
            w.writerow([f"{ts:.0f}", *(linhas[ts].get(c, "") for c in CONSULTAS)])

    hora = lambda t: datetime.fromtimestamp(t).strftime("%H:%M:%S")
    print(f"{destino}: {len(linhas)} amostras ({hora(inicio)} a {hora(fim)})")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("checar", "exportar"):
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    if sys.argv[1] == "checar":
        checar(sys.argv[2])
    else:
        exportar(Path(sys.argv[2]))


if __name__ == "__main__":
    main()
