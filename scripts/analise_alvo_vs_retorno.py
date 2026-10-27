'''
------------------------------------------------------------
Author: Fabio da Costa Pinto
Email: fpinto0416@gmail.com
Created: Outubro 2026
License: Proprietary / Private Use
------------------------------------------------------------
Description:

Análise "Alvo × Retorno": na data-base (padrão = data mais antiga de
dados/analyst_insights.csv), mede o quanto cada ação estava abaixo do
preço-alvo mínimo (target low) e médio (target mean) — mesma fórmula e
mesma ordenação dos rankings do Panorama de Alvos (gerar_panorama.py) — e
cruza com o retorno da ação da data-base até o último pregão fechado.

- Retorno principal: fechamento ajustado do yfinance (inclui proventos),
  entrada no fechamento da data-base.
- Robustez: entrada no fechamento do pregão SEGUINTE (o alvo do CSV é
  coletado depois do fechamento — entrar no mesmo fechamento tem algumas
  horas de lookahead).
- Conferência: preco_atual do CSV na data-base vs na data mais recente.
- Benchmark: BOVA11 (proxy do IBrA, que não tem série no yfinance).

Escreve dados/analise_alvo_vs_retorno.html a partir de
scripts/_template_alvo_retorno.html (tokens __MARCADOR__), sem edição
manual. Uso:

    python scripts/analise_alvo_vs_retorno.py [--data-base AAAA-MM-DD] [--data-fim AAAA-MM-DD]
------------------------------------------------------------
'''

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

from gerar_panorama import (CSV_ANALYST, CSV_SNAPSHOT, PASTA_DADOS, PASTA_SCRIPTS,
                            montar_registro_ativo)

SAIDA_HTML = os.path.join(PASTA_DADOS, "analise_alvo_vs_retorno.html")
TEMPLATE = os.path.join(PASTA_SCRIPTS, "_template_alvo_retorno.html")
BENCH = "BOVA11.SA"
URL_PANORAMA = "https://claude.ai/code/artifact/92957051-3ba4-4940-bece-1af526ebe172"
_FUSO_BRT = timezone(timedelta(hours=-3))
DIVERGENCIA_CSV = 0.15  # |retorno CSV − retorno yfinance s/ proventos| acima disso é sinalizado


def carregar_data_base(data_base):
    """Mesmo merge de gerar_panorama.carregar_dados(), mas numa data escolhida."""
    snap = pd.read_csv(CSV_SNAPSHOT)
    ana = pd.read_csv(CSV_ANALYST)
    if data_base is None:
        data_base = ana["data"].min()
    snap = snap[snap["data"] == data_base].copy()
    ana_d = ana[ana["data"] == data_base].copy()
    df = snap.merge(ana_d.drop(columns=["data"]), on="ticker", how="left", suffixes=("", "_an"))
    ultima = ana[ana["data"] == ana["data"].max()][["ticker", "preco_atual"]]
    return df, data_base, ana["data"].max(), ultima.set_index("ticker")["preco_atual"]


def ultimo_pregao_fechado():
    agora = datetime.now(_FUSO_BRT)
    hoje = pd.Timestamp(agora.date())
    # B3 fecha 17h + call; antes das 18h30 BRT a barra do dia ainda é intraday
    if agora.hour < 18 or (agora.hour == 18 and agora.minute < 30):
        hoje = hoje - pd.Timedelta(days=1)
    return hoje


def baixar_precos(tickers, data_base, data_fim):
    ini = pd.Timestamp(data_base) - pd.Timedelta(days=10)
    sims = [f"{t}.SA" for t in tickers] + [BENCH]
    aj = yf.download(sims, start=ini, end=data_fim + pd.Timedelta(days=1),
                     auto_adjust=True, progress=False, threads=True)["Close"]
    cru = yf.download(sims, start=ini, end=data_fim + pd.Timedelta(days=1),
                      auto_adjust=False, progress=False, threads=True)["Close"]
    return aj.loc[:data_fim], cru.loc[:data_fim]


def retorno(serie, d_ini, pos_ini=0):
    s = serie.dropna()
    s_ini = s.loc[s.index >= pd.Timestamp(d_ini)]
    if len(s_ini) <= pos_ini or s.empty:
        return None, None, None
    p0 = s_ini.iloc[pos_ini]
    p1 = s.iloc[-1]
    return float(p1 / p0 - 1), s_ini.index[pos_ini], s.index[-1]


def spearman_parcial(x, y, controles):
    """Spearman de x com y depois de tirar (em postos) o efeito linear dos controles."""
    base = pd.DataFrame({"x": x, "y": y, **controles}).dropna().rank()
    Z = np.column_stack([np.ones(len(base))] + [base[c] for c in controles])
    res = {c: base[c] - Z @ np.linalg.lstsq(Z, base[c], rcond=None)[0] for c in ("x", "y")}
    rho, p = stats.pearsonr(res["x"], res["y"])
    return float(rho), float(p), int(len(base))


def resumo_estatistico(x, y, top_mask, bench, controles):
    rho_parc, p_parc, n_parc = spearman_parcial(x, y, controles)
    x, y = np.asarray(x), np.asarray(y)
    rho, p_rho = stats.spearmanr(x, y)
    reg = stats.linregress(x, y)
    top, resto = y[top_mask], y[~top_mask]
    t = stats.ttest_ind(top, resto, equal_var=False)
    mw = stats.mannwhitneyu(top, resto, alternative="two-sided")
    # quantos Top 10 aleatórios batem o Top 10 real (permutação, 10k sorteios)
    rng = np.random.default_rng(42)
    sorteios = np.array([rng.choice(y, size=top_mask.sum(), replace=False).mean() for _ in range(10000)])
    p_perm = float((sorteios >= top.mean()).mean())
    return {
        "n": int(len(x)),
        "spearman": float(rho), "p_spearman": float(p_rho),
        "slope": float(reg.slope), "intercept": float(reg.intercept),
        "r2": float(reg.rvalue ** 2), "p_slope": float(reg.pvalue),
        "top_media": float(top.mean()), "resto_media": float(resto.mean()),
        "top_mediana": float(np.median(top)), "resto_mediana": float(np.median(resto)),
        "universo_media": float(y.mean()), "bench": bench,
        "top_vs_bench": float(top.mean() - bench),
        "top_batem_bench": int((top > bench).sum()),
        "spearman_parcial": rho_parc, "p_parcial": p_parc, "n_parcial": n_parc,
        "p_welch": float(t.pvalue), "p_mw": float(mw.pvalue), "p_perm": p_perm,
    }


def analisar(data_base=None, data_fim=None):
    df, data_base, data_csv_fim, preco_csv_fim = carregar_data_base(data_base)
    data_fim = pd.Timestamp(data_fim) if data_fim else ultimo_pregao_fechado()

    registros = [montar_registro_ativo(r) | {"preco_base": r["preco_atual"], "market_cap": r.get("market_cap"), "beta": r.get("beta")} for _, r in df.iterrows()]
    com_alvo = [r for r in registros if r["tem_alvo"]]
    sem_alvo = sorted(r["ticker"] for r in registros if not r["tem_alvo"])

    aj, cru = baixar_precos([r["ticker"] for r in com_alvo], data_base, data_fim)
    ret_bench, d0, d1 = retorno(aj[BENCH], data_base)
    ret_bench_d1, _, _ = retorno(aj[BENCH], data_base, pos_ini=1)

    linhas, descartados, alertas = [], [], []
    for r in com_alvo:
        t = r["ticker"]
        col = f"{t}.SA"
        ret, ini, fim = retorno(aj[col], data_base) if col in aj else (None, None, None)
        if ret is None or ini.normalize() != pd.Timestamp(data_base):
            descartados.append({"ticker": t, "motivo": "sem preço no yfinance na data-base" if ret is not None or col not in aj else "sem preço no yfinance"})
            continue
        ret_d1, _, _ = retorno(aj[col], data_base, pos_ini=1)
        # conferência na MESMA janela do CSV (data-base → última data do CSV), senão compara períodos diferentes
        ret_cru, _, _ = retorno(cru[col].loc[:pd.Timestamp(data_csv_fim)], data_base)
        p_fim_csv = preco_csv_fim.get(t)
        ret_csv = float(p_fim_csv / r["preco_base"] - 1) if pd.notna(p_fim_csv) and r["preco_base"] else None
        if ret_csv is not None and abs(ret_csv - ret_cru) > DIVERGENCIA_CSV:
            alertas.append({"ticker": t, "ret_csv": ret_csv, "ret_yf": ret_cru})
        linhas.append({
            "ticker": t, "nome": r["nome"], "setor": r["setor"], "n_analistas": r["n_analistas"],
            "rec": r["rec_label"], "preco_base": float(r["preco_base"]),
            "low_fmt": r["low_fmt"], "mean_fmt": r["mean_fmt"],
            "dist_low": r["dist_low_pct"] / 100, "dist_mean": r["dist_mean_pct"] / 100,
            "ret": ret, "ret_d1": ret_d1, "ret_csv": ret_csv, "ret_cru": ret_cru,
            "fim": fim.strftime("%Y-%m-%d"),
            "log_mcap": float(np.log(r["market_cap"])) if pd.notna(r.get("market_cap")) and r["market_cap"] > 0 else None,
            "beta": float(r["beta"]) if pd.notna(r.get("beta")) else None,
            "saiu_do_csv": bool(pd.isna(p_fim_csv)),
        })

    tab = pd.DataFrame(linhas)
    resultado = {}
    for chave, dist in (("low", "dist_low"), ("mean", "dist_mean")):
        tab = tab.sort_values(dist, ascending=False).reset_index(drop=True)
        top_mask = np.zeros(len(tab), dtype=bool)
        top_mask[:10] = True
        # postos de tamanho e beta + dummies de setor (rank de 0/1 continua binário)
        setores = pd.get_dummies(tab["setor"], prefix="s", drop_first=True).astype(float)
        controles = {"log_mcap": tab["log_mcap"], "beta": tab["beta"], **setores.to_dict("series")}
        principal = resumo_estatistico(tab[dist], tab["ret"], top_mask, ret_bench, controles)
        robustez = resumo_estatistico(tab[dist], tab["ret_d1"], top_mask, ret_bench_d1, controles)
        setores_top = tab.head(10)["setor"].value_counts()
        ranking = [{"rank": i + 1, **row} for i, row in enumerate(tab.to_dict("records"))]
        resultado[chave] = {"stats": principal, "stats_d1": robustez, "ranking": ranking,
                            "setor_top": setores_top.index[0], "setor_top_n": int(setores_top.iloc[0]),
                            "rho_mcap": float(stats.spearmanr(tab[dist], tab["log_mcap"], nan_policy="omit")[0])}
    # evolução dia a dia: Spearman e Top 10 − resto se a janela terminasse em cada pregão
    base = aj.loc[pd.Timestamp(data_base)]
    cols = tab["ticker"] + ".SA"
    evolucao = []
    for d in aj.index[aj.index > pd.Timestamp(data_base)]:
        ret_d = (aj.loc[d, cols] / base[cols] - 1).to_numpy()
        ponto = {"data": d.strftime("%Y-%m-%d"), "bench": float(aj.loc[d, BENCH] / base[BENCH] - 1),
                 "universo": float(np.nanmean(ret_d))}
        for chave, dist in (("low", "dist_low"), ("mean", "dist_mean")):
            ordem = tab[dist].rank(ascending=False, method="first").to_numpy() <= 10
            ponto[f"rho_{chave}"] = float(stats.spearmanr(tab[dist], ret_d, nan_policy="omit")[0])
            ponto[f"spread_{chave}"] = float(np.nanmean(ret_d[ordem]) - np.nanmean(ret_d[~ordem]))
        evolucao.append(ponto)
    top_low = set(t["ticker"] for t in resultado["low"]["ranking"][:10])
    top_mean = set(t["ticker"] for t in resultado["mean"]["ranking"][:10])

    return {
        "data_base": data_base, "data_fim": d1.strftime("%Y-%m-%d"),
        "data_csv_fim": data_csv_fim, "gerado_em": datetime.now(_FUSO_BRT).strftime("%d/%m/%Y %H:%M"),
        "bench": BENCH.replace(".SA", ""), "ret_bench": ret_bench, "ret_bench_d1": ret_bench_d1,
        "n_universo": len(registros), "n_com_alvo": len(com_alvo),
        "sem_alvo": sem_alvo, "descartados": descartados, "alertas": alertas,
        "url_panorama": URL_PANORAMA, "sobreposicao_top": len(top_low & top_mean),
        "saiu_do_csv": sorted(t["ticker"] for t in linhas if t["saiu_do_csv"]),
        "ret_universo_eq": float(tab["ret"].mean()), "evolucao": evolucao, **resultado,
    }


def fmt_data(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")


def gerar(data_base=None, data_fim=None):
    res = analisar(data_base, data_fim)
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    substituicoes = {
        "__DATA_BASE_FMT__": fmt_data(res["data_base"]),
        "__DATA_FIM_FMT__": fmt_data(res["data_fim"]),
        "__GERADO_EM__": res["gerado_em"],
        "__DADOS_JSON__": json.dumps(res, ensure_ascii=False, default=str),
    }
    for token, valor in substituicoes.items():
        html = html.replace(token, valor)
    with open(SAIDA_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    s_l, s_m = res["low"]["stats"], res["mean"]["stats"]
    print(f"Análise gerada em {SAIDA_HTML}: {res['data_base']} → {res['data_fim']}, N={s_l['n']}")
    for nome, s in (("low", s_l), ("mean", s_m)):
        print(f"  {nome}: spearman={s['spearman']:+.3f} (p={s['p_spearman']:.3f}) "
              f"top10={s['top_media']:+.2%} resto={s['resto_media']:+.2%} bench={s['bench']:+.2%} "
              f"p_perm={s['p_perm']:.3f} parcial={s['spearman_parcial']:+.3f} (p={s['p_parcial']:.3f})")
    print(f"  descartados={len(res['descartados'])} alertas_csv={len(res['alertas'])}")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-base", default=None, help="padrão: data mais antiga do analyst_insights.csv")
    ap.add_argument("--data-fim", default=None, help="padrão: último pregão fechado")
    a = ap.parse_args()
    gerar(a.data_base, a.data_fim)
