#!/usr/bin/env python3
"""Gera a tabela-resumo e os gráficos do relatório a partir de results/.

    python loadtest/analyze.py                    todas as apps e cenários
    python loadtest/analyze.py --app galeria_filtros

Lê, para cada results/<app>/<cenario>/:
    <nivel>.k6.json     resumo do k6 (latência, vazão, falhas, início/fim)
    <nivel>.app.json    estatísticas da própria app (galeria: cache; bolsa: fsync), se houver
    metricas_vm.csv     CPU/RAM/disco/rede da VM exportados do Prometheus

Escreve em results/_analysis/:
    resumo.csv                       uma linha por app × cenário × nível
    <app>/<metrica>.png              métrica × usuários, uma linha por cenário
    <app>/visao_geral.png            todas as métricas acima numa figura só
    <app>/serie_<cenario>.png        CPU, RAM e disco ao longo do teste, uma faixa por nível

Nas médias da VM a rampa de subida de cada nível fica de fora.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
RESULTADOS = RAIZ / "results"
SAIDA = RESULTADOS / "_analysis"
MB = 1024 * 1024

# coluna do resumo -> título do gráfico comparativo
COMPARATIVOS = {
    "req_s": "Vazão (req/s)",
    "lat_p50_ms": "Latência p50 (ms)",
    "lat_p95_ms": "Latência p95 (ms)",
    "falhas_pct": "Falhas (%)",
    "cpu_pct": "CPU da VM (%)",
    "cpu_iowait_pct": "iowait da VM (%)",
    "mem_usada_mb": "RAM usada na VM (MB)",
    "disco_escrita_mbs": "Escrita em disco (MB/s)",
}
EXTRAS = {
    "galeria_filtros": {"cache_hit_pct": "Acertos no cache (%)", "swap_usada_mb": "Swap usada (MB)"},
    "bolsa_ficticia": {"fsync_medio_ms": "fsync médio do livro-caixa (ms)"},
}


def ordem_cenario(nome: str):
    """1cpu-1gb < 1cpu-2gb < 2cpu-2gb < 3cpu-3gb; nomes fora do padrão vão para o fim."""
    m = re.fullmatch(r"(\d+)cpu-(\d+(?:\.\d+)?)gb", nome)
    return (0, int(m[1]), float(m[2]), "") if m else (1, 0, 0.0, nome)


def stats_da_app(arquivo: Path) -> dict:
    # vazio quando a app não respondeu ao /stats no fim do nível (ex.: galeria em swap pesado)
    if not arquivo.exists() or not arquivo.read_text().strip():
        return {}
    d = json.loads(arquivo.read_text())
    if "hit_ratio" in d:  # galeria: /cache/stats
        return {"cache_hit_pct": 100 * d["hit_ratio"] if d["hit_ratio"] is not None else None,
                "cache_despejos": d["evictions"]}
    if "livro_caixa" in d:  # bolsa: /livro-caixa/stats
        return {"fsync_medio_ms": d["livro_caixa"]["processo"]["fsync_medio_ms"],
                "ordens": d["banco"]["ordens"]}
    return {}


def ler_execucoes(app_filtro: str | None) -> tuple[pd.DataFrame, dict]:
    linhas, series = [], {}
    for pasta in sorted(RESULTADOS.glob("*/*/")):
        app, cenario = pasta.parent.name, pasta.name
        if app.startswith("_") or (app_filtro and app != app_filtro):
            continue
        csv = pasta / "metricas_vm.csv"
        vm = pd.read_csv(csv) if csv.exists() else None
        if vm is None:
            print(f"aviso: {pasta.relative_to(RAIZ)} sem metricas_vm.csv (rode metricas_vm.py exportar)")
        else:
            series[(app, cenario)] = vm

        for arq in sorted(pasta.glob("*.k6.json")):
            e = json.loads(arq.read_text())
            m = e["metricas"]
            lat = m["http_req_duration"]["values"]
            chk = m.get("checks", {}).get("values", {"passes": 0, "fails": 0})
            linha = {
                "app": app, "cenario": cenario, "nivel": e["nivel"], "vus": e["vus"],
                "inicio_epoch": e["inicio_epoch"], "fim_epoch": e["fim_epoch"],
                "requisicoes": m["http_reqs"]["values"]["count"],
                "req_s": m["http_reqs"]["values"]["rate"],
                "lat_media_ms": lat["avg"], "lat_p50_ms": lat["med"],
                "lat_p95_ms": lat["p(95)"], "lat_p99_ms": lat["p(99)"],
                "falhas_pct": 100 * chk["fails"] / max(1, chk["passes"] + chk["fails"]),
            }
            if vm is not None:
                janela = vm[(vm.ts_epoch >= e["inicio_epoch"] + e["rampa_s"]) & (vm.ts_epoch <= e["fim_epoch"])]
                linha.update({
                    "cpu_pct": janela.cpu_pct.mean(),
                    "cpu_iowait_pct": janela.cpu_iowait_pct.mean(),
                    "load1": janela.load1.mean(),
                    "mem_usada_mb": janela.mem_usada_bytes.mean() / MB,
                    "mem_usada_max_mb": janela.mem_usada_bytes.max() / MB,
                    "swap_usada_mb": janela.swap_usada_bytes.max() / MB,
                    "disco_escrita_mbs": janela.disco_escrita_Bps.mean() / MB,
                    "disco_ocupado_pct": janela.disco_ocupado_pct.mean(),
                })
            linha.update(stats_da_app(pasta / f"{e['nivel']}.app.json"))
            linhas.append(linha)
    return pd.DataFrame(linhas), series


def avisar_faltando(df: pd.DataFrame) -> None:
    for app, g in df.groupby("app"):
        niveis = set(g.nivel)
        for cenario, gc in g.groupby("cenario"):
            faltam = niveis - set(gc.nivel)
            if faltam:
                print(f"aviso: {app}/{cenario} sem os níveis {sorted(faltam)}")


def desenhar_comparativo(ax, g: pd.DataFrame, coluna: str, titulo: str) -> None:
    for cenario in sorted(g.cenario.unique(), key=ordem_cenario):
        c = g[g.cenario == cenario].sort_values("vus")
        ax.plot(c.vus, c[coluna], marker="o", label=cenario)
    ax.set_xscale("log", base=2)
    ax.set_xticks(sorted(g.vus.unique()), labels=[str(v) for v in sorted(g.vus.unique())])
    ax.minorticks_off()
    ax.set_xlabel("usuários simultâneos")
    ax.set_title(titulo)
    ax.grid(alpha=0.3)
    ax.set_ylim(bottom=0)


def graficos_comparativos(app: str, g: pd.DataFrame, destino: Path) -> None:
    metricas = {**COMPARATIVOS, **EXTRAS.get(app, {})}
    metricas = {k: v for k, v in metricas.items() if k in g and g[k].notna().any()}

    for coluna, titulo in metricas.items():
        fig, ax = plt.subplots(figsize=(6.4, 4.2))
        desenhar_comparativo(ax, g, coluna, titulo)
        ax.legend(title="cenário")
        fig.suptitle(app, fontsize=9, color="gray")
        fig.tight_layout()
        fig.savefig(destino / f"{coluna}.png", dpi=150)
        plt.close(fig)

    colunas = 3
    linhas = -(-len(metricas) // colunas)
    fig, eixos = plt.subplots(linhas, colunas, figsize=(5 * colunas, 3.6 * linhas), squeeze=False)
    for ax, (coluna, titulo) in zip(eixos.flat, metricas.items()):
        desenhar_comparativo(ax, g, coluna, titulo)
    for ax in list(eixos.flat)[len(metricas):]:
        ax.axis("off")
    eixos.flat[0].legend(title="cenário", fontsize=8)
    fig.suptitle(f"{app}: demanda × provisionamento")
    fig.tight_layout()
    fig.savefig(destino / "visao_geral.png", dpi=120)
    plt.close(fig)


def grafico_serie(app: str, cenario: str, vm: pd.DataFrame, niveis: pd.DataFrame, destino: Path) -> None:
    t0 = vm.ts_epoch.min()
    t = (vm.ts_epoch - t0) / 60
    fig, (a_cpu, a_mem, a_disco) = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

    a_cpu.stackplot(t, vm.cpu_user_pct.fillna(0), vm.cpu_system_pct.fillna(0), vm.cpu_iowait_pct.fillna(0),
                    labels=["user", "system", "iowait"], alpha=0.8)
    a_cpu.set_ylim(0, 100)
    a_cpu.set_ylabel("CPU (%)")
    a_cpu.legend(loc="upper left", fontsize=8)

    a_mem.plot(t, vm.mem_usada_bytes / MB, label="usada")
    a_mem.plot(t, vm.swap_usada_bytes / MB, label="swap usada")
    a_mem.axhline(vm.mem_total_bytes.max() / MB, color="gray", ls="--", lw=1, label="total")
    a_mem.set_ylabel("RAM (MB)")
    a_mem.set_ylim(bottom=0)
    a_mem.legend(loc="upper left", fontsize=8)

    a_disco.plot(t, vm.disco_escrita_Bps / MB, label="escrita")
    a_disco.plot(t, vm.disco_leitura_Bps / MB, label="leitura")
    a_disco.set_ylabel("Disco (MB/s)")
    a_disco.set_ylim(bottom=0)
    a_disco.set_xlabel("minutos desde o início")
    a_disco.legend(loc="upper left", fontsize=8)

    for i, n in enumerate(niveis.sort_values("inicio_epoch").itertuples()):
        x0, x1 = (n.inicio_epoch - t0) / 60, (n.fim_epoch - t0) / 60
        for ax in (a_cpu, a_mem, a_disco):
            ax.axvspan(x0, x1, color="tab:gray", alpha=0.08 if i % 2 else 0.16, lw=0)
        a_cpu.text((x0 + x1) / 2, 102, f"{n.vus} usuários", ha="center", va="bottom", fontsize=8)

    for ax in (a_cpu, a_mem, a_disco):
        ax.grid(alpha=0.3)
    fig.suptitle(f"{app} — {cenario}", y=0.995)
    fig.tight_layout()
    fig.savefig(destino / f"serie_{cenario}.png", dpi=120)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", help="só uma app (cofre_digital, galeria_filtros, bolsa_ficticia)")
    args = ap.parse_args()

    df, series = ler_execucoes(args.app)
    if df.empty:
        raise SystemExit("nenhum resultado em results/ (rode loadtest/run_scenario.sh antes)")
    avisar_faltando(df)

    SAIDA.mkdir(parents=True, exist_ok=True)
    df["_ordem"] = df.cenario.map(ordem_cenario)
    df = df.sort_values(["app", "_ordem", "vus"]).drop(columns="_ordem")
    df.round(3).to_csv(SAIDA / "resumo.csv", index=False)

    for app, g in df.groupby("app"):
        destino = SAIDA / app
        destino.mkdir(exist_ok=True)
        graficos_comparativos(app, g, destino)
        for cenario, gc in g.groupby("cenario"):
            if (app, cenario) in series:
                grafico_serie(app, cenario, series[(app, cenario)], gc, destino)
        print(f"{app}: {g.cenario.nunique()} cenário(s), {len(g)} execuções -> {destino.relative_to(RAIZ)}/")
    print(f"tabela: {(SAIDA / 'resumo.csv').relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
