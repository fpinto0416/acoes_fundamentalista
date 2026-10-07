'''
------------------------------------------------------------
Author: Fabio da Costa Pinto
Email: fpinto0416@gmail.com
Created: Outubro 2026
License: Proprietary / Private Use
------------------------------------------------------------
Description:

Testa a hipotese de CONVERGENCIA do preco para o preco-alvo dos analistas:
"comprar os ativos mais distantes do alvo (maior upside) deveria render
mais". Acumula o placar a cada rodada diaria, de modo que o poder
estatistico cresce sozinho com o tempo de coleta.

O ponto central do desenho eh separar convergencia de BETA. As duas
hipoteses explicam o mesmo spread bruto, mas preveem coisas diferentes:

  - convergencia: puxao especifico do ativo em direcao a uma ancora parada
    (o alvo). Acontece com o mercado subindo ou caindo. Preve coeficiente
    positivo no upside DEPOIS de controlar por exposicao ao mercado.

  - beta: a cesta de maior upside so rende mais porque caiu mais (foi isso
    que criou o upside), ficou mais alavancada e por isso amplifica o
    mercado. Preve coeficiente ~0 no upside depois do controle.

Medicao feita em 07/10/2026 sobre as primeiras 6 semanas de coleta deu
beta realizado 1,98 na cesta de maior upside contra 0,30 na de menor, com
spread correlacionado a +0,975 com a direcao do mercado -- padrao de beta,
nao de convergencia. Mas aquela medicao estimou o beta DENTRO do periodo de
teste, o que absorve no beta qualquer convergencia ocorrida em dia de alta
e portanto vicia o resultado contra a hipotese. Este script corrige isso:
o beta eh sempre estimado numa janela que TERMINA ANTES do inicio da
janela de teste (fora da amostra por construcao).

Saidas:

1. `dados/precos_historico.csv` -- fechamento AJUSTADO (proventos
   reinvestidos) diario por ticker, cache incremental. E a unica parte que
   bate na rede. Preco ajustado porque o `preco_atual` do snapshot eh
   preco puro: usa-lo subestimaria o retorno justamente dos pagadores
   pesados, que se concentram na ponta de BAIXO upside.

2. `dados/placar_upside.csv` -- uma linha por janela de teste, com spread
   bruto, spread neutralizado por beta e o coeficiente de convergencia.
   Reescrito por inteiro a cada run (idempotente).

Uso:
    python scripts/analise_upside.py                 # horizonte padrao
    HORIZONTE=42 python scripts/analise_upside.py    # janelas de 42 pregoes
    PULAR_DOWNLOAD=1 python scripts/analise_upside.py  # so recalcula
'''

import os
import sys
import time
from datetime import timedelta

import numpy as np
import pandas as pd
import yfinance as yf

PASTA_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PASTA_PROJETO = os.path.dirname(PASTA_SCRIPTS)

PASTA_DADOS = os.path.join(PASTA_PROJETO, "dados")
PASTA_TICKERS = os.path.join(PASTA_PROJETO, "tickers")
CSV_TICKERS = os.path.join(PASTA_TICKERS, "ibra_composicao.csv")
CSV_ANALYST = os.path.join(PASTA_DADOS, "analyst_insights.csv")
CSV_PRECOS = os.path.join(PASTA_DADOS, "precos_historico.csv")
CSV_PLACAR = os.path.join(PASTA_DADOS, "placar_upside.csv")

os.makedirs(PASTA_DADOS, exist_ok=True)

# Horizonte da janela de teste, em pregoes. 21 ~ 1 mes. O alvo dos
# analistas eh de 12 meses, entao a convergencia, se existir, aparece em
# horizonte de 3-6 meses (63-126 pregoes) -- mas isso so fica testavel
# quando houver historico de upside suficiente, ja que a serie de
# analyst_insights.csv comeca em 24/08/2026 e nao eh recuperavel
# retroativamente (o Yahoo serve so o snapshot corrente do alvo).
HORIZONTE = int(os.getenv("HORIZONTE", "21"))

# Pregoes usados pra estimar o beta, SEMPRE anteriores ao inicio da janela.
LOOKBACK_BETA = int(os.getenv("LOOKBACK_BETA", "252"))
MIN_OBS_BETA = int(os.getenv("MIN_OBS_BETA", "120"))

# Quantos ativos em cada ponta. Decil do IBrA (~152 ativos) ~ 15.
TAMANHO_CESTA = int(os.getenv("TAMANHO_CESTA", "15"))

ANOS_HISTORICO = int(os.getenv("ANOS_HISTORICO", "4"))
LOTE_DOWNLOAD = 40
PAUSA_ENTRE_LOTES = 1.5
TENTATIVAS = 5
PAUSA_RETRY = 15

# Benchmark do beta. Ibovespa eh a convencao e eh o principal. Mas ele eh
# ponderado por capitalizacao e muito concentrado (VALE3 10,8%, ITUB4 8,0%,
# PETR4 7,4%, PETR3 6,5% -- top5 = 37% do IBrA), enquanto as cestas do
# teste sao equal-weight. Entre 24/08 e 06/10 o cap-weight rendeu +19,9% e
# o equal-weight +24,2%: 4,3 pp de deriva de estilo em 6 semanas, que
# viraria "alfa" espurio e apareceria como convergencia. As duas series
# correlacionam +0,956 e o beta contra o Ibovespa sai ate maior (2,33 vs
# 2,15 na cesta de maior upside), entao a diferenca eh pequena -- mas o
# script mede contra os dois. Se as duas respostas divergirem, a
# divergencia eh o achado: significa que o efeito eh fator de tamanho /
# estilo, nao convergencia pro alvo.
TICKER_IBOV = "^BVSP"


# ---------------------------------------------------------------------
# 1. Historico de precos (unica parte que usa rede)
# ---------------------------------------------------------------------

def _download_com_retry(lote: list[str], desde: pd.Timestamp):
    """yf.download com retry -- mesmo padrao do projeto_macd.py, que apanha
    de rate-limit do Yahoo em rodada longa (150+ tickers)."""
    for tentativa in range(TENTATIVAS):
        try:
            return yf.download(
                lote,
                start=desde.strftime("%Y-%m-%d"),
                auto_adjust=True,      # proventos reinvestidos
                progress=False,
                threads=True,
            )
        except Exception as exc:
            print(f"  ! tentativa {tentativa + 1}/{TENTATIVAS} falhou: {exc}")
            if tentativa < TENTATIVAS - 1:
                time.sleep(PAUSA_RETRY)
    return None


def baixar_precos(tickers_yf: list[str], desde: pd.Timestamp) -> pd.DataFrame:
    """Baixa fechamento ajustado em lotes. Devolve formato longo."""
    partes = []
    for i in range(0, len(tickers_yf), LOTE_DOWNLOAD):
        lote = tickers_yf[i:i + LOTE_DOWNLOAD]
        print(f"  lote {i // LOTE_DOWNLOAD + 1}: {len(lote)} tickers...", flush=True)
        bruto = _download_com_retry(lote, desde)
        if bruto is None or bruto.empty:
            continue
        fech = bruto["Close"] if "Close" in bruto else bruto
        if isinstance(fech, pd.Series):          # lote de 1 ticker
            fech = fech.to_frame(lote[0])
        longo = (
            fech.stack(future_stack=True)
            .rename("fechamento")
            .reset_index()
        )
        longo.columns = ["data", "ticker_yf", "fechamento"]
        partes.append(longo.dropna(subset=["fechamento"]))
        time.sleep(PAUSA_ENTRE_LOTES)
    if not partes:
        return pd.DataFrame(columns=["data", "ticker_yf", "fechamento"])
    return pd.concat(partes, ignore_index=True)


def atualizar_cache_precos(tickers: pd.DataFrame) -> pd.DataFrame:
    """Mantem dados/precos_historico.csv incremental; so baixa o que falta."""
    if os.path.exists(CSV_PRECOS):
        cache = pd.read_csv(CSV_PRECOS, parse_dates=["data"])
    else:
        cache = pd.DataFrame(columns=["data", "ticker", "fechamento"])

    if os.getenv("PULAR_DOWNLOAD"):
        print("PULAR_DOWNLOAD definido -- usando so o cache existente.")
        return cache

    if cache.empty:
        desde = pd.Timestamp.today().normalize() - pd.DateOffset(years=ANOS_HISTORICO)
        print(f"Cache vazio: baixando {ANOS_HISTORICO} anos de historico.")
    else:
        # relbaixa os ultimos dias pra pegar revisao de ajuste por provento
        desde = cache["data"].max() - timedelta(days=7)
        print(f"Cache ate {cache['data'].max().date()}: atualizando a partir de {desde.date()}.")

    # o ^BVSP entra junto: eh o benchmark principal do beta
    alvos = tickers["ticker_yf"].tolist() + [TICKER_IBOV]
    novo = baixar_precos(alvos, desde)
    if novo.empty:
        print("! nada baixado -- seguindo com o cache.")
        return cache

    mapa = dict(zip(tickers["ticker_yf"], tickers["ticker"]))
    mapa[TICKER_IBOV] = TICKER_IBOV
    novo["ticker"] = novo["ticker_yf"].map(mapa)
    novo = novo.dropna(subset=["ticker"])[["data", "ticker", "fechamento"]]

    junto = pd.concat([cache, novo], ignore_index=True)
    junto["data"] = pd.to_datetime(junto["data"]).dt.tz_localize(None).dt.normalize()
    # o recem-baixado vence em caso de empate (ajuste revisado)
    junto = junto.drop_duplicates(subset=["data", "ticker"], keep="last")
    junto = junto.sort_values(["ticker", "data"]).reset_index(drop=True)
    junto.to_csv(CSV_PRECOS, index=False)
    print(f"Cache de precos: {len(junto):,} linhas, "
          f"{junto['ticker'].nunique()} tickers, "
          f"{junto['data'].min().date()} a {junto['data'].max().date()}.")
    return junto


# ---------------------------------------------------------------------
# 2. Beta fora da amostra
# ---------------------------------------------------------------------

def matriz_retornos(precos: pd.DataFrame) -> pd.DataFrame:
    """Retornos diarios simples, data x ticker."""
    larga = precos.pivot(index="data", columns="ticker", values="fechamento").sort_index()
    return larga.pct_change()


def serie_mercado(ret: pd.DataFrame, bench: str) -> pd.Series:
    """bench='ibov' usa o ^BVSP; bench='ew' usa o equal-weight do universo."""
    if bench == "ibov":
        if TICKER_IBOV not in ret.columns:
            raise KeyError(f"{TICKER_IBOV} ausente do cache de precos.")
        return ret[TICKER_IBOV]
    return ret.drop(columns=[TICKER_IBOV], errors="ignore").mean(axis=1, skipna=True)


def estimar_betas(ret: pd.DataFrame, mkt_full: pd.Series, fim: pd.Timestamp,
                  dimson: bool = True) -> pd.Series:
    """
    Beta diario de cada ticker contra `mkt_full`, estimado nos
    LOOKBACK_BETA pregoes que TERMINAM em `fim`.

    `fim` deve ser o ultimo pregao ANTERIOR ao inicio da janela de teste --
    eh isso que torna o beta fora da amostra. Estimar o beta dentro do
    proprio periodo de teste absorveria no beta qualquer convergencia
    ocorrida em dia de alta, viciando o teste contra a hipotese.

    Com dimson=True soma os coeficientes de t e t-1. Correcao necessaria
    porque beta diario de acao iliquida eh viesado pra baixo por negociacao
    nao-sincronizada, e as de alto upside sao justamente as menos liquidas
    -- ou seja, o vies erraria A FAVOR da hipotese de convergencia.
    """
    janela = ret.loc[:fim].tail(LOOKBACK_BETA)
    if len(janela) < MIN_OBS_BETA:
        return pd.Series(dtype=float)

    mkt = mkt_full.reindex(janela.index)
    betas = {}
    for tk in janela.columns:
        y = janela[tk]
        regs = [mkt, mkt.shift(1)] if dimson else [mkt]
        X = pd.concat(regs, axis=1)
        dados = pd.concat([y.rename("y"), X], axis=1).dropna()
        if len(dados) < MIN_OBS_BETA:
            continue
        A = np.column_stack([np.ones(len(dados)), dados.iloc[:, 1:].to_numpy()])
        coef, *_ = np.linalg.lstsq(A, dados["y"].to_numpy(), rcond=None)
        betas[tk] = float(coef[1:].sum())      # Dimson: soma das defasagens
    return pd.Series(betas, name="beta")


# ---------------------------------------------------------------------
# 3. Janelas de teste
# ---------------------------------------------------------------------

def carregar_upside() -> pd.DataFrame:
    ana = pd.read_csv(CSV_ANALYST, parse_dates=["data"])
    ana = ana.dropna(subset=["upside_pct", "preco_atual"])
    ana = ana[ana["preco_atual"] > 0]
    return ana[["data", "ticker", "upside_pct"]]


def ols(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """OLS com intercepto. Devolve (coeficientes, erros-padrao)."""
    A = np.column_stack([np.ones(len(y)), X])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ coef
    gl = len(y) - A.shape[1]
    if gl <= 0:
        return coef, np.full(len(coef), np.nan)
    s2 = float(resid @ resid) / gl
    try:
        cov = s2 * np.linalg.inv(A.T @ A)
        return coef, np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        return coef, np.full(len(coef), np.nan)


def avaliar_janela(ret: pd.DataFrame, upside: pd.DataFrame,
                   i0: int, i1: int, bench: str) -> dict | None:
    """Mede uma janela [i0, i1] de pregoes sobre a matriz de retornos."""
    datas = ret.index
    d0, d1 = datas[i0], datas[i1]

    # upside conhecido em d0 (ou no pregao imediatamente anterior)
    disp = upside[upside["data"] <= d0]
    if disp.empty:
        return None
    data_up = disp["data"].max()
    if (d0 - data_up).days > 5:
        return None
    up = disp[disp["data"] == data_up].set_index("ticker")["upside_pct"] * 100

    # Retorno acumulado na janela, sobre preco AJUSTADO por proventos
    # (auto_adjust=True no download). Isso corrige um vies real: o
    # `preco_atual` que o download_fundamentals.py guarda vem de
    # info["currentPrice"], que eh preco negociado cru, sem ajuste. Usar
    # ele subestimaria o retorno das pagadoras pesadas (BBSE3, ABEV3,
    # ITUB4, TAEE11...), que se concentram na ponta de BAIXO upside,
    # inflando o spread a favor da hipotese de convergencia.
    acum = (1 + ret.iloc[i0 + 1:i1 + 1]).prod() - 1
    acum = acum[ret.iloc[i0 + 1:i1 + 1].notna().sum() >= (i1 - i0) * 0.8] * 100
    acum = acum.drop(labels=[TICKER_IBOV], errors="ignore")

    # beta estimado ATE d0 -- nenhum dado da janela entra aqui
    mkt_full = serie_mercado(ret, bench)
    betas = estimar_betas(ret, mkt_full, fim=d0)
    if betas.empty:
        return None

    df = pd.concat([up.rename("upside"), acum.rename("ret"), betas], axis=1).dropna()
    df = df.drop(index=[TICKER_IBOV], errors="ignore")
    if len(df) < TAMANHO_CESTA * 3:
        return None

    # retorno do benchmark na janela, na mesma base composta
    if bench == "ibov":
        mkt = float((1 + ret[TICKER_IBOV].iloc[i0 + 1:i1 + 1]).prod() - 1) * 100
    else:
        mkt = float(acum.reindex(df.index).mean())
    df["ret_residual"] = df["ret"] - df["beta"] * mkt

    ordenado = df.sort_values("upside", ascending=False)
    topo, base = ordenado.head(TAMANHO_CESTA), ordenado.tail(TAMANHO_CESTA)

    # spread neutralizado DENTRO de quintil de beta: em cada quintil,
    # compra o terco de maior upside e vende o de menor. Imune a diferenca
    # de beta entre as cestas, que eh o problema central.
    df["q_beta"] = pd.qcut(df["beta"].rank(method="first"), 5, labels=False)
    pedacos = []
    for _, g in df.groupby("q_beta"):
        g = g.sort_values("upside", ascending=False)
        k = max(1, len(g) // 3)
        pedacos.append(g["ret"].head(k).mean() - g["ret"].tail(k).mean())
    spread_neutro = float(np.mean(pedacos))

    # regressao: ret ~ beta*mercado + upside. O coeficiente do upside eh o
    # coeficiente de convergencia -- quanto de retorno sobra por ponto de
    # upside depois de descontar a exposicao ao mercado.
    X = np.column_stack([df["beta"].to_numpy() * mkt, df["upside"].to_numpy()])
    coef, se = ols(df["ret"].to_numpy(), X)
    c_conv, se_conv = float(coef[2]), float(se[2])

    return {
        "benchmark": "ibovespa" if bench == "ibov" else "equal_weight",
        "inicio": d0.date(), "fim": d1.date(), "pregoes": i1 - i0, "n": len(df),
        "mercado_pct": round(mkt, 2),
        "ret_topo_pct": round(float(topo["ret"].mean()), 2),
        "ret_base_pct": round(float(base["ret"].mean()), 2),
        "spread_bruto_pp": round(float(topo["ret"].mean() - base["ret"].mean()), 2),
        "beta_topo": round(float(topo["beta"].mean()), 2),
        "beta_base": round(float(base["beta"].mean()), 2),
        "spread_residual_pp": round(float(topo["ret_residual"].mean()
                                          - base["ret_residual"].mean()), 2),
        "spread_neutro_beta_pp": round(spread_neutro, 2),
        "coef_convergencia": round(c_conv, 4),
        "t_convergencia": round(c_conv / se_conv, 2) if se_conv and se_conv == se_conv else np.nan,
    }


def construir_placar(ret: pd.DataFrame, upside: pd.DataFrame) -> pd.DataFrame:
    """
    Janelas NAO sobrepostas, pra que cada uma conte como uma observacao
    independente. Roda o teste inteiro contra os dois benchmarks.
    """
    primeiro = upside["data"].min()
    datas = ret.index
    linhas = []
    benches = ["ibov", "ew"] if TICKER_IBOV in ret.columns else ["ew"]
    if "ibov" not in benches:
        print(f"! {TICKER_IBOV} ausente do cache -- so o equal-weight sera medido.")
    for bench in benches:
        pos = int(np.searchsorted(datas, primeiro))
        while pos + HORIZONTE < len(datas):
            r = avaliar_janela(ret, upside, pos, pos + HORIZONTE, bench)
            if r:
                linhas.append(r)
            pos += HORIZONTE
    return pd.DataFrame(linhas)


# ---------------------------------------------------------------------
# 4. Relatorio
# ---------------------------------------------------------------------

def resumir(placar: pd.DataFrame) -> None:
    n = len(placar)
    print("\n" + "=" * 72)
    print(f"PLACAR -- janelas nao sobrepostas de {HORIZONTE} pregoes, "
          f"cestas de {TAMANHO_CESTA}")
    print("=" * 72)
    if n == 0:
        print("Sem janela completa ainda. Volte depois de mais coleta.")
        return

    for bench, g in placar.groupby("benchmark"):
        _resumir_bench(bench, g)

    if placar["benchmark"].nunique() > 1:
        print("\n" + "-" * 72)
        print("Se os dois benchmarks discordarem, a divergencia EH o achado:")
        print("significa fator de tamanho/estilo, nao convergencia pro alvo.")

    print(f"\nPlacar salvo em {os.path.relpath(CSV_PLACAR, PASTA_PROJETO)}")


def _resumir_bench(bench: str, placar: pd.DataFrame) -> None:
    n = len(placar)
    print(f"\n### beta contra: {bench.upper()}  ({n} janela(s))")

    cols = ["inicio", "fim", "n", "mercado_pct", "spread_bruto_pp",
            "beta_topo", "beta_base", "spread_neutro_beta_pp", "coef_convergencia"]
    print(placar[cols].to_string(index=False))

    print("\nAgregado:")
    for rotulo, col in [("spread BRUTO            ", "spread_bruto_pp"),
                        ("spread residual (-beta) ", "spread_residual_pp"),
                        ("spread neutro p/ quintil", "spread_neutro_beta_pp")]:
        v = placar[col].dropna()
        if len(v) < 2:
            print(f"  {rotulo}: {v.mean():+.2f} pp (n={len(v)}, sem t)")
            continue
        t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
        print(f"  {rotulo}: {v.mean():+6.2f} pp  desvio {v.std(ddof=1):5.2f}  "
              f"t={t:+5.2f}  positivo em {int((v > 0).sum())}/{len(v)}")

    c = placar["coef_convergencia"].dropna()
    if len(c) >= 2:
        t = c.mean() / (c.std(ddof=1) / np.sqrt(len(c)))
        print("\n  coeficiente de convergencia (ret por pp de upside, "
              "ja descontado o mercado):")
        print(f"    media {c.mean():+.4f}   t={t:+.2f}   positivo em "
              f"{int((c > 0).sum())}/{len(c)}")

    # o teste que separa as duas hipoteses
    if n >= 4:
        mk, sp = placar["mercado_pct"].to_numpy(), placar["spread_bruto_pp"].to_numpy()
        coef, se = ols(sp, mk.reshape(-1, 1))
        print(f"\n  spread_bruto = {coef[1]:.2f} x mercado {coef[0]:+.2f}")
        print("    inclinacao ~0 e intercepto >0 => CONVERGENCIA")
        print("    inclinacao >0 e intercepto ~0 => BETA disfarcado")
        if se[0] == se[0]:
            print(f"    (erro-padrao do intercepto: {se[0]:.2f})")

    print(f"  Poder: com {n} janela(s), um spread real de 5 pp so vira t>2 "
          f"com ~{max(0, 16 - n)} janela(s) a mais.")


def main() -> None:
    if not os.path.exists(CSV_ANALYST):
        sys.exit(f"Falta {CSV_ANALYST} -- rode download_fundamentals.py antes.")

    tickers = pd.read_csv(CSV_TICKERS)
    precos = atualizar_cache_precos(tickers)
    if precos.empty:
        sys.exit("Sem historico de precos -- nada a fazer.")

    precos["data"] = pd.to_datetime(precos["data"])
    ret = matriz_retornos(precos)
    upside = carregar_upside()

    print(f"\nUpside disponivel de {upside['data'].min().date()} a "
          f"{upside['data'].max().date()} "
          f"({upside['data'].nunique()} datas, {upside['ticker'].nunique()} tickers).")
    print(f"Beta: {LOOKBACK_BETA} pregoes terminando ANTES do inicio de cada janela.")

    placar = construir_placar(ret, upside)
    if not placar.empty:
        placar.to_csv(CSV_PLACAR, index=False)
    resumir(placar)


if __name__ == "__main__":
    main()
